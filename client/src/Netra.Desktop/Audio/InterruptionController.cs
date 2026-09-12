using System.Threading;
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

    // Guarded by _lock: StopAsync runs on the UI thread while ShouldPlay is
    // consulted from the WebSocket receive loop, so this is genuinely
    // cross-thread. A HashSet read during a concurrent write can loop
    // forever or miss an entry, and missing an entry here means cancelled
    // audio plays.
    private readonly object _lock = new();
    private readonly HashSet<string> _cancelledGenerationIds = new();
    private readonly Queue<string> _cancellationOrder = new();

    public InterruptionController(IPlaybackController playbackController, ConnectionManager connectionManager)
    {
        _playbackController = playbackController;
        _connectionManager = connectionManager;
    }

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
        var activeGenerationId = _playbackController.CurrentSnapshot.GenerationId;

        // Local stop happens synchronously, before any network call.
        _playbackController.StopImmediately();

        if (activeGenerationId is not null)
        {
            Fence(activeGenerationId);
        }

        await _connectionManager.SendResponseCancelAsync(
            new ResponseCancelPayload
            {
                CancelRequestId = Guid.NewGuid(),
                GenerationId = activeGenerationId,
                Reason = reason,
            },
            cancellationToken).ConfigureAwait(false);
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
    }
}
