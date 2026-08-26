import 'package:flutter/foundation.dart';

/// ID prefix for a message created by [ChatController.confirmAsMemory].
///
/// These are stored (and reopen from the database) exactly like an ordinary
/// [MessageRole.user] turn, on purpose -- the memory-authority model treats
/// a confirmed observation as something the user genuinely asserted, and
/// existing turn-context/history logic must keep working unchanged for it.
/// The prefix exists only so presentation code (see [MessageTranscript]) can
/// tell the two apart without a schema change: a confirmed observation must
/// never be rendered as if the user had just typed and sent it.
const String confirmedObservationIdPrefix = 'confirmed_';

enum MessageRole { user, assistant, system }

enum AnswerAuthority { proved, evidence, abstention, contested, model, pending }

enum MemoryMode { tool, keywords, forceNext, off }

enum BitHorizonStage {
  idle,
  activating,
  routing,
  verifying,
  proofClosed,
  evidence,
  contested,
  abstained,
}

@immutable
class ProofSource {
  const ProofSource({
    required this.factId,
    required this.sourceId,
    required this.text,
    required this.parentSha256,
    this.sessionId,
    this.speaker,
    this.sourceSpan,
    this.textDeferred = false,
  });

  final int factId;
  final String sourceId;
  final String text;
  final String parentSha256;
  final String? sessionId;
  final String? speaker;
  final (int, int)? sourceSpan;
  final bool textDeferred;
}

@immutable
class ChatMessage {
  const ChatMessage({
    required this.id,
    required this.role,
    required this.text,
    required this.createdAt,
    this.authority = AnswerAuthority.model,
    this.memoryConsulted = false,
    this.certifiedText,
    this.certificateHex,
    this.proofRunId,
    this.proofMethod,
    this.queryDigest,
    this.documentsConsidered = 0,
    this.verifiedCandidates = 0,
    this.answerBytes = 0,
    this.textTruncated = false,
    this.sources = const <ProofSource>[],
  }) : assert(
         !memoryConsulted || role == MessageRole.assistant,
         'Only assistant output can carry the memory-activation marker.',
       );

  final String id;
  final MessageRole role;
  final String text;
  final DateTime createdAt;
  final AnswerAuthority authority;

  /// True only when the ProofRay memory pipeline actually ran for this answer.
  /// The UI renders the small green brain from this field and never guesses
  /// activation from the message text or authority label.
  final bool memoryConsulted;

  /// Exact deterministic text bound by [certificateHex]. A model rewrite may
  /// be shown in [text], but never replaces this authority boundary.
  final String? certifiedText;
  final String? certificateHex;
  final String? proofRunId;
  final String? proofMethod;
  final String? queryDigest;
  final int documentsConsidered;
  final int verifiedCandidates;
  final int answerBytes;
  final bool textTruncated;
  final List<ProofSource> sources;

  bool get hasProof =>
      authority == AnswerAuthority.proved &&
      certifiedText != null &&
      certificateHex != null;

  ChatMessage copyWith({
    String? text,
    AnswerAuthority? authority,
    bool? memoryConsulted,
    String? certifiedText,
    String? certificateHex,
    String? proofRunId,
    String? proofMethod,
    String? queryDigest,
    int? documentsConsidered,
    int? verifiedCandidates,
    int? answerBytes,
    bool? textTruncated,
    List<ProofSource>? sources,
  }) => ChatMessage(
    id: id,
    role: role,
    text: text ?? this.text,
    createdAt: createdAt,
    authority: authority ?? this.authority,
    memoryConsulted: memoryConsulted ?? this.memoryConsulted,
    certifiedText: certifiedText ?? this.certifiedText,
    certificateHex: certificateHex ?? this.certificateHex,
    proofRunId: proofRunId ?? this.proofRunId,
    proofMethod: proofMethod ?? this.proofMethod,
    queryDigest: queryDigest ?? this.queryDigest,
    documentsConsidered: documentsConsidered ?? this.documentsConsidered,
    verifiedCandidates: verifiedCandidates ?? this.verifiedCandidates,
    answerBytes: answerBytes ?? this.answerBytes,
    textTruncated: textTruncated ?? this.textTruncated,
    sources: sources ?? this.sources,
  );
}

@immutable
class ConversationSummary {
  const ConversationSummary({
    required this.id,
    required this.title,
    required this.updatedAt,
    required this.memoryMode,
  });

  final String id;
  final String title;
  final DateTime updatedAt;
  final MemoryMode memoryMode;
}
