using System.Threading;
using Netra.Desktop.Audio;
using Netra.Desktop.Networking;
using Netra.Desktop.State;
using Xunit;

namespace Netra.Desktop.Tests;

// The audit found the 200ms position timer re-sending "started" forever,
// so the server could never tell first-audio from mid-playback and never
// saw a "progress" ack at all.
public sealed class PlaybackAcknowledgerTests
{
    [Fact]
    public void FirstSnapshotOfASegmentAcksStartedAndLaterOnesAckProgress()
    {
        var (playback, socket, acknowledger) = Build();
        using var _ = acknowledger;

        playback.SetSnapshot(Playing("gen-1", "seg-1", positionMs: 0));
        playback.SetSnapshot(Playing("gen-1", "seg-1", positionMs: 200));
        playback.SetSnapshot(Playing("gen-1", "seg-1", positionMs: 400));

        Assert.Equal(1, socket.CountContaining("\"started\""));
        Assert.Equal(2, socket.CountContaining("\"progress\""));
    }

    [Fact]
    public void EachSegmentGetsItsOwnStartedAck()
    {
        var (playback, socket, acknowledger) = Build();
        using var _ = acknowledger;

        playback.SetSnapshot(Playing("gen-1", "seg-1", positionMs: 0));
        playback.SetSnapshot(Playing("gen-1", "seg-1", positionMs: 200));
        playback.SetSnapshot(Playing("gen-1", "seg-2", positionMs: 0));

        Assert.Equal(2, socket.CountContaining("\"started\""));
    }

    [Fact]
    public void StoppedPlaybackIsNotAcknowledgedAsCompleted()
    {
        // Acknowledging an interrupted segment as completed would tell the
        // server the student heard content they stopped.
        var (playback, socket, acknowledger) = Build();
        using var _ = acknowledger;

        playback.SetSnapshot(Playing("gen-1", "seg-1", positionMs: 0));
        playback.SetSnapshot(Playing("gen-1", "seg-1", positionMs: 200) with
        {
            Status = PlaybackStatus.Stopped,
        });

        Assert.Equal(0, socket.CountContaining("\"completed\""));
    }

    [Fact]
    public void PausedPlaybackIsNotAcknowledged()
    {
        var (playback, socket, acknowledger) = Build();
        using var _ = acknowledger;

        playback.SetSnapshot(Playing("gen-1", "seg-1", positionMs: 0));
        var before = socket.SentMessages.Count;

        playback.SetSnapshot(Playing("gen-1", "seg-1", positionMs: 200) with
        {
            Status = PlaybackStatus.Paused,
        });

        Assert.Equal(before, socket.SentMessages.Count);
    }

    [Fact]
    public void AnIdleSnapshotBeforeAnySegmentStartedIsNotACompletion()
    {
        var (playback, socket, acknowledger) = Build();
        using var _ = acknowledger;

        playback.SetSnapshot(new PlaybackSnapshot
        {
            GenerationId = "gen-1",
            SegmentId = "seg-1",
            SentenceId = "sentence-1",
            Status = PlaybackStatus.Idle,
        });

        Assert.Equal(0, socket.CountContaining("\"completed\""));
    }

    [Fact]
    public void SegmentThatStartedAndEndedAcksCompletedOnce()
    {
        var (playback, socket, acknowledger) = Build();
        using var _ = acknowledger;

        playback.SetSnapshot(Playing("gen-1", "seg-1", positionMs: 0));
        playback.SetSnapshot(Playing("gen-1", "seg-1", positionMs: 200) with
        {
            Status = PlaybackStatus.Idle,
        });

        Assert.Equal(1, socket.CountContaining("\"completed\""));
    }

    private static PlaybackSnapshot Playing(string generationId, string segmentId, long positionMs) =>
        new()
        {
            GenerationId = generationId,
            SegmentId = segmentId,
            SentenceId = "sentence-1",
            Status = PlaybackStatus.Playing,
            PositionMs = positionMs,
        };

    private static (FakePlaybackController, RecordingWebSocketClient, PlaybackAcknowledger) Build()
    {
        var playback = new FakePlaybackController();
        var socket = new RecordingWebSocketClient();
        var sessionState = new ClientSessionState();
        sessionState.Initialize(Guid.NewGuid(), sessionVersion: 1);
        var connectionManager = new ConnectionManager(socket, sessionState);
        return (playback, socket, new PlaybackAcknowledger(playback, connectionManager));
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

        public void Pause()
        {
        }

        public bool Resume() => false;

        public void StopImmediately() =>
            CurrentSnapshot = CurrentSnapshot with { Status = PlaybackStatus.Stopped };

        public void RaisePlaybackCompleted(string generationId) =>
            PlaybackCompleted?.Invoke(this, generationId);
    }

    private sealed class RecordingWebSocketClient : INetraWebSocketClient
    {
        public List<string> SentMessages { get; } = new();

        public bool IsConnected => true;

        public event EventHandler<string>? TextMessageReceived;
        public event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
        public event EventHandler<Exception>? ConnectionFaulted;
        public event EventHandler? Disconnected;

        public int CountContaining(string fragment) =>
            SentMessages.Count(message => message.Contains(fragment, StringComparison.Ordinal));

        public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendBinaryAsync(ReadOnlyMemory<byte> message, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendTextAsync(string message, CancellationToken cancellationToken)
        {
            SentMessages.Add(message);
            return Task.CompletedTask;
        }

        public Task CloseAsync(CancellationToken cancellationToken) => Task.CompletedTask;

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
}
