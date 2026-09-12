using System.Text;
using System.Text.Json;
using Netra.Desktop.Protocol;

namespace Netra.Desktop.Audio;

// Thrown for any structurally invalid binary audio frame. The caller's
// response is the same for every case below — reject the frame outright,
// never guess a correction or fall back to a different framing.
public sealed class AudioFrameException : Exception
{
    public AudioFrameException(string message) : base(message)
    {
    }

    public AudioFrameException(string message, Exception innerException) : base(message, innerException)
    {
    }
}

// Parses the binary framing defined by
// shared/contracts/protocol/v1/audio_frame_header.schema.json:
//   [4-byte unsigned big-endian header length]
//   [UTF-8 JSON header]
//   [raw audio bytes]
// Applies ONLY to server -> client synthesized/playback audio. Microphone
// upload uses its own approved protocol and must never be parsed here.
public static class BinaryAudioFrame
{
    public const int MaxHeaderBytes = 16 * 1024;
    private const int LengthPrefixSize = 4;
    private const int SupportedVersion = 1;

    // Validates the declared header length against the frame's actual
    // available bytes BEFORE attempting to parse any JSON. Throws
    // AudioFrameException for every structural problem: a frame shorter
    // than the length prefix, a zero/invalid length, a length exceeding
    // either the 16 KiB cap or the bytes actually present, malformed JSON,
    // an unknown field, a wrong field type, or an unsupported version.
    public static (AudioFrameHeader Header, ReadOnlyMemory<byte> AudioBytes) Parse(ReadOnlyMemory<byte> frame)
    {
        if (frame.Length < LengthPrefixSize)
        {
            throw new AudioFrameException("Frame is shorter than the 4-byte length prefix.");
        }

        var span = frame.Span;
        var declaredHeaderLength =
            (span[0] << 24) | (span[1] << 16) | (span[2] << 8) | span[3];

        if (declaredHeaderLength <= 0)
        {
            throw new AudioFrameException("Declared header length is zero or negative.");
        }

        if (declaredHeaderLength > MaxHeaderBytes)
        {
            throw new AudioFrameException(
                $"Declared header length {declaredHeaderLength} exceeds the {MaxHeaderBytes}-byte limit.");
        }

        var availableAfterPrefix = frame.Length - LengthPrefixSize;
        if (declaredHeaderLength > availableAfterPrefix)
        {
            throw new AudioFrameException(
                $"Declared header length {declaredHeaderLength} exceeds the " +
                $"{availableAfterPrefix} bytes actually available in the frame.");
        }

        var headerStart = LengthPrefixSize;
        var headerBytes = frame.Slice(headerStart, declaredHeaderLength);

        AudioFrameHeader header;
        try
        {
            var options = new JsonSerializerOptions(NetraJsonSerialization.Options);
            options.Converters.Add(new StrictAudioFrameHeaderConverter());
            header = JsonSerializer.Deserialize<AudioFrameHeader>(headerBytes.Span, options)
                ?? throw new AudioFrameException("Audio frame header deserialized to null.");
        }
        catch (JsonException ex)
        {
            throw new AudioFrameException("Invalid audio frame header.", ex);
        }

        if (header.Version != SupportedVersion)
        {
            throw new AudioFrameException($"Unsupported audio frame version {header.Version}.");
        }

        var audioBytes = frame.Slice(headerStart + declaredHeaderLength);
        return (header, audioBytes);
    }
}

// Per-generation strictly-increasing sequence enforcement. Duplicate or
// decreasing sequence numbers within one generation_id are rejected; gaps
// are permitted, since transport/recovery must not assume every frame
// arrives. One tracker is scoped to one generation_id — a new generation
// gets a fresh tracker rather than resetting shared state, so an old
// generation's sequence history can never affect admission of a new one.
public sealed class GenerationSequenceTracker
{
    private readonly string _generationId;
    private long? _highestSequence;

    public GenerationSequenceTracker(string generationId)
    {
        _generationId = generationId;
    }

    public bool Admit(AudioFrameHeader header)
    {
        if (!string.Equals(header.GenerationId, _generationId, StringComparison.Ordinal))
        {
            return false;
        }

        if (_highestSequence is { } highest && header.Sequence <= highest)
        {
            return false;
        }

        _highestSequence = header.Sequence;
        return true;
    }
}
