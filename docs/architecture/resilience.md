# M2 resilience guarantees

This document records only failure behavior covered by the deterministic
resilience tests. It does not claim exactly-once execution.

- PostgreSQL remains authoritative for canonical sources, versions, blocks,
  chunks, lifecycle state, jobs, and outbox records.
- Pinecone is a rebuildable projection. Projection or provider failure leaves
  canonical PostgreSQL records intact and cannot mark an incomplete version
  ready.
- Jobs are at-least-once. `operation_key` is the idempotency boundary, and
  handlers are safe to replay after a crash or expired lease.
- Job claims use short PostgreSQL transactions and lease tokens plus worker
  identity. Expired claims can be reclaimed; a stale holder cannot complete
  or heartbeat a reclaimed job.
- Outbox events are claimed with leases and acknowledged only after the
  durable job effect is established. Duplicate delivery is safe through the
  operation key; event IDs remain available for consumer idempotency.
- Parser, embedding, and projection failures do not mark their stages
  complete. Readiness and activation remain gated by completed stages.
- Retrieval treats lexical and semantic provider failures according to the
  explicit unavailable-provider contract. Unexpected exceptions propagate;
  resolved evidence is still validated against canonical PostgreSQL state,
  including authorization and source-version freshness.

Remaining risks include live provider outage behavior, database failover,
uncertain external effects occurring immediately before worker death, and
multi-process timing beyond the deterministic repository tests. These require
environment-backed integration testing and are not claimed here.
