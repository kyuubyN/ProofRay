import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../l10n/app_strings.dart';
import '../../services/bridge/proofray_bridge.dart';
import '../../storage/integration_store.dart';
import '../../storage/conversation_store.dart';

class SourcesScreen extends StatefulWidget {
  const SourcesScreen({
    required this.integrations,
    required this.bridge,
    required this.currentLocale,
    required this.store,
    super.key,
  });

  final IntegrationStore integrations;
  final ProofRayBridge? Function() bridge;
  final Locale currentLocale;
  final ConversationStore store;

  @override
  State<SourcesScreen> createState() => _SourcesScreenState();
}

class _SourcesScreenState extends State<SourcesScreen> {
  static const List<String> _kinds = <String>[
    'sqlite',
    'duckdb',
    'mongodb',
    'postgresql',
    'mysql',
    'redis',
    'dynamodb',
    'elasticsearch',
    'spacetimedb',
  ];

  final TextEditingController _endpoint = TextEditingController();
  final TextEditingController _secret = TextEditingController();
  final TextEditingController _idField = TextEditingController();
  final TextEditingController _textField = TextEditingController();
  final TextEditingController _sourceField = TextEditingController();
  final TextEditingController _sessionField = TextEditingController();
  final TextEditingController _sequenceField = TextEditingController();
  final TextEditingController _eventTimeField = TextEditingController();
  final TextEditingController _roleField = TextEditingController();
  final TextEditingController _speakerField = TextEditingController();
  final TextEditingController _versionField = TextEditingController();
  final TextEditingController _scopeId = TextEditingController(text: '1');
  final TextEditingController _options = TextEditingController(text: '{}');
  String? _kind;
  String? _connectorId;
  List<Map<String, Object?>> _namespaces = const <Map<String, Object?>>[];
  Map<String, Object?>? _namespace;
  Map<String, Object?>? _mapping;
  String? _preview;
  String? _status;
  bool _busy = false;
  bool _managedNamespaceAvailable = false;
  late Future<List<StoredConnector>> _stored;
  late Future<List<VerifiedSourceRecord>> _verified;
  late Future<List<StoredLocalImport>> _localImports;

  String _tr(String pt, String en) =>
      widget.currentLocale.languageCode == 'pt' ? pt : en;

  @override
  void initState() {
    super.initState();
    _stored = widget.integrations.connectors();
    _verified = widget.store.verifiedSources();
    _localImports = widget.integrations.localImports();
  }

  @override
  void dispose() {
    _endpoint.dispose();
    _secret.dispose();
    _idField.dispose();
    _textField.dispose();
    _sourceField.dispose();
    _sessionField.dispose();
    _sequenceField.dispose();
    _eventTimeField.dispose();
    _roleField.dispose();
    _speakerField.dispose();
    _versionField.dispose();
    _scopeId.dispose();
    _options.dispose();
    super.dispose();
  }

