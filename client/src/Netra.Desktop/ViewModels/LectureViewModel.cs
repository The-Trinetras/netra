using System.Threading;
using System.Windows.Input;
using Netra.Desktop.Library;
using Netra.Desktop.Video;

namespace Netra.Desktop.ViewModels;

// The Lecture tab (F8, M5-VIDEO): an embedded YouTube lecture operated
// entirely from WPF with the keyboard. Playback readiness and analysis
// readiness are two separate lines and are never merged into one "ready".
// Times are spoken in words. The lecture never plays on its own: a question
// pauses it, and only Continue resumes it, at exactly the paused position.
public sealed class LectureViewModel : ViewModelBase, IDisposable
{
    public const long SkipMilliseconds = 10_000;

    // Until the server serves C8 (readiness route) for a selected video,
    // there is no analysis fact to show; saying so is the honest line.
    public const string AnalysisNotReported =
        "Analysis: not available. This Netra server does not report lecture analysis yet, so Netra cannot describe what the video shows.";

    private readonly LecturePlayerController _player;
    private string _linkText = string.Empty;
    private string _statusMessage = string.Empty;

    public LectureViewModel(LecturePlayerController player)
    {
        _player = player;
        _player.Changed += OnPlayerChanged;

        OpenLinkCommand = new RelayCommand(_ => _ = OpenLinkAsync(CancellationToken.None));
        PlayPauseCommand = new RelayCommand(_ => PlayPause());
        BackCommand = new RelayCommand(_ => Skip(-SkipMilliseconds));
        ForwardCommand = new RelayCommand(_ => Skip(SkipMilliseconds));
        WhereAmICommand = new RelayCommand(_ => _ = WhereAmIAsync(CancellationToken.None));
        ContinueCommand = new RelayCommand(_ => Continue());
        DescribeCommand = new RelayCommand(_ => _ = DescribeAsync(CancellationToken.None));
    }

    public string LinkText
    {
        get => _linkText;
        set => SetField(ref _linkText, value);
    }

    // Announced through the view's live region.
    public string StatusMessage
    {
        get => _statusMessage;
        private set => SetField(ref _statusMessage, value);
    }

    public string Title => _player.Video is { } video
        ? (video.Title.Length > 0 ? video.Title : $"YouTube video {video.Id}")
        : "No lecture open.";

    public string PlaybackLine => _player.Readiness switch
    {
        PlaybackReadiness.NotLoaded => "Playback: no lecture open.",
        PlaybackReadiness.Loading => "Playback: loading.",
        PlaybackReadiness.Ready => "Playback: ready.",
        _ => $"Playback: not available. {_player.UnavailableReason}",
    };

    public string AnalysisLine => AnalysisNotReported;

    public string TimeText => _player.Readiness == PlaybackReadiness.Ready
        ? $"{SpokenTime.Clock(_player.PositionMs)}{(_player.DurationMs is { } total ? " of " + SpokenTime.Clock(total) : string.Empty)}, {StateWord}"
        : string.Empty;

    public string PausePointText => _player.PausedForQuestionAtMs is { } position
        ? $"Paused for your question at {SpokenTime.Words(position)}. Continue resumes from there."
        : string.Empty;

    public bool IsReady => _player.Readiness == PlaybackReadiness.Ready;

    public ICommand OpenLinkCommand { get; }
    public ICommand PlayPauseCommand { get; }
    public ICommand BackCommand { get; }
    public ICommand ForwardCommand { get; }
    public ICommand WhereAmICommand { get; }
    public ICommand ContinueCommand { get; }
    public ICommand DescribeCommand { get; }

    private string StateWord => _player.State switch
    {
        LecturePlayerState.Playing => "playing",
        LecturePlayerState.Buffering => "loading more video",
        LecturePlayerState.Ended => "ended",
        LecturePlayerState.Paused => "paused",
        _ => "not started",
    };

