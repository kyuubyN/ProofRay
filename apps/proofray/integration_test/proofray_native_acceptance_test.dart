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
    // The launch screen covers the app while it starts. A finder locates
    // widgets it hides, so waiting for the text alone would tap the animation.
    await _waitWhile(
      tester,
      find.byKey(const ValueKey<String>('proofray-launch-wave')),
      timeout: const Duration(seconds: 30),
    );
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
    expect(find.text('LOCAL CORE UNAVAILABLE'), findsNothing);

    await _send(tester, 'My bicycle is cobalt blue.');
    await _waitFor(
      tester,
      find.byIcon(Icons.arrow_upward),
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
  // `pumpWidget` keeps an outgoing native surface as a transition snapshot
  // for a frame. A plain text finder can see the old unlock label there even
  // though it cannot receive a pointer. Wait for the actual controls that
  // pass a hit test, otherwise the restart check races the old snapshot.
  final Finder usableTarget = target.hitTestable();
  final Finder unlockButton = find
      .widgetWithText(FilledButton, 'Unlock with PBKDF2')
      .hitTestable();
  await _waitForEither(
    tester,
    usableTarget,
    unlockButton,
    timeout: const Duration(seconds: 30),
  );
  if (unlockButton.evaluate().isEmpty) return;
  final Finder passphrase = find
      .widgetWithText(TextField, 'Passphrase (10+ characters)')
      .hitTestable();
  await _waitFor(tester, passphrase);
  await tester.enterText(passphrase, 'proofray-ci-passphrase');
  await tester.tap(unlockButton);
  await tester.pump();
}

Future<void> _send(WidgetTester tester, String text) async {
  final Finder composer = find.widgetWithText(TextField, 'Ask anything…');
  await _waitFor(tester, composer);
  // Chat becomes visible before bridge/outbox recovery has necessarily
  // finished. During that short window the same composer shows Stop rather
  // than Send; waiting for the actual affordance avoids tapping an icon that
  // is not in the live tree yet.
  await _waitFor(
    tester,
    find.byIcon(Icons.arrow_upward),
    timeout: const Duration(seconds: 45),
  );
  await tester.enterText(composer, text);
  // Native TextInput implementations may submit the current composing value
  // while `enterText` synchronizes it. If that happened, the composer has
  // already switched to Stop; tapping the now-absent arrow is not a product
  // failure and would make this check platform-timing dependent.
  if (find.byIcon(Icons.arrow_upward).evaluate().isNotEmpty) {
    await tester.tap(find.byIcon(Icons.arrow_upward));
  } else {
    expect(find.byIcon(Icons.stop), findsWidgets);
  }
  await tester.pump();
  await _waitForExchangeToSettle(tester);
}

Future<void> _waitForExchangeToSettle(WidgetTester tester) async {
  // The first send is deliberately not a recall. It still has to finish
  // before the test sends the recall: ChatController rejects a second send
  // while its outbox exchange is active. Waiting merely for the arrow icon is
  // racy because it exists before the tap and can also reappear during a
  // rebuild. Observe either the in-flight stop affordance or a terminal
  // answer, then require the composer to return to its send affordance.
  final Finder inFlight = find.byIcon(Icons.stop);
  final Finder terminal = find.textContaining(
    RegExp(r'^(PROVED|EVIDENCE|ABSTENTION|CONTESTED|MODEL ANSWER)$'),
  );
  await _waitForEither(
    tester,
    inFlight,
    terminal,
    timeout: const Duration(seconds: 45),
  );
  if (inFlight.evaluate().isNotEmpty) {
    await _waitFor(
      tester,
      find.byIcon(Icons.arrow_upward),
      timeout: const Duration(seconds: 45),
    );
  }
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
