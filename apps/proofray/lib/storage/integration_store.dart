import 'dart:convert';

import 'package:drift/drift.dart';

import 'app_database.dart';

class StoredProvider {
  const StoredProvider({
    required this.id,
    required this.kind,
    required this.displayName,
    required this.modelId,
    required this.endpoint,
    required this.secretHandle,
    required this.customModel,
    required this.supportsTools,
  });

  final String id;
  final String kind;
  final String displayName;
  final String modelId;
  final String endpoint;
  final String? secretHandle;
  final bool customModel;
  final bool supportsTools;
}

class StoredConnector {
  const StoredConnector({
    required this.id,
    required this.kind,
    required this.displayName,
    required this.endpoint,
    required this.secretHandle,
    required this.status,
    required this.options,
  });

  final String id;
  final String kind;
  final String displayName;
  final String endpoint;
  final String? secretHandle;
  final String status;
  final Map<String, Object?> options;
}

class StoredLocalImport {
  const StoredLocalImport({
    required this.id,
    required this.fileName,
    required this.fileSha256,
    required this.totalBytes,
    required this.sourceIds,
    required this.importedAt,
  });

  final String id;
  final String fileName;
  final String fileSha256;
  final int totalBytes;
  final List<String> sourceIds;
  final DateTime importedAt;
}

class PendingConnectorSync {
  const PendingConnectorSync({
    required this.operationId,
    required this.mappingId,
    required this.connectorId,
    required this.mapping,
    this.checkpoint,
  });

  final String operationId;
  final String mappingId;
  final String connectorId;
  final Map<String, Object?> mapping;
  final Map<String, Object?>? checkpoint;
}

class IntegrationStore {
  IntegrationStore(this.database, this.keyStore);

  final ProofRayDatabase database;
  final AppKeyStore keyStore;

  Future<void> saveProvider({
    required String id,
    required String kind,
    required String displayName,
    required String modelId,
    required String endpoint,
    required bool customModel,
    required bool supportsTools,
    String? secret,
  }) async {
    final String? previousHandle = await _secretHandle(
      'provider_configurations',
      id,
    );
    final String? newHandle = secret == null || secret.isEmpty
        ? null
        : 'provider.$id.${DateTime.now().microsecondsSinceEpoch}';
    final String? retainedHandle = newHandle ?? previousHandle;
    if (newHandle != null) await keyStore.storeSecret(newHandle, secret!);
    try {
      await database.customStatement(
        'INSERT INTO provider_configurations '
        '(id, provider_kind, display_name, model_id, endpoint, secret_handle, enabled, '
        'custom_model, supports_tools, model_cache_json, model_cache_expires_at) '
        'VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, NULL, NULL) '
        'ON CONFLICT(id) DO UPDATE SET '
        'provider_kind=excluded.provider_kind, display_name=excluded.display_name, '
        'model_id=excluded.model_id, endpoint=excluded.endpoint, '
        'secret_handle=excluded.secret_handle, enabled=1, '
        'custom_model=excluded.custom_model, supports_tools=excluded.supports_tools, '
        'model_cache_json=NULL, model_cache_expires_at=NULL',
        <Object?>[
          id,
          kind,
          displayName,
          modelId,
          _redactEndpoint(endpoint),
          retainedHandle,
          customModel,
          supportsTools,
        ],
      );
    } on Object {
      if (newHandle != null) await keyStore.deleteSecret(newHandle);
      rethrow;
    }
    if (newHandle != null &&
        previousHandle != null &&
        previousHandle != newHandle) {
      await keyStore.deleteSecret(previousHandle);
    }
  }

  Future<List<StoredProvider>> providers() async {
    final List<QueryRow> rows = await database
        .customSelect(
          'SELECT id, provider_kind, display_name, model_id, endpoint, secret_handle, '
          'custom_model, supports_tools '
          'FROM provider_configurations WHERE enabled=1 ORDER BY display_name',
        )
        .get();
    return <StoredProvider>[
      for (final QueryRow row in rows)
        StoredProvider(
          id: row.read<String>('id'),
          kind: row.read<String>('provider_kind'),
          displayName: row.read<String>('display_name'),
          modelId: row.read<String>('model_id'),
          endpoint: row.read<String?>('endpoint') ?? '',
          secretHandle: row.read<String?>('secret_handle'),
          customModel: row.read<int>('custom_model') != 0,
          supportsTools: row.read<int>('supports_tools') != 0,
        ),
    ];
  }

