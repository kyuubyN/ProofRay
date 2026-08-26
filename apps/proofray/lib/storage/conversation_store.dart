import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:drift/drift.dart';

import '../models/chat_models.dart';
import 'app_database.dart';
import 'external_database_host.dart';

abstract interface class ConversationStore {
  Future<void> ensureLocalProfile({
    required String profileId,
    required String displayName,
    required String locale,
    required String timezone,
  });
  Future<LocalProfile?> localProfile(String profileId);

  Future<void> ensureConversation({
    required String conversationId,
    required String profileId,
    required String title,
    required MemoryMode memoryMode,
  });

  Future<List<ChatMessage>> loadMessages(String conversationId);
  Future<int> nextSequence(String conversationId);
  Future<List<ConversationSummary>> conversations(String profileId);
  Future<void> stageUserMessage(
    String conversationId,
    int sequence,
    ChatMessage message, {
    required MemoryMode memoryMode,
    String? providerId,
    List<String> keywords = const <String>[],
  });
  Future<void> commitExchange(ChatMessage user, ChatMessage assistant);
  Future<List<PendingUserMessage>> pendingUserMessages(String conversationId);
  Future<List<MemoryObservation>> memoryObservations();
  Future<List<VerifiedSourceRecord>> verifiedSources();
  Future<void> markMemoryPurged(String messageId);
  Future<void> updateMemoryMode(String conversationId, MemoryMode mode);
  Future<void> renameConversation(String conversationId, String title);
  Future<void> commitConfirmedObservation(
    String conversationId,
    ChatMessage message,
  );
  Future<void> stageConfirmedObservation(
    String conversationId,
    int sequence,
    ChatMessage message,
  );
  Future<void> deleteConversationHistory(String conversationId);
}

class LocalProfile {
  const LocalProfile({
    required this.id,
    required this.displayName,
    required this.locale,
    required this.timezone,
  });

  final String id;
  final String displayName;
  final String locale;
  final String timezone;
}

class MemoryObservation {
  const MemoryObservation({
    required this.messageId,
    required this.conversationId,
    required this.text,
    required this.createdAt,
  });

  final String messageId;
  final String conversationId;
  final String text;
  final DateTime createdAt;

  String get sourceId => 'conversation:$conversationId:$messageId';
}

class PendingUserMessage {
  const PendingUserMessage({
    required this.conversationId,
    required this.sequence,
    required this.message,
    required this.memoryMode,
    required this.keywords,
    this.confirmation = false,
    this.providerId,
  });

  final String conversationId;
  final int sequence;
  final ChatMessage message;
  final MemoryMode memoryMode;
  final List<String> keywords;
  final bool confirmation;
  final String? providerId;
}

class VerifiedSourceRecord {
  const VerifiedSourceRecord({
    required this.factId,
    required this.sourceId,
    required this.text,
    required this.parentSha256,
    required this.lastUsedAt,
    this.sessionId,
    this.speaker,
  });

  final int factId;
  final String sourceId;
  final String text;
  final String parentSha256;
  final String? sessionId;
  final String? speaker;
  final DateTime lastUsedAt;
}

class DriftConversationStore implements ConversationStore {
  DriftConversationStore(this.database);

  final ProofRayDatabase database;
  static const ExternalDatabaseHost _externalDatabases = ExternalDatabaseHost();

  Future<Map<String, Object?>> handleHostRequest(
    String method,
    Map<String, Object?> payload,
  ) async {
    if (method.startsWith('connector.sqlite.') ||
        method.startsWith('connector.duckdb.')) {
      return _externalDatabases.handle(method, payload);
    }
    if (payload['store_key'] != 'personal-memory-v1') {
      throw StateError('unknown sidecar store');
    }
    switch (method) {
      case 'sidecar.load':
        return _loadSidecarPage(payload);
      case 'sidecar.replace_suffix':
        return _replaceSidecarSuffix(payload);
      case 'sidecar.replace_begin':
        return _beginSidecarReplacement(payload);
      case 'sidecar.replace_chunk':
        return _stageSidecarReplacementChunk(payload);
      case 'sidecar.replace_commit':
        return _commitSidecarReplacement(payload);
      default:
        throw StateError('unknown host method');
    }
  }

  Future<Map<String, Object?>> _loadSidecarPage(
    Map<String, Object?> payload,
  ) async {
    final Object? afterValue = payload['after_sequence'];
    final Object? limitValue = payload['limit'];
    if (afterValue is! int ||
        limitValue is! int ||
        afterValue < 0 ||
        limitValue < 1 ||
        limitValue > 512) {
      throw StateError('invalid sidecar page');
    }
    final List<QueryRow> rows = await database
        .customSelect(
          'SELECT sequence, canonical_payload FROM sidecar_records '
          'WHERE sequence > ? ORDER BY sequence LIMIT ?',
          variables: <Variable<Object>>[
            Variable<int>(afterValue),
            Variable<int>(limitValue),
          ],
        )
        .get();
    return <String, Object?>{
      'records': <String>[
        for (final QueryRow row in rows)
          base64Encode(row.read<Uint8List>('canonical_payload')),
      ],
      'complete': rows.length < limitValue,
    };
  }

