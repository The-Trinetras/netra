namespace Netra.Desktop.State;

public enum PlaybackStatus
{
    Idle,

    // Play() was requested and the player is opening the media; nothing is
    // audible yet, so it is not reported (or acknowledged) as playing. It is
    // still the active speaking response for STOP and press-to-interrupt.
    Loading,
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
