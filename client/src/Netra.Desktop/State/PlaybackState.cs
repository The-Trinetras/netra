namespace Netra.Desktop.State;

public enum PlaybackStatus
{
    Idle,
    Playing,
    Paused,
    Stopped,
}

// Immutable snapshot of local playback. GenerationId is the fencing token
// used by Audio/InterruptionController to guarantee that audio tied to a
// stopped/cancelled generation never resumes.
public sealed record PlaybackSnapshot
{
    public string? GenerationId { get; init; }
    public string? SegmentId { get; init; }
    public string? SentenceId { get; init; }
    public PlaybackStatus Status { get; init; } = PlaybackStatus.Idle;
    public long PositionMs { get; init; }
}
