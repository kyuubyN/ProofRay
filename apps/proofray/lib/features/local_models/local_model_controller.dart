import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import '../../services/bridge/bridge_protocol.dart';
import '../../services/bridge/proofray_bridge.dart';
import '../../storage/integration_store.dart';

/// Which local GGUF files exist, and which one llama.cpp currently holds.
///
/// Kept apart from [ChatController] on purpose: the loaded model outlives any
/// single conversation, and every surface that cares -- settings, the composer
/// switcher and the chat overlay -- has to agree on one answer about what is in
/// VRAM right now.
class LocalModelController extends ChangeNotifier {
  LocalModelController({required this.integrations, required this.bridge});

  static const String directoryPreference = 'local_models.directory';
  static const String binaryPreference = 'local_models.server_binary';

  final IntegrationStore integrations;
  final ProofRayBridge? Function() bridge;

  String? _directory;
  String? _serverBinary;
  List<LocalModelEntry> _models = const <LocalModelEntry>[];
  String _state = 'idle';
  String? _activePath;
  String? _endpoint;
  String? _error;
  bool _busy = false;
  List<ModelFamily> _families = const <ModelFamily>[];
  List<ModelSummary> _results = const <ModelSummary>[];
  String _query = '';
  List<CatalogModelFile> _catalogFiles = const <CatalogModelFile>[];
  String? _catalogRepository;
  String? _downloadLabel;
  int _downloadReceived = 0;
  int _downloadTotal = 0;

  String? get directory => _directory;
  String? get serverBinary => _serverBinary;
  List<LocalModelEntry> get models => List<LocalModelEntry>.unmodifiable(_models);
  List<LocalModelEntry> get usableModels =>
      _models.where((LocalModelEntry item) => item.supported).toList();
  String get state => _state;
  String? get activePath => _activePath;
  String? get endpoint => _endpoint;
  String? get error => _error;
  bool get busy => _busy;
  bool get loading => _state == 'loading' || (_busy && _activePath != null);
  List<ModelFamily> get families => _families;
  List<ModelSummary> get searchResults => _results;
  String get query => _query;
  List<CatalogModelFile> get catalogFiles => _catalogFiles;
  String? get catalogRepository => _catalogRepository;

  /// Non-null exactly while something is being fetched, so one progress bar
  /// serves both the engine download and a model download.
  String? get downloadLabel => _downloadLabel;
  int get downloadReceived => _downloadReceived;
  int get downloadTotal => _downloadTotal;
  double? get downloadFraction => _downloadTotal <= 0
      ? null
      : (_downloadReceived / _downloadTotal).clamp(0.0, 1.0);

  LocalModelEntry? get active {
    for (final LocalModelEntry item in _models) {
      if (item.path == _activePath) return item;
    }
    return null;
  }

  /// Where downloads land unless someone picks somewhere else.
  ///
  /// Defaulting matters more than it looks: asking for a folder before
  /// anything has been downloaded turns "Install" into a file manager opening,
  /// which reads as the wrong thing happening rather than as a step. The path
  /// is shown in settings so a hand-placed GGUF still has an obvious home.
  Future<String> defaultRoot(String leaf) async {
    final Directory support = await getApplicationSupportDirectory();
    final Directory root = Directory(p.join(support.path, 'proofray', leaf));
    await root.create(recursive: true);
    return root.path;
  }

  Future<String> ensureModelDirectory() async {
    final String? chosen = _directory;
    if (chosen != null) return chosen;
    final String fallback = await defaultRoot('models');
    await useDirectory(fallback);
    return fallback;
  }

  Future<void> restore() async {
    final Object? directory = await integrations.preference(directoryPreference);
    final Object? binary = await integrations.preference(binaryPreference);
    _directory = directory is String && directory.isNotEmpty ? directory : null;
    _serverBinary = binary is String && binary.isNotEmpty ? binary : null;
    notifyListeners();
    // Settled up front so the panel can always name a real folder. "No folder
    // chosen" tells someone with their own GGUF nothing about where to put it.
    try {
      await ensureModelDirectory();
    } on Object {
      // A read-only or missing support directory must not stop the rest of the
      // app from restoring; the folder can still be picked by hand.
    }
    await scan();
    await refreshStatus();
  }

  Future<void> useDirectory(String directory) async {
    _directory = directory;
    await integrations.setPreference(directoryPreference, directory);
    await scan();
  }

  Future<void> useServerBinary(String? binary) async {
    _serverBinary = binary == null || binary.isEmpty ? null : binary;
    await integrations.setPreference(binaryPreference, _serverBinary ?? '');
    notifyListeners();
  }

  Future<void> scan() async {
    final ProofRayBridge? active = bridge();
    final String? directory = _directory;
    if (active == null || directory == null) return;
    _busy = true;
    _error = null;
    notifyListeners();
    try {
      final List<Map<String, Object?>> rows = await active.scanLocalModels(
        directory,
      );
      _models = <LocalModelEntry>[
        for (final Map<String, Object?> row in rows) LocalModelEntry.from(row),
      ];
    } on ProofRayBridgeException catch (failure) {
      _error = failure.code;
      _models = const <LocalModelEntry>[];
    } on Object catch (failure) {
      _error = failure.toString();
      _models = const <LocalModelEntry>[];
    } finally {
      _busy = false;
      notifyListeners();
    }
  }

