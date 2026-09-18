using System.Diagnostics;
using System.Text.Json;

namespace Netra.Desktop.Diagnostics;

// What the client can itself observe about one response's audio, in order.
// "Sent" is a server fact and never appears here; the client records what it
// RECEIVED, QUEUED, PLAYED and ACKNOWLEDGED (ack = the playback.ack send
// completed locally, not that the server durably applied it).
public enum PlaybackMilestone
{
    SegmentTextReceived,
    FirstFrameReceived,
    FrameRejected,
    SegmentAudioComplete,
    SegmentDiscarded,
    SegmentQueued,
    PlaybackRequested,
    PlaybackStarted,
    PlaybackCompleted,
    PlaybackFailed,
    AckSent,
    AckFailed,
    StopRequested,
    LocalStopReturned,
    CancelSent,
    CancelFailed,
}

public sealed record TimelineEntry(
    long Timestamp,
    PlaybackMilestone Milestone,
    string? RequestId,
    string? GenerationId,
    string? SegmentId,
    string? Detail);

// Local test measurement log (docs/architecture/arize-ax-integration.md, M5):
// one monotonic client clock (Stopwatch), joined only by EXISTING opaque
// protocol identities (request_id, generation_id, segment_id). It holds no
// utterance/response text, audio bytes, credentials or account data, is
// bounded, never leaves the machine on its own and has no AX/Modal client.
// Export is an explicit user action producing a file for M1/M4 review.
//
// Thread-safe: frames are recorded from the WebSocket receive loop while
// playback/STOP entries come from the UI thread.
public sealed class PlaybackTimeline
{
    public const int MaxEntries = 4096;
    private const int MaxRememberedGenerations = 256;

    private readonly object _lock = new();
    private readonly Queue<TimelineEntry> _entries = new();
    private readonly Dictionary<string, string> _requestByGeneration = new(StringComparer.Ordinal);
    private readonly Queue<string> _generationOrder = new();
    private readonly Func<long> _clock;
    private long _droppedEntries;

    public PlaybackTimeline()
        : this(Stopwatch.GetTimestamp, Stopwatch.Frequency)
    {
    }

    // Test seam: a deterministic clock. Frequency is ticks per second.
    public PlaybackTimeline(Func<long> clock, long frequency)
    {
        _clock = clock;
        Frequency = frequency;
    }

    public long Frequency { get; }

    public long DroppedEntries
    {
        get
        {
            lock (_lock)
            {
                return _droppedEntries;
            }
        }
    }

    // Associates a generation with the request that produced it, from the
    // response.segment envelope. Later entries that only know the generation
    // are joined to the request here rather than by any new wire field.
    public void LinkGeneration(string requestId, string generationId)
    {
        lock (_lock)
        {
            if (_requestByGeneration.ContainsKey(generationId))
            {
                return;
            }

            _requestByGeneration[generationId] = requestId;
            _generationOrder.Enqueue(generationId);
            while (_generationOrder.Count > MaxRememberedGenerations)
            {
                _requestByGeneration.Remove(_generationOrder.Dequeue());
            }
        }
    }

    public void Record(PlaybackMilestone milestone, string? generationId = null, string? segmentId = null, string? detail = null, string? requestId = null)
    {
        var timestamp = _clock();
        lock (_lock)
        {
            if (requestId is null && generationId is not null)
            {
                _requestByGeneration.TryGetValue(generationId, out requestId);
            }

            _entries.Enqueue(new TimelineEntry(timestamp, milestone, requestId, generationId, segmentId, detail));
            while (_entries.Count > MaxEntries)
            {
                _entries.Dequeue();
                _droppedEntries++;
            }
        }
    }

    public IReadOnlyList<TimelineEntry> Snapshot()
    {
        lock (_lock)
        {
            return _entries.ToArray();
        }
    }

    public void Clear()
    {
        lock (_lock)
        {
            _entries.Clear();
            _droppedEntries = 0;
        }
    }

    public double ToMilliseconds(long ticks) => ticks * 1000.0 / Frequency;

    // STOP-to-player-stopped per STOP: the elapsed client time from the
    // STOP request to the local player returning from Stop(). This is the
    // in-process bound on local silence; acoustic silence at the speaker is
    // a separate hardware measurement (loopback capture or a listener).
    public IReadOnlyList<double> StopToLocalStopMilliseconds()
    {
        var results = new List<double>();
        long? requested = null;
        foreach (var entry in Snapshot())
        {
            if (entry.Milestone == PlaybackMilestone.StopRequested)
            {
                requested = entry.Timestamp;
            }
            else if (entry.Milestone == PlaybackMilestone.LocalStopReturned && requested is { } start)
            {
                results.Add(ToMilliseconds(entry.Timestamp - start));
                requested = null;
            }
        }

        return results;
    }

    public string ExportJson(string environmentNote)
    {
        var entries = Snapshot();
        var origin = entries.Count > 0 ? entries[0].Timestamp : 0;
        var document = new
        {
            kind = "netra.client.playback_timeline",
            labelled = "Client-local test measurement. Opaque protocol ids only; no text, audio or credentials.",
            clock = new
            {
                source = "System.Diagnostics.Stopwatch (single client monotonic clock)",
                is_high_resolution = Stopwatch.IsHighResolution,
                frequency = Frequency,
            },
            environment = new
            {
                os = Environment.OSVersion.VersionString,
                runtime = Environment.Version.ToString(),
                processors = Environment.ProcessorCount,
                note = environmentNote,
            },
            dropped_entries = DroppedEntries,
            stop_to_local_stop_ms = StopToLocalStopMilliseconds(),
            entries = entries.Select(e => new
            {
                t_ms = Math.Round(ToMilliseconds(e.Timestamp - origin), 3),
                milestone = e.Milestone.ToString(),
                request_id = e.RequestId,
                generation_id = e.GenerationId,
                segment_id = e.SegmentId,
                detail = e.Detail,
            }),
        };

        return JsonSerializer.Serialize(document, new JsonSerializerOptions { WriteIndented = true });
    }
}
