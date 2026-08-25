import 'dart:convert';

const String proofRayBridgeSchema = 'proofray.app.bridge.v1';
const int proofRayBridgeMaxFrameBytes = 1024 * 1024;

class BridgeRequest {
  const BridgeRequest({
    required this.requestId,
    required this.method,
    this.payload = const <String, Object?>{},
  });

  final String requestId;
  final String method;
  final Map<String, Object?> payload;

  String encode() {
    final String frame =
        '${jsonEncode(<String, Object?>{'schema': proofRayBridgeSchema, 'request_id': requestId, 'method': method, 'payload': payload})}\n';
    if (utf8.encode(frame).length > proofRayBridgeMaxFrameBytes) {
      throw const FormatException('bridge request exceeds byte limit');
    }
    return frame;
  }
}

class BridgeEvent {
  const BridgeEvent({
    required this.requestId,
    required this.event,
    required this.payload,
  });

  factory BridgeEvent.decode(String line) {
    if (utf8.encode(line).length > proofRayBridgeMaxFrameBytes) {
      throw const FormatException('bridge frame exceeds byte limit');
    }
    final Object? raw = jsonDecode(line);
    if (raw is! Map<Object?, Object?> ||
        raw['schema'] != proofRayBridgeSchema ||
        raw['request_id'] is! String ||
        raw['event'] is! String ||
        raw['payload'] is! Map<Object?, Object?> ||
        (raw['payload']! as Map<Object?, Object?>).keys.any(
          (Object? key) => key is! String,
        )) {
      throw const FormatException('invalid ProofRay bridge event');
    }
    return BridgeEvent(
      requestId: raw['request_id']! as String,
      event: raw['event']! as String,
      payload: (raw['payload']! as Map<Object?, Object?>).map(
        (Object? key, Object? value) => MapEntry(key! as String, value),
      ),
    );
  }

  final String requestId;
  final String event;
  final Map<String, Object?> payload;
}
