using System.Buffers.Binary;
using System.Text;
using System.Text.Json;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.Speech;

namespace Netra.Desktop.Tests;

internal sealed record SentMicrophoneFrame(Guid CaptureId, long Sequence, bool EndOfUtterance, string MediaType, int Version, byte[] Audio);

// Decodes a microphone frame the way a server must: length prefix, strict
// header, then the audio bytes exactly as sent (little-endian PCM).
internal static class MicrophoneFrameReader
{
    public static SentMicrophoneFrame Read(ReadOnlySpan<byte> frame)
    {
        var headerLength = checked((int)BinaryPrimitives.ReadUInt32BigEndian(frame));
        using var header = JsonDocument.Parse(frame.Slice(4, headerLength).ToArray());
        var root = header.RootElement;
        var names = root.EnumerateObject().Select(p => p.Name).OrderBy(n => n, StringComparer.Ordinal).ToArray();
        if (!names.SequenceEqual(new[] { "capture_id", "end_of_utterance", "media_type", "sequence", "version" }))
        {
            throw new InvalidDataException("Unexpected microphone header fields: " + string.Join(",", names));
        }

        return new SentMicrophoneFrame(
            root.GetProperty("capture_id").GetGuid(),
            root.GetProperty("sequence").GetInt64(),
            root.GetProperty("end_of_utterance").GetBoolean(),
            root.GetProperty("media_type").GetString()!,
            root.GetProperty("version").GetInt32(),
            frame[(4 + headerLength)..].ToArray());
    }


}

internal sealed class FakeAsrChannel : IAsrChannel
{
    private readonly object _lock = new();

    public bool IsConnected { get; set; } = true;
    public TaskCompletionSource? StartGate { get; set; }
    public bool FailFrameSends { get; set; }
    public List<(Guid RequestId, AsrStartPayload Payload)> Starts { get; } = new();
    public List<SentMicrophoneFrame> Frames { get; } = new();

    // Runs inside SendMicrophoneFrameAsync, before it completes: models a
    // server reply racing the send's continuation.
    public Action<SentMicrophoneFrame>? OnFrameSending { get; set; }

    public event EventHandler<ServerToClientEnvelope>? MessageReceived;
    public event EventHandler? Disconnected;

    public Task SendAsrStartAsync(Guid requestId, AsrStartPayload payload, CancellationToken cancellationToken)
    {
        if (!IsConnected)
        {
            return Task.FromException(new NotConnectedException());
        }

        lock (_lock)
        {
            Starts.Add((requestId, payload));
        }

        return StartGate?.Task ?? Task.CompletedTask;
    }

    public Task SendMicrophoneFrameAsync(ReadOnlyMemory<byte> frame, CancellationToken cancellationToken)
    {
        if (FailFrameSends || !IsConnected)
        {
            return Task.FromException(new NotConnectedException());
        }

        var decoded = MicrophoneFrameReader.Read(frame.Span);
        lock (_lock)
        {
            Frames.Add(decoded);
        }

        OnFrameSending?.Invoke(decoded);
        return Task.CompletedTask;
    }

    public SentMicrophoneFrame[] FramesSnapshot()
    {
        lock (_lock)
        {
            return Frames.ToArray();
        }
    }

    public (Guid RequestId, AsrStartPayload Payload) SingleStart()
    {
        lock (_lock)
        {
            return Starts.Single();
        }
    }

    public void Transcript(Guid captureId, Guid requestId, string text, bool isFinal, Guid? transcriptId = null) =>
        Raise(ServerMessageType.AsrTranscript, requestId, new AsrTranscriptPayload
        {
            CaptureId = captureId,
            TranscriptId = transcriptId ?? Guid.NewGuid(),
            Text = text,
            IsFinal = isFinal,
        });

    public void Error(Guid requestId, ErrorCode code, string message) =>
        Raise(ServerMessageType.Error, requestId, new ErrorPayload { Code = code, Message = message, Retryable = false });

