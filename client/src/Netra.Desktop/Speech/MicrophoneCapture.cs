using System.Text.Json;
using System.Threading;
using System.Threading.Channels;
using Netra.Desktop.Protocol;
using Netra.Desktop.Protocol.Dto;

namespace Netra.Desktop.Speech;

public sealed class TranscriptReceivedEventArgs : EventArgs
{
    public required string Text { get; init; }

    // Only a final transcript may become a submitted turn. An interim
    // transcript must update live captioning only and must never trigger
    // navigation (CLAUDE.md important-behavior rules).
    public required bool IsFinal { get; init; }

    // Stable identity for one recognised utterance, assigned by the server
    // (asr.transcript's transcript_id) and unchanged across redelivery of
    // the same result. Required by client.md ("Deduplicate repeated final
    // events according to protocol identity").
    public required Guid TranscriptId { get; init; }
}

// Whether held push-to-talk can actually produce transcripts: true only
// after the connected server has returned a transcript, and false
// again after a rejection or a lost connection.
public interface IRecognitionAvailability
{
    bool IsRecognitionAvailable { get; }
}

public enum VoiceInputState
{
    Off,
    Listening,
    Sending,
    Unavailable,
    Failed,
}

// Announce is false while the microphone is open: a screen reader speaking
// during capture would be recorded into the student's own question
// (hands-free echo cancellation is deferred).
public sealed record VoiceInputStatus(VoiceInputState State, string Message, bool Announce);

public interface ISpeechInputService : IDisposable
{
    bool IsListening { get; }

    event EventHandler<TranscriptReceivedEventArgs>? TranscriptReceived;
    event EventHandler<VoiceInputStatus>? StatusChanged;

    // Push-to-talk pressed.
    Task StartListeningAsync(CancellationToken cancellationToken);

    // Push-to-talk released: send what was said.
    void StopListening();

    // STOP or focus loss: discard the capture; it never becomes a turn.
    void AbortListening();
}

// Lets general error display skip errors that belong to a voice capture.
public interface IVoiceRequestOwner
{
    bool OwnsRequest(Guid requestId);
}

// Device boundary: 16-bit little-endian mono PCM at 16 kHz. `stop` ends
// capture after delivering the final partial buffer; `abort` discards it.
public interface IPcmSource
{
    Task CaptureAsync(Func<ReadOnlyMemory<byte>, bool> acceptFrame, CancellationToken stop, CancellationToken abort);
}

// The connection as voice input needs it (ConnectionManager).
public interface IAsrChannel
{
    bool IsConnected { get; }

    event EventHandler<ServerToClientEnvelope>? MessageReceived;
    event EventHandler? Disconnected;

    Task SendAsrStartAsync(Guid requestId, AsrStartPayload payload, CancellationToken cancellationToken);
    Task SendMicrophoneFrameAsync(ReadOnlyMemory<byte> frame, CancellationToken cancellationToken);
}

// Client-local bounds (no wire field; not product policy). Each wait ends
// in an accessible status instead of silence.
public sealed record VoiceInputOptions
{
    public TimeSpan StartTimeout { get; init; } = TimeSpan.FromSeconds(5);
    public TimeSpan FinalTranscriptTimeout { get; init; } = TimeSpan.FromSeconds(10);
    public TimeSpan MaxCaptureDuration { get; init; } = TimeSpan.FromSeconds(60);

    // Audio held behind a slow send: 6 s of 16 kHz
    // 16-bit mono. Exceeding it stops the capture; audio is never dropped.
    public int MaxBufferedAudioBytes { get; init; } = 6 * 32_000;
}

// Push-to-talk over the approved D-MIC protocol:
//   key down -> microphone opens at once; audio is held locally
//            -> asr.start {capture_id}
//   asr.start send completes       -> held and new audio is streamed as
//                                     binary frames (MicrophoneFrame)
//   key up   -> last frame with end_of_utterance
//   asr.transcript, is_final: false -> live captioning only
//   asr.transcript, is_final: true  -> TranscriptReceived; the conversation
//                                     submits it as a voice turn.submit
// There is no asr.ready handshake in D-MIC. The WebSocket preserves start /
// audio ordering; errors are correlated by the start request id.
public sealed class MicrophoneCapture : ISpeechInputService, IRecognitionAvailability, IVoiceRequestOwner
{
    // Captures remembered after they end, so late transcripts and errors
    // for them are recognised and ignored, not misread.
    private const int RememberedCaptures = 8;

