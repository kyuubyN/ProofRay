import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/features/chat/bit_horizon_wave.dart';
import 'package:proofray_app/models/chat_models.dart';

void main() {
  test('Bit Horizon geometry is frozen at exactly 128 columns', () {
    expect(BitHorizonWave.columns, 128);
  });

  testWidgets('reduced motion renders a static custom paint', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(
      const MediaQuery(
        data: MediaQueryData(disableAnimations: true),
        child: MaterialApp(
          home: BitHorizonWave(
            stage: BitHorizonStage.routing,
            queryDigest: 'fixed-query-digest',
          ),
        ),
      ),
    );
    expect(find.byType(CustomPaint), findsWidgets);
    await tester.pump(const Duration(seconds: 2));
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'digest changes and terminal stages settle without stale frames',
    (WidgetTester tester) async {
      Widget wave(BitHorizonStage stage, String digest) => MaterialApp(
        home: BitHorizonWave(stage: stage, queryDigest: digest),
      );

      await tester.pumpWidget(wave(BitHorizonStage.routing, 'first-digest'));
      await tester.pump(const Duration(milliseconds: 100));
      await tester.pumpWidget(wave(BitHorizonStage.routing, 'second-digest'));
      await tester.pump(const Duration(milliseconds: 100));
      await tester.pumpWidget(
        wave(BitHorizonStage.proofClosed, 'second-digest'),
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(tester.binding.hasScheduledFrame, isFalse);
    },
  );
}
