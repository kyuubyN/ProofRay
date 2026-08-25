import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:serious_python/serious_python.dart';

import '../bridge/proofray_bridge.dart';

class EmbeddedPythonRuntime {
  EmbeddedPythonRuntime();

  ProofRayBridge? _bridge;
  bool _started = false;

  ProofRayBridge? get bridge => _bridge;

  Future<ProofRayBridge> start({
    HostRequestHandler? hostRequestHandler,
    String profileName = 'User',
    String timezone = 'UTC',
  }) async {
    if (_bridge != null) {
      return _bridge!;
    }
    if (_started) {
      throw StateError('ProofRay runtime startup is already in progress');
    }
    _started = true;
    try {
      final Directory support = await getApplicationSupportDirectory();
      final Directory runtimeDirectory = Directory(
        p.join(support.path, 'proofray', 'runtime'),
      );
      await runtimeDirectory.create(recursive: true);
      final File bootstrap = File(
        p.join(runtimeDirectory.path, 'bootstrap.json'),
      );
      final File runtime = File(p.join(runtimeDirectory.path, 'runtime.json'));
      if (await runtime.exists()) {
        await runtime.delete();
      }
      if (await bootstrap.exists()) {
        await bootstrap.delete();
      }
      final String token = _randomToken();
      await _writeAtomic(bootstrap, <String, Object?>{
        'schema': 'proofray.app.bootstrap.v1',
        'profile_name': profileName,
        'timezone': timezone,
      });
      await SeriousPython.run(
        environmentVariables: <String, String>{
          'PROOFRAY_APP_BOOTSTRAP': bootstrap.path,
          'PROOFRAY_APP_TOKEN': token,
          'PYTHONUTF8': '1',
        },
        sync: false,
      );
      final int port = await _waitForPort(runtime);
      _bridge = await ProofRayBridge.connect(
        port: port,
        token: token,
        hostRequestHandler: hostRequestHandler,
      );
      await _bridge!.warmMemory();
      return _bridge!;
    } on Object {
      await _bridge?.close();
      _bridge = null;
      SeriousPython.terminate();
      rethrow;
    } finally {
      _started = false;
    }
  }

  Future<void> stop() async {
    await _bridge?.close();
    _bridge = null;
    SeriousPython.terminate();
  }

  String _randomToken() {
    final Random random = Random.secure();
    return List<int>.generate(
      32,
      (_) => random.nextInt(256),
    ).map((int value) => value.toRadixString(16).padLeft(2, '0')).join();
  }

  Future<int> _waitForPort(File runtime) async {
    // A cold embedded interpreter (CPython + NumPy + horizon_memory import)
    // on a freshly provisioned CI runner can take well over ten seconds to
    // bind its bridge socket and publish runtime.json; 12s was measured to
    // be too tight on hosted Linux/Windows GitHub Actions runners and made
    // the app surface a permanent "LOCAL CORE UNAVAILABLE" error before the
    // process had a real chance to start. 30s leaves headroom under the
    // acceptance test's own 45s "STARTING LOCAL CORE" tolerance for the
    // bridge connect + auth handshake (up to 10s) that follows this wait.
    final DateTime deadline = DateTime.now().add(const Duration(seconds: 30));
    while (DateTime.now().isBefore(deadline)) {
      if (await runtime.exists()) {
        try {
          final Object? decoded = jsonDecode(await runtime.readAsString());
          if (decoded is Map<String, Object?> &&
              decoded['schema'] == 'proofray.app.runtime.v1' &&
              decoded['port'] is int) {
            return decoded['port']! as int;
          }
        } on FormatException {
          // Atomic replacement normally prevents this; retry defensively.
        }
      }
      await Future<void>.delayed(const Duration(milliseconds: 80));
    }
    throw TimeoutException('embedded ProofRay runtime did not become ready');
  }

  Future<void> _writeAtomic(
    File destination,
    Map<String, Object?> value,
  ) async {
    final File temporary = File('${destination.path}.tmp');
    await temporary.writeAsString(jsonEncode(value), flush: true);
    await temporary.rename(destination.path);
  }
}
