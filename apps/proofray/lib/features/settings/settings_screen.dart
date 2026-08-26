import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_timezone/flutter_timezone.dart';

import '../../design/proofray_theme.dart';
import '../local_models/local_model_controller.dart';
import '../local_models/local_model_panel.dart';
import 'model_id_field.dart';
import '../../features/chat/chat_controller.dart';
import '../../l10n/app_strings.dart';
import '../../models/chat_models.dart';
import '../../services/bridge/proofray_bridge.dart';
import '../../storage/integration_store.dart';
import '../../storage/conversation_store.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    required this.integrations,
    required this.bridge,
    required this.localModels,
    required this.onProviderSelected,
    required this.chat,
    required this.currentLocale,
    required this.onLocaleChanged,
    required this.store,
    required this.profileId,
    super.key,
  });

  final IntegrationStore integrations;
  final ProofRayBridge? Function() bridge;
  final LocalModelController localModels;
  /// Routed through the app so the choice is remembered, rather than
  /// living only in the controller of the current conversation.
  final Future<void> Function(String? providerId, {bool supportsTools})
  onProviderSelected;
  final ChatController chat;
  final Locale currentLocale;
  final ValueChanged<Locale> onLocaleChanged;
  final ConversationStore store;
  final String profileId;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  static const Map<String, String> _endpoints = <String, String>{
    'gemini': 'https://generativelanguage.googleapis.com/v1beta',
    'openai': 'https://api.openai.com/v1',
    'anthropic': 'https://api.anthropic.com/v1',
    'openai_compatible': 'http://127.0.0.1:11434/v1',
  };

  final TextEditingController _endpoint = TextEditingController();
  final TextEditingController _secret = TextEditingController();
  final TextEditingController _model = TextEditingController();
  final TextEditingController _keywords = TextEditingController();
  final TextEditingController _displayName = TextEditingController();
  final TextEditingController _timezone = TextEditingController();
  String _kind = 'gemini';
  bool _customModel = false;
  bool _supportsTools = true;
  bool _busy = false;
  String? _status;
  List<Map<String, Object?>> _models = const <Map<String, Object?>>[];
  late Future<List<StoredProvider>> _stored;

  String _tr(String pt, String en) =>
      widget.currentLocale.languageCode == 'pt' ? pt : en;

  @override
  void initState() {
    super.initState();
    _endpoint.text = _endpoints[_kind]!;
    _stored = widget.integrations.providers();
    _keywords.text = widget.chat.keywords.isEmpty
        ? 'lembra, recordar, remember, recall'
        : widget.chat.keywords.join(', ');
    unawaited(_loadProfile());
    unawaited(_loadCachedProvider());
  }

  @override
  void dispose() {
    _endpoint.dispose();
    _secret.dispose();
    _model.dispose();
    _keywords.dispose();
    _displayName.dispose();
    _timezone.dispose();
    super.dispose();
  }

  Future<void> _loadProfile() async {
    final LocalProfile? profile = await widget.store.localProfile(
      widget.profileId,
    );
    if (!mounted || profile == null) return;
    setState(() {
      _displayName.text = profile.displayName;
      _timezone.text = profile.timezone;
    });
  }

  Future<void> _loadCachedProvider() async {
    final List<StoredProvider> providers = await widget.integrations
        .providers();
    if (providers.isEmpty) return;
    final StoredProvider provider = providers.first;
    final List<Map<String, Object?>> models = await widget.integrations
        .cachedProviderModels(provider.id);
    if (!mounted) return;
    setState(() {
      _kind = provider.kind;
      _endpoint.text = provider.endpoint;
      _model.text = provider.modelId;
      _customModel = provider.customModel;
      _supportsTools = provider.supportsTools;
      _models = models;
    });
  }

  Future<void> _openProvider(StoredProvider provider) async {
    final List<Map<String, Object?>> models = await widget.integrations
        .cachedProviderModels(provider.id);
    if (!mounted) return;
    setState(() {
      _kind = provider.kind;
      _endpoint.text = provider.endpoint;
      _model.text = provider.modelId;
      _secret.clear();
      _customModel = provider.customModel;
      _supportsTools = provider.supportsTools;
      _models = models;
      unawaited(widget.onProviderSelected(
        provider.id,
        supportsTools: provider.supportsTools,
      ));
    });
  }

  Future<void> _deleteProvider(StoredProvider provider) async {
    final bool pt = widget.currentLocale.languageCode == 'pt';
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: Text(pt ? 'Esquecer este provedor?' : 'Forget this provider?'),
        content: Text(
          pt
              ? 'A configuração e a chave serão removidas deste dispositivo.'
              : 'The configuration and key will be removed from this device.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(pt ? 'Cancelar' : 'Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(pt ? 'Esquecer' : 'Forget'),
          ),
        ],
      ),
    );
    if (!mounted || confirmed != true) return;
    final ProofRayBridge? bridge = widget.bridge();
    if (bridge != null) {
      try {
        await bridge.removeProvider(provider.id);
      } on Object {
        // The durable config/key deletion below remains authoritative.
      }
    }
    await widget.integrations.deleteProvider(provider);
    if (widget.chat.providerId == provider.id) {
      widget.chat.setProvider(null);
    }
    if (!mounted) return;
    setState(() {
      _stored = widget.integrations.providers();
      _secret.clear();
      _status = _tr('Provedor e chave removidos.', 'Provider and key removed.');
    });
  }

  Future<void> _saveProfile() async {
    final String displayName = _displayName.text.trim();
    final String timezone = _timezone.text.trim();
    if (displayName.isEmpty ||
        timezone.isEmpty ||
        !RegExp(r'^[A-Za-z_+-]+(?:/[A-Za-z0-9_+-]+)+$|^UTC$')
            .hasMatch(timezone)) {
      setState(
        () => _status = _tr(
          'Perfil local ou fuso IANA inválido.',
          'Invalid local profile or IANA timezone.',
        ),
      );
      return;
    }
    try {
      if (timezone != 'UTC') {
        final zones = await FlutterTimezone.getAvailableTimezones();
        if (!zones.any((zone) => zone.identifier == timezone)) {
          throw const FormatException('unknown IANA timezone');
        }
      }
    } on Object {
      if (!mounted) return;
      setState(
        () => _status = _tr(
          'Fuso IANA desconhecido; o perfil não foi alterado.',
          'Unknown IANA timezone; profile was not changed.',
        ),
      );
      return;
    }
    try {
      await widget.store.ensureLocalProfile(
        profileId: widget.profileId,
        displayName: displayName,
        locale: widget.currentLocale.languageCode == 'pt' ? 'pt-BR' : 'en',
        timezone: timezone,
      );
      final ProofRayBridge? bridge = widget.bridge();
      bool appliedLive = false;
      if (bridge != null) {
        try {
          await bridge.updateLocalProfile(
            profileName: displayName,
            timezoneName: timezone,
          );
          appliedLive = true;
        } on Object {
          appliedLive = false;
        }
      }
      if (!mounted) return;
      setState(
        () => _status = appliedLive
            ? _tr(
                'Perfil local aplicado às próximas observações.',
                'Local profile applied to future observations.',
              )
            : _tr(
                'Perfil salvo; será aplicado na próxima abertura.',
                'Profile saved; it will apply on the next launch.',
              ),
      );
    } on Object {
      if (!mounted) return;
      setState(
        () => _status = _tr(
          'Não foi possível salvar o perfil local.',
          'The local profile could not be saved.',
        ),
      );
    }
  }

  Future<void> _saveKeywords() async {
    final List<String> values = _keywords.text.split(',');
    widget.chat.setKeywords(values);
    await widget.integrations.setPreference(
      'memory.keywords',
      widget.chat.keywords,
    );
    if (!mounted) return;
    setState(
      () => _status = _tr(
        'Gatilho determinístico salvo localmente.',
        'Deterministic keyword trigger saved locally.',
      ),
    );
  }

  /// The key for the provider being edited: what was typed, or what the vault
  /// already holds. The secret field is cleared after every successful save, so
  /// treating "empty field" as "no key" would break every action taken after
  /// the first one -- which is exactly how model discovery came back empty.
  Future<String?> _resolveSecret() async {
    final String typed = _secret.text.trim();
    if (typed.isNotEmpty) return typed;
    for (final StoredProvider item in await widget.integrations.providers()) {
      if (item.id == 'provider-$_kind') {
        return widget.integrations.providerSecret(item);
      }
    }
    return null;
  }

  Future<void> _connect() async {
    final ProofRayBridge? bridge = widget.bridge();
    if (bridge == null || _model.text.trim().isEmpty) {
      setState(
        () => _status = _tr(
          'Core local ou ID do modelo indisponível.',
          'Local core or model ID unavailable.',
        ),
      );
      return;
    }
    setState(() {
      _busy = true;
      _status = null;
    });
    final String providerId = 'provider-$_kind';
    try {
      final String? secretLease = await _resolveSecret();
      final String configuredModel = _model.text.trim();
      final bool selectedToolSupport = _supportsTools;
      await bridge.configureProvider(
        providerId: providerId,
        kind: _kind,
        modelId: configuredModel,
        endpoint: _endpoint.text.trim(),
        customModel: _customModel,
        toolCallingOverride: selectedToolSupport,
      );
      await bridge.testProvider(providerId, secret: secretLease);
      await widget.integrations.saveProvider(
        id: providerId,
        kind: _kind,
        displayName: _kind.replaceAll('_', ' '),
        modelId: configuredModel,
        endpoint: _endpoint.text.trim(),
        customModel: _customModel,
        supportsTools: selectedToolSupport,
        secret: secretLease,
      );
      if (_models.isNotEmpty) {
        await widget.integrations.cacheProviderModels(providerId, _models);
      }
      await widget.onProviderSelected(
        providerId,
        supportsTools: selectedToolSupport,
      );
      if (!mounted) return;
      setState(() {
        _stored = widget.integrations.providers();
        _secret.clear();
        _status = _tr(
          'Conectado. A chave está no cofre; sem cofre, vive somente nesta sessão.',
          'Connected. The key is in the vault, or session-only when no vault exists.',
        );
      });
    } on Object catch (error) {
      if (mounted) {
        final String reason = error is ProofRayBridgeException
            ? error.code
            : error.runtimeType.toString();
        setState(
          () => _status = _tr(
            'Conexão rejeitada ($reason). Verifique endpoint, modelo e chave.',
            'Connection rejected ($reason). Check endpoint, model and key.',
          ),
        );
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    return ListView(
      padding: const EdgeInsets.all(24),
      children: <Widget>[
        Text(
          strings.settings,
          style: Theme.of(context).textTheme.headlineSmall,
        ),
        const SizedBox(height: 16),
        DropdownButtonFormField<String>(
          initialValue: widget.currentLocale.languageCode,
          decoration: InputDecoration(labelText: strings.interfaceLanguage),
          items: const <DropdownMenuItem<String>>[
            DropdownMenuItem(value: 'pt', child: Text('Português (Brasil)')),
            DropdownMenuItem(value: 'en', child: Text('English')),
          ],
          onChanged: (String? value) {
            if (value != null) {
              widget.onLocaleChanged(
                value == 'pt' ? const Locale('pt', 'BR') : const Locale('en'),
              );
            }
          },
        ),
        const SizedBox(height: 12),
        Text(
          strings.localProfile,
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 10),
        TextField(
          controller: _displayName,
          decoration: InputDecoration(labelText: strings.displayName),
        ),
        const SizedBox(height: 10),
        TextField(
          controller: _timezone,
          decoration: InputDecoration(labelText: strings.timezone),
        ),
        const SizedBox(height: 10),
        CompactAction(
          child: OutlinedButton(
            onPressed: _saveProfile,
            child: Text(strings.saveProfile),
          ),
        ),
        const Divider(height: 40),
        Text(
          strings.aiProvider,
          style: Theme.of(context).textTheme.headlineSmall,
        ),
        const SizedBox(height: 8),
        Text(
          strings.aiProviderExplanation,
          style: const TextStyle(color: ProofRayColors.quietInk),
        ),
        const SizedBox(height: 24),
        DropdownButtonFormField<String>(
          initialValue: _kind,
          decoration: InputDecoration(labelText: strings.provider),
          items: const <DropdownMenuItem<String>>[
            DropdownMenuItem(value: 'gemini', child: Text('Gemini')),
            DropdownMenuItem(value: 'openai', child: Text('OpenAI')),
            DropdownMenuItem(value: 'anthropic', child: Text('Anthropic')),
            DropdownMenuItem(
              value: 'openai_compatible',
              child: Text('OpenAI compatible / Ollama / LM Studio'),
            ),
            DropdownMenuItem(value: 'local', child: Text('Local model')),
          ],
          onChanged: (String? value) {
            if (value == null) return;
            setState(() {
              _kind = value;
              _endpoint.text = _endpoints[value] ?? '';
            });
          },
        ),
        const SizedBox(height: 12),
        if (_kind == 'local') ...<Widget>[
          LocalModelPanel(
            controller: widget.localModels,
            bridge: widget.bridge,
          ),
        ] else ...<Widget>[
        TextField(
          controller: _endpoint,
          decoration: const InputDecoration(labelText: 'Endpoint'),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _secret,
          obscureText: true,
          enableSuggestions: false,
          autocorrect: false,
          decoration: InputDecoration(labelText: strings.apiKeyVaultOnly),
        ),
        const SizedBox(height: 12),
        ModelIdField(
          controller: _model,
          kind: _kind,
          endpoint: () => _endpoint.text.trim(),
          bridge: widget.bridge,
          secret: _resolveSecret,
          initialModels: _models,
          onModelsDiscovered: (List<Map<String, Object?>> models) =>
              setState(() => _models = models),
          custom: _customModel,
          onCustomChanged: (bool value) =>
              setState(() => _customModel = value),
          onToolSupportDiscovered: (bool supported) => setState(() {
            _supportsTools = supported;
            if (!supported && widget.chat.memoryMode == MemoryMode.tool) {
              widget.chat.setMemoryMode(MemoryMode.keywords);
            }
          }),
          label: strings.modelId,
          customLabel: strings.customModel,
          customExplanation: strings.customModelExplanation,
        ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: Text(
            strings.locale.languageCode == 'pt'
                ? 'O modelo suporta tool calling'
                : 'Model supports tool calling',
          ),
          subtitle: Text(
            strings.locale.languageCode == 'pt'
                ? 'Desative para impedir fisicamente o modo Tool neste modelo.'
                : 'Turn off to physically disable Tool mode for this model.',
          ),
          value: _supportsTools,
          onChanged: (bool value) => setState(() => _supportsTools = value),
        ),
        ],
        if (_kind != 'local')
          Wrap(
            spacing: 10,
            children: <Widget>[
              FilledButton(
                onPressed: _busy ? null : _connect,
                child: Text(strings.testAndSave),
              ),
              TextButton(
                onPressed: () => unawaited(widget.onProviderSelected(null)),
                child: Text(strings.useWithoutAi),
              ),
            ],
          ),
        if (_status != null) ...<Widget>[
          const SizedBox(height: 12),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              border: Border.all(color: ProofRayColors.ink),
              borderRadius: const BorderRadius.all(Radius.circular(2)),
            ),
            child: Text(
              _status!,
              style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
            ),
          ),
        ],
        const Divider(height: 40),
        Text(
          strings.memoryActivation,
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 10),
        TextField(
          controller: _keywords,
          decoration: InputDecoration(
            labelText: strings.keywordsCommaSeparated,
          ),
        ),
        const SizedBox(height: 10),
        CompactAction(
          child: OutlinedButton(
            onPressed: _saveKeywords,
            child: Text(strings.saveDeterministicTrigger),
          ),
        ),
        const Divider(height: 40),
        Text(
          strings.savedLocally,
          style: Theme.of(context).textTheme.titleMedium,
        ),
        FutureBuilder<List<StoredProvider>>(
          future: _stored,
          builder:
              (
                BuildContext context,
                AsyncSnapshot<List<StoredProvider>> snapshot,
              ) => Column(
                children: <Widget>[
                  for (final StoredProvider provider
                      in snapshot.data ?? const <StoredProvider>[])
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.key_outlined),
                      title: Text(provider.displayName),
                      subtitle: Text(provider.modelId),
                      onTap: () => unawaited(_openProvider(provider)),
                      trailing: IconButton(
                        tooltip: widget.currentLocale.languageCode == 'pt'
                            ? 'Esquecer provedor'
                            : 'Forget provider',
                        onPressed: () => unawaited(_deleteProvider(provider)),
                        icon: const Icon(Icons.delete_outline),
                      ),
                    ),
                ],
              ),
        ),
      ],
    );
  }
}
