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

// session.snapshot, quiz.question and error were previously dropped with a
// `// TODO` comment (see docs/team/handoffs/M5.md). These verify the
// replacement handling actually reconciles ClientSessionState and surfaces
// safe status text, rather than merely not crashing.
public sealed class SessionSnapshotHandlingTests
{
    [Fact]
    public void SessionSnapshot_ReconcilesClientSessionState()
    {
        var (sessionState, socket, _) = Build();

        socket.RaiseTextMessageReceived(SessionSnapshotJson());

        Assert.Equal(20, sessionState.SessionVersion);
        Assert.Equal(SessionInteractionMode.Quiz, sessionState.InteractionMode);
        Assert.Equal("src-v-42", sessionState.ActiveSourceVersionId);
        Assert.Equal("block-17", sessionState.CurrentBlockId);
        Assert.Equal("q-88", sessionState.PendingQuestionId);
    }

    [Fact]
    public void QuizQuestion_SetsPendingQuestionAndSurfacesThePrompt()
    {
        var (sessionState, socket, viewModel) = Build();

        socket.RaiseTextMessageReceived(QuizQuestionJson());

        Assert.Equal("q-88", sessionState.PendingQuestionId);
        Assert.Contains(viewModel.Transcript, line => line.Text == "Which protocol is connection-oriented?");
    }

    // error.schema.json's "message" field is the only field approved for
    // "safe/accessible text only" — code/retryable/correlation_id must
    // never leak into spoken/announced text.
    [Fact]
    public void Error_SurfacesOnlyTheSafeMessageAndReconcilesSessionVersion()
    {
        var (sessionState, socket, viewModel) = Build();

        socket.RaiseTextMessageReceived(ErrorJson());

        Assert.Equal("Your view of this session is out of date. Reconnecting to refresh it.", viewModel.StatusMessage);
        Assert.DoesNotContain("SESSION_VERSION_CONFLICT", viewModel.StatusMessage);
        Assert.Equal(18, sessionState.SessionVersion);
    }

    private static (ClientSessionState SessionState, RecordingWebSocketClient Socket, ConversationViewModel ViewModel) Build()
    {
        var sessionState = new ClientSessionState();
        sessionState.Initialize(Guid.NewGuid(), sessionVersion: 1);
        var socket = new RecordingWebSocketClient();
        var connectionManager = new ConnectionManager(socket, sessionState);
        var playbackController = new NoOpPlaybackController();
        var interruptionController = new InterruptionController(playbackController, connectionManager);
        var binaryAudioFrameProcessor = new BinaryAudioFrameProcessor(interruptionController);
        var speechInputService = new MicrophoneCapture();

        var viewModel = new ConversationViewModel(
            sessionState, connectionManager, playbackController, interruptionController,
            binaryAudioFrameProcessor, speechInputService, new SynchronousUiDispatcher());

        return (sessionState, socket, viewModel);
    }

    private static string SessionSnapshotJson() =>
        """
        {
          "protocol_version": "1.0",
          "message_id": "b1f2b3c4-0001-4a00-8000-000000000001",
          "session_id": "b1f2b3c4-0000-4a00-8000-000000000000",
          "request_id": "b1f2b3c4-0002-4a00-8000-000000000002",
          "sequence": 1,
          "type": "session.snapshot",
          "payload": {
            "session_version": 20,
            "interaction_mode": "quiz",
            "active_source_version_id": "src-v-42",
            "current_block_id": "block-17",
            "current_sentence_id": "s-45",
            "last_acknowledged_sentence_id": "s-45",
            "active_lesson": { "lesson_id": "b1f2b3c4-0003-4a00-8000-000000000003" },
            "pending_question": { "question_id": "q-88", "question_version": 1, "hints_used": 1 },
            "last_result_set": { "result_set_id": "b1f2b3c4-0004-4a00-8000-000000000004", "created_at": "2026-09-12T10:15:00Z" }
          }
        }
        """;

    private static string QuizQuestionJson() =>
        """
        {
          "protocol_version": "1.0",
          "message_id": "c2f3c4d5-0001-4a00-8000-000000000001",
          "session_id": "c2f3c4d5-0000-4a00-8000-000000000000",
          "request_id": "c2f3c4d5-0002-4a00-8000-000000000002",
          "sequence": 4,
          "type": "quiz.question",
          "payload": {
            "question_id": "q-88",
            "question_version": 1,
            "kind": "multiple_choice",
            "prompt": "Which protocol is connection-oriented?",
            "options": [
              { "option_id": "opt-1", "text": "UDP" },
              { "option_id": "opt-2", "text": "TCP" }
            ],
            "hints_used": 0
          }
        }
        """;

    private static string ErrorJson() =>
        """
        {
          "protocol_version": "1.0",
          "message_id": "d3f4d5e6-0001-4a00-8000-000000000001",
          "session_id": "d3f4d5e6-0000-4a00-8000-000000000000",
          "request_id": "d3f4d5e6-0002-4a00-8000-000000000002",
          "sequence": 6,
          "type": "error",
          "payload": {
            "code": "SESSION_VERSION_CONFLICT",
            "message": "Your view of this session is out of date. Reconnecting to refresh it.",
            "retryable": false,
            "current_session_version": 18
          }
        }
        """;

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

        public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendTextAsync(string message, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task CloseAsync(CancellationToken cancellationToken) => Task.CompletedTask;

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
}