    private readonly IAsrChannel _channel;
    private readonly IPcmSource _pcm;
    private readonly VoiceInputOptions _options;
    private readonly Func<TimeSpan, CancellationToken, Task> _delay;
    private readonly CancellationTokenSource _lifetime = new();
    private readonly SemaphoreSlim _deviceLock = new(1, 1);
    private readonly object _lock = new();
    private readonly List<Capture> _recent = new();
    private Capture? _current;
    private bool _available;
    private bool _disposed;

    public MicrophoneCapture(
        IAsrChannel channel,
        IPcmSource? pcm = null,
        VoiceInputOptions? options = null,
        Func<TimeSpan, CancellationToken, Task>? delay = null)
    {
        _channel = channel;
        _pcm = pcm ?? new WinMmPcmCapture();
        _options = options ?? new VoiceInputOptions();
        _delay = delay ?? Task.Delay;
        _channel.MessageReceived += OnMessageReceived;
        _channel.Disconnected += OnDisconnected;
    }

    public event EventHandler<TranscriptReceivedEventArgs>? TranscriptReceived;
    public event EventHandler<VoiceInputStatus>? StatusChanged;

    public bool IsListening
    {
        get
        {
            lock (_lock)
            {
                return _current is { Phase: CapturePhase.Capturing };
            }
        }
    }

    public bool IsRecognitionAvailable
    {
        get
        {
            lock (_lock)
            {
                return _available;
            }
        }
    }

    public bool OwnsRequest(Guid requestId)
    {
        lock (_lock)
        {
            return _recent.Exists(c => c.RequestId == requestId);
        }
    }

    public async Task StartListeningAsync(CancellationToken cancellationToken)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        cancellationToken.ThrowIfCancellationRequested();

        // A new press supersedes any earlier capture still waiting for its
        // transcript; that transcript is then ignored.
        Capture? previous;
        lock (_lock)
        {
            previous = _current;
        }

        if (previous is not null)
        {
            End(previous);
        }

        if (!_channel.IsConnected)
        {
            Report(VoiceInputState.Unavailable, "Voice input needs a connection to Netra. Type your question instead.", announce: true);
            return;
        }

        var capture = new Capture();
        lock (_lock)
        {
            _current = capture;
            _recent.Add(capture);
            while (_recent.Count > RememberedCaptures)
            {
                _recent[0].Dispose();
                _recent.RemoveAt(0);
            }
        }

        Report(VoiceInputState.Listening, "Listening.", announce: false);

        // The microphone opens now so the first words are kept; they wait in
        // the bounded buffer until asr.start has been sent. A preceding
        // capture may still be closing its device on the native thread.
        capture.CaptureTask = CaptureDeviceAsync(capture);
        _ = ObserveCaptureAsync(capture);
        _ = StreamAudioAsync(capture);
        _ = FailIfStartNotSentAsync(capture);
        _ = ReleaseAtMaximumDurationAsync(capture);

