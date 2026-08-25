import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/design/proofray_theme.dart';
import 'package:proofray_app/features/chat/chat_controller.dart';
import 'package:proofray_app/features/chat/chat_screen.dart';
import 'package:proofray_app/models/chat_models.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('desktop English editorial transcript matches golden', (
    WidgetTester tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1320, 820));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final ChatController controller = _controller();
    addTearDown(controller.dispose);
    await tester.pumpWidget(_fixture(controller, const Locale('en')));
    await tester.tap(find.text('Observatory'));
    await tester.pumpAndSettle();
    await expectLater(
      find.byType(ChatScreen),
      matchesGoldenFile('goldens/chat_desktop_en.png'),
    );
  });

  testWidgets(
    'mobile Portuguese transcript matches golden with memory marker',
    (WidgetTester tester) async {
      await tester.binding.setSurfaceSize(const Size(390, 820));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final ChatController controller = _controller();
      addTearDown(controller.dispose);
      await tester.pumpWidget(_fixture(controller, const Locale('pt', 'BR')));
      expect(
        find.byKey(const ValueKey<String>('proofray-memory-brain')),
        findsOneWidget,
      );
      expect(find.text('PROVADA'), findsOneWidget);
      await expectLater(
        find.byType(ChatScreen),
        matchesGoldenFile('goldens/chat_mobile_pt.png'),
      );
    },
  );

  testWidgets('desktop Portuguese editorial transcript matches golden', (
    WidgetTester tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1320, 820));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final ChatController controller = _controller();
    addTearDown(controller.dispose);
    await tester.pumpWidget(_fixture(controller, const Locale('pt', 'BR')));
    await tester.tap(find.text('Observatório'));
    await tester.pumpAndSettle();
    await expectLater(
      find.byType(ChatScreen),
      matchesGoldenFile('goldens/chat_desktop_pt.png'),
    );
  });

  testWidgets('mobile English transcript matches golden with memory marker', (
    WidgetTester tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(390, 820));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final ChatController controller = _controller();
    addTearDown(controller.dispose);
    await tester.pumpWidget(_fixture(controller, const Locale('en')));
    expect(
      find.byKey(const ValueKey<String>('proofray-memory-brain')),
      findsOneWidget,
    );
    expect(find.text('PROVED'), findsOneWidget);
    await expectLater(
      find.byType(ChatScreen),
      matchesGoldenFile('goldens/chat_mobile_en.png'),
    );
  });
}

Widget _fixture(ChatController controller, Locale locale) => MaterialApp(
  locale: locale,
  supportedLocales: const <Locale>[Locale('en'), Locale('pt', 'BR')],
  localizationsDelegates: GlobalMaterialLocalizations.delegates,
  theme: buildProofRayTheme(),
  home: Scaffold(body: ChatScreen(controller: controller)),
);

ChatController _controller() => ChatController(
  conversationId: 'golden-thread',
  memoryMode: MemoryMode.keywords,
  initialMessages: <ChatMessage>[
    ChatMessage(
      id: 'user',
      role: MessageRole.user,
      text: 'Você lembra qual é a cor da minha bicicleta?',
      createdAt: DateTime.utc(2026, 8, 25, 12),
    ),
    ChatMessage(
      id: 'answer',
      role: MessageRole.assistant,
      text: 'Sua bicicleta é azul cobalto.',
      createdAt: DateTime.utc(2026, 8, 25, 12),
      authority: AnswerAuthority.proved,
      memoryConsulted: true,
      certifiedText: 'Sua bicicleta é azul cobalto.',
      certificateHex: 'aabbccdd',
      proofRunId: 'proof-answer',
      proofMethod: 'certified_exact_readout',
      queryDigest:
          '76f57aa50128d5791b2ce70da3e32ddc4d8c17909380656481a6062f0fb3beef',
      documentsConsidered: 8,
      verifiedCandidates: 3,
      answerBytes: 34,
      sources: const <ProofSource>[
        ProofSource(
          factId: 42,
          sourceId: 'conversation:thread:message:42',
          text: 'Minha bicicleta é azul cobalto.',
          parentSha256: '446aa3d4f673f49e1412dd5a8747fb666d31d3a15ebf3d22a62dbf20af5e1633',
          sessionId: 'thread',
          speaker: 'User',
          sourceSpan: (0, 33),
        ),
      ],
    ),
  ],
);
