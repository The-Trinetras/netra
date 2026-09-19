using System.Text.Json;
using Netra.Desktop.Audio;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.Speech;
using Netra.Desktop.State;
using Netra.Desktop.Threading;
using Netra.Desktop.ViewModels;
using Xunit;

namespace Netra.Desktop.Tests;

// The conversation's side of push-to-talk, over the real ConnectionManager
// and MicrophoneCapture with server messages as wire JSON: one accepted
// final becomes exactly one voice turn.submit, what was heard is read back,
// nothing is announced while the microphone is open, and voice failures are
// told in voice input's own words.
public sealed class ConversationVoiceTests
{
    [Fact]
    public async Task ASpokenQuestionBecomesOneVoiceTurnAndTheTypedDraftIsKept()
    {
        var rig = Rig.Create();
        rig.ViewModel.InputText = "half typed";

        var (requestId, captureId) = await rig.PressAndAcceptAsync();
        rig.Pcm.Speak(Pcm.Samples(320, 1));
        rig.Mic.StopListening();
        await Eventually.TrueAsync(() => rig.Socket.BinarySent.Count > 0, "the end frame is sent");
        rig.Socket.Receive(Transcript(requestId, captureId, "What is Ohm's law?", isFinal: true));
        await Eventually.TrueAsync(() => rig.Socket.Sent("turn.submit").Count == 1, "the turn is sent");
        rig.Socket.Receive(Transcript(requestId, captureId, "What is Ohm's law?", isFinal: true));
        await Eventually.SettleAsync();

        var turn = Assert.Single(rig.Socket.Sent("turn.submit")).GetProperty("payload");
        Assert.Equal("What is Ohm's law?", turn.GetProperty("utterance").GetString());
        Assert.Equal("voice", turn.GetProperty("input_mode").GetString());
        Assert.Equal("final", turn.GetProperty("transcript_status").GetString());
        Assert.Equal("half typed", rig.ViewModel.InputText);
        var line = rig.ViewModel.Transcript.Last();
        Assert.Equal("You (voice)", line.Speaker);
        Assert.Equal("What is Ohm's law?", line.Text);
        Assert.Equal("Heard: What is Ohm's law?", rig.ViewModel.StatusMessage);
    }

    [Fact]
    public async Task InterimTextIsShownButNeitherSubmittedNorAnnounced()
    {
        var rig = Rig.Create();
        var (requestId, captureId) = await rig.PressAndAcceptAsync();

        rig.Socket.Receive(Transcript(requestId, captureId, "what is", isFinal: false));
        await Eventually.SettleAsync();

        Assert.Equal("what is", rig.ViewModel.InterimTranscript);
        Assert.Empty(rig.Socket.Sent("turn.submit"));
        Assert.Equal(string.Empty, rig.ViewModel.StatusMessage);
    }

    [Fact]
    public async Task ListeningIsShownButNotAnnounced()
    {
        var rig = Rig.Create();

        await rig.PressAndAcceptAsync();

        Assert.Equal("Listening.", rig.ViewModel.VoiceStatus);
        Assert.Equal(string.Empty, rig.ViewModel.StatusMessage);
    }

    [Fact]
    public async Task ARefusalIsToldInVoiceInputsWordsNotAsTheRawServerError()
    {
        var rig = Rig.Create();
        await rig.Mic.StartListeningAsync(CancellationToken.None);
        var start = Assert.Single(rig.Socket.Sent("asr.start"));
        var requestId = Guid.Parse(start.GetProperty("request_id").GetString()!);

        rig.Socket.Receive(Error(requestId, "INVALID_REQUEST", "invalid message"));

        Assert.Equal("Voice input is not available on this Netra server yet. Type your question instead.", rig.ViewModel.StatusMessage);
        Assert.Empty(rig.Socket.BinarySent);
    }

    [Fact]
    public async Task StopDuringCaptureDiscardsItAndSaysStoppedEvenIfTheCancelCannotBeSent()
    {
        var rig = Rig.Create();
        var (requestId, captureId) = await rig.PressAndAcceptAsync();
        rig.Socket.FailTextSends = true;

        rig.ViewModel.StopCommand.Execute(null);
        await Eventually.TrueAsync(() => rig.ViewModel.StatusMessage == "Stopped.", "STOP is confirmed");
        rig.Socket.Receive(Transcript(requestId, captureId, "discarded", isFinal: true));
        await Eventually.SettleAsync();

        Assert.True(rig.Pcm.Aborted);
        Assert.Empty(rig.Socket.Sent("turn.submit"));
        Assert.Equal("Stopped.", rig.ViewModel.StatusMessage);
    }

