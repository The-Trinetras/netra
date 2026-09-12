using System.Text.Json.Serialization;

namespace Netra.Desktop.Protocol.Dto;

// Wire values below are copied exactly from
// shared/contracts/protocol/v1/client_to_server.schema.json and
// shared/contracts/protocol/v1/server_to_client.schema.json. Do not rename
// members without a corresponding shared contract version change.

[JsonConverter(typeof(JsonStringEnumConverter<ClientMessageType>))]
public enum ClientMessageType
{
    [JsonStringEnumMemberName("session.resume")]
    SessionResume,

    [JsonStringEnumMemberName("turn.submit")]
    TurnSubmit,

    [JsonStringEnumMemberName("navigation.command")]
    NavigationCommand,

    [JsonStringEnumMemberName("response.cancel")]
    ResponseCancel,

    [JsonStringEnumMemberName("playback.ack")]
    PlaybackAck,
}

[JsonConverter(typeof(JsonStringEnumConverter<ServerMessageType>))]
public enum ServerMessageType
{
    [JsonStringEnumMemberName("session.snapshot")]
    SessionSnapshot,

    [JsonStringEnumMemberName("response.segment")]
    ResponseSegment,

    [JsonStringEnumMemberName("quiz.question")]
    QuizQuestion,

    [JsonStringEnumMemberName("error")]
    Error,
}

[JsonConverter(typeof(JsonStringEnumConverter<InputMode>))]
public enum InputMode
{
    [JsonStringEnumMemberName("keyboard")]
    Keyboard,

    [JsonStringEnumMemberName("voice")]
    Voice,
}

[JsonConverter(typeof(JsonStringEnumConverter<TranscriptStatus>))]
public enum TranscriptStatus
{
    // The schema only ever allows the constant "final" — a turn.submit may
    // not carry an interim transcript. See CLAUDE.md: "Only final speech
    // transcript should become a submitted turn."
    [JsonStringEnumMemberName("final")]
    Final,
}

// Deterministic navigation commands (CLAUDE.md "Deterministic commands").
// These must be handled without an LLM call whenever intent is unambiguous.
[JsonConverter(typeof(JsonStringEnumConverter<NavigationCommandType>))]
public enum NavigationCommandType
{
    [JsonStringEnumMemberName("stop")]
    Stop,

    [JsonStringEnumMemberName("pause")]
    Pause,

    [JsonStringEnumMemberName("continue")]
    Continue,

    [JsonStringEnumMemberName("next")]
    Next,

    [JsonStringEnumMemberName("previous")]
    Previous,

    [JsonStringEnumMemberName("repeat")]
    Repeat,

    [JsonStringEnumMemberName("where_am_i")]
    WhereAmI,

    [JsonStringEnumMemberName("back_to_reading")]
    BackToReading,

    [JsonStringEnumMemberName("undo_jump")]
    UndoJump,

    [JsonStringEnumMemberName("return_to_question")]
    ReturnToQuestion,
}

[JsonConverter(typeof(JsonStringEnumConverter<NavigationUnit>))]
public enum NavigationUnit
{
    [JsonStringEnumMemberName("sentence")]
    Sentence,

    [JsonStringEnumMemberName("block")]
    Block,

    [JsonStringEnumMemberName("heading")]
    Heading,

    [JsonStringEnumMemberName("figure")]
    Figure,

    [JsonStringEnumMemberName("equation")]
    Equation,
}

[JsonConverter(typeof(JsonStringEnumConverter<CancelReason>))]
public enum CancelReason
{
    [JsonStringEnumMemberName("user_stop")]
    UserStop,

    [JsonStringEnumMemberName("new_turn")]
    NewTurn,

    [JsonStringEnumMemberName("navigation")]
    Navigation,

    [JsonStringEnumMemberName("client_shutdown")]
    ClientShutdown,
}

[JsonConverter(typeof(JsonStringEnumConverter<PlaybackAckStatus>))]
public enum PlaybackAckStatus
{
    [JsonStringEnumMemberName("started")]
    Started,

    [JsonStringEnumMemberName("progress")]
    Progress,

    [JsonStringEnumMemberName("completed")]
    Completed,
}

// Canonical interaction-mode vocabulary approved 2026-09-12 (see
// docs/architecture/message-flow.md and data-ownership.md). This is a wire
// enum: it appears in session.snapshot. Deliberately excludes "listening"
// (client-local PlaybackStatus territory, not learning-flow state),
// "answering"/"waiting_for_answer" (derivable from a non-null
// PendingQuestion within TutorLesson/Quiz) and "navigation" (deterministic
// commands complete synchronously; nothing persists a "navigating" mode).
// ConnectionState is a SEPARATE, client-local-only concept — see
// State/ClientSessionState.cs — and never appears on the wire, so it has no
// enum here.
[JsonConverter(typeof(JsonStringEnumConverter<SessionInteractionMode>))]
public enum SessionInteractionMode
{
    [JsonStringEnumMemberName("idle")]
    Idle,

    [JsonStringEnumMemberName("reading")]
    Reading,

    [JsonStringEnumMemberName("tutor_lesson")]
    TutorLesson,

    [JsonStringEnumMemberName("quiz")]
    Quiz,
}

// Wire error codes for the server_to_client `error` payload. Mirrors
// shared/contracts/protocol/v1/error.schema.json and
// api/src/netra_api/platform/errors.py's ErrorCode namespace exactly.
[JsonConverter(typeof(JsonStringEnumConverter<ErrorCode>))]
public enum ErrorCode
{
    [JsonStringEnumMemberName("AUTH_REQUIRED")]
    AuthRequired,

    [JsonStringEnumMemberName("AUTHORIZATION_DENIED")]
    AuthorizationDenied,

    [JsonStringEnumMemberName("SESSION_VERSION_CONFLICT")]
    SessionVersionConflict,

    [JsonStringEnumMemberName("REQUEST_ID_CONFLICT")]
    RequestIdConflict,

    [JsonStringEnumMemberName("INVALID_REQUEST")]
    InvalidRequest,

    [JsonStringEnumMemberName("UNSUPPORTED_PROTOCOL_VERSION")]
    UnsupportedProtocolVersion,

    [JsonStringEnumMemberName("STALE_REQUEST")]
    StaleRequest,

    [JsonStringEnumMemberName("RESOURCE_UNAVAILABLE")]
    ResourceUnavailable,

    [JsonStringEnumMemberName("PROVIDER_UNAVAILABLE")]
    ProviderUnavailable,

    [JsonStringEnumMemberName("INTERNAL_ERROR")]
    InternalError,
}

// Quiz question kind, reused verbatim from the existing student-facing
// vocabulary (client_to_server.schema.json's questions carry the same
// values via netra_api.learning.quiz.models.QuestionKind) rather than a
// second parallel enum.
[JsonConverter(typeof(JsonStringEnumConverter<QuizQuestionKind>))]
public enum QuizQuestionKind
{
    [JsonStringEnumMemberName("multiple_choice")]
    MultipleChoice,

    [JsonStringEnumMemberName("true_false")]
    TrueFalse,

    [JsonStringEnumMemberName("short_answer")]
    ShortAnswer,

    [JsonStringEnumMemberName("free_response")]
    FreeResponse,
}
