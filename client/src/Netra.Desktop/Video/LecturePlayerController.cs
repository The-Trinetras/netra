using System.Text.Json;
using System.Threading;

namespace Netra.Desktop.Video;

public sealed record LectureVideo(YouTubeVideoId Id, string Title);

public enum PlaybackReadiness
{
    NotLoaded,
    Loading,
    Ready,
    Unavailable,
}

public enum LecturePlayerState
{
    Unstarted,
    Cued,
    Playing,
    Paused,
    Buffering,
    Ended,
}

// The web surface that hosts Netra's player page (WebView2 on Windows). It
// moves JSON strings only; this controller owns the protocol, so all of it is
// testable without a browser.
public interface IPlayerSurface
{
    // Messages from Netra's own player page only (the surface checks origin).
    event EventHandler<string>? MessageReceived;

    // Creates the web view and opens the player page. Throws
    // PlayerUnavailableException with an accessible reason when it cannot.
    Task InitializeAsync(CancellationToken cancellationToken);

    void Post(string json);
}

public sealed class PlayerUnavailableException : Exception
{
    public PlayerUnavailableException(string accessibleReason)
        : base(accessibleReason)
    {
    }
}

// Pausing the lecture for a question (conversation and push-to-talk), apart
// from the player so those callers need nothing else.
public interface ILecturePause
{
    // Pauses first, then reads the time the player itself reports once it is
    // paused. Null when no lecture is loaded or the time could not be read;
    // never a guessed or wall-clock time.
    Task<long?> PauseForQuestionAsync(CancellationToken cancellationToken);
}

// Client-local bounds (no wire field).
public sealed record LecturePlayerTimeouts
{
    public TimeSpan PlayerStart { get; init; } = TimeSpan.FromSeconds(20);
    public TimeSpan VideoLoad { get; init; } = TimeSpan.FromSeconds(20);
    public TimeSpan TimeReport { get; init; } = TimeSpan.FromSeconds(2);
}

// The lecture player (M5-VIDEO, F8): playback readiness, actual player time,
// pause-for-a-question with exact resume. It never plays on its own: only
// Play, the play/pause toggle or ContinueFromPause start playback, so the
// lecture stays paused while Netra answers.
public sealed class LecturePlayerController : ILecturePause, IDisposable
{
    private readonly IPlayerSurface _surface;
    private readonly LecturePlayerTimeouts _timeouts;
    private readonly object _lock = new();
    private readonly Dictionary<string, TaskCompletionSource<TimeReport>> _timeRequests = new();
    private TaskCompletionSource _apiReady = NewSignal();
    private TaskCompletionSource<string?>? _loading;
    private bool _initialized;
    private int _loadGeneration;

    public LecturePlayerController(IPlayerSurface surface, LecturePlayerTimeouts? timeouts = null)
    {
        _surface = surface;
        _timeouts = timeouts ?? new LecturePlayerTimeouts();
        _surface.MessageReceived += OnMessage;
    }

    public LectureVideo? Video { get; private set; }
    public PlaybackReadiness Readiness { get; private set; } = PlaybackReadiness.NotLoaded;
    public string? UnavailableReason { get; private set; }
    public LecturePlayerState State { get; private set; } = LecturePlayerState.Unstarted;
    public long PositionMs { get; private set; }
    public long? DurationMs { get; private set; }

    // Where the lecture was paused for the latest question, until the
    // student continues. This is the time a question is about.
    public long? PausedForQuestionAtMs { get; private set; }

    public bool IsPlaying => State is LecturePlayerState.Playing or LecturePlayerState.Buffering;

    // Any state above changed (UI thread, as the surface raises messages there).
    public event EventHandler? Changed;

