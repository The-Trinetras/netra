using System.Collections.Concurrent;
using System.Threading;
using Netra.Desktop.Audio;
using Netra.Desktop.Networking;
using Netra.Desktop.State;

namespace Netra.Desktop.Tests;

// Test-only doubles shared by the playback/transport tests added with the
// segment playback path. They simulate the WPF MediaPlayer lifecycle and the
// socket; they are not evidence of audible output.
internal sealed class ScriptedPlayer : IPlaybackController
{
    public List<(Uri Source, string GenerationId, string SegmentId, string SentenceId)> Played { get; } = new();

    public int StopCount { get; private set; }

    public PlaybackSnapshot CurrentSnapshot { get; private set; } = new();

    public event EventHandler<PlaybackSnapshot>? SnapshotChanged;
    public event EventHandler<string>? PlaybackCompleted;

    public void Play(Uri audioSource, string generationId, string segmentId, string sentenceId)
    {
        Played.Add((audioSource, generationId, segmentId, sentenceId));
        Set(new PlaybackSnapshot { GenerationId = generationId, SegmentId = segmentId, SentenceId = sentenceId, Status = PlaybackStatus.Loading });
    }

    // MediaOpened.
    public void Open() => Set(CurrentSnapshot with { Status = PlaybackStatus.Playing, PositionMs = 0 });

    public void Progress(long positionMs) => Set(CurrentSnapshot with { Status = PlaybackStatus.Playing, PositionMs = positionMs });

    // MediaEnded.
    public void End()
    {
        var generation = CurrentSnapshot.GenerationId!;
        Set(CurrentSnapshot with { Status = PlaybackStatus.Idle, PositionMs = 0 });
        PlaybackCompleted?.Invoke(this, generation);
    }

    public void Pause() => Set(CurrentSnapshot with { Status = PlaybackStatus.Paused });

    public bool Resume() => false;

    public void StopImmediately()
    {
        StopCount++;
        Set(CurrentSnapshot with { Status = PlaybackStatus.Stopped });
    }

    private void Set(PlaybackSnapshot snapshot)
    {
        CurrentSnapshot = snapshot;
        SnapshotChanged?.Invoke(this, snapshot);
    }
}

internal sealed class CapturingSocket : INetraWebSocketClient
{
    public ConcurrentQueue<string> Sent { get; } = new();

    public bool FailSends { get; set; }

    public bool IsConnected => true;

    public event EventHandler<string>? TextMessageReceived;
    public event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
#pragma warning disable CS0067 // part of the interface; not raised by these tests
    public event EventHandler<Exception>? ConnectionFaulted;
#pragma warning restore CS0067
    public event EventHandler? Disconnected;

    public int ConnectCount { get; private set; }

    public int FailNextConnects { get; set; }

    public Exception ConnectFailure { get; set; } = new System.Net.WebSockets.WebSocketException("refused");

    public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken)
    {
        ConnectCount++;
        if (FailNextConnects > 0)
        {
            FailNextConnects--;
            return Task.FromException(ConnectFailure);
        }

        return Task.CompletedTask;
    }

    public Task SendBinaryAsync(ReadOnlyMemory<byte> message, CancellationToken cancellationToken) => Task.CompletedTask;

    public Task SendTextAsync(string message, CancellationToken cancellationToken)
    {
        if (FailSends)
        {
            throw new InvalidOperationException("WebSocket is not connected.");
        }

        Sent.Enqueue(message);
        return Task.CompletedTask;
    }

    public Task CloseAsync(CancellationToken cancellationToken) => Task.CompletedTask;

    public void ReceiveText(string json) => TextMessageReceived?.Invoke(this, json);

    public void ReceiveBinary(byte[] frame) => BinaryMessageReceived?.Invoke(this, frame);

    public void Drop() => Disconnected?.Invoke(this, EventArgs.Empty);

    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
}

internal sealed class RecordingAudioStore : ISegmentAudioStore
{
    public List<CompleteSegmentAudio> Staged { get; } = new();

    public List<Uri> Released { get; } = new();

    public Uri? Stage(CompleteSegmentAudio segment)
    {
        if (!TempFileSegmentAudioStore.IsSupported(segment.MediaType))
        {
            return null;
        }

        Staged.Add(segment);
        return new Uri($"file:///staged/{Staged.Count}");
    }

    public void Release(Uri staged) => Released.Add(staged);

    public void Dispose()
    {
    }
}