  Future<void> refreshStatus() async {
    final ProofRayBridge? active = bridge();
    if (active == null) return;
    try {
      _apply(await active.localModelStatus());
    } on Object {
      // Status is advisory; a failure here must not blank the picked folder.
    }
    notifyListeners();
  }

  /// Loads one model and only returns once llama.cpp answers or gives up.
  Future<void> load(LocalModelEntry model) async {
    final ProofRayBridge? active = bridge();
    if (active == null || !model.supported) return;
    _busy = true;
    _error = null;
    _state = 'loading';
    _activePath = model.path;
    _endpoint = null;
    notifyListeners();
    try {
      _apply(
        await active.loadLocalModel(model.path, serverBinary: _serverBinary),
      );
    } on ProofRayBridgeException catch (failure) {
      _error = failure.code;
      _state = 'failed';
    } on Object catch (failure) {
      _error = failure.toString();
      _state = 'failed';
    } finally {
      _busy = false;
      notifyListeners();
    }
  }

  Future<void> unload() async {
    final ProofRayBridge? active = bridge();
    if (active == null) return;
    _busy = true;
    notifyListeners();
    try {
      _apply(await active.unloadLocalModel());
    } on Object catch (failure) {
      _error = failure.toString();
    } finally {
      _busy = false;
      notifyListeners();
    }
  }

  Future<void> loadCatalog() async {
    final ProofRayBridge? active = bridge();
    if (active == null) return;
    if (_families.isEmpty) {
      try {
        final Map<String, Object?> result = await active.modelFamilies();
        final Object? rows = result['families'];
        if (rows is List<Object?>) {
          _families = <ModelFamily>[
            for (final Object? item in rows)
              if (item is Map<String, Object?>) ModelFamily.from(item),
          ];
        }
      } on Object catch (failure) {
        _error = failure.toString();
      }
    }
    if (_results.isEmpty) await search('');
    notifyListeners();
  }

  /// Searches Hugging Face live. A fixed catalogue would be wrong within
  /// weeks, and the model someone wants is often the one released yesterday.
  Future<void> search(String query) async {
    final ProofRayBridge? active = bridge();
    if (active == null) return;
    _query = query;
    _busy = true;
    _error = null;
    _catalogFiles = const <CatalogModelFile>[];
    _catalogRepository = null;
    notifyListeners();
    try {
      final Map<String, Object?> result = await active.searchModels(query);
      final Object? rows = result['results'];
      _results = <ModelSummary>[
        for (final Object? item in rows is List<Object?> ? rows : const <Object?>[])
          if (item is Map<String, Object?>) ModelSummary.from(item),
      ];
    } on ProofRayBridgeException catch (failure) {
      _error = failure.code;
      _results = const <ModelSummary>[];
    } on Object catch (failure) {
      _error = failure.toString();
      _results = const <ModelSummary>[];
    } finally {
      _busy = false;
      notifyListeners();
    }
  }

  Future<void> openRepository(String repository) async {
    final ProofRayBridge? active = bridge();
    if (active == null) return;
    _catalogRepository = repository;
    _catalogFiles = const <CatalogModelFile>[];
    _busy = true;
    _error = null;
    notifyListeners();
    try {
      final Map<String, Object?> result = await active.modelFiles(repository);
      final Object? files = result['files'];
      if (files is List<Object?>) {
        _catalogFiles = <CatalogModelFile>[
          for (final Object? item in files)
            if (item is Map<String, Object?>) CatalogModelFile.from(item),
        ];
      }
    } on ProofRayBridgeException catch (failure) {
      _error = failure.code;
    } on Object catch (failure) {
      _error = failure.toString();
    } finally {
      _busy = false;
      notifyListeners();
    }
  }

  Future<bool> downloadCatalogModel(CatalogModelFile file) async {
    final ProofRayBridge? active = bridge();
    if (active == null) return false;
    final String directory = await ensureModelDirectory();
    return _runDownload(
      label: file.filename,
      total: file.sizeBytes,
      events: active.downloadModel(
        file: file.payload,
        destination: directory,
      ),
      onDone: scan,
    );
  }

  /// Installs an official llama.cpp build and adopts it as the server.
  Future<bool> installLlama(
    Map<String, Object?> build, {
    String? destination,
  }) async {
    final ProofRayBridge? active = bridge();
    if (active == null) return false;
    final String root = destination ?? await defaultRoot('llama.cpp');
    String? installed;
    final bool ok = await _runDownload(
      label: build['asset'] as String? ?? 'llama.cpp',
      total: build['size_bytes'] as int? ?? 0,
      events: active.installLlamaBuild(build: build, destination: root),
      onResult: (Map<String, Object?> result) {
        installed = result['server_binary'] as String?;
      },
    );
    if (ok && installed != null) await useServerBinary(installed);
    return ok;
  }

