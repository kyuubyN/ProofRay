import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/design/proofray_theme.dart';
import 'package:proofray_app/features/chat/memory_activation_badge.dart';

void main() {
  testWidgets('green brain exists only when memory actually activated', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(home: MemoryActivationBadge(activated: false)),
    );
    expect(
      find.byKey(const ValueKey<String>('proofray-memory-brain')),
      findsNothing,
    );

    await tester.pumpWidget(
      const MaterialApp(home: MemoryActivationBadge(activated: true)),
    );
    final Icon icon = tester.widget<Icon>(
      find.byKey(const ValueKey<String>('proofray-memory-brain')),
    );
    expect(icon.color, ProofRayColors.memoryGreen);
    expect(find.text('PFR'), findsOneWidget);
  });
}
