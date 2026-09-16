# Shared contracts

[contracts](contracts/) is authoritative for wire formats and agent handoffs.
The [current product scope](../docs/architecture/current-scope.md) cannot silently
rename fields, remove enum values or add messages. Examples illustrate the committed
schemas; the original Engineering Plan's examples are historical.

| Boundary | Authority | Owners / consumers |
|---|---|---|
| Client → API | [client_to_server](contracts/protocol/v1/client_to_server.schema.json) | M1/M5; M4 consumes submitted answers through services |
| API → client | [server_to_client](contracts/protocol/v1/server_to_client.schema.json), [error](contracts/protocol/v1/error.schema.json) | M1/M5; M4 reviews public question privacy |
| Synthesized binary audio | [audio_frame_header](contracts/protocol/v1/audio_frame_header.schema.json) | M1/M5; server-to-client only |
| Coordinator → Tutor | [coordinator_to_tutor](contracts/agent/v1/coordinator_to_tutor.schema.json) | M1/M4; M2/M3 provide authorized evidence |
| Tutor → Coordinator | [tutor_to_coordinator](contracts/agent/v1/tutor_to_coordinator.schema.json) | M4/M1; M5 consumes public output |
| Jobs | [job schema placeholder](contracts/jobs/v1/job.schema.json) is empty | M2/M1 must decide whether an external schema is needed |

The current handoff still requires `assessment_summaries`, whose items contain legacy
status labels; result events still include `review_requested`. These are compatibility
facts, not active requirements for automatic labels or scheduled reviews. Do not
invent a status for “Studied — understanding not tested” or fabricate summaries to
satisfy the old shape. A versioned migration for factual history needs owner review.

Mirror locations currently include [Python serialization](../api/src/netra_api/transport/websocket/serializer.py),
[Python handoffs](../api/src/netra_api/coordinator/handoff.py) and
[C# protocol](../client/src/Netra.Desktop/Protocol/). The neutral mirror-package
location remains undecided. Keep strict validation and coordinate both consumers
before schema/version changes. See [message flow](../docs/architecture/message-flow.md)
for retry, cancellation and reconnect invariants.
