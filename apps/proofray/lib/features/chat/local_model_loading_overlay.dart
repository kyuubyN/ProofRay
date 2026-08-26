import 'package:flutter/material.dart';

import '../../design/pixel_wave.dart';
import '../../design/proofray_theme.dart';

/// Covers the conversation while a local model is being read into VRAM.
///
/// Loading a multi-gigabyte GGUF takes real time, and llama.cpp does not serve
/// anything until it finishes -- so the honest thing is to say the chat is
/// unavailable rather than accept a message that would sit unanswered. It
/// covers the conversation only: the sidebar, navigation and settings stay
/// usable, because nothing about them depends on the model.
class LocalModelLoadingOverlay extends StatelessWidget {
  const LocalModelLoadingOverlay({required this.modelName, super.key});

  final String modelName;

  @override
  Widget build(BuildContext context) {
    final bool pt = Localizations.localeOf(context).languageCode == 'pt';
    return ColoredBox(
      color: ProofRayColors.paper,
      child: Stack(
        fit: StackFit.expand,
        children: <Widget>[
          const PixelWave(
            gridSize: PixelWave.referenceGridSize,
            speed: 0.35,
            foreground: ProofRayColors.ink,
            background: ProofRayColors.paper,
          ),
          Center(
            child: DecoratedBox(
              decoration: const BoxDecoration(
                color: ProofRayColors.paper,
                border: Border.fromBorderSide(
                  BorderSide(color: ProofRayColors.ink),
                ),
              ),
              child: Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: 24,
                  vertical: 18,
                ),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(
                      pt ? 'CARREGANDO MODELO LOCAL' : 'LOADING LOCAL MODEL',
                      style: const TextStyle(
                        color: ProofRayColors.ink,
                        fontFamily: 'monospace',
                        fontSize: 11,
                        letterSpacing: 2,
                      ),
                    ),
                    const SizedBox(height: 8),
                    ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 320),
                      child: Text(
                        modelName,
                        textAlign: TextAlign.center,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: ProofRayColors.ink,
                          fontFamily: 'monospace',
                          fontSize: 12,
                        ),
                      ),
                    ),
                    const SizedBox(height: 10),
                    Text(
                      pt
                          ? 'O chat volta assim que os pesos estiverem na VRAM.'
                          : 'The chat returns once the weights are in VRAM.',
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        color: ProofRayColors.ink,
                        fontSize: 11,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
