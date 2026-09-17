# Netra evaluation foundation

Evaluation is file-based and retrieval-first. The checked-in E3 retrieval and
P3 answer datasets are versioned real golden fixtures; synthetic fixtures
belong only in unit tests. Cases are JSONL
records loaded deterministically from `evaluation/cases/`, versioned by
`dataset_id` and `dataset_version`, and should later be locked by a SHA-256
manifest under `evaluation/locked/`.

Each real case must use actual, version-pinned PostgreSQL-resolved evidence:
question, reference answer, reference evidence IDs, canonical contexts when
available, source/version identity, and tags. Coverage should include factual,
section-specific, multi-hop/multi-chunk, lexical/semantic, metadata-filtered,
stale-version, authorization-boundary, table, equation, and difficult cases.

The current runner records Precision@5, Recall@5, Recall@10, MRR, nDCG@5,
source-version and authorization correctness, canonical contexts, status,
error rate, and p50/p95 latency. It exits nonzero for incomplete execution,
stale evidence, or any unauthorized final context. Prometheus-2 can optionally
grade evidence support through its strict localhost HTTP client; no proprietary
fallback exists.

`evaluation/scripts/retrieval_experiments.py` provides the reproducible E0-E5
experiment matrix. `evaluation/manifests/retrieval_v1.json` records every
stage flag and frozen retrieval value explicitly. The harness composes the
existing lexical, semantic, RRF, BGE and PostgreSQL evidence boundaries; it
does not fork production retrieval algorithms.

E0 is lexical only, E1 semantic only, E2 hybrid without RRF, E3 hybrid with
RRF, E4 hybrid+RRF+BGE, and E5 metadata-filtered hybrid+RRF+BGE. E4/E5 are
reported as blocked when a real BGE reranker is not supplied; the harness
never substitutes the passthrough reranker while labeling the result BGE.
Each executed case records retrieved IDs, Recall@5, Recall@10, MRR, stage
latencies and total latency. Summary JSONL artifacts can be compared with
`comparison_rows()`.

Results belong in `evaluation/results/<experiment_id>.jsonl`; writes refuse to
overwrite unless explicitly requested. No production benchmark results are
fabricated here.

With the PostgreSQL/PgBouncer tunnel and provider configuration active, run a
direct M2 experiment without Coordinator or Tutor:

```powershell
uv run --locked python evaluation/scripts/run_m2_evaluation.py --experiment E3
```

Run the complete hybrid dependency health check with a known readable object:

```powershell
uv run --locked python evaluation/scripts/hybrid_health.py --s3-key "documents/<account>/<source>/<version>/<object>"
```

After opening the optional Prometheus SSM tunnel on local port 18000, add:

```powershell
uv run --locked python evaluation/scripts/run_m2_evaluation.py --experiment E4 --prometheus-endpoint http://127.0.0.1:18000
```

RAGAS is not yet declared because its current compatible release has mandatory
OpenAI SDK dependencies that conflict with Netra's zero-proprietary-evaluator
dependency rule. Deterministic metrics and the custom Prometheus client remain
usable independently.