  Future<Map<String, Object?>> _replaceSidecarSuffix(
    Map<String, Object?> payload,
  ) async {
    final Object? prefixValue = payload['common_prefix'];
    final Object? prefixDigestValue = payload['common_prefix_sha256'];
    final Object? recordsValue = payload['records'];
    if (prefixValue is! int ||
        prefixValue < 0 ||
        prefixDigestValue is! String ||
        recordsValue is! List<Object?> ||
        recordsValue.any((Object? item) => item is! String)) {
      throw StateError('invalid sidecar replacement');
    }
    final List<Uint8List> records = <Uint8List>[];
    for (final Object? encoded in recordsValue) {
      records.add(base64Decode(encoded! as String));
    }
    await database.transaction(() async {
      final QueryRow countRow = await database
          .customSelect('SELECT COUNT(*) AS row_count FROM sidecar_records')
          .getSingle();
      final int count = countRow.read<int>('row_count');
      if (prefixValue > count) {
        throw StateError('sidecar prefix is beyond durable state');
      }
      String previousRecordDigest = List<String>.filled(64, '0').join();
      if (prefixValue > 0) {
        final QueryRow? row = await database
            .customSelect(
              'SELECT record_sha256, canonical_payload FROM sidecar_records WHERE sequence=?',
              variables: <Variable<Object>>[Variable<int>(prefixValue)],
            )
            .getSingleOrNull();
        if (row == null ||
            sha256
                    .convert(row.read<Uint8List>('canonical_payload'))
                    .toString() !=
                prefixDigestValue) {
          throw StateError('sidecar prefix digest mismatch');
        }
        previousRecordDigest = row.read<String>('record_sha256');
      } else if (prefixDigestValue.isNotEmpty) {
        throw StateError('genesis prefix digest must be empty');
      }

      final List<_ValidatedSidecarRecord> validated =
          <_ValidatedSidecarRecord>[];
      for (int index = 0; index < records.length; index++) {
        final _ValidatedSidecarRecord record = _validateSidecarRecord(
          records[index],
          expectedSequence: prefixValue + index + 1,
          expectedPrevious: previousRecordDigest,
        );
        validated.add(record);
        previousRecordDigest = record.recordSha256;
      }
      final List<QueryRow> removedMessageRows = await database
          .customSelect(
            'SELECT message_id FROM sidecar_records '
            'WHERE sequence > ? AND message_id IS NOT NULL',
            variables: <Variable<Object>>[Variable<int>(prefixValue)],
          )
          .get();
      final Set<String> removedMessageIds = <String>{
        for (final QueryRow row in removedMessageRows)
          row.read<String>('message_id'),
      };
      await database.customStatement(
        'DELETE FROM sidecar_records WHERE sequence > ?',
        <Object?>[prefixValue],
      );
      final int committedAt = DateTime.now().toUtc().millisecondsSinceEpoch;
      for (final _ValidatedSidecarRecord record in validated) {
        await database.customStatement(
          'INSERT INTO sidecar_records '
          '(sequence, previous_sha256, record_sha256, canonical_payload, '
          'message_id, committed_at) VALUES (?, ?, ?, ?, ?, ?)',
          <Object?>[
            record.sequence,
            record.previousSha256,
            record.recordSha256,
            record.payload,
            record.messageId,
            committedAt,
          ],
        );
        if (record.messageId != null) {
          final QueryRow? messageRow = await database
              .customSelect(
                'SELECT memory_source_id FROM messages WHERE id=?',
                variables: <Variable<Object>>[
                  Variable<String>(record.messageId!),
                ],
              )
              .getSingleOrNull();
          if (messageRow == null ||
              messageRow.read<String?>('memory_source_id') !=
                  record.memorySourceId) {
            throw StateError(
              'sidecar message authority does not match staged source',
            );
          }
          await database.customStatement(
            'UPDATE outbox_operations SET state=?, updated_at=? WHERE id=?',
            <Object?>[
              'memory_committed',
              committedAt,
              'ingest:${record.messageId}',
            ],
          );
          await database.customStatement(
            'UPDATE messages SET memory_eligible=1 WHERE id=?',
            <Object?>[record.messageId],
          );
        }
      }
      final Set<String> survivingChangedMessageIds = <String>{
        for (final _ValidatedSidecarRecord record in validated)
          if (record.messageId != null) record.messageId!,
      };
      for (final String removedMessageId in removedMessageIds.difference(
        survivingChangedMessageIds,
      )) {
        await database.customStatement(
          'UPDATE messages SET memory_eligible=0 WHERE id=?',
          <Object?>[removedMessageId],
        );
      }
    });
    return <String, Object?>{'committed': true};
  }

