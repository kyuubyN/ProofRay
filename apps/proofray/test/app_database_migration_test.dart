import 'dart:convert';
import 'dart:io';

import 'package:drift/drift.dart' hide isNull;
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/storage/app_database.dart';
import 'package:sqlite3/sqlite3.dart' as raw;

void main() {
  test(
    'v6 migration rebuilds exact message authority without blessing AI text',
    () async {
      final Directory directory = await Directory.systemTemp.createTemp(
        'proofray-migration-',
      );
      addTearDown(() => directory.delete(recursive: true));
      final File file = File('${directory.path}/v6.db');
      final raw.Database legacy = raw.sqlite3.open(file.path);
      legacy.execute('''
      CREATE TABLE messages (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        role TEXT NOT NULL,
        memory_source_id TEXT,
        memory_eligible INTEGER NOT NULL DEFAULT 0
      )
    ''');
      legacy.execute('''
      CREATE TABLE sidecar_records (
        sequence INTEGER PRIMARY KEY,
        previous_sha256 TEXT NOT NULL,
        record_sha256 TEXT NOT NULL,
        canonical_payload BLOB NOT NULL,
        committed_at INTEGER NOT NULL
      )
    ''');
      legacy.execute(
        'INSERT INTO messages VALUES (?, ?, ?, NULL, 0)',
        <Object?>['m1', 'thread', 'user'],
      );
      legacy.execute(
        'INSERT INTO messages VALUES (?, ?, ?, NULL, 0)',
        <Object?>['a1', 'thread', 'assistant'],
      );
      legacy.execute(
        'INSERT INTO sidecar_records VALUES (1, ?, ?, ?, 0)',
        <Object?>[
          List<String>.filled(64, '0').join(),
          List<String>.filled(64, 'a').join(),
          utf8.encode(
            jsonEncode(<String, Object?>{'source_id': 'message:thread:m1'}),
          ),
        ],
      );
      legacy.execute('PRAGMA user_version = 6');
      legacy.close();

      final ProofRayDatabase migrated = ProofRayDatabase(NativeDatabase(file));
      addTearDown(migrated.close);
      final List<QueryRow> rows = await migrated
          .customSelect(
            'SELECT id, memory_source_id, memory_eligible FROM messages ORDER BY id',
          )
          .get();
      expect(rows.first.read<String>('id'), 'a1');
      expect(rows.first.read<String?>('memory_source_id'), isNull);
      expect(rows.first.read<int>('memory_eligible'), 0);
      expect(rows.last.read<String>('id'), 'm1');
      expect(
        rows.last.read<String>('memory_source_id'),
        'conversation:thread:m1',
      );
      expect(rows.last.read<int>('memory_eligible'), 1);
      final QueryRow sidecar = await migrated
          .customSelect('SELECT message_id FROM sidecar_records')
          .getSingle();
      expect(sidecar.read<String>('message_id'), 'm1');
      final QueryRow version = await migrated
          .customSelect('PRAGMA user_version')
          .getSingle();
      expect(version.read<int>('user_version'), 8);
    },
  );

  for (int legacyVersion = 1; legacyVersion <= 7; legacyVersion++) {
    test(
      'v$legacyVersion schema reaches v8 and reopens idempotently',
      () async {
        final Directory directory = await Directory.systemTemp.createTemp(
          'proofray-migration-v$legacyVersion-',
        );
        addTearDown(() => directory.delete(recursive: true));
        final File file = File('${directory.path}/legacy.db');
        final raw.Database legacy = raw.sqlite3.open(file.path);
        _createRequiredLegacySurface(legacy, legacyVersion);
        legacy.execute('PRAGMA user_version = $legacyVersion');
        legacy.close();

        ProofRayDatabase migrated = ProofRayDatabase(NativeDatabase(file));
        await _expectCurrentMigrationSurface(migrated);
        await migrated.close();

        migrated = ProofRayDatabase(NativeDatabase(file));
        await _expectCurrentMigrationSurface(migrated);
        await migrated.close();
      },
    );
  }
}

