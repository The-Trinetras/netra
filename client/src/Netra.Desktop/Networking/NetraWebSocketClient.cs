using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;

namespace Netra.Desktop.Networking;

// Transport-level abstraction over the single control WebSocket. Carries
// JSON protocol envelopes only. Per CLAUDE.md protocol rules ("Audio is not
// transported as large base64 JSON payloads"), binary frames are exposed
// separately via BinaryMessageReceived rather than folded into JSON text
// messages; routing them to playback is a TODO until the binary audio
// framing is defined (see the receive loop below).
public interface INetraWebSocketClient : IAsyncDisposable
{
    bool IsConnected { get; }

    event EventHandler<string>? TextMessageReceived;
    event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
    event EventHandler<Exception>? ConnectionFaulted;
    event EventHandler? Disconnected;

    Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken);
    Task SendTextAsync(string message, CancellationToken cancellationToken);
    Task CloseAsync(CancellationToken cancellationToken);
}

public sealed class NetraWebSocketClient : INetraWebSocketClient
{
    private readonly ICredentialSource? _credentials;

    // WebSocket's documented contract is one outstanding send at a time.
    // Sends come from independent paths (navigation/turns, STOP's
    // response.cancel, timer-driven playback acks, session.resume). The
    // current managed ClientWebSocket happens to serialize sends internally,
    // so no failure was observed; this keeps the client within the contract
    // instead of relying on that implementation detail.
    private readonly SemaphoreSlim _sendLock = new(1, 1);
    private ClientWebSocket? _socket;
    private CancellationTokenSource? _receiveLoopCts;
    private Task? _receiveLoop;

    public bool IsConnected => _socket?.State == WebSocketState.Open;

    public event EventHandler<string>? TextMessageReceived;
    public event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
    public event EventHandler<Exception>? ConnectionFaulted;
    public event EventHandler? Disconnected;

    // Without a credential source the client never connects: M1 verifies the
    // bearer credential before accepting the upgrade, and an anonymous
    // attempt could only be refused.
    public NetraWebSocketClient(ICredentialSource? credentials = null)
    {
        _credentials = credentials;
    }

    public async Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken)
    {
        ServerEndpoint.Validate(endpoint);
        var token = _credentials is null ? null : await _credentials.GetBearerTokenAsync(cancellationToken).ConfigureAwait(false);
        if (string.IsNullOrWhiteSpace(token))
        {
            throw new CredentialUnavailableException();
        }

        var socket = new ClientWebSocket();
        // Header only (M1/M5-reviewed presentation); never the URL.
        socket.Options.SetRequestHeader("Authorization", "Bearer " + token);
        try
        {
            await socket.ConnectAsync(endpoint, cancellationToken).ConfigureAwait(false);
        }
        catch
        {
            socket.Dispose();
            throw;
        }

        // A reconnect replaces a dead socket: stop its receive loop and
        // release it rather than leaking one socket per reconnect.
        var previousSocket = _socket;
        var previousLoopCts = _receiveLoopCts;
        _socket = socket;
        previousLoopCts?.Cancel();
        previousSocket?.Dispose();
        previousLoopCts?.Dispose();

        _receiveLoopCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _receiveLoop = ReceiveLoopAsync(socket, _receiveLoopCts.Token);
    }

    public async Task SendTextAsync(string message, CancellationToken cancellationToken)
    {
        if (_socket is not { State: WebSocketState.Open } socket)
        {
            throw new InvalidOperationException("WebSocket is not connected.");
        }

        var bytes = Encoding.UTF8.GetBytes(message);
        await _sendLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            await socket.SendAsync(bytes, WebSocketMessageType.Text, endOfMessage: true, cancellationToken)
                .ConfigureAwait(false);
        }
        finally
        {
            _sendLock.Release();
        }
    }

    public async Task CloseAsync(CancellationToken cancellationToken)
    {
        _receiveLoopCts?.Cancel();

        if (_socket is { State: WebSocketState.Open } socket)
        {
            await _sendLock.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "client_shutdown", cancellationToken)
                    .ConfigureAwait(false);
            }
            finally
            {
                _sendLock.Release();
            }
        }

        Disconnected?.Invoke(this, EventArgs.Empty);
    }

    private async Task ReceiveLoopAsync(ClientWebSocket socket, CancellationToken cancellationToken)
    {
        var buffer = new byte[8192];
        using var messageBuffer = new MemoryStream();

        try
        {
            while (!cancellationToken.IsCancellationRequested && socket.State == WebSocketState.Open)
            {
                messageBuffer.SetLength(0);
                WebSocketReceiveResult result;
                do
                {
                    result = await socket.ReceiveAsync(buffer, cancellationToken).ConfigureAwait(false);
                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        Disconnected?.Invoke(this, EventArgs.Empty);
                        return;
                    }

                    messageBuffer.Write(buffer, 0, result.Count);
                }
                while (!result.EndOfMessage);

                if (result.MessageType == WebSocketMessageType.Text)
                {
                    TextMessageReceived?.Invoke(this, Encoding.UTF8.GetString(messageBuffer.ToArray()));
                }
                else
                {
                    BinaryMessageReceived?.Invoke(this, messageBuffer.ToArray());
                }
            }
        }
        catch (OperationCanceledException)
        {
            // Expected during shutdown; not an error.
        }
        catch (Exception ex)
        {
            ConnectionFaulted?.Invoke(this, ex);
        }
    }

    public async ValueTask DisposeAsync()
    {
        _receiveLoopCts?.Cancel();

        if (_receiveLoop is not null)
        {
            try
            {
                await _receiveLoop.ConfigureAwait(false);
            }
            catch
            {
                // Already surfaced via ConnectionFaulted.
            }
        }

        _socket?.Dispose();
        _receiveLoopCts?.Dispose();
    }
}
