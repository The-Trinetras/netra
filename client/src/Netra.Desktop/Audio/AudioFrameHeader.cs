using System.Text.Json;
using System.Text.Json.Serialization;

namespace Netra.Desktop.Audio;

// Mirrors shared/contracts/protocol/v1/audio_frame_header.schema.json.
// Applies ONLY to server -> client synthesized/playback audio. Microphone
// (client -> server) upload is governed by its own approved protocol and
// must never be parsed with this type.
public sealed record AudioFrameHeader
{
    public required int Version { get; init; }
    public required string GenerationId { get; init; }
    public required string SegmentId { get; init; }
    public required long Sequence { get; init; }
    public required bool EndOfSegment { get; init; }
    public required bool EndOfGeneration { get; init; }
    public required string MediaType { get; init; }
}

// System.Text.Json ignores unknown members by default, which the schema's
// "reject unknown header fields" rule forbids. This converter parses into a
// JsonDocument first specifically so it can reject any property name this
// record does not declare, then delegates to the normal typed
// deserialization for value/type checking.
public sealed class StrictAudioFrameHeaderConverter : JsonConverter<AudioFrameHeader>
{
    private static readonly HashSet<string> KnownProperties = new(StringComparer.Ordinal)
    {
        "version", "generation_id", "segment_id", "sequence",
        "end_of_segment", "end_of_generation", "media_type",
    };

    public override AudioFrameHeader Read(
        ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        using var document = JsonDocument.ParseValue(ref reader);
        foreach (var property in document.RootElement.EnumerateObject())
        {
            if (!KnownProperties.Contains(property.Name))
            {
                throw new JsonException($"Unknown audio frame header field '{property.Name}'.");
            }
        }

        var strictOptions = new JsonSerializerOptions(options);
        // Remove this converter before delegating, or we would recurse
        // into Read() forever on the same token stream.
        for (var i = strictOptions.Converters.Count - 1; i >= 0; i--)
        {
            if (strictOptions.Converters[i] is StrictAudioFrameHeaderConverter)
            {
                strictOptions.Converters.RemoveAt(i);
            }
        }

        return document.RootElement.Deserialize<AudioFrameHeader>(strictOptions)
            ?? throw new JsonException("Audio frame header deserialized to null.");
    }

    public override void Write(
        Utf8JsonWriter writer, AudioFrameHeader value, JsonSerializerOptions options)
    {
        // Server -> client only: the client never encodes these frames.
        throw new NotSupportedException("AudioFrameHeader is never serialized by the client.");
    }
}
