import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../l10n/app_strings.dart';
import '../../models/chat_models.dart';
import 'memory_activation_badge.dart';

class MessageTranscript extends StatelessWidget {
  const MessageTranscript({
    required this.message,
    required this.onOpenProof,
    required this.onConfirmMemory,
    super.key,
  });

  final ChatMessage message;
  final VoidCallback onOpenProof;
  final VoidCallback onConfirmMemory;

  String _authorityLabel(AppStrings strings) => switch (message.authority) {
    AnswerAuthority.proved => strings.proved,
    AnswerAuthority.evidence => strings.evidence,
    AnswerAuthority.abstention => strings.abstention,
    AnswerAuthority.contested => strings.contested,
    AnswerAuthority.model => strings.model,
    AnswerAuthority.pending => strings.pending,
  };

  String _visibleText(AppStrings strings) {
    if (message.text.isNotEmpty) {
      return message.text;
    }
    return switch (message.authority) {
      AnswerAuthority.pending => '···',
      AnswerAuthority.abstention =>
        strings.locale.languageCode == 'pt'
            ? 'Não encontrei memória suficiente para responder com segurança.'
            : 'I could not find enough memory to answer safely.',
      AnswerAuthority.contested =>
        strings.locale.languageCode == 'pt'
            ? 'Encontrei memórias incompatíveis e não escolhi uma delas.'
            : 'I found incompatible memories and did not choose between them.',
      AnswerAuthority.model =>
        strings.locale.languageCode == 'pt'
            ? 'Nenhum provedor de IA está conectado. O ProofRay continua disponível para memória, evidência e abstenção.'
            : 'No AI provider is connected. ProofRay remains available for memory, evidence and abstention.',
      _ => '',
    };
  }

  @override
  Widget build(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    final bool user = message.role == MessageRole.user;
    return Semantics(
      label: user
          ? (strings.locale.languageCode == 'pt'
                ? 'Mensagem do usuário'
                : 'User message')
          : _authorityLabel(strings),
      child: Padding(
        padding: EdgeInsets.only(
          left: user ? 52 : 0,
          right: user ? 0 : 28,
          top: 18,
          bottom: 18,
        ),
        child: DecoratedBox(
          decoration: const BoxDecoration(
            border: Border(top: BorderSide(color: ProofRayColors.hairline)),
          ),
          child: Padding(
            padding: const EdgeInsets.only(top: 11),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Text(
                      user
                          ? (strings.locale.languageCode == 'pt'
                                ? 'VOCÊ'
                                : 'YOU')
                          : _authorityLabel(strings),
                      style: Theme.of(context).textTheme.labelSmall
                          ?.copyWith(color: ProofRayColors.quietInk),
                    ),
                    const Spacer(),
                    if (!user && message.memoryConsulted)
                      TextButton(
                        onPressed: onOpenProof,
                        style: TextButton.styleFrom(
                          foregroundColor: ProofRayColors.ink,
                          padding: const EdgeInsets.symmetric(horizontal: 8),
                          minimumSize: const Size(0, 28),
                          shape: const RoundedRectangleBorder(
                            borderRadius: BorderRadius.all(Radius.circular(2)),
                          ),
                        ),
                        child: Text(strings.observatory),
                      ),
                  ],
                ),
                const SizedBox(height: 9),
                _CertifiedTextToggle(
                  displayedText: _visibleText(strings),
                  certifiedText: message.certifiedText,
                  pending: message.authority == AnswerAuthority.pending,
                ),
                if (!user && message.textTruncated)
                  Padding(
                    padding: const EdgeInsets.only(top: 7),
                    child: Text(
                      strings.locale.languageCode == 'pt'
                          ? 'Trecho limitado a 24.576 bytes; a fonte integral continua reabrível.'
                          : 'Excerpt limited to 24,576 bytes; the full source remains reopenable.',
                      style: Theme.of(context).textTheme.labelSmall
                          ?.copyWith(color: ProofRayColors.quietInk),
                    ),
                  ),
                if (!user)
                  MemoryActivationBadge(activated: message.memoryConsulted),
                if (!user && message.text.isNotEmpty)
                  TextButton.icon(
                    onPressed: onConfirmMemory,
                    icon: const Icon(Icons.add_circle_outline, size: 14),
                    label: Text(
                      strings.locale.languageCode == 'pt'
                          ? 'Confirmar como minha memória'
                          : 'Confirm as my memory',
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _CertifiedTextToggle extends StatefulWidget {
  const _CertifiedTextToggle({
    required this.displayedText,
    required this.certifiedText,
    required this.pending,
  });

  final String displayedText;
  final String? certifiedText;
  final bool pending;

  @override
  State<_CertifiedTextToggle> createState() => _CertifiedTextToggleState();
}

class _CertifiedTextToggleState extends State<_CertifiedTextToggle> {
  bool _exact = false;

  @override
  void didUpdateWidget(_CertifiedTextToggle oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.certifiedText == null ||
        widget.certifiedText == widget.displayedText) {
      _exact = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    final bool rewritten =
        widget.certifiedText != null &&
        widget.certifiedText != widget.displayedText;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        SelectableText(
          _exact && widget.certifiedText != null
              ? widget.certifiedText!
              : widget.displayedText,
          style: Theme.of(context).textTheme.bodyLarge?.copyWith(
            height: 1.55,
            fontStyle: widget.pending ? FontStyle.italic : null,
          ),
        ),
        if (rewritten)
          TextButton(
            onPressed: () => setState(() => _exact = !_exact),
            child: Text(
              _exact
                  ? (strings.locale.languageCode == 'pt'
                        ? 'Mostrar reescrita do modelo'
                        : 'Show model rewrite')
                  : strings.exactCertifiedText,
            ),
          ),
      ],
    );
  }
}
