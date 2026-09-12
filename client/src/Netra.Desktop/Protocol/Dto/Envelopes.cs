using System.Text.Json;

namespace Netra.Desktop.Protocol.Dto;

public static class NetraProtocol
{
    // shared/contracts/protocol/v1/*.schema.json both pin protocol_version to
    // the const "1.0". Bump only alongside a new contract version.
    public const string Version = "1.0";
}

// Mirrors shared/contracts/protocol/v1/client_to_server.schema.json.
// Payload is kept as a raw JsonElement at the envelope level; MessageFactory
// produces it from a typed payload record, and MessageParser is not needed
// on this side since the client only ever sends these, never parses them.
public sealed record ClientToServerEnvelope
{
    public string ProtocolVersion { get; init; } = NetraProtocol.Version;
    public required Guid MessageId { get; init; }
    public required Guid SessionId { get; init; }
    public required Guid RequestId { get; init; }
    public required long Sequence { get; init; }
    public required ClientMessageType Type { get; init; }
    public required JsonElement Payload { get; init; }
}

// Mirrors shared/contracts/protocol/v1/server_to_client.schema.json.
// Payload stays a raw JsonElement at the envelope level for every type;
// MessageParser exposes a typed accessor per message type
// (ParseResponseSegment, ParseSessionSnapshot, ParseQuizQuestion, ParseError).
public sealed record ServerToClientEnvelope
{
    public string ProtocolVersion { get; init; } = NetraProtocol.Version;
    public Guid MessageId { get; init; }
    public Guid SessionId { get; init; }
    public Guid RequestId { get; init; }
    public long Sequence { get; init; }
    public ServerMessageType Type { get; init; }
    public JsonElement Payload { get; init; }
}
