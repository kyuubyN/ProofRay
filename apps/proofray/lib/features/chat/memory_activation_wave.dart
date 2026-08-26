import 'dart:async';

import 'package:flutter/material.dart';

import '../../design/pixel_wave.dart';
import '../../design/proofray_theme.dart';
import '../../models/chat_models.dart';

/// The strip that plays while Horizon is consulting memory.
///
/// It replaces the scattered dot field the stage painter used to draw, which
/// read as visual noise near the chat header rather than as activity. Every
/// activation now shows the same monochrome pixel wave for at least one full
/// second -- deliberately including the case where the deterministic core
/// already answered before the first frame, so activation is always something
/// you can see happen rather than a flicker.
class MemoryActivationWave extends StatefulWidget {
  const MemoryActivationWave({
    required this.stage,
    required this.queryDigest,
    super.key,
    this.height = 56,
    this.minimumVisible = const Duration(seconds: 1),
  });

  final BitHorizonStage stage;

  /// Identity of the current memory activation. A new digest means a new
  /// consultation, which is what restarts the animation.
  final String queryDigest;

  final double height;
  final Duration minimumVisible;

  @override
  State<MemoryActivationWave> createState() => _MemoryActivationWaveState();
}

class _MemoryActivationWaveState extends State<MemoryActivationWave> {
  Timer? _floor;
  bool _holding = false;
  String _shownDigest = '';

  static bool _isWorking(BitHorizonStage stage) => switch (stage) {
    BitHorizonStage.activating ||
    BitHorizonStage.routing ||
    BitHorizonStage.verifying => true,
    _ => false,
  };

  @override
  void initState() {
    super.initState();
    // No setState here: the first build reads the fields directly.
    if (_shouldRestart()) _startHold();
  }

  @override
  void didUpdateWidget(MemoryActivationWave oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (_shouldRestart()) setState(_startHold);
  }

  /// A restart is exactly one of: a new activation identity, or work starting
  /// while nothing is on screen (an activation whose digest never arrived).
  bool _shouldRestart() {
    final String digest = widget.queryDigest;
    if (digest.isNotEmpty && digest != _shownDigest) return true;
    return _isWorking(widget.stage) && !_holding;
  }

  void _startHold() {
    _shownDigest = widget.queryDigest;
    _floor?.cancel();
    _holding = true;
    _floor = Timer(widget.minimumVisible, () {
      if (mounted) setState(() => _holding = false);
    });
  }

  @override
  void dispose() {
    _floor?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // Stays up while the core is still working, so a slow consultation is not
    // cut off at the one-second floor.
    if (!_holding && !_isWorking(widget.stage)) return const SizedBox.shrink();
    return SizedBox(
      height: widget.height,
      width: double.infinity,
      child: const ClipRect(
        child: PixelWave(
          gridSize: PixelWave.referenceGridSize,
          speed: 0.6,
          foreground: ProofRayColors.ink,
          background: ProofRayColors.paper,
        ),
      ),
    );
  }
}
