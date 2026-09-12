using System.Text;
using System.Text.Json;
using System.Threading;
using Netra.Desktop.Audio;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;
using Xunit;

namespace Netra.Desktop.Tests;

// Exercises the actual receive-path gate (BinaryAudioFrameProcessor), not
// just the standalone parser: "Only frames belonging to the explicitly
// admitted active generation may enter playback. Receiving a frame for an
// unknown generation MUST NOT automatically make that generation active."
public sealed class BinaryAudioFrameProcessorTests
{
    [Fact]
    public void FrameForAnUnadmittedGenerationIsNeverAdmitted()
    {
        var (processor, admitted, _, _) = Build();

        processor.OnBinaryMessageReceived(null, Frame("gen-1", sequence: 0));

        Assert.Empty(admitted);
    }

    [Fact]
    public void FrameForTheExplicitlyAdmittedGenerationIsAdmitted()
    {
        var (processor, admitted, _, _) = Build();

        processor.AdmitGeneration("gen-1");
        processor.OnBinaryMessageReceived(null, Frame("gen-1", sequence: 0));

        Assert.Single(admitted);
        Assert.Equal("gen-1", admitted[0].Header.GenerationId);
    }

    [Fact]
    public void FrameForADifferentGenerationThanTheAdmittedOneIsDropped()
    {
        var (processor, admitted, _, _) = Build();

        processor.AdmitGeneration("gen-1");
        processor.OnBinaryMessageReceived(null, Frame("gen-2", sequence: 0));

        Assert.Empty(admitted);
    }

    [Fact]
    public void DuplicateSequenceWithinTheAdmittedGenerationIsDropped()
    {
        var (processor, admitted, _, _) = Build();

        processor.AdmitGeneration("gen-1");
        processor.OnBinaryMessageReceived(null, Frame("gen-1", sequence: 3));
        processor.OnBinaryMessageReceived(null, Frame("gen-1", sequence: 3));

        Assert.Single(admitted);
    }

    [Fact]
    public void MalformedFrameIsDroppedWithoutThrowing()
    {
        var (processor, admitted, _, _) = Build();
        processor.AdmitGeneration("gen-1");

        var exception = Record.Exception(() => processor.OnBinaryMessageReceived(null, new byte[] { 0, 0 }));

        Assert.Null(exception);
        Assert.Empty(admitted);
    }

    [Fact]
    public async Task StoppingFencesTheGenerationSoLateFramesAreRejectedEvenIfStillAdmitted()
    {
        var (processor, admitted, interruptionController, playbackController) = Build();
        processor.AdmitGeneration("gen-1");
        playbackController.SetSnapshot(new PlaybackSnapshot { GenerationId = "gen-1", Status = PlaybackStatus.Playing });

        await interruptionController.StopAsync(CancelReason.UserStop, CancellationToken.None);

        // GenerationFenced clears admission, but even without that a fenced
        // generation must never be admitted again via ShouldPlay.
        processor.OnBinaryMessageReceived(null, Frame("gen-1", sequence: 1));

        Assert.Empty(admitted);
    }

    [Fact]
    public void DisconnectClearsAdmissionSoALateFrameForTheOldGenerationIsRejected()
    {
        var sessionState = new ClientSessionState();
        sessionState.Initialize(Guid.NewGuid(), sessionVersion: 1);
        var socket = new FakeWebSocketClient();
        var connectionManager = new ConnectionManager(socket, sessionState);
        var playbackController = new FakePlaybackController();
        playbackController.SetSnapshot(new PlaybackSnapshot { GenerationId = "gen-1", Status = PlaybackStatus.Playing });
        var interruptionController = new InterruptionController(playbackController, connectionManager);
        var processor = new BinaryAudioFrameProcessor(interruptionController);
        var admitted = new List<(AudioFrameHeader Header, ReadOnlyMemory<byte> AudioBytes)>();
        processor.AudioBytesAdmitted += (_, e) => admitted.Add(e);
        interruptionController.GenerationFenced += (_, _) => processor.ClearActiveGeneration();

        processor.AdmitGeneration("gen-1");
        socket.RaiseDisconnected();

        processor.OnBinaryMessageReceived(null, Frame("gen-1", sequence: 0));

        Assert.Empty(admitted);
    }

    private static (
        BinaryAudioFrameProcessor Processor,
        List<(AudioFrameHeader Header, ReadOnlyMemory<byte> AudioBytes)> Admitted,
        InterruptionController InterruptionController,
        FakePlaybackController PlaybackController) Build()
    {
        var sessionState = new ClientSessionState();
        sessionState.Initialize(Guid.NewGuid(), sessionVersion: 1);
        var connectionManager = new ConnectionManager(new FakeWebSocketClient(), sessionState);
        var playbackController = new FakePlaybackController();
        var interruptionController = new InterruptionController(playbackController, connectionManager);
        var processor = new BinaryAudioFrameProcessor(interruptionController);
        var admitted = new List<(AudioFrameHeader Header, ReadOnlyMemory<byte> AudioBytes)>();
        processor.AudioBytesAdmitted += (_, e) => admitted.Add(e);
        return (processor, admitted, interruptionController, playbackController);
    }

    private static byte[] Frame(string generationId, long sequence)
    {
        var header = JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["version"] = 1,
            ["generation_id"] = generationId,
            ["segment_id"] = "seg-1",
            ["sequence"] = sequence,
            ["end_of_segment"] = false,
            ["end_of_generation"] = false,
            ["media_type"] = "audio/mpeg",
        });
        var headerBytes = Encoding.UTF8.GetBytes(header);
        var length = headerBytes.Length;
        var prefix = new byte[] { (byte)(length >> 24), (byte)(length >> 16), (byte)(length >> 8), (byte)length };
        return prefix.Concat(headerBytes).ToArray();
    }

    private sealed class FakePlaybackController : IPlaybackController
    {
        public PlaybackSnapshot CurrentSnapshot { get; private set; } = new();

        public event EventHandler<PlaybackSnapshot>? SnapshotChanged;
        public event EventHandler<string>? PlaybackCompleted;

        public void SetSnapshot(PlaybackSnapshot snapshot)
        {
            CurrentSnapshot = snapshot;
            SnapshotChanged?.Invoke(this, snapshot);
        }

        public void Play(Uri audioSource, string generationId, string segmentId, string sentenceId)
        {
        }

        public void Pause() => CurrentSnapshot = CurrentSnapshot with { Status = PlaybackStatus.Paused };

        public bool Resume() => false;

        public void StopImmediately() => CurrentSnapshot = CurrentSnapshot with { Status = PlaybackStatus.Stopped };

        public void RaisePlaybackCompleted(string generationId) => PlaybackCompleted?.Invoke(this, generationId);
    }

    private sealed class FakeWebSocketClient : INetraWebSocketClient
    {
        public bool IsConnected => false;

        public event EventHandler<string>? TextMessageReceived;
        public event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
        public event EventHandler<Exception>? ConnectionFaulted;
        public event EventHandler? Disconnected;

        public void RaiseDisconnected() => Disconnected?.Invoke(this, EventArgs.Empty);

        public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendTextAsync(string message, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task CloseAsync(CancellationToken cancellationToken) => Task.CompletedTask;

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
}