  Future<bool> _runDownload({
    required String label,
    required int total,
    required Stream<BridgeEvent> events,
    Future<void> Function()? onDone,
    void Function(Map<String, Object?> result)? onResult,
  }) async {
    _downloadLabel = label;
    _downloadReceived = 0;
    _downloadTotal = total;
    _error = null;
    notifyListeners();
    bool completed = false;
    try {
      await for (final BridgeEvent event in events) {
        if (event.event == 'progress') {
          _downloadReceived = event.payload['received_bytes'] as int? ?? 0;
          final int reported = event.payload['total_bytes'] as int? ?? 0;
          if (reported > 0) _downloadTotal = reported;
          notifyListeners();
        } else if (event.event == 'completed') {
          completed = true;
          onResult?.call(event.payload);
        } else if (event.event == 'error') {
          _error = event.payload['code'] as String? ?? 'download_failed';
        }
      }
    } on ProofRayBridgeException catch (failure) {
      _error = failure.code;
    } on Object catch (failure) {
      _error = failure.toString();
    } finally {
      _downloadLabel = null;
      _downloadReceived = 0;
      _downloadTotal = 0;
      notifyListeners();
    }
    if (completed && onDone != null) await onDone();
    return completed;
  }

  void _apply(Map<String, Object?> status) {
    final Object? state = status['state'];
    _state = state is String ? state : 'idle';
    final Object? path = status['model_path'];
    _activePath = path is String ? path : null;
    final Object? endpoint = status['endpoint'];
    _endpoint = endpoint is String ? endpoint : null;
    final Object? detail = status['detail'];
    if (detail is String && detail.isNotEmpty) _error = detail;
  }
}

class LocalModelEntry {
  const LocalModelEntry({
    required this.path,
    required this.name,
    required this.sizeBytes,
    required this.format,
    required this.supported,
    this.reason,
  });

  factory LocalModelEntry.from(Map<String, Object?> row) => LocalModelEntry(
    path: row['path'] as String? ?? '',
    name: row['name'] as String? ?? '',
    sizeBytes: row['size_bytes'] as int? ?? 0,
    format: row['format'] as String? ?? '',
    supported: row['supported'] == true,
    reason: row['reason'] as String?,
  );

  final String path;
  final String name;
  final int sizeBytes;
  final String format;
  final bool supported;
  final String? reason;

  String get sizeLabel => sizeBytes >= 1000000000
      ? '${(sizeBytes / 1000000000).toStringAsFixed(2)} GB'
      : '${(sizeBytes / 1000000).toStringAsFixed(0)} MB';
}


class ModelFamily {
  const ModelFamily({required this.key, required this.label});

  factory ModelFamily.from(Map<String, Object?> row) => ModelFamily(
    key: row['key'] as String? ?? '',
    label: row['label'] as String? ?? '',
  );

  final String key;
  final String label;
}

class ModelSummary {
  const ModelSummary({
    required this.repository,
    required this.downloads,
    required this.likes,
    required this.updated,
  });

  factory ModelSummary.from(Map<String, Object?> row) => ModelSummary(
    repository: row['repository'] as String? ?? '',
    downloads: row['downloads'] as int? ?? 0,
    likes: row['likes'] as int? ?? 0,
    updated: row['updated'] as String? ?? '',
  );

  final String repository;
  final int downloads;
  final int likes;
  final String updated;

  String get owner => repository.split('/').first;
  String get name => repository.split('/').last;

  String get downloadsLabel => downloads >= 1000000
      ? '${(downloads / 1000000).toStringAsFixed(1)}M'
      : downloads >= 1000
      ? '${(downloads / 1000).toStringAsFixed(0)}k'
      : '$downloads';
}

class CatalogModelFile {
  const CatalogModelFile({
    required this.repository,
    required this.filename,
    required this.sizeBytes,
    required this.sha256,
  });

  factory CatalogModelFile.from(Map<String, Object?> row) => CatalogModelFile(
    repository: row['repository'] as String? ?? '',
    filename: row['filename'] as String? ?? '',
    sizeBytes: row['size_bytes'] as int? ?? 0,
    sha256: row['sha256'] as String? ?? '',
  );

  final String repository;
  final String filename;
  final int sizeBytes;
  final String sha256;

  Map<String, Object?> get payload => <String, Object?>{
    'repository': repository,
    'filename': filename,
    'size_bytes': sizeBytes,
    'sha256': sha256,
  };

  /// The quantisation label people actually choose by, pulled off the filename.
  String get quantisation {
    final RegExpMatch? match = RegExp(
      r'(IQ\d[A-Z_]*|Q\d[A-Z0-9_]*|BF16|F16|F32)',
      caseSensitive: false,
    ).firstMatch(filename);
    return match?.group(0)?.toUpperCase() ?? filename;
  }

  String get sizeLabel => sizeBytes >= 1000000000
      ? '${(sizeBytes / 1000000000).toStringAsFixed(2)} GB'
      : '${(sizeBytes / 1000000).toStringAsFixed(0)} MB';
}
