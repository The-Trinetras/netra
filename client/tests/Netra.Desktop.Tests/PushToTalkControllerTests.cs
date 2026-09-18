using System.Threading;
using Netra.Desktop.Accessibility;
using Netra.Desktop.Audio;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.Speech;
using Netra.Desktop.State;
using Xunit;

namespace Netra.Desktop.Tests;

// client.md: "Support push-to-talk, press-to-interrupt and immediate local
// STOP... Do not silently capture or retain microphone audio outside the
// selected mode." No WPF Window/UIElement dependency here by design — see
// Accessibility/PushToTalkController.cs — so these run without a live
// window.
public sealed class PushToTalkControllerTests
{
    [Fact]
    public async Task KeyDown_WhilePlaying_InterruptsThenStartsListening()
    {
        var (controller, playbackController, speechInputService, _) = Build();
        playbackController.SetPlaying();

        await controller.OnKeyDownAsync(CancellationToken.None);

        Assert.True(playbackController.StopImmediateCalled);
        Assert.True(speechInputService.IsListening);
    }

    [Fact]
    public async Task KeyDown_WhileNotPlaying_StartsListeningWithoutInterrupting()
    {
        var (controller, playbackController, speechInputService, _) = Build();

        await controller.OnKeyDownAsync(CancellationToken.None);

        Assert.False(playbackController.StopImmediateCalled);
        Assert.True(speechInputService.IsListening);
    }

    [Fact]
    public async Task KeyRepeatWhileHeld_DoesNotRestartListeningOrReInterrupt()
    {
        var (controller, playbackController, speechInputService, _) = Build();
        playbackController.SetPlaying();

        await controller.OnKeyDownAsync(CancellationToken.None);
        playbackController.StopImmediateCallCount = 0;

        // Key-repeat: another key-down while still held.
        await controller.OnKeyDownAsync(CancellationToken.None);

        Assert.Equal(0, playbackController.StopImmediateCallCount);
        Assert.True(speechInputService.IsListening);
    }

    [Fact]
    public async Task KeyUp_StopsListening()
    {
        var (controller, _, speechInputService, _) = Build();
        await controller.OnKeyDownAsync(CancellationToken.None);

        controller.OnKeyUp();

        Assert.False(speechInputService.IsListening);
    }

    private static (PushToTalkController Controller, RecordingPlaybackController PlaybackController, MicrophoneCapture SpeechInputService, InterruptionController InterruptionController) Build()
    {
        var sessionState = new ClientSessionState();
        sessionState.Initialize(Guid.NewGuid(), sessionVersion: 1);
        var socket = new NoOpWebSocketClient();
        var connectionManager = new ConnectionManager(socket, sessionState);
        var playbackController = new RecordingPlaybackController();
        var interruptionController = new InterruptionController(playbackController, connectionManager);
        var speechInputService = new MicrophoneCapture();

        var controller = new PushToTalkController(playbackController, interruptionController, speechInputService);
        return (controller, playbackController, speechInputService, interruptionController);
    }

    private sealed class RecordingPlaybackController : IPlaybackController
    {
        public PlaybackSnapshot CurrentSnapshot { get; private set; } = new();
        public bool StopImmediateCalled { get; private set; }
        public int StopImmediateCallCount { get; set; }

        public event EventHandler<PlaybackSnapshot>? SnapshotChanged;
        public event EventHandler<string>? PlaybackCompleted;

        public void SetPlaying() =>
            CurrentSnapshot = CurrentSnapshot with { GenerationId = "gen-1", Status = PlaybackStatus.Playing };

        public void Play(Uri audioSource, string generationId, string segmentId, string sentenceId)
        {
        }

        public void Pause()
        {
        }

        public bool Resume() => false;

        public void StopImmediately()
        {
            StopImmediateCalled = true;
            StopImmediateCallCount++;
            CurrentSnapshot = CurrentSnapshot with { Status = PlaybackStatus.Stopped };
        }
    }

    private sealed class NoOpWebSocketClient : INetraWebSocketClient
    {
        public bool IsConnected => true;

        public event EventHandler<string>? TextMessageReceived;
        public event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
        public event EventHandler<Exception>? ConnectionFaulted;
        public event EventHandler? Disconnected;

        public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendTextAsync(string message, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task CloseAsync(CancellationToken cancellationToken) => Task.CompletedTask;

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
}
