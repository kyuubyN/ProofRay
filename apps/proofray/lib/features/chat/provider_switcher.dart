import 'dart:async';

import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../storage/integration_store.dart';
import '../local_models/local_model_controller.dart';

/// Marks "no AI" as a real choice.
///
/// It cannot be represented by a null menu value: `PopupMenuButton` treats a
/// null result as the menu being dismissed and never calls `onSelected`, so the
/// item looked present and did nothing when tapped.
@immutable
class _NoProvider {
  const _NoProvider();
}

const _NoProvider _noProvider = _NoProvider();

/// Chooses what answers, from the conversation itself.
///
/// Everything that is actually usable appears in one menu: the API providers
/// already saved to the vault, whichever GGUF files are on disk, and turning
/// the AI off entirely. Nothing is offered that has not been set up, so the
/// menu never promises something that would fail on the next message.
class ProviderSwitcher extends StatefulWidget {
  const ProviderSwitcher({
    required this.integrations,
    required this.localModels,
    required this.selectedProviderId,
    required this.onSelect,
    required this.onSelectLocalModel,
    super.key,
  });

  final IntegrationStore integrations;
  final LocalModelController localModels;
  final String? selectedProviderId;
  final Future<void> Function(StoredProvider? provider) onSelect;
  final Future<void> Function(LocalModelEntry model) onSelectLocalModel;

  @override
  State<ProviderSwitcher> createState() => _ProviderSwitcherState();
}

class _ProviderSwitcherState extends State<ProviderSwitcher> {
  List<StoredProvider> _providers = const <StoredProvider>[];

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void didUpdateWidget(ProviderSwitcher oldWidget) {
    super.didUpdateWidget(oldWidget);
    // A provider saved in settings has to show up here without a restart.
    if (oldWidget.selectedProviderId != widget.selectedProviderId) {
      unawaited(_load());
    }
  }

  Future<void> _load() async {
    try {
      final List<StoredProvider> providers = await widget.integrations
          .providers();
      if (mounted) setState(() => _providers = providers);
    } on Object {
      // Without the vault there is simply nothing to offer; the local models
      // and the off switch still work.
    }
  }

  String _label(String kind) => switch (kind) {
    'gemini' => 'Gemini',
    'openai' => 'OpenAI',
    'anthropic' => 'Anthropic',
    'openai_compatible' => 'OpenAI compatible',
    _ => kind.replaceAll('_', ' '),
  };

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: widget.localModels,
    builder: (BuildContext context, Widget? child) {
      final bool pt = Localizations.localeOf(context).languageCode == 'pt';
      final List<LocalModelEntry> local = widget.localModels.usableModels;
      if (_providers.isEmpty && local.isEmpty) return const SizedBox.shrink();
      final String? activeLocal = widget.localModels.activePath;
      return PopupMenuButton<Object?>(
        key: const ValueKey<String>('provider-switcher'),
        tooltip: pt ? 'Quem responde' : 'What answers',
        enabled: !widget.localModels.busy,
        onSelected: (Object? choice) {
          if (choice is StoredProvider) {
            unawaited(widget.onSelect(choice));
          } else if (choice is LocalModelEntry) {
            unawaited(widget.onSelectLocalModel(choice));
          } else if (choice is _NoProvider) {
            unawaited(widget.onSelect(null));
          }
        },
        itemBuilder: (BuildContext context) => <PopupMenuEntry<Object?>>[
          if (_providers.isNotEmpty) ...<PopupMenuEntry<Object?>>[
            PopupMenuItem<Object?>(
              enabled: false,
              height: 30,
              child: Text(
                pt ? 'PROVEDORES' : 'PROVIDERS',
                style: Theme.of(context).textTheme.labelSmall,
              ),
            ),
            for (final StoredProvider provider in _providers)
              PopupMenuItem<Object?>(
                value: provider,
                child: _row(
                  selected: provider.id == widget.selectedProviderId,
                  title: _label(provider.kind),
                  detail: provider.modelId,
                ),
              ),
          ],
          if (local.isNotEmpty) ...<PopupMenuEntry<Object?>>[
            PopupMenuItem<Object?>(
              enabled: false,
              height: 30,
              child: Text(
                pt ? 'MODELOS LOCAIS' : 'LOCAL MODELS',
                style: Theme.of(context).textTheme.labelSmall,
              ),
            ),
            for (final LocalModelEntry model in local)
              PopupMenuItem<Object?>(
                value: model,
                child: _row(
                  selected: model.path == activeLocal &&
                      widget.localModels.state == 'ready',
                  title: model.name,
                  detail: model.sizeLabel,
                ),
              ),
          ],
          const PopupMenuDivider(),
          PopupMenuItem<Object?>(
            value: _noProvider,
            child: _row(
              selected: widget.selectedProviderId == null,
              title: pt ? 'Sem IA' : 'No AI',
              detail: pt ? 'só memória' : 'memory only',
            ),
          ),
        ],
        icon: Icon(
          widget.selectedProviderId == null
              ? Icons.psychology_outlined
              : activeLocal != null &&
                    widget.localModels.state == 'ready'
              ? Icons.memory
              : Icons.cloud_outlined,
          color: ProofRayColors.ink,
          size: 20,
        ),
      );
    },
  );

  Widget _row({
    required bool selected,
    required String title,
    required String detail,
  }) => Row(
    children: <Widget>[
      if (selected)
        const Icon(Icons.check, size: 16)
      else
        const SizedBox(width: 16),
      const SizedBox(width: 9),
      Flexible(child: Text(title, overflow: TextOverflow.ellipsis)),
      const SizedBox(width: 10),
      Text(
        detail,
        style: const TextStyle(fontFamily: 'monospace', fontSize: 9),
      ),
    ],
  );
}