  Future<Map<String, Object?>> _beginSidecarReplacement(
    Map<String, Object?> payload,
  ) async {
    final Object? transactionValue = payload['transaction_id'];
    final Object? prefixValue = payload['common_prefix'];
    final Object? digestValue = payload['common_prefix_sha256'];
    final Object? totalValue = payload['total_records'];
    if (transactionValue is! String ||
        !RegExp(r'^[0-9a-f]{64}$').hasMatch(transactionValue) ||
        prefixValue is! int ||
        prefixValue < 0 ||
        digestValue is! String ||
        totalValue is! int ||
        totalValue < 1) {
      throw StateError('invalid staged sidecar replacement');
    }
    final QueryRow? existing = await database
        .customSelect(
          'SELECT common_prefix, common_prefix_sha256, total_records '
          'FROM sidecar_replacement_plans WHERE transaction_id=?',
          variables: <Variable<Object>>[Variable<String>(transactionValue)],
        )
        .getSingleOrNull();
    if (existing == null) {
      await database.customStatement(
        'INSERT INTO sidecar_replacement_plans '
        '(transaction_id, common_prefix, common_prefix_sha256, total_records, created_at) '
        'VALUES (?, ?, ?, ?, ?)',
        <Object?>[
          transactionValue,
          prefixValue,
          digestValue,
          totalValue,
          DateTime.now().toUtc().millisecondsSinceEpoch,
        ],
      );
    } else if (existing.read<int>('common_prefix') != prefixValue ||
        existing.read<String>('common_prefix_sha256') != digestValue ||
        existing.read<int>('total_records') != totalValue) {
      throw StateError('staged sidecar replacement identity collision');
    }
    return <String, Object?>{'staged': true};
  }

  Future<Map<String, Object?>> _stageSidecarReplacementChunk(
    Map<String, Object?> payload,
  ) async {
    final Object? transactionValue = payload['transaction_id'];
    final Object? indexValue = payload['chunk_index'];
    final Object? recordsValue = payload['records'];
    if (transactionValue is! String ||
        !RegExp(r'^[0-9a-f]{64}$').hasMatch(transactionValue) ||
        indexValue is! int ||
        indexValue < 0 ||
        recordsValue is! List<Object?> ||
        recordsValue.isEmpty ||
        recordsValue.any((Object? item) => item is! String)) {
      throw StateError('invalid staged sidecar chunk');
    }
    final Uint8List encoded = Uint8List.fromList(
      utf8.encode(jsonEncode(recordsValue)),
    );
    final QueryRow? existing = await database
        .customSelect(
          'SELECT encoded_records FROM sidecar_replacement_chunks '
          'WHERE transaction_id=? AND chunk_index=?',
          variables: <Variable<Object>>[
            Variable<String>(transactionValue),
            Variable<int>(indexValue),
          ],
        )
        .getSingleOrNull();
    if (existing == null) {
      await database.customStatement(
        'INSERT INTO sidecar_replacement_chunks '
        '(transaction_id, chunk_index, encoded_records) VALUES (?, ?, ?)',
        <Object?>[transactionValue, indexValue, encoded],
      );
    } else if (!_bytesEqual(
      existing.read<Uint8List>('encoded_records'),
      encoded,
    )) {
      throw StateError('staged sidecar chunk identity collision');
    }
    return <String, Object?>{'staged': true};
  }

  Future<Map<String, Object?>> _commitSidecarReplacement(
    Map<String, Object?> payload,
  ) async {
    final Object? transactionValue = payload['transaction_id'];
    if (transactionValue is! String ||
        !RegExp(r'^[0-9a-f]{64}$').hasMatch(transactionValue)) {
      throw StateError('invalid staged sidecar commit');
    }
    final QueryRow? plan = await database
        .customSelect(
          'SELECT common_prefix, common_prefix_sha256, total_records '
          'FROM sidecar_replacement_plans WHERE transaction_id=?',
          variables: <Variable<Object>>[Variable<String>(transactionValue)],
        )
        .getSingleOrNull();
    if (plan == null) throw StateError('unknown staged sidecar replacement');
    final List<QueryRow> chunks = await database
        .customSelect(
          'SELECT chunk_index, encoded_records FROM sidecar_replacement_chunks '
          'WHERE transaction_id=? ORDER BY chunk_index',
          variables: <Variable<Object>>[Variable<String>(transactionValue)],
        )
        .get();
    final List<Object?> records = <Object?>[];
    for (int index = 0; index < chunks.length; index++) {
      if (chunks[index].read<int>('chunk_index') != index) {
        throw StateError('staged sidecar chunks are not contiguous');
      }
      final Object? decoded = jsonDecode(
        utf8.decode(
          chunks[index].read<Uint8List>('encoded_records'),
          allowMalformed: false,
        ),
      );
      if (decoded is! List<Object?> ||
          decoded.any((Object? item) => item is! String)) {
        throw StateError('staged sidecar chunk is corrupt');
      }
      records.addAll(decoded);
    }
    if (records.length != plan.read<int>('total_records')) {
      throw StateError('staged sidecar replacement is incomplete');
    }
    final Map<String, Object?> result = await _replaceSidecarSuffix(
      <String, Object?>{
        'common_prefix': plan.read<int>('common_prefix'),
        'common_prefix_sha256': plan.read<String>('common_prefix_sha256'),
        'records': records,
      },
    );
    await database.transaction(() async {
      await database.customStatement(
        'DELETE FROM sidecar_replacement_chunks WHERE transaction_id=?',
        <Object?>[transactionValue],
      );
      await database.customStatement(
        'DELETE FROM sidecar_replacement_plans WHERE transaction_id=?',
        <Object?>[transactionValue],
      );
    });
    return result;
  }

