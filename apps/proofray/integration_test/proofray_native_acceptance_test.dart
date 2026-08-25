import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:proofray_app/app/proofray_app.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('native first launch remembers, marks and reopens one answer', (
    WidgetTester tester,
  ) async {
    tester.binding.platformDispatcher.localeTestValue = const Locale('en');
    addTearDown(tester.binding.platformDispatcher.clearLocaleTestValue);
    await tester.pumpWidget(const ProofRayApp());
    await _unlockIfRequired(tester, target: find.text('Continue'));
    await _waitFor(tester, find.text('Continue'));

    for (int page = 0; page < 4; page++) {
      await tester.tap(find.text('Continue'));
      await tester.pumpAndSettle();
    }
    await tester.tap(find.text('Create local memory'));
    await tester.pump();
    await _waitFor(
      tester,
      find.byKey(const ValueKey<String>('memory-mode-button')),
      timeout: const Duration(seconds: 45),
    );
    await _waitWhile(
      tester,
      find.text('STARTING LOCAL CORE'),
      timeout: const Duration(seconds: 45),
    );

    await _send(tester, 'My bicycle is cobalt blue.');
    await _waitFor(
      tester,
      find.text(
        'No AI provider is connected. ProofRay remains available for memory, evidence and abstention.',
      ),
      timeout: const Duration(seconds: 30),
    );
    expect(
      find.byKey(const ValueKey<String>('proofray-memory-brain')),
      findsNothing,
    );

    await _send(tester, 'Remember what color is my bicycle?');
    await _waitFor(
      tester,
      find.byKey(const ValueKey<String>('proofray-memory-brain')),
      timeout: const Duration(seconds: 30),
    );
    expect(find.text('My bicycle is cobalt blue.'), findsWidgets);
    expect(find.text('EVIDENCE'), findsOneWidget);
    expect(find.text('Observatory'), findsOneWidget);

    // Dispose the entire native surface, including the embedded runtime and
    // encrypted database, then start from the persisted app directory again.
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(seconds: 3));
    await Future<void>.delayed(const Duration(seconds: 1));
    await tester.pumpWidget(const ProofRayApp());
    await _unlockIfRequired(
      tester,
      target: find.byKey(const ValueKey<String>('memory-mode-button')),
    );
    await _waitFor(
      tester,
      find.byKey(const ValueKey<String>('proofray-memory-brain')),
      timeout: const Duration(seconds: 45),
    );
    expect(find.text('My bicycle is cobalt blue.'), findsWidgets);
    expect(find.text('EVIDENCE'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    await Future<void>.delayed(const Duration(milliseconds: 500));
  });
}

Future<void> _unlockIfRequired(
  WidgetTester tester, {
  required Finder target,
}) async {
  await _waitForEither(
    tester,
    target,
    find.text('Unlock with PBKDF2'),
    timeout: const Duration(seconds: 30),
  );
  if (find.text('Unlock with PBKDF2').evaluate().isEmpty) return;
  await tester.enterText(
    find.widgetWithText(TextField, 'Passphrase (10+ characters)'),
    'proofray-ci-passphrase',
  );
  await tester.tap(find.text('Unlock with PBKDF2'));
  await tester.pump();
}

Future<void> _send(WidgetTester tester, String text) async {
  final Finder composer = find.widgetWithText(TextField, 'Ask anything…');
  await _waitFor(tester, composer);
  await tester.enterText(composer, text);
  await tester.tap(find.byIcon(Icons.arrow_upward));
  await tester.pump();
}

Future<void> _waitFor(
  WidgetTester tester,
  Finder finder, {
  Duration timeout = const Duration(seconds: 15),
}) async {
  final DateTime deadline = DateTime.now().add(timeout);
  while (finder.evaluate().isEmpty && DateTime.now().isBefore(deadline)) {
    await tester.pump(const Duration(milliseconds: 80));
    await Future<void>.delayed(const Duration(milliseconds: 20));
  }
  expect(finder, findsWidgets);
}

Future<void> _waitWhile(
  WidgetTester tester,
  Finder finder, {
  Duration timeout = const Duration(seconds: 15),
}) async {
  final DateTime deadline = DateTime.now().add(timeout);
  while (finder.evaluate().isNotEmpty && DateTime.now().isBefore(deadline)) {
    await tester.pump(const Duration(milliseconds: 80));
    await Future<void>.delayed(const Duration(milliseconds: 20));
  }
  expect(finder, findsNothing);
}

Future<void> _waitForEither(
  WidgetTester tester,
  Finder first,
  Finder second, {
  required Duration timeout,
}) async {
  final DateTime deadline = DateTime.now().add(timeout);
  while (first.evaluate().isEmpty &&
      second.evaluate().isEmpty &&
      DateTime.now().isBefore(deadline)) {
    await tester.pump(const Duration(milliseconds: 80));
    await Future<void>.delayed(const Duration(milliseconds: 20));
  }
  expect(first.evaluate().isNotEmpty || second.evaluate().isNotEmpty, isTrue);
}