void _createRequiredLegacySurface(raw.Database database, int version) {
  database.execute('''
    CREATE TABLE provider_configurations (
      id TEXT PRIMARY KEY,
      provider_kind TEXT NOT NULL,
      display_name TEXT NOT NULL,
      model_id TEXT NOT NULL,
      endpoint TEXT,
      secret_handle TEXT,
      enabled INTEGER NOT NULL DEFAULT 1,
      custom_model INTEGER NOT NULL DEFAULT 0,
      ${version >= 3 ? 'supports_tools INTEGER NOT NULL DEFAULT 1,' : ''}
      model_cache_json TEXT,
      model_cache_expires_at INTEGER
    )
  ''');
  database.execute('''
    CREATE TABLE proof_runs (
      id TEXT PRIMARY KEY,
      message_id TEXT NOT NULL,
      state TEXT NOT NULL,
      action TEXT NOT NULL,
      deterministic_answer TEXT,
      displayed_rewrite TEXT,
      method TEXT,
      certificate BLOB,
      query_digest TEXT NOT NULL,
      documents_considered INTEGER NOT NULL DEFAULT 0,
      verified_candidates INTEGER NOT NULL DEFAULT 0,
      answer_bytes INTEGER NOT NULL DEFAULT 0,
      ${version >= 5 ? 'text_truncated INTEGER NOT NULL DEFAULT 0,' : ''}
      created_at INTEGER NOT NULL
    )
  ''');
  database.execute('''
    CREATE TABLE messages (
      id TEXT PRIMARY KEY,
      conversation_id TEXT NOT NULL,
      role TEXT NOT NULL,
      memory_source_id TEXT,
      memory_eligible INTEGER NOT NULL DEFAULT 0
    )
  ''');
  database.execute('''
    CREATE TABLE sidecar_records (
      sequence INTEGER PRIMARY KEY,
      previous_sha256 TEXT NOT NULL,
      record_sha256 TEXT NOT NULL,
      canonical_payload BLOB NOT NULL,
      ${version >= 7 ? 'message_id TEXT,' : ''}
      committed_at INTEGER NOT NULL
    )
  ''');
  if (version >= 2) {
    database.execute(
      'CREATE TABLE app_preferences (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)',
    );
  }
  if (version >= 4) {
    database.execute('''
      CREATE TABLE sidecar_replacement_plans (
        transaction_id TEXT PRIMARY KEY,
        common_prefix INTEGER NOT NULL,
        common_prefix_sha256 TEXT NOT NULL,
        total_records INTEGER NOT NULL,
        created_at INTEGER NOT NULL
      )
    ''');
    database.execute('''
      CREATE TABLE sidecar_replacement_chunks (
        transaction_id TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        encoded_records BLOB NOT NULL,
        PRIMARY KEY (transaction_id, chunk_index)
      )
    ''');
  }
  if (version >= 6) {
    database.execute('''
      CREATE TABLE local_imports (
        id TEXT PRIMARY KEY,
        file_name TEXT NOT NULL,
        file_sha256 TEXT NOT NULL,
        total_bytes INTEGER NOT NULL,
        source_ids_json TEXT NOT NULL,
        imported_at INTEGER NOT NULL
      )
    ''');
  }
}

Future<void> _expectCurrentMigrationSurface(ProofRayDatabase database) async {
  final QueryRow version = await database
      .customSelect('PRAGMA user_version')
      .getSingle();
  expect(version.read<int>('user_version'), 8);
  for (final String table in <String>[
    'app_preferences',
    'sidecar_replacement_plans',
    'sidecar_replacement_chunks',
    'local_imports',
  ]) {
    final QueryRow row = await database
        .customSelect(
          "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name=?",
          variables: <Variable<Object>>[Variable<String>(table)],
        )
        .getSingle();
    expect(row.read<int>('n'), 1, reason: '$table missing after migration');
  }
  for (final (String table, String column) in <(String, String)>[
    ('provider_configurations', 'supports_tools'),
    ('proof_runs', 'text_truncated'),
    ('sidecar_records', 'message_id'),
  ]) {
    final List<QueryRow> columns = await database
        .customSelect('PRAGMA table_info($table)')
        .get();
    expect(
      columns.map((QueryRow row) => row.read<String>('name')),
      contains(column),
      reason: '$table.$column missing after migration',
    );
  }
}