  Future<String?> providerSecret(StoredProvider provider) =>
      provider.secretHandle == null
      ? Future<String?>.value(null)
      : keyStore.readSecret(provider.secretHandle!);

  Future<String?> providerSecretById(String providerId) async {
    final QueryRow? row = await database
        .customSelect(
          'SELECT secret_handle FROM provider_configurations '
          'WHERE id=? AND enabled=1',
          variables: <Variable<Object>>[Variable<String>(providerId)],
        )
        .getSingleOrNull();
    final String? handle = row?.read<String?>('secret_handle');
    return handle == null ? null : keyStore.readSecret(handle);
  }

  Future<void> deleteProvider(StoredProvider provider) async {
    if (provider.secretHandle != null) {
      await keyStore.deleteSecret(provider.secretHandle!);
    }
    await database.customStatement(
      'DELETE FROM provider_configurations WHERE id=?',
      <Object?>[provider.id],
    );
  }

  Future<void> cacheProviderModels(
    String providerId,
    List<Map<String, Object?>> models,
  ) => database.customStatement(
    'UPDATE provider_configurations SET model_cache_json=?, model_cache_expires_at=? '
    'WHERE id=?',
    <Object?>[
      jsonEncode(models),
      DateTime.now().add(const Duration(hours: 24)).millisecondsSinceEpoch,
      providerId,
    ],
  );

  Future<List<Map<String, Object?>>> cachedProviderModels(
    String providerId,
  ) async {
    final QueryRow? row = await database
        .customSelect(
          'SELECT model_cache_json, model_cache_expires_at '
          'FROM provider_configurations WHERE id=?',
          variables: <Variable<Object>>[Variable<String>(providerId)],
        )
        .getSingleOrNull();
    final int? expires = row?.read<int?>('model_cache_expires_at');
    final String? encoded = row?.read<String?>('model_cache_json');
    if (expires == null ||
        encoded == null ||
        expires <= DateTime.now().millisecondsSinceEpoch) {
      return const <Map<String, Object?>>[];
    }
    final Object? decoded = jsonDecode(encoded);
    if (decoded is! List<Object?>) return const <Map<String, Object?>>[];
    return <Map<String, Object?>>[
      for (final Object? item in decoded)
        if (item is Map<Object?, Object?> &&
            item.keys.every((Object? key) => key is String))
          item.map(
            (Object? key, Object? value) => MapEntry(key! as String, value),
          ),
    ];
  }

  Future<void> saveConnector({
    required String id,
    required String kind,
    required String displayName,
    required String endpoint,
    required Map<String, Object?> capabilities,
    Map<String, Object?> options = const <String, Object?>{},
    String? secret,
  }) async {
    validateSecretlessConnectorOptions(options);
    final String? previousHandle = await _secretHandle('connectors', id);
    final String? newHandle = secret == null || secret.isEmpty
        ? null
        : 'connector.$id.${DateTime.now().microsecondsSinceEpoch}';
    final String? retainedHandle = newHandle ?? previousHandle;
    if (newHandle != null) await keyStore.storeSecret(newHandle, secret!);
    try {
      await database.customStatement(
        'INSERT INTO connectors '
        '(id, connector_kind, display_name, redacted_endpoint, secret_handle, '
        'capabilities_json, status, created_at, last_sync_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL) '
        'ON CONFLICT(id) DO UPDATE SET '
        'connector_kind=excluded.connector_kind, display_name=excluded.display_name, '
        'redacted_endpoint=excluded.redacted_endpoint, '
        'secret_handle=excluded.secret_handle, '
        'capabilities_json=excluded.capabilities_json, status=excluded.status',
        <Object?>[
          id,
          kind,
          displayName,
          _redactEndpoint(endpoint),
          retainedHandle,
          jsonEncode(<String, Object?>{
            'capabilities': capabilities,
            'options': options,
          }),
          'configured',
          DateTime.now().millisecondsSinceEpoch,
        ],
      );
    } on Object {
      if (newHandle != null) await keyStore.deleteSecret(newHandle);
      rethrow;
    }
    if (newHandle != null &&
        previousHandle != null &&
        previousHandle != newHandle) {
      await keyStore.deleteSecret(previousHandle);
    }
  }

