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
        Assert.Equal(1, speechInputService.StopCount);
        Assert.Equal(0, speechInputService.AbortCount);
    }

    // Alt+Tab while holding the key: the key-up goes to another window, so
    // the capture is discarded (never sent), not left running.
    [Fact]
    public async Task FocusLostWhileHeld_DiscardsTheCaptureAndTheLaterKeyUpDoesNothing()
    {
        var (controller, _, speechInputService, _) = Build();
        await controller.OnKeyDownAsync(CancellationToken.None);

        controller.OnFocusLost();
        controller.OnKeyUp();

        Assert.False(speechInputService.IsListening);
        Assert.Equal(1, speechInputService.AbortCount);
        Assert.Equal(0, speechInputService.StopCount);
    }

    [Fact]
    public void FocusLostWhileNotHeld_DoesNothing()
    {
        var (controller, _, speechInputService, _) = Build();

        controller.OnFocusLost();

        Assert.Equal(0, speechInputService.AbortCount);
    }

    // Press-to-interrupt while disconnected: playback still stops locally,
    // the unsendable cancel does not escape (it would crash the async key
    // handler), and the student can still speak.
    [Fact]
    public async Task InterruptWhoseCancelCannotBeSent_StillSilencesAndStartsListening()
    {
        var (controller, playbackController, speechInputService, _) = Build(new NoOpWebSocketClient { FailSends = true });
        playbackController.SetPlaying();

        await controller.OnKeyDownAsync(CancellationToken.None);

        Assert.True(playbackController.StopImmediateCalled);
        Assert.True(speechInputService.IsListening);
    }

    private static (PushToTalkController Controller, RecordingPlaybackController PlaybackController, RecordingSpeechInput SpeechInputService, InterruptionController InterruptionController) Build(
        NoOpWebSocketClient? socket = null)
    {
        var sessionState = new ClientSessionState();
        sessionState.Initialize(Guid.NewGuid(), sessionVersion: 1);
        var connectionManager = new ConnectionManager(socket ?? new NoOpWebSocketClient(), sessionState);
        var playbackController = new RecordingPlaybackController();
        var interruptionController = new InterruptionController(playbackController, connectionManager);
        var speechInputService = new RecordingSpeechInput();

        var controller = new PushToTalkController(playbackController, interruptionController, speechInputService);
        return (controller, playbackController, speechInputService, interruptionController);
    }

    private sealed class RecordingSpeechInput : ISpeechInputService
    {
        public bool IsListening { get; private set; }
        public int StopCount { get; private set; }
        public int AbortCount { get; private set; }

        public event EventHandler<TranscriptReceivedEventArgs>? TranscriptReceived;
        public event EventHandler<VoiceInputStatus>? StatusChanged;

        public Task StartListeningAsync(CancellationToken cancellationToken)
        {
            IsListening = true;
            return Task.CompletedTask;
        }

        public void StopListening()
        {
            IsListening = false;
            StopCount++;
        }

        public void AbortListening()
        {
            IsListening = false;
            AbortCount++;
        }

        public void Dispose()
        {
        }
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
        public bool FailSends { get; init; }

        public bool IsConnected => !FailSends;

        public event EventHandler<string>? TextMessageReceived;
        public event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
        public event EventHandler<Exception>? ConnectionFaulted;
        public event EventHandler? Disconnected;

        public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendBinaryAsync(ReadOnlyMemory<byte> message, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendTextAsync(string message, CancellationToken cancellationToken) =>
            FailSends ? Task.FromException(new NotConnectedException()) : Task.CompletedTask;

        public Task CloseAsync(CancellationToken cancellationToken) => Task.CompletedTask;

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
}
