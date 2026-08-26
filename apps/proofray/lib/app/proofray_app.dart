import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_timezone/flutter_timezone.dart';

import '../design/pixel_black_hole.dart';
import '../design/proofray_theme.dart';
import '../features/chat/chat_controller.dart';
import '../features/chat/provider_switcher.dart';
import '../features/local_models/local_model_controller.dart';
import '../features/onboarding/onboarding_screen.dart';
import '../features/shell/proofray_shell.dart';
import '../l10n/app_strings.dart';
import '../models/chat_models.dart';
import '../services/bridge/proofray_bridge.dart';
import '../services/runtime/embedded_python_runtime.dart';
import '../storage/app_database.dart';
import '../storage/conversation_store.dart';
import '../storage/integration_store.dart';

class ProofRayApp extends StatefulWidget {
  const ProofRayApp({super.key});

  @override
  State<ProofRayApp> createState() => _ProofRayAppState();
}

class _ProofRayAppState extends State<ProofRayApp> {
  static const String _profileId = 'local-owner';
  ChatController? _chat;
  LocalModelController? _localModels;
  static const String _localProviderId = 'provider-local-model';
  static const String _providerPreference = 'provider.selected';
  static const String _noProviderChoice = 'none';
  String? _configuredLocalEndpoint;
  final EmbeddedPythonRuntime _runtime = EmbeddedPythonRuntime();
  ProofRayDatabase? _database;
  DriftConversationStore? _store;
  IntegrationStore? _integrations;
  AppKeyStore _keyStore = AppKeyStore();
  // Launch screen: the storage/runtime work below already takes time, so this
  // is a floor on how long the wave is shown, not an added delay on top of it.
  bool _splashHolding = true;
  bool _splashDone = false;
  bool _storageStarting = true;
  String? _storageError;
  bool _runtimeStarting = false;
  String? _runtimeStatus;
  String? _runtimeError;
  String? _localDatabasePath;
  // Everything before a profile exists -- storage gate, passphrase gate and
  // the onboarding tutorial -- renders in English. A returning user's stored
  // locale replaces this during `_initializeStorage`, so this only ever decides
  // the very first launch.
  Locale? _locale = const Locale('en');

  @override
  void initState() {
    super.initState();
    Timer(const Duration(milliseconds: 2400), () {
      if (mounted) setState(() => _splashHolding = false);
    });
    unawaited(_initializeStorage());
  }

  @override
  void dispose() {
    _chat?.dispose();
    _localModels?.removeListener(_syncLocalModelProvider);
    _localModels?.dispose();
    unawaited(_runtime.stop());
    unawaited(_database?.close());
    super.dispose();
  }

  Future<void> _initializeStorage({String? passphrase}) async {
    if (passphrase != null) {
      _keyStore = AppKeyStore(passphraseProvider: () async => passphrase);
    }
    if (mounted) {
      setState(() {
        _storageStarting = true;
        _storageError = null;
      });
    }
    try {
      final ProofRayDatabase database = await openProofRayDatabase(
        keyStore: _keyStore,
      );
      final String databasePath = await proofRayDatabasePath();
      if (!mounted) {
        await database.close();
        return;
      }
      setState(() {
        _database = database;
        _localDatabasePath = databasePath;
        _store = DriftConversationStore(database);
        _integrations = IntegrationStore(database, _keyStore);
        _localModels = LocalModelController(
          integrations: _integrations!,
          bridge: () => _runtime.bridge,
        )..addListener(_syncLocalModelProvider);
        _storageStarting = false;
      });
      final LocalProfile? profile = await _store!.localProfile(_profileId);
      final List<ConversationSummary> conversations = await _store!
          .conversations(_profileId);
      if (profile != null && conversations.isNotEmpty && mounted) {
        _locale = profile.locale == 'pt-BR'
            ? const Locale('pt', 'BR')
            : const Locale('en');
        final ConversationSummary active = conversations.first;
        final List<ChatMessage> initialMessages = await _store!.loadMessages(
          active.id,
        );
        if (!mounted) return;
        final ChatController controller = ChatController(
          conversationId: active.id,
          memoryMode: active.memoryMode,
          store: _store,
          initialMessages: initialMessages,
          initialNextSequence: await _store!.nextSequence(active.id),
          providerSecretLoader: _providerSecret,
        );
        setState(() {
          _chat = controller;
          _runtimeStarting = true;
        });
        try {
          final bridge = await _runtime.start(
            hostRequestHandler: _store!.handleHostRequest,
            profileName: profile.displayName,
            timezone: profile.timezone,
            onStatus: (String stage) {
              if (mounted) setState(() => _runtimeStatus = stage);
            },
          );
          await _restoreIntegrations(bridge, controller);
          controller.attachBridge(bridge);
          unawaited(_localModels?.restore());
        } on Object {
          if (mounted) setState(() => _runtimeError = 'runtime_start_failed');
        } finally {
          if (mounted) setState(() => _runtimeStarting = false);
        }
      }
    } on SecureVaultUnavailable {
      if (mounted) {
        setState(() {
          _storageStarting = false;
          _storageError = 'passphrase_required';
        });
      }
    } on Object {
      if (mounted) {
        setState(() {
          _storageStarting = false;
          // A fallback passphrase may simply be wrong. Return to the unlock
          // gate without ever attempting an unencrypted open. Failures before
          // a passphrase was supplied remain hard storage errors.
          _storageError = passphrase == null
              ? 'encrypted_storage_unavailable'
              : 'passphrase_required';
        });
      }
    }
  }

