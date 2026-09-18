using System.Threading;
using Netra.Desktop.Diagnostics;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;

namespace Netra.Desktop.Audio;

// Sends playback.ack messages as local playback progresses (CLAUDE.md
// "Playback acknowledgement handling"). Emits started once per segment,
// progress while that segment keeps playing, and completed when it ends
// on its own. A stopped or paused segment is not acknowledged at all.
public sealed class PlaybackAcknowledger : IDisposable
{
    private readonly IPlaybackController _playbackController;
    private readonly ConnectionManager _connectionManager;
    private readonly PlaybackTimeline? _timeline;

    // Segments already acknowledged as "started", so the first snapshot of
    // a segment reports started and every later one reports progress.
    // Without this the 200ms position timer re-sends "started" forever and
    // the server can never tell first-audio from mid-playback.
    private readonly object _lock = new();
    private readonly HashSet<string> _startedSegments = new();

    public PlaybackAcknowledger(
        IPlaybackController playbackController, ConnectionManager connectionManager, PlaybackTimeline? timeline = null)
    {
        _playbackController = playbackController;
        _connectionManager = connectionManager;
        _timeline = timeline;
        _playbackController.SnapshotChanged += OnSnapshotChanged;
    }

    private async void OnSnapshotChanged(object? sender, PlaybackSnapshot snapshot)
    {
        if (snapshot.GenerationId is null || snapshot.SegmentId is null || snapshot.SentenceId is null)
        {
            return;
        }

        if (ClassifyAck(snapshot) is not { } ackStatus)
        {
            return;
        }

        try
        {
            await _connectionManager.SendPlaybackAckAsync(
                new PlaybackAckPayload
                {
                    GenerationId = snapshot.GenerationId,
                    SegmentId = snapshot.SegmentId,
                    SentenceId = snapshot.SentenceId,
                    Status = ackStatus,
                    PlayedMs = snapshot.PositionMs,
                },
                CancellationToken.None).ConfigureAwait(false);
            _timeline?.Record(PlaybackMilestone.AckSent, snapshot.GenerationId, snapshot.SegmentId, ackStatus.ToString());
        }
        catch (Exception)
        {
            _timeline?.Record(PlaybackMilestone.AckFailed, snapshot.GenerationId, snapshot.SegmentId, ackStatus.ToString());
            // TODO: surface ack delivery failures once a retry/telemetry path
            // for outbound acks is defined. Never let this crash the app.
        }
    }

    // Maps local playback to the three ack statuses the protocol defines.
    // Paused and Stopped produce no acknowledgement: neither is a report of
    // audio the student heard, and acknowledging a stop as "completed"
    // would tell the server the student heard content they interrupted
    // (client.md: "Do not skip unheard content by acknowledging receipt as
    // completion").
    private PlaybackAckStatus? ClassifyAck(PlaybackSnapshot snapshot)
    {
        var segmentKey = $"{snapshot.GenerationId}/{snapshot.SegmentId}";

        switch (snapshot.Status)
        {
            case PlaybackStatus.Playing:
                lock (_lock)
                {
                    return _startedSegments.Add(segmentKey)
                        ? PlaybackAckStatus.Started
                        : PlaybackAckStatus.Progress;
                }

            case PlaybackStatus.Idle:
                lock (_lock)
                {
                    // Only a segment that actually started can complete.
                    // The controller's initial Idle snapshot must not be
                    // reported as a completed segment.
                    if (!_startedSegments.Remove(segmentKey))
                    {
                        return null;
                    }
                }

                return PlaybackAckStatus.Completed;

            default:
                // Stopped or paused. Forget the segment so the set does not
                // accumulate entries for segments that never complete; a
                // resumed segment simply re-acks started.
                lock (_lock)
                {
                    _startedSegments.Remove(segmentKey);
                }

                return null;
        }
    }

    public void Dispose() => _playbackController.SnapshotChanged -= OnSnapshotChanged;
}
