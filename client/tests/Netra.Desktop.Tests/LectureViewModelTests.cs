using System.Text.Json;
using Netra.Desktop.Accessibility;
using Netra.Desktop.Audio;
using Netra.Desktop.Library;
using Netra.Desktop.Networking;
using Netra.Desktop.Speech;
using Netra.Desktop.State;
using Netra.Desktop.Threading;
using Netra.Desktop.Video;
using Netra.Desktop.ViewModels;
using Xunit;

namespace Netra.Desktop.Tests;

// The Lecture tab as a keyboard and screen-reader user meets it, and how a
// question anywhere in Netra pauses the lecture first.
public sealed class LectureViewModelTests
{
    [Fact]
    public async Task APastedLinkOpensAndReadinessIsToldInTwoSeparateLines()
    {
        var (player, page) = LecturePlayerControllerTests.Build();
        using var lecture = new LectureViewModel(player) { LinkText = "https://youtu.be/dQw4w9WgXcQ" };
        Assert.Equal("Playback: no lecture open.", lecture.PlaybackLine);

        var open = lecture.OpenLinkAsync(CancellationToken.None);
        page.Say("""{"type":"apiReady"}""");
        await Eventually.TrueAsync(() => page.Posted("load").Count == 1, "the load");
        page.Say("""{"type":"ready","title":"Ohm's law, part 1","durationMs":1812000}""");
        await open;

        Assert.Equal("dQw4w9WgXcQ", page.Posted("load").Single().GetProperty("videoId").GetString());
        Assert.Equal("Lecture ready: Ohm's law, part 1. Press K to play or pause.", lecture.StatusMessage);
        Assert.Equal("Playback: ready.", lecture.PlaybackLine);
        Assert.Equal(LectureViewModel.AnalysisNotReported, lecture.AnalysisLine);
        Assert.Equal("0:00 of 30:12, not started", lecture.TimeText);
    }

    [Fact]
    public async Task ALinkThatIsNotYouTubeLoadsNothing()
    {
        var (player, page) = LecturePlayerControllerTests.Build();
        using var lecture = new LectureViewModel(player) { LinkText = "https://example.com/lecture" };

        await lecture.OpenLinkAsync(CancellationToken.None);

        Assert.StartsWith("That is not a YouTube video link.", lecture.StatusMessage);
        Assert.Empty(page.Messages);
        Assert.Equal(0, page.Initializations);
    }

    [Fact]
    public async Task AFixtureResultWithoutARealVideoSaysSo()
    {
        var (player, page) = LecturePlayerControllerTests.Build();
        using var lecture = new LectureViewModel(player);

        await lecture.OpenResultAsync(
            new VideoDiscoveryResult { Ordinal = 2, Title = "Series and Parallel Resistance (fixture)", Lecturer = "Fixture", VideoId = "fixture-lecture-v2" },
            CancellationToken.None);

        Assert.Equal("Result 2, Series and Parallel Resistance (fixture), has no playable YouTube video. Search results are still fixture data.", lecture.StatusMessage);
        Assert.Equal(0, page.Initializations);
    }

    [Fact]
    public async Task AnUnplayableVideoIsExplainedAndItsControlsSaySo()
    {
        var (player, page) = LecturePlayerControllerTests.Build();
        using var lecture = new LectureViewModel(player) { LinkText = "dQw4w9WgXcQ" };

        var open = lecture.OpenLinkAsync(CancellationToken.None);
        page.Say("""{"type":"apiReady"}""");
        await Eventually.TrueAsync(() => page.Posted("load").Count == 1, "the load");
        page.Say("""{"type":"error","code":150}""");
        await open;
        lecture.PlayPauseCommand.Execute(null);

        Assert.Equal("Playback: not available. The video's owner does not allow it to be played in other apps.", lecture.PlaybackLine);
        Assert.Equal(LectureViewModel.AnalysisNotReported, lecture.AnalysisLine);
        Assert.StartsWith("Open a lecture first", lecture.StatusMessage);
        Assert.Empty(page.Posted("play"));
    }