  @override
  Future<void> ensureLocalProfile({
    required String profileId,
    required String displayName,
    required String locale,
    required String timezone,
  }) => database.customStatement(
    'INSERT INTO profiles (id, display_name, locale, timezone, created_at) '
    'VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET '
    'display_name=excluded.display_name, locale=excluded.locale, timezone=excluded.timezone',
    <Object?>[
      profileId,
      displayName,
      locale,
      timezone,
      DateTime.now().millisecondsSinceEpoch,
    ],
  );

  @override
  Future<LocalProfile?> localProfile(String profileId) async {
    final QueryRow? row = await database
        .customSelect(
          'SELECT id, display_name, locale, timezone FROM profiles WHERE id=?',
          variables: <Variable<Object>>[Variable<String>(profileId)],
        )
        .getSingleOrNull();
    return row == null
        ? null
        : LocalProfile(
            id: row.read<String>('id'),
            displayName: row.read<String>('display_name'),
            locale: row.read<String>('locale'),
            timezone: row.read<String>('timezone'),
          );
  }

  @override
  Future<void> ensureConversation({
    required String conversationId,
    required String profileId,
    required String title,
    required MemoryMode memoryMode,
  }) => database.customStatement(
    'INSERT INTO conversations '
    '(id, profile_id, title, memory_mode, created_at, updated_at, deleted_at) '
    'VALUES (?, ?, ?, ?, ?, ?, NULL) ON CONFLICT(id) DO UPDATE SET '
    'memory_mode=excluded.memory_mode, updated_at=excluded.updated_at',
    <Object?>[
      conversationId,
      profileId,
      title,
      memoryMode.name,
      DateTime.now().millisecondsSinceEpoch,
      DateTime.now().millisecondsSinceEpoch,
    ],
  );

  @override
  Future<List<ChatMessage>> loadMessages(String conversationId) async {
    final List<QueryRow> rows = await database
        .customSelect(
          'SELECT m.id, m.role, m.authority, m.content, m.memory_consulted, m.created_at, '
          'p.id AS proof_id, p.deterministic_answer, p.certificate, p.method, '
          'p.query_digest, p.documents_considered, p.verified_candidates, p.answer_bytes, '
          'p.text_truncated '
          'FROM messages m LEFT JOIN proof_runs p ON p.message_id=m.id '
          'WHERE m.conversation_id=? AND m.deleted_at IS NULL ORDER BY m.sequence',
          variables: <Variable<Object>>[Variable<String>(conversationId)],
        )
        .get();
    final List<QueryRow> sourceRows = await database
        .customSelect(
          'SELECT p.message_id, s.fact_id, s.source_id, s.text_value, s.parent_sha256, '
          's.session_id, s.speaker, s.span_start, s.span_end '
          'FROM proof_sources s JOIN proof_runs p ON p.id=s.proof_run_id '
          'JOIN messages m ON m.id=p.message_id '
          'WHERE m.conversation_id=? AND m.deleted_at IS NULL '
          'ORDER BY p.message_id, s.ordinal',
          variables: <Variable<Object>>[Variable<String>(conversationId)],
        )
        .get();
    final Map<String, List<ProofSource>> sourcesByMessage =
        <String, List<ProofSource>>{};
    for (final QueryRow row in sourceRows) {
      final int? spanStart = row.read<int?>('span_start');
      final int? spanEnd = row.read<int?>('span_end');
      sourcesByMessage
          .putIfAbsent(row.read<String>('message_id'), () => <ProofSource>[])
          .add(
            ProofSource(
              factId: row.read<int>('fact_id'),
              sourceId: row.read<String>('source_id'),
              text: row.read<String>('text_value'),
              parentSha256: row.read<String>('parent_sha256'),
              sessionId: row.read<String?>('session_id'),
              speaker: row.read<String?>('speaker'),
              sourceSpan: spanStart == null || spanEnd == null
                  ? null
                  : (spanStart, spanEnd),
              textDeferred: row.read<String>('text_value').isEmpty,
            ),
          );
    }
    return rows
        .map((QueryRow row) {
          final String messageId = row.read<String>('id');
          final String role = row.read<String>('role');
          final String authority = row.read<String>('authority');
          final Uint8List? certificate = row.read<Uint8List?>('certificate');
          return ChatMessage(
            id: messageId,
            role: MessageRole.values.firstWhere(
              (MessageRole item) => item.name == role,
              orElse: () => MessageRole.assistant,
            ),
            text: row.read<String>('content'),
            createdAt: DateTime.fromMillisecondsSinceEpoch(
              row.read<int>('created_at'),
              isUtc: true,
            ),
            authority: AnswerAuthority.values.firstWhere(
              (AnswerAuthority item) => item.name == authority,
              orElse: () => AnswerAuthority.model,
            ),
            memoryConsulted: row.read<int>('memory_consulted') != 0,
            certifiedText: row.read<String?>('deterministic_answer'),
            certificateHex: certificate == null ? null : _hex(certificate),
            proofRunId: row.read<String?>('proof_id'),
            proofMethod: row.read<String?>('method'),
            queryDigest: row.read<String?>('query_digest'),
            documentsConsidered: row.read<int?>('documents_considered') ?? 0,
            verifiedCandidates: row.read<int?>('verified_candidates') ?? 0,
            answerBytes: row.read<int?>('answer_bytes') ?? 0,
            textTruncated: (row.read<int?>('text_truncated') ?? 0) != 0,
            sources: List<ProofSource>.unmodifiable(
              sourcesByMessage[messageId] ?? const <ProofSource>[],
            ),
          );
        })
        .toList(growable: false);
  }

