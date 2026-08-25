import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:crypto/crypto.dart';

import '../../models/chat_models.dart';
import '../../services/bridge/proofray_bridge.dart';
import '../../services/bridge/bridge_protocol.dart';
import '../../storage/conversation_store.dart';

class ChatController extends ChangeNotifier {
  ChatController({
    required this.conversationId,
    this.bridge,
    this.store,
    this.memoryMode = MemoryMode.keywords,
    this.providerId,
    this.providerSecretLoader,
    List<String> keywords = const <String>[],
    List<ChatMessage> initialMessages = const <ChatMessage>[],
    int? initialNextSequence,
  }) {
    _messages.addAll(initialMessages);
    _nextSequence = initialNextSequence ?? initialMessages.length;
    _baseMemoryMode = memoryMode == MemoryMode.forceNext
        ? MemoryMode.keywords
        : memoryMode;
    _keywords = List<String>.from(keywords);
  }

  final String conversationId;
  ProofRayBridge? bridge;
  final ConversationStore? store;
  MemoryMode memoryMode;
  MemoryMode _baseMemoryMode = MemoryMode.keywords;
  String? providerId;
  bool _providerSupportsTools = true;
  bool get providerSupportsTools => _providerSupportsTools;
  final Future<String?> Function(String providerId)? providerSecretLoader;
  late List<String> _keywords;
  List<String> get keywords => List<String>.unmodifiable(_keywords);

  final List<ChatMessage> _messages = <ChatMessage>[];
  List<ChatMessage> get messages => List<ChatMessage>.unmodifiable(_messages);

  BitHorizonStage _stage = BitHorizonStage.idle;
  BitHorizonStage get stage => _stage;

  String _queryDigest = '';
  String get queryDigest => _queryDigest;

  bool _sending = false;
  bool get sending => _sending;
  bool _memoryStartedForRequest = false;

  int _identity = 0;
  late int _nextSequence;
  final Map<String, StringBuffer> _streamBuffers = <String, StringBuffer>{};
  String? _activeRequestId;

  void attachBridge(ProofRayBridge value) {
    bridge = value;
    notifyListeners();
    unawaited(recoverPending());
  }

  void setMemoryMode(MemoryMode value) {
    if (value == MemoryMode.tool && !_providerSupportsTools) {
      value = MemoryMode.keywords;
    }
    if (memoryMode == value) {
      return;
    }
    memoryMode = value;
    if (value != MemoryMode.forceNext) {
      _baseMemoryMode = value;
      final ConversationStore? activeStore = store;
      if (activeStore != null) {
        unawaited(activeStore.updateMemoryMode(conversationId, value));
      }
    }
    notifyListeners();
  }

  void setProvider(String? value, {bool supportsTools = true}) {
    final bool effectiveToolSupport = value != null && supportsTools;
    if (providerId == value && _providerSupportsTools == effectiveToolSupport) {
      return;
    }
    providerId = value;
    _providerSupportsTools = effectiveToolSupport;
    if (!_providerSupportsTools && memoryMode == MemoryMode.tool) {
      memoryMode = MemoryMode.keywords;
      _baseMemoryMode = MemoryMode.keywords;
      final ConversationStore? activeStore = store;
      if (activeStore != null) {
        unawaited(
          activeStore.updateMemoryMode(conversationId, MemoryMode.keywords),
        );
      }
    }
    notifyListeners();
    if (value == null) {
      unawaited(recoverPending());
    }
  }

  void setKeywords(Iterable<String> values) {
    final List<String> normalized =
        values
            .map((String item) => item.trim().toLowerCase())
            .where(
              (String item) =>
                  item.isNotEmpty && utf8.encode(item).length <= 128,
            )
            .toSet()
            .toList()
          ..sort();
    if (normalized.length > 64) {
      normalized.removeRange(64, normalized.length);
    }
    _keywords = normalized;
    notifyListeners();
  }

