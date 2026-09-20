namespace Netra.Desktop.Protocol.Dto;

// One record per $defs entry in
// shared/contracts/protocol/v1/client_to_server.schema.json. Property names
// are PascalCase here and mapped to the schema's snake_case wire names by
// NetraJsonSerialization's naming policy (see Protocol/NetraJsonSerialization.cs).

public sealed record SessionResumePayload
{
    public required long LastKnownSessionVersion { get; init; }
    public string? LastAcknowledgedSentenceId { get; init; }
}

public sealed record TurnSubmitPayload
{
    public required string Utterance { get; init; }
    public required InputMode InputMode { get; init; }
    public TranscriptStatus TranscriptStatus { get; init; } = TranscriptStatus.Final;
    public required long ExpectedSessionVersion { get; init; }
    public DateTimeOffset? ClientTimestamp { get; init; }
}

public sealed record NavigationCommandPayload
{
    public required NavigationCommandType Command { get; init; }
    public NavigationUnit? NavigationUnit { get; init; }
    public required long ExpectedSessionVersion { get; init; }
}

public sealed record ResponseCancelPayload
{
    public required Guid CancelRequestId { get; init; }
    public string? GenerationId { get; init; }
    public CancelReason? Reason { get; init; }
}

public sealed record PlaybackAckPayload
{
    public required string GenerationId { get; init; }
    public required string SegmentId { get; init; }
    public required string SentenceId { get; init; }
    public required PlaybackAckStatus Status { get; init; }
    public long? PlayedMs { get; init; }
}

// asr.start (D-MIC): opens one push-to-talk capture. Audio frames for
// capture_id follow only after the server answers asr.ready.
//
// capture_id is the ONLY field. client_to_server.schema.json declares
// "additionalProperties": false for AsrStart and the server's wire models
// forbid extras, so also sending a media type failed validation on every
// capture - which this client then reported to the student as "voice input
// is not available on this Netra server yet". The audio's media type belongs
// to each frame, in microphone_frame_header.schema.json, where it is sent.
public sealed record AsrStartPayload
{
    public required Guid CaptureId { get; init; }
}
