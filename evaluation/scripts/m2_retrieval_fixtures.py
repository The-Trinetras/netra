"""M2 retrieval fixtures for M4/M3: immutable IDs, relevance labels, negative evidence.

Synthetic, public content only (the Ohm's-law chapter used across teams). The
active version ID matches M4's ``tutor_reference_v1`` source excerpts so cases
can be joined by source version. Every identifier is a deterministic UUID5 of a
readable name, so regenerating the file never changes an ID.

Each query lists graded relevance labels (2 = directly answers, 1 = supporting)
over chunk IDs, and ``expected_resolution`` states what M2's canonical resolver
must return for each probe (``resolved`` or a rejection reason) under the
stated scope. Negative probes cover: a missing ID, another account's chunk
(denied), a chunk of a deleted source, a superseded version's chunk (stale
under active-only scope), a failed (non-ready) version's chunk, and a vector
with an incompatible embedding specification.

Regenerate:  python evaluation/scripts/m2_retrieval_fixtures.py --write
Validate:    pytest evaluation/scripts/test_m2_retrieval_fixtures.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "evaluation" / "cases" / "m2_retrieval_fixtures_v1.json"
NAMESPACE = uuid5(NAMESPACE_URL, "netra:m2-retrieval-fixtures:v1")

ACCOUNT = uuid5(NAMESPACE, "account:student")
OTHER_ACCOUNT = uuid5(NAMESPACE, "account:other-student")
SOURCE = uuid5(NAMESPACE, "source:ohm-chapter-4")
ACTIVE_VERSION = UUID("6f9c1b52-0d34-4a7e-9d21-7c4f5a2b8e10")  # M4 tutor_reference_v1
SUPERSEDED_VERSION = uuid5(NAMESPACE, "version:ohm-chapter-4:v1")
FAILED_VERSION = uuid5(NAMESPACE, "version:ohm-chapter-4:v3-failed")
DELETED_SOURCE_VERSION = uuid5(NAMESPACE, "version:deleted-notes:v1")
OTHER_ACCOUNT_VERSION = uuid5(NAMESPACE, "version:other-student-notes:v1")

EMBEDDING_SPEC = "gemini-embedding-001:1536"
INCOMPATIBLE_SPEC = "gemini-embedding-001:768"


def chunk_id(name: str) -> str:
    return str(uuid5(NAMESPACE, f"chunk:{name}"))


CHUNKS = [
    ("table-4-1", ACTIVE_VERSION, "chapter 4, table 4.1",
     "Table 4.1, measured values for a single resistor. Current 1 A gives 2 V, 2 A gives 4 V and 3 A gives 6 V."),
    ("figure-4-2", ACTIVE_VERSION, "chapter 4, figure 4.2",
     "Figure 4.2 plots voltage in volts against current in amperes; the points lie on a straight line through "
     "the origin rising 2 volts per ampere."),
    ("ohms-law-statement", ACTIVE_VERSION, "chapter 4, section 4.1, paragraph 1",
     "Ohm's law states that the voltage across a resistor equals the current through it multiplied by its "
     "resistance, V = I R."),
    ("resistance-units", ACTIVE_VERSION, "chapter 4, section 4.1, paragraph 2",
     "Resistance is measured in ohms; one ohm is one volt per ampere."),
    ("power-intro", ACTIVE_VERSION, "chapter 4, section 4.3, paragraph 1",
     "Electrical power is the rate at which energy is transferred, measured in watts."),
    ("v1-ohms-law-statement", SUPERSEDED_VERSION, "chapter 4, section 4.1, paragraph 1",
     "Ohm's law: voltage is proportional to current for an ohmic conductor."),
    ("v3-failed-statement", FAILED_VERSION, "chapter 4, section 4.1, paragraph 1",
     "Ohm's law text from an ingestion run that failed validation."),
    ("deleted-note", DELETED_SOURCE_VERSION, "notes, paragraph 1",
     "A deleted note that also mentions Ohm's law."),
    ("other-student-note", OTHER_ACCOUNT_VERSION, "notes, paragraph 1",
     "Another student's private note about V = I R."),
]

VERSIONS = [
    {"source_version_id": str(ACTIVE_VERSION), "source_id": str(SOURCE), "account_id": str(ACCOUNT),
     "version_number": 2, "status": "ready", "is_active": True},
    {"source_version_id": str(SUPERSEDED_VERSION), "source_id": str(SOURCE), "account_id": str(ACCOUNT),
     "version_number": 1, "status": "ready", "is_active": False},
    {"source_version_id": str(FAILED_VERSION), "source_id": str(SOURCE), "account_id": str(ACCOUNT),
     "version_number": 3, "status": "failed", "is_active": False},
    {"source_version_id": str(DELETED_SOURCE_VERSION), "source_id": str(uuid5(NAMESPACE, "source:deleted-notes")),
     "account_id": str(ACCOUNT), "version_number": 1, "status": "ready", "is_active": True,
     "deleted": True},
    {"source_version_id": str(OTHER_ACCOUNT_VERSION), "source_id": str(uuid5(NAMESPACE, "source:other-notes")),
     "account_id": str(OTHER_ACCOUNT), "version_number": 1, "status": "ready", "is_active": True},
]


def _queries() -> list[dict]:
    active_scope = None  # unscoped: active versions only
    return [
        {
            "query_id": "ohm-q1-proportionality",
            "query": "What does the graph of voltage against current show for the resistor?",
            "scope_source_version_ids": active_scope,
            "relevance": {chunk_id("figure-4-2"): 2, chunk_id("table-4-1"): 1, chunk_id("ohms-law-statement"): 1},
            "expected_resolution": {
                chunk_id("figure-4-2"): "resolved",
                chunk_id("v1-ohms-law-statement"): "source_version_mismatch",  # stale: superseded, not active
                chunk_id("v3-failed-statement"): "source_version_mismatch",  # never citable
                chunk_id("other-student-note"): "unauthorized",
                chunk_id("deleted-note"): "not_found",
                str(uuid5(NAMESPACE, "chunk:never-existed")): "not_found",
            },
        },
        {
            "query_id": "ohm-q2-units",
            "query": "What unit is resistance measured in?",
            "scope_source_version_ids": [str(ACTIVE_VERSION)],
            "relevance": {chunk_id("resistance-units"): 2, chunk_id("ohms-law-statement"): 1},
            "expected_resolution": {
                chunk_id("resistance-units"): "resolved",
                chunk_id("power-intro"): "resolved",  # authorized but irrelevant: relevance 0
            },
        },
        {
            "query_id": "ohm-q3-pinned-old-version",
            "query": "State Ohm's law.",
            "scope_source_version_ids": [str(SUPERSEDED_VERSION)],
            "relevance": {chunk_id("v1-ohms-law-statement"): 2},
            "expected_resolution": {
                chunk_id("v1-ohms-law-statement"): "resolved",  # pinned session keeps its version
                chunk_id("ohms-law-statement"): "source_version_mismatch",  # newer version is out of scope
            },
        },
        {
            "query_id": "ohm-q4-no-evidence",
            "query": "How does a transistor amplify a signal?",
            "scope_source_version_ids": active_scope,
            "relevance": {},
            "expected_resolution": {},
            "note": "No chunk answers this; an honest system reports missing evidence.",
        },
    ]


def build() -> dict:
    return {
        "dataset_id": "m2-retrieval-fixtures",
        "dataset_version": "1",
        "permission": "synthetic",
        "owner": "M2",
        "account_id": str(ACCOUNT),
        "embedding_spec": EMBEDDING_SPEC,
        "versions": VERSIONS,
        "chunks": [
            {"chunk_id": chunk_id(name), "name": name, "source_version_id": str(version), "locator": locator,
             "text": text, "embedding_spec": EMBEDDING_SPEC}
            for name, version, locator, text in CHUNKS
        ],
        "incompatible_vectors": [
            {"vector_id": chunk_id("ohms-law-statement"), "embedding_spec": INCOMPATIBLE_SPEC,
             "expected": "dropped_before_resolution"},
        ],
        "queries": _queries(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="write the fixture file")
    args = parser.parse_args()
    text = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.write:
        OUTPUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUTPUT}")
    else:
        print(text)


if __name__ == "__main__":
    main()
