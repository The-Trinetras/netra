using System.Threading;
using Netra.Desktop.Protocol;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;

namespace Netra.Desktop.Networking;

// Owns the outbound sequence counter and wires the WebSocket transport to
// the typed message layer. Session/request identifiers are tracked on
// ClientSessionState per CLAUDE.md ("Client must track request/generation
// identifiers").
public sealed class ConnectionManager : IAsyncDisposable
{
    private readonly INetraWebSocketClient _webSocketClient;
    private readonly ClientSessionState _sessionState;
    private long _outboundSequence;

    public ConnectionManager(INetraWebSocketClient webSocketClient, ClientSessionState sessionState)
    {
        _webSocketClient = webSocketClient;
        _sessionState = sessionState;
        _webSocketClient.TextMessageReceived += OnTextMessageReceived;
        _webSocketClient.ConnectionFaulted += OnConnectionFaulted;
    }

    public event EventHandler<ServerToClientEnvelope>? MessageReceived;
    public event EventHandler<Exception>? ConnectionFaulted;

    public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken) =>
        _webSocketClient.ConnectAsync(endpoint, cancellationToken);

    public Task SendSessionResumeAsync(SessionResumePayload payload, CancellationToken cancellationToken) =>
        SendAsync((requestId, sequence) =>
            MessageFactory.CreateSessionResume(_sessionState.SessionId, requestId, sequence, payload), cancellationToken);

    public Task SendTurnSubmitAsync(TurnSubmitPayload payload, CancellationToken cancellationToken) =>
        SendAsync((requestId, sequence) =>
            MessageFactory.CreateTurnSubmit(_sessionState.SessionId, requestId, sequence, payload), cancellationToken);

    public Task SendNavigationCommandAsync(NavigationCommandPayload payload, CancellationToken cancellationToken) =>
        SendAsync((requestId, sequence) =>
            MessageFactory.CreateNavigationCommand(_sessionState.SessionId, requestId, sequence, payload), cancellationToken);

    public Task SendResponseCancelAsync(ResponseCancelPayload payload, CancellationToken cancellationToken) =>
        SendAsync((requestId, sequence) =>
            MessageFactory.CreateResponseCancel(_sessionState.SessionId, requestId, sequence, payload), cancellationToken);

    public Task SendPlaybackAckAsync(PlaybackAckPayload payload, CancellationToken cancellationToken) =>
        SendAsync((requestId, sequence) =>
            MessageFactory.CreatePlaybackAck(_sessionState.SessionId, requestId, sequence, payload), cancellationToken);

    private async Task SendAsync(
        Func<Guid, long, ClientToServerEnvelope> buildEnvelope, CancellationToken cancellationToken)
    {
        var requestId = Guid.NewGuid();
        var sequence = Interlocked.Increment(ref _outboundSequence);
        _sessionState.LastRequestId = requestId;

        var envelope = buildEnvelope(requestId, sequence);
        await _webSocketClient.SendTextAsync(MessageFactory.Serialize(envelope), cancellationToken)
            .ConfigureAwait(false);
    }

    private void OnTextMessageReceived(object? sender, string json)
    {
        try
        {
            var envelope = MessageParser.ParseEnvelope(json);
            MessageReceived?.Invoke(this, envelope);
        }
        catch (ProtocolException ex)
        {
            ConnectionFaulted?.Invoke(this, ex);
        }
    }

    private void OnConnectionFaulted(object? sender, Exception ex) => ConnectionFaulted?.Invoke(this, ex);

    public async ValueTask DisposeAsync()
    {
        _webSocketClient.TextMessageReceived -= OnTextMessageReceived;
        _webSocketClient.ConnectionFaulted -= OnConnectionFaulted;
        await _webSocketClient.DisposeAsync().ConfigureAwait(false);
    }
}
