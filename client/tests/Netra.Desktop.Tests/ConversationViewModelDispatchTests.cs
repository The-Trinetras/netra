using System.Threading;
using Netra.Desktop.Audio;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.Speech;
using Netra.Desktop.State;
using Netra.Desktop.Threading;
using Netra.Desktop.ViewModels;
using Xunit;

namespace Netra.Desktop.Tests;

// Covers two real gaps the scaffold left open (see docs/team/handoffs/M5.md):
//  1. ConnectionManager.MessageReceived fires from the WebSocket receive
//     loop thread; every UI mutation it triggers must go through
//     IUiDispatcher, not touch the ObservableCollection directly.
//  2. response.segment must call BinaryAudioFrameProcessor.AdmitGeneration
//     so a subsequently-received binary frame for that generation can ever
//     be admitted — nothing did this before.
public sealed class ConversationViewModelDispatchTests
{
    [Fact]
    public async Task ResponseSegmentFromBackgroundThread_IsAppliedThroughTheDispatcher()
    {
        var (viewModel, socket, dispatcher, _) = Build();

        // Simulate the real delivery path: the message arrives on a thread
        // that is not the test's calling thread, exactly like the WebSocket
        // receive loop.
        await Task.Run(() => socket.RaiseTextMessageReceived(ResponseSegmentJson("gen-1", "Hello.", final: false)));

        Assert.True(dispatcher.InvokeCount > 0);
        Assert.Contains(viewModel.Transcript, line => line.Speaker == "Tutor" && line.Text == "Hello.");
    }

    [Fact]
    public async Task ResponseSegment_AdmitsGenerationSoALaterAudioFrameIsAccepted()
    {
        var (viewModel, socket, _, binaryAudioFrameProcessor) = Build();
        _ = viewModel;

        socket.RaiseTextMessageReceived(ResponseSegmentJson("gen-42", "Explanation text.", final: false));

        (AudioFrameHeader Header, ReadOnlyMemory<byte> AudioBytes)? admitted = null;
        binaryAudioFrameProcessor.AudioBytesAdmitted += (_, frame) => admitted = frame;

        var frame = BuildAudioFrame("gen-42", sequence: 1);
        socket.RaiseBinaryMessageReceived(frame);

        Assert.NotNull(admitted);
        Assert.Equal("gen-42", admitted!.Value.Header.GenerationId);
    }

    [Fact]
    public void ResponseSegment_DoesNotReAdmitTheSameStillActiveGeneration()
    {
        var (_, socket, _, binaryAudioFrameProcessor) = Build();

        socket.RaiseTextMessageReceived(ResponseSegmentJson("gen-7", "First.", final: false));

        // Admit sequence 1 for gen-7, then a second response.segment for the
        // SAME generation must not reset the sequence tracker — sequence 1
        // must remain rejected as a duplicate/decreasing sequence.
        socket.RaiseBinaryMessageReceived(BuildAudioFrame("gen-7", sequence: 1));
        socket.RaiseTextMessageReceived(ResponseSegmentJson("gen-7", "Second.", final: false));

        var admittedCount = 0;
        binaryAudioFrameProcessor.AudioBytesAdmitted += (_, _) => admittedCount++;

        socket.RaiseBinaryMessageReceived(BuildAudioFrame("gen-7", sequence: 1));

        Assert.Equal(0, admittedCount);
    }

    private static (ConversationViewModel ViewModel, RecordingWebSocketClient Socket, RecordingUiDispatcher Dispatcher, BinaryAudioFrameProcessor Processor) Build()
    {
        var sessionState = new ClientSessionState();
        sessionState.Initialize(Guid.NewGuid(), sessionVersion: 1);
        var socket = new RecordingWebSocketClient();
        var connectionManager = new ConnectionManager(socket, sessionState);
        var playbackController = new NoOpPlaybackController();
        var interruptionController = new InterruptionController(playbackController, connectionManager);
        var binaryAudioFrameProcessor = new BinaryAudioFrameProcessor(interruptionController);
        socket.BinaryMessageReceived += binaryAudioFrameProcessor.OnBinaryMessageReceived;
        var speechInputService = new MicrophoneCapture();
        var dispatcher = new RecordingUiDispatcher();

        var viewModel = new ConversationViewModel(
            sessionState, connectionManager, playbackController, interruptionController,
            binaryAudioFrameProcessor, speechInputService, dispatcher);

        return (viewModel, socket, dispatcher, binaryAudioFrameProcessor);
    }

    private static byte[] BuildAudioFrame(string generationId, long sequence)
    {
        var headerJson = $$"""
            {"version":1,"generation_id":"{{generationId}}","segment_id":"seg-1","sequence":{{sequence}},"end_of_segment":false,"end_of_generation":false,"media_type":"audio/opus"}
            """;
        var headerBytes = System.Text.Encoding.UTF8.GetBytes(headerJson);
        var audioBytes = new byte[] { 1, 2, 3, 4 };

        var lengthPrefix = new byte[4];
        lengthPrefix[0] = (byte)(headerBytes.Length >> 24);
        lengthPrefix[1] = (byte)(headerBytes.Length >> 16);
        lengthPrefix[2] = (byte)(headerBytes.Length >> 8);
        lengthPrefix[3] = (byte)headerBytes.Length;

        var frame = new byte[lengthPrefix.Length + headerBytes.Length + audioBytes.Length];
        lengthPrefix.CopyTo(frame, 0);
        headerBytes.CopyTo(frame, lengthPrefix.Length);
        audioBytes.CopyTo(frame, lengthPrefix.Length + headerBytes.Length);
        return frame;
    }

    private static string ResponseSegmentJson(string generationId, string text, bool final) =>
        $$"""
        {
          "protocol_version": "1.0",
          "message_id": "{{Guid.NewGuid()}}",
          "session_id": "{{Guid.NewGuid()}}",
          "request_id": "{{Guid.NewGuid()}}",
          "sequence": 1,
          "type": "response.segment",
          "payload": {
            "generation_id": "{{generationId}}",
            "segment_id": "seg-1",
            "sentence_id": "sentence-1",
            "text": "{{text}}",
            "final": {{(final ? "true" : "false")}}
          }
        }
        """;

    private sealed class RecordingUiDispatcher : IUiDispatcher
    {
        public int InvokeCount { get; private set; }

        public void Invoke(Action action)
        {
            InvokeCount++;
            action();
        }
    }

    private sealed class NoOpPlaybackController : IPlaybackController
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

        public void StopImmediately() =>
            CurrentSnapshot = CurrentSnapshot with { Status = PlaybackStatus.Stopped };
    }

    private sealed class RecordingWebSocketClient : INetraWebSocketClient
    {
        public bool IsConnected => true;

        public event EventHandler<string>? TextMessageReceived;
        public event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
        public event EventHandler<Exception>? ConnectionFaulted;
        public event EventHandler? Disconnected;

        public void RaiseTextMessageReceived(string json) => TextMessageReceived?.Invoke(this, json);

        public void RaiseBinaryMessageReceived(byte[] frame) => BinaryMessageReceived?.Invoke(this, frame);

        public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendTextAsync(string message, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task CloseAsync(CancellationToken cancellationToken) => Task.CompletedTask;

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
}
