import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:proofray_app/features/chat/bit_horizon_wave.dart';
import 'package:proofray_app/models/chat_models.dart';

void main() {
  final IntegrationTestWidgetsFlutterBinding binding =
      IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Bit Horizon reports physical p95 frame time', (
    WidgetTester tester,
  ) async {
    final List<FrameTiming> timings = <FrameTiming>[];
    final TimingsCallback collect = timings.addAll;
    WidgetsBinding.instance.addTimingsCallback(collect);
    addTearDown(() => WidgetsBinding.instance.removeTimingsCallback(collect));

    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 1024,
            child: BitHorizonWave(
              stage: BitHorizonStage.routing,
              queryDigest: 'proofray-frame-budget-v1',
            ),
          ),
        ),
      ),
    );
    for (int frame = 0; frame < 300; frame++) {
      await tester.pump(const Duration(microseconds: 16667));
    }
    // Engines batch FrameTiming delivery. This delay is outside the measured
    // animation and gives the last batch time to arrive.
    await Future<void>.delayed(const Duration(seconds: 2));
    WidgetsBinding.instance.removeTimingsCallback(collect);

    expect(timings, isNotEmpty);
    final List<double> frameMillis = <double>[
      for (final FrameTiming timing in timings)
        (timing.buildDuration + timing.rasterDuration).inMicroseconds / 1000,
    ]..sort();
    final int p95Index = ((frameMillis.length - 1) * 0.95).ceil();
    final double p95 = frameMillis[p95Index];
    binding.reportData = <String, Object?>{
      'schema': 'proofray.bit-horizon-frame-budget.v1',
      'frames': frameMillis.length,
      'p95_total_frame_ms': p95,
      'budget_ms': 16.7,
    };
    if (const bool.fromEnvironment('PROOFRAY_ENFORCE_FRAME_BUDGET')) {
      expect(p95, lessThan(16.7));
    }
  });
}
