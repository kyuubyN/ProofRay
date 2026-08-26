import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/design/proofray_theme.dart';
import 'package:proofray_app/features/onboarding/onboarding_screen.dart';

void main() {
  testWidgets('PT-BR mobile onboarding completes at 200% text scale', (
    WidgetTester tester,
  ) async {
    tester.view.physicalSize = const Size(360, 640);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    OnboardingResult? result;

    await tester.pumpWidget(
      MediaQuery(
        data: const MediaQueryData(
          size: Size(360, 640),
          textScaler: TextScaler.linear(2),
          disableAnimations: true,
        ),
        child: MaterialApp(
          theme: buildProofRayTheme(),
          locale: const Locale('pt', 'BR'),
          supportedLocales: const <Locale>[Locale('en'), Locale('pt', 'BR')],
          localizationsDelegates: GlobalMaterialLocalizations.delegates,
          home: OnboardingScreen(
            databasePath:
                '/private/proofray/a-long-local-database-location/proofray.db',
            onFinished: (OnboardingResult value) => result = value,
          ),
        ),
      ),
    );

    for (int page = 0; page < 4; page++) {
      expect(tester.takeException(), isNull);
      await tester.tap(find.text('Continuar'));
      await tester.pumpAndSettle();
    }
    expect(tester.takeException(), isNull);
    await tester.tap(find.text('Criar memória local'));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(result, isNotNull);
    expect(result!.providerKind, isNull);
  });
}
