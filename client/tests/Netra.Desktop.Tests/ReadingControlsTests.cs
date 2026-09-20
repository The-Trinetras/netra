using System.Text.Json;
using Netra.Desktop.Accessibility;
using Netra.Desktop.Audio;
using Netra.Desktop.Library;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.Speech;
using Netra.Desktop.State;
using Netra.Desktop.Threading;
using Netra.Desktop.ViewModels;
using Xunit;

namespace Netra.Desktop.Tests;

// F10: every contracted command reachable and immediate, restored places
// told only when something was restored (D-open-5), honest status text, and
// a shortcut list that covers every command.
public sealed class ReadingControlsTests
{
    [Fact]
    public async Task PauseSilencesLocallyBeforeTheServerIsTold()
    {
        var rig = Rig.Create();

        rig.ViewModel.NavigationCommandRequest.Execute(NavigationCommandType.Pause);
        await Eventually.TrueAsync(() => rig.Log.Contains("send:navigation.command:pause"), "the command is sent");

        Assert.Equal(new[] { "player:pause", "send:navigation.command:pause" }, rig.Log);
    }

    [Fact]
    public async Task ContinueResumesTheLocalSegmentAndTellsTheServer()
    {
        var rig = Rig.Create();

        rig.ViewModel.NavigationCommandRequest.Execute(NavigationCommandType.Continue);
        await Eventually.TrueAsync(() => rig.Log.Contains("send:navigation.command:continue"), "the command is sent");

        Assert.Equal(new[] { "player:resume", "send:navigation.command:continue" }, rig.Log);
    }

    [Theory]
    [InlineData(NavigationCommandType.WhereAmI, "where_am_i")]
    [InlineData(NavigationCommandType.BackToReading, "back_to_reading")]
    [InlineData(NavigationCommandType.UndoJump, "undo_jump")]
    [InlineData(NavigationCommandType.ReturnToQuestion, "return_to_question")]
    public async Task TheRemainingCommandsGoOutAsTheContractSpellsThem(NavigationCommandType command, string wire)
    {
        var rig = Rig.Create();

        rig.ViewModel.NavigationCommandRequest.Execute(command);
        await Eventually.TrueAsync(() => rig.Log.Count > 0, "the command is sent");

        Assert.Equal(new[] { $"send:navigation.command:{wire}" }, rig.Log);
    }

    [Fact]
    public async Task ARestoredPlaceIsToldOnlyForTheSnapshotAnsweringAResume()
    {
        var rig = Rig.Create();
        await rig.Connection.SendSessionResumeAsync(new SessionResumePayload { LastKnownSessionVersion = 3 }, CancellationToken.None);
        var resumeId = rig.Connection.LastResumeRequestId!.Value;
        Assert.Equal(resumeId.ToString(), rig.LastSent.GetProperty("request_id").GetString());

        rig.Socket.Receive(Snapshot(Guid.NewGuid(), "reading", pending: false));
        Assert.Equal(string.Empty, rig.ViewModel.StatusMessage);

        rig.Socket.Receive(Snapshot(resumeId, "reading", pending: false));
        Assert.Equal("Your place in the reading is restored.", rig.ViewModel.StatusMessage);

        rig.Socket.Receive(Snapshot(resumeId, "quiz", pending: true));
        Assert.Equal("Your place is restored. A question is waiting for your answer.", rig.ViewModel.StatusMessage);
    }

    [Fact]
    public async Task AnEmptySessionIsNotAnnouncedAsRestored()
    {
        var rig = Rig.Create();
        await rig.Connection.SendSessionResumeAsync(new SessionResumePayload { LastKnownSessionVersion = 0 }, CancellationToken.None);

        rig.Socket.Receive(Snapshot(rig.Connection.LastResumeRequestId!.Value, "idle", pending: false));

        Assert.Equal(string.Empty, rig.ViewModel.StatusMessage);
    }

    [Fact]
    public void TheShortcutListNamesEveryContractedCommandAndTheLectureKeys()
    {
        var actions = string.Join(" ", ShortcutGuide.Everywhere.Select(s => s.Action)).ToLowerInvariant();
        foreach (var phrase in new[] { "push to talk", "stop", "pause", "continue", "next", "previous", "repeat", "where am i", "back to reading", "undo", "waiting question" })
        {
            Assert.Contains(phrase, actions);
        }

        Assert.Equal(new[] { "K", "J", "L", "T", "C", "D" }, ShortcutGuide.LectureTab.Select(s => s.Keys));
        var preferences = new PreferencesViewModel(new ClientSessionState());
        Assert.Contains(preferences.Shortcuts, s => s.Keys == "K in the Lecture tab");
        Assert.Equal("Control+P: Pause Netra's speech.", ShortcutGuide.Everywhere.Single(s => s.Keys == "Control+P").ToString());
    }

