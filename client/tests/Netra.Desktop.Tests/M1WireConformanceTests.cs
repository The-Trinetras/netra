using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using Netra.Desktop.Audio;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.Speech;
using Netra.Desktop.State;
using Netra.Desktop.Threading;
using Netra.Desktop.ViewModels;
using Xunit;

namespace Netra.Desktop.Tests;

// Cross-language conformance against bytes M1's real server code produced:
// Fixtures/m1_navigation_next_with_audio.json was captured from M1's actual
// transport/serializer/frame encoder (codex/m1-coordinator, commit recorded
// in the file) driving M1's labelled Ohm fixture journey. The audio bytes
// are fixture placeholders, so this proves wire compatibility and client
// handling, not decodable or audible speech.
public sealed class M1WireConformanceTests
{
    private sealed record WireMessage(string Kind, string? Text, byte[]? Binary);

    private static (string ProducerCommit, List<WireMessage> Messages) LoadTranscript()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "Fixtures", "m1_navigation_next_with_audio.json");
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        var messages = document.RootElement.GetProperty("messages").EnumerateArray()
            .Select(m => m.GetProperty("kind").GetString() == "text"
                ? new WireMessage("text", m.GetProperty("text").GetString(), null)
                : new WireMessage("binary", null, Convert.FromBase64String(m.GetProperty("base64").GetString()!)))
            .ToList();
        return (document.RootElement.GetProperty("producer_commit").GetString()!, messages);
    }

    [Fact]
    public void EveryM1TextEnvelopeAndAudioFrameParsesStrictly()
    {
        var (commit, messages) = LoadTranscript();
        Assert.Equal(40, commit.Length);

        var types = new List<ServerMessageType>();
        foreach (var message in messages)
        {
            if (message.Kind == "text")
            {
                var envelope = MessageParser.ParseEnvelope(message.Text!);
                types.Add(envelope.Type);
                switch (envelope.Type)
                {
                    case ServerMessageType.SessionSnapshot:
                        MessageParser.ParseSessionSnapshot(envelope);
                        break;
                    case ServerMessageType.ResponseSegment:
                        MessageParser.ParseResponseSegment(envelope);
                        break;
                }
            }
            else
            {
                BinaryAudioFrame.Parse(message.Binary!);
            }
        }

        Assert.Contains(ServerMessageType.SessionSnapshot, types);
        Assert.Contains(ServerMessageType.ResponseSegment, types);
        Assert.Contains(messages, m => m.Kind == "binary");
    }

    [Fact]
    public void M1SendsSegmentTextBeforeItsAudio()
    {
        // The client admits a generation from response.segment; if audio
        // could precede its segment, every first frame would be dropped.
        var (_, messages) = LoadTranscript();
        var firstSegment = messages.FindIndex(m => m.Kind == "text" && m.Text!.Contains("\"response.segment\""));
        var firstFrame = messages.FindIndex(m => m.Kind == "binary");
        Assert.True(firstSegment >= 0 && firstSegment < firstFrame);
    }

    [Fact]
    public async Task M1JourneyDrivesTheClientPipelineToPlaybackAndAcknowledgement()
    {
        var (_, messages) = LoadTranscript();
        var socket = new CapturingSocket();
        var sessionState = new ClientSessionState();
        sessionState.Initialize(SessionIdOf(messages), sessionVersion: 10);
        var connection = new ConnectionManager(socket, sessionState);
        var player = new ScriptedPlayer();
        var store = new RecordingAudioStore();
        var interruption = new InterruptionController(player, connection, () => sessionState.CurrentGenerationId);
        var processor = new BinaryAudioFrameProcessor(interruption);
        var assembler = new SegmentAudioAssembler();
        var queue = new SegmentPlaybackQueue(player, interruption, store);
        processor.AudioBytesAdmitted += assembler.OnAudioBytesAdmitted;
        assembler.SegmentCompleted += (_, s) => queue.Enqueue(s);
        socket.BinaryMessageReceived += processor.OnBinaryMessageReceived;
        using var acknowledger = new PlaybackAcknowledger(player, connection);
        using var viewModel = new ConversationViewModel(
            sessionState, connection, player, interruption, processor, new MicrophoneCapture(connection), new SynchronousUiDispatcher(), queue);

        foreach (var message in messages)
        {
            if (message.Kind == "text")
            {
                socket.ReceiveText(message.Text!);
            }
            else
            {
                socket.ReceiveBinary(message.Binary!);
            }
        }

        var segment = messages.Where(m => m.Kind == "text")
            .Select(m => MessageParser.ParseEnvelope(m.Text!))
            .Where(e => e.Type == ServerMessageType.ResponseSegment)
            .Select(MessageParser.ParseResponseSegment)
            .Single();

        Assert.Equal(11, sessionState.SessionVersion);
        Assert.Contains(viewModel.Transcript, line => line.Text == segment.Text);
        var played = Assert.Single(player.Played);
        Assert.Equal((segment.GenerationId, segment.SegmentId, segment.SentenceId), (played.GenerationId, played.SegmentId, played.SentenceId));
        Assert.Equal("audio/mpeg", store.Staged[0].MediaType);

        player.Open();
        player.End();
        await Task.Yield();

        var acks = socket.Sent.Where(s => s.Contains("\"playback.ack\"")).ToList();
        Assert.Equal(2, acks.Count); // started (on open) and completed (on end); nothing on Loading
        Assert.All(acks, ack => Assert.Contains($"\"sentence_id\":\"{segment.SentenceId}\"", ack));
    }

    [Fact]
    public async Task RealLoopbackWebSocketCarriesTheBearerHeaderAndM1FramesUnchanged()
    {
        var (_, messages) = LoadTranscript();
        var port = FreePort();
        using var listener = new HttpListener();
        listener.Prefixes.Add($"http://localhost:{port}/v1/ws/");
        listener.Start();

        string? authorization = null;
        string? rawUrl = null;
        var serverTask = Task.Run(async () =>
        {
            var context = await listener.GetContextAsync();
            authorization = context.Request.Headers["Authorization"];
            rawUrl = context.Request.RawUrl;
            var ws = (await context.AcceptWebSocketAsync(subProtocol: null)).WebSocket;
            foreach (var message in messages)
            {
                if (message.Kind == "text")
                {
                    await ws.SendAsync(Encoding.UTF8.GetBytes(message.Text!), WebSocketMessageType.Text, true, CancellationToken.None);
                }
                else
                {
                    await ws.SendAsync(message.Binary!, WebSocketMessageType.Binary, true, CancellationToken.None);
                }
            }

            await ws.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, "done", CancellationToken.None);
        });

        var received = new List<WireMessage>();
        var closed = new TaskCompletionSource();
        await using var client = new NetraWebSocketClient(new StaticCredential("test-token-123"));
        client.TextMessageReceived += (_, text) => { lock (received) { received.Add(new WireMessage("text", text, null)); } };
        client.BinaryMessageReceived += (_, bytes) => { lock (received) { received.Add(new WireMessage("binary", null, bytes.ToArray())); } };
        client.Disconnected += (_, _) => closed.TrySetResult();
        client.ConnectionFaulted += (_, ex) => closed.TrySetException(ex);

        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        await client.ConnectAsync(new Uri($"ws://localhost:{port}/v1/ws/"), timeout.Token);
        await closed.Task.WaitAsync(timeout.Token);
        await serverTask.WaitAsync(timeout.Token);

        Assert.Equal("Bearer test-token-123", authorization);
        Assert.DoesNotContain("test-token-123", rawUrl);
        Assert.Equal(messages.Count, received.Count);
        for (var i = 0; i < messages.Count; i++)
        {
            Assert.Equal(messages[i].Kind, received[i].Kind);
            Assert.Equal(messages[i].Text, received[i].Text);
            Assert.Equal(messages[i].Binary, received[i].Binary);
        }
    }

    [Fact]
    public async Task NoCredentialMeansNoConnectionAttempt()
    {
        await using var anonymous = new NetraWebSocketClient();
        await Assert.ThrowsAsync<CredentialUnavailableException>(
            () => anonymous.ConnectAsync(new Uri("ws://localhost:1/v1/ws"), CancellationToken.None));

        await using var empty = new NetraWebSocketClient(new StaticCredential(null));
        await Assert.ThrowsAsync<CredentialUnavailableException>(
            () => empty.ConnectAsync(new Uri("ws://localhost:1/v1/ws"), CancellationToken.None));
    }

    [Theory]
    [InlineData("wss://netra.example/v1/ws", true)]
    [InlineData("ws://localhost:8000/v1/ws", true)]
    [InlineData("ws://127.0.0.1:8000/v1/ws", true)]
    [InlineData("ws://netra.example/v1/ws", false)]
    [InlineData("wss://netra.example/v1/ws?token=abc", false)]
    [InlineData("wss://user:secret@netra.example/v1/ws", false)]
    [InlineData("https://netra.example/v1/ws", false)]
    public void EndpointPolicyKeepsCredentialsOutOfUrls(string endpoint, bool valid)
    {
        if (valid)
        {
            ServerEndpoint.Validate(new Uri(endpoint));
        }
        else
        {
            Assert.Throws<InvalidServerEndpointException>(() => ServerEndpoint.Validate(new Uri(endpoint)));
        }
    }

    private static Guid SessionIdOf(List<WireMessage> messages) =>
        MessageParser.ParseEnvelope(messages.First(m => m.Kind == "text").Text!).SessionId;

    private static int FreePort()
    {
        var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        return port;
    }

    private sealed class StaticCredential : ICredentialSource
    {
        private readonly string? _token;

        public StaticCredential(string? token) => _token = token;

        public ValueTask<string?> GetBearerTokenAsync(CancellationToken cancellationToken) => ValueTask.FromResult(_token);
    }
}
