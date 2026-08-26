import 'dart:async';

import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../services/bridge/proofray_bridge.dart';

/// Asks a provider which models it actually serves for this key.
///
/// Every shipped provider exposes `GET /models`, so the catalogue comes from
/// the provider itself rather than from a list baked into the app that would
/// silently go stale every time a vendor ships a model. Discovery runs under a
/// throwaway provider id so the configuration being edited is never replaced by
/// a half-finished one; the id is removed again whatever the outcome.
Future<List<Map<String, Object?>>> discoverProviderModels({
  required ProofRayBridge bridge,
  required String kind,
  required String endpoint,
  String? secret,
}) async {
  final String providerId = 'provider-discovery-$kind';
  await bridge.configureProvider(
    providerId: providerId,
    kind: kind,
    modelId: 'model-discovery',
    endpoint: endpoint,
    customModel: true,
  );
  try {
    return await bridge.listProviderModels(providerId, secret: secret);
  } finally {
    try {
      await bridge.removeProvider(providerId);
    } on Object {
      // A leaked discovery id is inert: it holds no secret and is never the
      // provider a conversation talks to.
    }
  }
}

/// Model picker: a list of the provider's own models, or free text on demand.
///
/// Typing a model id from memory is a real barrier -- most people have no idea
/// what their provider currently calls its models. The list removes that. The
/// escape hatch stays because a preview or experimental identifier often is not
/// returned by the catalogue endpoint at all, and refusing to accept one would
/// trade a small barrier for a hard wall.
class ModelIdField extends StatefulWidget {
  const ModelIdField({
    required this.controller,
    required this.kind,
    required this.endpoint,
    required this.custom,
    required this.onCustomChanged,
    super.key,
    this.bridge,
    this.secret,
    this.initialModels = const <Map<String, Object?>>[],
    this.onModelsDiscovered,
    this.onToolSupportDiscovered,
    this.label,
    this.customLabel,
    this.customExplanation,
  });

  final TextEditingController controller;
  final String kind;

  /// Read lazily: the endpoint field is edited on the same screen.
  final String Function() endpoint;
  final bool custom;
  final ValueChanged<bool> onCustomChanged;

  /// Absent during onboarding, where the embedded core has not started yet --
  /// the field then offers free text only, and says why.
  final ProofRayBridge? Function()? bridge;
  /// Async because the key usually is not in the text field: it was saved to
  /// the vault and the field cleared, so resolving it means a store read.
  final Future<String?> Function()? secret;

  /// Previously discovered models, so reopening the screen shows the list
  /// without asking the provider again.
  final List<Map<String, Object?>> initialModels;
  final ValueChanged<List<Map<String, Object?>>>? onModelsDiscovered;
  final ValueChanged<bool>? onToolSupportDiscovered;
  final String? label;
  final String? customLabel;
  final String? customExplanation;

  @override
  State<ModelIdField> createState() => _ModelIdFieldState();
}

class _ModelIdFieldState extends State<ModelIdField> {
  late List<Map<String, Object?>> _models = widget.initialModels;
  bool _busy = false;
  String? _error;
  late String _discoveredFor = widget.initialModels.isEmpty
      ? ''
      : widget.kind;

  bool _autoLoaded = false;