  Future<List<StoredConnector>> connectors() async {
    final List<QueryRow> rows = await database
        .customSelect(
          'SELECT id, connector_kind, display_name, redacted_endpoint, secret_handle, '
          'status, capabilities_json '
          'FROM connectors ORDER BY display_name',
        )
        .get();
    return <StoredConnector>[
      for (final QueryRow row in rows)
        StoredConnector(
          id: row.read<String>('id'),
          kind: row.read<String>('connector_kind'),
          displayName: row.read<String>('display_name'),
          endpoint: row.read<String>('redacted_endpoint'),
          secretHandle: row.read<String?>('secret_handle'),
          status: row.read<String>('status'),
          options: _optionsFromJson(row.read<String>('capabilities_json')),
        ),
    ];
  }

  Future<void> markConnectorDisconnected(String connectorId) =>
      database.customStatement(
        'UPDATE connectors SET status=\'disconnected\' WHERE id=?',
        <Object?>[connectorId],
      );

  Future<void> markConnectorConnected(String connectorId) =>
      database.customStatement(
        'UPDATE connectors SET status=\'configured\' WHERE id=?',
        <Object?>[connectorId],
      );

  Future<String?> connectorSecret(StoredConnector connector) =>
      connector.secretHandle == null
      ? Future<String?>.value(null)
      : keyStore.readSecret(connector.secretHandle!);

  Future<void> saveMapping({
    required String id,
    required String connectorId,
    required String namespace,
    required Map<String, Object?> mapping,
    bool managed = false,
  }) => database.customStatement(
    'INSERT INTO connector_mappings '
    '(id, connector_id, namespace, mapping_json, managed_namespace, mirror_deletes) '
    'VALUES (?, ?, ?, ?, ?, 0) ON CONFLICT(id) DO UPDATE SET '
    'connector_id=excluded.connector_id, namespace=excluded.namespace, '
    'mapping_json=excluded.mapping_json, '
    'managed_namespace=excluded.managed_namespace',
    <Object?>[id, connectorId, namespace, jsonEncode(mapping), managed],
  );

  Future<Map<String, Object?>?> checkpoint(String mappingId) async {
    final QueryRow? row = await database
        .customSelect(
          'SELECT cursor_json FROM connector_checkpoints WHERE mapping_id=?',
          variables: <Variable<Object>>[Variable<String>(mappingId)],
        )
        .getSingleOrNull();
    if (row == null) {
      return null;
    }
    return (jsonDecode(row.read<String>('cursor_json'))
            as Map<Object?, Object?>)
        .map((Object? key, Object? value) => MapEntry(key! as String, value));
  }

  Future<void> commitCheckpoint(
    String mappingId,
    Map<String, Object?> checkpoint,
  ) => database.customStatement(
    'INSERT OR REPLACE INTO connector_checkpoints '
    '(mapping_id, cursor_json, source_digest, committed_at) VALUES (?, ?, ?, ?)',
    <Object?>[
      mappingId,
      jsonEncode(checkpoint),
      '',
      DateTime.now().millisecondsSinceEpoch,
    ],
  );

