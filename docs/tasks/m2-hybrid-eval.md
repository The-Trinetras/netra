# M2 Hybrid AWS + RAGAS Evaluation Task

## Goal

Implement the AWS infrastructure and M2 evaluation path described in:

- `CLAUDE.md`
- `docs/architecture/evaluation-aws-baseline.md`
- existing Netra architecture documents

Do not redesign the architecture.

---

## Scope

Primary directories:

- `infrastructure/`
- `evaluation/`
- M2 retrieval/content code required for evaluation
- relevant configuration
- relevant tests

Avoid unrelated M1/M3/M4/M5 code unless a required shared contract forces a minimal change.

---

## Phase 1 — Repository Discovery

- [ ] Inspect Terraform/infrastructure currently present
- [ ] Inspect Python/runtime version
- [ ] Inspect resolved RAGAS version
- [ ] Identify actual M2 retrieval entry point
- [ ] Identify Pinecone adapter/config
- [ ] Identify S3 adapter/config
- [ ] Identify PostgreSQL configuration
- [ ] Identify source/version/evidence models
- [ ] Identify current embedding provider
- [ ] Identify existing evaluation package/tests
- [ ] Identify existing environment/config conventions

Do not guess versions or interfaces.

---

## Phase 2 — Terraform Base

Implement or complete:

- [ ] Terraform/provider version constraints
- [ ] provider configuration
- [ ] variables
- [ ] locals
- [ ] outputs
- [ ] `.gitignore` for local Terraform state

Use local state initially if no backend already exists.

Do not create a complex Terraform-state backend solely for this task.

---

## Phase 3 — Networking

Provision:

- [ ] minimal VPC
- [ ] required subnets
- [ ] route configuration
- [ ] RDS security group
- [ ] PgBouncer security group
- [ ] evaluator security group if GPU enabled

Requirements:

- [ ] RDS private
- [ ] no `0.0.0.0/0` PostgreSQL
- [ ] RDS 5432 allowed only from PgBouncer SG
- [ ] prefer SSM administration/tunneling
- [ ] avoid public PgBouncer
- [ ] avoid NAT Gateway unless required

---

## Phase 4 — S3

Provision:

- [ ] private document bucket
- [ ] Block Public Access
- [ ] bucket-owner-enforced ownership
- [ ] server-side encryption
- [ ] versioning
- [ ] useful non-secret Terraform outputs

Do not create public ACLs.

---

## Phase 5 — RDS

Provision PostgreSQL compatible with Netra runtime.

- [ ] private RDS
- [ ] encrypted storage
- [ ] backups
- [ ] explicit deletion/final snapshot policy
- [ ] Netra database
- [ ] safe credential handling
- [ ] dedicated application identity strategy

Do not make Netra permanently use the master DB account.

Do not run Alembic migrations inside Terraform apply.

---

## Phase 6 — PgBouncer EC2

Provision a small EC2 instance with:

- [ ] PgBouncer
- [ ] SSM Agent
- [ ] IAM instance profile
- [ ] IMDSv2
- [ ] reproducible bootstrap/user-data
- [ ] no unnecessary public inbound ports

Validate pool-mode compatibility with LangGraph checkpointing.

---

## Phase 7 — Monitoring + Cost

Implement only lightweight controls.

- [ ] basic CloudWatch visibility
- [ ] configurable AWS Budget
- [ ] budget email/amount as variables
- [ ] no invented hard-coded budget

---

## Phase 8 — Optional GPU Evaluator

Add Terraform flag equivalent to:

`enable_prometheus_gpu = false`

Requirements:

- [ ] GPU disabled by default
- [ ] evaluator instance type configurable
- [ ] no blind hard-coding of GPU family
- [ ] Prometheus model/runtime bootstrap reproducible
- [ ] use SSM
- [ ] no public inference port
- [ ] useful evaluator instance output
- [ ] clean disable/removal path

Target model:

`prometheus-eval/prometheus-7b-v2.0`

Choose vLLM or llama.cpp based on the actual checkpoint.

If GPU quota is unavailable, report it and continue all offline work.

---

## Phase 9 — Hybrid Health Check

Implement a health-check command/script.

Check:

- [ ] localhost Netra
- [ ] DB path through forwarded PgBouncer using `SELECT 1`
- [ ] S3 read-only access
- [ ] Pinecone read-only reachability
- [ ] Prometheus through forwarded endpoint when enabled
- [ ] embedding provider only if query-time retrieval requires it

Requirements:

- [ ] bounded timeouts
- [ ] secret redaction
- [ ] clear per-component status
- [ ] non-zero exit on required dependency failure
- [ ] JSON mode if practical

---

## Phase 10 — Evaluation Dataset

Implement/reuse dataset schema containing:

- [ ] case_id
- [ ] query
- [ ] account/auth scope
- [ ] allowed source IDs
- [ ] pinned source version
- [ ] gold context IDs
- [ ] reference answer where needed
- [ ] expected evidence/citations where needed
- [ ] semantic rubric where needed

If no real dataset exists:

- [ ] add schema/loader/validator
- [ ] add a small synthetic smoke fixture only
- [ ] report human-curated gold dataset as outstanding

---

## Phase 11 — Deterministic Metrics

Implement:

- [ ] Precision@K
- [ ] Recall@K
- [ ] MRR
- [ ] nDCG@K where appropriate
- [ ] correct_source_version_rate
- [ ] stale_source_version_count
- [ ] authorized_retrieval_rate
- [ ] unauthorized_context_count

Hard fail if:

`unauthorized_context_count > 0`

---

## Phase 12 — Retrieval Experiment Matrix

Support configurations for capabilities that actually exist:

- [ ] baseline
- [ ] metadata filtering
- [ ] hybrid retrieval
- [ ] hybrid + RRF
- [ ] hybrid + RRF + reranking
- [ ] final selected configuration

Do not implement fake stages merely to populate the matrix.

Record:

- Precision@K
- Recall@K
- MRR
- nDCG where applicable
- source-version correctness
- authorization correctness
- p50 latency
- p95 latency
- error rate

---

## Phase 13 — Prometheus-2 Judge

Implement a real evaluator client.

Requirements:

- [ ] async-capable
- [ ] configurable endpoint/model
- [ ] bounded timeout
- [ ] bounded output
- [ ] deterministic settings where supported
- [ ] strict absolute-grading prompt
- [ ] strict `[RESULT] N` parsing
- [ ] score must be 1..5
- [ ] feedback preserved
- [ ] normalized score `(N - 1) / 4`
- [ ] typed failures
- [ ] transient-only retries
- [ ] no proprietary fallback
- [ ] no secrets logged

Reject malformed/missing/conflicting scores.

---

## Phase 14 — RAGAS Integration

Inspect the installed RAGAS version first.

- [ ] do not blindly upgrade RAGAS
- [ ] implement against installed API
- [ ] keep deterministic metrics outside the judge
- [ ] use custom Prometheus-backed semantic metrics where required
- [ ] do not force Prometheus through incompatible structured-output prompts
- [ ] no fallback judge

Prometheus may evaluate:

- answer correctness
- answer relevance
- evidence support
- rubric compliance

---

## Phase 15 — Evaluation Runner

Implement executable evaluation runner.

It should:

- [ ] validate configuration
- [ ] load dataset
- [ ] optionally run health checks
- [ ] invoke M2 retrieval locally
- [ ] capture ranked evidence/context IDs
- [ ] capture latency
- [ ] calculate deterministic metrics
- [ ] call semantic judge only where required
- [ ] aggregate results
- [ ] write machine-readable outputs
- [ ] print concise summary
- [ ] exit non-zero on security/correctness hard failure

Use existing output conventions or:

`evaluation/results/<run-id>/`

Avoid committing large generated results.

---

## Phase 16 — Tests

Terraform:

- [ ] `terraform fmt -check`
- [ ] `terraform validate`

Python tests:

- [ ] dataset validation
- [ ] Precision@K
- [ ] Recall@K
- [ ] MRR
- [ ] nDCG if implemented
- [ ] source-version correctness
- [ ] unauthorized-context hard failure
- [ ] stale/unauthorized Pinecone candidate rejection
- [ ] Prometheus scores 1..5
- [ ] malformed result
- [ ] missing result
- [ ] conflicting result
- [ ] normalization
- [ ] timeout
- [ ] transient transport failure
- [ ] no proprietary fallback
- [ ] mocked RAGAS/Prometheus integration
- [ ] evaluation aggregation
- [ ] health-check secret redaction

Unit tests must not require live AWS/Pinecone/GPU or paid APIs.

---

## Phase 17 — Validation

Run what is possible:

- [ ] Terraform format check
- [ ] Terraform init
- [ ] Terraform validate
- [ ] Terraform plan if AWS credentials permit
- [ ] Python/unit tests
- [ ] live health checks only when infrastructure exists

Do not run `terraform apply` automatically.

---

## Required Final Report

Keep final response under 40 lines unless blocked.

Report only:

### IMPLEMENTED
Files/features completed.

### VALIDATION
Tests and Terraform checks actually run.

### BLOCKED
Only real blockers.

### COMMANDS
Exact commands for:
- Terraform init/plan/apply
- DB SSM tunnel
- local Netra startup
- health check
- GPU enable
- Prometheus SSM tunnel
- evaluation
- GPU disable

### READINESS

- READY FOR TERRAFORM PLAN: YES/NO
- READY FOR TERRAFORM APPLY: YES/NO
- READY FOR M2 EVALUATION: YES/NO