  Future<void> send(String rawText) async {
    final String text = rawText.trim();
    if (text.isEmpty || utf8.encode(text).length > 64 * 1024 || _sending) {
      return;
    }
    _sending = true;
    _stage = BitHorizonStage.idle;
    _memoryStartedForRequest = false;
    final DateTime now = DateTime.now().toUtc();
    final String messageId =
        'message_${now.microsecondsSinceEpoch}_${_identity++}';
    final String assistantId = 'answer_$messageId';
    _messages.add(
      ChatMessage(
        id: messageId,
        role: MessageRole.user,
        text: text,
        createdAt: now,
      ),
    );
    _messages.add(
      ChatMessage(
        id: assistantId,
        role: MessageRole.assistant,
        text: '',
        createdAt: now,
        authority: AnswerAuthority.pending,
      ),
    );
    notifyListeners();

    final ChatMessage userMessage = _messages[_messages.length - 2];
    final int userSequence = _nextSequence;
    final ConversationStore? activeStore = store;
    if (activeStore != null) {
      try {
        await activeStore.stageUserMessage(
          conversationId,
          userSequence,
          userMessage,
          memoryMode: memoryMode,
          providerId: providerId,
          keywords: _keywords,
        );
      } on Object {
        _replaceAssistant(
          assistantId,
          ChatMessage(
            id: assistantId,
            role: MessageRole.assistant,
            text: '',
            createdAt: now,
            authority: AnswerAuthority.abstention,
          ),
        );
        _stage = BitHorizonStage.abstained;
        _finishSending();
        return;
      }
    }
    _nextSequence = userSequence + 2;

    final ProofRayBridge? activeBridge = bridge;
    if (activeBridge == null) {
      _replaceAssistant(
        assistantId,
        ChatMessage(
          id: assistantId,
          role: MessageRole.assistant,
          text: '',
          createdAt: now,
          authority: AnswerAuthority.abstention,
        ),
      );
      _finishSending();
      return;
    }

    bool completed = false;
    try {
      final String? providerSecret =
          providerId == null || providerSecretLoader == null
          ? null
          : await providerSecretLoader!(providerId!);
      await for (final BridgeEvent event in activeBridge.sendMessage(
        conversationId: conversationId,
        messageId: messageId,
        text: text,
        memoryMode: memoryMode,
        sequence: userSequence,
        createdAt: now,
        providerId: providerId,
        providerSecret: providerSecret,
        turns: _recentTurnsBefore(userMessage.id),
        keywords: _keywords,
        onRequestId: (String value) => _activeRequestId = value,
      )) {
        _applyEvent(event, assistantId, now);
        completed =
            completed ||
            event.event == 'completed' ||
            (event.event == 'error' && event.payload['code'] == 'cancelled');
      }
      if (completed) {
        await _hydrateAssistantSources(assistantId, activeBridge);
      }
    } on Object {
      _replaceAssistant(
        assistantId,
        ChatMessage(
          id: assistantId,
          role: MessageRole.assistant,
          text: '',
          createdAt: now,
          authority: AnswerAuthority.abstention,
        ),
      );
    } finally {
      if (completed && activeStore != null) {
        try {
          await activeStore.commitExchange(userMessage, _messages.last);
        } on Object {
          // The encrypted outbox remains authoritative and will be retried.
          _stage = BitHorizonStage.abstained;
        }
      }
      _finishSending();
    }
  }