    [Fact]
    public async Task ATypedQuestionThatCannotBeSentSaysSo()
    {
        var rig = Rig.Create();
        rig.Socket.FailTextSends = true;
        rig.ViewModel.InputText = "What is resistance?";

        rig.ViewModel.SubmitCommand.Execute(null);
        await Eventually.TrueAsync(() => rig.ViewModel.StatusMessage.Length > 0, "the failure is announced");

        Assert.Equal(
            "Not connected to Netra, so Netra could not send your question. Your place is kept; try again when connected.",
            rig.ViewModel.StatusMessage);
    }

    private static string Transcript(Guid requestId, Guid captureId, string text, bool isFinal) =>
        Envelope("asr.transcript", requestId, new
        {
            capture_id = captureId,
            transcript_id = Guid.NewGuid(),
            text,
            is_final = isFinal,
        });

    private static string Error(Guid requestId, string code, string message) =>
        Envelope("error", requestId, new { code, message, retryable = false });

    internal static string Envelope(string type, Guid requestId, object payload) =>
        JsonSerializer.Serialize(new
        {
            protocol_version = "1.0",
            message_id = Guid.NewGuid(),
            session_id = Guid.NewGuid(),
            request_id = requestId,
            sequence = 1,
            type,
            payload,
        });

    private sealed record Rig(ConversationViewModel ViewModel, MicrophoneCapture Mic, ScriptedSocket Socket, FakePcmSource Pcm)
    {
        public static Rig Create()
        {
            var state = new ClientSessionState();
            state.Initialize(Guid.NewGuid(), sessionVersion: 3);
            var socket = new ScriptedSocket();
            var connection = new ConnectionManager(socket, state);
            var player = new SilentPlayer();
            var interruption = new InterruptionController(player, connection);
            var processor = new BinaryAudioFrameProcessor(interruption);
            var pcm = new FakePcmSource();
            var mic = new MicrophoneCapture(connection, pcm, delay: (_, token) => Task.Delay(Timeout.Infinite, token));
            var viewModel = new ConversationViewModel(
                state, connection, player, interruption, processor, mic, new SynchronousUiDispatcher());
            return new Rig(viewModel, mic, socket, pcm);
        }

        public async Task<(Guid RequestId, Guid CaptureId)> PressAndAcceptAsync()
        {
            await Mic.StartListeningAsync(CancellationToken.None);
            var start = Assert.Single(Socket.Sent("asr.start"));
            var requestId = Guid.Parse(start.GetProperty("request_id").GetString()!);
            var captureId = start.GetProperty("payload").GetProperty("capture_id").GetGuid();
            Socket.Receive(Envelope("asr.ready", requestId, new { capture_id = captureId }));
            return (requestId, captureId);
        }
    }

    private sealed class ScriptedSocket : INetraWebSocketClient
    {
        private readonly List<string> _text = new();

        public bool FailTextSends { get; set; }
        public List<byte[]> BinarySent { get; } = new();
        public bool IsConnected => true;

        public event EventHandler<string>? TextMessageReceived;
        public event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
        public event EventHandler<Exception>? ConnectionFaulted;
        public event EventHandler? Disconnected;

        public void Receive(string json) => TextMessageReceived?.Invoke(this, json);

        public List<JsonElement> Sent(string type)
        {
            lock (_text)
            {
                return _text.Select(t => JsonDocument.Parse(t).RootElement)
                    .Where(e => e.GetProperty("type").GetString() == type)
                    .ToList();
            }
        }

        public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendTextAsync(string message, CancellationToken cancellationToken)
        {
            if (FailTextSends)
            {
                return Task.FromException(new NotConnectedException());
            }

            lock (_text)
            {
                _text.Add(message);
            }

            return Task.CompletedTask;
        }

        public Task SendBinaryAsync(ReadOnlyMemory<byte> message, CancellationToken cancellationToken)
        {
            lock (BinarySent)
            {
                BinarySent.Add(message.ToArray());
            }

            return Task.CompletedTask;
        }

        public Task CloseAsync(CancellationToken cancellationToken) => Task.CompletedTask;

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
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

        public void StopImmediately() => CurrentSnapshot = CurrentSnapshot with { Status = PlaybackStatus.Stopped };
    }
}
