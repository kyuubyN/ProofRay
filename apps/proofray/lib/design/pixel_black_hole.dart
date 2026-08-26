import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'pixel_wave.dart';

/// A black hole drawn in the same visual language as [PixelWave]: one square
/// grid, a 4x4 Bayer matrix, pure black or pure white, never anti-aliased.
///
/// Everything here is arithmetic on the cell's polar coordinates, so there is
/// no asset to ship and it stays crisp at any size:
///
///  * inside the event horizon nothing is dithered -- it is solid, the only
///    region in the whole image with no texture at all;
///  * a thin photon ring sits just outside it, the brightest thing on screen;
///  * an accretion disk swirls around that, brighter on the side rotating
///    toward the viewer (relativistic beaming);
///  * the background wave is sampled through a deflection that grows as it
///    approaches the horizon, so the diagonal bands visibly bend around it.
class PixelBlackHole extends StatefulWidget {
  const PixelBlackHole({
    super.key,
    this.gridSize = PixelWave.referenceGridSize,
    this.speed = 0.25,
    this.frequency = 4,
    this.foreground = const Color(0xFF000000),
    this.background = const Color(0xFFFFFFFF),
  });

  final int gridSize;
  final double speed;
  final double frequency;
  final Color foreground;
  final Color background;

  @override
  State<PixelBlackHole> createState() => _PixelBlackHoleState();
}

class _PixelBlackHoleState extends State<PixelBlackHole>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  bool _reduceMotion = false;

  @override
  void initState() {
    super.initState();
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
        painter: PixelBlackHolePainter(
          time: _controller.value,
          gridSize: widget.gridSize,
          frequency: widget.frequency,
          foreground: widget.foreground,
          background: widget.background,
        ),
        size: Size.infinite,
      ),
    ),
  );
}

const List<List<double>> _bayer4x4 = <List<double>>[
  <double>[0 / 16, 8 / 16, 2 / 16, 10 / 16],
  <double>[12 / 16, 4 / 16, 14 / 16, 6 / 16],
  <double>[3 / 16, 11 / 16, 1 / 16, 9 / 16],
  <double>[15 / 16, 7 / 16, 13 / 16, 5 / 16],
];

class PixelBlackHolePainter extends CustomPainter {
  const PixelBlackHolePainter({
    required this.time,
    required this.gridSize,
    required this.frequency,
    required this.foreground,
    required this.background,
  });

  final double time;
  final int gridSize;
  final double frequency;
  final Color foreground;
  final Color background;

  /// Radii as a fraction of the grid's half-width.
  static const double _horizon = 0.155;
  static const double _photonRing = 0.245;
  static const double _diskRadius = 0.395;
  static const double _diskWidth = 0.10;

  @override
  void paint(Canvas canvas, Size size) {
    if (size.isEmpty) return;
    canvas.drawRect(Offset.zero & size, Paint()..color = background);

    final double cell = math.max(size.width, size.height) / gridSize;
    final int columns = (size.width / cell).ceil();
    final int rows = (size.height / cell).ceil();
    final double centreX = (columns - 1) / 2;
    final double centreY = (rows - 1) / 2;
    // Normalize against the shorter axis so the hole stays circular in a
    // non-square viewport instead of being squashed into an ellipse.
    final double unit = math.min(columns, rows) / 2;
    final int last = math.max(1, gridSize - 1);
    final double spin = time * math.pi * 2;

    final Paint pixel = Paint()
      ..color = foreground
      ..isAntiAlias = false
      ..style = PaintingStyle.fill;

    for (int y = 0; y < rows; y++) {
      for (int x = 0; x < columns; x++) {
        final double dx = (x - centreX) / unit;
        final double dy = (y - centreY) / unit;
        final double radius = math.sqrt(dx * dx + dy * dy);

        final bool paintCell;
        if (radius <= _horizon) {
          // The event horizon is the one untextured region: solid, so it reads
          // as absence rather than as another shade of the pattern.
          paintCell = true;
        } else {
          paintCell =
              _light(x, y, dx, dy, radius, last, spin) <=
              _bayer4x4[y % 4][x % 4];
        }
        if (!paintCell) continue;
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

  /// Luminance in [0, 1]; 0 is solid ink, 1 is bare paper. The caller dithers
  /// it, so only the ordering of these values matters, never their exactness.
  double _light(
    int x,
    int y,
    double dx,
    double dy,
    double radius,
    int last,
    double spin,
  ) {
    // Photon sphere: a clean band of paper hugging the horizon. Without it the
    // solid disc would run straight into the dark accretion ring and the shape
    // would stop reading as a hole at all.
    if (radius < _photonRing) {
      return ((radius - _horizon) / (_photonRing - _horizon)).clamp(0.0, 1.0);
    }

    // Gravitational lensing: the same diagonal wave the rest of the app draws,
    // sampled from an apparent position pushed outward by k / r^2. The 1/r^2
    // falloff is the whole point -- a plain 1 + k/r factor works out to r + k,
    // a uniform shift that slides the bands sideways without ever bending them.
    final double bent = 1 + 0.20 / (radius * radius);
    final double wx = dx * bent;
    final double wy = dy * bent;
    // Back into the wave's own diagonal coordinate, in the same sense the
    // background uses (bottom-right to top-left).
    final double diagonal = (-(wx + wy) / 4).clamp(-6.0, 6.0);
    final double band =
        (math.sin(diagonal * frequency * math.pi * 2 - spin) + 1) / 2;
    // Paper-dominant, so the background reads as the app's own surface rather
    // than as a field of noise.
    double light = 0.62 + 0.38 * band;

    // Accretion disk: a ring of ink around the photon sphere, densest on its
    // own centreline and thinning to nothing on both sides.
    final double ring = math.exp(
      -math.pow((radius - _diskRadius) / _diskWidth, 2).toDouble(),
    );
    if (ring > 0.004) {
      final double angle = math.atan2(dy, dx);
      // Angular velocity rises closer in, so the inner edge outruns the outer.
      final double swirl =
          (math.sin(angle * 3 - spin * 2.2 / math.max(radius, 0.25)) + 1) / 2;
      // Relativistic beaming: the limb rotating toward the viewer is denser.
      final double beaming = 0.80 + 0.20 * math.cos(angle - math.pi / 2);
      light -= ring * (0.82 + 0.18 * swirl) * beaming;
    }
    return light.clamp(0.0, 1.0);
  }

  @override
  bool shouldRepaint(PixelBlackHolePainter oldDelegate) =>
      oldDelegate.time != time ||
      oldDelegate.gridSize != gridSize ||
      oldDelegate.frequency != frequency ||
      oldDelegate.foreground != foreground ||
      oldDelegate.background != background;
}
