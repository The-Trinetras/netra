using System.Text.Json;

namespace Netra.Desktop.Protocol;

public static class NetraJsonSerialization
{
    // shared/contracts/protocol/v1/*.schema.json use snake_case field names
    // (protocol_version, message_id, ...). This policy makes the PascalCase
    // C# properties on the Dto records round-trip to those exact names
    // without per-property [JsonPropertyName] attributes.
    public static JsonSerializerOptions Options { get; } = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DictionaryKeyPolicy = JsonNamingPolicy.SnakeCaseLower,
    };
}
