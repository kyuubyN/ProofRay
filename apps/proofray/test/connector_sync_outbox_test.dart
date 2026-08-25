import 'dart:io';

import 'package:drift/drift.dart' hide isNotNull, isNull;
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/storage/app_database.dart';
import 'package:proofray_app/storage/integration_store.dart';

void main() {
  test(
    'connector checkpoint and outbox ACK commit atomically across restart',
    () async {
      final Directory directory = await Directory.systemTemp.createTemp(
        'proofray-connector-outbox-',
      );
      addTearDown(() => directory.delete(recursive: true));
      final File file = File('${directory.path}/proofray.db');

      ProofRayDatabase database = ProofRayDatabase(NativeDatabase(file));
      IntegrationStore store = IntegrationStore(database, AppKeyStore());
      await _seedConnector(database);
      await store.commitCheckpoint('mapping', <String, Object?>{'offset': 256});
      await store.stageConnectorSync(
        mappingId: 'mapping',
        connectorId: 'connector',
        mapping: const <String, Object?>{
          'namespace': 'messages',
          'id_field': 'id',
          'text_field': 'text',
        },
        checkpoint: const <String, Object?>{'offset': 256},
      );
      await database.close();

      // A process crash after staging must preserve the exact prior checkpoint
      // and make the operation available for deterministic retry.
      database = ProofRayDatabase(NativeDatabase(file));
      store = IntegrationStore(database, AppKeyStore());
      final PendingConnectorSync operation =
          (await store.pendingConnectorSyncs()).single;
      expect(operation.checkpoint, <String, Object?>{'offset': 256});
      expect(await store.checkpoint('mapping'), <String, Object?>{
        'offset': 256,
      });

      // Inject a crash at the final outbox delete. The surrounding transaction
      // must also roll back checkpoint advancement.
      await database.customStatement('''
      CREATE TRIGGER fail_connector_ack BEFORE DELETE ON outbox_operations
      WHEN OLD.id = 'connector-sync:mapping'
      BEGIN
        SELECT RAISE(ABORT, 'injected crash');
      END
    ''');
      await expectLater(
        store.completeConnectorSync(operation, const <String, Object?>{
          'offset': 512,
        }),
        throwsA(anything),
      );
      expect(await store.checkpoint('mapping'), <String, Object?>{
        'offset': 256,
      });
      expect(
        (await store.pendingConnectorSyncs()).single.operationId,
        operation.operationId,
      );

      await database.customStatement('DROP TRIGGER fail_connector_ack');
      await store.completeConnectorSync(operation, const <String, Object?>{
        'offset': 512,
      });
      expect(await store.checkpoint('mapping'), <String, Object?>{
        'offset': 512,
      });
      expect(await store.pendingConnectorSyncs(), isEmpty);

      final QueryRow connector = await database
          .customSelect(
            'SELECT last_sync_at FROM connectors WHERE id=?',
            variables: <Variable<Object>>[const Variable<String>('connector')],
          )
          .getSingle();
      expect(connector.read<int?>('last_sync_at'), isNotNull);
      await database.close();
    },
  );
}

Future<void> _seedConnector(ProofRayDatabase database) async {
  final int now = DateTime.now().toUtc().millisecondsSinceEpoch;
  await database.customStatement(
    'INSERT INTO connectors '
    '(id, connector_kind, display_name, redacted_endpoint, secret_handle, '
    'capabilities_json, status, created_at, last_sync_at) '
    'VALUES (?, ?, ?, ?, NULL, ?, ?, ?, NULL)',
    <Object?>[
      'connector',
      'sqlite',
      'Fixture',
      '/redacted/source.db',
      '{"capabilities":[],"options":{}}',
      'configured',
      now,
    ],
  );
  await database.customStatement(
    'INSERT INTO connector_mappings '
    '(id, connector_id, namespace, mapping_json, managed_namespace, mirror_deletes) '
    'VALUES (?, ?, ?, ?, 0, 0)',
    <Object?>['mapping', 'connector', 'messages', '{}'],
  );
}