  Future<void> stageConnectorSync({
    required String mappingId,
    required String connectorId,
    required Map<String, Object?> mapping,
    Map<String, Object?>? checkpoint,
  }) async {
    final String operationId = 'connector-sync:$mappingId';
    final Uint8List payload = Uint8List.fromList(
      utf8.encode(
        jsonEncode(<String, Object?>{
          'schema': 'proofray.app.outbox.connector-sync.v1',
          'mapping_id': mappingId,
          'connector_id': connectorId,
          'mapping': mapping,
          'checkpoint': ?checkpoint,
        }),
      ),
    );
    await database.customStatement(
      'INSERT INTO outbox_operations '
      '(id, operation_kind, payload, state, attempts, created_at, updated_at) '
      'VALUES (?, ?, ?, ?, 0, ?, ?) ON CONFLICT(id) DO UPDATE SET '
      'payload=excluded.payload, state=\'pending\', '
      'attempts=outbox_operations.attempts+1, updated_at=excluded.updated_at',
      <Object?>[
        operationId,
        'connector.sync',
        payload,
        'pending',
        DateTime.now().toUtc().millisecondsSinceEpoch,
        DateTime.now().toUtc().millisecondsSinceEpoch,
      ],
    );
  }

  Future<List<PendingConnectorSync>> pendingConnectorSyncs() async {
    final List<QueryRow> rows = await database
        .customSelect(
          "SELECT id, payload FROM outbox_operations "
          "WHERE operation_kind='connector.sync' AND state='pending' "
          'ORDER BY created_at, id',
        )
        .get();
    final List<PendingConnectorSync> result = <PendingConnectorSync>[];
    for (final QueryRow row in rows) {
      final Object? decoded = jsonDecode(
        utf8.decode(row.read<Uint8List>('payload'), allowMalformed: false),
      );
      if (decoded is! Map<Object?, Object?> ||
          decoded['schema'] != 'proofray.app.outbox.connector-sync.v1' ||
          decoded['mapping_id'] is! String ||
          decoded['connector_id'] is! String ||
          decoded['mapping'] is! Map<Object?, Object?> ||
          (decoded['checkpoint'] != null &&
              decoded['checkpoint'] is! Map<Object?, Object?>)) {
        throw StateError('invalid encrypted connector sync outbox operation');
      }
      Map<String, Object?> stringMap(Object? value) =>
          (value! as Map<Object?, Object?>).map((Object? key, Object? item) {
            if (key is! String) {
              throw StateError('connector sync outbox keys must be strings');
            }
            return MapEntry<String, Object?>(key, item);
          });
      result.add(
        PendingConnectorSync(
          operationId: row.read<String>('id'),
          mappingId: decoded['mapping_id']! as String,
          connectorId: decoded['connector_id']! as String,
          mapping: stringMap(decoded['mapping']),
          checkpoint: decoded['checkpoint'] == null
              ? null
              : stringMap(decoded['checkpoint']),
        ),
      );
    }
    return result;
  }

  Future<void> completeConnectorSync(
    PendingConnectorSync operation,
    Map<String, Object?> checkpoint,
  ) async {
    await database.transaction(() async {
      await commitCheckpoint(operation.mappingId, checkpoint);
      await database.customStatement(
        'DELETE FROM outbox_operations WHERE id=?',
        <Object?>[operation.operationId],
      );
      await database.customStatement(
        'UPDATE connectors SET last_sync_at=? WHERE id=?',
        <Object?>[
          DateTime.now().toUtc().millisecondsSinceEpoch,
          operation.connectorId,
        ],
      );
    });
  }

  Future<void> saveLocalImports(List<StoredLocalImport> imports) async {
    await database.transaction(() async {
      for (final StoredLocalImport item in imports) {
        await database.customStatement(
          'INSERT OR REPLACE INTO local_imports '
          '(id, file_name, file_sha256, total_bytes, source_ids_json, imported_at) '
          'VALUES (?, ?, ?, ?, ?, ?)',
          <Object?>[
            item.id,
            item.fileName,
            item.fileSha256,
            item.totalBytes,
            jsonEncode(item.sourceIds),
            item.importedAt.millisecondsSinceEpoch,
          ],
        );
      }
    });
  }

  Future<List<StoredLocalImport>> localImports() async {
    final List<QueryRow> rows = await database
        .customSelect(
          'SELECT id, file_name, file_sha256, total_bytes, source_ids_json, imported_at '
          'FROM local_imports ORDER BY imported_at DESC',
        )
        .get();
    return <StoredLocalImport>[
      for (final QueryRow row in rows)
        StoredLocalImport(
          id: row.read<String>('id'),
          fileName: row.read<String>('file_name'),
          fileSha256: row.read<String>('file_sha256'),
          totalBytes: row.read<int>('total_bytes'),
          sourceIds: (jsonDecode(
            row.read<String>('source_ids_json'),
          ) as List<Object?>).whereType<String>().toList(growable: false),
          importedAt: DateTime.fromMillisecondsSinceEpoch(
            row.read<int>('imported_at'),
            isUtc: true,
          ),
        ),
    ];
  }

