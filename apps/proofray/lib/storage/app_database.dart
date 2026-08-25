import 'dart:convert';
import 'dart:io';
import 'dart:isolate';
import 'dart:math';

import 'package:drift/drift.dart';
import 'package:drift/native.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import 'key_derivation.dart';

part 'app_database.g.dart';

class Profiles extends Table {
  TextColumn get id => text()();
  TextColumn get displayName => text()();
  TextColumn get locale => text()();
  TextColumn get timezone => text()();
  DateTimeColumn get createdAt => dateTime()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{id};
}

class Conversations extends Table {
  TextColumn get id => text()();
  TextColumn get profileId => text().references(Profiles, #id)();
  TextColumn get title => text()();
  TextColumn get memoryMode => text()();
  DateTimeColumn get createdAt => dateTime()();
  DateTimeColumn get updatedAt => dateTime()();
  DateTimeColumn get deletedAt => dateTime().nullable()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{id};
}

class Messages extends Table {
  TextColumn get id => text()();
  TextColumn get conversationId => text().references(Conversations, #id)();
  IntColumn get sequence => integer()();
  TextColumn get role => text()();
  TextColumn get origin => text()();
  TextColumn get authority => text()();
  TextColumn get content => text()();
  BoolColumn get memoryEligible =>
      boolean().withDefault(const Constant(false))();
  BoolColumn get memoryConsulted =>
      boolean().withDefault(const Constant(false))();
  TextColumn get memorySourceId => text().nullable()();
  TextColumn get status => text()();
  DateTimeColumn get createdAt => dateTime()();
  DateTimeColumn get deletedAt => dateTime().nullable()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{id};

  @override
  List<Set<Column<Object>>> get uniqueKeys => <Set<Column<Object>>>[
    <Column<Object>>{conversationId, sequence},
  ];
}

class ProofRuns extends Table {
  TextColumn get id => text()();
  TextColumn get messageId => text().references(Messages, #id)();
  TextColumn get state => text()();
  TextColumn get action => text()();
  TextColumn get deterministicAnswer => text().nullable()();
  TextColumn get displayedRewrite => text().nullable()();
  TextColumn get method => text().nullable()();
  BlobColumn get certificate => blob().nullable()();
  TextColumn get queryDigest => text()();
  IntColumn get documentsConsidered =>
      integer().withDefault(const Constant(0))();
  IntColumn get verifiedCandidates =>
      integer().withDefault(const Constant(0))();
  IntColumn get answerBytes => integer().withDefault(const Constant(0))();
  BoolColumn get textTruncated =>
      boolean().withDefault(const Constant(false))();
  DateTimeColumn get createdAt => dateTime()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{id};
}

@DataClassName('ProofSourceRow')
class ProofSources extends Table {
  TextColumn get proofRunId => text().references(ProofRuns, #id)();
  IntColumn get ordinal => integer()();
  IntColumn get factId => integer()();
  TextColumn get sourceId => text()();
  TextColumn get textValue => text()();
  TextColumn get parentSha256 => text()();
  TextColumn get sessionId => text().nullable()();
  TextColumn get speaker => text().nullable()();
  IntColumn get spanStart => integer().nullable()();
  IntColumn get spanEnd => integer().nullable()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{proofRunId, ordinal};
}

class ProviderConfigurations extends Table {
  TextColumn get id => text()();
  TextColumn get providerKind => text()();
  TextColumn get displayName => text()();
  TextColumn get modelId => text()();
  TextColumn get endpoint => text().nullable()();
  TextColumn get secretHandle => text().nullable()();
  BoolColumn get enabled => boolean().withDefault(const Constant(true))();
  BoolColumn get customModel => boolean().withDefault(const Constant(false))();
  BoolColumn get supportsTools => boolean().withDefault(const Constant(true))();
  TextColumn get modelCacheJson => text().nullable()();
  DateTimeColumn get modelCacheExpiresAt => dateTime().nullable()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{id};
}

class Connectors extends Table {
  TextColumn get id => text()();
  TextColumn get connectorKind => text()();
  TextColumn get displayName => text()();
  TextColumn get redactedEndpoint => text()();
  TextColumn get secretHandle => text().nullable()();
  TextColumn get capabilitiesJson => text()();
  TextColumn get status => text()();
  DateTimeColumn get createdAt => dateTime()();
  DateTimeColumn get lastSyncAt => dateTime().nullable()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{id};
}

class ConnectorMappings extends Table {
  TextColumn get id => text()();
  TextColumn get connectorId => text().references(Connectors, #id)();
  TextColumn get namespace => text()();
  TextColumn get mappingJson => text()();
  BoolColumn get managedNamespace =>
      boolean().withDefault(const Constant(false))();
  BoolColumn get mirrorDeletes =>
      boolean().withDefault(const Constant(false))();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{id};
}

class ConnectorCheckpoints extends Table {
  TextColumn get mappingId => text().references(ConnectorMappings, #id)();
  TextColumn get cursorJson => text()();
  TextColumn get sourceDigest => text()();
  DateTimeColumn get committedAt => dateTime()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{mappingId};
}

class LocalImports extends Table {
  TextColumn get id => text()();
  TextColumn get fileName => text()();
  TextColumn get fileSha256 => text()();
  IntColumn get totalBytes => integer()();
  TextColumn get sourceIdsJson => text()();
  DateTimeColumn get importedAt => dateTime()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{id};
}

class OutboxOperations extends Table {
  TextColumn get id => text()();
  TextColumn get operationKind => text()();
  BlobColumn get payload => blob()();
  TextColumn get state => text()();
  IntColumn get attempts => integer().withDefault(const Constant(0))();
  DateTimeColumn get createdAt => dateTime()();
  DateTimeColumn get updatedAt => dateTime()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{id};
}

class SidecarRecords extends Table {
  IntColumn get sequence => integer()();
  TextColumn get previousSha256 => text()();
  TextColumn get recordSha256 => text()();
  BlobColumn get canonicalPayload => blob()();
  TextColumn get messageId => text().nullable()();
  DateTimeColumn get committedAt => dateTime()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{sequence};
}

class SidecarReplacementPlans extends Table {
  TextColumn get transactionId => text()();
  IntColumn get commonPrefix => integer()();
  TextColumn get commonPrefixSha256 => text()();
  IntColumn get totalRecords => integer()();
  DateTimeColumn get createdAt => dateTime()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{transactionId};
}

class SidecarReplacementChunks extends Table {
  TextColumn get transactionId => text().references(
    SidecarReplacementPlans,
    #transactionId,
    onDelete: KeyAction.cascade,
  )();
  IntColumn get chunkIndex => integer()();
  BlobColumn get encodedRecords => blob()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{
    transactionId,
    chunkIndex,
  };
}

class AppPreferences extends Table {
  TextColumn get key => text()();
  TextColumn get valueJson => text()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{key};
}

@DriftDatabase(
  tables: <Type>[
    Profiles,
    Conversations,
    Messages,
    ProofRuns,
    ProofSources,
    ProviderConfigurations,
    Connectors,
    ConnectorMappings,
    ConnectorCheckpoints,
    LocalImports,
    OutboxOperations,
    SidecarRecords,
    SidecarReplacementPlans,
    SidecarReplacementChunks,
    AppPreferences,
  ],
)
class ProofRayDatabase extends _$ProofRayDatabase {
  ProofRayDatabase(super.executor);

