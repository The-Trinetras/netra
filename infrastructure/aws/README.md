# Deployment documentation

## Evaluation compute decision — 17 September 2026

The user's AWS Free Plan cannot launch `g5.xlarge`; remove AWS GPU provisioning
from the required plan rather than treating it as only a quota request. The selected
primary model evaluator is Prometheus-2 7B on Modal using one A100 40 GB, under the
[model/evaluation plan](../../docs/architecture/model-evaluation-plan.md).
Modal credit/cost caps, authentication and scale-to-zero apply; Lightning AI and
Kaggle are alternatives. This is external offline evaluation compute only, not an
AWS upgrade or student-path service. Keep GPU dependencies outside API/worker
images. No deployment is performed by this documentation change. Application
EC2/Compose and database-placement decisions below remain unchanged.

## Application deployment

The [18 September AX plan](../../docs/architecture/arize-ax-integration.md) selects
Arize AX as a managed engineering destination for sanitized background tracing,
datasets and experiments. AX replaces historical LangSmith tracing; retain
operational logs. Keep credentials server/evaluation-side, export bounded and
nonblocking, and outages independent of application readiness. No collector,
Phoenix server or high-volume infrastructure is required. Alyx is an engineering
assistant; Prometheus remains on Modal. No AX configuration/deployment is performed
here, and compatible telemetry dependency pins remain pending review.

The [runtime baseline](../../docs/architecture/runtime-baseline.md) retains Docker
Compose on one EC2 instance for API and worker, with the same Python baseline.
PostgreSQL remains authoritative; private S3 owns stored source bytes; Pinecone and
Neo4j are derived. No Kubernetes or additional production services are introduced;
the external evaluator and AX engineering integration are the approved boundaries above.

The [AgentSpec](../../docs/architecture/Netra-SPEC.md) describes RDS and PgBouncer
as its target database connection setup. The historical plan describes a Compose
PostgreSQL container; the current [Compose file](../compose/docker-compose.yml)
is empty. M1/M2 must reconcile that deployment placement and connection-pooling
decision before implementation. This documentation does not choose or configure it.

Dependency locks, verified image digests, deployment wiring, authorized provider
checks and backup/restore exercises remain pending. This directory is not a runbook
for a working deployment. Do not deploy or access credentials as part of the
documentation migration. See the [migration report](../../docs/audits/documentation-migration-report.md).
