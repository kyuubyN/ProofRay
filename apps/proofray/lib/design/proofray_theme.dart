import 'package:flutter/material.dart';

abstract final class ProofRayColors {
  static const Color paper = Color(0xFFFDFDFC);
  static const Color ink = Color(0xFF090909);
  static const Color quietInk = Color(0xFF626262);
  static const Color hairline = Color(0xFFE4E4E0);
  static const Color softPaper = Color(0xFFF5F5F1);

  /// The single semantic color permitted by the Bit Horizon identity.
  /// It means only "ProofRay memory was activated for this answer".
  static const Color memoryGreen = Color(0xFF178A4B);
}

ThemeData buildProofRayTheme() {
  const ColorScheme scheme = ColorScheme.light(
    primary: ProofRayColors.ink,
    onPrimary: ProofRayColors.paper,
    secondary: ProofRayColors.ink,
    onSecondary: ProofRayColors.paper,
    surface: ProofRayColors.paper,
    onSurface: ProofRayColors.ink,
    error: ProofRayColors.ink,
    onError: ProofRayColors.paper,
    outline: ProofRayColors.hairline,
  );

  final TextTheme text = Typography.material2021().black.apply(
    bodyColor: ProofRayColors.ink,
    displayColor: ProofRayColors.ink,
    fontFamily: 'sans-serif',
  );

  return ThemeData(
    useMaterial3: true,
    brightness: Brightness.light,
    colorScheme: scheme,
    scaffoldBackgroundColor: ProofRayColors.paper,
    dividerColor: ProofRayColors.hairline,
    textTheme: text.copyWith(
      headlineLarge: text.headlineLarge?.copyWith(
        fontWeight: FontWeight.w400,
        letterSpacing: -1.2,
      ),
      titleMedium: text.titleMedium?.copyWith(fontWeight: FontWeight.w600),
      labelSmall: text.labelSmall?.copyWith(
        fontFamily: 'monospace',
        letterSpacing: 0.8,
      ),
    ),
    iconTheme: const IconThemeData(color: ProofRayColors.ink, size: 20),
    dividerTheme: const DividerThemeData(
      color: ProofRayColors.hairline,
      thickness: 1,
      space: 1,
    ),
    cardTheme: const CardThemeData(
      color: ProofRayColors.paper,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        side: BorderSide(color: ProofRayColors.hairline),
        borderRadius: BorderRadius.all(Radius.circular(2)),
      ),
    ),
    inputDecorationTheme: const InputDecorationTheme(
      filled: false,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.all(Radius.circular(2)),
        borderSide: BorderSide(color: ProofRayColors.hairline),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.all(Radius.circular(2)),
        borderSide: BorderSide(color: ProofRayColors.hairline),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.all(Radius.circular(2)),
        borderSide: BorderSide(color: ProofRayColors.ink),
      ),
    ),
  );
}
