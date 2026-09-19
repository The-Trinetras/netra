using System.Threading;
using Netra.Desktop.Protocol;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.Speech;
using Netra.Desktop.State;

namespace Netra.Desktop.Networking;

// Owns the outbound sequence counter and wires the WebSocket transport to
// the typed message layer. Session/request identifiers are tracked on
// ClientSessionState per CLAUDE.md ("Client must track request/generation
// identifiers").
public sealed class ConnectionManager : IAsyncDisposable, IAsrChannel
{
    private readonly INetraWebSocketClient _webSocketClient;
    private readonly ClientSessionState _sessionState;
    private long _outboundSequence;

    // Tracks the one outstanding turn.submit/navigation.command awaiting a
    // response, keyed by its ORIGINAL request_id (decision F, 2026-09-12).
    // Only these two message types get this treatment: "Allow one active
    // speaking turn per session" already means at most one such mutation is
    // ever meaningfully outstanding, so one slot suffices without a queue.
    private readonly object _pendingLock = new();
    private (Guid RequestId, Func<Guid, long, ClientToServerEnvelope> Build)? _pendingMutatingRequest;

    public ConnectionManager(INetraWebSocketClient webSocketClient, ClientSessionState sessionState)
    {
        _webSocketClient = webSocketClient;
        _sessionState = sessionState;
        _webSocketClient.TextMessageReceived += OnTextMessageReceived;
        _webSocketClient.ConnectionFaulted += OnConnectionFaulted;
        _webSocketClient.Disconnected += OnDisconnected;
    }

    public event EventHandler<ServerToClientEnvelope>? MessageReceived;
    public event EventHandler<Exception>? ConnectionFaulted;

    // Forwarded so higher layers (InterruptionController) can fence the
    // current generation on disconnect without depending on the transport
    // interface directly.
    public event EventHandler? Disconnected;

    public bool IsConnected => _webSocketClient.IsConnected;

