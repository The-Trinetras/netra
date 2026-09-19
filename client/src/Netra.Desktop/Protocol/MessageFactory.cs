using System.Text.Json;
using Netra.Desktop.Protocol.Dto;

namespace Netra.Desktop.Protocol;

// Builds outgoing envelopes matching
// shared/contracts/protocol/v1/client_to_server.schema.json. message_id is
// generated here; session_id/request_id/sequence are supplied by the caller
// (see Networking/ConnectionManager.cs, which owns request-id and sequence
// tracking per CLAUDE.md "Client must track request/generation identifiers").
public static class MessageFactory
{
    public static ClientToServerEnvelope CreateSessionResume(
        Guid sessionId, Guid requestId, long sequence, SessionResumePayload payload) =>
        Create(sessionId, requestId, sequence, ClientMessageType.SessionResume, payload);

    public static ClientToServerEnvelope CreateTurnSubmit(
        Guid sessionId, Guid requestId, long sequence, TurnSubmitPayload payload) =>
        Create(sessionId, requestId, sequence, ClientMessageType.TurnSubmit, payload);

    public static ClientToServerEnvelope CreateNavigationCommand(
        Guid sessionId, Guid requestId, long sequence, NavigationCommandPayload payload) =>
        Create(sessionId, requestId, sequence, ClientMessageType.NavigationCommand, payload);

    public static ClientToServerEnvelope CreateResponseCancel(
        Guid sessionId, Guid requestId, long sequence, ResponseCancelPayload payload) =>
        Create(sessionId, requestId, sequence, ClientMessageType.ResponseCancel, payload);

    public static ClientToServerEnvelope CreatePlaybackAck(
        Guid sessionId, Guid requestId, long sequence, PlaybackAckPayload payload) =>
        Create(sessionId, requestId, sequence, ClientMessageType.PlaybackAck, payload);

    public static ClientToServerEnvelope CreateAsrStart(
        Guid sessionId, Guid requestId, long sequence, AsrStartPayload payload) =>
        Create(sessionId, requestId, sequence, ClientMessageType.AsrStart, payload);

    public static string Serialize(ClientToServerEnvelope envelope) =>
        JsonSerializer.Serialize(envelope, NetraJsonSerialization.Options);

    private static ClientToServerEnvelope Create<TPayload>(
        Guid sessionId, Guid requestId, long sequence, ClientMessageType type, TPayload payload)
    {
        return new ClientToServerEnvelope
        {
            MessageId = Guid.NewGuid(),
            SessionId = sessionId,
            RequestId = requestId,
            Sequence = sequence,
            Type = type,
            Payload = JsonSerializer.SerializeToElement(payload, NetraJsonSerialization.Options),
        };
    }
}
