import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/features/settings/model_id_field.dart';
import 'package:proofray_app/services/bridge/proofray_bridge.dart';

/// The model picker exists so nobody has to know what their provider currently
/// calls its models. These pin the two halves of that: a list when one is
/// available, free text the moment someone asks for it.
void main() {
  Widget host(Widget child) => MaterialApp(
    locale: const Locale('en'),
    supportedLocales: const <Locale>[Locale('en')],
    localizationsDelegates: const <LocalizationsDelegate<Object>>[
      DefaultMaterialLocalizations.delegate,
      DefaultWidgetsLocalizations.delegate,
    ],
    home: Scaffold(body: SingleChildScrollView(child: child)),
  );

  testWidgets('a cached catalogue is offered as a list, not as typing', (
    WidgetTester tester,
  ) async {
    final TextEditingController controller = TextEditingController(
      text: 'gemini-3.5-flash-lite',
    );
    addTearDown(controller.dispose);
    await tester.pumpWidget(
      host(
        ModelIdField(
          controller: controller,
          kind: 'gemini',
          endpoint: () => 'https://example.invalid/v1',
          custom: false,
          onCustomChanged: (_) {},
          bridge: () => null,
          initialModels: const <Map<String, Object?>>[
            <String, Object?>{
              'model_id': 'gemini-3.5-flash-lite',
              'display_name': 'Gemini 3.5 Flash Lite',
              'supports_tools': true,
            },
            <String, Object?>{
              'model_id': 'gemini-3.6-flash',
              'display_name': 'Gemini 3.6 Flash',
              'supports_tools': true,
            },
          ],
        ),
      ),
    );
    await tester.pump();

    // No bridge means no discovery, but a cached catalogue still spares the
    // typing: this must be a chooser, not a text box.
    expect(find.byType(DropdownButtonFormField<String>), findsOneWidget);
    expect(find.text('Gemini 3.5 Flash Lite'), findsWidgets);
  });

  testWidgets('the custom switch always returns a free text field', (
    WidgetTester tester,
  ) async {
    final TextEditingController controller = TextEditingController();
    addTearDown(controller.dispose);
    bool custom = false;
    await tester.pumpWidget(
      host(
        StatefulBuilder(
          builder: (BuildContext context, StateSetter setState) => ModelIdField(
            controller: controller,
            kind: 'gemini',
            endpoint: () => 'https://example.invalid/v1',
            custom: custom,
            onCustomChanged: (bool value) => setState(() => custom = value),
            bridge: () => null,
            initialModels: const <Map<String, Object?>>[
              <String, Object?>{
                'model_id': 'gemini-3.5-flash-lite',
                'supports_tools': true,
              },
            ],
          ),
        ),
      ),
    );
    await tester.pump();
    expect(find.byType(DropdownButtonFormField<String>), findsOneWidget);

    await tester.tap(find.byType(Switch));
    await tester.pump();

    // A preview or experimental id is often absent from the catalogue, so the
    // escape hatch must never be blocked by the list.
    expect(find.byType(DropdownButtonFormField<String>), findsNothing);
    expect(find.byType(TextField), findsOneWidget);
    await tester.enterText(find.byType(TextField), 'gemini-4.0-preview');
    expect(controller.text, 'gemini-4.0-preview');
  });

  testWidgets('discovery resolves the key from the vault, not the text box', (
    WidgetTester tester,
  ) async {
    // Regression for a real report: the model list came back empty because the
    // key lives in the vault and the on-screen field is cleared after every
    // successful save. Discovery has to ask for the key, not read a text box.
    final TextEditingController controller = TextEditingController();
    addTearDown(controller.dispose);
    final _RecordingBridge bridge = _RecordingBridge();
    await tester.pumpWidget(
      host(
        ModelIdField(
          controller: controller,
          kind: 'gemini',
          endpoint: () => 'https://example.invalid/v1',
          custom: false,
          onCustomChanged: (_) {},
          bridge: () => bridge,
          secret: () async => 'vault-key',
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(bridge.secretsSeen, <String?>['vault-key']);
    // Fetched unprompted: opening the screen is enough to want the options.
    expect(find.byType(DropdownButtonFormField<String>), findsOneWidget);
    expect(bridge.removedDiscoveryIds, <String>['provider-discovery-gemini']);
  });
}

/// Records what discovery actually sent, so the test can assert the key.
class _RecordingBridge implements ProofRayBridge {
  final List<String?> secretsSeen = <String?>[];
  final List<String> removedDiscoveryIds = <String>[];

  @override
  Future<void> configureProvider({
    required String providerId,
    required String kind,
    required String modelId,
    required String endpoint,
    bool customModel = false,
    bool? toolCallingOverride,
  }) async {}

  @override
  Future<List<Map<String, Object?>>> listProviderModels(
    String providerId, {
    String? secret,
  }) async {
    secretsSeen.add(secret);
    return <Map<String, Object?>>[
      <String, Object?>{
        'model_id': 'gemini-3.5-flash-lite',
        'display_name': 'Gemini 3.5 Flash Lite',
        'supports_tools': true,
      },
    ];
  }

  @override
  Future<void> removeProvider(String providerId) async {
    removedDiscoveryIds.add(providerId);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
