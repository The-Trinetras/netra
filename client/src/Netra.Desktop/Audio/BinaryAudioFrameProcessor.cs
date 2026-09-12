namespace Netra.Desktop.Audio;

// Gates incoming binary audio frames (server -> client synthesized speech
// only) before anything downstream may treat them as playable.
//
// "Only frames belonging to the explicitly admitted active generation may
// enter playback. Receiving a frame for an unknown generation MUST NOT
// automatically make that generation active." AdmitGeneration is therefore
// the ONLY way _activeGenerationId is ever set — never a side effect of
// parsing a frame. The normal caller is the response-delivery path, once it
// has told the student a new generation_id is about to speak (e.g. from a
// response.segment), not this processor itself.
//
// This class decides ELIGIBILITY, not final playback: eligible frames are
// raised via AudioBytesAdmitted for a playback pipeline to consume. WPF's
// MediaPlayer plays from a Uri/stream, not incremental byte chunks, so
// wiring admitted bytes into actual speaker output is a separate,
// unimplemented integration step — see the accompanying report.
public sealed class BinaryAudioFrameProcessor
{
    private readonly InterruptionController _interruptionController;

    private readonly object _lock = new();
    private string? _activeGenerationId;
    private GenerationSequenceTracker? _sequenceTracker;

    public BinaryAudioFrameProcessor(InterruptionController interruptionController)
    {
        _interruptionController = interruptionController;
    }

    public event EventHandler<(AudioFrameHeader Header, ReadOnlyMemory<byte> AudioBytes)>? AudioBytesAdmitted;

    // Explicit admission. Starting a new generation always replaces
    // whatever was previously admitted, even if that generation never
    // reached end_of_generation — a superseding response is expected to
    // interrupt, not queue behind, the one it replaces.
    public void AdmitGeneration(string generationId)
    {
        lock (_lock)
        {
            _activeGenerationId = generationId;
            _sequenceTracker = new GenerationSequenceTracker(generationId);
        }
    }

    // Called on local STOP and on disconnect: no generation is eligible
    // until AdmitGeneration is called again for a new one.
    public void ClearActiveGeneration()
    {
        lock (_lock)
        {
            _activeGenerationId = null;
            _sequenceTracker = null;
        }
    }

    // Wired to INetraWebSocketClient.BinaryMessageReceived. Never throws:
    // a malformed or ineligible frame is simply dropped, the same way a
    // dropped network packet would be — it must not crash the receive loop.
    public void OnBinaryMessageReceived(object? sender, ReadOnlyMemory<byte> frame)
    {
        AudioFrameHeader header;
        ReadOnlyMemory<byte> audioBytes;
        try
        {
            (header, audioBytes) = BinaryAudioFrame.Parse(frame);
        }
        catch (AudioFrameException)
        {
            return;
        }

        GenerationSequenceTracker? tracker;
        lock (_lock)
        {
            if (_activeGenerationId is null || header.GenerationId != _activeGenerationId)
            {
                // Unknown or not-yet-admitted generation: never auto-activates.
                return;
            }

            tracker = _sequenceTracker;
        }

        if (!_interruptionController.ShouldPlay(header.GenerationId))
        {
            // Cancelled, superseded, or pre-reconnect: fenced permanently.
            return;
        }

        if (tracker is null || !tracker.Admit(header))
        {
            // Duplicate or decreasing sequence within this generation.
            return;
        }

        AudioBytesAdmitted?.Invoke(this, (header, audioBytes));
    }
}
