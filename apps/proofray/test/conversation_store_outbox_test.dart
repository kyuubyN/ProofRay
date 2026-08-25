import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:drift/drift.dart' hide isNull;
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/models/chat_models.dart';
import 'package:proofray_app/storage/app_database.dart';
import 'package:proofray_app/storage/conversation_store.dart';

void main() {
  late ProofRayDatabase database;
  late DriftConversationStore store;

  setUp(() async {
    database = ProofRayDatabase(NativeDatabase.memory());
    store = DriftConversationStore(database);
    await store.ensureLocalProfile(
      profileId: 'owner',
      displayName: 'User',
      locale: 'en',
      timezone: 'UTC',
    );
    await store.ensureConversation(
      conversationId: 'thread',
      profileId: 'owner',
      title: 'Thread',
      memoryMode: MemoryMode.keywords,
    );
  });

  tearDown(() => database.close());

  test(
    'host ACK commits exact message authority before exchange publication',
    () async {
      final ChatMessage user = ChatMessage(
        id: 'm1',
        role: MessageRole.user,
        text: 'My bicycle is cobalt blue.',
        createdAt: DateTime.utc(2026, 8, 25),
      );
      await store.stageUserMessage(
        'thread',
        0,
        user,
        memoryMode: MemoryMode.keywords,
      );

      final List<QueryRow> staged = await database
          .customSelect(
            'SELECT id, sequence, role, status, memory_source_id FROM messages '
            'ORDER BY sequence',
          )
          .get();
      expect(staged.map((QueryRow row) => row.read<String>('id')), <String>[
        'm1',
        'answer_m1',
      ]);
      expect(staged.map((QueryRow row) => row.read<int>('sequence')), <int>[
        0,
        1,
      ]);
      expect(
        staged.first.read<String>('memory_source_id'),
        'conversation:thread:m1',
      );
      expect(staged.last.read<String?>('memory_source_id'), isNull);

      final Uint8List record = _sidecarRecord(
        sequence: 1,
        previousSha256: List<String>.filled(64, '0').join(),
        sourceId: 'message:thread:m1',
        content: user.text,
      );
      expect(
        await store.handleHostRequest(
          'sidecar.replace_suffix',
          <String, Object?>{
            'store_key': 'personal-memory-v1',
            'common_prefix': 0,
            'common_prefix_sha256': '',
            'records': <String>[base64Encode(record)],
          },
        ),
        <String, Object?>{'committed': true},
      );

      final List<MemoryObservation> observations = await store
          .memoryObservations();
      expect(observations.single.sourceId, 'conversation:thread:m1');
      expect(
        (await store.pendingUserMessages('thread')).single.message.id,
        'm1',
      );

      await store.commitExchange(
        user,
        ChatMessage(
          id: 'answer_m1',
          role: MessageRole.assistant,
          text: 'Saved.',
          createdAt: user.createdAt,
        ),
      );
      expect(await store.pendingUserMessages('thread'), isEmpty);
      expect((await store.loadMessages('thread')).length, 2);
    },
  );

  test(
    'unknown message authority rolls back sidecar and eligibility',
    () async {
      final ChatMessage user = ChatMessage(
        id: 'm1',
        role: MessageRole.user,
        text: 'Exact observation.',
        createdAt: DateTime.utc(2026, 8, 25),
      );
      await store.stageUserMessage(
        'thread',
        0,
        user,
        memoryMode: MemoryMode.keywords,
      );
      final Uint8List record = _sidecarRecord(
        sequence: 1,
        previousSha256: List<String>.filled(64, '0').join(),
        sourceId: 'message:thread:unknown',
        content: 'forged',
      );
      await expectLater(
        store.handleHostRequest('sidecar.replace_suffix', <String, Object?>{
          'store_key': 'personal-memory-v1',
          'common_prefix': 0,
          'common_prefix_sha256': '',
          'records': <String>[base64Encode(record)],
        }),
        throwsStateError,
      );
      final QueryRow count = await database
          .customSelect('SELECT COUNT(*) AS n FROM sidecar_records')
          .getSingle();
      expect(count.read<int>('n'), 0);
      expect(await store.memoryObservations(), isEmpty);
      expect(
        (await store.pendingUserMessages('thread')).single.message.id,
        'm1',
      );
    },
  );

  test(
    'chunked sidecar replacement is invisible until complete and replayable',
    () async {
      final ChatMessage user = ChatMessage(
        id: 'm1',
        role: MessageRole.user,
        text: 'Durable observation.',
        createdAt: DateTime.utc(2026, 8, 25),
      );
      await store.stageUserMessage(
        'thread',
        0,
        user,
        memoryMode: MemoryMode.forceNext,
      );
      final Uint8List record = _sidecarRecord(
        sequence: 1,
        previousSha256: List<String>.filled(64, '0').join(),
        sourceId: 'message:thread:m1',
        content: user.text,
      );
      final String transactionId = sha256.convert(record).toString();
      final Map<String, Object?> plan = <String, Object?>{
        'store_key': 'personal-memory-v1',
        'transaction_id': transactionId,
        'common_prefix': 0,
        'common_prefix_sha256': '',
        'total_records': 1,
      };
      final Map<String, Object?> chunk = <String, Object?>{
        'store_key': 'personal-memory-v1',
        'transaction_id': transactionId,
        'chunk_index': 0,
        'records': <String>[base64Encode(record)],
      };

      await store.handleHostRequest('sidecar.replace_begin', plan);
      await store.handleHostRequest('sidecar.replace_chunk', chunk);
      final Map<String, Object?> before = await store.handleHostRequest(
        'sidecar.load',
        <String, Object?>{
          'store_key': 'personal-memory-v1',
          'after_sequence': 0,
          'limit': 10,
        },
      );
      expect(before['records'], isEmpty);

      expect(
        await store.handleHostRequest(
          'sidecar.replace_commit',
          <String, Object?>{
            'store_key': 'personal-memory-v1',
            'transaction_id': transactionId,
          },
        ),
        <String, Object?>{'committed': true},
      );
      expect((await store.memoryObservations()).single.messageId, 'm1');

      // A crash after the durable swap but before the caller receives the ACK
      // may replay the same deterministic plan. It must remain one record.
      await store.handleHostRequest('sidecar.replace_begin', plan);
      await store.handleHostRequest('sidecar.replace_chunk', chunk);
      await store.handleHostRequest('sidecar.replace_commit', <String, Object?>{
        'store_key': 'personal-memory-v1',
        'transaction_id': transactionId,
      });
      final QueryRow count = await database
          .customSelect('SELECT COUNT(*) AS n FROM sidecar_records')
          .getSingle();
      expect(count.read<int>('n'), 1);
    },
  );

  test(
    'next sequence follows durable topology rather than visible row count',
    () async {
      final ChatMessage first = ChatMessage(
        id: 'm-gap-1',
        role: MessageRole.user,
        text: 'First durable row.',
        createdAt: DateTime.utc(2026, 8, 25),
      );
      await store.stageUserMessage(
        'thread',
        4,
        first,
        memoryMode: MemoryMode.keywords,
      );
      expect(await store.nextSequence('thread'), 6);
      expect(await store.loadMessages('thread'), hasLength(2));
    },
  );
}

Uint8List _sidecarRecord({
  required int sequence,
  required String previousSha256,
  required String sourceId,
  required String content,
}) {
  final Map<String, Object?> record = <String, Object?>{
    'schema': 'horizon.authorized-sidecar-batch.v1',
    'sequence': sequence,
    'previous_sha256': previousSha256,
    'scope': '1',
    'adapter_id': 'fixture',
    'authority_sha256': List<String>.filled(64, 'a').join(),
    'source_id': sourceId,
    'content': content,
    'source_sha256': sha256.convert(utf8.encode(content)).toString(),
    'facts': const <Object?>[],
    'completeness_claims': const <Object?>[],
  };
  record['record_sha256'] = sha256
      .convert(utf8.encode(_canonicalJson(record)))
      .toString();
  return Uint8List.fromList(utf8.encode(_canonicalJson(record)));
}

String _canonicalJson(Object? value) => jsonEncode(_canonicalValue(value));

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
