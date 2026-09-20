"""Run the repository-owned Ragas-style metrics over a results or golden file.

The Ragas package stays uninstalled (docs/architecture/runtime-baseline.md,
"Evaluation dependencies"): these are the deterministic lexical metrics in
ragas_style. Report them as Ragas-style proxies, never as an executed Ragas run
and never alongside Prometheus ordinal scores as if they were the same scale.

Which answer was scored is printed with the numbers, because it changes what
they mean:

- ``generated_answer`` present -> the producer's output was scored. That is a
  candidate evaluation.
- only ``reference_answer`` present -> the dataset's own reference was scored
  against its retrieved contexts. That is a grounding check ON THE DATASET: it
  says whether the reference is supported by the context the case supplies, and
  it says nothing about Netra's answers.

Every average is printed with the number of cases it was computed on. A metric
without the inputs it needs is not evaluated; it is never 0.0.

    python evaluation/scripts/run_ragas_style.py [path.jsonl] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ragas_style import answer_relevancy, context_precision, context_recall, faithfulness

DEFAULT_DATASET = Path("evaluation/cases/netra_p3_answer_golden_v1.jsonl")


def score(case: dict[str, Any]) -> dict[str, Any]:
    generated = case.get("generated_answer")
    answer = generated or case.get("reference_answer")
    retrieved = tuple(case.get("retrieved_chunk_ids") or ())
    relevant = set(case.get("reference_evidence_ids") or ()) or None
    contexts = tuple(case.get("retrieved_contexts") or ())
    metrics = [
        context_precision(retrieved, relevant),
        context_recall(retrieved, relevant),
        faithfulness(answer, contexts),
        answer_relevancy(case.get("question"), answer),
    ]
    return {
        "case_id": case.get("case_id") or case.get("example_id") or "?",
        "scored_answer": "generated" if generated else ("reference" if answer else "none"),
        "metrics": {metric.name: metric for metric in metrics},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_ragas_style")
    parser.add_argument("dataset", nargs="?", default=str(DEFAULT_DATASET))
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    path = Path(args.dataset)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    scored = [score(case) for case in cases]
    names = ["context_precision", "context_recall", "faithfulness", "answer_relevancy"]

    if args.json:
        print(json.dumps([
            {"case_id": row["case_id"], "scored_answer": row["scored_answer"],
             **{name: row["metrics"][name].value for name in names}} for row in scored], indent=2))
        return 0

    print(f"{path}  ({len(scored)} cases)")
    print(f"{'case':<34} {'answer':<10} " + " ".join(f"{name:>18}" for name in names))
    for row in scored:
        cells = []
        for name in names:
            metric = row["metrics"][name]
            cells.append(f"{metric.value:>18.3f}" if metric.evaluated else f"{'- ' + (metric.reason or ''):>18}")
        print(f"{row['case_id'][:33]:<34} {row['scored_answer']:<10} " + " ".join(cells))

    print("\nmean (over the cases each metric could be computed on):")
    for name in names:
        values = [row["metrics"][name].value for row in scored if row["metrics"][name].evaluated]
        if values:
            print(f"  {name:<20} {sum(values) / len(values):.3f}   n={len(values)}/{len(scored)}")
        else:
            reasons = sorted({row["metrics"][name].reason or "unknown" for row in scored})
            print(f"  {name:<20} not evaluated   n=0/{len(scored)}   ({', '.join(reasons)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