  @override
  int get schemaVersion => 8;

  @override
  MigrationStrategy get migration => MigrationStrategy(
    onCreate: (Migrator migrator) => migrator.createAll(),
    onUpgrade: (Migrator migrator, int from, int to) async {
      if (from < 2) await migrator.createTable(appPreferences);
      if (from < 3) {
        await migrator.addColumn(
          providerConfigurations,
          providerConfigurations.supportsTools,
        );
      }
      if (from < 4) {
        await migrator.createTable(sidecarReplacementPlans);
        await migrator.createTable(sidecarReplacementChunks);
      }
      if (from < 5) {
        await migrator.addColumn(proofRuns, proofRuns.textTruncated);
      }
      if (from < 6) {
        await migrator.createTable(localImports);
      }
      if (from < 7) {
        await migrator.addColumn(sidecarRecords, sidecarRecords.messageId);
        final List<QueryRow> rows = await customSelect(
          'SELECT sequence, canonical_payload FROM sidecar_records',
        ).get();
        for (final QueryRow row in rows) {
          final Object? decoded = jsonDecode(
            utf8.decode(
              row.read<Uint8List>('canonical_payload'),
              allowMalformed: false,
            ),
          );
          final Object? sourceId = decoded is Map<Object?, Object?>
              ? decoded['source_id']
              : null;
          if (sourceId is String && sourceId.startsWith('message:')) {
            await customStatement(
              'UPDATE sidecar_records SET message_id=? WHERE sequence=?',
              <Object?>[sourceId.split(':').last, row.read<int>('sequence')],
            );
          }
        }
      }
      if (from < 8) {
        await customStatement(
          "UPDATE messages SET memory_source_id="
          "('conversation:' || conversation_id || ':' || id) "
          "WHERE role='user'",
        );
        await customStatement(
          "UPDATE messages SET memory_source_id=NULL WHERE role<>'user'",
        );
        await customStatement(
          'UPDATE messages SET memory_eligible=1 WHERE id IN '
          '(SELECT message_id FROM sidecar_records WHERE message_id IS NOT NULL)',
        );
      }
    },
    beforeOpen: (OpeningDetails details) async {
      await customStatement('PRAGMA foreign_keys = ON');
      await customStatement('PRAGMA journal_mode = WAL');
      await customStatement('PRAGMA synchronous = FULL');
    },
  );
}

class AppKeyStore {
  AppKeyStore({FlutterSecureStorage? storage, this._passphraseProvider})
    : _storage = storage ?? const FlutterSecureStorage();