  Future<void> deleteLocalImport(String id) => database.customStatement(
    'DELETE FROM local_imports WHERE id=?',
    <Object?>[id],
  );

  Future<void> setPreference(
    String key,
    Object? value,
  ) => database.customStatement(
    'INSERT OR REPLACE INTO app_preferences (key, value_json) VALUES (?, ?)',
    <Object?>[key, jsonEncode(value)],
  );

  Future<Object?> preference(String key) async {
    final QueryRow? row = await database
        .customSelect(
          'SELECT value_json FROM app_preferences WHERE key=?',
          variables: <Variable<Object>>[Variable<String>(key)],
        )
        .getSingleOrNull();
    return row == null ? null : jsonDecode(row.read<String>('value_json'));
  }

  Future<String?> _secretHandle(String table, String id) async {
    if (table != 'provider_configurations' && table != 'connectors') {
      throw ArgumentError('unknown secret-handle table');
    }
    final QueryRow? row = await database
        .customSelect(
          'SELECT secret_handle FROM $table WHERE id=?',
          variables: <Variable<Object>>[Variable<String>(id)],
        )
        .getSingleOrNull();
    return row?.read<String?>('secret_handle');
  }

  String _redactEndpoint(String endpoint) {
    if (RegExp(r'^[A-Za-z]:[\\/]').hasMatch(endpoint)) {
      return endpoint;
    }
    final Uri? uri = Uri.tryParse(endpoint);
    if (uri == null || !uri.hasScheme) {
      return endpoint;
    }
    final Map<String, List<String>> safeQuery = <String, List<String>>{
      for (final MapEntry<String, List<String>> entry
          in uri.queryParametersAll.entries)
        if (!RegExp(
          r'(token|secret|password|api.?key|credential)',
          caseSensitive: false,
        ).hasMatch(entry.key))
          entry.key: entry.value,
    };
    return Uri(
      scheme: uri.scheme,
      userInfo: uri.userInfo.isEmpty ? '' : uri.userInfo.split(':').first,
      host: uri.host,
      port: uri.hasPort ? uri.port : null,
      path: uri.path,
      queryParameters: safeQuery.isEmpty ? null : safeQuery,
    ).toString();
  }

  Map<String, Object?> _optionsFromJson(String value) {
    final Object? decoded = jsonDecode(value);
    if (decoded is! Map<Object?, Object?> ||
        decoded['options'] is! Map<Object?, Object?>) {
      return const <String, Object?>{};
    }
    return (decoded['options']! as Map<Object?, Object?>).map(
      (Object? key, Object? item) => MapEntry(key! as String, item),
    );
  }
}

void validateSecretlessConnectorOptions(Map<String, Object?> options) {
  final RegExp sensitive = RegExp(
    r'(token|secret|password|passphrase|api.?key|credential|access.?key)',
    caseSensitive: false,
  );
  void inspect(Object? value) {
    if (value is Map<Object?, Object?>) {
      for (final MapEntry<Object?, Object?> entry in value.entries) {
        final Object? key = entry.key;
        if (key is! String || sensitive.hasMatch(key)) {
          throw ArgumentError('connector options cannot contain credentials');
        }
        inspect(entry.value);
      }
      return;
    }
    if (value is List<Object?>) {
      for (final Object? item in value) {
        inspect(item);
      }
      return;
    }
    if (value is String) {
      final Uri? uri = Uri.tryParse(value);
      if (uri != null &&
          uri.hasScheme &&
          (uri.userInfo.contains(':') ||
              uri.queryParameters.keys.any(sensitive.hasMatch))) {
        throw ArgumentError('connector options cannot contain credential URIs');
      }
    }
  }

  inspect(options);
}
