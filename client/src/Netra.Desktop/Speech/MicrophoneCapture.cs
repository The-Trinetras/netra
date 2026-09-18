using System.Threading;

namespace Netra.Desktop.Speech;

public sealed class TranscriptReceivedEventArgs : EventArgs
{
    public required string Text { get; init; }

    // Only a final transcript may become a submitted turn. An interim
    // transcript must update live captioning only and must never trigger
    // navigation (CLAUDE.md important-behavior rules).
    public required bool IsFinal { get; init; }

    // Stable identity for one recognised utterance, assigned by the
    // recognition adapter and unchanged across redelivery of the same
    // result. Required by client.md ("Deduplicate repeated final events
    // according to protocol identity"): without it a provider that emits
    // the same final transcript twice produces two turns, because each
    // send would otherwise get a fresh request_id and look distinct to
    // the server.
    public required Guid TranscriptId { get; init; }
}

// Whether held push-to-talk can actually produce transcripts. Separate from
// ISpeechInputService so existing implementations are unaffected.
public interface IRecognitionAvailability
{
    bool IsRecognitionAvailable { get; }
}

public interface ISpeechInputService : IDisposable
{
    bool IsListening { get; }

    event EventHandler<TranscriptReceivedEventArgs>? TranscriptReceived;

    Task StartListeningAsync(CancellationToken cancellationToken);
    void StopListening();
}

// Microphone lifecycle abstraction only. No recognition provider is wired
// up: CLAUDE.md's scaffold rules forbid implementing external provider
// calls for a boilerplate task, and runtime-baseline.md requires provider
// SDKs to sit behind an adapter with explicit, version-pinned configuration
// before they're introduced. A future provider implementation raises
// TranscriptReceived through this same interface.
//
// IsListening reflects the held push-to-talk mode only. No device is opened:
// microphone upload has no approved protocol (M1's server closes the socket
// with 1003 on any client binary frame) and no client-side recognizer is
// approved, so IsRecognitionAvailable is false and the UI says so instead of
// pretending to listen.
public sealed class MicrophoneCapture : ISpeechInputService, IRecognitionAvailability
{
    public bool IsListening { get; private set; }

    public bool IsRecognitionAvailable => false;

    public event EventHandler<TranscriptReceivedEventArgs>? TranscriptReceived;

    public Task StartListeningAsync(CancellationToken cancellationToken)
    {
        IsListening = true;
        // TODO: open the microphone device and begin streaming audio to a
        // recognition provider once one is selected and approved.
        return Task.CompletedTask;
    }

    public void StopListening()
    {
        IsListening = false;
        // TODO: close the microphone device / stop the provider stream.
    }

    // Hook for a future provider implementation (or a test) to raise a
    // transcript event through this instance. transcriptId must be stable
    // for one recognised utterance: pass the same value when re-raising a
    // result the provider redelivered.
    internal void RaiseTranscriptReceived(string text, bool isFinal, Guid transcriptId) =>
        TranscriptReceived?.Invoke(
            this,
            new TranscriptReceivedEventArgs
            {
                Text = text,
                IsFinal = isFinal,
                TranscriptId = transcriptId,
            });

    public void Dispose() => StopListening();
}
