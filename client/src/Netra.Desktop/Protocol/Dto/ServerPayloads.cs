namespace Netra.Desktop.Protocol.Dto;

// response.segment shape is taken from
// shared/contracts/examples/server/response_segment.json (the
// server_to_client schema does not yet define per-type $defs the way
// client_to_server does).
public sealed record ResponseSegmentPayload
{
    public required string GenerationId { get; init; }
    public required string SegmentId { get; init; }
    public required string SentenceId { get; init; }
    public string? Kind { get; init; }
    public required string Text { get; init; }
    public bool Final { get; init; }
    public IReadOnlyList<string>? EvidenceIds { get; init; }
}

// session.snapshot, quiz.question and error were approved 2026-09-12 (see
// docs/architecture/message-flow.md) and are now typed sub-schemas of
// shared/contracts/protocol/v1/server_to_client.schema.json. These records
// mirror the SessionSnapshot/QuizQuestion $defs and error.schema.json
// field-for-field.

public sealed record ActiveLessonRefPayload
{
    public required Guid LessonId { get; init; }
}

// Field-for-field identical to the agent-contract PendingQuestion shape by
// design (see the schema's own $comment), not by accident.
public sealed record PendingQuestionRefPayload
{
    public required string QuestionId { get; init; }
    public required int QuestionVersion { get; init; }
    public required int HintsUsed { get; init; }
}

public sealed record ResultSetRefPayload
{
    public required Guid ResultSetId { get; init; }
    public required DateTimeOffset CreatedAt { get; init; }
}

// Reference-only by design: it is not a dump of session history. See the
// schema's $comment for why connection_state, full playback-generation
// detail and any return-navigation position are absent. A non-null
// PendingQuestion here means the server also sends a fresh quiz.question
// message immediately after this snapshot with the full persisted question.
public sealed record SessionSnapshotPayload
{
    public required long SessionVersion { get; init; }
    public required SessionInteractionMode InteractionMode { get; init; }
    public string? ActiveSourceVersionId { get; init; }
    public string? CurrentBlockId { get; init; }
    public string? CurrentSentenceId { get; init; }
    public string? LastAcknowledgedSentenceId { get; init; }
    public ActiveLessonRefPayload? ActiveLesson { get; init; }
    public PendingQuestionRefPayload? PendingQuestion { get; init; }
    public ResultSetRefPayload? LastResultSet { get; init; }
}

public sealed record QuestionOptionPayload
{
    public required string OptionId { get; init; }
    public required string Text { get; init; }
}

// Wire form of the server's StudentFacingQuestion: structurally cannot
// carry an answer key, rubric or grading notes, because those exist only
// on the server-side-only ApprovedQuestion type. A spoken/typed answer to
// this question is an ordinary turn.submit, not a separate message type.
public sealed record QuizQuestionPayload
{
    public required string QuestionId { get; init; }
    public required int QuestionVersion { get; init; }
    public required QuizQuestionKind Kind { get; init; }
    public required string Prompt { get; init; }
    public IReadOnlyList<QuestionOptionPayload> Options { get; init; } = Array.Empty<QuestionOptionPayload>();
    public int HintsUsed { get; init; }
}

// No RequestId field on purpose: the envelope already carries request_id
// at the top level, and duplicating it here would only create a second
// place the two values could disagree.
public sealed record ErrorPayload
{
    public required ErrorCode Code { get; init; }
    public required string Message { get; init; }
    public required bool Retryable { get; init; }
    public long? CurrentSessionVersion { get; init; }
    public string? CorrelationId { get; init; }
}

// asr.transcript (D-MIC). Interim text is captioning only. One final per
// capture, after end_of_utterance, carries the whole utterance; the client
// (never the server) turns it into a voice turn.submit.
public sealed record AsrTranscriptPayload
{
    public required Guid CaptureId { get; init; }
    public required Guid TranscriptId { get; init; }
    public required string Text { get; init; }
    public required bool IsFinal { get; init; }
}