  Future<void> _finishOnboarding(OnboardingResult result) async {
    final DriftConversationStore? store = _store;
    if (store == null) {
      return;
    }
    const String conversationId = 'local-first-conversation';
    final Locale locale = View.of(context).platformDispatcher.locale;
    final String localeName = result.locale == 'system'
        ? (locale.languageCode == 'pt' ? 'pt-BR' : 'en')
        : result.locale;
    String timezone = result.timezone;
    if (timezone == 'system') {
      try {
        timezone = (await FlutterTimezone.getLocalTimezone()).identifier;
      } on Object {
        // A fabricated local clock is worse than an explicit UTC fallback.
        timezone = 'UTC';
      }
    }
    _locale = localeName == 'pt-BR'
        ? const Locale('pt', 'BR')
        : const Locale('en');
    await store.ensureLocalProfile(
      profileId: _profileId,
      displayName: result.displayName,
      locale: localeName,
      timezone: timezone,
    );
    await store.ensureConversation(
      conversationId: conversationId,
      profileId: _profileId,
      title: localeName == 'pt-BR' ? 'Primeira conversa' : 'First conversation',
      memoryMode: result.memoryMode,
    );
    final List<ChatMessage> initialMessages = await store.loadMessages(
      conversationId,
    );
    if (!mounted) return;
    final ChatController controller = ChatController(
      conversationId: conversationId,
      memoryMode: result.memoryMode,
      store: store,
      providerSecretLoader: _providerSecret,
      initialMessages: initialMessages,
      initialNextSequence: await store.nextSequence(conversationId),
    );
    setState(() {
      _chat = controller;
      _runtimeStarting = true;
      _runtimeError = null;
    });
    try {
      final bridge = await _runtime.start(
        hostRequestHandler: store.handleHostRequest,
        profileName: result.displayName,
        timezone: timezone,
        onStatus: (String stage) {
          if (mounted) setState(() => _runtimeStatus = stage);
        },
      );
      await _restoreIntegrations(bridge, controller);
      if (result.providerKind != null &&
          result.providerEndpoint != null &&
          result.providerModelId != null) {
        final String providerId = 'provider-${result.providerKind}';
        try {
          await bridge.configureProvider(
            providerId: providerId,
            kind: result.providerKind!,
            modelId: result.providerModelId!,
            endpoint: result.providerEndpoint!,
            customModel: result.providerCustomModel,
            toolCallingOverride: result.providerSupportsTools,
          );
          await bridge.testProvider(providerId, secret: result.providerSecret);
          await _integrations!.saveProvider(
            id: providerId,
            kind: result.providerKind!,
            displayName: result.providerKind!.replaceAll('_', ' '),
            modelId: result.providerModelId!,
            endpoint: result.providerEndpoint!,
            customModel: result.providerCustomModel,
            supportsTools: result.providerSupportsTools,
            secret: result.providerSecret,
          );
          controller.setProvider(
            providerId,
            supportsTools: result.providerSupportsTools,
          );
          if (result.memoryMode == MemoryMode.tool &&
              result.providerSupportsTools) {
            controller.setMemoryMode(MemoryMode.tool);
          }
        } on Object {
          controller.setProvider(null);
        }
      } else {
        controller.setProvider(null);
      }
      controller.attachBridge(bridge);
    } on Object {
      if (mounted) {
        setState(() => _runtimeError = 'runtime_start_failed');
      }
    } finally {
      if (mounted) {
        setState(() => _runtimeStarting = false);
      }
    }
  }

