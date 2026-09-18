using System.Net;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using Netra.Desktop.Networking;
using Xunit;

namespace Netra.Desktop.Tests;

// A real ClientWebSocket against a loopback HttpListener WebSocket: the
// bearer-header connect, concurrent sends from independent paths (as the app
// does: commands, STOP's response.cancel, timer-driven acks) and close.
// NOTE: this passes with or without NetraWebSocketClient's send lock, because
// the current managed ClientWebSocket serializes sends internally; it checks
// the behaviour, it is not a reproduction of a defect.
public sealed class WebSocketSendSerializationTests
{
    [Fact]
    public async Task ConcurrentSendsAllArriveIntactInsteadOfFaulting()
    {
        var port = FreePort();
        using var listener = new HttpListener();
        listener.Prefixes.Add($"http://localhost:{port}/");
        listener.Start();

        var received = new List<string>();
        var server = Task.Run(async () =>
        {
            var context = await listener.GetContextAsync();
            var accepted = await context.AcceptWebSocketAsync(subProtocol: null);
            var buffer = new byte[64 * 1024];
            // NetraWebSocketClient.CloseAsync cancels its receive loop before
            // closing, which aborts the socket without a close handshake
            // (pre-existing; recorded in integration-status.md). Every message
            // was received by then, so the abrupt end is tolerated here.
            try
            {
            while (accepted.WebSocket.State == WebSocketState.Open)
            {
                using var message = new MemoryStream();
                WebSocketReceiveResult result;
                do
                {
                    result = await accepted.WebSocket.ReceiveAsync(buffer, CancellationToken.None);
                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        await accepted.WebSocket.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, null, CancellationToken.None);
                        return;
                    }

                    message.Write(buffer, 0, result.Count);
                }
                while (!result.EndOfMessage);

                lock (received)
                {
                    received.Add(Encoding.UTF8.GetString(message.ToArray()));
                }
            }
            }
            catch (WebSocketException)
            {
            }
        });

        await using var client = new NetraWebSocketClient(new NetraApiClientTests.FixedCredentials("test-token-not-real"));
        await client.ConnectAsync(new Uri($"ws://localhost:{port}/v1/ws"), CancellationToken.None);

        // Large enough that each send spans several socket writes.
        var payload = new string('x', 256 * 1024);
        var sends = Enumerable.Range(0, 24)
            .Select(i => Task.Run(() => client.SendTextAsync($"{i}:{payload}", CancellationToken.None)))
            .ToArray();
        await Task.WhenAll(sends);

        await client.CloseAsync(CancellationToken.None);
        await server.WaitAsync(TimeSpan.FromSeconds(10));

        Assert.Equal(24, received.Count);
        Assert.All(received, message => Assert.EndsWith(payload, message));
        Assert.Equal(Enumerable.Range(0, 24).Select(i => i.ToString()), received.Select(m => m.Split(':')[0]).OrderBy(int.Parse));
    }

    private static int FreePort()
    {
        var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        return port;
    }
}
