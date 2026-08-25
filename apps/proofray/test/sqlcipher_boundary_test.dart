import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:sqlite3/sqlite3.dart';

void main() {
  test('SQLCipher encrypts database and transient journal bytes', () async {
    final Directory directory = await Directory.systemTemp.createTemp(
      'proofray-sqlcipher-',
    );
    addTearDown(() => directory.delete(recursive: true));
    final String path = '${directory.path}/memory.db';
    const String key =
        '3f8f70d7f7283793364cfbe7b99698c4a8b75060f67b19b681e9fba2db474a11';
    const String wrongKey =
        '4f8f70d7f7283793364cfbe7b99698c4a8b75060f67b19b681e9fba2db474a11';
    const String sentinel = 'PROOFRAY-PLAINTEXT-MUST-NEVER-APPEAR-8f46b9';

    final Database database = sqlite3.open(path);
    database.execute('PRAGMA key = "x\'$key\'"');
    final ResultSet cipher = database.select('PRAGMA cipher_version');
    expect(cipher, isNotEmpty);
    expect(cipher.first.values.first.toString(), isNotEmpty);
    database.execute('PRAGMA journal_mode = WAL');
    database.execute('PRAGMA synchronous = FULL');
    database.execute('CREATE TABLE memory (value TEXT NOT NULL)');
    database.execute('INSERT INTO memory (value) VALUES (?)', <Object?>[
      sentinel,
    ]);

    for (final String candidate in <String>[
      path,
      '$path-wal',
      '$path-journal',
      '$path-shm',
    ]) {
      final File file = File(candidate);
      if (await file.exists()) {
        expect(
          _contains(file.readAsBytesSync(), utf8.encode(sentinel)),
          isFalse,
          reason: 'plaintext leaked into $candidate',
        );
      }
    }
    database.close();

    final Database rejected = sqlite3.open(path);
    rejected.execute('PRAGMA key = "x\'$wrongKey\'"');
    expect(
      () => rejected.select('SELECT COUNT(*) FROM sqlite_master'),
      throwsA(isA<SqliteException>()),
    );
    rejected.close();

    final Database reopened = sqlite3.open(path);
    reopened.execute('PRAGMA key = "x\'$key\'"');
    expect(
      reopened.select('SELECT value FROM memory').single['value'],
      sentinel,
    );
    reopened.close();
  });
}

bool _contains(List<int> haystack, List<int> needle) {
  if (needle.isEmpty || haystack.length < needle.length) return false;
  for (int start = 0; start <= haystack.length - needle.length; start++) {
    bool equal = true;
    for (int offset = 0; offset < needle.length; offset++) {
      if (haystack[start + offset] != needle[offset]) {
        equal = false;
        break;
      }
    }
    if (equal) return true;
  }
  return false;
}