  Future<void> recoverPending() async {
    final ConversationStore? activeStore = store;
    final ProofRayBridge? activeBridge = bridge;
    if (_sending || activeStore == null || activeBridge == null) {
      return;
    }
    final List<PendingUserMessage> pending = await activeStore
        .pendingUserMessages(conversationId);
    for (final PendingUserMessage operation in pending) {
      if (_sending || bridge != activeBridge) {
        return;
      }
      _sending = true;
      _memoryStartedForRequest = false;
      final ChatMessage user = operation.message;
      if (!_messages.any((ChatMessage item) => item.id == user.id)) {
        _messages.add(user);
      }
      if (operation.confirmation) {
        try {
          await activeBridge.confirmMemory(
            conversationId: operation.conversationId,
            messageId: user.id,
            text: user.text,
            sequence: operation.sequence,
            createdAt: user.createdAt,
          );
          await activeStore.commitConfirmedObservation(
            operation.conversationId,
            user,
          );
        } on Object {
          _sending = false;
          notifyListeners();
          return;
        }
        _sending = false;
        notifyListeners();
        continue;
      }
      final String assistantId = 'answer_${user.id}';
      final ChatMessage waiting = ChatMessage(
        id: assistantId,
        role: MessageRole.assistant,
        text: '',
        createdAt: user.createdAt,
        authority: AnswerAuthority.pending,
      );
      final int prior = _messages.indexWhere(
        (ChatMessage item) => item.id == assistantId,
      );
      if (prior < 0) {
        _messages.add(waiting);
      } else {
        _messages[prior] = waiting;
      }
      notifyListeners();
      bool completed = false;
      bool durablyCommitted = false;
      try {
        final String? retryProviderId = operation.providerId == providerId
            ? operation.providerId
            : null;
        final MemoryMode retryMode =
            operation.memoryMode == MemoryMode.tool && retryProviderId == null
            ? MemoryMode.keywords
            : operation.memoryMode;
        final String? providerSecret =
            retryProviderId == null || providerSecretLoader == null
            ? null
            : await providerSecretLoader!(retryProviderId);
        await for (final BridgeEvent event in activeBridge.sendMessage(
          conversationId: operation.conversationId,
          messageId: user.id,
          text: user.text,
          memoryMode: retryMode,
          sequence: operation.sequence,
          createdAt: user.createdAt,
          providerId: retryProviderId,
          providerSecret: providerSecret,
          turns: _recentTurnsBefore(user.id),
          keywords: operation.keywords,
          onRequestId: (String value) => _activeRequestId = value,
        )) {
          _applyEvent(event, assistantId, user.createdAt);
          completed =
              completed ||
              event.event == 'completed' ||
              (event.event == 'error' && event.payload['code'] == 'cancelled');
        }
        if (completed) {
          await _hydrateAssistantSources(assistantId, activeBridge);
        }
        if (completed) {
          final ChatMessage assistant = _messages.firstWhere(
            (ChatMessage item) => item.id == assistantId,
          );
          await activeStore.commitExchange(user, assistant);
          durablyCommitted = true;
        }
      } on Object {
        _stage = BitHorizonStage.abstained;
      } finally {
        _sending = false;
        _activeRequestId = null;
        _memoryStartedForRequest = false;
        notifyListeners();
      }
      if (!completed || !durablyCommitted) {
        return;
      }
    }
  }

  Future<void> confirmAsMemory(ChatMessage source) async {
    final ProofRayBridge? activeBridge = bridge;
    final ConversationStore? activeStore = store;
    final String text = (source.certifiedText ?? source.text).trim();
    if (_sending ||
        activeBridge == null ||
        activeStore == null ||
        text.isEmpty) {
      return;
    }
    _sending = true;
    notifyListeners();
    final DateTime now = DateTime.now().toUtc();
    final String messageId =
        'confirmed_${now.microsecondsSinceEpoch}_${_identity++}';
    final ChatMessage observation = ChatMessage(
      id: messageId,
      role: MessageRole.user,
      text: text,
      createdAt: now,
    );
    try {
      final int sequence = _nextSequence;
      await activeStore.stageConfirmedObservation(
        conversationId,
        sequence,
        observation,
      );
      _nextSequence = sequence + 1;
      await activeBridge.confirmMemory(
        conversationId: conversationId,
        messageId: messageId,
        text: text,
        sequence: sequence,
        createdAt: now,
      );
      await activeStore.commitConfirmedObservation(conversationId, observation);
      _messages.add(observation);
    } on Object {
      _stage = BitHorizonStage.abstained;
    } finally {
      _sending = false;
      notifyListeners();
    }
  }

  Future<void> cancel() async {
    final String? requestId = _activeRequestId;
    final ProofRayBridge? activeBridge = bridge;
    if (requestId != null && activeBridge != null) {
      await activeBridge.cancel(requestId);
    }
  }