  /// Switches the conversation's provider and remembers the choice.
  ///
  /// Selection has to outlive the process: someone who picked OpenAI, or who
  /// deliberately turned the AI off, should not find Gemini selected again the
  /// next time the app opens.
  Future<void> selectProvider(String? providerId, {bool supportsTools = true}) async {
    _chat?.setProvider(providerId, supportsTools: supportsTools);
    await _integrations?.setPreference(
      _providerPreference,
      providerId ?? _noProviderChoice,
    );
    if (mounted) setState(() {});
  }

  /// Points the conversation at llama.cpp once it is actually serving.
  ///
  /// A local model needs no new provider implementation: llama.cpp exposes the
  /// same OpenAI-shaped surface the app already speaks, so this configures the
  /// existing openai_compatible path against the loopback port the runtime
  /// just opened. The endpoint only exists while the model is ready, which is
  /// what keeps a half-loaded model from being selected as a working provider.
  void _syncLocalModelProvider() {
    final LocalModelController? local = _localModels;
    final ChatController? chat = _chat;
    final ProofRayBridge? bridge = _runtime.bridge;
    if (local == null || chat == null || bridge == null) return;
    final String? endpoint = local.endpoint;
    if (endpoint == null) {
      if (chat.providerId == _localProviderId) {
        unawaited(selectProvider(null));
      }
      return;
    }
    if (chat.providerId == _localProviderId &&
        _configuredLocalEndpoint == endpoint) {
      return;
    }
    _configuredLocalEndpoint = endpoint;
    unawaited(() async {
      try {
        await bridge.configureProvider(
          providerId: _localProviderId,
          kind: 'openai_compatible',
          modelId: local.active?.name ?? 'local-model',
          endpoint: endpoint,
          customModel: true,
        );
        // llama.cpp advertises tool calling per model and this app cannot know
        // which build is running, so Tool mode stays off rather than failing
        // mid-conversation.
        unawaited(selectProvider(_localProviderId, supportsTools: false));
      } on Object {
        _configuredLocalEndpoint = null;
      }
    }());
  }

  Future<void> _restoreIntegrations(
    ProofRayBridge bridge,
    ChatController controller,
  ) async {
    final IntegrationStore? integrations = _integrations;
    if (integrations == null) return;
    final Object? keywords = await integrations.preference('memory.keywords');
    if (keywords is List<Object?>) {
      controller.setKeywords(keywords.whereType<String>());
    }
    final List<StoredProvider> providers = await integrations.providers();
    final List<StoredProvider> configured = <StoredProvider>[];
    for (final StoredProvider provider in providers) {
      try {
        await bridge.configureProvider(
          providerId: provider.id,
          kind: provider.kind,
          modelId: provider.modelId,
          endpoint: provider.endpoint,
          customModel: provider.customModel,
          toolCallingOverride: provider.supportsTools,
        );
        configured.add(provider);
      } on Object {
        // A stale optional provider must not block the local deterministic core.
      }
    }
    // Whatever was last chosen, not whatever happens to be first in the table.
    // Restoring the first row is why the app kept coming back on Gemini after
    // someone had deliberately switched away from it.
    final Object? remembered = await integrations.preference(_providerPreference);
    StoredProvider? selected;
    for (final StoredProvider provider in configured) {
      if (provider.id == remembered) {
        selected = provider;
        break;
      }
    }
    if (remembered == _noProviderChoice) {
      // An explicit "no AI" is a choice too, and must survive a restart.
      controller.setProvider(null);
      return;
    }
    selected ??= configured.isEmpty ? null : configured.first;
    controller.setProvider(
      selected?.id,
      supportsTools: selected?.supportsTools ?? true,
    );
    for (final StoredConnector connector in await integrations.connectors()) {
      if (connector.status == 'disconnected') continue;
      try {
        await bridge.configureConnector(
          connectorId: connector.id,
          kind: connector.kind,
          endpoint: connector.endpoint,
          options: connector.options,
        );
      } on Object {
        // Sources are optional; users can repair them in the source wizard.
      }
    }
    unawaited(_recoverConnectorSyncs(bridge));
  }

  Future<String?> _providerSecret(String providerId) async {
    final IntegrationStore? integrations = _integrations;
    if (integrations == null) return null;
    return integrations.providerSecretById(providerId);
  }

