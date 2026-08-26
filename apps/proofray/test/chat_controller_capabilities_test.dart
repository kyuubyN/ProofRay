import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/features/chat/chat_controller.dart';
import 'package:proofray_app/models/chat_models.dart';

void main() {
  test(
    'Tool mode is physically unavailable without a tool-capable provider',
    () {
      final ChatController controller = ChatController(
        conversationId: 'thread',
        memoryMode: MemoryMode.tool,
      );
      controller.setProvider(null);
      expect(controller.providerSupportsTools, isFalse);
      expect(controller.memoryMode, MemoryMode.keywords);

      controller.setProvider('plain', supportsTools: false);
      controller.setMemoryMode(MemoryMode.tool);
      expect(controller.memoryMode, MemoryMode.keywords);

      controller.setProvider('tools', supportsTools: true);
      controller.setMemoryMode(MemoryMode.tool);
      expect(controller.providerSupportsTools, isTrue);
      expect(controller.memoryMode, MemoryMode.tool);
      controller.dispose();
    },
  );

  test('proof receipt copy retains authority while hydrating sources', () {
    final ChatMessage original = ChatMessage(
      id: 'answer',
      role: MessageRole.assistant,
      text: 'proved',
      createdAt: DateTime.utc(2026, 8, 25),
      authority: AnswerAuthority.proved,
      memoryConsulted: true,
      certifiedText: 'proved',
      certificateHex: 'abcd',
      sources: const <ProofSource>[
        ProofSource(
          factId: 1,
          sourceId: 'source:1',
          text: '',
          parentSha256: 'digest',
          textDeferred: true,
        ),
      ],
    );
    final ChatMessage hydrated = original.copyWith(
      sources: const <ProofSource>[
        ProofSource(
          factId: 1,
          sourceId: 'source:1',
          text: 'exact',
          parentSha256: 'digest',
        ),
      ],
    );
    expect(hydrated.hasProof, isTrue);
    expect(hydrated.sources.single.text, 'exact');
    expect(hydrated.memoryConsulted, isTrue);
  });
}