  Future<void> _detectAndConnect() async {
    final ProofRayBridge? bridge = widget.bridge();
    if (bridge == null || _endpoint.text.trim().isEmpty) return;
    setState(() => _busy = true);
    try {
      final String? secretLease = _secret.text.isEmpty ? null : _secret.text;
      final Map<String, Object?> detected = await bridge.detectConnector(
        _endpoint.text.trim(),
      );
      _kind = detected['kind'] as String? ?? _kind;
      if (_kind == null) {
        setState(
          () => _status = _tr(
            'HTTP é ambíguo. Selecione explicitamente o tipo da fonte.',
            'HTTP is ambiguous. Select the source type explicitly.',
          ),
        );
        return;
      }
      final String connectorId =
          'connector_${DateTime.now().microsecondsSinceEpoch}';
      final Object? decodedOptions = jsonDecode(_options.text.trim());
      if (decodedOptions is! Map<Object?, Object?>) {
        throw const FormatException('connector options must be an object');
      }
      final Map<String, Object?> options = decodedOptions.map(
        (Object? key, Object? value) => MapEntry(key! as String, value),
      );
      validateSecretlessConnectorOptions(options);
      final Object? rawCapabilities = detected['capabilities'];
      final bool managedNamespaceAvailable =
          rawCapabilities is Map<Object?, Object?> &&
              rawCapabilities['managed_namespace'] == true ||
          (rawCapabilities == null && _kind != 'spacetimedb');
      await bridge.configureConnector(
        connectorId: connectorId,
        kind: _kind!,
        endpoint: _endpoint.text.trim(),
        options: options,
      );
      await bridge.testConnector(connectorId, secret: secretLease);
      _namespaces = await bridge.connectorNamespaces(
        connectorId,
        secret: secretLease,
      );
      await widget.integrations.saveConnector(
        id: connectorId,
        kind: _kind!,
        displayName: _kind!,
        endpoint: _endpoint.text.trim(),
        capabilities: detected['capabilities'] is Map<String, Object?>
            ? detected['capabilities']! as Map<String, Object?>
            : const <String, Object?>{},
        options: options,
        secret: secretLease,
      );
      if (!mounted) return;
      setState(() {
        _connectorId = connectorId;
        _secret.clear();
        _managedNamespaceAvailable = managedNamespaceAvailable;
        _stored = widget.integrations.connectors();
        _status = _tr(
          'Fonte somente leitura conectada. Selecione um namespace.',
          'Read-only source connected. Select a namespace.',
        );
      });
    } on Object {
      if (mounted) {
        setState(
          () => _status = _tr(
            'Conexão rejeitada sem importar dados.',
            'Connection rejected without importing data.',
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<String?> _connectorSecretLease() async {
    if (_secret.text.isNotEmpty) return _secret.text;
    final String? connectorId = _connectorId;
    if (connectorId == null) return null;
    final List<StoredConnector> stored = await widget.integrations.connectors();
    StoredConnector? matching;
    for (final StoredConnector item in stored) {
      if (item.id == connectorId) {
        matching = item;
        break;
      }
    }
    return matching == null
        ? null
        : widget.integrations.connectorSecret(matching);
  }

  Future<void> _selectNamespace(Map<String, Object?> namespace) async {
    final ProofRayBridge? bridge = widget.bridge();
    if (bridge == null) return;
    final Map<String, Object?> suggestion = await bridge
        .suggestConnectorMapping(namespace);
    if (!mounted) return;
    setState(() {
      _namespace = namespace;
      final String suggestedId = suggestion['id_field'] as String? ?? '';
      _idField.text = _kind == 'elasticsearch' && suggestedId == '_id'
          ? ''
          : suggestedId;
      _textField.text = suggestion['text_field'] as String? ?? '';
      _sourceField.text = suggestion['source_field'] as String? ?? '';
      _sessionField.text = suggestion['session_field'] as String? ?? '';
      _sequenceField.text = suggestion['sequence_field'] as String? ?? '';
      _eventTimeField.text = suggestion['event_time_field'] as String? ?? '';
      _roleField.text = suggestion['role_field'] as String? ?? '';
      _speakerField.text = suggestion['speaker_field'] as String? ?? '';
      _versionField.text = suggestion['version_field'] as String? ?? '';
      _status = _kind == 'elasticsearch' && suggestedId == '_id'
          ? _tr(
              'Escolha um campo keyword único e ordenável; _id não oferece checkpoint incremental seguro.',
              'Choose a unique sortable keyword field; _id cannot provide a safe incremental checkpoint.',
            )
          : suggestion['state'] == 'suggested'
          ? _tr(
              'Mapping sugerido. Revise antes da prévia.',
              'Mapping suggested. Review before preview.',
            )
          : _tr(
              'Escolha manualmente os campos de ID e texto.',
              'Choose ID and text fields manually.',
            );
    });
  }

  Map<String, Object?> _buildMapping() {
    final int? scopeId = int.tryParse(_scopeId.text.trim());
    if (scopeId == null || scopeId < 0 || scopeId >= (1 << 32)) {
      throw const FormatException(
        'scope ID must be an unsigned 32-bit integer',
      );
    }
    final Map<String, Object?> mapping = <String, Object?>{
      'namespace': _namespace?['identity'],
      'id_field': _idField.text.trim(),
      'text_field': _textField.text.trim(),
      'scope_id': scopeId,
    };
    _putOptional(mapping, 'source_field', _sourceField);
    _putOptional(mapping, 'session_field', _sessionField);
    _putOptional(mapping, 'sequence_field', _sequenceField);
    _putOptional(mapping, 'event_time_field', _eventTimeField);
    _putOptional(mapping, 'role_field', _roleField);
    _putOptional(mapping, 'speaker_field', _speakerField);
    _putOptional(mapping, 'version_field', _versionField);
    return mapping;
  }

  void _putOptional(
    Map<String, Object?> mapping,
    String key,
    TextEditingController controller,
  ) {
    final String value = controller.text.trim();
    if (value.isNotEmpty) mapping[key] = value;
  }

  Future<void> _createManagedNamespace() async {
    final ProofRayBridge? bridge = widget.bridge();
    final String? connectorId = _connectorId;
    if (bridge == null || connectorId == null || !_managedNamespaceAvailable) {
      return;
    }
    final AppStrings strings = AppStrings.of(context);
    final bool pt = strings.locale.languageCode == 'pt';
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: Text(
          pt
              ? 'Criar um namespace dedicado ao ProofRay?'
              : 'Create a dedicated ProofRay namespace?',
        ),
        content: Text(
          pt
              ? 'Esta é a única operação de escrita oferecida para uma fonte externa. O ProofRay nunca escreverá em um namespace existente.'
              : 'This is the only write operation offered for an external source. ProofRay will never write into an existing namespace.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(strings.cancel),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(strings.createManagedNamespace),
          ),
        ],
      ),
    );
    if (!mounted) return;
    if (confirmed != true) return;
    setState(() => _busy = true);
    try {
      final String namespace = await bridge.createManagedConnectorNamespace(
        connectorId,
        secret: await _connectorSecretLease(),
      );
      _namespaces = await bridge.connectorNamespaces(
        connectorId,
        secret: await _connectorSecretLease(),
      );
      if (!mounted) return;
      setState(
        () => _status = _tr(
          'Namespace dedicado $namespace criado. Os existentes não foram alterados.',
          'Dedicated namespace $namespace created. Existing namespaces were untouched.',
        ),
      );
    } on Object {
      if (mounted) {
        setState(
          () => _status = _tr(
            'A criação do namespace gerenciado falhou de modo fechado.',
            'Managed namespace creation failed closed.',
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _previewMapping() async {
    final ProofRayBridge? bridge = widget.bridge();
    if (bridge == null || _connectorId == null || _namespace == null) return;
    try {
      final Map<String, Object?> mapping = _buildMapping();
      final Map<String, Object?> result = await bridge.previewConnector(
        _connectorId!,
        mapping,
        secret: await _connectorSecretLease(),
      );
      if (!mounted) return;
      setState(() {
        _mapping = mapping;
        _preview = const JsonEncoder.withIndent('  ')
            .convert(result['documents']);
        _status = _tr(
          'Apenas prévia. Nada foi importado.',
          'Preview only. Nothing has been imported yet.',
        );
      });
    } on Object {
      if (mounted) {
        setState(
          () => _status = _tr(
            'A prévia do mapping falhou de modo fechado.',
            'Mapping preview failed closed.',
          ),
        );
      }
    }
  }

  Future<void> _sync() async {
    final ProofRayBridge? bridge = widget.bridge();
    final Map<String, Object?>? mapping = _mapping;
    if (bridge == null || _connectorId == null || mapping == null) return;
    final String mappingId = 'mapping:${_connectorId!}:${mapping['namespace']}';
    setState(() => _busy = true);
    try {
      await widget.integrations.saveMapping(
        id: mappingId,
        connectorId: _connectorId!,
        namespace: mapping['namespace']! as String,
        mapping: mapping,
      );
      final Map<String, Object?>? priorCheckpoint = await widget.integrations
          .checkpoint(mappingId);
      await widget.integrations.stageConnectorSync(
        mappingId: mappingId,
        connectorId: _connectorId!,
        mapping: mapping,
        checkpoint: priorCheckpoint,
      );
      final Map<String, Object?> result = await bridge.syncConnector(
        _connectorId!,
        mapping,
        secret: await _connectorSecretLease(),
        checkpoint: priorCheckpoint,
      );
      final Object? checkpoint = result['checkpoint'];
      if (checkpoint is! Map<String, Object?>) {
        throw const FormatException(
          'connector sync lacks a checkpoint receipt',
        );
      }
      await widget.integrations.completeConnectorSync(
        PendingConnectorSync(
          operationId: 'connector-sync:$mappingId',
          mappingId: mappingId,
          connectorId: _connectorId!,
          mapping: mapping,
          checkpoint: priorCheckpoint,
        ),
        checkpoint,
      );
      if (!mounted) return;
      setState(
        () => _status = _tr(
          '${result['documents_committed']} documentos confirmados.',
          '${result['documents_committed']} documents committed.',
        ),
      );
    } on Object {
      if (mounted) {
        setState(
          () => _status = _tr(
            'Sincronização interrompida. O checkpoint anterior continua autoritativo.',
            'Sync stopped. The previous checkpoint remains authoritative.',
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _importLocalFiles() async {
    final ProofRayBridge? bridge = widget.bridge();
    if (bridge == null) return;
    const XTypeGroup type = XTypeGroup(
      label: 'ProofRay text',
      extensions: <String>['txt', 'md', 'markdown', 'json'],
    );
    final List<XFile> files = await openFiles(
      acceptedTypeGroups: const <XTypeGroup>[type],
    );
    if (files.isEmpty) return;
    setState(() => _busy = true);
    int chunks = 0;
    final List<String> committedSources = <String>[];
    final List<StoredLocalImport> pendingImports = <StoredLocalImport>[];
    try {
      for (final XFile file in files) {
        final List<String> fileSources = <String>[];
        final int length = await file.length();
        if (length > 64 * 1024 * 1024) {
          throw const FormatException('local file exceeds 64 MiB');
        }
        final String digest = await _fileSha256(file);
        if (file.name.toLowerCase().endsWith('.json')) {
          await file
              .openRead()
              .cast<List<int>>()
              .transform(utf8.decoder)
              .transform(json.decoder)
              .drain<void>();
        } else {
          await file
              .openRead()
              .cast<List<int>>()
              .transform(utf8.decoder)
              .drain<void>();
        }
        await for (final _FileChunk chunk in _fileChunks(file)) {
          final String text = utf8.decode(chunk.bytes, allowMalformed: false);
          if (text.trim().isNotEmpty) {
            final Map<String, Object?> result = await bridge.importLocalChunk(
              fileName: file.name,
              fileSha256: digest,
              byteStart: chunk.start,
              byteEnd: chunk.end,
              text: text,
            );
            final Object? sourceId = result['source_id'];
            if (sourceId is! String) {
              throw const FormatException(
                'local import receipt lacks source identity',
              );
            }
            if (result['state'] == 'APPLIED') {
              committedSources.add(sourceId);
            }
            fileSources.add(sourceId);
            chunks++;
          }
        }
        if (fileSources.isNotEmpty) {
          pendingImports.add(
            StoredLocalImport(
              id: 'local:$digest',
              fileName: file.name,
              fileSha256: digest,
              totalBytes: length,
              sourceIds: fileSources.toSet().toList()..sort(),
              importedAt: DateTime.now().toUtc(),
            ),
          );
        }
      }
      await widget.integrations.saveLocalImports(pendingImports);
      if (!mounted) return;
      setState(() {
        _localImports = widget.integrations.localImports();
        _status = _tr(
          '$chunks trechos exatos de arquivos locais importados.',
          '$chunks exact local file chunks imported.',
        );
      });
    } on Object {
      bool rollbackFailed = false;
      if (committedSources.isNotEmpty) {
        try {
          await bridge.purgeMemorySources(
            committedSources.toSet().toList()..sort(),
          );
        } on Object {
          rollbackFailed = true;
        }
      }
      if (mounted) {
        setState(
          () => _status = rollbackFailed
              ? _tr(
                  'A importação falhou e o rollback não foi confirmado; revise a memória antes de repetir.',
                  'Import failed and rollback was not confirmed; inspect memory before retrying.',
                )
              : _tr(
                  'A importação local falhou; novos trechos foram revertidos.',
                  'Local import failed; newly committed chunks were rolled back.',
                ),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _deleteLocalImport(StoredLocalImport item) async {
    final ProofRayBridge? bridge = widget.bridge();
    if (bridge == null) return;
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: Text(_tr('Apagar arquivo importado?', 'Delete imported file?')),
        content: Text(
          _tr(
            'Todos os trechos deste digest serão removidos e o ledger será reencadeado.',
            'Every chunk for this digest will be removed and the ledger will be rechained.',
          ),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(_tr('Cancelar', 'Cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(_tr('Apagar', 'Delete')),
          ),
        ],
      ),
    );
    if (!mounted || confirmed != true) return;
    final Map<String, Object?> result = await bridge.purgeMemorySources(
      item.sourceIds.toSet().toList()..sort(),
    );
    if (result['state'] != 'PURGED' &&
        result['state'] != 'REJECTED_NOT_FOUND') {
      return;
    }
    await widget.integrations.deleteLocalImport(item.id);
    if (!mounted) return;
    setState(() {
      _localImports = widget.integrations.localImports();
      _status = _tr(
        'Arquivo importado removido da memória.',
        'Imported file removed from memory.',
      );
    });
  }

  Future<void> _disconnect(StoredConnector connector) async {
    final ProofRayBridge? bridge = widget.bridge();
    if (bridge == null) return;
    await bridge.removeConnector(connector.id);
    await widget.integrations.markConnectorDisconnected(connector.id);
    if (!mounted) return;
    setState(() {
      _stored = widget.integrations.connectors();
      _status = _tr(
        'Desconectado. Os fatos importados foram mantidos.',
        'Disconnected. Imported facts were retained.',
      );
    });
  }

  Future<void> _openStoredConnector(StoredConnector connector) async {
    final ProofRayBridge? bridge = widget.bridge();
    if (bridge == null) return;
    await bridge.configureConnector(
      connectorId: connector.id,
      kind: connector.kind,
      endpoint: connector.endpoint,
      options: connector.options,
    );
    final List<Map<String, Object?>> namespaces = await bridge
        .connectorNamespaces(
          connector.id,
          secret: await widget.integrations.connectorSecret(connector),
        );
    await widget.integrations.markConnectorConnected(connector.id);
    if (!mounted) return;
    setState(() {
      _connectorId = connector.id;
      _kind = connector.kind;
      _endpoint.text = connector.endpoint;
      _secret.clear();
      _options.text = const JsonEncoder.withIndent('  ')
          .convert(connector.options);
      _namespaces = namespaces;
      _stored = widget.integrations.connectors();
      _managedNamespaceAvailable = connector.kind != 'spacetimedb';
      _status = _tr(
        'Conector reaberto com credencial temporária limitada à chamada.',
        'Stored connector reopened with a call-scoped credential lease.',
      );
    });
  }

  Future<void> _deleteImportedMemory(StoredConnector connector) async {
    final ProofRayBridge? bridge = widget.bridge();
    if (bridge == null) return;
    final AppStrings strings = AppStrings.of(context);
    final bool pt = strings.locale.languageCode == 'pt';
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: Text(
          pt ? 'Apagar a memória importada?' : 'Delete imported memory?',
        ),
        content: Text(
          pt
              ? 'Isso é separado de desconectar e reencadeia o ledger de prova ativo.'
              : 'This is separate from disconnecting and rechains the active proof ledger.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(strings.cancel),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(pt ? 'Apagar' : 'Delete'),
          ),
        ],
      ),
    );
    if (!mounted) return;
    if (confirmed != true) return;
    final Map<String, Object?> result = await bridge.purgeMemorySourcePrefix(
      'connector:${connector.id}:',
    );
    if (!mounted) return;
    setState(
      () => _status = result['state'] == 'PURGED'
          ? _tr(
              'Fatos importados apagados e memória restante revalidada.',
              'Imported facts deleted and remaining memory revalidated.',
            )
          : _tr(
              'Nenhum fato importado foi encontrado.',
              'No imported facts were found.',
            ),
    );
  }

  Future<void> _openVerifiedSource(VerifiedSourceRecord source) async {
    String text = source.text;
    String digest = source.parentSha256;
    if (text.isEmpty) {
      final ProofRayBridge? bridge = widget.bridge();
      if (bridge != null) {
        try {
          final Map<String, Object?> reopened = await bridge.getMemorySource(
            factId: source.factId,
            sourceId: source.sourceId,
          );
          final String candidate = reopened['text'] as String? ?? '';
          final String candidateDigest =
              reopened['parent_sha256'] as String? ?? '';
          if (candidate.isNotEmpty &&
              sha256.convert(utf8.encode(candidate)).toString() ==
                  candidateDigest) {
            text = candidate;
            digest = candidateDigest;
          }
        } on Object {
          // The dialog below reports an unavailable exact source without substitution.
        }
      }
    }
    if (!mounted) return;
    await showDialog<void>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: SelectableText(source.sourceId),
        content: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 620),
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                SelectableText(
                  text.isEmpty
                      ? _tr(
                          'Fonte exata temporariamente indisponível.',
                          'Exact source temporarily unavailable.',
                        )
                      : text,
                ),
                const SizedBox(height: 14),
                SelectableText(
                  'FactId ${source.factId}\n'
                  'sha256 $digest\n'
                  '${source.sessionId ?? 'no-session'} · '
                  '${source.speaker ?? 'no-speaker'}',
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 10),
                ),
              ],
            ),
          ),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(_tr('Fechar', 'Close')),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    final bool pt = strings.locale.languageCode == 'pt';
    return ListView(
      padding: const EdgeInsets.all(24),
      children: <Widget>[
        Text(strings.sources, style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 8),
        Text(
          strings.sourcesIntroduction,
          style: const TextStyle(color: ProofRayColors.quietInk),
        ),
        const SizedBox(height: 22),
        Text(
          _tr('Fontes verificadas recentemente', 'Recently verified sources'),
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 8),
        FutureBuilder<List<VerifiedSourceRecord>>(
          future: _verified,
          builder:
              (
                BuildContext context,
                AsyncSnapshot<List<VerifiedSourceRecord>> snapshot,
              ) {
                final List<VerifiedSourceRecord> sources =
                    snapshot.data ?? const <VerifiedSourceRecord>[];
                if (snapshot.connectionState != ConnectionState.done) {
                  return const LinearProgressIndicator(minHeight: 1);
                }
                if (sources.isEmpty) {
                  return Text(
                    _tr(
                      'Nenhuma fonte foi usada em uma resposta ainda.',
                      'No source has been used in an answer yet.',
                    ),
                    style: const TextStyle(color: ProofRayColors.quietInk),
                  );
                }
                return Column(
                  children: <Widget>[
                    for (final VerifiedSourceRecord source in sources.take(20))
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(Icons.verified_outlined),
                        title: Text(
                          source.text.isEmpty ? source.sourceId : source.text,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                        subtitle: Text(
                          source.sourceId,
                          style: const TextStyle(
                            fontFamily: 'monospace',
                            fontSize: 9,
                          ),
                        ),
                        onTap: () => unawaited(_openVerifiedSource(source)),
                      ),
                  ],
                );
              },
        ),
        const Divider(height: 38),
        Text(
          _tr('Conectar ou importar', 'Connect or import'),
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 22),
        OutlinedButton.icon(
          onPressed: _busy ? null : _importLocalFiles,
          icon: const Icon(Icons.file_open_outlined),
          label: Text(strings.importTextFiles),
        ),
        FutureBuilder<List<StoredLocalImport>>(
          future: _localImports,
          builder:
              (
                BuildContext context,
                AsyncSnapshot<List<StoredLocalImport>> snapshot,
              ) => Column(
                children: <Widget>[
                  for (final StoredLocalImport item
                      in snapshot.data ?? const <StoredLocalImport>[])
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.description_outlined),
                      title: Text(item.fileName),
                      subtitle: Text(
                        '${item.totalBytes} bytes · ${item.fileSha256.substring(0, 12)}…',
                        style: const TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 9,
                        ),
                      ),
                      trailing: IconButton(
                        tooltip: _tr(
                          'Apagar memória importada',
                          'Delete imported memory',
                        ),
                        onPressed: _busy
                            ? null
                            : () => unawaited(_deleteLocalImport(item)),
                        icon: const Icon(Icons.delete_outline),
                      ),
                    ),
                ],
              ),
        ),
        const SizedBox(height: 18),
        TextField(
          controller: _endpoint,
          decoration: InputDecoration(labelText: strings.databaseEndpoint),
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<String>(
          initialValue: _kind,
          decoration: InputDecoration(labelText: strings.sourceType),
          items: <DropdownMenuItem<String>>[
            for (final String kind in _kinds)
              DropdownMenuItem<String>(value: kind, child: Text(kind)),
          ],
          onChanged: (String? value) => setState(() {
            _kind = value;
            if (value == 'duckdb') _secret.clear();
          }),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _secret,
          enabled: _kind != 'duckdb',
          obscureText: true,
          decoration: InputDecoration(labelText: strings.credentialLease),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _options,
          minLines: 1,
          maxLines: 4,
          decoration: InputDecoration(labelText: strings.connectorOptions),
        ),
        const SizedBox(height: 12),
        CompactAction(
          child: FilledButton(
            onPressed: _busy ? null : _detectAndConnect,
            child: Text(strings.detectTestDiscover),
          ),
        ),
        if (_namespaces.isNotEmpty) ...<Widget>[
          const Divider(height: 38),
          Text(
            strings.namespaces,
            style: Theme.of(context).textTheme.titleMedium,
          ),
          for (final Map<String, Object?> namespace in _namespaces)
            ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(namespace['display_name'] as String? ?? ''),
              subtitle: Text(
                (namespace['fields'] as List<Object?>? ?? const <Object?>[])
                    .join(' · '),
              ),
              onTap: () => _selectNamespace(namespace),
            ),
        ],
        if (_namespace != null) ...<Widget>[
          const SizedBox(height: 16),
          _MappingFields(
            fields: <(String, TextEditingController)>[
              (pt ? 'Campo de ID *' : 'ID field *', _idField),
              (pt ? 'Campo de texto *' : 'Text field *', _textField),
              (pt ? 'Campo de fonte' : 'Source field', _sourceField),
              (
                pt ? 'Campo de sessão / conversa' : 'Session / thread field',
                _sessionField,
              ),
              (pt ? 'Campo de sequência' : 'Sequence field', _sequenceField),
              (
                pt
                    ? 'Campo de data / tempo do evento'
                    : 'Date / event time field',
                _eventTimeField,
              ),
              (pt ? 'Campo de papel' : 'Role field', _roleField),
              (pt ? 'Campo de pessoa' : 'Speaker field', _speakerField),
              (pt ? 'Campo de versão' : 'Version field', _versionField),
              ('Scope ID', _scopeId),
            ],
          ),
          const SizedBox(height: 12),
          CompactAction(
            child: OutlinedButton(
              onPressed: _previewMapping,
              child: Text(strings.generatePreview),
            ),
          ),
        ],
        if (_preview != null) ...<Widget>[
          const SizedBox(height: 16),
          Container(
            constraints: const BoxConstraints(maxHeight: 260),
            padding: const EdgeInsets.all(12),
            decoration: const BoxDecoration(
              border: Border.fromBorderSide(
                BorderSide(color: ProofRayColors.hairline),
              ),
            ),
            child: SingleChildScrollView(
              child: SelectableText(
                _preview!,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 10),
              ),
            ),
          ),
          const SizedBox(height: 12),
          CompactAction(
            child: FilledButton(
              onPressed: _busy ? null : _sync,
              child: Text(strings.importAuthorizedMapping),
            ),
          ),
          if (_connectorId != null && _managedNamespaceAvailable) ...<Widget>[
            const SizedBox(height: 8),
            OutlinedButton(
              onPressed: _busy ? null : _createManagedNamespace,
              child: Text(strings.createManagedNamespace),
            ),
          ],
        ],
        if (_status != null) ...<Widget>[
          const SizedBox(height: 14),
          Text(
            _status!,
            style: const TextStyle(fontFamily: 'monospace', fontSize: 11),
          ),
        ],
        const Divider(height: 40),
        FutureBuilder<List<StoredConnector>>(
          future: _stored,
          builder:
              (
                BuildContext context,
                AsyncSnapshot<List<StoredConnector>> snapshot,
              ) => Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    strings.configuredSources,
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  for (final StoredConnector connector
                      in snapshot.data ?? const <StoredConnector>[])
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.storage_outlined),
                      title: Text(connector.displayName),
                      subtitle: Text(
                        '${connector.kind} · ${connector.endpoint}',
                      ),
                      onTap: () => unawaited(_openStoredConnector(connector)),
                      trailing: PopupMenuButton<String>(
                        onSelected: (String action) {
                          if (action == 'disconnect') {
                            unawaited(_disconnect(connector));
                          } else {
                            unawaited(_deleteImportedMemory(connector));
                          }
                        },
                        itemBuilder: (BuildContext context) =>
                            <PopupMenuEntry<String>>[
                              PopupMenuItem(
                                value: 'disconnect',
                                child: Text(strings.disconnectOnly),
                              ),
                              PopupMenuItem(
                                value: 'delete',
                                child: Text(strings.deleteImportedMemory),
                              ),
                            ],
                      ),
                    ),
                ],
              ),
        ),
      ],
    );
  }
}