  Future<void> _recoverConnectorSyncs(ProofRayBridge bridge) async {
    final IntegrationStore? integrations = _integrations;
    if (integrations == null) return;
    final Map<String, StoredConnector> connectors = <String, StoredConnector>{
      for (final StoredConnector item in await integrations.connectors())
        if (item.status != 'disconnected') item.id: item,
    };
    for (final PendingConnectorSync operation
        in await integrations.pendingConnectorSyncs()) {
      final StoredConnector? connector = connectors[operation.connectorId];
      if (connector == null) continue;
      try {
        final String? secret = await integrations.connectorSecret(connector);
        await bridge.previewConnector(
          operation.connectorId,
          operation.mapping,
          secret: secret,
        );
        final Map<String, Object?> result = await bridge.syncConnector(
          operation.connectorId,
          operation.mapping,
          secret: secret,
          checkpoint: operation.checkpoint,
        );
        final Object? checkpoint = result['checkpoint'];
        if (checkpoint is! Map<String, Object?>) continue;
        await integrations.completeConnectorSync(operation, checkpoint);
      } on Object {
        // The encrypted outbox remains pending for an explicit later retry.
      }
    }
  }

  Future<void> _openConversation(ConversationSummary summary) async {
    final DriftConversationStore? store = _store;
    if (store == null || _chat?.sending == true) return;
    final Object? storedKeywords = await _integrations?.preference(
      'memory.keywords',
    );
    final ChatController controller = ChatController(
      conversationId: summary.id,
      memoryMode: summary.memoryMode,
      store: store,
      initialMessages: await store.loadMessages(summary.id),
      initialNextSequence: await store.nextSequence(summary.id),
      providerSecretLoader: _providerSecret,
      keywords: storedKeywords is List<Object?>
          ? storedKeywords.whereType<String>().toList()
          : const <String>[],
    );
    final ProofRayBridge? bridge = _runtime.bridge;
    if (bridge != null) controller.attachBridge(bridge);
    final ChatController? prior = _chat;
    // The AI provider is a process-wide selection, not per-conversation: the
    // embedded core's ProviderManager already holds the live configuration
    // from startup (_restoreIntegrations) or from the settings screen, and it
    // survives every conversation switch. Only the Dart-side *selection* was
    // being dropped here, so opening any conversation silently fell back to
    // memory-only and someone had to reconnect the provider by hand.
    //
    // Carrying it over from the prior controller is a plain in-memory copy --
    // deliberately NOT a _restoreIntegrations() call, which would re-issue a
    // configureProvider bridge round-trip per provider on every single
    // conversation click for a configuration the core already has.
    if (prior != null && prior.providerId != null) {
      controller.setProvider(
        prior.providerId,
        supportsTools: prior.providerSupportsTools,
      );
    }
    if (!mounted) {
      controller.dispose();
      return;
    }
    setState(() => _chat = controller);
    prior?.dispose();
  }

  Future<void> _newConversation() async {
    final DriftConversationStore? store = _store;
    final ChatController? current = _chat;
    if (store == null || current == null) return;
    final DateTime now = DateTime.now().toUtc();
    final String id = 'conversation_${now.microsecondsSinceEpoch}';
    final String title = 'Conversation ${now.toLocal()}';
    await store.ensureConversation(
      conversationId: id,
      profileId: _profileId,
      title: title,
      memoryMode: current.memoryMode,
    );
    if (!current.sending) {
      await _openConversation(
        ConversationSummary(
          id: id,
          title: title,
          updatedAt: now,
          memoryMode: current.memoryMode,
        ),
      );
      return;
    }

    // A fresh conversation has no historical reads to race with the active
    // exchange. The old controller keeps its bridge/outbox ownership until
    // its terminal event, then releases itself.
    final ChatController controller = ChatController(
      conversationId: id,
      memoryMode: current.memoryMode,
      store: store,
      initialNextSequence: 0,
      providerSecretLoader: _providerSecret,
      keywords: current.keywords,
    );
    controller.setProvider(
      current.providerId,
      supportsTools: current.providerSupportsTools,
    );
    if (!mounted) {
      controller.dispose();
      return;
    }
    setState(() => _chat = controller);
    final ProofRayBridge? bridge = _runtime.bridge;
    if (bridge != null) controller.attachBridge(bridge);
    current.releaseWhenIdle();
  }

