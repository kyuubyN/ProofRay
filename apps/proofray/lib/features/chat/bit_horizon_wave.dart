import 'dart:math' as math;
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../models/chat_models.dart';

class BitHorizonWave extends StatefulWidget {
  const BitHorizonWave({
    required this.stage,
    required this.queryDigest,
    super.key,
    this.height = 92,
  });

  static const int columns = 128;

  final BitHorizonStage stage;
  final String queryDigest;
  final double height;

  @override
  State<BitHorizonWave> createState() => _BitHorizonWaveState();
}

class _BitHorizonWaveState extends State<BitHorizonWave>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  late Uint8List _seed;
  bool _reduceMotion = false;

  bool get _isActive => switch (widget.stage) {
    BitHorizonStage.activating ||
    BitHorizonStage.routing ||
    BitHorizonStage.verifying => true,
    _ => false,
  };

  @override
  void initState() {
    super.initState();
    _seed = _seedForDigest(widget.queryDigest);
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1450),
    );
    if (_isActive) {
      _controller.repeat();
    }
  }

  @override
  void didUpdateWidget(BitHorizonWave oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.queryDigest != widget.queryDigest) {
      _seed = _seedForDigest(widget.queryDigest);
    }
    if (_isActive && !_reduceMotion && !_controller.isAnimating) {
      _controller.repeat();
    } else if (!_isActive && _controller.isAnimating) {
      _controller.stop();
      _controller.value = 1;
    }
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final bool reduceMotion = MediaQuery.disableAnimationsOf(context);
    if (_reduceMotion == reduceMotion) return;
    _reduceMotion = reduceMotion;
    if (_reduceMotion) {
      _controller.stop();
      _controller.value = 1;
    } else if (_isActive && !_controller.isAnimating) {
      _controller.repeat();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_reduceMotion || !_isActive) {
      return SizedBox(
        height: widget.height,
        width: double.infinity,
        child: CustomPaint(
          painter: _BitHorizonPainter(
            stage: widget.stage,
            seed: _seed,
            progress: 1,
          ),
        ),
      );
    }
    return SizedBox(
      height: widget.height,
      width: double.infinity,
      child: AnimatedBuilder(
        animation: _controller,
        builder: (BuildContext context, Widget? child) => CustomPaint(
          painter: _BitHorizonPainter(
            stage: widget.stage,
            seed: _seed,
            progress: _controller.value,
          ),
        ),
      ),
    );
  }
}

Uint8List _seedForDigest(String digest) => Uint8List.fromList(
  sha256.convert(digest.isEmpty ? <int>[0] : digest.codeUnits).bytes,
);

class _BitHorizonPainter extends CustomPainter {
  _BitHorizonPainter({
    required this.stage,
    required this.seed,
    required this.progress,
  });

  static const int _rows = 24;

  final BitHorizonStage stage;
  final Uint8List seed;
  final double progress;

  @override
  void paint(Canvas canvas, Size size) {
    final Paint pixel = Paint()
      ..color = ProofRayColors.ink
      ..isAntiAlias = false;
    final Paint hairline = Paint()
      ..color = ProofRayColors.hairline
      ..strokeWidth = 1
      ..isAntiAlias = false;

    canvas.drawLine(
      Offset(0, size.height / 2),
      Offset(size.width, size.height / 2),
      hairline,
    );

    final double cellWidth = size.width / BitHorizonWave.columns;
    final double cellHeight = size.height / _rows;
    final double phase = progress * math.pi * 2;
    final double stageAmplitude = switch (stage) {
      BitHorizonStage.idle => 0.8,
      BitHorizonStage.activating => 2.4,
      BitHorizonStage.routing => 6.0,
      BitHorizonStage.verifying => 3.4,
      BitHorizonStage.proofClosed => 0.5,
      BitHorizonStage.evidence => 2.0,
      BitHorizonStage.contested => 5.0,
      BitHorizonStage.abstained => 1.2,
    };

    for (int column = 0; column < BitHorizonWave.columns; column++) {
      final double unit = column / (BitHorizonWave.columns - 1);
      final double x = (column * cellWidth).floorToDouble();

      if (stage == BitHorizonStage.evidence) {
        final int count = 1 + seed[column % seed.length] % 3;
        for (int point = 0; point < count; point++) {
          final int row = seed[(column * 5 + point * 11) % seed.length] % _rows;
          if (seed[(column + point * 7) % seed.length] < 92) continue;
          final double side = math.max(
            1,
            math.min(cellWidth, cellHeight) * 0.62,
          );
          canvas.drawRect(
            Rect.fromLTWH(x, (row * cellHeight).floorToDouble(), side, side),
            pixel,
          );
        }
        continue;
      }

      if (stage == BitHorizonStage.proofClosed) {
        final int center = _rows ~/ 2;
        final int spread = math.max(0, ((1 - unit) * 9).round());
        final double side = math.max(1, math.min(cellWidth, cellHeight) * 0.76);
        final Iterable<int> rows = spread == 0
            ? <int>[center]
            : <int>[center - spread, center + spread];
        for (final int row in rows) {
          canvas.drawRect(
            Rect.fromLTWH(x, (row * cellHeight).floorToDouble(), side, side),
            pixel,
          );
        }
        continue;
      }

      if (stage == BitHorizonStage.abstained &&
          seed[(column * 3) % seed.length] / 255 < unit) {
        continue;
      }
      final double localPhase =
          unit * math.pi * 4 + phase + seed[column % seed.length] / 255;
      double wave = math.sin(localPhase) * stageAmplitude;
      if (stage == BitHorizonStage.contested) {
        wave += math.sin(localPhase * 1.73 + math.pi / 3) * 3.2;
      }
      if (stage == BitHorizonStage.abstained) {
        wave *= 1 - unit;
      }

      final int center = (_rows / 2 + wave).round().clamp(1, _rows - 2).toInt();
      final int density = switch (stage) {
        BitHorizonStage.proofClosed => 1,
        BitHorizonStage.verifying => 2,
        BitHorizonStage.evidence => 2 + seed[column % seed.length] % 2,
        _ => 2,
      };
      for (int offset = -density; offset <= density; offset++) {
        final int row = center + offset;
        final int threshold = 96 + offset.abs() * 34;
        if (seed[(column + row) % seed.length] < threshold && offset != 0) {
          continue;
        }
        final double y = (row * cellHeight).floorToDouble();
        final double side = math.max(1, math.min(cellWidth, cellHeight) * 0.72);
        canvas.drawRect(Rect.fromLTWH(x, y, side, side), pixel);
      }
      if (stage == BitHorizonStage.verifying &&
          seed[(column * 7) % seed.length] % 13 == 0) {
        final int anchorRow =
            _rows ~/ 2 - 3 + seed[(column * 11) % seed.length] % 7;
        final double side = math.max(1, math.min(cellWidth, cellHeight) * 0.92);
        canvas.drawRect(
          Rect.fromLTWH(
            x,
            (anchorRow * cellHeight).floorToDouble(),
            side,
            side,
          ),
          pixel,
        );
      }
    }
  }

  @override
  bool shouldRepaint(_BitHorizonPainter oldDelegate) =>
      oldDelegate.stage != stage ||
      !identical(oldDelegate.seed, seed) ||
      oldDelegate.progress != progress;
}
