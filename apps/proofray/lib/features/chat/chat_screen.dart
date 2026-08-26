import 'dart:async';

import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../l10n/app_strings.dart';
import '../../models/chat_models.dart';
import 'memory_activation_wave.dart';
import '../local_models/local_model_controller.dart';
import 'chat_composer.dart';
import 'local_model_loading_overlay.dart';
import 'chat_controller.dart';
import 'message_transcript.dart';
import 'proof_observatory.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({
    required this.controller,
    required this.localModels,
    required this.providerSwitcher,
    super.key,
  });

  final ChatController controller;
  final LocalModelController localModels;
  final Widget providerSwitcher;

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  ChatMessage? _selectedProof;
  bool _observatoryCollapsed = false;
  final ScrollController _transcript = ScrollController();

  @override
  void dispose() {
    _transcript.dispose();
    super.dispose();
  }

  @override
  void didUpdateWidget(ChatScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller) {
      _selectedProof = null;
      WidgetsBinding.instance.addPostFrameCallback((_) => _jumpToTail());
    }
  }

  void _jumpToTail() {
    if (_transcript.hasClients) {
      _transcript.jumpTo(_transcript.position.maxScrollExtent);
    }
  }

  void _followTailIfAppropriate() {
    final bool follow =
        !_transcript.hasClients || _transcript.position.extentAfter < 120;
    if (!follow) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_transcript.hasClients) return;
      _jumpToTail();
    });
  }

  /// Wraps the conversation so a loading model blocks only the chat.
  ///
  /// Navigation, settings and the conversation list keep working: none of them
  /// depend on what is in VRAM, and locking the whole app would make a slow
  /// load look like a hang.
  Widget _withLoadingOverlay(Widget conversation) => ListenableBuilder(
    listenable: widget.localModels,
    builder: (BuildContext context, Widget? child) => Stack(
      children: <Widget>[
        child!,
        if (widget.localModels.loading)
          Positioned.fill(
            child: LocalModelLoadingOverlay(
              modelName:
                  widget.localModels.active?.name ??
                  (Localizations.localeOf(context).languageCode == 'pt'
                      ? 'modelo local'
                      : 'local model'),
            ),
          ),
      ],
    ),
    child: conversation,
  );

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: widget.controller,
    builder: (BuildContext context, Widget? child) {
      _followTailIfAppropriate();
      final bool desktop = MediaQuery.sizeOf(context).width >= 1050;
      final Widget conversation = _withLoadingOverlay(_Conversation(
        controller: widget.controller,
        localModels: widget.localModels,
        providerSwitcher: widget.providerSwitcher,
        transcriptController: _transcript,
        onOpenProof: (ChatMessage message) {
          setState(() => _selectedProof = message);
          if (!desktop) {
            _showMobileObservatory(message);
          }
        },
      ));
      if (!desktop) {
        return conversation;
      }
      final AppStrings strings = AppStrings.of(context);
      return Row(
        children: <Widget>[
          Expanded(child: conversation),
          const VerticalDivider(width: 1),
          if (_observatoryCollapsed)
            _ObservatoryCollapseStrip(
              tooltip: strings.locale.languageCode == 'pt'
                  ? 'Expandir Observatório'
                  : 'Expand Observatory',
              icon: Icons.chevron_left,
              onPressed: () => setState(() => _observatoryCollapsed = false),
            )
          else
            SizedBox(
              width: 340,
              child: Column(
                children: <Widget>[
                  Align(
                    alignment: Alignment.centerRight,
                    child: IconButton(
                      tooltip: strings.locale.languageCode == 'pt'
                          ? 'Recolher Observatório'
                          : 'Collapse Observatory',
                      onPressed: () =>
                          setState(() => _observatoryCollapsed = true),
                      icon: const Icon(Icons.chevron_right, size: 18),
                    ),
                  ),
                  Expanded(
                    child: ProofObservatory(message: _selectedProof),
                  ),
                ],
              ),
            ),
        ],
      );
    },
  );

  Future<void> _showMobileObservatory(ChatMessage message) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: ProofRayColors.softPaper,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(2)),
      ),
      builder: (BuildContext context) => FractionallySizedBox(
        heightFactor: 0.84,
        child: ProofObservatory(
          message: message,
          onClose: () => Navigator.of(context).pop(),
        ),
      ),
    );
  }
}

class _Conversation extends StatelessWidget {
  const _Conversation({
    required this.controller,
    required this.localModels,
    required this.providerSwitcher,
    required this.onOpenProof,
    required this.transcriptController,
  });

  final ChatController controller;
  final LocalModelController localModels;
  final Widget providerSwitcher;
  final ValueChanged<ChatMessage> onOpenProof;
  final ScrollController transcriptController;


  @override
  Widget build(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    return Column(
      children: <Widget>[
        // Was BitHorizonWave, whose evidence stage painted a scattered dot
        // field that read as noise next to the header. The activation wave
        // shows the same monochrome pixel pattern used on the launch screen,
        // and guarantees a visible second even when the deterministic core
        // answers instantly.
        RepaintBoundary(
          child: MemoryActivationWave(
            stage: controller.stage,
            queryDigest: controller.queryDigest,
          ),
        ),
        Expanded(
          child: controller.messages.isEmpty
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(40),
                    child: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 420),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: <Widget>[
                          Image.asset(
                            'assets/ProofRay.jpeg',
                            width: 78,
                            height: 78,
                            cacheWidth: 160,
                            cacheHeight: 160,
                          ),
                          const SizedBox(height: 22),
                          Text(
                            strings.noConversationYet,
                            textAlign: TextAlign.center,
                            style: const TextStyle(
                              height: 1.55,
                              color: ProofRayColors.quietInk,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                )
              : ListView.builder(
                  controller: transcriptController,
                  padding: const EdgeInsets.symmetric(
                    horizontal: 24,
                    vertical: 12,
                  ),
                  itemCount: controller.messages.length,
                  itemBuilder: (BuildContext context, int index) {
                    final ChatMessage message = controller.messages[index];
                    return MessageTranscript(
                      key: ValueKey<String>(message.id),
                      message: message,
                      onOpenProof: () => onOpenProof(message),
                      onConfirmMemory: () =>
                          unawaited(controller.confirmAsMemory(message)),
                    );
                  },
                ),
        ),
        ChatComposer(
          memoryMode: controller.memoryMode,
          onMemoryModeChanged: controller.setMemoryMode,
          onSend: (String text) => unawaited(controller.send(text)),
          sending: controller.sending,
          onCancel: () => unawaited(controller.cancel()),
          toolModeAvailable: controller.providerSupportsTools,
          localModels: localModels,
          providerSwitcher: providerSwitcher,
        ),
      ],
    );
  }
}

/// The thin, always-visible strip the desktop Observatory panel shrinks
/// down to when collapsed -- keeps a way back to re-expand it.
class _ObservatoryCollapseStrip extends StatelessWidget {
  const _ObservatoryCollapseStrip({
    required this.tooltip,
    required this.icon,
    required this.onPressed,
  });

  final String tooltip;
  final IconData icon;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) => SizedBox(
    width: 32,
    child: IconButton(
      tooltip: tooltip,
      onPressed: onPressed,
      icon: Icon(icon, size: 16, color: ProofRayColors.ink),
    ),
  );
}
