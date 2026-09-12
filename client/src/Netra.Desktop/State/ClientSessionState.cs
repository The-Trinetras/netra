using Netra.Desktop.Protocol.Dto;

namespace Netra.Desktop.State;

// Client-local transport state, deliberately separate from
// SessionInteractionMode (Protocol/Dto/Enums.cs): a disconnected student
// may still read cached content, so connection state must never be folded
// into learning-flow mode, and this enum therefore never appears on the
// wire (approved 2026-09-12; see docs/architecture/data-ownership.md).
public enum ConnectionState
{
    Connected,
    Reconnecting,
    Disconnected,
}

// Client-side mirror of the fields CLAUDE.md's "Session rules" require a
// session to track. PostgreSQL (via the Session service) remains
// authoritative; this is a local projection used to build outgoing messages
// and reconcile server-confirmed state. Nothing here is written back to the
// database directly.
public sealed class ClientSessionState
{
    private readonly object _lock = new();
    private readonly HashSet<Guid> _appliedRequestIds = new();
    private long _sessionVersion;

    public Guid SessionId { get; private set; }

    // TODO: populated from the server's session.snapshot payload once that
    // payload shape is defined in shared/contracts (see
    // Protocol/Dto/ServerPayloads.cs).
    public string? AccountContextId { get; set; }

    public string? ActiveSourceVersionId { get; set; }
    public string? CurrentBlockId { get; set; }
    public string? CurrentSentenceId { get; set; }
    public string? LastAcknowledgedSentenceId { get; set; }

    // Canonical vocabulary (Protocol/Dto/Enums.cs), approved 2026-09-12.
    // Reconciled from session.snapshot on connect/reconnect.
    public SessionInteractionMode InteractionMode { get; set; } = SessionInteractionMode.Idle;

    // Transport-local; never derived from or sent to the server. Owned by
    // whatever drives ConnectionManager's connect/reconnect lifecycle.
    public ConnectionState ConnectionState { get; set; } = ConnectionState.Disconnected;

    public string? ActiveTutorLessonId { get; set; }
    public string? PendingQuestionId { get; set; }

    // Reference only, matching the server's ResultSetRef design (decision
    // E, 2026-09-12): the ordered result list itself lives server-side,
    // resolved by result_set_id when the student says "open the third one".
    public Guid? LastResultSetId { get; set; }

    // "Client must track request/generation identifiers."
    public Guid? LastRequestId { get; set; }
    public string? CurrentGenerationId { get; set; }

    public long SessionVersion
    {
        get
        {
            lock (_lock)
            {
                return _sessionVersion;
            }
        }
    }

    public void Initialize(Guid sessionId, long sessionVersion)
    {
        lock (_lock)
        {
            SessionId = sessionId;
            _sessionVersion = sessionVersion;
        }
    }

    // Navigation/turn mutations must be built against the current
    // SessionVersion ("Navigation mutations must use expected session
    // versions"). Returns false without applying the update when newVersion
    // does not move the counter forward, leaving the caller to treat it as a
    // stale or conflicting update from the server.
    public bool TryAdvanceSessionVersion(long newVersion)
    {
        lock (_lock)
        {
            if (newVersion <= _sessionVersion)
            {
                return false;
            }

            _sessionVersion = newVersion;
            return true;
        }
    }

    // Guards against applying the same server-confirmed mutation twice on
    // redelivery ("Duplicate request IDs must not apply the same mutation
    // twice").
    public bool TryMarkApplied(Guid requestId)
    {
        lock (_lock)
        {
            return _appliedRequestIds.Add(requestId);
        }
    }
}
