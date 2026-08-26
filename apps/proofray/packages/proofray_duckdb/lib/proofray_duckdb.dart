import 'dart:typed_data';
import 'dart:io';

import 'package:dart_duckdb/dart_duckdb.dart';
import 'package:dart_duckdb/open.dart' as duckdb_loader;

class ProofRayDuckDbHost {
  const ProofRayDuckDbHost();

  Future<void> test(String path) async {
    await _withSource(path, (Connection connection) async {
      await _query(connection, 'SELECT 1');
    });
  }

  Future<List<Map<String, Object?>>> discover(String path) =>
      _withSource(path, (Connection connection) async {
        final _Rows columns = await _query(
          connection,
          "SELECT schema_name, table_name, column_name FROM duckdb_columns() "
          "WHERE database_name='proofray_source' "
          'ORDER BY schema_name, table_name, column_index',
        );
        final Map<String, List<String>> grouped = <String, List<String>>{};
        for (final List<Object?> row in columns.rows) {
          final String identity = '${row[0]}.${row[1]}';
          grouped
              .putIfAbsent(identity, () => <String>[])
              .add(row[2]! as String);
        }
        return <Map<String, Object?>>[
          for (final MapEntry<String, List<String>> item in grouped.entries)
            <String, Object?>{
              'identity': item.key,
              'display_name': item.key,
              'fields': item.value,
              'primary_keys': const <String>[],
              'estimated_rows': null,
            },
        ];
      });

  Future<List<Map<String, Object?>>> sample(
    String path,
    String namespace,
    int limit,
  ) =>
      _withSource(path, (Connection connection) async {
        if (limit < 1 || limit > 50) throw ArgumentError.value(limit);
        return (await _query(
          connection,
          'SELECT * FROM ${_qualified(namespace)} LIMIT $limit',
        ))
            .maps;
      });

  Future<Map<String, Object?>> page(
    String path, {
    required String namespace,
    required String idField,
    required int offset,
    required int limit,
  }) =>
      _withSource(path, (Connection connection) async {
        final String order = _quote(idField);
        final List<Map<String, Object?>> rows = (await _query(
          connection,
          'SELECT * FROM ${_qualified(namespace)} ORDER BY $order '
          'LIMIT $limit OFFSET $offset',
        ))
            .maps;
        return <String, Object?>{
          'rows': rows,
          'checkpoint': <String, Object?>{'offset': offset + rows.length},
          'complete': rows.length < limit,
        };
      });

  Future<String> createManagedNamespace(String path) async {
    _configureDuckDbLibrary();
    final Database database = await duckdb.open(path);
    final Connection connection = await duckdb.connect(database);
    try {
      await connection.execute(
        'CREATE TABLE IF NOT EXISTS proofray_memory ('
        'id VARCHAR PRIMARY KEY, text VARCHAR NOT NULL, source VARCHAR NOT NULL, '
        'session_id VARCHAR, sequence BIGINT, event_time BIGINT, role VARCHAR, '
        'speaker VARCHAR, version BIGINT NOT NULL, sha256 VARCHAR NOT NULL, '
        'created_at VARCHAR NOT NULL)',
      );
      return 'proofray_memory';
    } finally {
      await connection.dispose();
      await database.dispose();
    }
  }

  Future<T> _withSource<T>(
    String path,
    Future<T> Function(Connection connection) operation,
  ) async {
    _configureDuckDbLibrary();
    final Database database = await duckdb.open(':memory:');
    final Connection connection = await duckdb.connect(database);
    try {
      await connection.execute(
        "ATTACH '${path.replaceAll("'", "''")}' AS proofray_source (READ_ONLY)",
      );
      return await operation(connection);
    } finally {
      await connection.dispose();
      await database.dispose();
    }
  }

  Future<_Rows> _query(Connection connection, String sql) async {
    final ResultSet result = await connection.query(sql);
    try {
      return _Rows(result.columnNames, result.fetchAll());
    } finally {
      await result.dispose();
    }
  }

  String _qualified(String namespace) {
    final List<String> parts = namespace.split('.');
    if (parts.length != 2) throw ArgumentError.value(namespace);
    return '${_quote('proofray_source')}.${_quote(parts[0])}.${_quote(parts[1])}';
  }

  String _quote(String value) {
    if (value.isEmpty ||
        value.length > 128 ||
        value.runes.any((int rune) => rune < 32)) {
      throw ArgumentError.value(value);
    }
    return '"${value.replaceAll('"', '""')}"';
  }
}

bool _duckDbConfigured = false;

void _configureDuckDbLibrary() {
  if (_duckDbConfigured) return;
  final String? path = Platform.environment['PROOFRAY_DUCKDB_LIBRARY'];
  if (path != null && path.isNotEmpty) {
    final OperatingSystem system = Platform.isWindows
        ? OperatingSystem.windows
        : Platform.isAndroid
            ? OperatingSystem.android
            : OperatingSystem.linux;
    duckdb_loader.open.overrideFor(system, path);
  }
  _duckDbConfigured = true;
}

class _Rows {
  const _Rows(this.columns, this.rows);

  final List<String> columns;
  final List<List<Object?>> rows;

  List<Map<String, Object?>> get maps => <Map<String, Object?>>[
        for (final List<Object?> row in rows)
          <String, Object?>{
            for (int index = 0; index < columns.length; index++)
              columns[index]: _safeValue(row[index]),
          },
      ];
}

Object? _safeValue(Object? value) {
  if (value == null || value is String || value is num || value is bool) {
    return value;
  }
  if (value is Uint8List) {
    return <String, Object?>{
      '__proofray_transport_scalar_v1__': 'bytes',
      'value': value
          .map((int byte) => byte.toRadixString(16).padLeft(2, '0'))
          .join(),
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
  throw UnsupportedError('DuckDB returned an unsupported scalar type');
}
