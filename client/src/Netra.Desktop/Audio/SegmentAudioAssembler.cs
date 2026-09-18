using System.IO;

namespace Netra.Desktop.Audio;

public sealed record CompleteSegmentAudio(
    string GenerationId,
    string SegmentId,
    string MediaType,
    byte[] Audio,
    bool EndOfGeneration);

public sealed record DiscardedSegmentAudio(string GenerationId, string SegmentId, string Reason);

// Joins admitted frames (BinaryAudioFrameProcessor.AudioBytesAdmitted) into
// complete per-segment audio. WPF's MediaPlayer opens whole media, not an
// appended byte stream, so audible output here is segment-granular: a
// segment plays once its end_of_segment frame arrives. Incremental playback
// needs a reviewed audio dependency (docs/team/handoffs/M5.md, INT-11).
//
// Bounds are CLIENT-LOCAL memory limits, not the undecided wire total-frame
// size (message-flow.md keeps that open with M1): a segment larger than
// MaxSegmentBytes is discarded, never truncated into partial speech.
// Only one segment is assembled at a time because M1 sends a segment's
// frames contiguously; a new segment arriving first discards the incomplete
// one rather than playing half of it.
//
// Thread-safe; called from the WebSocket receive loop.
public sealed class SegmentAudioAssembler
{
    public const int MaxSegmentBytes = 8 * 1024 * 1024;

    private readonly object _lock = new();
    private string? _generationId;
    private string? _segmentId;
    private string? _mediaType;
    private MemoryStream? _buffer;
    private bool _overflowed;

    public event EventHandler<CompleteSegmentAudio>? SegmentCompleted;
    public event EventHandler<DiscardedSegmentAudio>? SegmentDiscarded;

    // Raised for the first admitted frame of each segment (a measurement hook).
    public event EventHandler<(string GenerationId, string SegmentId)>? SegmentStarted;

    public void OnAudioBytesAdmitted(object? sender, (AudioFrameHeader Header, ReadOnlyMemory<byte> AudioBytes) frame)
    {
        var header = frame.Header;
        CompleteSegmentAudio? completed = null;
        var discarded = new List<DiscardedSegmentAudio>(2);
        var started = false;

        lock (_lock)
        {
            if (_segmentId is not null
                && (!string.Equals(_generationId, header.GenerationId, StringComparison.Ordinal)
                    || !string.Equals(_segmentId, header.SegmentId, StringComparison.Ordinal)))
            {
                discarded.Add(new DiscardedSegmentAudio(_generationId!, _segmentId, "incomplete_segment"));
                ResetLocked();
            }

            if (_segmentId is null)
            {
                _generationId = header.GenerationId;
                _segmentId = header.SegmentId;
                _mediaType = header.MediaType;
                _buffer = new MemoryStream();
                _overflowed = false;
                started = true;
            }

            if (!string.Equals(_mediaType, header.MediaType, StringComparison.OrdinalIgnoreCase))
            {
                _overflowed = true; // mixed encodings within one segment are unplayable
            }
            else if (!_overflowed)
            {
                if (_buffer!.Length + frame.AudioBytes.Length > MaxSegmentBytes)
                {
                    _overflowed = true;
                    _buffer.SetLength(0);
                }
                else
                {
                    _buffer.Write(frame.AudioBytes.Span);
                }
            }

            if (header.EndOfSegment)
            {
                if (_overflowed)
                {
                    discarded.Add(new DiscardedSegmentAudio(header.GenerationId, header.SegmentId, "segment_unplayable_or_too_large"));
                }
                else if (_buffer!.Length == 0)
                {
                    discarded.Add(new DiscardedSegmentAudio(header.GenerationId, header.SegmentId, "empty_segment"));
                }
                else
                {
                    completed = new CompleteSegmentAudio(
                        header.GenerationId, header.SegmentId, _mediaType!, _buffer.ToArray(), header.EndOfGeneration);
                }

                ResetLocked();
            }
        }

        if (started)
        {
            SegmentStarted?.Invoke(this, (header.GenerationId, header.SegmentId));
        }

        foreach (var item in discarded)
        {
            SegmentDiscarded?.Invoke(this, item);
        }

        if (completed is not null)
        {
            SegmentCompleted?.Invoke(this, completed);
        }
    }

    // STOP, disconnect or supersession: drop whatever is partially assembled.
    public void Reset()
    {
        lock (_lock)
        {
            ResetLocked();
        }
    }

    private void ResetLocked()
    {
        _buffer?.Dispose();
        _buffer = null;
        _generationId = null;
        _segmentId = null;
        _mediaType = null;
        _overflowed = false;
    }
}
