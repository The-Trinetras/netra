"""What a producer (Netra) may see, and how its outputs come back.

Comparison validity depends on this boundary:

- producer_inputs() exports ONLY what Netra would legitimately have for a
  case: the student's input, earlier turns, session context and the
  authorized evidence excerpts. It never exports the reference, rationale,
  alternatives, assertions, calculations, withheld evidence, category,
  expected behaviour, failure modes, provenance, limitations or candidate
  fixtures. assert_no_leak() checks the export against every reference and
  withheld item before it is written.
- import_outputs() freezes producer outputs into a run. The run's producer
  decides whether the text is labelled Netra output or fixture text; fixture
  text can never be imported into a Netra run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from eval_dataset import DatasetSnapshot, JudgeCase
from eval_results import canonical_json, sha256_hex
from eval_store import RunStore
from grounding import Registry

PRODUCER_FIELDS = ("case_id", "student_input", "conversation", "session_context", "evidence")


class ReferenceLeakError(Exception):
    """A producer input contains a reference answer or withheld evidence."""


def producer_input(case: JudgeCase) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "student_input": case.student_input,
        "conversation": [{"role": turn.role, "content": turn.content} for turn in case.conversation],
        "session_context": case.session_context.model_dump(mode="json") if case.session_context else None,
        "evidence": [
            {"evidence_id": item.evidence_id, "source_version_id": item.source_version_id, "locator": item.locator,
             "text": item.text, "trust": item.trust, "start_ms": item.start_ms, "end_ms": item.end_ms}
            for item in case.source_excerpts
        ],
    }


def assert_no_leak(cases: Iterable[JudgeCase], rows: list[dict[str, Any]], registry: Optional[Registry] = None) -> None:
    """Checked per case against that case's own row.

    A withheld item leaks if it is supplied as evidence or its registered
    text appears in the row. (Its id alone may legitimately appear inside
    supplied text: an injected page can name another student's evidence id.)
    """

    by_id = {row.get("case_id"): row for row in rows}
    for row in rows:
        if set(row) != set(PRODUCER_FIELDS):
            raise ReferenceLeakError(f"{row.get('case_id')}: unexpected producer fields {sorted(set(row) - set(PRODUCER_FIELDS))}")
    for case in cases:
        row = by_id.get(case.case_id)
        if row is None:
            continue
        blob = canonical_json(row)
        if case.reference is not None and case.reference.text in blob:
            raise ReferenceLeakError(f"{case.case_id}: the reference text appears in the producer inputs")
        for alternative in case.reference.acceptable_alternatives if case.reference else []:
            if len(alternative) > 30 and alternative in blob:
                raise ReferenceLeakError(f"{case.case_id}: an acceptable alternative appears in the producer inputs")
        supplied = {item["evidence_id"] for item in row["evidence"]}
        for held in case.withheld_evidence:
            if held.evidence_id in supplied:
                raise ReferenceLeakError(f"{case.case_id}: withheld evidence {held.evidence_id} is supplied")
            found = registry.evidence(held.evidence_id, held.source_version_id) if registry else None
            if found is not None and found[1]["text"] in blob:
                raise ReferenceLeakError(f"{case.case_id}: the text of withheld evidence {held.evidence_id} appears")


def producer_inputs(snapshot: DatasetSnapshot, split: str, registry: Optional[Registry] = None) -> list[dict[str, Any]]:
    cases = [case for case in snapshot.by_split(split) if case.student_input is not None]  # type: ignore[arg-type]
    rows = [producer_input(case) for case in cases]
    assert_no_leak(cases, rows, registry or Registry.load())
    return rows


def write_producer_inputs(snapshot: DatasetSnapshot, split: str, path: Path) -> dict[str, Any]:
    rows = producer_inputs(snapshot, split)
    text = "".join(canonical_json(row) + "\n" for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"dataset": snapshot.snapshot_id, "split": split, "cases": len(rows), "path": str(path),
            "sha256": sha256_hex(text)}


def import_outputs(store: RunStore, path: Path) -> dict[str, Any]:
    """Freeze outputs from a JSONL file: {case_id, repetition?, response,
    cited_evidence_ids?, structured?, trace_id?}. Idempotent per unit."""

    imported, unchanged = 0, 0
    before = store.outputs()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        unknown = set(row) - {"case_id", "repetition", "response", "cited_evidence_ids", "structured", "trace_id"}
        if unknown:
            raise ValueError(f"line {number}: unexpected fields {sorted(unknown)}")
        key = (row["case_id"], int(row.get("repetition", 0)))
        store.append_output(key[0], key[1], row["response"], trace_id=row.get("trace_id"),
                            cited_evidence_ids=row.get("cited_evidence_ids"), structured=row.get("structured"))
        if key in before:
            unchanged += 1
        else:
            imported += 1
    return {"imported": imported, "already_frozen": unchanged, "outputs": len(store.outputs())}
