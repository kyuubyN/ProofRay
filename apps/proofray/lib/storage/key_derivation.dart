import 'dart:convert';

import 'package:crypto/crypto.dart';

const int proofRayFallbackKdfIterations = 600000;

String deriveProofRayDatabaseKey(
  String passphrase,
  List<int> salt, {
  int iterations = proofRayFallbackKdfIterations,
}) {
  if (passphrase.isEmpty || salt.isEmpty || iterations < 1) {
    throw ArgumentError(
      'PBKDF2 requires passphrase, salt and positive iterations',
    );
  }
  final Hmac hmac = Hmac(sha256, utf8.encode(passphrase));
  List<int> value = hmac.convert(<int>[...salt, 0, 0, 0, 1]).bytes;
  final List<int> result = List<int>.from(value);
  for (int iteration = 1; iteration < iterations; iteration++) {
    value = hmac.convert(value).bytes;
    for (int index = 0; index < result.length; index++) {
      result[index] ^= value[index];
    }
  }
  return result
      .map((int byte) => byte.toRadixString(16).padLeft(2, '0'))
      .join();
}
