using System.Net;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using Netra.Desktop.Audio;
using Netra.Desktop.Networking;
using Netra.Desktop.Speech;
using Netra.Desktop.State;
using Netra.Desktop.Threading;
using Netra.Desktop.ViewModels;
using Xunit;

namespace Netra.Desktop.Tests;

// Push-to-talk over a real loopback WebSocket (a .NET HttpListener playing
// the server's side of the D-MIC proposal; not M1's server, which does not
// implement C1 yet). The real client stack is used end to end: socket,
// ConnectionManager, MicrophoneCapture, ConversationViewModel. Only the
// microphone is simulated.
public sealed class VoiceLoopbackTests
{
    [Fact]
    public async Task PressSpeakReleaseTravelsAsFramesAndTheFinalComesBackAsOneVoiceTurn()
    {
        var port = FreePort();
        using var listener = new HttpListener();
        listener.Prefixes.Add($"http://localhost:{port}/v1/ws/");
        listener.Start();
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(15));

        var spoken = Pcm.Samples(2000, 7); // 4000 bytes: one full frame and a partial one
        string? authorization = null;
        var framesSeen = new List<SentMicrophoneFrame>();
        JsonElement turn = default;
        var server = Task.Run(async () =>
        {
            var context = await listener.GetContextAsync();
            authorization = context.Request.Headers["Authorization"];
            var ws = (await context.AcceptWebSocketAsync(subProtocol: null)).WebSocket;

            var start = await ReceiveTextAsync(ws, timeout.Token);
            Assert.Equal("asr.start", start.GetProperty("type").GetString());
            var requestId = Guid.Parse(start.GetProperty("request_id").GetString()!);
            var captureId = start.GetProperty("payload").GetProperty("capture_id").GetGuid();
            Assert.Equal("audio/L16;rate=16000", start.GetProperty("payload").GetProperty("media_type").GetString());
            await SendTextAsync(ws, ConversationVoiceTests.Envelope("asr.ready", requestId, new { capture_id = captureId }), timeout.Token);

            while (true)
            {
                var frame = MicrophoneFrameReader.Read(await ReceiveBinaryAsync(ws, timeout.Token));
                framesSeen.Add(frame);
                if (frame.EndOfUtterance)
                {
                    break;
                }
            }

            await SendTextAsync(ws, ConversationVoiceTests.Envelope("asr.transcript", requestId, new
            {
                capture_id = captureId,
                transcript_id = Guid.NewGuid(),
                text = "Explain the second row of the table.",
                is_final = true,
            }), timeout.Token);

            turn = await ReceiveTextAsync(ws, timeout.Token);
            await ws.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, "done", CancellationToken.None);
        });

        var state = new ClientSessionState();
        state.Initialize(Guid.NewGuid(), sessionVersion: 4);
        var socket = new NetraWebSocketClient(new TestCredential("voice-test-token"));
        await using var connection = new ConnectionManager(socket, state);
        var player = new SilentPlayer();
        var interruption = new InterruptionController(player, connection);
        var pcm = new FakePcmSource();
        using var mic = new MicrophoneCapture(connection, pcm);
        using var viewModel = new ConversationViewModel(
            state, connection, player, interruption, new BinaryAudioFrameProcessor(interruption), mic, new SynchronousUiDispatcher());

        await connection.ConnectAsync(new Uri($"ws://localhost:{port}/v1/ws/"), timeout.Token);
        await mic.StartListeningAsync(timeout.Token);
        await Eventually.TrueAsync(() => mic.IsRecognitionAvailable, "the server accepts the capture");
        Assert.True(pcm.Speak(spoken));
        mic.StopListening();
        await server.WaitAsync(timeout.Token);

        Assert.Equal("Bearer voice-test-token", authorization);
        Assert.Equal(Enumerable.Range(0, framesSeen.Count).Select(i => (long)i), framesSeen.Select(f => f.Sequence));
        Assert.Equal(spoken, framesSeen.SelectMany(f => MicrophoneFrameReader.ToLittleEndian(f.Audio)).ToArray());
        Assert.Equal("turn.submit", turn.GetProperty("type").GetString());
        var payload = turn.GetProperty("payload");
        Assert.Equal("Explain the second row of the table.", payload.GetProperty("utterance").GetString());
        Assert.Equal("voice", payload.GetProperty("input_mode").GetString());
        Assert.Equal(4, payload.GetProperty("expected_session_version").GetInt64());
    }

    private static async Task<JsonElement> ReceiveTextAsync(WebSocket ws, CancellationToken token)
    {
        var (type, bytes) = await ReceiveAsync(ws, token);
        Assert.Equal(WebSocketMessageType.Text, type);
        return JsonDocument.Parse(bytes).RootElement.Clone();
    }

    private static async Task<byte[]> ReceiveBinaryAsync(WebSocket ws, CancellationToken token)
    {
        var (type, bytes) = await ReceiveAsync(ws, token);
        Assert.Equal(WebSocketMessageType.Binary, type);
        return bytes;
    }

    private static async Task<(WebSocketMessageType Type, byte[] Bytes)> ReceiveAsync(WebSocket ws, CancellationToken token)
    {
        using var message = new MemoryStream();
        var buffer = new byte[8192];
        WebSocketReceiveResult result;
        do
        {
            result = await ws.ReceiveAsync(buffer, token);
            message.Write(buffer, 0, result.Count);
        }
        while (!result.EndOfMessage);

        return (result.MessageType, message.ToArray());
    }

    private static Task SendTextAsync(WebSocket ws, string json, CancellationToken token) =>
        ws.SendAsync(Encoding.UTF8.GetBytes(json), WebSocketMessageType.Text, true, token);

    private static int FreePort()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        return port;
    }

    private sealed class TestCredential(string token) : ICredentialSource
    {
        public ValueTask<string?> GetBearerTokenAsync(CancellationToken cancellationToken) => ValueTask.FromResult<string?>(token);
    }

    private sealed class SilentPlayer : IPlaybackController
    {
        public PlaybackSnapshot CurrentSnapshot { get; private set; } = new();

        public event EventHandler<PlaybackSnapshot>? SnapshotChanged;
        public event EventHandler<string>? PlaybackCompleted;

        public void Play(Uri audioSource, string generationId, string segmentId, string sentenceId)
        {
        }

        public void Pause()
        {
        }

        public bool Resume() => false;

        public void StopImmediately()
        {
        }
    }
}
