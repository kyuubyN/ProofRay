import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/services/bridge/bridge_protocol.dart';

void main() {
  test('bridge rejects a wrong schema and oversized frame', () {
    expect(
      () => BridgeEvent.decode(
        '{"schema":"wrong","request_id":"r","event":"completed","payload":{}}',
      ),
      throwsFormatException,
    );
    expect(
      () => BridgeEvent.decode(
        List<String>.filled(proofRayBridgeMaxFrameBytes + 1, 'x').join(),
      ),
      throwsFormatException,
    );
  });

  test('request always frames one JSON line', () {
    const BridgeRequest request = BridgeRequest(
      requestId: 'r1',
      method: 'bridge.health',
    );
    expect(request.encode().endsWith('\n'), isTrue);
    expect(request.encode().split('\n').length, 2);
    expect(
      () => BridgeRequest(
        requestId: 'large',
        method: 'import.local_chunk',
        payload: <String, Object?>{
          'text': List<String>.filled(proofRayBridgeMaxFrameBytes, 'x').join(),
        },
      ).encode(),
      throwsFormatException,
    );
  });
}