  List<Map<String, String>> _recentTurnsBefore(String currentMessageId) {
    final List<Map<String, String>> selected = <Map<String, String>>[];
    int bytes = 0;
    for (final ChatMessage message in _messages.reversed) {
      if (message.id == currentMessageId ||
          message.authority == AnswerAuthority.pending ||
          message.text.isEmpty) {
        continue;
      }
      final int size = utf8.encode(message.text).length;
      if (bytes + size > 16 * 1024) {
        break;
      }
      selected.add(<String, String>{
        'role': message.role == MessageRole.user ? 'user' : 'assistant',
        'text': message.text,
      });
      bytes += size;
    }
    return selected.reversed.toList(growable: false);
  }

  void _applyEvent(BridgeEvent event, String assistantId, DateTime createdAt) {
    switch (event.event) {
      case 'memory.started':
        _memoryStartedForRequest = true;
        _queryDigest = event.payload['query_digest'] as String? ?? '';
        _stage = BitHorizonStage.activating;
        break;
      case 'routing':
        _stage = BitHorizonStage.routing;
        break;
      case 'verifying':
        _stage = BitHorizonStage.verifying;
        break;
      case 'proof.closed':
        _stage = BitHorizonStage.proofClosed;
        _replaceAssistant(
          assistantId,
          _messageFromPayload(assistantId, createdAt, event.payload),
        );
        break;
      case 'evidence':
        _stage = BitHorizonStage.evidence;
        _replaceAssistant(
          assistantId,
          _messageFromPayload(assistantId, createdAt, event.payload),
        );
        break;
      case 'contested':
        _stage = BitHorizonStage.contested;
        break;
      case 'abstained':
        _stage = BitHorizonStage.abstained;
        break;
      case 'model.delta':
        if (_stage == BitHorizonStage.proofClosed ||
            _stage == BitHorizonStage.evidence) {
          // Certified text/evidence remains visible until the backend has
          // deterministically accepted the complete rewrite/summary.
          return;
        }
        final Object? delta = event.payload['text'];
        if (delta is String && delta.isNotEmpty) {
          final StringBuffer buffer = _streamBuffers.putIfAbsent(
            assistantId,
            StringBuffer.new,
          );
          final String fitted = _fitUtf8Prefix(
            '${buffer.toString()}$delta',
            24 * 1024,
          );
          buffer
            ..clear()
            ..write(fitted);
          _replaceAssistant(
            assistantId,
            ChatMessage(
              id: assistantId,
              role: MessageRole.assistant,
              text: buffer.toString(),
              createdAt: createdAt,
              authority: AnswerAuthority.pending,
            ),
          );
        }
        break;
      case 'completed':
        _streamBuffers.remove(assistantId);
        _replaceAssistant(
          assistantId,
          _messageFromPayload(assistantId, createdAt, event.payload),
        );
        break;
      case 'error':
        _streamBuffers.remove(assistantId);
        final int existingIndex = _messages.indexWhere(
          (ChatMessage item) => item.id == assistantId,
        );
        final ChatMessage? existing = existingIndex < 0
            ? null
            : _messages[existingIndex];
        if (event.payload['code'] == 'cancelled' &&
            existing?.memoryConsulted == true &&
            (existing?.authority == AnswerAuthority.proved ||
                existing?.authority == AnswerAuthority.evidence)) {
          return;
        }
        _stage = BitHorizonStage.abstained;
        _replaceAssistant(
          assistantId,
          ChatMessage(
            id: assistantId,
            role: MessageRole.assistant,
            text: '',
            createdAt: createdAt,
            authority: AnswerAuthority.abstention,
          ),
        );
        break;
      default:
        break;
    }
    notifyListeners();
  }

