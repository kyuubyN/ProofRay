import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proofray_app/models/chat_models.dart';
import 'package:proofray_app/storage/app_database.dart';
import 'package:proofray_app/storage/conversation_store.dart';

void main() {
  test(
    'first conversation and proof receipt reopen byte-identically',
    () async {
      final Directory directory = await Directory.systemTemp.createTemp(
        'proofray-first-launch-',
      );
      addTearDown(() => directory.delete(recursive: true));
      final File file = File('${directory.path}/proofray.db');
      final DateTime now = DateTime.utc(2026, 8, 25, 12);
      final String sourceText = 'Minha bicicleta é azul cobalto.';
      final String sourceDigest = sha256
          .convert(utf8.encode(sourceText))
          .toString();
      final String certificateHex = utf8
          .encode(
            '{"proof":"closed","source":"conversation:thread:observation"}',
          )
          .map((int byte) => byte.toRadixString(16).padLeft(2, '0'))
          .join();

      ProofRayDatabase database = ProofRayDatabase(NativeDatabase(file));
      DriftConversationStore store = DriftConversationStore(database);
      await store.ensureLocalProfile(
        profileId: 'owner',
        displayName: 'Alice',
        locale: 'pt-BR',
        timezone: 'America/Sao_Paulo',
      );
      await store.ensureConversation(
        conversationId: 'thread',
        profileId: 'owner',
        title: 'Primeira conversa',
        memoryMode: MemoryMode.keywords,
      );

      final ChatMessage observation = ChatMessage(
        id: 'observation',
        role: MessageRole.user,
        text: sourceText,
        createdAt: now,
      );
      await store.stageUserMessage(
        'thread',
        0,
        observation,
        memoryMode: MemoryMode.keywords,
      );
      final Uint8List sidecar = _sidecarRecord(
        sourceId: 'message:thread:observation',
        content: sourceText,
      );
      await store.handleHostRequest('sidecar.replace_suffix', <String, Object?>{
        'store_key': 'personal-memory-v1',
        'common_prefix': 0,
        'common_prefix_sha256': '',
        'records': <String>[base64Encode(sidecar)],
      });
      await store.commitExchange(
        observation,
        ChatMessage(
          id: 'answer_observation',
          role: MessageRole.assistant,
          text: 'Memória local confirmada.',
          createdAt: now,
        ),
      );

      final ChatMessage question = ChatMessage(
        id: 'question',
        role: MessageRole.user,
        text: 'Você lembra a cor da minha bicicleta?',
        createdAt: now.add(const Duration(minutes: 1)),
      );
      await store.stageUserMessage(
        'thread',
        2,
        question,
        memoryMode: MemoryMode.forceNext,
      );
      final ChatMessage proved = ChatMessage(
        id: 'answer_question',
        role: MessageRole.assistant,
        text: 'Sua bicicleta é azul cobalto.',
        createdAt: question.createdAt,
        authority: AnswerAuthority.proved,
        memoryConsulted: true,
        certifiedText: 'Sua bicicleta é azul cobalto.',
        certificateHex: certificateHex,
        proofRunId: 'proof-question',
        proofMethod: 'fixture-reopenable',
        queryDigest: sha256.convert(utf8.encode(question.text)).toString(),
        documentsConsidered: 1,
        verifiedCandidates: 1,
        answerBytes: utf8.encode('Sua bicicleta é azul cobalto.').length,
        sources: <ProofSource>[
          ProofSource(
            factId: 42,
            sourceId: 'conversation:thread:observation',
            text: sourceText,
            parentSha256: sourceDigest,
            sessionId: 'thread',
            speaker: 'Alice',
            sourceSpan: (0, sourceText.length),
          ),
        ],
      );
      await store.commitExchange(question, proved);
      await database.close();

      database = ProofRayDatabase(NativeDatabase(file));
      store = DriftConversationStore(database);
      final List<ChatMessage> reopened = await store.loadMessages('thread');
      final ChatMessage receipt = reopened.singleWhere(
        (ChatMessage message) => message.id == proved.id,
      );
      expect(receipt.text, proved.text);
      expect(receipt.authority, AnswerAuthority.proved);
      expect(receipt.memoryConsulted, isTrue);
      expect(receipt.certificateHex, certificateHex);
      expect(receipt.certifiedText, proved.certifiedText);
      expect(receipt.queryDigest, proved.queryDigest);
      expect(receipt.sources.single.sourceId, proved.sources.single.sourceId);
      expect(receipt.sources.single.parentSha256, sourceDigest);
      expect(receipt.sources.single.sourceSpan, (0, sourceText.length));
      expect((await store.memoryObservations()).single.text, sourceText);
      expect(await store.pendingUserMessages('thread'), isEmpty);
      await database.close();
    },
  );
}

Uint8List _sidecarRecord({required String sourceId, required String content}) {
  final Map<String, Object?> record = <String, Object?>{
    'schema': 'horizon.authorized-sidecar-batch.v1',
    'sequence': 1,
    'previous_sha256': List<String>.filled(64, '0').join(),
    'scope': '1',
    'adapter_id': 'acceptance-fixture',
    'authority_sha256': List<String>.filled(64, 'a').join(),
    'source_id': sourceId,
    'content': content,
    'source_sha256': sha256.convert(utf8.encode(content)).toString(),
    'facts': const <Object?>[],
    'completeness_claims': const <Object?>[],
  };
  record['record_sha256'] = sha256
      .convert(utf8.encode(_canonicalJson(record)))
      .toString();
  return Uint8List.fromList(utf8.encode(_canonicalJson(record)));
}

String _canonicalJson(Object? value) => jsonEncode(_canonicalValue(value));

Object? _canonicalValue(Object? value) {
  if (value is Map<Object?, Object?>) {
    final List<String> keys = value.keys.whereType<String>().toList()..sort();
    return <String, Object?>{
      for (final String key in keys) key: _canonicalValue(value[key]),
    };
  }
  if (value is List<Object?>) {
    return <Object?>[for (final Object? item in value) _canonicalValue(item)];
  }
  return value;
}
