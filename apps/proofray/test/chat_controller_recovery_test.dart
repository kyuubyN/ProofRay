import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/features/chat/chat_controller.dart';
import 'package:proofray_app/models/chat_models.dart';
import 'package:proofray_app/services/bridge/proofray_bridge.dart';
import 'package:proofray_app/storage/conversation_store.dart';

void main() {
  test(
    'stage failure always unlocks send and resets force-next mode',
    () async {
      final _FailingConversationStore store = _FailingConversationStore(
        failStage: true,
      );
      final ChatController controller = ChatController(
        conversationId: 'thread',
        store: store,
        memoryMode: MemoryMode.forceNext,
      );
      addTearDown(controller.dispose);

      await controller.send('Remember this exact observation.');

      expect(controller.sending, isFalse);
      expect(controller.memoryMode, MemoryMode.keywords);
      expect(controller.stage, BitHorizonStage.abstained);
      expect(controller.messages.last.authority, AnswerAuthority.abstention);
      expect(store.stageCalls, 1);
    },
  );

  test(
    'answer commit failure leaves retryable outbox without locking UI',
    () async {
      final _BridgeFixture fixture = await _BridgeFixture.start();
      addTearDown(fixture.close);
      final _FailingConversationStore store = _FailingConversationStore(
        failCommit: true,
      );
      final ChatController controller = ChatController(
        conversationId: 'thread',
        bridge: fixture.bridge,
        store: store,
        memoryMode: MemoryMode.forceNext,
      );
      addTearDown(controller.dispose);

      await controller.send('Question');

      expect(controller.sending, isFalse);
      expect(controller.memoryMode, MemoryMode.keywords);
      expect(controller.stage, BitHorizonStage.abstained);
      expect(controller.messages.last.text, 'deterministic response');
      expect(store.stageCalls, 1);
      expect(store.commitCalls, 1);
      expect(fixture.messageCalls, 1);
    },
  );

  test('failed staging does not consume the durable next sequence', () async {
    final _FailingConversationStore store = _FailingConversationStore(
      failStage: true,
    );
    final ChatController controller = ChatController(
      conversationId: 'thread',
      store: store,
      initialNextSequence: 6,
    );
    addTearDown(controller.dispose);

    await controller.send('First attempt fails before SQLite commit.');
    await controller.send('Second attempt reuses the free durable slot.');

    expect(store.stagedSequences, <int>[6, 6]);
    expect(controller.sending, isFalse);
  });

  test('recovery stops at first uncommitted exchange', () async {
    final _BridgeFixture fixture = await _BridgeFixture.start();
    addTearDown(fixture.close);
    final DateTime now = DateTime.utc(2026, 8, 25);
    final _FailingConversationStore store = _FailingConversationStore(
      failCommit: true,
      pending: <PendingUserMessage>[
        for (int index = 0; index < 2; index++)
          PendingUserMessage(
            conversationId: 'thread',
            sequence: index * 2,
            message: ChatMessage(
              id: 'pending-$index',
              role: MessageRole.user,
              text: 'Pending $index',
              createdAt: now,
            ),
            memoryMode: MemoryMode.keywords,
            keywords: const <String>[],
          ),
      ],
    );
    final ChatController controller = ChatController(
      conversationId: 'thread',
      bridge: fixture.bridge,
      store: store,
    );
    addTearDown(controller.dispose);

    await controller.recoverPending();

    expect(controller.sending, isFalse);
    expect(store.commitCalls, 1);
    expect(fixture.messageCalls, 1);
    expect(
      controller.messages.any((ChatMessage item) => item.id == 'pending-1'),
      isFalse,
    );
  });

  test('forged memory marker without lifecycle event is rejected', () async {
    final _BridgeFixture fixture = await _BridgeFixture.start(
      declaredMemoryConsulted: true,
    );
    addTearDown(fixture.close);
    final _FailingConversationStore store = _FailingConversationStore();
    final ChatController controller = ChatController(
      conversationId: 'thread',
      bridge: fixture.bridge,
      store: store,
    );
    addTearDown(controller.dispose);

    await controller.send('Untrusted marker');

    expect(controller.messages.last.authority, AnswerAuthority.abstention);
    expect(controller.messages.last.memoryConsulted, isFalse);
    expect(store.commitCalls, 0);
    expect(controller.sending, isFalse);
  });
}

class _FailingConversationStore implements ConversationStore {
  _FailingConversationStore({
    this.failStage = false,
    this.failCommit = false,
    this.pending = const <PendingUserMessage>[],
  });

  final bool failStage;
  final bool failCommit;
  final List<PendingUserMessage> pending;
  int stageCalls = 0;
  int commitCalls = 0;
  final List<int> stagedSequences = <int>[];

  @override
  Future<void> stageUserMessage(
    String conversationId,
    int sequence,
    ChatMessage message, {
    required MemoryMode memoryMode,
    String? providerId,
    List<String> keywords = const <String>[],
  }) async {
    stageCalls++;
    stagedSequences.add(sequence);
    if (failStage && stageCalls == 1) {
      throw StateError('injected stage failure');
    }
  }

  @override
  Future<void> commitExchange(ChatMessage user, ChatMessage assistant) async {
    commitCalls++;
    if (failCommit) throw StateError('injected commit failure');
  }

  @override
  Future<List<PendingUserMessage>> pendingUserMessages(
    String conversationId,
  ) async => List<PendingUserMessage>.unmodifiable(pending);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _BridgeFixture {
  _BridgeFixture._(this.server, this.bridge, this.declaredMemoryConsulted);

  final ServerSocket server;
  final ProofRayBridge bridge;
  final bool declaredMemoryConsulted;
  int messageCalls = 0;

  static Future<_BridgeFixture> start({
    bool declaredMemoryConsulted = false,
  }) async {
    final ServerSocket server = await ServerSocket.bind(
      InternetAddress.loopbackIPv4,
      0,
    );
    late _BridgeFixture fixture;
    server.listen((Socket socket) {
      socket
          .cast<List<int>>()
          .transform(utf8.decoder)
          .transform(const LineSplitter())
          .listen((String line) {
            final Map<String, Object?> request =
                (jsonDecode(line) as Map<Object?, Object?>).map(
                  (Object? key, Object? value) =>
                      MapEntry(key! as String, value),
                );
            final String requestId = request['request_id']! as String;
            final String method = request['method']! as String;
            if (method == 'message.send') fixture.messageCalls++;
            socket.write(
              '${jsonEncode(<String, Object?>{
                'schema': 'proofray.app.bridge.v1',
                'request_id': requestId,
                'event': method == 'bridge.authenticate' ? 'authenticated' : 'completed',
                'payload': method == 'bridge.authenticate' ? <String, Object?>{} : <String, Object?>{'text': 'deterministic response', 'authority': 'model', 'memory_consulted': fixture.declaredMemoryConsulted},
              })}\n',
            );
          });
    });
    final ProofRayBridge bridge = await ProofRayBridge.connect(
      port: server.port,
      token: List<String>.filled(64, 'a').join(),
    );
    fixture = _BridgeFixture._(server, bridge, declaredMemoryConsulted);
    return fixture;
  }

  Future<void> close() async {
    await bridge.close();
    await server.close();
  }
}