  @override
  Future<List<ConversationSummary>> conversations(String profileId) async {
    final List<QueryRow> rows = await database
        .customSelect(
          'SELECT id, title, memory_mode, updated_at FROM conversations '
          'WHERE profile_id=? AND deleted_at IS NULL ORDER BY updated_at DESC',
          variables: <Variable<Object>>[Variable<String>(profileId)],
        )
        .get();
    return <ConversationSummary>[
      for (final QueryRow row in rows)
        ConversationSummary(
          id: row.read<String>('id'),
          title: row.read<String>('title'),
          updatedAt: DateTime.fromMillisecondsSinceEpoch(
            row.read<int>('updated_at'),
            isUtc: true,
          ),
          memoryMode: MemoryMode.values.firstWhere(
            (MemoryMode item) => item.name == row.read<String>('memory_mode'),
            orElse: () => MemoryMode.keywords,
          ),
        ),
    ];
  }

  @override
  Future<int> nextSequence(String conversationId) async {
    final QueryRow row = await database
        .customSelect(
          'SELECT COALESCE(MAX(sequence) + 1, 0) AS next_sequence '
          'FROM messages WHERE conversation_id=?',
          variables: <Variable<Object>>[Variable<String>(conversationId)],
        )
        .getSingle();
    return row.read<int>('next_sequence');
  }

  @override
  Future<List<MemoryObservation>> memoryObservations() async {
    final List<QueryRow> rows = await database
        .customSelect(
          'SELECT id, conversation_id, content, created_at FROM messages '
          'WHERE memory_eligible=1 ORDER BY created_at DESC',
        )
        .get();
    return <MemoryObservation>[
      for (final QueryRow row in rows)
        MemoryObservation(
          messageId: row.read<String>('id'),
          conversationId: row.read<String>('conversation_id'),
          text: row.read<String>('content'),
          createdAt: DateTime.fromMillisecondsSinceEpoch(
            row.read<int>('created_at'),
            isUtc: true,
          ),
        ),
    ];
  }

  @override
  Future<List<VerifiedSourceRecord>> verifiedSources() async {
    final List<QueryRow> rows = await database
        .customSelect(
          'SELECT s.fact_id, s.source_id, MAX(s.text_value) AS text_value, '
          'MAX(s.parent_sha256) AS parent_sha256, MAX(s.session_id) AS session_id, '
          'MAX(s.speaker) AS speaker, MAX(p.created_at) AS last_used_at '
          'FROM proof_sources s JOIN proof_runs p ON p.id=s.proof_run_id '
          'GROUP BY s.fact_id, s.source_id ORDER BY last_used_at DESC LIMIT 200',
        )
        .get();
    return <VerifiedSourceRecord>[
      for (final QueryRow row in rows)
        VerifiedSourceRecord(
          factId: row.read<int>('fact_id'),
          sourceId: row.read<String>('source_id'),
          text: row.read<String>('text_value'),
          parentSha256: row.read<String>('parent_sha256'),
          sessionId: row.read<String?>('session_id'),
          speaker: row.read<String?>('speaker'),
          lastUsedAt: DateTime.fromMillisecondsSinceEpoch(
            row.read<int>('last_used_at'),
            isUtc: true,
          ),
        ),
    ];
  }

  @override
  Future<void> markMemoryPurged(String messageId) => database.customStatement(
    'UPDATE messages SET memory_eligible=0 WHERE id=?',
    <Object?>[messageId],
  );

  @override
  Future<void> updateMemoryMode(String conversationId, MemoryMode mode) =>
      database.customStatement(
        'UPDATE conversations SET memory_mode=?, updated_at=? WHERE id=?',
        <Object?>[
          mode.name,
          DateTime.now().toUtc().millisecondsSinceEpoch,
          conversationId,
        ],
      );

  @override
  Future<void> renameConversation(String conversationId, String title) =>
      database.customStatement(
        'UPDATE conversations SET title=?, updated_at=? WHERE id=?',
        <Object?>[
          title,
          DateTime.now().toUtc().millisecondsSinceEpoch,
          conversationId,
        ],
      );

