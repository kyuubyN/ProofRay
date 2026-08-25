import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import '../../models/chat_models.dart';
import 'bridge_protocol.dart';

typedef HostRequestHandler = Future<Map<String, Object?>> Function(
  String method,
  Map<String, Object?> payload,
);

class ProofRayBridgeException implements Exception {
  const ProofRayBridgeException(this.code);

  final String code;

  @override
  String toString() => 'ProofRayBridgeException($code)';
}

class ProofRayBridge {
  ProofRayBridge._(this._socket, this._token, this._hostRequestHandler) {
    _subscription = _socket
        .cast<List<int>>()
        .transform(utf8.decoder)
        .transform(const LineSplitter())
        .listen(_onLine, onError: _onTransportError, onDone: _onDone);
  }

  static Future<ProofRayBridge> connect({
    required int port,
    required String token,
    HostRequestHandler? hostRequestHandler,
  }) async {
    final Socket socket = await Socket.connect(
      InternetAddress.loopbackIPv4,
      port,
      timeout: const Duration(seconds: 5),
    );
    final ProofRayBridge bridge = ProofRayBridge._(
      socket,
      token,
      hostRequestHandler,
    );
    try {
      await bridge._authenticate();
      return bridge;
    } on Object {
      await bridge.close();
      rethrow;
    }
  }

  final Socket _socket;
  final String _token;
  final HostRequestHandler? _hostRequestHandler;
  final Map<String, StreamController<BridgeEvent>> _requests =
      <String, StreamController<BridgeEvent>>{};
  late final StreamSubscription<String> _subscription;
  final Random _random = Random.secure();
  bool _closed = false;

  String _newRequestId() {
    final String entropy = List<int>.generate(
      16,
      (int _) => _random.nextInt(256),
    ).map((int byte) => byte.toRadixString(16).padLeft(2, '0')).join();
    return 'app_$entropy';
  }

  Future<void> _authenticate() async {
    final String requestId = _newRequestId();
    final Stream<BridgeEvent> events = request(
      BridgeRequest(
        requestId: requestId,
        method: 'bridge.authenticate',
        payload: <String, Object?>{'token': _token},
      ),
    );
    final BridgeEvent event = await events.first.timeout(
      const Duration(seconds: 5),
    );
    if (event.event != 'authenticated') {
      throw const ProofRayBridgeException('authentication_failed');
    }
  }

  Stream<BridgeEvent> request(BridgeRequest request) {
    if (_closed || _requests.containsKey(request.requestId)) {
      throw const ProofRayBridgeException('bridge_unavailable');
    }
    final StreamController<BridgeEvent> controller =
        StreamController<BridgeEvent>();
    _requests[request.requestId] = controller;
    _socket.add(utf8.encode(request.encode()));
    unawaited(_socket.flush());
    return controller.stream;
  }

  Stream<BridgeEvent> sendMessage({
    required String conversationId,
    required String messageId,
    required String text,
    required MemoryMode memoryMode,
    required int sequence,
    required DateTime createdAt,
    String? providerId,
    String? providerSecret,
    List<String> keywords = const <String>[],
    List<Map<String, String>> turns = const <Map<String, String>>[],
    void Function(String)? onRequestId,
  }) {
    final String requestId = _newRequestId();
    onRequestId?.call(requestId);
    return request(
      BridgeRequest(
        requestId: requestId,
        method: 'message.send',
        payload: <String, Object?>{
          'conversation_id': conversationId,
          'message_id': messageId,
          'text': text,
          'memory_mode': memoryMode.name,
          'sequence': sequence,
          'created_at': createdAt.toUtc().toIso8601String(),
          'provider_id': ?providerId,
          'provider_secret': ?providerSecret,
          'turns': turns,
          if (keywords.isNotEmpty) 'keywords': keywords,
        },
      ),
    );
  }

  Future<void> configureProvider({
    required String providerId,
    required String kind,
    required String modelId,
    required String endpoint,
    bool customModel = false,
    bool? toolCallingOverride,
  }) async {
    final String requestId = _newRequestId();
    final BridgeEvent event = await request(
      BridgeRequest(
        requestId: requestId,
        method: 'provider.configure',
        payload: <String, Object?>{
          'provider_id': providerId,
          'kind': kind,
          'model_id': modelId,
          'endpoint': endpoint,
          'custom_model': customModel,
          'tool_calling_override': ?toolCallingOverride,
        },
      ),
    ).first;
    if (event.event != 'completed') {
      throw const ProofRayBridgeException('provider_configuration_failed');
    }
  }

