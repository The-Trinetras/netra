using System.IO;
using Netra.Desktop.Diagnostics;
using Netra.Desktop.State;

namespace Netra.Desktop.Audio;

// Turns complete segment audio into actual MediaPlayer output, in arrival
// order, for the one admitted generation. Must be used on the UI thread
// (MediaPlayer has dispatcher affinity); callers marshal through
// IUiDispatcher.
//
// Rules this class enforces locally:
// - One speaking generation: a segment of a different generation replaces
//   the queue (supersession), and a fenced (stopped/cancelled/disconnected)
//   generation never plays, even if its audio completed earlier.
// - Acknowledgement identity comes from the response.segment that
//   announced the segment (RegisterSegment); audio for a segment the client
//   never saw announced is not played, so an ack can never name unknown text.
// - The queue is bounded; overflow drops the NEWEST audio (reported), never
//   silently reorders or skips already-queued speech.
// - A playback failure stops the rest of the generation's audio: skipping
//   ahead would leave an unheard gap. Accessible text stays in the transcript.
public sealed class SegmentPlaybackQueue : IDisposable
{
    public const int MaxQueuedSegments = 32;
    private const int MaxRegisteredSegments = 256;

    private readonly IPlaybackController _player;
    private readonly InterruptionController _interruptionController;
    private readonly ISegmentAudioStore _store;
    private readonly PlaybackTimeline? _timeline;

    private readonly Dictionary<(string Generation, string Segment), string> _sentenceBySegment = new();
    private readonly Queue<(string Generation, string Segment)> _registrationOrder = new();
    private readonly Queue<CompleteSegmentAudio> _queue = new();

    private string? _generationId;
    private (CompleteSegmentAudio Segment, Uri Source)? _current;
    private bool _currentStarted;

    public SegmentPlaybackQueue(
        IPlaybackController player,
        InterruptionController interruptionController,
        ISegmentAudioStore store,
        PlaybackTimeline? timeline = null)
    {
        _player = player;
        _interruptionController = interruptionController;
        _store = store;
        _timeline = timeline;
        _player.PlaybackCompleted += OnPlaybackCompleted;
        _player.SnapshotChanged += OnSnapshotChanged;
        _interruptionController.GenerationFenced += OnGenerationFenced;
    }

    // Accessible, non-technical status for the live region.
    public event EventHandler<string>? StatusChanged;

    public int QueuedCount => _queue.Count;

    public bool IsSpeaking => _current is not null;

    public void RegisterSegment(string generationId, string segmentId, string sentenceId)
    {
        var key = (generationId, segmentId);
        if (_sentenceBySegment.ContainsKey(key))
        {
            return;
        }

        _sentenceBySegment[key] = sentenceId;
        _registrationOrder.Enqueue(key);
        while (_registrationOrder.Count > MaxRegisteredSegments)
        {
            _sentenceBySegment.Remove(_registrationOrder.Dequeue());
        }
    }

    public void Enqueue(CompleteSegmentAudio segment)
    {
        if (!_interruptionController.ShouldPlay(segment.GenerationId))
        {
            _timeline?.Record(PlaybackMilestone.SegmentDiscarded, segment.GenerationId, segment.SegmentId, "fenced");
            return;
        }

        if (!_sentenceBySegment.ContainsKey((segment.GenerationId, segment.SegmentId)))
        {
            _timeline?.Record(PlaybackMilestone.SegmentDiscarded, segment.GenerationId, segment.SegmentId, "unannounced_segment");
            return;
        }

        if (!string.Equals(_generationId, segment.GenerationId, StringComparison.Ordinal))
        {
            // A new generation supersedes whatever this client was still
            // queueing. The server cancels the old generation itself; this
            // only makes sure its leftovers never sound here.
            DropQueued();
            if (_current is not null)
            {
                StopCurrent();
            }

            _generationId = segment.GenerationId;
        }

        if (_queue.Count >= MaxQueuedSegments)
        {
            _timeline?.Record(PlaybackMilestone.SegmentDiscarded, segment.GenerationId, segment.SegmentId, "queue_full");
            StatusChanged?.Invoke(this, "Some spoken audio was dropped. The text is still in the conversation.");
            return;
        }

        _queue.Enqueue(segment);
        _timeline?.Record(PlaybackMilestone.SegmentQueued, segment.GenerationId, segment.SegmentId);
        PlayNextIfIdle();
    }

