# Deployment documentation

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
