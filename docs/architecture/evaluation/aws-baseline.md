# Netra AWS + Evaluation Baseline

## Purpose

This document defines the stable architecture and invariants for Netra's AWS-backed
development environment and M2 retrieval evaluation.

This file is architectural guidance, not a temporary implementation checklist.

---

## Runtime Placement

Netra remains local during the current development/hackathon phase.

Local developer machine runs:

- Netra FastAPI/runtime
- local worker where required
- M2 retrieval pipeline
- LangGraph runtime where required
- RAGAS evaluation runner

Netra itself must not be deployed to AWS merely to run evaluation.

---

## AWS Responsibility

AWS provides the minimal durable infrastructure required by local Netra.

Provision AWS resources declaratively using Terraform.

Persistent AWS resources:

- minimal VPC/networking
- private RDS PostgreSQL
- S3 document/object storage
- small EC2 host running PgBouncer
- AWS Systems Manager support
- IAM roles/policies
- security groups
- lightweight CloudWatch visibility
- configurable budget alerts

Optional temporary resource:

- GPU EC2 for Prometheus-2 evaluation

Do not introduce additional AWS services unless an actual requirement justifies them.

Avoid by default:

- EKS
- Kubernetes
- Redis
- SQS
- Kafka
- Lambda
- API Gateway
- Load Balancer
- unnecessary application EC2 instances
- NAT Gateway unless technically required

---

## Terraform

Terraform is the primary infrastructure-management mechanism.

Do not require manual AWS Console creation of:

- VPC
- subnets
- security groups
- RDS
- S3
- PgBouncer EC2
- IAM resources
- GPU evaluator infrastructure

Terraform must not automatically apply billable infrastructure during implementation.

Normal implementation validation may run:

- terraform fmt
- terraform init
- terraform validate
- terraform plan when credentials permit

`terraform apply` requires explicit operator authorization.

If no remote Terraform backend exists, local Terraform state is acceptable initially.

Do not commit:

- terraform.tfstate
- terraform.tfstate.*
- .terraform/
- credentials

---

## Data Ownership

PostgreSQL is authoritative for structured Netra state.

S3 stores durable source/document bytes.

Pinecone is a derived, rebuildable semantic-search projection.

Neo4j is a derived, rebuildable learning/concept projection.

Prometheus-2 is an evaluation-only model.

RAGAS is an offline evaluation framework.

Redis must never become a correctness dependency unless architecture is explicitly changed later.

---

## Agents

Netra has exactly two runtime agents:

1. Coordinator
2. Tutor

Do not add an LLM reasoning loop because another engineer owns a subsystem.

Everything else should remain deterministic software, services, tools, workflows,
workers, adapters, evaluators, routers, or projections.

Prometheus-2 is never a Netra agent.

---

## Database Connectivity

RDS PostgreSQL must remain private.

Required:

- `publicly_accessible = false`
- PostgreSQL 5432 must not be exposed to `0.0.0.0/0`

Preferred local connection path:

developer laptop
→ AWS Systems Manager port forwarding
→ PgBouncer EC2
→ private RDS PostgreSQL

RDS security group should accept PostgreSQL only from the PgBouncer EC2 security group.

Do not expose PgBouncer publicly when SSM can provide access.

Use IMDSv2 and an EC2 instance role.

Alembic owns schema migrations.

Terraform owns infrastructure.

Do not run destructive or normal application migrations inside `terraform apply`.

---

## PgBouncer

PgBouncer provides PostgreSQL connection pooling.

Provision and configure it automatically using Terraform plus user-data/cloud-init
or another repository-standard bootstrap mechanism.

Do not invent production-scale pool values.

Validate whether LangGraph PostgreSQL checkpointing is compatible with the selected
pool mode before routing those connections through PgBouncer.

---

## S3

Use one private Netra document bucket.

Required:

- Block Public Access enabled
- bucket owner enforced
- server-side encryption
- versioning enabled
- no public ACL

Object layout should remain compatible with Netra's source/version model, conceptually:

documents/
  <account_id>/
    <source_id>/
      <source_version_id>/

PostgreSQL remains authoritative for source/version metadata.

---

## Pinecone

Pinecone remains the semantic retrieval projection.

Use metadata filtering where supported for defense in depth, including fields such as:

- account_id
- source_id
- source_version_id

Pinecone metadata is not the final authorization authority.

Returned candidate IDs must be validated against authoritative PostgreSQL/source state.

Cross-account retrieval is a hard security failure.

---

## Prometheus-2 Evaluator

Prometheus-2 is used only for offline evaluation.

Target model family:

`prometheus-eval/prometheus-7b-v2.0`

The evaluator runs on a temporary GPU EC2 instance when enabled.

It may use:

- vLLM for a compatible Hugging Face checkpoint
- llama.cpp for an appropriate GGUF checkpoint

Do not assume both runtimes are interchangeable.

GPU selection must consider:

- checkpoint size
- quantization
- VRAM
- runtime overhead
- AWS region availability
- EC2 quota
- cost

The GPU resource must be disabled by default in Terraform.

Prometheus must not be required for normal Netra operation.

---

## Prometheus Networking

Do not expose the evaluator port publicly.

Preferred path:

local RAGAS
→ localhost forwarded port
→ AWS SSM port forwarding
→ GPU EC2
→ Prometheus-2 OpenAI-compatible endpoint

"OpenAI-compatible" refers only to API protocol compatibility.

It does not mean OpenAI is used.

No proprietary judge fallback is permitted.

---

## RAGAS

RAGAS runs locally.

Do not deploy RAGAS to AWS.

Do not assume Prometheus can simply replace the default RAGAS LLM for every built-in metric.

Prometheus is a specialized evaluator and should be integrated through custom metrics
or compatible adapters where required.

Use deterministic code for deterministic metrics.

Use Prometheus only where semantic judgment is useful.

---

## M2 Retrieval Evaluation

Evaluate retrieval independently from answer generation.

Deterministic metrics should include:

- Precision@K
- Recall@K
- MRR
- nDCG@K where ground truth supports it
- correct_source_version_rate
- stale_source_version_count
- authorized_retrieval_rate
- unauthorized_context_count

Security invariant:

`unauthorized_context_count == 0`

A high aggregate score must never hide an authorization or source-version failure.

Prometheus may evaluate semantic properties such as:

- answer correctness
- answer relevance
- evidence support
- rubric compliance

Do not use Prometheus to calculate deterministic ranking metrics.

---

## Evaluation Dataset

Evaluation cases should support:

- case_id
- query
- authorization scope
- allowed source IDs
- pinned source version
- gold context/block IDs
- reference answer where needed
- expected evidence/citations where needed
- semantic rubric where needed

If no real benchmark dataset exists:

- define the schema
- add validation
- add only a small synthetic smoke fixture
- explicitly report that a human-curated gold dataset is still required

Do not fabricate benchmark truth.

---

## Security Invariants

Never:

- make RDS public
- expose PostgreSQL to `0.0.0.0/0`
- expose PgBouncer publicly without a justified requirement
- expose Prometheus publicly
- commit AWS credentials
- commit DB passwords
- commit Terraform state
- make Pinecone the authorization authority
- silently fall back to a proprietary judge
- deploy Netra to AWS merely for evaluation

---

## Cost Invariants

GPU infrastructure must be optional and disabled by default.

A normal Terraform apply must not accidentally create the GPU evaluator.

GPU evaluation lifecycle:

enable GPU
→ start evaluator
→ establish secure tunnel
→ run evaluation
→ save results locally
→ disable/remove GPU

Do not automatically destroy persistent infrastructure.

---

## Final Principle

The intended architecture is:

local Netra + local RAGAS
→ Terraform-managed AWS data infrastructure
→ private PostgreSQL through PgBouncer + SSM
→ S3/Pinecone remotely
→ optional temporary Prometheus-2 GPU for semantic evaluation

AWS supports local Netra.

AWS does not replace local Netra.