  Future<void> _deleteConversation(
    ConversationSummary conversation,
    bool purgeMemory,
  ) async {
    final DriftConversationStore? store = _store;
    if (store == null) return;
    if (_chat?.conversationId == conversation.id && _chat?.sending == true) {
      return;
    }
    if (purgeMemory) {
      final ProofRayBridge? bridge = _runtime.bridge;
      if (bridge == null) return;
      final List<MemoryObservation> observations = await store
          .memoryObservations();
      final List<MemoryObservation> selected = observations
          .where(
            (MemoryObservation item) => item.conversationId == conversation.id,
          )
          .toList();
      if (selected.isNotEmpty) {
        final Map<String, Object?> result = await bridge.purgeMemorySources(
          selected.map((MemoryObservation item) => item.sourceId).toList()
            ..sort(),
        );
        if (result['state'] != 'PURGED') return;
      }
      for (final MemoryObservation observation in selected) {
        await store.markMemoryPurged(observation.messageId);
      }
    }
    await store.deleteConversationHistory(conversation.id);
    if (_chat?.conversationId == conversation.id) {
      await _newConversation();
    } else if (mounted) {
      setState(() {});
    }
  }

  Future<void> _changeLocale(Locale locale) async {
    final DriftConversationStore? store = _store;
    final LocalProfile? profile = await store?.localProfile(_profileId);
    if (store == null || profile == null) return;
    final String localeName = locale.languageCode == 'pt' ? 'pt-BR' : 'en';
    await store.ensureLocalProfile(
      profileId: profile.id,
      displayName: profile.displayName,
      locale: localeName,
      timezone: profile.timezone,
    );
    if (mounted) setState(() => _locale = locale);
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    debugShowCheckedModeBanner: false,
    title: 'ProofRay',
    theme: buildProofRayTheme(),
    locale: _locale,
    supportedLocales: const <Locale>[Locale('en'), Locale('pt', 'BR')],
    localizationsDelegates: GlobalMaterialLocalizations.delegates,
    home: Stack(
      children: <Widget>[
        _home(),
        // The launch screen fades out over its own layer rather than being
        // swapped away, so the app is already composed underneath by the time
        // the black hole finishes dissolving -- no flash of an empty frame
        // between the two.
        IgnorePointer(
          ignoring: !_splashHolding,
          child: AnimatedOpacity(
            opacity: _splashHolding ? 1 : 0,
            duration: const Duration(milliseconds: 650),
            curve: Curves.easeOutCubic,
            // Tearing the animation down mid-fade would freeze it on its last
            // frame; keeping it mounted until the fade completes is what makes
            // the transition read as smooth.
            onEnd: () {
              if (mounted && !_splashHolding) setState(() => _splashDone = true);
            },
            child: _splashDone
                ? const SizedBox.shrink()
                : const KeyedSubtree(
                    key: _LaunchWave.launchKey,
                    child: _LaunchWave(),
                  ),
          ),
        ),
      ],
    ),
  );

  Widget _home() {
    if (_storageStarting) {
      return const _StorageGate(label: 'OPENING ENCRYPTED MEMORY');
    }
    if (_storageError != null) {
      if (_storageError == 'passphrase_required') {
        return _PassphraseGate(
          onUnlock: (String passphrase) {
            unawaited(_initializeStorage(passphrase: passphrase));
          },
        );
      }
      return const _StorageGate(label: 'ENCRYPTED MEMORY UNAVAILABLE');
    }
    if (_chat == null) {
      return OnboardingScreen(
        databasePath: _localDatabasePath ?? 'proofray.db',
        onFinished: (OnboardingResult result) {
          unawaited(_finishOnboarding(result));
        },
      );
    }
    return Stack(
      children: <Widget>[
        ProofRayShell(
          chatController: _chat!,
          localModels: _localModels!,
          onProviderSelected: selectProvider,
          // Built here because selecting a provider is the app's business: it
          // has to reach the bridge, the chat and the stored preference at
          // once, and none of those belong to the composer.
          providerSwitcher: ProviderSwitcher(
            integrations: _integrations!,
            localModels: _localModels!,
            selectedProviderId: _chat!.providerId,
            onSelect: (StoredProvider? provider) async {
              if (provider == null) {
                await _localModels?.unload();
                await selectProvider(null);
                return;
              }
              await selectProvider(
                provider.id,
                supportsTools: provider.supportsTools,
              );
            },
            onSelectLocalModel: (LocalModelEntry model) async {
              // Loading publishes the loopback endpoint, and the listener that
              // watches for it is what actually selects the provider -- so the
              // selection can never point at a model that is not serving yet.
              await _localModels?.load(model);
            },
          ),
          store: _store!,
          integrations: _integrations!,
          profileId: _profileId,
          onOpenConversation: _openConversation,
          onNewConversation: _newConversation,
          onDeleteConversation: _deleteConversation,
          locale: _locale ?? View.of(context).platformDispatcher.locale,
          onLocaleChanged: (Locale locale) {
            unawaited(_changeLocale(locale));
          },
        ),
        if (_runtimeStarting)
          Positioned(
            top: 12,
            right: 12,
            child: _RuntimeStatus(label: _runtimeStatusLabel(_runtimeStatus)),
          ),
        if (_runtimeError != null)
          const Positioned(
            top: 12,
            right: 12,
            child: _RuntimeStatus(label: 'LOCAL CORE UNAVAILABLE'),
          ),
      ],
    );
  }
}

