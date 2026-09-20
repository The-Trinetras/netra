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
