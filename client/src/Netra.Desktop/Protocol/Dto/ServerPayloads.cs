namespace Netra.Desktop.Protocol.Dto;

// response.segment shape is taken from
// shared/contracts/examples/server/response_segment.json (the
// server_to_client schema does not yet define per-type $defs the way
// client_to_server does).
public sealed record ResponseSegmentPayload
{
    public required string GenerationId { get; init; }
    public required string SegmentId { get; init; }
    public required string SentenceId { get; init; }
    public string? Kind { get; init; }
    public required string Text { get; init; }
    public bool Final { get; init; }
    public IReadOnlyList<string>? EvidenceIds { get; init; }
}

// TODO(shared/contracts): session.snapshot, quiz.question and error payloads
// are not yet defined as sub-schemas of
// shared/contracts/protocol/v1/server_to_client.schema.json, and no example
// files exist for them under shared/contracts/examples/server/. Per
// CLAUDE.md ("do not invent missing product requirements") and this task's
// scope (read-only access to shared/contracts/), no shape is guessed here.
// Callers read ServerToClientEnvelope.Payload directly as JsonElement for
// these types until a contract version adds the sub-schema; add typed
// records here (ServerPayloads.cs) once it does.