  @override
  Future<void> commitConfirmedObservation(
    String conversationId,
    ChatMessage message,
  ) async {
    await database.transaction(() async {
      await database.customStatement(
        'UPDATE messages SET status=\'committed\', memory_eligible=1 WHERE id=?',
        <Object?>[message.id],
      );
      await database.customStatement(
        'DELETE FROM outbox_operations WHERE id=?',
        <Object?>['confirm:${message.id}'],
      );
    });
  }

  @override
  Future<void> stageConfirmedObservation(
    String conversationId,
    int sequence,
    ChatMessage message,
  ) async {
    final Uint8List payload = Uint8List.fromList(
      utf8.encode(
        jsonEncode(<String, Object?>{
          'schema': 'proofray.app.outbox.confirm.v1',
          'conversation_id': conversationId,
          'message_id': message.id,
          'text': message.text,
          'created_at': message.createdAt.toIso8601String(),
          'sequence': sequence,
        }),
      ),
    );
    await database.transaction(() async {
      await _insertMessage(
        conversationId,
        sequence,
        message,
        status: 'pending',
        memorySourceId: 'conversation:$conversationId:${message.id}',
      );
      await database.customStatement(
        'INSERT OR REPLACE INTO outbox_operations '
        '(id, operation_kind, payload, state, attempts, created_at, updated_at) '
        'VALUES (?, \'memory.confirm\', ?, \'pending\', 0, ?, ?)',
        <Object?>[
          'confirm:${message.id}',
          payload,
          message.createdAt.millisecondsSinceEpoch,
          message.createdAt.millisecondsSinceEpoch,
        ],
      );
    });
  }

  @override
  Future<void> deleteConversationHistory(String conversationId) async {
    final int now = DateTime.now().toUtc().millisecondsSinceEpoch;
    await database.transaction(() async {
      await database.customStatement(
        'DELETE FROM outbox_operations WHERE id IN '
        '(SELECT (\'ingest:\' || id) FROM messages WHERE conversation_id=?) '
        'OR id IN '
        '(SELECT (\'confirm:\' || id) FROM messages WHERE conversation_id=?)',
        <Object?>[conversationId, conversationId],
      );
      await database.customStatement(
        'UPDATE messages SET deleted_at=? WHERE conversation_id=?',
        <Object?>[now, conversationId],
      );
      await database.customStatement(
        'UPDATE conversations SET deleted_at=?, updated_at=? WHERE id=?',
        <Object?>[now, now, conversationId],
      );
    });
  }

