using System.Text.Json;
using Netra.Desktop.Video;
using Xunit;

namespace Netra.Desktop.Tests;

// The lecture player's protocol with its page (F8): readiness from real
// player events, the actual paused time for a question, exact resume, and no
// playback that the student did not ask for.
public sealed class LecturePlayerControllerTests
{
    private static readonly LectureVideo Ohm = new(YouTubeVideoId.Parse("dQw4w9WgXcQ"), "Ohm's law lecture");

    [Fact]
    public async Task LoadingOpensThePageOnceWaitsForTheApiAndBecomesReady()
    {
        var (player, page) = Build();

        var load = player.LoadAsync(Ohm, CancellationToken.None);
        Assert.Equal(PlaybackReadiness.Loading, player.Readiness);
        page.Say("""{"type":"apiReady"}""");
        await Eventually.TrueAsync(() => page.Posted("load").Count == 1, "the load command");
        page.Say("""{"type":"ready","title":"From YouTube","positionMs":0,"durationMs":1812000}""");
        await load;

        var command = page.Posted("load").Single();
        Assert.Equal("dQw4w9WgXcQ", command.GetProperty("videoId").GetString());
        Assert.Equal(PlaybackReadiness.Ready, player.Readiness);
        Assert.Null(player.UnavailableReason);
        Assert.Equal(1_812_000, player.DurationMs);
        Assert.Equal("Ohm's law lecture", player.Video!.Title);
        Assert.Equal(1, page.Initializations);

        var second = player.LoadAsync(Ohm with { Title = "" }, CancellationToken.None);
        await Eventually.TrueAsync(() => page.Posted("load").Count == 2, "the second load");
        page.Say("""{"type":"ready","title":"From YouTube"}""");
        await second;
        Assert.Equal(1, page.Initializations);
        Assert.Equal("From YouTube", player.Video!.Title);
    }

    [Theory]
    [InlineData(150, "The video's owner does not allow it to be played in other apps.")]
    [InlineData(101, "The video's owner does not allow it to be played in other apps.")]
    [InlineData(100, "This video was removed or is private.")]
    [InlineData(2, "This is not a valid YouTube video.")]
    [InlineData(5, "This video cannot be played in Netra's player.")]
    public async Task APlayerErrorMakesPlaybackUnavailableWithItsReason(int code, string reason)
    {
        var (player, page) = Build();

        var load = player.LoadAsync(Ohm, CancellationToken.None);
        page.Say("""{"type":"apiReady"}""");
        await Eventually.TrueAsync(() => page.Posted("load").Count == 1, "the load command");
        page.Say($$"""{"type":"error","code":{{code}}}""");
        await load;

        Assert.Equal(PlaybackReadiness.Unavailable, player.Readiness);
        Assert.Equal(reason, player.UnavailableReason);
    }

    [Fact]
    public async Task AMissingRuntimeIsReportedNotThrown()
    {
        var (player, page) = Build();
        page.InitializeFailure = new PlayerUnavailableException("The lecture player needs the Microsoft Edge WebView2 Runtime, which is not installed on this computer.");

        await player.LoadAsync(Ohm, CancellationToken.None);

        Assert.Equal(PlaybackReadiness.Unavailable, player.Readiness);
        Assert.StartsWith("The lecture player needs the Microsoft Edge WebView2 Runtime", player.UnavailableReason);
    }

    [Fact]
    public async Task WhenYouTubeCannotBeReachedTheLoadFailsAtOnceAndTheNextLoadReopensThePage()
    {
        var (player, page) = Build(new LecturePlayerTimeouts { PlayerStart = TimeSpan.FromMinutes(5) });

        var load = player.LoadAsync(Ohm, CancellationToken.None);
        page.Say("""{"type":"apiFailed"}""");
        await load.WaitAsync(TimeSpan.FromSeconds(5));

        Assert.Equal(PlaybackReadiness.Unavailable, player.Readiness);
        Assert.Equal("The video player could not reach YouTube. Check your internet connection and try again.", player.UnavailableReason);

        var retry = player.LoadAsync(Ohm, CancellationToken.None);
        page.Say("""{"type":"apiReady"}""");
        await Eventually.TrueAsync(() => page.Posted("load").Count == 1, "the retried load");
        page.Say("""{"type":"ready"}""");
        await retry;
        Assert.Equal(2, page.Initializations);
        Assert.Equal(PlaybackReadiness.Ready, player.Readiness);
    }