class _MappingFields extends StatelessWidget {
  const _MappingFields({required this.fields});

  final List<(String, TextEditingController)> fields;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (BuildContext context, BoxConstraints constraints) {
      final int columns = constraints.maxWidth >= 720
          ? 3
          : constraints.maxWidth >= 440
          ? 2
          : 1;
      final double width =
          (constraints.maxWidth - (columns - 1) * 10) / columns;
      return Wrap(
        spacing: 10,
        runSpacing: 10,
        children: <Widget>[
          for (final (String label, TextEditingController controller) in fields)
            SizedBox(
              width: width,
              child: TextField(
                controller: controller,
                decoration: InputDecoration(labelText: label),
              ),
            ),
        ],
      );
    },
  );
}

class _DigestSink implements Sink<Digest> {
  Digest? value;

  @override
  void add(Digest data) => value = data;

  @override
  void close() {}
}

Future<String> _fileSha256(XFile file) async {
  final _DigestSink output = _DigestSink();
  final ByteConversionSink input = sha256.startChunkedConversion(output);
  await for (final List<int> bytes in file.openRead()) {
    input.add(bytes);
  }
  input.close();
  final Digest? digest = output.value;
  if (digest == null) {
    throw const FormatException('file digest was not produced');
  }
  return digest.toString();
}

class _FileChunk {
  const _FileChunk(this.start, this.end, this.bytes);

  final int start;
  final int end;
  final Uint8List bytes;
}

Stream<_FileChunk> _fileChunks(XFile file) async* {
  const int limit = 128 * 1024;
  final List<int> pending = <int>[];
  int start = 0;
  await for (final List<int> incoming in file.openRead()) {
    pending.addAll(incoming);
    while (pending.length > limit) {
      int end = limit;
      while (end > 0 && (pending[end] & 0xC0) == 0x80) {
        end--;
      }
      if (end == 0) throw const FormatException('UTF-8 chunk boundary failed');
      final Uint8List bytes = Uint8List.fromList(pending.sublist(0, end));
      yield _FileChunk(start, start + end, bytes);
      pending.removeRange(0, end);
      start += end;
    }
  }
  if (pending.isNotEmpty) {
    yield _FileChunk(
      start,
      start + pending.length,
      Uint8List.fromList(pending),
    );
  }
}
