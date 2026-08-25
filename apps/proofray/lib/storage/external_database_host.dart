import 'dart:isolate';
import 'dart:typed_data';

import 'package:proofray_duckdb/proofray_duckdb.dart';
import 'package:sqlite3/sqlite3.dart';

class ExternalDatabaseHost {
  const ExternalDatabaseHost();

  Future<Map<String, Object?>> handle(
    String method,
    Map<String, Object?> payload,
  ) {
    if (method.startsWith('connector.sqlite.')) {
      return Isolate.run<Map<String, Object?>>(
        () => _handleSqlite(method, payload),
      );
    }
    if (method.startsWith('connector.duckdb.')) {
      return _handleDuckDb(method, payload);
    }
    throw StateError('unknown external database host method');
  }

  Future<Map<String, Object?>> _handleDuckDb(
    String method,
    Map<String, Object?> payload,
  ) async {
    final String path = _databasePath(payload['endpoint']);
    const ProofRayDuckDbHost host = ProofRayDuckDbHost();
    switch (method) {
      case 'connector.duckdb.test':
        await host.test(path);
        return <String, Object?>{'reachable': true};
      case 'connector.duckdb.discover':
        return <String, Object?>{'namespaces': await host.discover(path)};
      case 'connector.duckdb.sample':
        return <String, Object?>{
          'rows': await host.sample(
            path,
            payload['namespace']! as String,
            payload['limit']! as int,
          ),
        };
      case 'connector.duckdb.page':
        final Map<Object?, Object?> checkpoint =
            payload['checkpoint']! as Map<Object?, Object?>;
        return host.page(
          path,
          namespace: payload['namespace']! as String,
          idField: payload['id_field']! as String,
          offset: checkpoint['offset'] as int? ?? 0,
          limit: payload['limit']! as int,
        );
      case 'connector.duckdb.managed_create':
        if (!_managedWrite(payload)) {
          throw StateError('managed write was not authorized');
        }
        return <String, Object?>{
          'namespace': await host.createManagedNamespace(path),
        };
      default:
        throw StateError('unknown DuckDB operation');
    }
  }
}

