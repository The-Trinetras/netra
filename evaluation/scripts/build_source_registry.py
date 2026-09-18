"""Build evaluation/sources/source_registry_v1.json from the project's source fixtures.

The registry is the single place the evaluation dataset's evidence text is
checked against (grounding.py). It is GENERATED, never hand-edited, so no
excerpt can be retyped with a silent change:

- intro-circuits-ch4: evaluation/cases/ohms_law_source.json (M4) and the
  chapter-4 chunks and versions in evaluation/cases/m2_retrieval_fixtures_v1.json (M2).
- deleted-note, other-student-note-m2: the M2 fixture's deleted and
  other-account sources.
- ohm-study-pack-m3: api/tests/multimedia/fixtures/ohm_law.py (M3). The chart,
  table and equation are structured there; this script renders them to text
  deterministically, and labels each variant with M3's own validators
  (verified / source_mismatch / unreadable) rather than by hand.
- ohm-study-pack-m1-pdf, ohm-lecture-m1, other-student-m1: the evidence table in
  api/tests/transport/ohm_fixture.py (M1). ev-fig02 is excluded there: its text
  is a placeholder ("Figure 2 description"), not source content.
- Dialogue quotes from docs/architecture/Netra-SPEC.md section 4 (the AgentSpec
  walkthrough), for conversation turns that quote it.
- New synthetic miniatures: evaluation/sources/synthetic_miniatures_v1.json,
  copied in unchanged with origin new_synthetic_miniature.

All of these are synthetic fixtures. None is original media, and the M1 and M3
renderings of the "Ohm's Law Study Pack" disagree on source version ids and page
locators, so they are registered as separate sources and never mixed.

Usage (from the repository root):
    python evaluation/scripts/build_source_registry.py            # write
    python evaluation/scripts/build_source_registry.py --check    # compare only
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO / "evaluation" / "sources" / "source_registry_v1.json"
MINIATURES_PATH = REPO / "evaluation" / "sources" / "synthetic_miniatures_v1.json"
M4_SOURCE = REPO / "evaluation" / "cases" / "ohms_law_source.json"
M2_FIXTURES = REPO / "evaluation" / "cases" / "m2_retrieval_fixtures_v1.json"
M3_FIXTURE = REPO / "api" / "tests" / "multimedia" / "fixtures" / "ohm_law.py"
M1_FIXTURE = REPO / "api" / "tests" / "transport" / "ohm_fixture.py"
SPEC = REPO / "docs" / "architecture" / "Netra-SPEC.md"


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _load_module(name: str, path: Path):
    for entry in (REPO / "api" / "src", REPO / "worker" / "src"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Registered before execution: dataclasses resolve annotations through it.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _evidence(evidence_id, version_id, locator, text, *, trust=None, kind="passage", variant=None,
              start_ms=None, end_ms=None, defined_in: str) -> dict[str, Any]:
    return {"evidence_id": evidence_id, "source_version_id": str(version_id), "locator": locator, "text": text,
            "trust": trust, "kind": kind, "variant": variant, "start_ms": start_ms, "end_ms": end_ms,
            "defined_in": defined_in}


def _source(key, title, *, kind="document", origin="project_fixture", source_id=None, owner=None,
            versions, evidence, defined_in, notes=(), locator_scheme=None, media=None, excluded=()):
    return {"source_key": key, "title": title, "kind": kind, "origin": origin, "permission": "synthetic",
            "source_id": source_id, "owner_account_id": owner, "versions": versions,
            "locator_scheme": locator_scheme, "media": media, "evidence": evidence,
            "defined_in": sorted(defined_in), "notes": list(notes), "excluded": list(excluded)}


# --- M2 + M4: Introductory Circuits chapter 4 -------------------------------


def _m2_m4_sources() -> list[dict[str, Any]]:
    m4 = json.loads(M4_SOURCE.read_text(encoding="utf-8"))
    m2 = json.loads(M2_FIXTURES.read_text(encoding="utf-8"))
    versions_by_source: dict[str, list[dict]] = {}
    source_of_version: dict[str, dict] = {}
    for version in m2["versions"]:
        record = {"source_version_id": version["source_version_id"], "version_number": version["version_number"],
                  "status": version["status"], "is_active": version["is_active"],
                  "deleted": bool(version.get("deleted", False))}
        versions_by_source.setdefault(version["source_id"], []).append(record)
        source_of_version[version["source_version_id"]] = version

    chunks_by_source: dict[str, list[dict]] = {}
    for chunk in m2["chunks"]:
        version = source_of_version[chunk["source_version_id"]]
        chunks_by_source.setdefault(version["source_id"], []).append(_evidence(
            chunk["chunk_id"], chunk["source_version_id"], chunk["locator"], chunk["text"],
            kind="chunk", defined_in=f"{_rel(M2_FIXTURES)}#chunks/{chunk['name']}",
        ))

    ch4_version = m4["source_version_id"]
    ch4_source_id = source_of_version[ch4_version]["source_id"]
    ch4_evidence = chunks_by_source[ch4_source_id] + [
        _evidence(item["evidence_id"], ch4_version, item["locator"], item["text"], trust=item["trust"],
                  kind="passage", defined_in=f"{_rel(M4_SOURCE)}#{item['evidence_id']}")
        for item in m4["evidence"]
    ]
    sources = [_source(
        "intro-circuits-ch4", "Introductory Circuits, chapter 4 'Resistance' (synthetic fixture)",
        source_id=ch4_source_id, owner=source_of_version[ch4_version]["account_id"],
        versions=sorted(versions_by_source[ch4_source_id], key=lambda v: v["version_number"]),
        evidence=ch4_evidence, defined_in={_rel(M4_SOURCE), _rel(M2_FIXTURES)},
        locator_scheme="chapter/section/paragraph (M2 chunks) and chapter/page, table, figure, equation (M4)",
        notes=[
            "M4 evidence ids (ev-ohm-*) and M2 chunk ids render some of the same objects with different "
            "wording (e.g. table 4.1) and different locator schemes for the same version; cases use one "
            "rendering per object and never treat the two as independent confirmation.",
            "ev-ohm-graph is trust 'derived' (a description of the figure), not a verified reading of it.",
        ],
    )]

    other_keys = {True: "deleted-note", False: "other-student-note-m2"}
    for source_id, versions in versions_by_source.items():
        if source_id == ch4_source_id:
            continue
        deleted = any(v["deleted"] for v in versions)
        owner = source_of_version[versions[0]["source_version_id"]]["account_id"]
        key = other_keys[deleted]
        title = ("A deleted note (synthetic fixture)" if deleted
                 else "Another student's private note (synthetic fixture, other account)")
        sources.append(_source(
            key, title, source_id=source_id, owner=owner, versions=versions,
            evidence=chunks_by_source.get(source_id, []), defined_in={_rel(M2_FIXTURES)},
            notes=["Must never reach an answer: deleted source." if deleted
                   else "Belongs to another account: must never reach this student's answer."],
        ))
    return sources


# --- M3: Ohm's Law Study Pack, structured media -----------------------------


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "?"
    return str(int(value)) if float(value).is_integer() else str(value)


def render_chart(chart) -> str:
    def axis(a) -> str:
        name = a.orientation.value
        if a.label is None or a.unit is None:
            return f"{name} axis: label and unit unreadable in the source"
        return f"{name} axis: {a.label} in {a.unit}, {a.scale.value} scale from {_fmt(a.minimum)} to {_fmt(a.maximum)}"

    axes = sorted(chart.axes, key=lambda a: a.orientation.value)
    units = {a.orientation.value: a.unit for a in chart.axes}
    parts = [f"Figure fig02, a {chart.kind.value} chart titled '{chart.title}'"] + [axis(a) for a in axes]
    for series in chart.series:
        if any(p.x is None or p.y is None for p in series.points):
            parts.append(f"series '{series.label}': plotted values unreadable in the source")
        else:
            points = ", ".join(f"({_fmt(p.x)} {units.get('x') or ''}, {_fmt(p.y)} {units.get('y') or ''})".replace(" ,", ",").replace(" )", ")")
                               for p in series.points)
            parts.append(f"series '{series.label}' plots the points {points}")
    return ". ".join(parts) + "."


def render_table(table) -> str:
    headers = sorted(table.headers, key=lambda h: h.index)
    head = ", ".join(f"{h.text} ({h.unit})" for h in headers)
    unit_by_column = {h.index: h.unit for h in headers}
    rows: dict[int, dict[int, str]] = {}
    for cell in table.cells:
        rows.setdefault(cell.row_index, {})[cell.column_index] = f"{cell.text} {unit_by_column.get(cell.column_index) or ''}".strip()
    body = "; ".join(", ".join(row[c] for c in sorted(row)) for _, row in sorted(rows.items()))
    return (f"Table tbl01, caption '{table.caption}'. Columns: {head}. "
            f"Measurement rows: {body}.")


def render_equation(tree, canonical: str) -> str:
    product = next(child for child in tree.root.children if child.children)
    units = ", ".join(f"{node.symbol} in {node.unit}" for node in [tree.root.children[0], *product.children])
    return (f"Equation eq01, extracted form {canonical}, spoken as '{tree.root.spoken_form}'; "
            f"the right-hand side is spoken as '{product.spoken_form}'. Units: {units}.")


def _trust(report) -> str:
    if report.unreadable:
        return "unreadable"
    return "source_verified" if report.is_source_verified else "source_mismatch"


def _m3_source() -> dict[str, Any]:
    fx = _load_module("m3_ohm_law_fixture", M3_FIXTURE)
    from netra_api.multimedia.equations.validation import canonical_form, validate_equation_against_source
    from netra_api.multimedia.figures.validation import validate_chart_against_source
    from netra_api.multimedia.tables.validation import validate_table_against_source

    where = _rel(M3_FIXTURE)
    version = str(fx.SOURCE_VERSION_ID)
    evidence = []
    for variant, kwargs in [(None, {}), ("unreadable_axes", {"unreadable_axes": True}),
                            ("y_unit_mV", {"y_unit": "mV"}), ("swap_axes", {"swap_axes": True})]:
        chart = fx.extracted_chart(**kwargs)
        check = fx.chart_source_check(unreadable_axes=kwargs.get("unreadable_axes", False))
        evidence.append(_evidence(chart.reference.evidence_id, version, chart.reference.locator, render_chart(chart),
                                  trust=_trust(validate_chart_against_source(chart, check)), kind="chart",
                                  variant=variant, defined_in=f"{where}#extracted_chart"))
    for variant, kwargs in [(None, {}), ("voltage_unit_mV", {"voltage_unit": "mV"}), ("drop_row_2", {"drop_row": 2})]:
        table = fx.extracted_table(**kwargs)
        evidence.append(_evidence(table.reference.evidence_id, version, table.reference.locator, render_table(table),
                                  trust=_trust(validate_table_against_source(table, fx.table_source_check())),
                                  kind="table", variant=variant, defined_in=f"{where}#extracted_table"))
    for variant, kwargs in [(None, {}), ("operator_divide", {"operator": "÷", "spoken_operator": "divided by"})]:
        tree = fx.extracted_equation(**kwargs)
        evidence.append(_evidence(tree.reference.evidence_id, version, tree.reference.locator,
                                  render_equation(tree, canonical_form(tree.root)),
                                  trust=_trust(validate_equation_against_source(tree, fx.equation_source_check())),
                                  kind="equation", variant=variant, defined_in=f"{where}#extracted_equation"))
    for item, kind in [(fx.transcript_evidence(), "transcript_segment"), (fx.visual_evidence(), "visual_description")]:
        ref = item.reference
        evidence.append(_evidence(ref.evidence_id, version, ref.locator, item.description, trust="derived", kind=kind,
                                  start_ms=ref.start_ms, end_ms=ref.end_ms,
                                  defined_in=f"{where}#{'transcript_evidence' if kind.startswith('transcript') else 'visual_evidence'}"))
    return _source(
        "ohm-study-pack-m3", "Ohm's Law Study Pack (ohm-v1) with lecture-v1 (AgentSpec synthetic fixture, M3 rendering)",
        source_id=str(fx.SOURCE_ID), versions=[{"source_version_id": version, "version_number": 1, "status": "ready",
                                                "is_active": True, "deleted": False}],
        evidence=evidence, defined_in={where}, kind="document_with_video",
        locator_scheme="p<page>/<object> for PDF objects; lecture-v1 with millisecond ranges for video evidence",
        media={"video_locator": fx.VIDEO_LOCATOR, "video_id": str(fx.VIDEO_ID), "duration_ms": fx.LECTURE_DURATION_MS},
        notes=[
            "Chart, table and equation texts are deterministic renderings of M3's structured fixture objects "
            "(build_source_registry.render_*), not quotations from a real PDF.",
            "Variant trust comes from M3's validators against the fixture's reviewer-read source checks.",
            "Transcript and visual evidence are fixture stand-ins for Twelve Labs output (trust 'derived').",
            "Page locators differ from M1's rendering of the same pack (M1: tbl01 page 3, fig02 page 2).",
        ],
    )


# --- M1: Ohm's Law Study Pack, transport fixture ----------------------------


def _m1_sources() -> list[dict[str, Any]]:
    fx = _load_module("m1_ohm_transport_fixture", M1_FIXTURE)
    where = _rel(M1_FIXTURE)
    by_version: dict[str, list[dict]] = {}
    excluded = []
    for evidence_id, (_account, item) in sorted(fx.EVIDENCE.items()):
        if evidence_id == "ev-fig02":
            excluded.append({"evidence_id": evidence_id, "reason": f"placeholder text {item.text!r}, not source content"})
            continue
        trust = item.trust.value if hasattr(item.trust, "value") else str(item.trust)
        by_version.setdefault(str(item.source_version_id), []).append(_evidence(
            evidence_id, item.source_version_id, item.locator, item.text, trust=trust,
            kind="passage", defined_in=f"{where}#EVIDENCE/{evidence_id}"))

    def version(v) -> list[dict]:
        return [{"source_version_id": str(v), "version_number": 1, "status": "ready", "is_active": True, "deleted": False}]

    return [
        _source("ohm-study-pack-m1-pdf", "Ohm's Law Study Pack (AgentSpec synthetic fixture, M1 transport rendering)",
                owner=str(fx.ACCOUNT), versions=version(fx.OHM_V1), evidence=by_version[str(fx.OHM_V1)],
                defined_in={where}, locator_scheme="page <n>, <object>", excluded=excluded,
                notes=["ev-injected deliberately contains instructions addressed to an AI; it is data.",
                       "Page locators differ from M3's rendering of the same pack (M3: p4/tbl01, p4/fig02)."]),
        _source("ohm-lecture-m1", "lecture-v1 (AgentSpec synthetic fixture, M1 transport rendering)",
                owner=str(fx.ACCOUNT), versions=version(fx.LECTURE_V1), evidence=by_version[str(fx.LECTURE_V1)],
                defined_in={where}, kind="video", locator_scheme="lecture-v1"),
        _source("other-student-m1", "Another student's private notes (synthetic fixture, other account)",
                owner=str(fx.OTHER_ACCOUNT), versions=version(fx.OTHER_SOURCE),
                evidence=by_version[str(fx.OTHER_SOURCE)], defined_in={where},
                notes=["Belongs to another account: must never reach this student's answer."]),
    ]


# --- SPEC walkthrough quotes ------------------------------------------------


def spec_quotes() -> dict[str, Any]:
    text = SPEC.read_text(encoding="utf-8")
    section = text.split("## 4. A complete walkthrough", 1)[1].split("\n## 5.", 1)[0]
    quotes = sorted(set(re.findall(r"“([^”]+)”", section)))
    return {"key": "agentspec-walkthrough", "path": _rel(SPEC), "section": "4. A complete walkthrough",
            "quotes": quotes,
            "note": "Hand-worked acceptance dialogue (synthetic); quoted student and tutor lines only."}


def build() -> dict[str, Any]:
    miniatures = json.loads(MINIATURES_PATH.read_text(encoding="utf-8"))
    project = _m2_m4_sources() + [_m3_source()] + _m1_sources()
    for source in miniatures["sources"]:
        for item in source["evidence"]:
            item["defined_in"] = f"{_rel(MINIATURES_PATH)}#{source['source_key']}"
        source["defined_in"] = [_rel(MINIATURES_PATH)]
        source.setdefault("excluded", [])
    sources = sorted(project + miniatures["sources"], key=lambda s: s["source_key"])
    for source in sources:
        source["evidence"] = sorted(source["evidence"], key=lambda e: (e["source_version_id"], e["evidence_id"], e["variant"] or ""))
    return {
        "registry": "netra-eval-sources-v1",
        "generated_by": "evaluation/scripts/build_source_registry.py",
        "note": ("GENERATED. Every source here is synthetic: project fixtures that already existed in the "
                 "repository (origin project_fixture) or miniatures self-authored for evaluation (origin "
                 "new_synthetic_miniature). None is original media or a real course document."),
        "sources": sources,
        "dialogue_sources": [spec_quotes()],
    }


def render(registry: dict[str, Any]) -> str:
    return json.dumps(registry, indent=2, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the committed registry is stale")
    args = parser.parse_args(argv)
    text = render(build())
    if args.check:
        current = REGISTRY_PATH.read_text(encoding="utf-8") if REGISTRY_PATH.exists() else ""
        if current != text:
            print("source_registry_v1.json is stale; rerun build_source_registry.py", file=sys.stderr)
            return 1
        print("source registry is up to date")
        return 0
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(text, encoding="utf-8")
    registry = json.loads(text)
    print(f"wrote {_rel(REGISTRY_PATH)}: {len(registry['sources'])} sources, "
          f"{sum(len(s['evidence']) for s in registry['sources'])} evidence items")
    return 0


if __name__ == "__main__":
    sys.exit(main())
