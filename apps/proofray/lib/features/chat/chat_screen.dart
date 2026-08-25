import 'dart:async';

import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../l10n/app_strings.dart';
import '../../models/chat_models.dart';
import 'bit_horizon_wave.dart';
import 'chat_composer.dart';
import 'chat_controller.dart';
import 'message_transcript.dart';
import 'proof_observatory.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({required this.controller, super.key});

  final ChatController controller;

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  ChatMessage? _selectedProof;
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

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: widget.controller,
    builder: (BuildContext context, Widget? child) {
      _followTailIfAppropriate();
      final bool desktop = MediaQuery.sizeOf(context).width >= 1050;
      final Widget conversation = _Conversation(
        controller: widget.controller,
        transcriptController: _transcript,
        onOpenProof: (ChatMessage message) {
          setState(() => _selectedProof = message);
          if (!desktop) {
            _showMobileObservatory(message);
          }
        },
      );
      if (!desktop) {
        return conversation;
      }
      return Row(
        children: <Widget>[
          Expanded(child: conversation),
          const VerticalDivider(width: 1),
          SizedBox(
            width: 340,
            child: ProofObservatory(message: _selectedProof),
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
    required this.onOpenProof,
    required this.transcriptController,
  });

  final ChatController controller;
  final ValueChanged<ChatMessage> onOpenProof;
  final ScrollController transcriptController;

  @override
  Widget build(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    return Column(
      children: <Widget>[
        if (controller.stage != BitHorizonStage.idle)
          RepaintBoundary(
            child: BitHorizonWave(
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
        ),
      ],
    );
  }
}