  Future<List<Map<String, Object?>>> listProviderModels(
    String providerId, {
    String? secret,
  }) async {
    final BridgeEvent event = await request(
      BridgeRequest(
        requestId: _newRequestId(),
        method: 'provider.models',
        payload: <String, Object?>{
          'provider_id': providerId,
          'secret': ?secret,
        },
      ),
    ).first;
    final Object? raw = event.payload['models'];
    if (event.event != 'completed' || raw is! List<Object?>) {
      throw const ProofRayBridgeException('provider_models_failed');
    }
    return raw.whereType<Map<String, Object?>>().toList(growable: false);
  }

  Future<void> testProvider(String providerId, {String? secret}) async {
    final BridgeEvent event = await request(
      BridgeRequest(
        requestId: _newRequestId(),
        method: 'provider.test',
        payload: <String, Object?>{
          'provider_id': providerId,
          'secret': ?secret,
        },
      ),
    ).first;
    if (event.event != 'completed' || event.payload['reachable'] != true) {
      throw const ProofRayBridgeException('provider_test_failed');
    }
  }

  Future<void> removeProvider(String providerId) async {
    await _completed('provider.remove', <String, Object?>{
      'provider_id': providerId,
    });
  }

  Future<Map<String, Object?>> detectConnector(String endpoint) =>
      _completed('connector.detect', <String, Object?>{'endpoint': endpoint});

  Future<void> configureConnector({
    required String connectorId,
    required String kind,
    required String endpoint,
    Map<String, Object?> options = const <String, Object?>{},
  }) async {
    await _completed('connector.configure', <String, Object?>{
      'connector_id': connectorId,
      'kind': kind,
      'endpoint': endpoint,
      'options': options,
    });
  }

  Future<List<Map<String, Object?>>> connectorNamespaces(
    String connectorId, {
    String? secret,
  }) async {
    final Map<String, Object?> payload = await _completed(
      'connector.namespaces',
      <String, Object?>{'connector_id': connectorId, 'secret': ?secret},
    );
    final Object? rows = payload['namespaces'];
    return rows is List<Object?>
        ? rows.whereType<Map<String, Object?>>().toList(growable: false)
        : const <Map<String, Object?>>[];
  }

  Future<void> testConnector(String connectorId, {String? secret}) async {
    await _completed('connector.test', <String, Object?>{
      'connector_id': connectorId,
      'secret': ?secret,
    });
  }

  Future<Map<String, Object?>> sampleConnector(
    String connectorId,
    String namespace, {
    String? secret,
  }) => _completed('connector.sample', <String, Object?>{
    'connector_id': connectorId,
    'namespace': namespace,
    'limit': 50,
    'secret': ?secret,
  });

  Future<Map<String, Object?>> suggestConnectorMapping(
    Map<String, Object?> namespace,
  ) => _completed('connector.mapping.suggest', <String, Object?>{
    'namespace': namespace,
  });

  Future<Map<String, Object?>> previewConnector(
    String connectorId,
    Map<String, Object?> mapping, {
    String? secret,
  }) => _completed('connector.preview', <String, Object?>{
    'connector_id': connectorId,
    'mapping': mapping,
    'secret': ?secret,
  });

  Future<Map<String, Object?>> syncConnector(
    String connectorId,
    Map<String, Object?> mapping, {
    String? secret,
    Map<String, Object?>? checkpoint,
  }) => _completed('connector.sync', <String, Object?>{
    'connector_id': connectorId,
    'mapping': mapping,
    'secret': ?secret,
    'checkpoint': ?checkpoint,
  });

  Future<String> createManagedConnectorNamespace(
    String connectorId, {
    String? secret,
  }) async {
    final Map<String, Object?> result = await _completed(
      'connector.managed.create',
      <String, Object?>{
        'connector_id': connectorId,
        'authorize_managed_write': true,
        'secret': ?secret,
      },
    );
    final Object? namespace = result['namespace'];
    if (namespace is! String || namespace.isEmpty) {
      throw const ProofRayBridgeException('managed_namespace_failed');
    }
    return namespace;
  }

  Future<Map<String, Object?>> purgeMemorySource(String sourceId) => _completed(
    'memory.purge_source',
    <String, Object?>{'source_id': sourceId},
  );

  Future<int> warmMemory() async {
    final Map<String, Object?> result = await _completed(
      'memory.warm',
      const <String, Object?>{},
    );
    if (result['warmed'] != true || result['documents'] is! int) {
      throw const ProofRayBridgeException('memory_warm_failed');
    }
    return result['documents']! as int;
  }

  Future<Map<String, Object?>> getMemorySource({
    required int factId,
    required String sourceId,
  }) => _completed('memory.source.get', <String, Object?>{
    'fact_id': factId,
    'source_id': sourceId,
  });