    public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken) =>
        _webSocketClient.ConnectAsync(endpoint, cancellationToken);

    // The request id of the latest session.resume: the snapshot answering it
    // is a restored place, unlike snapshots that answer navigation.
    public Guid? LastResumeRequestId { get; private set; }

    public Task SendSessionResumeAsync(SessionResumePayload payload, CancellationToken cancellationToken)
    {
        var requestId = Guid.NewGuid();
        LastResumeRequestId = requestId;
        return SendAsync(
            requestId,
            (id, sequence) => MessageFactory.CreateSessionResume(_sessionState.SessionId, id, sequence, payload),
            cancellationToken);
    }

    public Task SendTurnSubmitAsync(TurnSubmitPayload payload, CancellationToken cancellationToken) =>
        SendMutatingAsync((requestId, sequence) =>
            MessageFactory.CreateTurnSubmit(_sessionState.SessionId, requestId, sequence, payload), cancellationToken);

    public Task SendNavigationCommandAsync(NavigationCommandPayload payload, CancellationToken cancellationToken) =>
        SendMutatingAsync((requestId, sequence) =>
            MessageFactory.CreateNavigationCommand(_sessionState.SessionId, requestId, sequence, payload), cancellationToken);

    // Resends the outstanding turn.submit/navigation.command, if any, under
    // its ORIGINAL request_id — never a new one. A caller invokes this
    // after reconnecting, before minting any new user action, so the server
    // recognises a genuine retry (replay_or_conflict) instead of seeing a
    // second logical action. Returns false when nothing is pending, which
    // is the normal case whenever the prior action already completed.
    public async Task<bool> ResendPendingMutatingRequestAsync(CancellationToken cancellationToken)
    {
        (Guid RequestId, Func<Guid, long, ClientToServerEnvelope> Build)? pending;
        lock (_pendingLock)
        {
            pending = _pendingMutatingRequest;
        }

        if (pending is not { } request)
        {
            return false;
        }

        await SendAsync(request.RequestId, request.Build, cancellationToken).ConfigureAwait(false);
        return true;
    }

    public Task SendResponseCancelAsync(ResponseCancelPayload payload, CancellationToken cancellationToken) =>
        SendAsync((requestId, sequence) =>
            MessageFactory.CreateResponseCancel(_sessionState.SessionId, requestId, sequence, payload), cancellationToken);

    public Task SendPlaybackAckAsync(PlaybackAckPayload payload, CancellationToken cancellationToken) =>
        SendAsync((requestId, sequence) =>
            MessageFactory.CreatePlaybackAck(_sessionState.SessionId, requestId, sequence, payload), cancellationToken);

    // One capture is one logical action: its request_id correlates the
    // server's asr.ready, asr.transcript and any error. Not a session
    // mutation, so it is never resent after a reconnect (the capture ends).
    // The caller mints request_id first, so a reply that races this send's
    // completion still matches its capture.
    public Task SendAsrStartAsync(Guid requestId, AsrStartPayload payload, CancellationToken cancellationToken) =>
        SendAsync(
            requestId,
            (id, sequence) => MessageFactory.CreateAsrStart(_sessionState.SessionId, id, sequence, payload),
            cancellationToken);

    public Task SendMicrophoneFrameAsync(ReadOnlyMemory<byte> frame, CancellationToken cancellationToken) =>
        _webSocketClient.SendBinaryAsync(frame, cancellationToken);

    // message_id is minted fresh per transmission (inside MessageFactory);
    // request_id is minted once per logical action and passed in here,
    // reused verbatim on a retransmit via ResendPendingMutatingRequestAsync.
    private Task SendAsync(
        Func<Guid, long, ClientToServerEnvelope> buildEnvelope, CancellationToken cancellationToken) =>
        SendAsync(Guid.NewGuid(), buildEnvelope, cancellationToken);

    private async Task SendAsync(
        Guid requestId, Func<Guid, long, ClientToServerEnvelope> buildEnvelope, CancellationToken cancellationToken)
    {
        var sequence = Interlocked.Increment(ref _outboundSequence);
        _sessionState.LastRequestId = requestId;

        var envelope = buildEnvelope(requestId, sequence);
        await _webSocketClient.SendTextAsync(MessageFactory.Serialize(envelope), cancellationToken)
            .ConfigureAwait(false);
    }

    // turn.submit/navigation.command only (decision F). Records the
    // request_id as pending BEFORE sending, so a drop between building and
    // transmitting still leaves a correct record to retry.
    private async Task SendMutatingAsync(
        Func<Guid, long, ClientToServerEnvelope> buildEnvelope, CancellationToken cancellationToken)
    {
        var requestId = Guid.NewGuid();
        lock (_pendingLock)
        {
            _pendingMutatingRequest = (requestId, buildEnvelope);
        }

        await SendAsync(requestId, buildEnvelope, cancellationToken).ConfigureAwait(false);
    }

    private void ClearPendingMutatingRequestIfMatching(Guid requestId)
    {
        lock (_pendingLock)
        {
            if (_pendingMutatingRequest is { } pending && pending.RequestId == requestId)
            {
                _pendingMutatingRequest = null;
            }
        }
    }

    private void OnTextMessageReceived(object? sender, string json)
    {
        try
        {
            var envelope = MessageParser.ParseEnvelope(json);
            // Any response correlating to a pending mutation — success or
            // a SESSION_VERSION_CONFLICT/REQUEST_ID_CONFLICT error alike —
            // ends that action: a further response to it is not expected,
            // and a subsequent client action must mint a new request_id.
            ClearPendingMutatingRequestIfMatching(envelope.RequestId);
            MessageReceived?.Invoke(this, envelope);
        }
        catch (ProtocolException ex)
        {
            ConnectionFaulted?.Invoke(this, ex);
        }
    }

    private void OnConnectionFaulted(object? sender, Exception ex) => ConnectionFaulted?.Invoke(this, ex);

    private void OnDisconnected(object? sender, EventArgs e) => Disconnected?.Invoke(this, EventArgs.Empty);

    public async ValueTask DisposeAsync()
    {
        _webSocketClient.TextMessageReceived -= OnTextMessageReceived;
        _webSocketClient.ConnectionFaulted -= OnConnectionFaulted;
        _webSocketClient.Disconnected -= OnDisconnected;
        await _webSocketClient.DisposeAsync().ConfigureAwait(false);
    }
}
