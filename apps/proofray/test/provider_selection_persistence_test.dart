import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/features/chat/provider_switcher.dart';
import 'package:proofray_app/features/local_models/local_model_controller.dart';
import 'package:proofray_app/storage/integration_store.dart';

/// The switcher only ever offers what is actually set up, and reports the
/// choice back so the app can remember it. Regression for a real report: the
/// app kept reopening on Gemini after the user had switched away.
void main() {
  testWidgets('every saved provider is offered, plus turning the AI off', (
    WidgetTester tester,
  ) async {
    final _FakeIntegrations integrations = _FakeIntegrations(<StoredProvider>[
      _provider('provider-gemini', 'gemini', 'gemini-3.5-flash-lite'),
      _provider('provider-openai', 'openai', 'gpt-5-mini'),
    ]);
    final List<String?> chosen = <String?>[];
    await tester.pumpWidget(
      MaterialApp(
        locale: const Locale('en'),
        supportedLocales: const <Locale>[Locale('en')],
        localizationsDelegates: const <LocalizationsDelegate<Object>>[
          DefaultMaterialLocalizations.delegate,
          DefaultWidgetsLocalizations.delegate,
        ],
        home: Scaffold(
          body: ProviderSwitcher(
            integrations: integrations,
            localModels: LocalModelController(
              integrations: integrations,
              bridge: () => null,
            ),
            selectedProviderId: 'provider-gemini',
            onSelect: (StoredProvider? provider) async =>
                chosen.add(provider?.id),
            onSelectLocalModel: (_) async {},
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey<String>('provider-switcher')));
    await tester.pumpAndSettle();
    expect(find.text('Gemini'), findsOneWidget);
    expect(find.text('OpenAI'), findsOneWidget);
    expect(find.text('No AI'), findsOneWidget);

    await tester.tap(find.text('OpenAI'));
    await tester.pumpAndSettle();
    expect(chosen, <String?>['provider-openai']);

    // "No AI" has to report back too. It carried no menu value at first, and
    // PopupMenuButton reads a null result as the menu being dismissed -- so the
    // item was visible, tappable, and silently did nothing.
    await tester.tap(find.byKey(const ValueKey<String>('provider-switcher')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('No AI'));
    await tester.pumpAndSettle();
    expect(chosen, <String?>['provider-openai', null]);
  });

  testWidgets('nothing is offered when nothing has been set up', (
    WidgetTester tester,
  ) async {
    final _FakeIntegrations integrations = _FakeIntegrations(
      const <StoredProvider>[],
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ProviderSwitcher(
            integrations: integrations,
            localModels: LocalModelController(
              integrations: integrations,
              bridge: () => null,
            ),
            selectedProviderId: null,
            onSelect: (_) async {},
            onSelectLocalModel: (_) async {},
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    // An empty menu would promise a choice that does not exist.
    expect(
      find.byKey(const ValueKey<String>('provider-switcher')),
      findsNothing,
    );
  });
}

StoredProvider _provider(String id, String kind, String modelId) =>
    StoredProvider(
      id: id,
      kind: kind,
      displayName: kind,
      modelId: modelId,
      endpoint: 'https://example.invalid/v1',
      customModel: false,
      supportsTools: true,
      secretHandle: null,
    );

class _FakeIntegrations implements IntegrationStore {
  _FakeIntegrations(this.saved);

  final List<StoredProvider> saved;

  @override
  Future<List<StoredProvider>> providers() async => saved;

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