    [Fact]
    public async Task KeyboardCommandsConfirmWhatHappened()
    {
        var (player, page) = await LecturePlayerControllerTests.ReadyAsync();
        using var lecture = new LectureViewModel(player);

        lecture.PlayPauseCommand.Execute(null);
        Assert.Equal("Playing.", lecture.StatusMessage);
        page.Say("""{"type":"state","state":"playing","positionMs":5000}""");
        lecture.BackCommand.Execute(null);
        Assert.Equal("Back 10 seconds.", lecture.StatusMessage);
        lecture.ForwardCommand.Execute(null);
        Assert.Equal("Forward 10 seconds.", lecture.StatusMessage);
        lecture.PlayPauseCommand.Execute(null);
        Assert.Equal("Paused.", lecture.StatusMessage);

        page.AnswerTimeRequests = 185_000;
        lecture.WhereAmICommand.Execute(null);
        await Eventually.TrueAsync(() => lecture.StatusMessage.StartsWith("3 minutes"), "the time is read");
        Assert.Equal("3 minutes 5 seconds of 30 minutes 12 seconds, paused.", lecture.StatusMessage);
        Assert.Equal(new long[] { -10_000, 10_000 }, page.Posted("seekBy").Select(c => c.GetProperty("deltaMs").GetInt64()));
    }

    [Fact]
    public async Task DescribePausesKeepsTheTimeAndIsHonestAboutAnalysis()
    {
        var (player, page) = await LecturePlayerControllerTests.ReadyAsync();
        using var lecture = new LectureViewModel(player);
        page.Say("""{"type":"state","state":"playing","positionMs":48000}""");
        page.AnswerTimeRequests = 48_250;

        lecture.DescribeCommand.Execute(null);
        await Eventually.TrueAsync(() => lecture.StatusMessage.StartsWith("Paused at"), "the pause is told");

        Assert.Equal("Paused at 48 seconds. Netra cannot describe what is shown yet: this server does not report lecture analysis.", lecture.StatusMessage);
        Assert.Equal("Paused for your question at 48 seconds. Continue resumes from there.", lecture.PausePointText);
        Assert.Single(page.Posted("pauseAndReport"));
        Assert.Empty(page.Posted("play"));
    }

    [Fact]
    public async Task PlayAfterAQuestionContinuesFromTheExactPausePoint()
    {
        var (player, page) = await LecturePlayerControllerTests.ReadyAsync();
        using var lecture = new LectureViewModel(player);
        await LecturePlayerControllerTests.PauseAtAsync(player, page, 48_250);

        lecture.PlayPauseCommand.Execute(null);

        Assert.Equal("Continuing from 48 seconds.", lecture.StatusMessage);
        Assert.Equal(48_250, page.Posted("seek").Single().GetProperty("positionMs").GetInt64());
        Assert.Empty(page.Posted("play"));
        Assert.Equal(string.Empty, lecture.PausePointText);
    }

    [Fact]
    public async Task ATypedQuestionPausesThePlayingLectureBeforeTheTurnIsSent()
    {
        var (player, page) = await LecturePlayerControllerTests.ReadyAsync();
        page.Say("""{"type":"state","state":"playing","positionMs":61000}""");
        page.AnswerTimeRequests = 61_500;
        var state = new ClientSessionState();
        state.Initialize(Guid.NewGuid(), 2);
        var socket = new OrderedSocket(page);
        var connection = new ConnectionManager(socket, state);
        var audio = new ScriptedPlayer();
        var interruption = new InterruptionController(audio, connection);
        using var conversation = new ConversationViewModel(
            state, connection, audio, interruption, new BinaryAudioFrameProcessor(interruption),
            new MicrophoneCapture(connection, new FakePcmSource()), new SynchronousUiDispatcher(), lecture: player)
        {
            InputText = "Why is the line straight?",
        };

        conversation.SubmitCommand.Execute(null);
        await Eventually.TrueAsync(() => socket.Order.Contains("turn.submit"), "the turn is sent");

        Assert.Equal(new[] { "pauseAndReport", "turn.submit" }, socket.Order);
        Assert.Equal(61_500, player.PausedForQuestionAtMs);
    }