  Future<Map<String, Object?>> purgeMemorySources(List<String> sourceIds) =>
      _completed('memory.purge_sources', <String, Object?>{
        'source_ids': sourceIds,
      });

  Future<Map<String, Object?>> purgeMemorySourcePrefix(String prefix) =>
      _completed('memory.purge_source_prefix', <String, Object?>{
        'prefix': prefix,
      });

  Future<void> removeConnector(String connectorId) async {
    await _completed('connector.remove', <String, Object?>{
      'connector_id': connectorId,
    });
  }

  Future<Map<String, Object?>> importLocalChunk({
    required String fileName,
    required String fileSha256,
    required int byteStart,
    required int byteEnd,
    required String text,
  }) => _completed('import.local_chunk', <String, Object?>{
    'file_name': fileName,
    'file_sha256': fileSha256,
    'byte_start': byteStart,
    'byte_end': byteEnd,
    'text': text,
  });

  Future<Map<String, Object?>> confirmMemory({
    required String conversationId,
    required String messageId,
    required String text,
    required int sequence,
    required DateTime createdAt,
  }) => _completed('memory.confirm', <String, Object?>{
    'conversation_id': conversationId,
    'message_id': messageId,
    'text': text,
    'sequence': sequence,
    'created_at': createdAt.toUtc().toIso8601String(),
  });

  Future<void> updateLocalProfile({
    required String profileName,
    required String timezoneName,
  }) async {
    await _completed('profile.update', <String, Object?>{
      'profile_name': profileName,
      'timezone_name': timezoneName,
    });
  }

  Future<Map<String, Object?>> _completed(
    String method,
    Map<String, Object?> payload,
  ) async {
    final BridgeEvent event = await request(
      BridgeRequest(
        requestId: _newRequestId(),
        method: method,
        payload: payload,
      ),
    ).first;
    if (event.event != 'completed') {
      throw ProofRayBridgeException('${method}_failed');
    }
    return event.payload;
  }

  Future<void> cancel(String requestId) async {
    if (_closed) {
      return;
    }
    final BridgeRequest cancellation = BridgeRequest(
      requestId: _newRequestId(),
      method: 'request.cancel',
      payload: <String, Object?>{'target_request_id': requestId},
    );
    _socket.add(utf8.encode(cancellation.encode()));
    await _socket.flush();
  }

  void _onLine(String line) {
    BridgeEvent event;
    try {
      event = BridgeEvent.decode(line);
    } on FormatException {
      _failAll('invalid_frame');
      return;
    }
    if (event.event == 'host.request') {
      unawaited(_handleHostRequest(event));
      return;
    }
    final StreamController<BridgeEvent>? controller =
        _requests[event.requestId];
    if (controller == null) {
      return;
    }
    controller.add(event);
    if (event.event == 'authenticated' ||
        event.event == 'completed' ||
        event.event == 'error') {
      _requests.remove(event.requestId);
      unawaited(controller.close());
    }
  }

  Future<void> _handleHostRequest(BridgeEvent event) async {
    final Object? methodValue = event.payload['method'];
    final Object? payloadValue = event.payload['payload'];
    final HostRequestHandler? handler = _hostRequestHandler;
    bool ok = false;
    Map<String, Object?> response = <String, Object?>{};
    if (handler != null &&
        methodValue is String &&
        payloadValue is Map<Object?, Object?> &&
        payloadValue.keys.every((Object? key) => key is String)) {
      try {
        response = await handler(
          methodValue,
          payloadValue.map(
            (Object? key, Object? value) => MapEntry(key! as String, value),
          ),
        );
        ok = true;
      } on Object {
        response = <String, Object?>{'code': 'host_request_rejected'};
      }
    } else {
      response = <String, Object?>{'code': 'host_request_unavailable'};
    }
    if (_closed) {
      return;
    }
    _socket.add(
      utf8.encode(
        BridgeRequest(
          requestId: event.requestId,
          method: 'host.response',
          payload: <String, Object?>{'ok': ok, 'payload': response},
        ).encode(),
      ),
    );
    await _socket.flush();
  }

  void _onTransportError(Object _) => _failAll('transport_error');

  void _onDone() => _failAll('transport_closed');

  void _failAll(String code) {
    if (_closed) {
      return;
    }
    _closed = true;
    for (final StreamController<BridgeEvent> controller in _requests.values) {
      controller.addError(ProofRayBridgeException(code));
      unawaited(controller.close());
    }
    _requests.clear();
  }

  Future<void> close() async {
    if (_closed) {
      return;
    }
    _closed = true;
    await _subscription.cancel();
    await _socket.close();
    for (final StreamController<BridgeEvent> controller in _requests.values) {
      await controller.close();
    }
    _requests.clear();
  }
}
