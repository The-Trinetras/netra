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
    private ClientWebSocket? _socket;
    private CancellationTokenSource? _receiveLoopCts;
    private Task? _receiveLoop;

    public bool IsConnected => _socket?.State == WebSocketState.Open;

    public event EventHandler<string>? TextMessageReceived;
    public event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
    public event EventHandler<Exception>? ConnectionFaulted;
    public event EventHandler? Disconnected;

    public async Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken)
    {
        var socket = new ClientWebSocket();
        await socket.ConnectAsync(endpoint, cancellationToken).ConfigureAwait(false);
        _socket = socket;

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
        await socket.SendAsync(bytes, WebSocketMessageType.Text, endOfMessage: true, cancellationToken)
            .ConfigureAwait(false);
    }

    public async Task CloseAsync(CancellationToken cancellationToken)
    {
        _receiveLoopCts?.Cancel();

        if (_socket is { State: WebSocketState.Open } socket)
        {
            await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "client_shutdown", cancellationToken)
                .ConfigureAwait(false);
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
