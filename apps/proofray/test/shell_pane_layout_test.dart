import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/features/chat/chat_controller.dart';
import 'package:proofray_app/features/chat/chat_screen.dart';
import 'package:proofray_app/features/history/history_screen.dart';
import 'package:proofray_app/features/local_models/local_model_controller.dart';
import 'package:proofray_app/features/shell/proofray_shell.dart';
import 'package:proofray_app/models/chat_models.dart';
import 'package:proofray_app/storage/conversation_store.dart';
import 'package:proofray_app/storage/integration_store.dart';

/// The History pane exists only where the desktop conversation sidebar cannot.
/// Removing it outright would leave a phone with no way to reach, create or
/// delete a conversation at all.
void main() {
  Widget shell(ChatController controller) => MaterialApp(
      locale: const Locale('en'),
      supportedLocales: const <Locale>[Locale('en'), Locale('pt', 'BR')],
      localizationsDelegates: GlobalMaterialLocalizationsShim.delegates,
      home: ProofRayShell(
        chatController: controller,
        localModels: _stubLocalModels(),
        providerSwitcher: const SizedBox.shrink(),
        onProviderSelected: (String? id, {bool supportsTools = true}) async {},
        store: _StubStore(),
        integrations: _StubIntegrations(),
        profileId: 'local-owner',
        onOpenConversation: (_) async {},
        onNewConversation: () async {},
        onDeleteConversation: (_, _) async {},
        locale: const Locale('en'),
        onLocaleChanged: (_) {},
      ),
  );

  testWidgets('mobile keeps History, desktop replaces it with the sidebar', (
    WidgetTester tester,
  ) async {
    final ChatController controller = ChatController(
      conversationId: 'c1',
      memoryMode: MemoryMode.keywords,
      initialNextSequence: 0,
    );
    addTearDown(controller.dispose);

    // Size the real test surface, not just MediaQuery: the shell picks its
    // layout from the viewport width, and a mismatched surface would lay the
    // chat out at a size the assertions never described.
    addTearDown(tester.view.reset);
    tester.view.devicePixelRatio = 1;

    tester.view.physicalSize = const Size(420, 900);
    await tester.pumpWidget(shell(controller));
    await tester.pump();
    expect(find.byIcon(Icons.schedule_outlined), findsOneWidget);

    tester.view.physicalSize = const Size(1400, 900);
    await tester.pumpWidget(shell(controller));
    await tester.pump();
    expect(find.byIcon(Icons.schedule_outlined), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('a History selection survives crossing the breakpoint', (
    WidgetTester tester,
  ) async {
    final ChatController controller = ChatController(
      conversationId: 'c1',
      memoryMode: MemoryMode.keywords,
      initialNextSequence: 0,
    );
    addTearDown(controller.dispose);
    addTearDown(tester.view.reset);
    tester.view.devicePixelRatio = 1;

    tester.view.physicalSize = const Size(420, 900);
    await tester.pumpWidget(shell(controller));
    await tester.pump();
    await tester.tap(find.byIcon(Icons.schedule_outlined));
    await tester.pump();
    expect(find.byType(HistoryScreen), findsOneWidget);

    // The selected pane no longer exists at this width. Selection is held by
    // identity, so this must land on the chat rather than on whatever now
    // happens to sit at the old index.
    tester.view.physicalSize = const Size(1400, 900);
    await tester.pumpWidget(shell(controller));
    await tester.pump();
    expect(tester.takeException(), isNull);
    expect(find.byType(HistoryScreen), findsNothing);
    expect(find.byType(ChatScreen), findsOneWidget);
  });
}

class GlobalMaterialLocalizationsShim {
  static const List<LocalizationsDelegate<Object>> delegates =
      <LocalizationsDelegate<Object>>[
        DefaultMaterialLocalizations.delegate,
        DefaultWidgetsLocalizations.delegate,
      ];
}

LocalModelController _stubLocalModels() => LocalModelController(
  integrations: _StubIntegrations(),
  bridge: () => null,
);

class _StubStore implements ConversationStore {
  @override
  Future<List<ConversationSummary>> conversations(String profileId) async =>
      const <ConversationSummary>[];

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _StubIntegrations implements IntegrationStore {
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