  static const String _databaseKeyName = 'proofray.database-key.v1';
  final FlutterSecureStorage _storage;
  Future<String> Function()? _passphraseProvider;
  final Map<String, String> _sessionSecrets = <String, String>{};

  Future<String> loadOrCreateDatabaseKey(Directory fallbackDirectory) async {
    try {
      final String? existing = await _storage.read(key: _databaseKeyName);
      if (existing != null) {
        if (RegExp(r'^[0-9a-f]{64}$').hasMatch(existing)) {
          return existing;
        }
        throw StateError('secure vault database key is corrupt');
      }
      final String key = _randomHex(32);
      await _storage.write(key: _databaseKeyName, value: key);
      return key;
    } on PlatformException {
      return _fallbackDatabaseKey(fallbackDirectory);
    } on MissingPluginException {
      return _fallbackDatabaseKey(fallbackDirectory);
    }
  }

  Future<String> _fallbackDatabaseKey(Directory fallbackDirectory) async {
    final Future<String> Function()? provider = _passphraseProvider;
    if (provider == null) {
      throw const SecureVaultUnavailable();
    }
    _passphraseProvider = null;
    final String passphrase = await provider();
    if (passphrase.length < 10) {
      throw ArgumentError(
        'fallback passphrase must contain at least 10 characters',
      );
    }
    final File saltFile = File(
      p.join(fallbackDirectory.path, 'proofray.kdf-salt'),
    );
    final List<int> salt;
    if (await saltFile.exists()) {
      salt = await saltFile.readAsBytes();
      if (salt.length != 16) throw StateError('invalid database KDF salt');
    } else {
      salt = _randomBytes(16);
      await saltFile.writeAsBytes(salt, flush: true);
    }
    return Isolate.run<String>(
      () => deriveProofRayDatabaseKey(passphrase, salt),
    );
  }

  Future<void> storeSecret(String handle, String value) async {
    if (handle.isEmpty || value.isEmpty) {
      throw ArgumentError('secret handle and value are required');
    }
    try {
      await _storage.write(key: 'proofray.secret.$handle', value: value);
      _sessionSecrets.remove(handle);
    } on PlatformException {
      _sessionSecrets[handle] = value;
    } on MissingPluginException {
      _sessionSecrets[handle] = value;
    }
  }

  Future<String?> readSecret(String handle) async {
    try {
      return await _storage.read(key: 'proofray.secret.$handle') ??
          _sessionSecrets[handle];
    } on PlatformException {
      return _sessionSecrets[handle];
    } on MissingPluginException {
      return _sessionSecrets[handle];
    }
  }

  Future<void> deleteSecret(String handle) async {
    _sessionSecrets.remove(handle);
    try {
      await _storage.delete(key: 'proofray.secret.$handle');
    } on PlatformException {
      return;
    } on MissingPluginException {
      return;
    }
  }
}

class SecureVaultUnavailable implements Exception {
  const SecureVaultUnavailable();
}

List<int> _randomBytes(int length) {
  final Random random = Random.secure();
  return List<int>.generate(length, (_) => random.nextInt(256));
}

String _randomHex(int length) =>
    _randomBytes(length)
        .map((int byte) => byte.toRadixString(16).padLeft(2, '0'))
        .join();

Future<String> proofRayDatabasePath() async {
  final Directory support = await getApplicationSupportDirectory();
  final Directory directory = Directory(p.join(support.path, 'proofray'));
  await directory.create(recursive: true);
  return p.join(directory.path, 'proofray.db');
}

Future<ProofRayDatabase> openProofRayDatabase({AppKeyStore? keyStore}) async {
  final File file = File(await proofRayDatabasePath());
  final Directory directory = file.parent;
  final String key = await (keyStore ?? AppKeyStore()).loadOrCreateDatabaseKey(
    directory,
  );
  final QueryExecutor executor = NativeDatabase.createInBackground(
    file,
    setup: (database) {
      database.execute('PRAGMA key = "x\'$key\'"');
      final rows = database.select('PRAGMA cipher_version');
      if (rows.isEmpty ||
          rows.first.values.isEmpty ||
          rows.first.values.first == null ||
          rows.first.values.first.toString().isEmpty) {
        throw StateError(
          'SQLCipher is required; plaintext SQLite is forbidden',
        );
      }
    },
  );
  return ProofRayDatabase(executor);
}