        try
        {
            await _channel.SendAsrStartAsync(
                capture.RequestId,
                new AsrStartPayload { CaptureId = capture.CaptureId },
                cancellationToken).ConfigureAwait(false);
            capture.IsStartSent = true;
            if (capture.Ended)
            {
                _ = CloseServerStreamAsync(capture);
            }
            else
            {
                capture.StartCompleted.TrySetResult();
            }
        }
        catch (Exception)
        {
            Fail(capture, VoiceInputState.Unavailable, "Voice input could not start because Netra is not connected. Type your question instead.");
        }
    }

    public void StopListening()
    {
        Capture? capture;
        lock (_lock)
        {
            capture = _current;
            if (capture is not { Phase: CapturePhase.Capturing })
            {
                return;
            }

            capture.Phase = CapturePhase.Sending;
        }

        capture.Release.Cancel();
        Report(VoiceInputState.Sending, "Sending what you said.", announce: false);
    }

    public void AbortListening()
    {
        Capture? capture;
        lock (_lock)
        {
            capture = _current;
        }

        if (capture is not null && End(capture))
        {
            Report(VoiceInputState.Off, "Voice input cancelled. Nothing was sent.", announce: false);
        }
    }

    // Runs on the capture thread: never blocks.
    private bool Accept(Capture capture, ReadOnlyMemory<byte> pcm)
    {
        if (Interlocked.Add(ref capture.BufferedBytes, pcm.Length) > _options.MaxBufferedAudioBytes)
        {
            return false;
        }

        return capture.Audio.Writer.TryWrite(pcm.ToArray());
    }

    private async Task CaptureDeviceAsync(Capture capture)
    {
        await _deviceLock.WaitAsync(capture.Abort.Token).ConfigureAwait(false);
        try
        {
            // Key-up or focus loss can occur while the previous native
            // capture closes. Never open the microphone after that release.
            capture.Abort.Token.ThrowIfCancellationRequested();
            if (capture.Release.IsCancellationRequested)
            {
                return;
            }

            await _pcm.CaptureAsync(pcm => Accept(capture, pcm), capture.Release.Token, capture.Abort.Token).ConfigureAwait(false);
        }
        finally
        {
            _deviceLock.Release();
        }
    }

    private async Task ObserveCaptureAsync(Capture capture)
    {
        try
        {
            await capture.CaptureTask!.ConfigureAwait(false);
            capture.Audio.Writer.TryComplete();
        }
        catch (OperationCanceledException) when (capture.Abort.IsCancellationRequested)
        {
            // Ended on purpose; whoever ended it reported why.
        }
        catch (Exception ex)
        {
            // WinMmPcmCapture's messages are client-owned accessible text.
            var reason = ex is InvalidOperationException or PlatformNotSupportedException
                ? ex.Message
                : "The microphone stopped unexpectedly.";
            Fail(capture, VoiceInputState.Failed, $"{reason} Type your question instead.");
        }
    }

    private async Task StreamAudioAsync(Capture capture)
    {
        var token = capture.Abort.Token;
        try
        {
            await capture.StartCompleted.Task.WaitAsync(token).ConfigureAwait(false);

            var batch = new byte[MicrophoneFrame.AudioBytesPerFrame];
            var filled = 0;
            await foreach (var chunk in capture.Audio.Reader.ReadAllAsync(token).ConfigureAwait(false))
            {
                Interlocked.Add(ref capture.BufferedBytes, -chunk.Length);
                var offset = 0;
                while (offset < chunk.Length)
                {
                    var count = Math.Min(batch.Length - filled, chunk.Length - offset);
                    Array.Copy(chunk, offset, batch, filled, count);
                    filled += count;
                    offset += count;
                    if (filled == batch.Length)
                    {
                        await SendFrameAsync(capture, batch, filled, endOfUtterance: false).ConfigureAwait(false);
                        filled = 0;
                    }
                }

                token.ThrowIfCancellationRequested();
            }

            // The microphone finished after the release (or the duration
            // bound): this frame ends the utterance, audio or not.
            if (await SendFrameAsync(capture, batch, filled, endOfUtterance: true).ConfigureAwait(false) && !capture.Ended)
            {
                Report(VoiceInputState.Sending, "Sent. Waiting for the transcript.", announce: false);
                _ = FailIfNoFinalTranscriptAsync(capture);
            }
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested)
        {
        }
        catch (Exception)
        {
            Fail(capture, VoiceInputState.Failed, "Voice input stopped because the connection was lost. Type your question instead.");
        }
    }

    // Serialized per capture so sequence numbers go out in order, and never
    // cancelled mid-send: cancelling a WebSocket send aborts the socket.
    // Returns true when this call sent the capture's end_of_utterance frame.
    private async Task<bool> SendFrameAsync(Capture capture, byte[] audio, int count, bool endOfUtterance)
    {
        await capture.SendLock.WaitAsync(_lifetime.Token).ConfigureAwait(false);
        try
        {
            if (capture.EndSent || (capture.Ended && !endOfUtterance))
            {
                return false;
            }

            // Marked before the send completes: the server's final can
            // arrive before this method's continuation runs.
            if (endOfUtterance)
            {
                capture.EndSent = true;
            }

            var frame = MicrophoneFrame.Encode(
                new MicrophoneFrameHeader
                {
                    CaptureId = capture.CaptureId,
                    Sequence = capture.NextSequence++,
                    EndOfUtterance = endOfUtterance,
                },
                audio.AsSpan(0, count));
            await _channel.SendMicrophoneFrameAsync(frame, _lifetime.Token).ConfigureAwait(false);
            return endOfUtterance;
        }
        finally
        {
            capture.SendLock.Release();
        }
    }

    // An accepted capture that ends early still gets its closing frame, so
    // the server can end its recognition stream instead of waiting.
    private async Task CloseServerStreamAsync(Capture capture)
    {
        try
        {
            await SendFrameAsync(capture, Array.Empty<byte>(), 0, endOfUtterance: true).ConfigureAwait(false);
        }
        catch (Exception)
        {
            // The connection is gone; the server ends the stream itself.
        }
    }

    private async Task FailIfStartNotSentAsync(Capture capture)
    {
        try
        {
            await _delay(_options.StartTimeout, capture.Abort.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            return;
        }

        if (!capture.IsStartSent)
        {
            SetAvailable(false);
            Fail(capture, VoiceInputState.Unavailable, "Voice input could not start: the connection stalled. Type your question instead.");
        }
    }

    private async Task ReleaseAtMaximumDurationAsync(Capture capture)
    {
        try
        {
            await _delay(_options.MaxCaptureDuration, capture.Abort.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            return;
        }

        lock (_lock)
        {
            if (capture.Phase != CapturePhase.Capturing || capture.Ended)
            {
                return;
            }

            capture.Phase = CapturePhase.Sending;
        }

        capture.Release.Cancel();
        var seconds = (int)_options.MaxCaptureDuration.TotalSeconds;
        Report(VoiceInputState.Sending, $"Voice input stopped after {seconds} seconds. Sending what you said.", announce: true);
    }

    private async Task FailIfNoFinalTranscriptAsync(Capture capture)
    {
        try
        {
            await _delay(_options.FinalTranscriptTimeout, capture.Abort.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            return;
        }

        Fail(capture, VoiceInputState.Failed, "No transcript arrived. Try again, or type your question.");
    }

    // Receive-loop thread.
    private void OnMessageReceived(object? sender, ServerToClientEnvelope envelope)
    {
        try
        {
            switch (envelope.Type)
            {
                case ServerMessageType.AsrTranscript:
                    OnTranscript(MessageParser.ParseAsrTranscript(envelope));
                    break;
                case ServerMessageType.Error:
                    OnError(envelope);
                    break;
            }
        }
        catch (Exception ex) when (ex is ProtocolException or JsonException)
        {
            // A malformed voice message cannot be attributed safely; the
            // capture's own timeouts end it with an accessible status.
        }
    }

    private void OnTranscript(AsrTranscriptPayload transcript)
    {
        var capture = Find(c => c.CaptureId == transcript.CaptureId);
        if (capture is null || capture.Ended)
        {
            return;
        }

        capture.RecognitionConfirmed = true;
        SetAvailable(true);

        // A final before this client ended the utterance cannot be the whole
        // utterance (the student is still speaking): caption only.
        var isFinal = transcript.IsFinal && capture.EndSent;
        if (!isFinal)
        {
            TranscriptReceived?.Invoke(this, new TranscriptReceivedEventArgs
            {
                Text = transcript.Text,
                IsFinal = false,
                TranscriptId = transcript.TranscriptId,
            });
            return;
        }

        if (!End(capture))
        {
            return;
        }

        if (string.IsNullOrWhiteSpace(transcript.Text))
        {
            Report(VoiceInputState.Off, "Netra did not catch that. Hold the talk key and try again.", announce: true);
            return;
        }

        Report(VoiceInputState.Off, "Voice input off.", announce: false);
        TranscriptReceived?.Invoke(this, new TranscriptReceivedEventArgs
        {
            Text = transcript.Text.Trim(),
            IsFinal = true,
            TranscriptId = transcript.TranscriptId,
        });
    }

    private void OnError(ServerToClientEnvelope envelope)
    {
        var capture = Find(c => c.RequestId == envelope.RequestId);
        if (capture is null || capture.Ended)
        {
            return;
        }

        var error = MessageParser.ParseError(envelope);
        SetAvailable(false);
        if (capture.RecognitionConfirmed)
        {
            Fail(capture, VoiceInputState.Failed, $"Voice input stopped. {error.Message} Type your question instead.");
            return;
        }

        SetAvailable(false);
        var message = error.Code is ErrorCode.InvalidRequest or ErrorCode.UnsupportedProtocolVersion
            ? "Voice input is not available on this Netra server yet. Type your question instead."
            : $"Voice input is not available. {error.Message} Type your question instead.";
        Fail(capture, VoiceInputState.Unavailable, message);
    }

    // Disconnect: the capture ends and nothing of it is resumed; a new
    // connection must accept a new capture before voice counts as working.
    private void OnDisconnected(object? sender, EventArgs e)
    {
        SetAvailable(false);
        Capture? capture;
        lock (_lock)
        {
            capture = _current;
        }

        if (capture is not null)
        {
            Fail(capture, VoiceInputState.Failed, "Voice input stopped because the connection was lost. Type your question instead.");
        }
    }

    private void Fail(Capture capture, VoiceInputState state, string message)
    {
        if (End(capture))
        {
            Report(state, message, announce: true);
        }
    }

    // Ends a capture exactly once: stops the microphone without sending its
    // remaining audio, fences its transcripts and stops its timers.
    private bool End(Capture capture)
    {
        lock (_lock)
        {
            if (capture.Ended)
            {
                return false;
            }

            capture.Ended = true;
            capture.Phase = CapturePhase.Ended;
            if (ReferenceEquals(_current, capture))
            {
                _current = null;
            }
        }

        capture.Abort.Cancel();
        capture.Audio.Writer.TryComplete();
        if (capture.IsStartSent && !capture.EndSent)
        {
            _ = CloseServerStreamAsync(capture);
        }

        return true;
    }

    private Capture? Find(Predicate<Capture> match)
    {
        lock (_lock)
        {
            return _recent.Find(match);
        }
    }

    private void SetAvailable(bool available)
    {
        lock (_lock)
        {
            _available = available;
        }
    }

    private void Report(VoiceInputState state, string message, bool announce) =>
        StatusChanged?.Invoke(this, new VoiceInputStatus(state, message, announce));

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        _channel.MessageReceived -= OnMessageReceived;
        _channel.Disconnected -= OnDisconnected;

        Capture? capture;
        lock (_lock)
        {
            capture = _current;
        }

        if (capture is not null)
        {
            End(capture);
        }

        // Token sources are left to the finalizer here: a microphone thread
        // may still be releasing the device on them during shutdown.
        _lifetime.Cancel();
    }

    private enum CapturePhase
    {
        Capturing,
        Sending,
        Ended,
    }

    private sealed class Capture : IDisposable
    {
        public Guid CaptureId { get; } = Guid.NewGuid();

        // Minted before asr.start is sent, so a fast reply always matches.
        public Guid RequestId { get; } = Guid.NewGuid();
        public CancellationTokenSource Release { get; } = new();
        public CancellationTokenSource Abort { get; } = new();
        public TaskCompletionSource StartCompleted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public Channel<byte[]> Audio { get; } = Channel.CreateUnbounded<byte[]>(
            new UnboundedChannelOptions { SingleReader = true, SingleWriter = true });
        public SemaphoreSlim SendLock { get; } = new(1, 1);
        public Task? CaptureTask { get; set; }
        public CapturePhase Phase { get; set; } = CapturePhase.Capturing;
        public int BufferedBytes;
        public long NextSequence;
        public volatile bool IsStartSent;
        public volatile bool RecognitionConfirmed;
        public volatile bool EndSent;
        public volatile bool Ended;

        public void Dispose()
        {
            Release.Dispose();
            Abort.Dispose();
        }
    }
}
