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
