import 'package:drift/native.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/storage/app_database.dart';
import 'package:proofray_app/storage/integration_store.dart';

void main() {
  test('provider ID resolves its opaque rotating vault handle', () async {
    final _MemorySecureStorage vault = _MemorySecureStorage();
    final ProofRayDatabase database = ProofRayDatabase(NativeDatabase.memory());
    addTearDown(database.close);
    final IntegrationStore store = IntegrationStore(
      database,
      AppKeyStore(storage: vault),
    );

    await store.saveProvider(
      id: 'provider-gemini',
      kind: 'gemini',
      displayName: 'Gemini',
      modelId: 'gemini-test',
      endpoint: 'https://generativelanguage.googleapis.com/v1beta',
      customModel: true,
      supportsTools: true,
      secret: 'first-secret',
    );
    final StoredProvider first = (await store.providers()).single;
    expect(first.secretHandle, isNot('provider.provider-gemini'));
    expect(await store.providerSecretById('provider-gemini'), 'first-secret');

    await store.saveProvider(
      id: 'provider-gemini',
      kind: 'gemini',
      displayName: 'Gemini',
      modelId: 'gemini-test',
      endpoint: 'https://generativelanguage.googleapis.com/v1beta',
      customModel: true,
      supportsTools: true,
      secret: 'rotated-secret',
    );
    final StoredProvider rotated = (await store.providers()).single;
    expect(rotated.secretHandle, isNot(first.secretHandle));
    expect(
      await vault.read(key: 'proofray.secret.${first.secretHandle}'),
      isNull,
    );
    expect(await store.providerSecretById('provider-gemini'), 'rotated-secret');
    expect(await store.providerSecretById('unknown'), isNull);
  });
}

class _MemorySecureStorage extends FlutterSecureStorage {
  _MemorySecureStorage();

  final Map<String, String> _values = <String, String>{};

  @override
  Future<String?> read({
    required String key,
    AppleOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WindowsOptions? wOptions,
    WebOptions? webOptions,
    AppleOptions? mOptions,
  }) async => _values[key];

  @override
  Future<void> write({
    required String key,
    required String? value,
    AppleOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WindowsOptions? wOptions,
    WebOptions? webOptions,
    AppleOptions? mOptions,
  }) async {
    if (value == null) {
      _values.remove(key);
    } else {
      _values[key] = value;
    }
  }

  @override
  Future<void> delete({
    required String key,
    AppleOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WindowsOptions? wOptions,
    WebOptions? webOptions,
    AppleOptions? mOptions,
  }) async {
    _values.remove(key);
  }
}