    [Fact]
    public async Task PushToTalkPausesTheLectureWithoutDelayingTheMicrophone()
    {
        var (player, page) = await LecturePlayerControllerTests.ReadyAsync(new LecturePlayerTimeouts { TimeReport = TimeSpan.FromSeconds(30) });
        page.Say("""{"type":"state","state":"playing","positionMs":61000}""");
        var state = new ClientSessionState();
        state.Initialize(Guid.NewGuid(), 2);
        var connection = new ConnectionManager(new CapturingSocket(), state);
        var audio = new ScriptedPlayer();
        var speech = new ListeningProbe(page);
        var controller = new PushToTalkController(audio, new InterruptionController(audio, connection), speech, player);

        var press = controller.OnKeyDownAsync(CancellationToken.None);
        await Eventually.TrueAsync(() => speech.Started, "the microphone opens");

        Assert.Equal(1, speech.PauseCommandsBeforeStart);
        Assert.False(press.IsCompleted);
        var request = page.Posted("pauseAndReport").Single().GetProperty("requestId").GetString();
        page.Say($$"""{"type":"time","requestId":"{{request}}","positionMs":61200,"state":"paused"}""");
        await press;
        Assert.Equal(61_200, player.PausedForQuestionAtMs);
    }

    [Fact]
    public async Task PlayingALibraryResultSelectsItExactlyAndOpensTheLectureTab()
    {
        var (player, page) = LecturePlayerControllerTests.Build();
        var library = new LibraryViewModel(new FixtureSourcePreparationService(), new FixtureVideoDiscoveryService());
        using var lecture = new LectureViewModel(player);
        using var shell = new ShellViewModel(
            library, new StudyViewModel(),
            new ConversationViewModel(
                new ClientSessionState(), new ConnectionManager(new CapturingSocket(), new ClientSessionState()), new ScriptedPlayer(),
                new InterruptionController(new ScriptedPlayer(), new ConnectionManager(new CapturingSocket(), new ClientSessionState())),
                new BinaryAudioFrameProcessor(new InterruptionController(new ScriptedPlayer(), new ConnectionManager(new CapturingSocket(), new ClientSessionState()))),
                new ListeningProbe(page), new SynchronousUiDispatcher()),
            new PreferencesViewModel(new ClientSessionState()),
            lecture);
        var shown = 0;
        shell.ShowLectureRequested += (_, _) => shown++;
        library.VideoSearchQuery = "ohm";
        await library.SearchVideosAsync();

        library.PlayVideoResultCommand.Execute(library.VideoResults[1]);

        Assert.Same(library.VideoResults[1], library.SelectedVideoResult);
        Assert.Equal(1, shown);
        await Eventually.TrueAsync(() => lecture.StatusMessage.StartsWith("Result 2"), "the lecture tab reports the result");
    }

    // Records the order of commands to the player page and frames to the server.
    private sealed class OrderedSocket(FakePlayerPage page) : INetraWebSocketClient
    {
        public List<string> Order { get; } = new();
        public bool IsConnected => true;

        public event EventHandler<string>? TextMessageReceived;
        public event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
        public event EventHandler<Exception>? ConnectionFaulted;
        public event EventHandler? Disconnected;

        public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendTextAsync(string message, CancellationToken cancellationToken)
        {
            lock (Order)
            {
                Order.AddRange(page.Messages.Select(m => JsonDocument.Parse(m).RootElement.GetProperty("type").GetString()!)
                    .Where(t => t == "pauseAndReport").Except(Order));
                Order.Add(JsonDocument.Parse(message).RootElement.GetProperty("type").GetString()!);
            }

            return Task.CompletedTask;
        }

        public Task SendBinaryAsync(ReadOnlyMemory<byte> message, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task CloseAsync(CancellationToken cancellationToken) => Task.CompletedTask;

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }

    // Notes how many pause commands reached the player page when the
    // microphone was asked to open.
    private sealed class ListeningProbe(FakePlayerPage page) : ISpeechInputService
    {
        public bool Started { get; private set; }
        public int PauseCommandsBeforeStart { get; private set; }
        public bool IsListening => Started;

        public event EventHandler<TranscriptReceivedEventArgs>? TranscriptReceived;
        public event EventHandler<VoiceInputStatus>? StatusChanged;

        public Task StartListeningAsync(CancellationToken cancellationToken)
        {
            PauseCommandsBeforeStart = page.Posted("pauseAndReport").Count;
            Started = true;
            return Task.CompletedTask;
        }

        public void StopListening()
        {
        }

        public void AbortListening()
        {
        }

        public void Dispose()
        {
        }
    }
}