  @override
  Future<void> stageUserMessage(
    String conversationId,
    int sequence,
    ChatMessage message, {
    required MemoryMode memoryMode,
    String? providerId,
    List<String> keywords = const <String>[],
  }) async {
    final String operationId = 'ingest:${message.id}';
    final Uint8List payload = Uint8List.fromList(
      utf8.encode(
        jsonEncode(<String, Object?>{
          'schema': 'proofray.app.outbox.message.v1',
          'conversation_id': conversationId,
          'message_id': message.id,
          'text': message.text,
          'created_at': message.createdAt.toIso8601String(),
          'sequence': sequence,
          'memory_mode': memoryMode.name,
          'provider_id': ?providerId,
          if (keywords.isNotEmpty) 'keywords': keywords,
        }),
      ),
    );
    await database.transaction(() async {
      await _insertMessage(
        conversationId,
        sequence,
        message,
        status: 'pending',
        memorySourceId: 'conversation:$conversationId:${message.id}',
      );
      await _insertMessage(
        conversationId,
        sequence + 1,
        ChatMessage(
          id: 'answer_${message.id}',
          role: MessageRole.assistant,
          text: '',
          createdAt: message.createdAt,
          authority: AnswerAuthority.pending,
        ),
        status: 'pending',
      );
      await database.customStatement(
        'INSERT OR REPLACE INTO outbox_operations '
        '(id, operation_kind, payload, state, attempts, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        <Object?>[
          operationId,
          'message.ingest',
          payload,
          'pending',
          0,
          message.createdAt.millisecondsSinceEpoch,
          message.createdAt.millisecondsSinceEpoch,
        ],
      );
    });
  }

  @override
  Future<List<PendingUserMessage>> pendingUserMessages(
    String conversationId,
  ) async {
    final List<QueryRow> rows = await database
        .customSelect(
          'SELECT o.payload, o.operation_kind FROM outbox_operations o '
          'JOIN messages m ON (o.id = (\'ingest:\' || m.id) OR o.id = (\'confirm:\' || m.id)) '
          'WHERE m.conversation_id=? AND o.state IN (\'pending\', \'memory_committed\') '
          'ORDER BY m.sequence',
          variables: <Variable<Object>>[Variable<String>(conversationId)],
        )
        .get();
    final List<PendingUserMessage> result = <PendingUserMessage>[];
    for (final QueryRow row in rows) {
      final Object? decoded = jsonDecode(
        utf8.decode(row.read<Uint8List>('payload'), allowMalformed: false),
      );
      final String operationKind = row.read<String>('operation_kind');
      if (decoded is! Map<Object?, Object?> ||
          decoded['schema'] !=
              (operationKind == 'memory.confirm'
                  ? 'proofray.app.outbox.confirm.v1'
                  : 'proofray.app.outbox.message.v1') ||
          decoded['conversation_id'] is! String ||
          decoded['message_id'] is! String ||
          decoded['text'] is! String ||
          decoded['created_at'] is! String ||
          decoded['sequence'] is! int ||
          (operationKind != 'memory.confirm' &&
              decoded['memory_mode'] is! String)) {
        throw StateError('invalid encrypted outbox operation');
      }
      result.add(
        PendingUserMessage(
          conversationId: decoded['conversation_id']! as String,
          sequence: decoded['sequence']! as int,
          message: ChatMessage(
            id: decoded['message_id']! as String,
            role: MessageRole.user,
            text: decoded['text']! as String,
            createdAt: DateTime.parse(decoded['created_at']! as String).toUtc(),
          ),
          memoryMode: operationKind == 'memory.confirm'
              ? MemoryMode.off
              : MemoryMode.values.firstWhere(
                  (MemoryMode item) => item.name == decoded['memory_mode'],
                  orElse: () => throw StateError('invalid outbox memory mode'),
                ),
          keywords: decoded['keywords'] is List<Object?>
              ? (decoded['keywords']! as List<Object?>)
                    .whereType<String>()
                    .toList()
              : const <String>[],
          confirmation: operationKind == 'memory.confirm',
          providerId: decoded['provider_id'] as String?,
        ),
      );
    }
    return result;
  }

  @override
  Future<void> commitExchange(ChatMessage user, ChatMessage assistant) async {
    await database.transaction(() async {
      if (assistant.id != 'answer_${user.id}') {
        throw StateError(
          'assistant identity differs from its staged user turn',
        );
      }
      final QueryRow? staged = await database
          .customSelect(
            'SELECT conversation_id, sequence FROM messages WHERE id=?',
            variables: <Variable<Object>>[Variable<String>(user.id)],
          )
          .getSingleOrNull();
      if (staged == null) {
        throw StateError('user message disappeared before commit');
      }
      final String conversationId = staged.read<String>('conversation_id');
      final int assistantSequence = staged.read<int>('sequence') + 1;
      await database.customStatement(
        'UPDATE messages SET status=? WHERE id=?',
        <Object?>['committed', user.id],
      );
      await database.customStatement(
        'DELETE FROM outbox_operations WHERE id=?',
        <Object?>['ingest:${user.id}'],
      );
      await _insertMessage(
        conversationId,
        assistantSequence,
        assistant,
        status: 'committed',
      );
      await database.customStatement(
        'UPDATE conversations SET updated_at=? WHERE id=?',
        <Object?>[
          DateTime.now().toUtc().millisecondsSinceEpoch,
          conversationId,
        ],
      );
      if (assistant.memoryConsulted) {
        await _insertProof(assistant);
      }
    });
  }

  Future<void> _insertMessage(
    String conversationId,
    int sequence,
    ChatMessage message, {
    required String status,
    bool memoryEligible = false,
    String? memorySourceId,
  }) => database.customStatement(
    'INSERT INTO messages '
    '(id, conversation_id, sequence, role, origin, authority, content, '
    'memory_eligible, memory_consulted, memory_source_id, status, created_at, deleted_at) '
    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL) '
    'ON CONFLICT(id) DO UPDATE SET '
    'conversation_id=excluded.conversation_id, sequence=excluded.sequence, '
    'role=excluded.role, origin=excluded.origin, authority=excluded.authority, '
    'content=excluded.content, memory_eligible=excluded.memory_eligible, '
    'memory_consulted=excluded.memory_consulted, '
    'memory_source_id=excluded.memory_source_id, status=excluded.status, '
    'created_at=excluded.created_at, deleted_at=NULL',
    <Object?>[
      message.id,
      conversationId,
      sequence,
      message.role.name,
      message.role == MessageRole.user ? 'user' : 'proofray_or_model',
      message.authority.name,
      message.text,
      memoryEligible,
      message.memoryConsulted,
      memorySourceId,
      status,
      message.createdAt.millisecondsSinceEpoch,
    ],
  );

  Future<void> _insertProof(ChatMessage message) async {
    final String proofId = message.proofRunId ?? 'proof:${message.id}';
    if (message.certificateHex != null &&
        (!RegExp(r'^[0-9a-f]+$').hasMatch(message.certificateHex!) ||
            message.certificateHex!.length.isOdd)) {
      throw const FormatException('proof certificate is not canonical hex');
    }
    final Uint8List? certificate = message.certificateHex == null
        ? null
        : Uint8List.fromList(<int>[
            for (
              int index = 0;
              index < message.certificateHex!.length;
              index += 2
            )
              int.parse(
                message.certificateHex!.substring(index, index + 2),
                radix: 16,
              ),
          ]);
    await database.customStatement(
      'INSERT INTO proof_runs '
      '(id, message_id, state, action, deterministic_answer, displayed_rewrite, method, '
      'certificate, query_digest, documents_considered, verified_candidates, answer_bytes, '
      'text_truncated, created_at) '
      'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
      'ON CONFLICT(id) DO UPDATE SET '
      'message_id=excluded.message_id, state=excluded.state, action=excluded.action, '
      'deterministic_answer=excluded.deterministic_answer, '
      'displayed_rewrite=excluded.displayed_rewrite, method=excluded.method, '
      'certificate=excluded.certificate, query_digest=excluded.query_digest, '
      'documents_considered=excluded.documents_considered, '
      'verified_candidates=excluded.verified_candidates, '
      'answer_bytes=excluded.answer_bytes, text_truncated=excluded.text_truncated, '
      'created_at=excluded.created_at',
      <Object?>[
        proofId,
        message.id,
        message.authority.name,
        message.authority == AnswerAuthority.proved
            ? 'answer'
            : message.authority.name,
        message.certifiedText,
        message.text == message.certifiedText ? null : message.text,
        message.proofMethod,
        certificate,
        message.queryDigest ?? '',
        message.documentsConsidered,
        message.verifiedCandidates,
        message.answerBytes,
        message.textTruncated,
        message.createdAt.millisecondsSinceEpoch,
      ],
    );
    await database.customStatement(
      'DELETE FROM proof_sources WHERE proof_run_id=?',
      <Object?>[proofId],
    );
    for (int index = 0; index < message.sources.length; index++) {
      final ProofSource source = message.sources[index];
      await database.customStatement(
        'INSERT OR REPLACE INTO proof_sources '
        '(proof_run_id, ordinal, fact_id, source_id, text_value, parent_sha256, '
        'session_id, speaker, span_start, span_end) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        <Object?>[
          proofId,
          index,
          source.factId,
          source.sourceId,
          source.text,
          source.parentSha256,
          source.sessionId,
          source.speaker,
          source.sourceSpan?.$1,
          source.sourceSpan?.$2,
        ],
      );
    }
  }
}