    public async Task LoadAsync(LectureVideo video, CancellationToken cancellationToken)
    {
        var generation = Interlocked.Increment(ref _loadGeneration);
        Video = video;
        Readiness = PlaybackReadiness.Loading;
        UnavailableReason = null;
        State = LecturePlayerState.Unstarted;
        PositionMs = 0;
        DurationMs = null;
        PausedForQuestionAtMs = null;
        RaiseChanged();

        string? failure;
        try
        {
            if (!_initialized)
            {
                await _surface.InitializeAsync(cancellationToken);
                _initialized = true;
            }

            if (!await WaitAsync(_apiReady.Task, _timeouts.PlayerStart, cancellationToken))
            {
                failure = "The video player did not start. Check your internet connection and try again.";
            }
            else
            {
                var loading = new TaskCompletionSource<string?>(TaskCreationOptions.RunContinuationsAsynchronously);

                // A load still waiting for an earlier choice ends now; its
                // generation check then leaves the state to this one.
                Interlocked.Exchange(ref _loading, loading)?.TrySetResult(null);
                _surface.Post(Command(new { type = "load", videoId = video.Id.Value, startMs = 0 }));
                failure = await WaitAsync(loading.Task, _timeouts.VideoLoad, cancellationToken)
                    ? loading.Task.Result
                    : "The lecture did not load in time. Check your internet connection and try again.";
            }
        }
        catch (PlayerUnavailableException ex)
        {
            failure = ex.Message;
        }

        if (generation != Volatile.Read(ref _loadGeneration))
        {
            return; // A newer choice replaced this one.
        }

        Readiness = failure is null ? PlaybackReadiness.Ready : PlaybackReadiness.Unavailable;
        UnavailableReason = failure;
        RaiseChanged();
    }

    public void Play() => PostIfReady(new { type = "play" });

    public void Pause() => PostIfReady(new { type = "pause" });

    public void TogglePlay()
    {
        if (IsPlaying)
        {
            Pause();
        }
        else
        {
            Play();
        }
    }

    // Moving through the lecture replaces the question's pause point: after
    // going back to re-listen, "continue" goes on from where the student is.
    public void SeekBy(long deltaMs)
    {
        if (Readiness != PlaybackReadiness.Ready)
        {
            return;
        }

        PausedForQuestionAtMs = null;
        _surface.Post(Command(new { type = "seekBy", deltaMs }));
        RaiseChanged();
    }

    // Resumes exactly where the lecture was paused for the question; with
    // no such point it simply plays.
    public void ContinueFromPause()
    {
        if (PausedForQuestionAtMs is { } position)
        {
            PausedForQuestionAtMs = null;
            PostIfReady(new { type = "seek", positionMs = position, play = true });
            RaiseChanged();
            return;
        }

        Play();
    }

    public async Task<long?> PauseForQuestionAsync(CancellationToken cancellationToken)
    {
        if (Readiness != PlaybackReadiness.Ready)
        {
            return null;
        }

        // Pause first (locally, before any network), then read the time the
        // player reports once paused.
        var report = await RequestTimeAsync(pause: true, cancellationToken);
        if (report is null)
        {
            return null;
        }

        PausedForQuestionAtMs = report.PositionMs;
        RaiseChanged();
        return report.PositionMs;
    }

    // "Where am I": the player's own current time, without pausing.
    public async Task<long?> ReadTimeAsync(CancellationToken cancellationToken) =>
        Readiness == PlaybackReadiness.Ready ? (await RequestTimeAsync(pause: false, cancellationToken))?.PositionMs : null;

    private async Task<TimeReport?> RequestTimeAsync(bool pause, CancellationToken cancellationToken)
    {
        var requestId = Guid.NewGuid().ToString("N");
        var reply = new TaskCompletionSource<TimeReport>(TaskCreationOptions.RunContinuationsAsynchronously);
        lock (_lock)
        {
            _timeRequests[requestId] = reply;
        }

        try
        {
            _surface.Post(Command(new { type = pause ? "pauseAndReport" : "time", requestId }));
            return await WaitAsync(reply.Task, _timeouts.TimeReport, cancellationToken) ? reply.Task.Result : null;
        }
        finally
        {
            lock (_lock)
            {
                _timeRequests.Remove(requestId);
            }
        }
    }

    private void OnMessage(object? sender, string json)
    {
        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(json);
        }
        catch (JsonException)
        {
            return;
        }

        using (document)
        {
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("type", out var typeElement))
            {
                return;
            }

