import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../l10n/app_strings.dart';
import '../../models/chat_models.dart';

class ProofObservatory extends StatelessWidget {
  const ProofObservatory({required this.message, super.key, this.onClose});

  final ChatMessage? message;
  final VoidCallback? onClose;

  @override
  Widget build(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    final ChatMessage? selected = message;
    return ColoredBox(
      color: ProofRayColors.softPaper,
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: selected == null
              ? Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    _Header(title: strings.observatory, onClose: onClose),
                    const SizedBox(height: 28),
                    Text(
                      strings.locale.languageCode == 'pt'
                          ? 'Selecione uma resposta para inspecionar sua autoridade.'
                          : 'Select an answer to inspect its authority.',
                      style: const TextStyle(color: ProofRayColors.quietInk),
                    ),
                  ],
                )
              : ListView(
                  children: <Widget>[
                    _Header(title: strings.observatory, onClose: onClose),
                    const SizedBox(height: 24),
                    _LabelValue(
                      label: strings.locale.languageCode == 'pt'
                          ? 'ESTADO'
                          : 'STATE',
                      value: selected.authority.name.toUpperCase(),
                    ),
                    if (selected.proofMethod != null) ...<Widget>[
                      const SizedBox(height: 8),
                      _LabelValue(
                        label: strings.locale.languageCode == 'pt'
                            ? 'MÉTODO'
                            : 'METHOD',
                        value: selected.proofMethod!,
                      ),
                    ],
                    if (selected.memoryConsulted) ...<Widget>[
                      const SizedBox(height: 8),
                      _LabelValue(
                        label: strings.locale.languageCode == 'pt'
                            ? 'DOCUMENTOS / VERIFICADOS / BYTES'
                            : 'DOCUMENTS / VERIFIED / BYTES',
                        value:
                            '${selected.documentsConsidered} / '
                            '${selected.verifiedCandidates} / ${selected.answerBytes}',
                      ),
                    ],
                    if (selected.queryDigest != null &&
                        selected.queryDigest!.isNotEmpty) ...<Widget>[
                      const SizedBox(height: 18),
                      const Text(
                        'QUERY DIGEST',
                        style: TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 10,
                          letterSpacing: 0.8,
                        ),
                      ),
                      const SizedBox(height: 6),
                      SelectableText(
                        selected.queryDigest!,
                        style: const TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 9,
                          color: ProofRayColors.quietInk,
                        ),
                      ),
                    ],
                    if (selected.certifiedText != null) ...<Widget>[
                      const SizedBox(height: 24),
                      Text(
                        strings.exactCertifiedText,
                        style: Theme.of(context).textTheme.labelSmall,
                      ),
                      const SizedBox(height: 8),
                      SelectableText(
                        selected.certifiedText!,
                        style: const TextStyle(height: 1.5),
                      ),
                    ],
                    if (selected.certificateHex != null) ...<Widget>[
                      const SizedBox(height: 24),
                      const Text(
                        'CERTIFICATE',
                        style: TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 10,
                          letterSpacing: 0.8,
                        ),
                      ),
                      const SizedBox(height: 8),
                      SelectableText(
                        selected.certificateHex!,
                        maxLines: 6,
                        style: const TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 10,
                          color: ProofRayColors.quietInk,
                        ),
                      ),
                    ],
                    const SizedBox(height: 24),
                    Text(
                      strings.proofSources,
                      style: Theme.of(context).textTheme.labelSmall,
                    ),
                    const SizedBox(height: 10),
                    if (selected.sources.isEmpty)
                      Text(
                        strings.locale.languageCode == 'pt'
                            ? 'Nenhuma fonte publicada.'
                            : 'No published sources.',
                        style: const TextStyle(color: ProofRayColors.quietInk),
                      )
                    else
                      ...selected.sources.map(
                        (ProofSource source) => _SourceCard(source: source),
                      ),
                  ],
                ),
        ),
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.title, this.onClose});

  final String title;
  final VoidCallback? onClose;

  @override
  Widget build(BuildContext context) => Row(
    children: <Widget>[
      Expanded(
        child: Text(
          title.toUpperCase(),
          style: Theme.of(context).textTheme.labelSmall,
        ),
      ),
      if (onClose != null)
        IconButton(onPressed: onClose, icon: const Icon(Icons.close, size: 18)),
    ],
  );
}

class _LabelValue extends StatelessWidget {
  const _LabelValue({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Row(
    children: <Widget>[
      Text(label, style: Theme.of(context).textTheme.labelSmall),
      const Spacer(),
      Text(value, style: Theme.of(context).textTheme.labelSmall),
    ],
  );
}

class _SourceCard extends StatelessWidget {
  const _SourceCard({required this.source});

  final ProofSource source;

  @override
  Widget build(BuildContext context) => Container(
    margin: const EdgeInsets.only(bottom: 12),
    padding: const EdgeInsets.all(12),
    decoration: const BoxDecoration(
      color: ProofRayColors.paper,
      border: Border.fromBorderSide(BorderSide(color: ProofRayColors.hairline)),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(source.sourceId, style: Theme.of(context).textTheme.labelSmall),
        const SizedBox(height: 8),
        SelectableText(
          source.textDeferred && source.text.isEmpty
              ? (AppStrings.of(context).locale.languageCode == 'pt'
                    ? 'Fonte exata pendente; reabra pela aba Fontes.'
                    : 'Exact source pending; reopen it from the Sources tab.')
              : source.text,
        ),
        const SizedBox(height: 9),
        Text(
          'FactId ${source.factId}',
          style: Theme.of(context).textTheme.labelSmall
              ?.copyWith(color: ProofRayColors.quietInk),
        ),
        if (source.sessionId != null || source.speaker != null) ...<Widget>[
          const SizedBox(height: 5),
          Text(
            '${source.sessionId ?? 'no-session'} · ${source.speaker ?? 'no-speaker'}',
            style: Theme.of(context).textTheme.labelSmall,
          ),
        ],
        if (source.sourceSpan != null) ...<Widget>[
          const SizedBox(height: 5),
          Text(
            'span ${source.sourceSpan!.$1}:${source.sourceSpan!.$2}',
            style: Theme.of(context).textTheme.labelSmall,
          ),
        ],
        if (source.parentSha256.isNotEmpty) ...<Widget>[
          const SizedBox(height: 5),
          SelectableText(
            'sha256 ${source.parentSha256}',
            maxLines: 2,
            style: const TextStyle(fontFamily: 'monospace', fontSize: 9),
          ),
        ],
      ],
    ),
  );
}