/// Launch screen: a pixelated black hole, drawn in the same monochrome dither
/// as the rest of the app. The wave bands bend into it, so the background and
/// the subject are literally the same field rather than two stacked layers.
class _LaunchWave extends StatelessWidget {
  const _LaunchWave();

  /// Named so a test can wait for the launch screen to finish instead of
  /// tapping through it: a finder still locates a widget that is covered, so
  /// "the button exists" is not the same as "the button can be pressed".
  static const ValueKey<String> launchKey =
      ValueKey<String>('proofray-launch-wave');

  @override
  Widget build(BuildContext context) => const Scaffold(
    backgroundColor: ProofRayColors.paper,
    body: PixelBlackHole(
      foreground: ProofRayColors.ink,
      background: ProofRayColors.paper,
    ),
  );
}

class _StorageGate extends StatelessWidget {
  const _StorageGate({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Image.asset(
              'assets/ProofRay.jpeg',
              width: 72,
              height: 72,
              cacheWidth: 160,
              cacheHeight: 160,
            ),
            const SizedBox(height: 20),
            Text(
              label,
              style: const TextStyle(
                fontFamily: 'monospace',
                fontSize: 10,
                letterSpacing: 0.9,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _PassphraseGate extends StatefulWidget {
  const _PassphraseGate({required this.onUnlock});

  final ValueChanged<String> onUnlock;

  @override
  State<_PassphraseGate> createState() => _PassphraseGateState();
}

class _PassphraseGateState extends State<_PassphraseGate> {
  final TextEditingController _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    return Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 440),
          child: Padding(
            padding: const EdgeInsets.all(28),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                const Icon(Icons.lock_outline, size: 38),
                const SizedBox(height: 20),
                Text(
                  strings.unlockLocalMemory,
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                const SizedBox(height: 10),
                Text(strings.vaultUnavailable),
                const SizedBox(height: 18),
                TextField(
                  controller: _controller,
                  obscureText: true,
                  decoration: InputDecoration(
                    labelText: strings.passphraseLabel,
                  ),
                  onSubmitted: widget.onUnlock,
                ),
                const SizedBox(height: 12),
                FilledButton(
                  onPressed: () => widget.onUnlock(_controller.text),
                  child: Text(strings.unlockWithPbkdf2),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Maps EmbeddedPythonRuntime's internal stage keys to the short, all-caps
/// labels this banner already uses. Diagnostic-only: helps tell "still
/// launching the interpreter" apart from "stuck warming the memory index"
/// instead of a single undifferentiated "STARTING LOCAL CORE" for the whole
/// startup sequence, some stages of which (warmMemory in particular) have no
/// timeout at all today.
String _runtimeStatusLabel(String? stage) => switch (stage) {
  'preparing_bootstrap' => 'PREPARING BOOTSTRAP',
  'launching_embedded_python' => 'LAUNCHING LOCAL CORE',
  'waiting_for_bridge_port' => 'WAITING FOR LOCAL CORE',
  'connecting_bridge' => 'CONNECTING TO LOCAL CORE',
  'warming_memory_index' => 'WARMING MEMORY INDEX',
  'ready' => 'LOCAL CORE READY',
  _ => 'STARTING LOCAL CORE',
};

class _RuntimeStatus extends StatelessWidget {
  const _RuntimeStatus({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) => Material(
    color: ProofRayColors.paper,
    child: DecoratedBox(
      decoration: BoxDecoration(border: Border.all(color: ProofRayColors.ink)),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
        child: Text(
          label,
          style: const TextStyle(
            color: ProofRayColors.ink,
            fontFamily: 'monospace',
            fontSize: 9,
            letterSpacing: 0.8,
          ),
        ),
      ),
    ),
  );
}
