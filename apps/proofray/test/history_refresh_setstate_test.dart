import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/features/history/history_screen.dart';
import 'package:proofray_app/models/chat_models.dart';
import 'package:proofray_app/storage/conversation_store.dart';

/// Regression for a real runtime failure seen in a live session: refreshing the
/// conversation list used `setState(() => _rows = store.conversations(...))`.
/// The arrow body returns the assigned `Future`, and Flutter rejects a setState
/// callback that returns one -- so every "new conversation" click threw an
/// unhandled exception mid-rebuild instead of refreshing the list. `flutter
/// analyze` cannot see this: `void` is erased at runtime, so the declared
/// return type never protects the call.
void main() {
  testWidgets('creating a conversation refreshes without throwing', (
    WidgetTester tester,
  ) async {
    final _RecordingStore store = _RecordingStore();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: HistoryScreen(
            store: store,
            profileId: 'local-owner',
            activeConversationId: 'c1',
            onOpen: (_) {},
            onCreate: () async {
              store.rows.add(
                ConversationSummary(
                  id: 'c2',
                  title: 'Second',
                  updatedAt: DateTime.utc(2026, 8, 25, 12),
                  memoryMode: MemoryMode.keywords,
                ),
              );
            },
            onDelete: (_, _) async {},
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('First'), findsOneWidget);

    await tester.tap(find.byIcon(Icons.add));
    await tester.pumpAndSettle();

    // The whole point: setState must not have thrown on the returned Future.
    expect(tester.takeException(), isNull);
    expect(find.text('Second'), findsOneWidget);
    expect(store.conversationCalls, greaterThan(1));
  });
}

class _RecordingStore implements ConversationStore {
  final List<ConversationSummary> rows = <ConversationSummary>[
    ConversationSummary(
      id: 'c1',
      title: 'First',
      updatedAt: DateTime.utc(2026, 8, 25, 11),
      memoryMode: MemoryMode.keywords,
    ),
  ];
  int conversationCalls = 0;

  @override
  Future<List<ConversationSummary>> conversations(String profileId) async {
    conversationCalls++;
    return List<ConversationSummary>.unmodifiable(rows);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
