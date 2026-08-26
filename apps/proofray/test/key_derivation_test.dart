import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/storage/key_derivation.dart';

void main() {
  test('PBKDF2-HMAC-SHA256 matches published vectors', () {
    expect(
      deriveProofRayDatabaseKey('password', utf8.encode('salt'), iterations: 1),
      '120fb6cffcf8b32c43e7225256c4f837a86548c92ccc35480805987cb70be17b',
    );
    expect(
      deriveProofRayDatabaseKey('password', utf8.encode('salt'), iterations: 2),
      'ae4d0c95af6b46d32d0adff928f06dd02a303f8ef3c251dfd6e2d85a95474c43',
    );
  });

  test('release fallback never lowers the declared work factor', () {
    expect(proofRayFallbackKdfIterations, 600000);
  });
}