    public void Drop()
    {
        IsConnected = false;
        Disconnected?.Invoke(this, EventArgs.Empty);
    }

    private void Raise<T>(ServerMessageType type, Guid requestId, T payload) =>
        MessageReceived?.Invoke(this, new ServerToClientEnvelope
        {
            MessageId = Guid.NewGuid(),
            SessionId = Guid.NewGuid(),
            RequestId = requestId,
            Sequence = 1,
            Type = type,
            Payload = JsonSerializer.SerializeToElement(payload, NetraJsonSerialization.Options),
        });
}

// A microphone the test speaks into. Release delivers FinalChunk (the
// partial buffer WinMM returns on release), abort discards, and an
// overflowing consumer fails the capture as WinMmPcmCapture does.
internal sealed class FakePcmSource : IPcmSource
{
    private Func<ReadOnlyMemory<byte>, bool>? _accept;
    private TaskCompletionSource? _running;

    public int Opened { get; private set; }
    public bool Aborted { get; private set; }
    public byte[]? FinalChunk { get; set; }
    public Exception? FailOnOpen { get; set; }

    public Task CaptureAsync(Func<ReadOnlyMemory<byte>, bool> acceptFrame, CancellationToken stop, CancellationToken abort)
    {
        Opened++;
        if (FailOnOpen is not null)
        {
            return Task.FromException(FailOnOpen);
        }

        _accept = acceptFrame;
        var running = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        _running = running;
        abort.Register(() =>
        {
            Aborted = true;
            running.TrySetCanceled(abort);
        });
        stop.Register(() =>
        {
            if (running.Task.IsCompleted)
            {
                return;
            }

            if (FinalChunk is not null && !acceptFrame(FinalChunk))
            {
                running.TrySetException(new InvalidOperationException("Voice input could not keep up. Please try again."));
                return;
            }

            running.TrySetResult();
        });
        return running.Task;
    }

    public bool Speak(byte[] pcm)
    {
        if (_running is not { Task.IsCompleted: false } running || _accept is null)
        {
            return false;
        }

        if (_accept(pcm))
        {
            return true;
        }

        running.TrySetException(new InvalidOperationException("Voice input could not keep up. Please try again."));
        return false;
    }
}

// Delays that only elapse when the test says so.
internal sealed class ManualDelays
{
    private readonly object _lock = new();
    private readonly List<(TimeSpan Delay, TaskCompletionSource Done)> _pending = new();

    public Task Delay(TimeSpan delay, CancellationToken cancellationToken)
    {
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        cancellationToken.Register(() => done.TrySetCanceled(cancellationToken));
        lock (_lock)
        {
            _pending.Add((delay, done));
        }

        return done.Task;
    }

    public void Elapse(TimeSpan delay)
    {
        List<TaskCompletionSource> due;
        lock (_lock)
        {
            due = _pending.Where(p => p.Delay == delay && !p.Done.Task.IsCompleted).Select(p => p.Done).ToList();
        }

        foreach (var done in due)
        {
            done.TrySetResult();
        }
    }
}

internal static class Eventually
{
    public static async Task TrueAsync(Func<bool> condition, string because)
    {
        var deadline = DateTime.UtcNow + TimeSpan.FromSeconds(5);
        while (!condition())
        {
            if (DateTime.UtcNow > deadline)
            {
                throw new TimeoutException("Timed out waiting until " + because);
            }

            await Task.Delay(5);
        }
    }

    // Lets fire-and-forget work settle before asserting that something did
    // NOT happen.
    public static Task SettleAsync() => Task.Delay(150);
}

internal static class Pcm
{
    // n little-endian samples with distinct values, starting at `first`.
    public static byte[] Samples(int count, int first)
    {
        var bytes = new byte[count * 2];
        for (var i = 0; i < count; i++)
        {
            BinaryPrimitives.WriteInt16LittleEndian(bytes.AsSpan(i * 2), (short)(first + i));
        }

        return bytes;
    }

    public static string Describe(byte[] bytes) => Encoding.ASCII.GetString(bytes);
}