    // Wired to PlaybackController.PlaybackFailed.
    public void OnPlaybackFailed(object? sender, string generationId)
    {
        if (_current is not { } current || !string.Equals(current.Segment.GenerationId, generationId, StringComparison.Ordinal))
        {
            return;
        }

        _timeline?.Record(PlaybackMilestone.PlaybackFailed, current.Segment.GenerationId, current.Segment.SegmentId, "media_failed");
        ReleaseCurrent();
        DropQueued();
        StatusChanged?.Invoke(this, "Spoken audio could not be played. The text is still in the conversation.");
    }

    private void PlayNextIfIdle()
    {
        while (_current is null && _queue.Count > 0)
        {
            var next = _queue.Dequeue();
            if (!_interruptionController.ShouldPlay(next.GenerationId))
            {
                _timeline?.Record(PlaybackMilestone.SegmentDiscarded, next.GenerationId, next.SegmentId, "fenced");
                continue;
            }

            if (!_sentenceBySegment.TryGetValue((next.GenerationId, next.SegmentId), out var sentenceId))
            {
                _timeline?.Record(PlaybackMilestone.SegmentDiscarded, next.GenerationId, next.SegmentId, "unannounced_segment");
                continue;
            }

            Uri? source;
            try
            {
                source = _store.Stage(next);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                source = null;
            }

            if (source is null)
            {
                _timeline?.Record(PlaybackMilestone.PlaybackFailed, next.GenerationId, next.SegmentId,
                    TempFileSegmentAudioStore.IsSupported(next.MediaType) ? "stage_failed" : "unsupported_media_type");
                DropQueued();
                StatusChanged?.Invoke(this, "Spoken audio is not available for this response. The text is still in the conversation.");
                return;
            }

            _current = (next, source);
            _currentStarted = false;
            _timeline?.Record(PlaybackMilestone.PlaybackRequested, next.GenerationId, next.SegmentId);
            _player.Play(source, next.GenerationId, next.SegmentId, sentenceId);
        }
    }

    private void OnSnapshotChanged(object? sender, PlaybackSnapshot snapshot)
    {
        // First Playing snapshot of the current segment = the player opened
        // the media and started it (PlaybackController only reports Playing
        // after MediaOpened). Recorded once per segment.
        if (snapshot.Status == PlaybackStatus.Playing
            && _current is { } current
            && string.Equals(snapshot.SegmentId, current.Segment.SegmentId, StringComparison.Ordinal)
            && string.Equals(snapshot.GenerationId, current.Segment.GenerationId, StringComparison.Ordinal)
            && !_currentStarted)
        {
            _currentStarted = true;
            _timeline?.Record(PlaybackMilestone.PlaybackStarted, current.Segment.GenerationId, current.Segment.SegmentId);
        }
    }

    private void OnPlaybackCompleted(object? sender, string generationId)
    {
        if (_current is not { } current || !string.Equals(current.Segment.GenerationId, generationId, StringComparison.Ordinal))
        {
            return;
        }

        _timeline?.Record(PlaybackMilestone.PlaybackCompleted, current.Segment.GenerationId, current.Segment.SegmentId);
        ReleaseCurrent();
        PlayNextIfIdle();
    }

    // STOP (local), disconnect and response.cancel all fence through
    // InterruptionController, which has already stopped the player.
    private void OnGenerationFenced(object? sender, string generationId)
    {
        if (string.Equals(_generationId, generationId, StringComparison.Ordinal))
        {
            DropQueued();
            ReleaseCurrent();
            _generationId = null;
        }
    }

    private void StopCurrent()
    {
        _player.StopImmediately();
        ReleaseCurrent();
    }

    private void ReleaseCurrent()
    {
        if (_current is { } current)
        {
            _current = null;
            _store.Release(current.Source);
        }
    }

    private void DropQueued()
    {
        while (_queue.Count > 0)
        {
            var dropped = _queue.Dequeue();
            _timeline?.Record(PlaybackMilestone.SegmentDiscarded, dropped.GenerationId, dropped.SegmentId, "dropped_with_generation");
        }
    }

    public void Dispose()
    {
        _player.PlaybackCompleted -= OnPlaybackCompleted;
        _player.SnapshotChanged -= OnSnapshotChanged;
        _interruptionController.GenerationFenced -= OnGenerationFenced;
        DropQueued();
        ReleaseCurrent();
    }
}
