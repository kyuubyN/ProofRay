import 'dart:math' as math;

import 'package:flutter/material.dart';

/// Monochrome pixel wave, ported faithfully from the reference TypeScript
/// renderer in `Documentos/Coder` (`src/renderer.ts`): a diagonal distance
/// metric drives a sine phase, a 4x4 Bayer matrix dithers it to pure black or
/// white, and nothing is ever anti-aliased. Every default here matches that
/// project's own `initialConfig` -- 128x128 grid, speed 0.1, frequency 4,
/// bottom-right to top-left, Bayer dither, BW palette.
///
/// This is arithmetic, not an asset: there is no image to ship, decode or keep
/// in sync, and it stays crisp at any size.
class PixelWave extends StatefulWidget {
  const PixelWave({
    super.key,
    this.gridSize = 128,
    this.speed = 0.1,
    this.frequency = 4,
    this.direction = PixelWaveDirection.bottomRightToTopLeft,
    this.foreground = const Color(0xFF000000),
    this.background = const Color(0xFFFFFFFF),
  });

  /// The reference project's own grid resolution. Kept as a named constant so
  /// every surface in the app renders the same wave rather than a look-alike.
  static const int referenceGridSize = 128;

  final int gridSize;
  final double speed;
  final double frequency;
  final PixelWaveDirection direction;
  final Color foreground;
  final Color background;

  @override
  State<PixelWave> createState() => _PixelWaveState();
}

enum PixelWaveDirection {
  bottomRightToTopLeft,
  topLeftToBottomRight,
  centerOut,
}

class _PixelWaveState extends State<PixelWave>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  bool _reduceMotion = false;

  @override
  void initState() {
    super.initState();
    // One controller revolution is one full wave cycle; the reference renderer
    // advances `time` by `delta * 2.5 * speed` per frame, so a cycle lasts
    // 1 / (2.5 * speed) seconds.
    _controller = AnimationController(
      vsync: this,
      duration: Duration(
        milliseconds: (1000 / (2.5 * widget.speed)).round().clamp(200, 60000),
      ),
    )..repeat();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final bool reduceMotion = MediaQuery.disableAnimationsOf(context);
    if (_reduceMotion == reduceMotion) return;
    _reduceMotion = reduceMotion;
    // Accessibility: a reduced-motion request still gets the pattern, frozen at
    // a representative phase, rather than a blank rectangle.
    if (_reduceMotion) {
      _controller.stop();
      _controller.value = 0.25;
    } else if (!_controller.isAnimating) {
      _controller.repeat();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => RepaintBoundary(
    child: AnimatedBuilder(
      animation: _controller,
      builder: (BuildContext context, Widget? child) => CustomPaint(
        painter: PixelWavePainter(
          time: _controller.value,
          gridSize: widget.gridSize,
          frequency: widget.frequency,
          direction: widget.direction,
          foreground: widget.foreground,
          background: widget.background,
        ),
        size: Size.infinite,
      ),
    ),
  );
}

/// The reference project's 4x4 Bayer matrix, normalized to [0, 1).
const List<List<double>> _bayer4x4 = <List<double>>[
  <double>[0 / 16, 8 / 16, 2 / 16, 10 / 16],
  <double>[12 / 16, 4 / 16, 14 / 16, 6 / 16],
  <double>[3 / 16, 11 / 16, 1 / 16, 9 / 16],
  <double>[15 / 16, 7 / 16, 13 / 16, 5 / 16],
];

class PixelWavePainter extends CustomPainter {
  const PixelWavePainter({
    required this.time,
    required this.gridSize,
    required this.frequency,
    required this.direction,
    required this.foreground,
    required this.background,
  });

  /// Wave phase as a fraction of one full cycle.
  final double time;
  final int gridSize;
  final double frequency;
  final PixelWaveDirection direction;
  final Color foreground;
  final Color background;

  @override
  void paint(Canvas canvas, Size size) {
    if (size.isEmpty) return;
    canvas.drawRect(Offset.zero & size, Paint()..color = background);

    // The grid is square and pixels stay square, so a non-square viewport
    // shows a centered slice rather than a stretched wave.
    final double cell = math.max(size.width, size.height) / gridSize;
    final int columns = (size.width / cell).ceil();
    final int rows = (size.height / cell).ceil();
    final int last = math.max(1, gridSize - 1);

    final Paint pixel = Paint()
      ..color = foreground
      ..isAntiAlias = false
      ..style = PaintingStyle.fill;

    for (int y = 0; y < rows; y++) {
      for (int x = 0; x < columns; x++) {
        final double normalized = switch (direction) {
          PixelWaveDirection.bottomRightToTopLeft =>
            ((last - x) + (last - y)) / (2 * last),
          PixelWaveDirection.topLeftToBottomRight => (x + y) / (2 * last),
          PixelWaveDirection.centerOut => _centerDistance(x, y, last),
        };
        final double phase =
            (normalized * frequency * math.pi * 2) - (time * math.pi * 2);
        final double base = (math.sin(phase) + 1) / 2;
        // Bayer dithering: pure black or white, never a grey. This is what
        // makes the gradient read as pixel art instead of a blur.
        if (base <= _bayer4x4[y % 4][x % 4]) continue;
        canvas.drawRect(
          Rect.fromLTWH(
            (x * cell).floorToDouble(),
            (y * cell).floorToDouble(),
            cell.ceilToDouble(),
            cell.ceilToDouble(),
          ),
          pixel,
        );
      }
    }
  }

  double _centerDistance(int x, int y, int last) {
    final double dx = x - last / 2;
    final double dy = y - last / 2;
    return math.sqrt(dx * dx + dy * dy) / (math.sqrt2 * (gridSize / 2));
  }

  @override
  bool shouldRepaint(PixelWavePainter oldDelegate) =>
      oldDelegate.time != time ||
      oldDelegate.gridSize != gridSize ||
      oldDelegate.frequency != frequency ||
      oldDelegate.direction != direction ||
      oldDelegate.foreground != foreground ||
      oldDelegate.background != background;
}
