using System.Windows.Media;
using System.Windows.Threading;
using Netra.Desktop.State;

namespace Netra.Desktop.Audio;

// Local audio playback abstraction. WPF owns playback and immediate local
// interruption per docs/architecture/runtime-baseline.md ("WPF owns
// microphone access, playback and immediate local interruption"), so
// StopImmediately() must never wait on the network.
public interface IPlaybackController
{
    PlaybackSnapshot CurrentSnapshot { get; }

    event EventHandler<PlaybackSnapshot>? SnapshotChanged;
    event EventHandler<string>? PlaybackCompleted;

    void Play(Uri audioSource, string generationId, string segmentId, string sentenceId);

    // Pause and Stop are deliberately different operations. CLAUDE.md:
    // "Pause may preserve an eligible response; cancellation must not
    // silently resurrect it." Pause keeps the generation alive and
    // resumable; StopImmediately ends it, and InterruptionController then
    // fences the generation id so nothing can bring it back.
    void Pause();

    // Resumes a paused generation. Returns false when there is nothing
    // eligible to resume — including when the generation was cancelled
    // while paused, which must never resume.
    bool Resume();

    void StopImmediately();
}

public sealed class PlaybackController : IPlaybackController, IDisposable
{
    private readonly MediaPlayer _player = new();
    private readonly DispatcherTimer _positionTimer;
    private string? _activeGenerationId;

    public PlaybackController()
    {
        _player.MediaEnded += OnMediaEnded;
        _positionTimer = new DispatcherTimer(DispatcherPriority.Background)
        {
            Interval = TimeSpan.FromMilliseconds(200),
        };
        _positionTimer.Tick += (_, _) => ReportProgress();
    }

    public PlaybackSnapshot CurrentSnapshot { get; private set; } = new();

    public event EventHandler<PlaybackSnapshot>? SnapshotChanged;
    public event EventHandler<string>? PlaybackCompleted;

    public void Play(Uri audioSource, string generationId, string segmentId, string sentenceId)
    {
        // Callers (Audio/InterruptionController) are expected to check
        // ShouldPlay(generationId) before calling this; the generation-id
        // fencing there is what guarantees stopped/cancelled audio never
        // resumes. This assignment is the last line of defense.
        _activeGenerationId = generationId;

        _player.Open(audioSource);
        _player.Play();
        _positionTimer.Start();

        UpdateSnapshot(generationId, segmentId, sentenceId, PlaybackStatus.Playing, 0);
    }

    public void Pause()
    {
        if (_activeGenerationId is null || CurrentSnapshot.Status != PlaybackStatus.Playing)
        {
            return;
        }

        _positionTimer.Stop();
        _player.Pause();

        // _activeGenerationId is deliberately retained: the generation is
        // still live, just not sounding. Clearing it here is what would
        // make pause indistinguishable from stop.
        UpdateSnapshot(
            CurrentSnapshot.GenerationId, CurrentSnapshot.SegmentId, CurrentSnapshot.SentenceId,
            PlaybackStatus.Paused, (long)_player.Position.TotalMilliseconds);
    }

    public bool Resume()
    {
        if (_activeGenerationId is null || CurrentSnapshot.Status != PlaybackStatus.Paused)
        {
            return false;
        }

        _player.Play();
        _positionTimer.Start();

        UpdateSnapshot(
            CurrentSnapshot.GenerationId, CurrentSnapshot.SegmentId, CurrentSnapshot.SentenceId,
            PlaybackStatus.Playing, (long)_player.Position.TotalMilliseconds);
        return true;
    }

    public void StopImmediately()
    {
        _positionTimer.Stop();
        _player.Stop();

        // Dropping the active generation id is what makes a stop
        // unrecoverable here: Resume() has nothing to resume, so a later
        // "continue" cannot revive this generation even before
        // InterruptionController fences the id.
        _activeGenerationId = null;

        UpdateSnapshot(
            CurrentSnapshot.GenerationId, CurrentSnapshot.SegmentId, CurrentSnapshot.SentenceId,
            PlaybackStatus.Stopped, CurrentSnapshot.PositionMs);
    }

    private void ReportProgress()
    {
        if (_activeGenerationId is null)
        {
            return;
        }

        var positionMs = (long)_player.Position.TotalMilliseconds;
        UpdateSnapshot(
            CurrentSnapshot.GenerationId, CurrentSnapshot.SegmentId, CurrentSnapshot.SentenceId,
            PlaybackStatus.Playing, positionMs);
    }

    private void OnMediaEnded(object? sender, EventArgs e)
    {
        _positionTimer.Stop();
        var completedGenerationId = _activeGenerationId;
        _activeGenerationId = null;

        UpdateSnapshot(
            CurrentSnapshot.GenerationId, CurrentSnapshot.SegmentId, CurrentSnapshot.SentenceId,
            PlaybackStatus.Idle, 0);

        if (completedGenerationId is not null)
        {
            PlaybackCompleted?.Invoke(this, completedGenerationId);
        }
    }

    private void UpdateSnapshot(
        string? generationId, string? segmentId, string? sentenceId, PlaybackStatus status, long positionMs)
    {
        CurrentSnapshot = new PlaybackSnapshot
        {
            GenerationId = generationId,
            SegmentId = segmentId,
            SentenceId = sentenceId,
            Status = status,
            PositionMs = positionMs,
        };
        SnapshotChanged?.Invoke(this, CurrentSnapshot);
    }

    public void Dispose()
    {
        _positionTimer.Stop();
        _player.Close();
    }
}
