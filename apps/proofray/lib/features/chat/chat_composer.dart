import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../design/proofray_theme.dart';
import '../../l10n/app_strings.dart';
import '../../models/chat_models.dart';

class ChatComposer extends StatefulWidget {
  const ChatComposer({
    required this.memoryMode,
    required this.onMemoryModeChanged,
    required this.onSend,
    required this.sending,
    required this.onCancel,
    required this.toolModeAvailable,
    super.key,
  });

  final MemoryMode memoryMode;
  final ValueChanged<MemoryMode> onMemoryModeChanged;
  final ValueChanged<String> onSend;
  final bool sending;
  final VoidCallback onCancel;
  final bool toolModeAvailable;

  @override
  State<ChatComposer> createState() => _ChatComposerState();
}

class _ChatComposerState extends State<ChatComposer> {
  final TextEditingController _controller = TextEditingController();
  final FocusNode _focusNode = FocusNode();

  @override
  void dispose() {
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _send() {
    final String text = _controller.text.trim();
    if (text.isEmpty || widget.sending) {
      return;
    }
    widget.onSend(text);
    _controller.clear();
    _focusNode.requestFocus();
  }

  String _modeLabel(AppStrings strings, MemoryMode mode) => switch (mode) {
    MemoryMode.tool => strings.toolMode,
    MemoryMode.keywords => strings.keywordMode,
    MemoryMode.forceNext => strings.forceNext,
    MemoryMode.off => strings.memoryOff,
  };

  @override
  Widget build(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    return DecoratedBox(
      decoration: const BoxDecoration(
        color: ProofRayColors.paper,
        border: Border(top: BorderSide(color: ProofRayColors.hairline)),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 14),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: <Widget>[
            PopupMenuButton<MemoryMode>(
              key: const ValueKey<String>('memory-mode-button'),
              tooltip: _modeLabel(strings, widget.memoryMode),
              initialValue: widget.memoryMode,
              onSelected: widget.onMemoryModeChanged,
              itemBuilder: (BuildContext context) => MemoryMode.values
                  .map(
                    (MemoryMode mode) => PopupMenuItem<MemoryMode>(
                      value: mode,
                      enabled:
                          mode != MemoryMode.tool || widget.toolModeAvailable,
                      child: Row(
                        children: <Widget>[
                          if (mode == widget.memoryMode)
                            const Icon(Icons.check, size: 16)
                          else
                            const SizedBox(width: 16),
                          const SizedBox(width: 9),
                          Text(_modeLabel(strings, mode)),
                        ],
                      ),
                    ),
                  )
                  .toList(growable: false),
              child: Container(
                height: 48,
                width: 48,
                decoration: BoxDecoration(
                  border: Border.all(color: ProofRayColors.hairline),
                  borderRadius: const BorderRadius.all(Radius.circular(2)),
                  color: widget.memoryMode == MemoryMode.off
                      ? ProofRayColors.paper
                      : ProofRayColors.softPaper,
                ),
                child: Icon(
                  widget.memoryMode == MemoryMode.off
                      ? Icons.psychology_alt_outlined
                      : Icons.psychology_alt,
                  semanticLabel: _modeLabel(strings, widget.memoryMode),
                ),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: TextField(
                controller: _controller,
                focusNode: _focusNode,
                minLines: 1,
                maxLines: 6,
                inputFormatters: <TextInputFormatter>[
                  const Utf8LengthLimitingTextInputFormatter(64 * 1024),
                ],
                textInputAction: TextInputAction.newline,
                decoration: InputDecoration(
                  hintText: strings.askAnything,
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 14,
                    vertical: 13,
                  ),
                ),
                onSubmitted: (_) => _send(),
              ),
            ),
            const SizedBox(width: 8),
            IconButton.filled(
              onPressed: widget.sending ? widget.onCancel : _send,
              style: IconButton.styleFrom(
                shape: const RoundedRectangleBorder(
                  borderRadius: BorderRadius.all(Radius.circular(2)),
                ),
                minimumSize: const Size(48, 48),
              ),
              icon: widget.sending
                  ? const Icon(Icons.stop)
                  : const Icon(Icons.arrow_upward),
            ),
          ],
        ),
      ),
    );
  }
}

class Utf8LengthLimitingTextInputFormatter extends TextInputFormatter {
  const Utf8LengthLimitingTextInputFormatter(this.maxBytes);

  final int maxBytes;

  @override
  TextEditingValue formatEditUpdate(
    TextEditingValue oldValue,
    TextEditingValue newValue,
  ) => utf8.encode(newValue.text).length <= maxBytes ? newValue : oldValue;
}
