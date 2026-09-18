using System.Threading;
using Netra.Desktop.Diagnostics;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;

namespace Netra.Desktop.Audio;

// Coordinates the "stop now, tell the server after" contract required by
// CLAUDE.md's accessibility rules: "Stopping audio locally must not wait for
// the server. Server cancellation follows after local playback stops."
//
// Also enforces "Old audio must never resume after stop/cancel" by fencing
// on generation id: once a generation is cancelled, ShouldPlay(...) rejects
// it even if a queued/late-arriving segment for it shows up afterward.
public sealed class InterruptionController
{
    // How many cancelled generation ids to remember. The fence is consulted
    // for late and queued segments, which arrive shortly after the stop, so
    // a bounded recent history is sufficient and an unbounded set would
    // grow for the lifetime of the session (client.md: "Use bounded queues
    // and backpressure").
    private const int MaxRememberedCancellations = 256;

    private readonly IPlaybackController _playbackController;
    private readonly ConnectionManager _connectionManager;
    private readonly Func<string?> _announcedGenerationId;
    private readonly PlaybackTimeline? _timeline;

    // Guarded by _lock: StopAsync runs on the UI thread while ShouldPlay is
    // consulted from the WebSocket receive loop, so this is genuinely
    // cross-thread. A HashSet read during a concurrent write can loop
    // forever or miss an entry, and missing an entry here means cancelled
    // audio plays.
    private readonly object _lock = new();
    private readonly HashSet<string> _cancelledGenerationIds = new();
    private readonly Queue<string> _cancellationOrder = new();

    // announcedGenerationId: the generation the server most recently
    // announced with response.segment. STOP during a network wait (text or
    // audio still in flight, nothing playing yet) must fence THAT generation
    // too, or its audio would start after the student pressed STOP.
    public InterruptionController(
        IPlaybackController playbackController,
        ConnectionManager connectionManager,
        Func<string?>? announcedGenerationId = null,
        PlaybackTimeline? timeline = null)
    {
        _playbackController = playbackController;
        _connectionManager = connectionManager;
        _announcedGenerationId = announcedGenerationId ?? (() => null);
        _timeline = timeline;
        _connectionManager.Disconnected += OnDisconnected;
    }

    // Raised whenever a generation is fenced (by StopAsync or by
    // disconnect), naming the generation id that was fenced. A subscriber
    // such as BinaryAudioFrameProcessor uses this to stop admitting frames
    // for it — see the audio-framing rule "disconnect fences the current
    // generation."
    public event EventHandler<string>? GenerationFenced;

    public bool IsCancelled(string generationId)
    {
        lock (_lock)
        {
            return _cancelledGenerationIds.Contains(generationId);
        }
    }

    public bool ShouldPlay(string generationId) => !IsCancelled(generationId);

    public async Task StopAsync(CancelReason reason, CancellationToken cancellationToken)
    {
        var playingGenerationId = _playbackController.CurrentSnapshot.GenerationId;
        var announcedGenerationId = _announcedGenerationId();
        _timeline?.Record(PlaybackMilestone.StopRequested, playingGenerationId ?? announcedGenerationId, detail: reason.ToString());

        // Local stop happens synchronously, before any network call.
        _playbackController.StopImmediately();
        _timeline?.Record(PlaybackMilestone.LocalStopReturned, playingGenerationId ?? announcedGenerationId);

        if (playingGenerationId is not null)
        {
            Fence(playingGenerationId);
        }

        if (announcedGenerationId is not null)
        {
            Fence(announcedGenerationId);
        }

        // The newest generation is the one the server is still producing.
        var cancelGenerationId = announcedGenerationId ?? playingGenerationId;
        try
        {
            await _connectionManager.SendResponseCancelAsync(
                new ResponseCancelPayload
                {
                    CancelRequestId = Guid.NewGuid(),
                    GenerationId = cancelGenerationId,
                    Reason = reason,
                },
                cancellationToken).ConfigureAwait(false);
            _timeline?.Record(PlaybackMilestone.CancelSent, cancelGenerationId);
        }
        catch (Exception)
        {
            // Local silence already happened; the cancel could not be sent
            // (e.g. disconnected). Record it and let the caller surface it.
            _timeline?.Record(PlaybackMilestone.CancelFailed, cancelGenerationId);
            throw;
        }
    }

    // A disconnect fences whatever generation was active, exactly like a
    // local STOP, but never sends response.cancel — there is no connection
    // to send it on, and CLAUDE.md already treats "reconnection cannot
    // restore cancelled output" as the governing rule: this is what makes
    // that true rather than merely stated.
    private void OnDisconnected(object? sender, EventArgs e)
    {
        var activeGenerationId = _playbackController.CurrentSnapshot.GenerationId;
        var announcedGenerationId = _announcedGenerationId();

        _playbackController.StopImmediately();

        if (activeGenerationId is not null)
        {
            Fence(activeGenerationId);
        }

        if (announcedGenerationId is not null)
        {
            Fence(announcedGenerationId);
        }
    }

    private void Fence(string generationId)
    {
        lock (_lock)
        {
            if (!_cancelledGenerationIds.Add(generationId))
            {
                return;
            }

            _cancellationOrder.Enqueue(generationId);

            while (_cancellationOrder.Count > MaxRememberedCancellations)
            {
                _cancelledGenerationIds.Remove(_cancellationOrder.Dequeue());
            }
        }

        GenerationFenced?.Invoke(this, generationId);
    }
}