  bool get _pt => Localizations.localeOf(context).languageCode == 'pt';

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // Fetch once, unprompted: someone opening this screen wants to choose a
    // model, and making them press a button first to see any options at all is
    // the barrier this field exists to remove. Only when there is nothing
    // cached and a core to ask.
    if (_autoLoaded || _models.isNotEmpty || widget.bridge?.call() == null) {
      return;
    }
    _autoLoaded = true;
    unawaited(_discover(silent: true));
  }

  String get _label =>
      widget.label ?? (_pt ? 'Modelo' : 'Model');

  Future<void> _discover({bool silent = false}) async {
    final ProofRayBridge? bridge = widget.bridge?.call();
    if (bridge == null) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final List<Map<String, Object?>> found = await discoverProviderModels(
        bridge: bridge,
        kind: widget.kind,
        endpoint: widget.endpoint(),
        secret: await widget.secret?.call(),
      );
      if (!mounted) return;
      widget.onModelsDiscovered?.call(found);
      setState(() {
        _models = found;
        _discoveredFor = widget.kind;
        _error = found.isEmpty && !silent
            ? (_pt
                  ? 'O provedor não retornou nenhum modelo.'
                  : 'The provider returned no models.')
            : null;
      });
    } on ProofRayBridgeException catch (error) {
      // An unprompted attempt that fails stays quiet: there may simply be no
      // key yet, and an error nobody asked for reads as something being broken.
      if (mounted && !silent) setState(() => _error = error.code);
    } on Object catch (error) {
      if (mounted && !silent) setState(() => _error = error.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _select(String? modelId) {
    if (modelId == null) return;
    widget.controller.text = modelId;
    for (final Map<String, Object?> model in _models) {
      if (model['model_id'] == modelId) {
        widget.onToolSupportDiscovered?.call(model['supports_tools'] == true);
        break;
      }
    }
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final bool canDiscover = widget.bridge?.call() != null;
    // A catalogue fetched for one provider says nothing about another.
    final bool fresh = _discoveredFor == widget.kind;
    final List<Map<String, Object?>> models = fresh
        ? _models
        : const <Map<String, Object?>>[];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        // Free text only when it is actually asked for, or when there is
        // nothing to choose from and no way to fetch anything. A cached
        // catalogue is still a catalogue even where discovery cannot run --
        // it just cannot be refreshed.
        if (widget.custom || (models.isEmpty && !canDiscover))
          TextField(
            controller: widget.controller,
            decoration: InputDecoration(
              labelText: _label,
              helperText: canDiscover
                  ? null
                  : (_pt
                        ? 'Opcional agora. A lista de modelos do seu provedor aparece nas Configurações.'
                        : 'Optional for now — your provider’s model list appears in Settings.'),
              helperMaxLines: 2,
            ),
          )
        else if (models.isEmpty)
          InputDecorator(
            decoration: InputDecoration(
              labelText: _label,
              helperText: _pt
                  ? 'Preencha a chave de API e busque os modelos do provedor.'
                  : 'Fill in the API key, then load the provider’s models.',
              helperMaxLines: 2,
            ),
            child: Row(
              children: <Widget>[
                Expanded(
                  child: Text(
                    widget.controller.text.isEmpty
                        ? (_pt ? 'Nenhum modelo escolhido' : 'No model chosen')
                        : widget.controller.text,
                    style: const TextStyle(fontFamily: 'monospace'),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                const SizedBox(width: 8),
                if (_busy)
                  const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 1),
                  )
                else
                  OutlinedButton(
                    onPressed: _discover,
                    child: Text(_pt ? 'Buscar modelos' : 'Load models'),
                  ),
              ],
            ),
          )
        else
          Row(
            children: <Widget>[
              Expanded(
                child: DropdownButtonFormField<String>(
                  initialValue: models.any(
                        (Map<String, Object?> item) =>
                            item['model_id'] == widget.controller.text,
                      )
                      ? widget.controller.text
                      : null,
                  isExpanded: true,
                  decoration: InputDecoration(labelText: _label),
                  items: <DropdownMenuItem<String>>[
                    for (final Map<String, Object?> model in models)
                      DropdownMenuItem<String>(
                        value: model['model_id'] as String?,
                        child: Text(
                          model['display_name'] as String? ??
                              model['model_id'] as String? ??
                              '',
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                  ],
                  onChanged: _select,
                ),
              ),
              if (canDiscover)
                IconButton(
                  tooltip: _pt ? 'Buscar de novo' : 'Reload',
                  onPressed: _busy ? null : _discover,
                  icon: const Icon(Icons.refresh, size: 18),
                ),
            ],
          ),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text(
              _error!,
              style: const TextStyle(
                fontFamily: 'monospace',
                fontSize: 11,
                color: ProofRayColors.ink,
              ),
            ),
          ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: Text(
            widget.customLabel ??
                (_pt
                    ? 'ID personalizado ou experimental'
                    : 'Custom / newer model ID'),
          ),
          subtitle: widget.customExplanation == null
              ? null
              : Text(widget.customExplanation!),
          value: widget.custom,
          onChanged: widget.onCustomChanged,
        ),
      ],
    );
  }
}
