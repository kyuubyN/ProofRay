import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/storage/integration_store.dart';

void main() {
  test('connector options reject nested credentials and credential URIs', () {
    expect(
      () => validateSecretlessConnectorOptions(<String, Object?>{
        'database': 'notes',
        'nested': <String, Object?>{'api_key': 'secret'},
      }),
      throwsArgumentError,
    );
    expect(
      () => validateSecretlessConnectorOptions(<String, Object?>{
        'mirror': 'https://user:pass@example.test/data',
      }),
      throwsArgumentError,
    );
    expect(
      () => validateSecretlessConnectorOptions(<String, Object?>{
        'database': 'notes',
        'tables': <String>['messages'],
        'read_only': true,
      }),
      returnsNormally,
    );
  });
}
