import 'dart:async';
import 'dart:convert';
import 'dart:ffi';
import 'dart:io';
import 'dart:math';

import 'package:ffi/ffi.dart';

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
    // Diagnostic-only: reports which startup stage is in progress so the UI
    // can show more than a single static "starting" label. Every stage here
    // is currently unbounded except _waitForPort's own 30s timeout --
    // warmMemory() in particular has none, so a slow local corpus can leave
    // the UI stuck on that one label indefinitely with no other symptom.
    void Function(String stage)? onStatus,
  }) async {
    if (_bridge != null) {
      return _bridge!;
    }
    if (_started) {
      throw StateError('ProofRay runtime startup is already in progress');
    }
    _started = true;
    try {
      onStatus?.call('preparing_bootstrap');
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
      onStatus?.call('launching_embedded_python');
      _ensureGlobalPythonSymbols();
      await SeriousPython.run(
        environmentVariables: <String, String>{
          'PROOFRAY_APP_BOOTSTRAP': bootstrap.path,
          'PROOFRAY_APP_TOKEN': token,
          'PYTHONUTF8': '1',
        },
        sync: false,
      );
      onStatus?.call('waiting_for_bridge_port');
      final int port = await _waitForPort(runtime);
      onStatus?.call('connecting_bridge');
      _bridge = await ProofRayBridge.connect(
        port: port,
        token: token,
        hostRequestHandler: hostRequestHandler,
      );
      onStatus?.call('warming_memory_index');
      // warmMemory() has no bridge-level timeout of its own (see
      // ProofRayBridge.request/_completed) and its cost scales with the
      // local corpus, not a fixed amount of work -- diagnosed as the actual
      // cause of a real, reproducible "stuck on STARTING LOCAL CORE"
      // report after a long testing session accumulated many conversations/
      // messages. 45s here is generous for a personal-scale corpus and
      // turns an indefinite hang into a visible, reportable failure.
      await _bridge!.warmMemory().timeout(const Duration(seconds: 45));
      onStatus?.call('ready');
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

  /// Loads libpython into the global symbol scope before the interpreter runs.
  ///
  /// Compiled C-extension wheels like NumPy never link against a libpython of
  /// their own -- the manylinux convention is that the embedding process has
  /// already made those symbols global. Dart's FFI `dlopen` uses RTLD_LOCAL, so
  /// the interpreter that `dart_bridge` pulls in stays private and NumPy dies
  /// on import with `undefined symbol: PyObject_SelfIter`.
  ///
  /// The shipped launcher works around this with LD_PRELOAD, but that only
  /// covers being started through the launcher: `flutter test -d linux`, an IDE
  /// run and anything else invoking the binary directly all bypass it, which is
  /// exactly how the acceptance test ended up with no local core at all. Doing
  /// it here fixes every launch path, and is a no-op when LD_PRELOAD already
  /// did it: dlopen on an already-loaded library just promotes its scope.
  void _ensureGlobalPythonSymbols() {
    if (!Platform.isLinux && !Platform.isAndroid) return;
    try {
      final Directory libraries = Directory(
        p.join(p.dirname(Platform.resolvedExecutable), 'lib'),
      );
      if (!libraries.existsSync()) return;
      final List<FileSystemEntity> found = libraries
          .listSync()
          .where((FileSystemEntity item) =>
              p.basename(item.path).startsWith('libpython3.') &&
              p.basename(item.path).contains('.so'))
          .toList();
      if (found.isEmpty) return;
      // RTLD_NOW | RTLD_GLOBAL: resolve immediately so a broken build fails
      // here rather than deep inside an import, and publish the symbols.
      final int flags = 0x00002 | 0x00100;
      final _DlOpen dlopen = DynamicLibrary.process()
          .lookupFunction<_DlOpenNative, _DlOpen>('dlopen');
      final Pointer<Utf8> path = found.first.path.toNativeUtf8();
      try {
        dlopen(path, flags);
      } finally {
        malloc.free(path);
      }
    } on Object {
      // Best effort. If this fails the launcher's LD_PRELOAD may still have
      // covered it, and the port wait below reports a core that never started.
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

typedef _DlOpenNative = Pointer<Void> Function(Pointer<Utf8>, Int32);
typedef _DlOpen = Pointer<Void> Function(Pointer<Utf8>, int);