    [Fact]
    public async Task ALoadThatNeverAnswersEndsInAnHonestTimeout()
    {
        var (player, page) = Build(new LecturePlayerTimeouts { VideoLoad = TimeSpan.FromMilliseconds(50) });

        var load = player.LoadAsync(Ohm, CancellationToken.None);
        page.Say("""{"type":"apiReady"}""");
        await load;

        Assert.Equal(PlaybackReadiness.Unavailable, player.Readiness);
        Assert.Equal("The lecture did not load in time. Check your internet connection and try again.", player.UnavailableReason);
    }

    [Fact]
    public async Task AQuestionPausesFirstAndKeepsThePlayersOwnPausedTime()
    {
        var (player, page) = await ReadyAsync();
        page.Say("""{"type":"state","state":"playing","positionMs":47000}""");

        var question = player.PauseForQuestionAsync(CancellationToken.None);
        var request = page.Posted("pauseAndReport").Single();
        page.Say($$"""{"type":"time","requestId":"{{request.GetProperty("requestId").GetString()}}","positionMs":48250,"state":"paused"}""");

        Assert.Equal(48_250, await question);
        Assert.Equal(48_250, player.PausedForQuestionAtMs);
        Assert.Equal(LecturePlayerState.Paused, player.State);
        Assert.Empty(page.Posted("play"));
        Assert.Empty(page.Posted("seek"));
    }

    [Fact]
    public async Task AnUnansweredTimeRequestGivesNoTimeRatherThanAGuess()
    {
        var (player, page) = await ReadyAsync(new LecturePlayerTimeouts { TimeReport = TimeSpan.FromMilliseconds(50) });
        page.Say("""{"type":"state","state":"playing","positionMs":47000}""");

        var position = await player.PauseForQuestionAsync(CancellationToken.None);
        page.Say("""{"type":"time","requestId":"someone-else","positionMs":1}""");

        Assert.Null(position);
        Assert.Null(player.PausedForQuestionAtMs);
    }

    [Fact]
    public async Task ContinueResumesExactlyWhereTheQuestionPausedIt()
    {
        var (player, page) = await ReadyAsync();
        await PauseAtAsync(player, page, 48_250);

        player.ContinueFromPause();

        var seek = page.Posted("seek").Single();
        Assert.Equal(48_250, seek.GetProperty("positionMs").GetInt64());
        Assert.True(seek.GetProperty("play").GetBoolean());
        Assert.Null(player.PausedForQuestionAtMs);
    }

    [Fact]
    public async Task MovingWhilePausedReplacesThePausePoint()
    {
        var (player, page) = await ReadyAsync();
        await PauseAtAsync(player, page, 48_250);

        player.SeekBy(-10_000);
        player.ContinueFromPause();

        Assert.Equal(-10_000, page.Posted("seekBy").Single().GetProperty("deltaMs").GetInt64());
        Assert.Empty(page.Posted("seek"));
        Assert.Single(page.Posted("play"));
    }

    [Fact]
    public async Task NothingIsSentBeforeALectureIsReady()
    {
        var (player, page) = Build();

        player.Play();
        player.TogglePlay();
        player.SeekBy(10_000);
        player.ContinueFromPause();

        Assert.Null(await player.PauseForQuestionAsync(CancellationToken.None));
        Assert.Null(await player.ReadTimeAsync(CancellationToken.None));
        Assert.Empty(page.Messages);
        Assert.Equal(0, page.Initializations);
    }

    [Fact]
    public async Task TheToggleFollowsThePlayersReportedState()
    {
        var (player, page) = await ReadyAsync();

        player.TogglePlay();
        page.Say("""{"type":"state","state":"playing","positionMs":1000}""");
        player.TogglePlay();

        Assert.Single(page.Posted("play"));
        Assert.Single(page.Posted("pause"));
        Assert.Equal(1000, player.PositionMs);
    }