class _ValidatedSidecarRecord {
  const _ValidatedSidecarRecord({
    required this.sequence,
    required this.previousSha256,
    required this.recordSha256,
    required this.payload,
    this.messageId,
    this.memorySourceId,
  });

  final int sequence;
  final String previousSha256;
  final String recordSha256;
  final Uint8List payload;
  final String? messageId;
  final String? memorySourceId;
}

_ValidatedSidecarRecord _validateSidecarRecord(
  Uint8List payload, {
  required int expectedSequence,
  required String expectedPrevious,
}) {
  final Object? decoded = jsonDecode(
    utf8.decode(payload, allowMalformed: false),
  );
  if (decoded is! Map<Object?, Object?>) {
    throw StateError('sidecar record must be an object');
  }
  final Map<String, Object?> record = <String, Object?>{
    for (final MapEntry<Object?, Object?> entry in decoded.entries)
      if (entry.key is String) entry.key as String: entry.value,
  };
  final Object? claimed = record.remove('record_sha256');
  if (record.length != decoded.length - 1 ||
      record['schema'] != 'horizon.authorized-sidecar-batch.v1' ||
      record['sequence'] != expectedSequence ||
      record['previous_sha256'] != expectedPrevious ||
      claimed is! String ||
      sha256.convert(utf8.encode(_canonicalJson(record))).toString() !=
          claimed) {
    throw StateError('sidecar record chain or digest is invalid');
  }
  record['record_sha256'] = claimed;
  if (_canonicalJson(record) != utf8.decode(payload)) {
    throw StateError('sidecar record bytes are not canonical');
  }
  final Object? sourceId = record['source_id'];
  final List<String> messageParts = sourceId is String
      ? sourceId.split(':')
      : const <String>[];
  final bool messageBatch =
      messageParts.length == 3 &&
      messageParts.first == 'message' &&
      messageParts[1].isNotEmpty &&
      messageParts[2].isNotEmpty;
  return _ValidatedSidecarRecord(
    sequence: expectedSequence,
    previousSha256: expectedPrevious,
    recordSha256: claimed,
    payload: payload,
    messageId: messageBatch ? messageParts[2] : null,
    memorySourceId: messageBatch
        ? 'conversation:${messageParts[1]}:${messageParts[2]}'
        : null,
  );
}

String _canonicalJson(Object? value) => jsonEncode(_canonicalValue(value));

String _hex(Uint8List value) =>
    value.map((int byte) => byte.toRadixString(16).padLeft(2, '0')).join();

bool _bytesEqual(Uint8List left, Uint8List right) {
  if (left.length != right.length) return false;
  for (int index = 0; index < left.length; index++) {
    if (left[index] != right[index]) return false;
  }
  return true;
}

Object? _canonicalValue(Object? value) {
  if (value is Map<Object?, Object?>) {
    final List<String> keys = value.keys.whereType<String>().toList()..sort();
    return <String, Object?>{
      for (final String key in keys) key: _canonicalValue(value[key]),
    };
  }
  if (value is List<Object?>) {
    return <Object?>[for (final Object? item in value) _canonicalValue(item)];
  }
  return value;
}