            switch (typeElement.GetString())
            {
                case "apiReady":
                    _apiReady.TrySetResult();
                    break;
                case "apiFailed":
                    FailApiStart("The video player could not reach YouTube. Check your internet connection and try again.");
                    break;
                case "ready":
                    ApplyTimes(root);
                    if (root.TryGetProperty("title", out var title) && title.ValueKind == JsonValueKind.String
                        && Video is { } video && video.Title.Length == 0 && title.GetString() is { Length: > 0 } text)
                    {
                        Video = video with { Title = text };
                    }

                    State = LecturePlayerState.Cued;
                    _loading?.TrySetResult(null);
                    RaiseChanged();
                    break;
                case "state":
                    ApplyTimes(root);
                    State = ParseState(root);
                    RaiseChanged();
                    break;
                case "error":
                    var code = root.TryGetProperty("code", out var codeElement) && codeElement.TryGetInt32(out var value) ? value : -1;
                    if (_loading?.TrySetResult(DescribeError(code)) != true && Readiness == PlaybackReadiness.Ready)
                    {
                        Readiness = PlaybackReadiness.Unavailable;
                        UnavailableReason = DescribeError(code);
                        RaiseChanged();
                    }

                    break;
                case "time":
                    ApplyTimes(root);
                    State = ParseState(root);
                    if (root.TryGetProperty("requestId", out var id) && id.GetString() is { } requestId
                        && root.TryGetProperty("positionMs", out var position) && position.TryGetInt64(out var ms) && ms >= 0)
                    {
                        TaskCompletionSource<TimeReport>? waiting;
                        lock (_lock)
                        {
                            _timeRequests.TryGetValue(requestId, out waiting);
                        }

                        waiting?.TrySetResult(new TimeReport(ms));
                    }

                    RaiseChanged();
                    break;
            }
        }
    }

    // The page could not load YouTube's player script: a waiting load fails
    // now, and the next load opens the page again for a fresh start.
    private void FailApiStart(string reason)
    {
        var failed = _apiReady;
        _apiReady = NewSignal();
        _initialized = false;
        failed.TrySetException(new PlayerUnavailableException(reason));
        _loading?.TrySetResult(reason);
    }

    private void ApplyTimes(JsonElement root)
    {
        if (root.TryGetProperty("positionMs", out var position) && position.TryGetInt64(out var ms) && ms >= 0)
        {
            PositionMs = ms;
        }

        if (root.TryGetProperty("durationMs", out var duration) && duration.TryGetInt64(out var total) && total > 0)
        {
            DurationMs = total;
        }
    }

    private static LecturePlayerState ParseState(JsonElement root) =>
        root.TryGetProperty("state", out var state) ? state.GetString() switch
        {
            "playing" => LecturePlayerState.Playing,
            "paused" => LecturePlayerState.Paused,
            "buffering" => LecturePlayerState.Buffering,
            "ended" => LecturePlayerState.Ended,
            "cued" => LecturePlayerState.Cued,
            _ => LecturePlayerState.Unstarted,
        } : LecturePlayerState.Unstarted;

    // YouTube IFrame API error codes, in words (C8 reasons in brackets).
    public static string DescribeError(int code) => code switch
    {
        2 => "This is not a valid YouTube video.", // media_missing
        5 => "This video cannot be played in Netra's player.", // unsupported_container
        100 => "This video was removed or is private.", // media_missing
        101 or 150 => "The video's owner does not allow it to be played in other apps.", // embedding_not_permitted
        153 => "YouTube refused to play this video in Netra's player.",
        _ => "This video could not be played.",
    };

    private void PostIfReady(object command)
    {
        if (Readiness == PlaybackReadiness.Ready)
        {
            _surface.Post(Command(command));
        }
    }

    private static string Command(object command) => JsonSerializer.Serialize(command);

    private static async Task<bool> WaitAsync(Task task, TimeSpan timeout, CancellationToken cancellationToken)
    {
        try
        {
            await task.WaitAsync(timeout, cancellationToken);
            return true;
        }
        catch (TimeoutException)
        {
            return false;
        }
    }

    private static TaskCompletionSource NewSignal() => new(TaskCreationOptions.RunContinuationsAsynchronously);

    private void RaiseChanged() => Changed?.Invoke(this, EventArgs.Empty);

    public void Dispose() => _surface.MessageReceived -= OnMessage;

    private sealed record TimeReport(long PositionMs);
}
