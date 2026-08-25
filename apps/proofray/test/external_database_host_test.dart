import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/storage/external_database_host.dart';
import 'package:sqlite3/sqlite3.dart';

void main() {
  test(
    'host SQLite path is read-only and safely quotes source identifiers',
    () async {
      final Directory directory = await Directory.systemTemp.createTemp(
        'proofray-host-test-',
      );
      final File file = File(
        '${directory.path}${Platform.pathSeparator}source.sqlite',
      );
      final Database database = sqlite3.open(file.path);
      database.execute(
        'CREATE TABLE "source notes" '
        '("record id" INTEGER PRIMARY KEY, "body text" TEXT NOT NULL)',
      );
      database.execute('INSERT INTO "source notes" VALUES (1, \'exact text\')');
      database.close();

      try {
        const ExternalDatabaseHost host = ExternalDatabaseHost();
        final Map<String, Object?> discovery = await host.handle(
          'connector.sqlite.discover',
          <String, Object?>{
            'endpoint': file.path,
            'options': const <String, Object?>{},
          },
        );
        final List<Object?> namespaces =
            discovery['namespaces']! as List<Object?>;
        expect(
          (namespaces.single! as Map<Object?, Object?>)['identity'],
          'source notes',
        );

        final Map<String, Object?> page = await host.handle(
          'connector.sqlite.page',
          <String, Object?>{
            'endpoint': file.path,
            'options': const <String, Object?>{},
            'namespace': 'source notes',
            'id_field': 'record id',
            'checkpoint': <String, Object?>{'offset': 0},
            'limit': 256,
          },
        );
        final List<Object?> rows = page['rows']! as List<Object?>;
        expect(
          (rows.single! as Map<Object?, Object?>)['body text'],
          'exact text',
        );
      } finally {
        await directory.delete(recursive: true);
      }
    },
  );

  test(
    'host SQLite connector unlocks SQLCipher only with its call-scoped key',
    () async {
      final Directory directory = await Directory.systemTemp.createTemp(
        'proofray-host-sqlcipher-',
      );
      addTearDown(() => directory.delete(recursive: true));
      final File file = File('${directory.path}/encrypted.sqlite');
      const String secret = 'external-database-passphrase';
      final Database database = sqlite3.open(file.path);
      database.execute("PRAGMA key = '$secret'");
      expect(database.select('PRAGMA cipher_version'), isNotEmpty);
      database.execute(
        'CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)',
      );
      database.execute('INSERT INTO notes VALUES (1, ?)', <Object?>[
        'encrypted exact source',
      ]);
      database.close();

      const ExternalDatabaseHost host = ExternalDatabaseHost();
      await expectLater(
        host.handle('connector.sqlite.discover', <String, Object?>{
          'endpoint': file.path,
          'options': const <String, Object?>{},
          'secret': 'wrong-passphrase',
        }),
        throwsA(anything),
      );
      final Map<String, Object?> discovery = await host.handle(
        'connector.sqlite.discover',
        <String, Object?>{
          'endpoint': file.path,
          'options': const <String, Object?>{},
          'secret': secret,
        },
      );
      final List<Object?> namespaces =
          discovery['namespaces']! as List<Object?>;
      expect(
        namespaces.whereType<Map<Object?, Object?>>().single['identity'],
        'notes',
      );
      final String raw = String.fromCharCodes(await file.readAsBytes());
      expect(raw, isNot(contains('encrypted exact source')));
    },
  );

  test(
    'repository DuckDB FFI host creates only its dedicated namespace',
    () async {
      final Directory directory = await Directory.systemTemp.createTemp(
        'proofray-duckdb-test-',
      );
      final File file = File(
        '${directory.path}${Platform.pathSeparator}source.duckdb',
      );
      try {
        const ExternalDatabaseHost host = ExternalDatabaseHost();
        final Map<String, Object?> created = await host.handle(
          'connector.duckdb.managed_create',
          <String, Object?>{
            'endpoint': file.path,
            'options': const <String, Object?>{'managed_write': true},
          },
        );
        expect(created['namespace'], 'proofray_memory');
        expect(
          await host.handle('connector.duckdb.test', <String, Object?>{
            'endpoint': file.path,
            'options': const <String, Object?>{},
          }),
          <String, Object?>{'reachable': true},
        );
        final Map<String, Object?> discovery = await host.handle(
          'connector.duckdb.discover',
          <String, Object?>{
            'endpoint': file.path,
            'options': const <String, Object?>{},
          },
        );
        final List<Object?> namespaces =
            discovery['namespaces']! as List<Object?>;
        final Map<Object?, Object?> descriptor = namespaces
            .whereType<Map<Object?, Object?>>()
            .singleWhere(
              (Map<Object?, Object?> item) =>
                  item['identity'] == 'main.proofray_memory',
            );
        expect(
          descriptor['fields'],
          containsAll(<String>['id', 'text', 'sha256']),
        );
      } finally {
        await directory.delete(recursive: true);
      }
    },
  );
}