  ChatMessage _messageFromPayload(
    String id,
    DateTime createdAt,
    Map<String, Object?> payload,
  ) {
    final String authorityName = payload['authority'] as String? ?? 'model';
    final AnswerAuthority authority = AnswerAuthority.values.firstWhere(
      (AnswerAuthority item) => item.name == authorityName,
      orElse: () => AnswerAuthority.model,
    );
    final bool memoryConsulted = payload['memory_consulted'] == true;
    if (memoryConsulted != _memoryStartedForRequest) {
      throw const FormatException(
        'memory activation marker differs from request lifecycle',
      );
    }
    final List<ProofSource> sources = <ProofSource>[];
    final Object? rawSources = payload['sources'];
    if (rawSources is List<Object?>) {
      for (final Object? raw in rawSources) {
        if (raw is! Map<String, Object?> ||
            raw['fact_id'] is! int ||
            raw['source_id'] is! String ||
            raw['text'] is! String) {
          continue;
        }
        final Object? rawSpan = raw['source_span'];
        sources.add(_proofSource(raw, rawSpan));
      }
    }
    return ChatMessage(
      id: id,
      role: MessageRole.assistant,
      text: payload['text'] as String? ?? '',
      createdAt: createdAt,
      authority: authority,
      memoryConsulted: memoryConsulted,
      certifiedText: payload['certified_text'] as String?,
      certificateHex: payload['certificate_hex'] as String?,
      proofRunId: payload['proof_run_id'] as String?,
      proofMethod: payload['proof_method'] as String?,
      queryDigest:
          payload['query_digest'] as String? ??
          (_queryDigest.isEmpty ? null : _queryDigest),
      documentsConsidered: payload['documents_considered'] as int? ?? 0,
      verifiedCandidates: payload['verified_candidates'] as int? ?? 0,
      answerBytes: payload['answer_bytes'] as int? ?? 0,
      textTruncated: payload['text_truncated'] == true,
      sources: sources,
    );
  }

  ProofSource _proofSource(Map<String, Object?> raw, Object? rawSpan) {
    final String text = raw['text']! as String;
    final String digest = raw['parent_sha256'] as String? ?? '';
    if (text.isNotEmpty &&
        digest.isNotEmpty &&
        sha256.convert(utf8.encode(text)).toString() != digest) {
      throw const FormatException('source digest mismatch');
    }
    final (int, int)? span =
        rawSpan is List<Object?> &&
            rawSpan.length == 2 &&
            rawSpan[0] is int &&
            rawSpan[1] is int
        ? (rawSpan[0]! as int, rawSpan[1]! as int)
        : null;
    if (span != null &&
        (span.$1 < 0 ||
            span.$2 < span.$1 ||
            (text.isNotEmpty && span.$2 > text.length))) {
      throw const FormatException('source span mismatch');
    }
    return ProofSource(
      factId: raw['fact_id']! as int,
      sourceId: raw['source_id']! as String,
      text: text,
      parentSha256: digest,
      sessionId: raw['session_id'] as String?,
      speaker: raw['speaker'] as String?,
      sourceSpan: span,
      textDeferred: raw['text_deferred'] == true,
    );
  }

  Future<void> _hydrateAssistantSources(
    String assistantId,
    ProofRayBridge activeBridge,
  ) async {
    final int index = _messages.indexWhere(
      (ChatMessage item) => item.id == assistantId,
    );
    if (index < 0 ||
        !_messages[index].sources.any(
          (ProofSource source) => source.textDeferred,
        )) {
      return;
    }
    final List<ProofSource> hydrated = <ProofSource>[];
    for (final ProofSource source in _messages[index].sources) {
      if (!source.textDeferred) {
        hydrated.add(source);
        continue;
      }
      try {
        final Map<String, Object?> raw = await activeBridge.getMemorySource(
          factId: source.factId,
          sourceId: source.sourceId,
        );
        hydrated.add(_proofSource(raw, raw['source_span']));
      } on Object {
        hydrated.add(source);
      }
    }
    _messages[index] = _messages[index].copyWith(
      sources: List<ProofSource>.unmodifiable(hydrated),
    );
    notifyListeners();
  }

  void _replaceAssistant(String id, ChatMessage replacement) {
    final int index = _messages.indexWhere((ChatMessage item) => item.id == id);
    if (index >= 0) {
      _messages[index] = replacement;
    }
  }

  void _finishSending() {
    _sending = false;
    _activeRequestId = null;
    _memoryStartedForRequest = false;
    if (memoryMode == MemoryMode.forceNext) {
      memoryMode = _baseMemoryMode;
    }
    notifyListeners();
  }
}

String _fitUtf8Prefix(String text, int limit) {
  final List<int> raw = utf8.encode(text);
  if (raw.length <= limit) return text;
  int end = limit;
  while (end > 0 && (raw[end] & 0xC0) == 0x80) {
    end--;
  }
  return utf8.decode(raw.sublist(0, end), allowMalformed: false);
}
