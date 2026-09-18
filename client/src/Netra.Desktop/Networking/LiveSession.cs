using System.Threading;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;

namespace Netra.Desktop.Networking;

// Establishing the live session, and re-establishing it on demand.
public interface ILiveSession
{
    // Creates the session (once) and connects the socket if it is not
    // connected. Safe to call repeatedly; a failed start is retried by the
    // next call instead of leaving the app unconnected until restart.
    // The token also bounds the socket's receive loop when this call
    // connects (NetraWebSocketClient links it), so pass one that lives as
    // long as the connection should, not a per-request timeout.
    Task EnsureStartedAsync(CancellationToken cancellationToken);

    // Asks the server for its authoritative session.snapshot (session.resume
    // on the open socket) and completes once one has been received, so a
    // caller can say the state is current. Throws if none arrives in time.
    Task ResynchronizeAsync(CancellationToken cancellationToken);
}

// 1. POST /v1/sessions exactly once: the server mints the id, Identity binds
//    it to the verified principal, and the returned snapshot is adopted.
// 2. /v1/ws through ReconnectCoordinator (session.resume; later drops are
//    handled by its bounded reconnect loop).
// Call from the UI thread: snapshots are applied there. Concurrent callers
// on that thread share one in-flight attempt.
public sealed class LiveSession : ILiveSession
{
    public static readonly TimeSpan DefaultResynchronizeTimeout = TimeSpan.FromSeconds(5);

    private readonly INetraApi _api;
    private readonly ClientSessionState _state;
    private readonly ConnectionManager _connection;
    private readonly Func<CancellationToken, Task> _connect;
    private readonly TimeSpan _resynchronizeTimeout;
    private Task? _starting;
    private bool _created;

    public LiveSession(
        INetraApi api,
        ClientSessionState state,
        ConnectionManager connection,
        Func<CancellationToken, Task> connect,
        TimeSpan? resynchronizeTimeout = null)
    {
        _api = api;
        _state = state;
        _connection = connection;
        _connect = connect;
        _resynchronizeTimeout = resynchronizeTimeout ?? DefaultResynchronizeTimeout;
    }

    public Task EnsureStartedAsync(CancellationToken cancellationToken)
    {
        if (_starting is { IsCompleted: false } inFlight)
        {
            return inFlight;
        }

        _starting = StartAsync(cancellationToken);
        return _starting;
    }

    private async Task StartAsync(CancellationToken cancellationToken)
    {
        if (!_created)
        {
            var created = await _api.CreateSessionAsync(cancellationToken);
            SnapshotReconciler.Apply(_state, created.Snapshot, created.SessionId);
            _created = true;
        }

        // Reconnecting: the coordinator's own loop owns the socket right now.
        if (_state.ConnectionState == ConnectionState.Disconnected)
        {
            await _connect(cancellationToken);
        }
    }

    public async Task ResynchronizeAsync(CancellationToken cancellationToken)
    {
        var snapshot = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);

        // Subscribed after ConversationViewModel (composed first), so by the
        // time this fires the snapshot has already been applied to state.
        void OnMessage(object? sender, ServerToClientEnvelope envelope)
        {
            if (envelope.Type == ServerMessageType.SessionSnapshot)
            {
                snapshot.TrySetResult();
            }
        }

        _connection.MessageReceived += OnMessage;
        try
        {
            await _connection.SendSessionResumeAsync(
                new SessionResumePayload
                {
                    LastKnownSessionVersion = _state.SessionVersion,
                    LastAcknowledgedSentenceId = _state.LastAcknowledgedSentenceId,
                },
                cancellationToken);
            await snapshot.Task.WaitAsync(_resynchronizeTimeout, cancellationToken);
        }
        finally
        {
            _connection.MessageReceived -= OnMessage;
        }
    }
}