Map<String, Object?> _handleSqlite(
  String method,
  Map<String, Object?> payload,
) {
  final String path = _databasePath(payload['endpoint']);
  final bool write = method == 'connector.sqlite.managed_create';
  if (write && !_managedWrite(payload)) {
    throw StateError('managed write was not authorized');
  }
  final Database database = sqlite3.open(
    path,
    mode: write ? OpenMode.readWriteCreate : OpenMode.readOnly,
  );
  try {
    final Object? secret = payload['secret'];
    if (secret is String && secret.isNotEmpty) {
      if (secret.runes.any((int rune) => rune < 32)) {
        throw ArgumentError('SQLite key contains control characters');
      }
      database.execute("PRAGMA key = '${secret.replaceAll("'", "''")}'");
    }
    switch (method) {
      case 'connector.sqlite.test':
        database.select('SELECT 1');
        return <String, Object?>{'reachable': true};
      case 'connector.sqlite.discover':
        return <String, Object?>{'namespaces': _sqliteNamespaces(database)};
      case 'connector.sqlite.sample':
        return <String, Object?>{
          'rows': _sqliteRows(
            database,
            payload['namespace']! as String,
            limit: payload['limit']! as int,
          ),
        };
      case 'connector.sqlite.page':
        final Map<Object?, Object?> checkpoint =
            payload['checkpoint']! as Map<Object?, Object?>;
        final int offset = checkpoint['offset'] as int? ?? 0;
        final int limit = payload['limit']! as int;
        final List<Map<String, Object?>> rows = _sqliteRows(
          database,
          payload['namespace']! as String,
          idField: payload['id_field']! as String,
          offset: offset,
          limit: limit,
        );
        return <String, Object?>{
          'rows': rows,
          'checkpoint': <String, Object?>{'offset': offset + rows.length},
          'complete': rows.length < limit,
        };
      case 'connector.sqlite.managed_create':
        database.execute(
          'CREATE TABLE IF NOT EXISTS proofray_memory ('
          'id TEXT PRIMARY KEY, text TEXT NOT NULL, source TEXT NOT NULL, '
          'session_id TEXT, sequence INTEGER, event_time INTEGER, role TEXT, '
          'speaker TEXT, version INTEGER NOT NULL, sha256 TEXT NOT NULL, '
          'created_at TEXT NOT NULL)',
        );
        return <String, Object?>{'namespace': 'proofray_memory'};
      default:
        throw StateError('unknown SQLite operation');
    }
  } finally {
    database.close();
  }
}

List<Map<String, Object?>> _sqliteNamespaces(Database database) {
  final ResultSet tables = database.select(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
    'ORDER BY name',
  );
  return <Map<String, Object?>>[
    for (final Row table in tables)
      _sqliteNamespace(database, table['name']! as String),
  ];
}

Map<String, Object?> _sqliteNamespace(Database database, String table) {
  final String quoted = _quote(table);
  final ResultSet columns = database.select('PRAGMA table_info($quoted)');
  return <String, Object?>{
    'identity': table,
    'display_name': table,
    'fields': <String>[for (final Row row in columns) row['name']! as String],
    'primary_keys': <String>[
      for (final Row row in columns)
        if ((row['pk'] as int? ?? 0) > 0) row['name']! as String,
    ],
    'estimated_rows': null,
  };
}

List<Map<String, Object?>> _sqliteRows(
  Database database,
  String table, {
  String? idField,
  int offset = 0,
  required int limit,
}) {
  if (limit < 1 || limit > 2048 || offset < 0) {
    throw ArgumentError('invalid page');
  }
  final String order = idField == null ? '' : ' ORDER BY ${_quote(idField)}';
  final ResultSet rows = database.select(
    'SELECT * FROM ${_quote(table)}$order LIMIT $limit OFFSET $offset',
  );
  return <Map<String, Object?>>[
    for (final Row row in rows)
      <String, Object?>{
        for (final String column in rows.columnNames)
          column: _jsonSafe(row[column]),
      },
  ];
}

Object? _jsonSafe(Object? value) {
  if (value == null || value is String || value is num || value is bool) {
    return value;
  }
  if (value is Uint8List) {
    return <String, Object?>{
      '__proofray_transport_scalar_v1__': 'bytes',
      'value': _hex(value),
    };
  }
  if (value is BigInt) {
    return <String, Object?>{
      '__proofray_transport_scalar_v1__': 'decimal',
      'value': value.toString(),
    };
  }
  if (value is DateTime) {
    return <String, Object?>{
      '__proofray_transport_scalar_v1__': 'datetime',
      'value': value.toIso8601String(),
    };
  }
  throw UnsupportedError('SQLite returned an unsupported scalar type');
}

String _hex(Uint8List value) =>
    value.map((int byte) => byte.toRadixString(16).padLeft(2, '0')).join();

bool _managedWrite(Map<String, Object?> payload) {
  final Object? options = payload['options'];
  return options is Map<Object?, Object?> && options['managed_write'] == true;
}

String _databasePath(Object? endpoint) {
  if (endpoint is! String || endpoint.isEmpty) {
    throw ArgumentError('database path required');
  }
  if (RegExp(r'^[A-Za-z]:[\\/]').hasMatch(endpoint)) return endpoint;
  final Uri? uri = Uri.tryParse(endpoint);
  if (uri != null && uri.hasScheme && uri.scheme != 'file') {
    if (uri.scheme != 'sqlite' &&
        uri.scheme != 'sqlite3' &&
        uri.scheme != 'duckdb') {
      throw ArgumentError('unsupported host database scheme');
    }
    final String decoded = Uri.decodeComponent(uri.path);
    return RegExp(r'^/[A-Za-z]:/').hasMatch(decoded)
        ? decoded.substring(1)
        : decoded;
  }
  return uri?.scheme == 'file' ? uri!.toFilePath() : endpoint;
}

String _quote(String value) {
  if (value.isEmpty ||
      value.length > 128 ||
      value.runes.any((int rune) => rune < 32)) {
    throw ArgumentError.value(value);
  }
  return '"${value.replaceAll('"', '""')}"';
}
