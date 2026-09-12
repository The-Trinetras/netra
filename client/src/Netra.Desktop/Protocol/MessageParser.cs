using System.Text.Json;
using Netra.Desktop.Protocol.Dto;

namespace Netra.Desktop.Protocol;

// Parses incoming frames against
// shared/contracts/protocol/v1/server_to_client.schema.json. Unsupported
// protocol versions are rejected per CLAUDE.md protocol rules ("Reject
// unsupported versions and unknown required structures").
public static class MessageParser
{
    public static ServerToClientEnvelope ParseEnvelope(string json)
    {
        ServerToClientEnvelope envelope;
        try
        {
            envelope = JsonSerializer.Deserialize<ServerToClientEnvelope>(json, NetraJsonSerialization.Options)
                ?? throw new ProtocolException("Server message deserialized to null.");
        }
        catch (JsonException ex)
        {
            throw new ProtocolException("Server message is not a valid protocol envelope.", ex);
        }

        if (envelope.ProtocolVersion != NetraProtocol.Version)
        {
            throw new ProtocolException($"Unsupported protocol_version '{envelope.ProtocolVersion}'.");
        }

        return envelope;
    }

    // Typed accessor for the one server payload shape currently confirmed by
    // shared/contracts/examples/server/response_segment.json. See
    // Protocol/Dto/ServerPayloads.cs for why session.snapshot and
    // quiz.question do not have typed accessors yet.
    public static ResponseSegmentPayload ParseResponseSegment(ServerToClientEnvelope envelope)
    {
        EnsureType(envelope, ServerMessageType.ResponseSegment);
        return envelope.Payload.Deserialize<ResponseSegmentPayload>(NetraJsonSerialization.Options)
            ?? throw new ProtocolException("response.segment payload could not be parsed.");
    }

    private static void EnsureType(ServerToClientEnvelope envelope, ServerMessageType expected)
    {
        if (envelope.Type != expected)
        {
            throw new ProtocolException($"Expected message type '{expected}' but received '{envelope.Type}'.");
        }
    }
}
