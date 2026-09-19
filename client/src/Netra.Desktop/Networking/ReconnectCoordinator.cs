using System.Net.WebSockets;
using System.Threading;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;

namespace Netra.Desktop.Networking;

// Connect, and after a drop reconnect with bounded backoff, then restore the
// session the way message-flow.md and M1's dispatcher define it:
//   1. session.resume {last_known_session_version, last_acknowledged_sentence_id}
//      -> M1 answers with session.snapshot and, if one is pending, the SAME
//      quiz.question (ConversationViewModel reconciles both);
//   2. resend the outstanding turn.submit/navigation.command under its
//      ORIGINAL request_id, so M1 replays the committed result (text only,
//      never old audio) instead of performing a second action.
// Generations from before the drop were already fenced locally by
// InterruptionController on Disconnected, so nothing old can resume audio.
//
// Retry timing is client-local policy (no wire field): exponential backoff
// with jitter, capped, and a bounded attempt count after which the client
// stays Disconnected and says so. Missing or rejected (e.g. expired)
// credentials and an invalid endpoint are not retried: they cannot succeed.
public sealed class ReconnectCoordinator : IAsyncDisposable
{
    public static readonly TimeSpan[] DefaultBackoff =
    {
        TimeSpan.FromMilliseconds(500), TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(2),
        TimeSpan.FromSeconds(4), TimeSpan.FromSeconds(8), TimeSpan.FromSeconds(15),
        TimeSpan.FromSeconds(30), TimeSpan.FromSeconds(30),
    };

    private readonly INetraWebSocketClient _socket;
    private readonly ConnectionManager _connection;
    private readonly ClientSessionState _sessionState;
    private readonly Uri _endpoint;
    private readonly IReadOnlyList<TimeSpan> _backoff;
    private readonly Func<TimeSpan, CancellationToken, Task> _delay;
    private readonly Random _jitter;
    private readonly CancellationTokenSource _stopping = new();
    private readonly object _lock = new();
    private Task? _reconnectLoop;
    private bool _disconnectedOnPurpose;

    public ReconnectCoordinator(
        INetraWebSocketClient socket,
        ConnectionManager connection,
        ClientSessionState sessionState,
        Uri endpoint,
        IReadOnlyList<TimeSpan>? backoff = null,
        Func<TimeSpan, CancellationToken, Task>? delay = null,
        Random? jitter = null)
    {
        _socket = socket;
        _connection = connection;
        _sessionState = sessionState;
        _endpoint = ServerEndpoint.Validate(endpoint);
        _backoff = backoff ?? DefaultBackoff;
        _delay = delay ?? Task.Delay;
        _jitter = jitter ?? new Random();
        _connection.Disconnected += OnConnectionLost;
        _connection.ConnectionFaulted += OnConnectionFaulted;
    }

    // Accessible status text (for the live region); never includes endpoint
    // or credential details.
    public event EventHandler<string>? StatusChanged;

    public event EventHandler<ConnectionState>? StateChanged;

    // Completes once the socket is up and session.resume plus any pending
    // resend were sent, or throws when connecting is not possible.
    public async Task ConnectAsync(CancellationToken cancellationToken)
    {
        Volatile.Write(ref _disconnectedOnPurpose, false);
        await ConnectAndRestoreAsync(cancellationToken).ConfigureAwait(false);
    }

    // A deliberate disconnect (signing out): the socket is closed cleanly and
    // not reconnected. The next ConnectAsync restores normal reconnection.
    public async Task DisconnectAsync(CancellationToken cancellationToken)
    {
        Volatile.Write(ref _disconnectedOnPurpose, true);
        await _socket.CloseAsync(cancellationToken).ConfigureAwait(false);
        _sessionState.ConnectionState = ConnectionState.Disconnected;
        StateChanged?.Invoke(this, ConnectionState.Disconnected);
    }

    public Task? CurrentReconnectLoop
    {
        get
        {
            lock (_lock)
            {
                return _reconnectLoop;
            }
        }
    }

    private void OnConnectionFaulted(object? sender, Exception ex)
    {
        // A ProtocolException for one bad frame is not a dropped connection.
        if (ex is WebSocketException or System.IO.IOException)
        {
            OnConnectionLost(sender, EventArgs.Empty);
        }
    }

    private void OnConnectionLost(object? sender, EventArgs e)
    {
        if (_stopping.IsCancellationRequested || Volatile.Read(ref _disconnectedOnPurpose))
        {
            return;
        }

        lock (_lock)
        {
            if (_reconnectLoop is { IsCompleted: false })
            {
                return;
            }

            SetState(ConnectionState.Reconnecting, "Connection lost. Reconnecting. Reading and typing controls stay available.");
            _reconnectLoop = Task.Run(() => ReconnectLoopAsync(_stopping.Token));
        }
    }

    private async Task ReconnectLoopAsync(CancellationToken cancellationToken)
    {
        for (var attempt = 0; attempt < _backoff.Count; attempt++)
        {
            var baseDelay = _backoff[attempt];
            var jittered = TimeSpan.FromMilliseconds(baseDelay.TotalMilliseconds * (0.8 + 0.4 * _jitter.NextDouble()));
            try
            {
                await _delay(jittered, cancellationToken).ConfigureAwait(false);
                await ConnectAndRestoreAsync(cancellationToken).ConfigureAwait(false);
                return;
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                return;
            }
            catch (Exception ex) when (ex is CredentialUnavailableException or InvalidServerEndpointException)
            {
                SetState(ConnectionState.Disconnected, "Cannot reconnect: this computer is not signed in to Netra. Choose Sign in under Preferences and status.");
                return;
            }
            catch (CredentialRejectedException)
            {
                SetState(ConnectionState.Disconnected, "Cannot reconnect: Netra did not accept this computer's sign-in. It may have expired. Choose Sign in under Preferences and status to enter a new access code.");
                return;
            }
            catch (Exception)
            {
                // Network failure: try again after the next backoff step.
            }
        }

        SetState(ConnectionState.Disconnected, "Could not reconnect. Your place is kept on the server; try again later.");
    }

    private async Task ConnectAndRestoreAsync(CancellationToken cancellationToken)
    {
        await _socket.ConnectAsync(_endpoint, cancellationToken).ConfigureAwait(false);
        SetState(ConnectionState.Connected, "Connected.");

        await _connection.SendSessionResumeAsync(
            new SessionResumePayload
            {
                LastKnownSessionVersion = _sessionState.SessionVersion,
                LastAcknowledgedSentenceId = _sessionState.LastAcknowledgedSentenceId,
            },
            cancellationToken).ConfigureAwait(false);

        await _connection.ResendPendingMutatingRequestAsync(cancellationToken).ConfigureAwait(false);
    }

    private void SetState(ConnectionState state, string message)
    {
        _sessionState.ConnectionState = state;
        StateChanged?.Invoke(this, state);
        StatusChanged?.Invoke(this, message);
    }

    public async ValueTask DisposeAsync()
    {
        _stopping.Cancel();
        _connection.Disconnected -= OnConnectionLost;
        _connection.ConnectionFaulted -= OnConnectionFaulted;
        var loop = CurrentReconnectLoop;
        if (loop is not null)
        {
            try
            {
                await loop.ConfigureAwait(false);
            }
            catch
            {
                // The loop reports through StatusChanged; shutdown ignores it.
            }
        }

        _stopping.Dispose();
    }
}