    public async Task OpenLinkAsync(CancellationToken cancellationToken)
    {
        if (!YouTubeVideoId.TryParseLink(LinkText, out var id))
        {
            StatusMessage = "That is not a YouTube video link. Paste a link from YouTube, such as youtube.com/watch?v= followed by the video code.";
            return;
        }

        await OpenAsync(new LectureVideo(id!, string.Empty), cancellationToken);
    }

    // A numbered lecture result chosen in the library, kept exactly.
    public async Task OpenResultAsync(VideoDiscoveryResult result, CancellationToken cancellationToken)
    {
        if (!YouTubeVideoId.IsValid(result.VideoId))
        {
            StatusMessage = $"Result {result.Ordinal}, {result.Title}, has no playable YouTube video. Search results are still fixture data.";
            return;
        }

        await OpenAsync(new LectureVideo(YouTubeVideoId.Parse(result.VideoId), result.Title), cancellationToken);
    }

    public async Task OpenAsync(LectureVideo video, CancellationToken cancellationToken)
    {
        StatusMessage = "Loading the lecture.";
        await _player.LoadAsync(video, cancellationToken);
        if (!ReferenceEquals(_player.Video, video) && _player.Video?.Id != video.Id)
        {
            return; // Another lecture was chosen meanwhile.
        }

        StatusMessage = _player.Readiness == PlaybackReadiness.Ready
            ? $"Lecture ready: {Title}. Press K to play or pause."
            : $"The lecture cannot be played. {_player.UnavailableReason}";
    }

    private void PlayPause()
    {
        if (!RequireLecture())
        {
            return;
        }

        var willPlay = !_player.IsPlaying;
        if (willPlay && _player.PausedForQuestionAtMs is not null)
        {
            Continue();
            return;
        }

        _player.TogglePlay();
        StatusMessage = willPlay ? "Playing." : "Paused.";
    }

    private void Skip(long deltaMs)
    {
        if (!RequireLecture())
        {
            return;
        }

        _player.SeekBy(deltaMs);
        StatusMessage = deltaMs < 0 ? "Back 10 seconds." : "Forward 10 seconds.";
    }

    private async Task WhereAmIAsync(CancellationToken cancellationToken)
    {
        if (!RequireLecture())
        {
            return;
        }

        var position = await _player.ReadTimeAsync(cancellationToken);
        StatusMessage = position is { } ms
            ? $"{SpokenTime.Words(ms)}{(_player.DurationMs is { } total ? " of " + SpokenTime.Words(total) : string.Empty)}, {StateWord}."
            : "The video's time could not be read.";
    }

    private void Continue()
    {
        if (!RequireLecture())
        {
            return;
        }

        var from = _player.PausedForQuestionAtMs;
        _player.ContinueFromPause();
        StatusMessage = from is { } ms ? $"Continuing from {SpokenTime.Words(ms)}." : "Playing.";
    }

    // Pause-and-describe: the lecture pauses and its actual time is kept.
    // Sending that time with a question needs C8 on the server; until then
    // the student is told what is and is not possible.
    private async Task DescribeAsync(CancellationToken cancellationToken)
    {
        if (!RequireLecture())
        {
            return;
        }

        var position = await _player.PauseForQuestionAsync(cancellationToken);
        StatusMessage = position is { } ms
            ? $"Paused at {SpokenTime.Words(ms)}. Netra cannot describe what is shown yet: this server does not report lecture analysis."
            : "The video is paused, but its time could not be read.";
    }

    private bool RequireLecture()
    {
        if (_player.Readiness == PlaybackReadiness.Ready)
        {
            return true;
        }

        StatusMessage = _player.Readiness == PlaybackReadiness.Loading
            ? "The lecture is still loading."
            : "Open a lecture first: choose Play in the library's lecture results, or paste a YouTube link.";
        return false;
    }

    private void OnPlayerChanged(object? sender, EventArgs e)
    {
        OnPropertyChanged(nameof(Title));
        OnPropertyChanged(nameof(PlaybackLine));
        OnPropertyChanged(nameof(TimeText));
        OnPropertyChanged(nameof(PausePointText));
        OnPropertyChanged(nameof(IsReady));
    }

    public void Dispose() => _player.Changed -= OnPlayerChanged;
}
