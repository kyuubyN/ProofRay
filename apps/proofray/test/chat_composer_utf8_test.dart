import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/features/chat/chat_composer.dart';

void main() {
  test('composer limit is physical UTF-8 bytes rather than code units', () {
    const Utf8LengthLimitingTextInputFormatter formatter =
        Utf8LengthLimitingTextInputFormatter(4);
    const TextEditingValue empty = TextEditingValue();
    const TextEditingValue fourBytes = TextEditingValue(text: 'áá');
    const TextEditingValue sixBytes = TextEditingValue(text: 'ááá');

    expect(formatter.formatEditUpdate(empty, fourBytes), fourBytes);
    expect(formatter.formatEditUpdate(fourBytes, sixBytes), fourBytes);
  });
}
