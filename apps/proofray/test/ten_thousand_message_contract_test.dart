import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/models/chat_models.dart';
import 'package:proofray_app/storage/app_database.dart';
import 'package:proofray_app/storage/conversation_store.dart';

void main() {
  test('10k-message transcript loads within the desktop diagnostic budget', () async {
    final ProofRayDatabase database = ProofRayDatabase(NativeDatabase.memory());
    addTearDown(database.close);
    final DriftConversationStore store = DriftConversationStore(database);
    await store.ensureLocalProfile(
      profileId: 'owner',
      displayName: 'User',
      locale: 'en',
      timezone: 'UTC',
    );
    await store.ensureConversation(
      conversationId: 'thread',
      profileId: 'owner',
      title: 'Ten thousand messages',
      memoryMode: MemoryMode.keywords,
    );
    final int createdAt = DateTime.utc(2026, 8, 25).millisecondsSinceEpoch;
    await database.transaction(() async {
      for (int sequence = 0; sequence < 10000; sequence++) {
        final bool user = sequence.isEven;
        await database.customStatement(
          'INSERT INTO messages '
          '(id, conversation_id, sequence, role, origin, authority, content, '
          'memory_eligible, memory_consulted, memory_source_id, status, created_at, '
          'deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, NULL)',
          <Object?>[
            'message-$sequence',
            'thread',
            sequence,
            user ? 'user' : 'assistant',
            user ? 'user' : 'model',
            user ? 'none' : 'model',
            'Compact transcript message $sequence',
            user ? 'conversation:thread:message-$sequence' : null,
            'committed',
            createdAt + sequence,
          ],
        );
      }
    });

    final Stopwatch stopwatch = Stopwatch()..start();
    final messages = await store.loadMessages('thread');
    stopwatch.stop();
    expect(messages, hasLength(10000));
    expect(messages.first.id, 'message-0');
    expect(messages.last.id, 'message-9999');
    // This is a deterministic desktop diagnostic. Android cold-start timing is
    // still a separate physical-device release gate.
    expect(stopwatch.elapsed, lessThan(const Duration(seconds: 5)));
  });
}
