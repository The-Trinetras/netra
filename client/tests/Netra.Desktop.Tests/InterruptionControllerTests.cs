using System.Threading;
using Netra.Desktop.Audio;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;
using Xunit;

namespace Netra.Desktop.Tests;

public sealed class InterruptionControllerTests
{
    // Verifies CLAUDE.md's "Old audio must never resume after stop/cancel":
    // once StopAsync fences a generation id, ShouldPlay must reject it even
    // if a queued/late segment for that generation shows up afterward.
    [Fact]
    public async Task StopAsync_FencesActiveGenerationIdAgainstFuturePlayback()
    {
        var playbackController = new FakePlaybackController();
        playbackController.SetSnapshot(new PlaybackSnapshot
        {
            GenerationId = "gen-1",
            SegmentId = "seg-1",
            SentenceId = "sentence-1",
            Status = PlaybackStatus.Playing,
        });

        var sessionState = new ClientSessionState();
        sessionState.Initialize(Guid.NewGuid(), sessionVersion: 1);
        var connectionManager = new ConnectionManager(new FakeWebSocketClient(), sessionState);

        var interruptionController = new InterruptionController(playbackController, connectionManager);

        Assert.True(interruptionController.ShouldPlay("gen-1"));

        await interruptionController.StopAsync(CancelReason.UserStop, CancellationToken.None);

        Assert.True(playbackController.StopImmediateCalled);
        Assert.False(interruptionController.ShouldPlay("gen-1"));
    }

    private sealed class FakePlaybackController : IPlaybackController
    {
        public PlaybackSnapshot CurrentSnapshot { get; private set; } = new();

        public bool StopImmediateCalled { get; private set; }

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

        public void Pause() =>
            CurrentSnapshot = CurrentSnapshot with { Status = PlaybackStatus.Paused };

        public bool Resume()
        {
            if (CurrentSnapshot.Status != PlaybackStatus.Paused)
            {
                return false;
            }

            CurrentSnapshot = CurrentSnapshot with { Status = PlaybackStatus.Playing };
            return true;
        }

        public void StopImmediately()
        {
            StopImmediateCalled = true;
            CurrentSnapshot = CurrentSnapshot with { Status = PlaybackStatus.Stopped };
        }

        public void RaisePlaybackCompleted(string generationId) => PlaybackCompleted?.Invoke(this, generationId);
    }

    // Minimal fake transport: no real socket connection is opened, matching
    // this scaffold's rule against making real backend calls from tests.
    private sealed class FakeWebSocketClient : INetraWebSocketClient
    {
        public bool IsConnected => false;

        public event EventHandler<string>? TextMessageReceived;
        public event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
        public event EventHandler<Exception>? ConnectionFaulted;
        public event EventHandler? Disconnected;

        public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendBinaryAsync(ReadOnlyMemory<byte> message, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendTextAsync(string message, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task CloseAsync(CancellationToken cancellationToken) => Task.CompletedTask;

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
}
