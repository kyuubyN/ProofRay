import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../l10n/app_strings.dart';

class MemoryActivationBadge extends StatelessWidget {
  const MemoryActivationBadge({required this.activated, super.key});

  final bool activated;

  @override
  Widget build(BuildContext context) {
    if (!activated) {
      return const SizedBox.shrink();
    }
    final AppStrings strings = AppStrings.of(context);
    return Padding(
      padding: const EdgeInsets.only(top: 7),
      child: Semantics(
        label: strings.memoryActivated,
        container: true,
        child: Tooltip(
          message: strings.memoryActivated,
          child: const Row(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Icon(
                Icons.psychology_alt_outlined,
                key: ValueKey<String>('proofray-memory-brain'),
                size: 14,
                color: ProofRayColors.memoryGreen,
                semanticLabel: null,
              ),
              SizedBox(width: 4),
              Text(
                'PFR',
                style: TextStyle(
                  color: ProofRayColors.memoryGreen,
                  fontFamily: 'monospace',
                  fontSize: 9,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.8,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