    [Fact]
    public void TheActivationShortcutIsToldEitherWay()
    {
        var preferences = new PreferencesViewModel(new ClientSessionState());

        preferences.SetActivationShortcut("Control+Alt+Shift+N");
        Assert.Equal("Activation shortcut: Control+Alt+Shift+N brings Netra to the front from anywhere. It never opens the microphone.", preferences.ActivationShortcut);

        preferences.SetActivationShortcut(null);
        Assert.Equal("No activation shortcut: other programs already use Netra's choices. Use Alt+Tab to reach Netra.", preferences.ActivationShortcut);
    }

    [Fact]
    public void TheDataNoticeSaysExactlyWhatIsRealAndWhatIsFixture()
    {
        Assert.StartsWith("Offline mode:", new PreferencesViewModel(new ClientSessionState()).DataSourceNotice);
        var live = new PreferencesViewModel(new ClientSessionState(), isLive: true).DataSourceNotice;
        Assert.StartsWith("Your sources, study session and conversation come from your Netra server.", live);
        Assert.Contains("still fixture data", live);
    }

    [Fact]
    public async Task AFailedLectureSearchIsToldNotSwallowed()
    {
        var library = new LibraryViewModel(new FixtureSourcePreparationService(), new FailingDiscovery()) { VideoSearchQuery = "ohm" };

        await library.SearchVideosAsync();

        Assert.Equal("Could not reach the Netra server to search for lectures.", library.StatusMessage);
        Assert.Empty(library.VideoResults);
    }

    [Fact]
    public async Task ASuccessfulSearchSaysHowToMoveThroughTheResults()
    {
        var library = new LibraryViewModel(new FixtureSourcePreparationService(), new FixtureVideoDiscoveryService()) { VideoSearchQuery = "ohm" };

        await library.SearchVideosAsync();

        Assert.Equal("Found 2 lecture results (fixture data). Use the arrow keys in the numbered list.", library.StatusMessage);
    }

    private static string Snapshot(Guid requestId, string mode, bool pending) =>
        ConversationVoiceTests.Envelope("session.snapshot", requestId, new
        {
            session_version = 4,
            interaction_mode = mode,
            active_source_version_id = "v-1",
            current_block_id = "b-1",
            current_sentence_id = "s-1",
            last_acknowledged_sentence_id = (string?)null,
            active_lesson = (object?)null,
            pending_question = pending ? new { question_id = "q-1", question_version = 1, hints_used = 0 } : null,
            last_result_set = (object?)null,
        });

    private sealed record Rig(ConversationViewModel ViewModel, ConnectionManager Connection, LoggingSocket Socket, List<string> Log)
    {
        public JsonElement LastSent => JsonDocument.Parse(Socket.Sent.Last()).RootElement;

        public static Rig Create()
        {
            var log = new List<string>();
            var state = new ClientSessionState();
            state.Initialize(Guid.NewGuid(), 3);
            var socket = new LoggingSocket(log);
            var connection = new ConnectionManager(socket, state);
            var player = new LoggingPlayer(log);
            var interruption = new InterruptionController(player, connection);
            var viewModel = new ConversationViewModel(
                state, connection, player, interruption, new BinaryAudioFrameProcessor(interruption),
                new MicrophoneCapture(connection, new FakePcmSource()), new SynchronousUiDispatcher());
            return new Rig(viewModel, connection, socket, log);
        }
    }

    private sealed class LoggingSocket(List<string> log) : INetraWebSocketClient
    {
        public List<string> Sent { get; } = new();
        public bool IsConnected => true;

        public event EventHandler<string>? TextMessageReceived;
        public event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
        public event EventHandler<Exception>? ConnectionFaulted;
        public event EventHandler? Disconnected;

        public void Receive(string json) => TextMessageReceived?.Invoke(this, json);

        public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendTextAsync(string message, CancellationToken cancellationToken)
        {
            Sent.Add(message);
            var root = JsonDocument.Parse(message).RootElement;
            var type = root.GetProperty("type").GetString();
            if (type == "navigation.command")
            {
                lock (log)
                {
                    log.Add($"send:{type}:{root.GetProperty("payload").GetProperty("command").GetString()}");
                }
            }

            return Task.CompletedTask;
        }

        public Task SendBinaryAsync(ReadOnlyMemory<byte> message, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task CloseAsync(CancellationToken cancellationToken) => Task.CompletedTask;

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }

    private sealed class LoggingPlayer(List<string> log) : IPlaybackController
    {
        public PlaybackSnapshot CurrentSnapshot { get; } = new();

        public event EventHandler<PlaybackSnapshot>? SnapshotChanged;
        public event EventHandler<string>? PlaybackCompleted;

        public void Play(Uri audioSource, string generationId, string segmentId, string sentenceId)
        {
        }

        public void Pause()
        {
            lock (log)
            {
                log.Add("player:pause");
            }
        }

        public bool Resume()
        {
            lock (log)
            {
                log.Add("player:resume");
            }

            return false;
        }

        public void StopImmediately()
        {
        }
    }

    private sealed class FailingDiscovery : IVideoDiscoveryService
    {
        public Task<IReadOnlyList<VideoDiscoveryResult>> SearchAsync(string query, CancellationToken cancellationToken) =>
            Task.FromException<IReadOnlyList<VideoDiscoveryResult>>(new HttpRequestException("down"));
    }
}
