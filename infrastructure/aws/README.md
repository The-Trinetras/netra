# Deployment documentation

## Evaluation compute decision — 17 September 2026

The user's AWS Free Plan cannot launch `g5.xlarge`; remove AWS GPU provisioning
from the required plan rather than treating it as only a quota request. No paid
upgrade, A100 rental or additional evaluator service is selected. Use deterministic
and human evaluation with optional hosted Gemini scoring outside the live path;
Prometheus-2 is deferred under the
[model/evaluation plan](../../docs/architecture/model-evaluation-plan.md).
An A100 40 GB is only a possible later, separately authorized comparison host.
Keep GPU dependencies outside API/worker images. This changes no EC2/Compose or
database placement decision below and does not establish that all AWS use is free.

## Application deployment

The [runtime baseline](../../docs/architecture/runtime-baseline.md) retains Docker
Compose on one EC2 instance for API and worker, with the same Python baseline.
PostgreSQL remains authoritative; private S3 owns stored source bytes; Pinecone and
Neo4j are derived. No Kubernetes or additional deployable services are introduced.

The [AgentSpec](../../docs/architecture/Netra-SPEC.md) describes RDS and PgBouncer
as its target database connection setup. The historical plan describes a Compose
PostgreSQL container; the current [Compose file](../compose/docker-compose.yml)
is empty. M1/M2 must reconcile that deployment placement and connection-pooling
decision before implementation. This documentation does not choose or configure it.

Dependency locks, verified image digests, deployment wiring, authorized provider
checks and backup/restore exercises remain pending. This directory is not a runbook
for a working deployment. Do not deploy or access credentials as part of the
documentation migration. See the [migration report](../../docs/audits/documentation-migration-report.md).