    [Fact]
    public async Task MalformedOrUnknownMessagesChangeNothing()
    {
        var (player, page) = await ReadyAsync();

        page.Say("not json");
        page.Say("[1,2]");
        page.Say("""{"type":"state","state":"playing","positionMs":-5}""");
        page.Say("""{"type":"somethingElse"}""");

        Assert.Equal(PlaybackReadiness.Ready, player.Readiness);
        Assert.Equal(0, player.PositionMs);
    }

    [Fact]
    public async Task ANewerChoiceWinsOverASlowerEarlierLoad()
    {
        var (player, page) = Build();
        var first = player.LoadAsync(Ohm, CancellationToken.None);
        page.Say("""{"type":"apiReady"}""");
        await Eventually.TrueAsync(() => page.Posted("load").Count == 1, "the first load");

        var other = new LectureVideo(YouTubeVideoId.Parse("a_b-c1D2e3F"), "Kirchhoff");
        var second = player.LoadAsync(other, CancellationToken.None);
        await Eventually.TrueAsync(() => page.Posted("load").Count == 2, "the second load");
        page.Say("""{"type":"ready"}""");
        await Task.WhenAll(first, second).WaitAsync(TimeSpan.FromSeconds(5));

        Assert.Equal("Kirchhoff", player.Video!.Title);
        Assert.Equal(PlaybackReadiness.Ready, player.Readiness);
    }

    internal static async Task<(LecturePlayerController, FakePlayerPage)> ReadyAsync(LecturePlayerTimeouts? timeouts = null)
    {
        var (player, page) = Build(timeouts);
        var load = player.LoadAsync(Ohm, CancellationToken.None);
        page.Say("""{"type":"apiReady"}""");
        await Eventually.TrueAsync(() => page.Posted("load").Count == 1, "the load command");
        page.Say("""{"type":"ready","durationMs":1812000}""");
        await load;
        return (player, page);
    }

    internal static async Task PauseAtAsync(LecturePlayerController player, FakePlayerPage page, long positionMs)
    {
        page.AnswerTimeRequests = positionMs;
        Assert.Equal(positionMs, await player.PauseForQuestionAsync(CancellationToken.None));
        page.AnswerTimeRequests = null;
    }

    internal static (LecturePlayerController, FakePlayerPage) Build(LecturePlayerTimeouts? timeouts = null)
    {
        var page = new FakePlayerPage();
        return (new LecturePlayerController(page, timeouts), page);
    }
}

// Stands in for WebView2 plus player.js: records commands and lets a test
// speak for the page.
internal sealed class FakePlayerPage : IPlayerSurface
{
    private readonly object _lock = new();

    public List<string> Messages { get; } = new();
    public int Initializations { get; private set; }
    public Exception? InitializeFailure { get; set; }

    // When set, a pauseAndReport/time request is answered at once with this
    // position and the paused state, as player.js does from a paused player.
    public long? AnswerTimeRequests { get; set; }

    public event EventHandler<string>? MessageReceived;

    public Task InitializeAsync(CancellationToken cancellationToken)
    {
        Initializations++;
        return InitializeFailure is null ? Task.CompletedTask : Task.FromException(InitializeFailure);
    }

    public void Post(string json)
    {
        lock (_lock)
        {
            Messages.Add(json);
        }

        using var command = JsonDocument.Parse(json);
        var type = command.RootElement.GetProperty("type").GetString();
        if (AnswerTimeRequests is { } position && type is "pauseAndReport" or "time")
        {
            var requestId = command.RootElement.GetProperty("requestId").GetString();
            Say($$"""{"type":"time","requestId":"{{requestId}}","positionMs":{{position}},"state":"paused"}""");
        }
    }

    public void Say(string json) => MessageReceived?.Invoke(this, json);

    public List<JsonElement> Posted(string type)
    {
        lock (_lock)
        {
            return Messages.Select(m => JsonDocument.Parse(m).RootElement)
                .Where(e => e.GetProperty("type").GetString() == type)
                .ToList();
        }
    }
}
