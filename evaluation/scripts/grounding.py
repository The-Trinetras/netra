"""Deterministic grounding checks: evidence, withheld items, quotes, calculations.

Every check here is exact and needs no model. It answers "is this case
honestly built?", not "is a response good?":

- Each supplied excerpt must exist in evaluation/sources/source_registry_v1.json
  with the same evidence id, source version, variant, locator, text, trust and
  time range. Nothing is paraphrased or retyped.
- Supplied evidence must be usable for this student: from a ready version that
  is active or explicitly pinned, never deleted, never another account's.
- Withheld evidence must really be what the case says (denied, deleted, stale,
  failed, unpinned or absent), judged from the registry's own records.
- Conversation turns marked as quotes must match the AgentSpec walkthrough
  verbatim; quoted spans in a reference must appear in the supplied evidence or
  the conversation.
- Calculations are recomputed with exact fractions and unit algebra; each input
  must be stated in the evidence it cites (or in the student's own words for a
  hypothetical), and the reference must state the result when it claims to.
- The reference must itself pass the case's text assertions (a reference that
  states a forbidden value, or omits a required one, is a broken case).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from eval_dataset import (
    Calculation,
    JudgeCase,
    MustCiteAny,
    MustNotCite,
    MustNotContain,
    MustNotStateQuantity,
    StatesQuantity,
)

REPO = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO / "evaluation" / "sources" / "source_registry_v1.json"
QUOTE_NOTE = "quote:agentspec-walkthrough"

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Issue:
    case_id: str
    code: str
    severity: Severity
    detail: str


# --- registry ---------------------------------------------------------------


class Registry:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.sources = {s["source_key"]: s for s in data["sources"]}
        self._evidence: dict[tuple[str, str, Optional[str]], tuple[str, dict]] = {}
        self._versions: dict[str, tuple[dict, dict]] = {}
        for source in data["sources"]:
            for version in source["versions"]:
                self._versions[version["source_version_id"]] = (source, version)
            for item in source["evidence"]:
                key = (item["evidence_id"], item["source_version_id"], item["variant"])
                if key in self._evidence:
                    raise ValueError(f"duplicate registry evidence {key}")
                self._evidence[key] = (source["source_key"], item)
        self.quotes = {quote for part in data.get("dialogue_sources", []) for quote in part["quotes"]}

    @classmethod
    def load(cls, path: Path = REGISTRY_PATH) -> "Registry":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def evidence(self, evidence_id: str, version_id: str, variant: Optional[str] = None) -> Optional[tuple[str, dict]]:
        return self._evidence.get((evidence_id, version_id, variant))

    def any_evidence(self, evidence_id: str) -> list[tuple[str, dict]]:
        return [value for key, value in self._evidence.items() if key[0] == evidence_id]

    def version(self, version_id: str) -> Optional[tuple[dict, dict]]:
        return self._versions.get(version_id)


# --- quantities --------------------------------------------------------------

UNIT_ALIASES: dict[str, tuple[str, ...]] = {
    "V": ("V", "volt", "volts"),
    "mV": ("mV", "millivolt", "millivolts"),
    "A": ("A", "amp", "amps", "ampere", "amperes"),
    "ohm": ("Ω", "ohm", "ohms"),
    "W": ("W", "watt", "watts"),
    "J": ("J", "joule", "joules"),
    "V/A": ("V/A", "volts per ampere", "volt per ampere", "V per A"),
}


def _number_forms(value: float) -> list[str]:
    fraction = Fraction(str(value))
    forms = []
    if fraction.denominator == 1:
        forms += [str(fraction.numerator), f"{fraction.numerator}.0"]
    else:
        text = f"{float(fraction):g}"
        forms += [text]
        if text.startswith("0."):
            forms.append(text[1:])
    return forms


def quantity_pattern(value: float, unit: str) -> re.Pattern[str]:
    aliases = UNIT_ALIASES.get(unit, (unit,))
    number = "|".join(re.escape(form) for form in _number_forms(value))
    unit_part = "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
    # Not preceded by a digit or decimal point (so 8 V does not match 18 V),
    # and the unit is a whole token (so 2 A does not match 2 Apples).
    return re.compile(rf"(?<![\d.])(?:{number})\s?(?:{unit_part})(?![A-Za-z])")


def mentions_quantity(text: str, value: float, unit: str) -> bool:
    return bool(quantity_pattern(value, unit).search(text))


class UnitError(ValueError):
    pass


_PRODUCTS = {frozenset({"V", "A"}): "W", frozenset({"A", "ohm"}): "V"}
_QUOTIENTS = {("V", "A"): "ohm", ("V", "ohm"): "A", ("W", "V"): "A", ("W", "A"): "V"}


def compute(calc: Calculation) -> tuple[Fraction, str]:
    values = [Fraction(str(q.value)) for q in calc.operands]
    units = [q.unit for q in calc.operands]
    if calc.operation == "add":
        if len(set(units)) != 1:
            raise UnitError(f"cannot add {units}")
        return sum(values, Fraction(0)), units[0]
    if calc.operation == "parallel":
        if set(units) != {"ohm"} or any(v == 0 for v in values):
            raise UnitError("parallel combination needs non-zero resistances in ohm")
        return 1 / sum((1 / v for v in values), Fraction(0)), "ohm"
    if len(values) != 2:
        raise UnitError(f"{calc.operation} takes exactly two operands")
    a, b = values
    if calc.operation == "multiply":
        unit = _PRODUCTS.get(frozenset(units)) if units[0] != units[1] else None
        if unit is None:
            raise UnitError(f"no unit rule for {units[0]} × {units[1]}")
        return a * b, unit
    unit = _QUOTIENTS.get((units[0], units[1]))
    if unit is None or b == 0:
        raise UnitError(f"no unit rule for {units[0]} / {units[1]}")
    return a / b, unit


# --- case checks ------------------------------------------------------------


def _student_owner(case: JudgeCase, registry: Registry) -> Optional[str]:
    owners = set()
    for version_id in (case.session_context.pinned_source_version_ids if case.session_context else []):
        found = registry.version(version_id)
        if found and found[0].get("owner_account_id"):
            owners.add(found[0]["owner_account_id"])
    return owners.pop() if len(owners) == 1 else None


def _dialogue_text(case: JudgeCase) -> str:
    return "\n".join([case.student_input or ""] + [turn.content for turn in case.conversation])


def check_case(case: JudgeCase, registry: Registry) -> list[Issue]:
    issues: list[Issue] = []

    def add(code: str, detail: str, severity: Severity = "error") -> None:
        issues.append(Issue(case.case_id, code, severity, detail))

    pinned = set(case.session_context.pinned_source_version_ids) if case.session_context else set()
    for version_id in pinned:
        if registry.version(version_id) is None:
            add("pinned_version_unknown", f"pinned version {version_id} is not in the registry")
    owner = _student_owner(case, registry)

    supplied = {item.evidence_id for item in case.source_excerpts}
    supplied_text = "\n".join(item.text for item in case.source_excerpts)
    for item in case.source_excerpts:
        found = registry.evidence(item.evidence_id, item.source_version_id, item.variant)
        label = f"{item.evidence_id}@{item.source_version_id[:8]}" + (f"[{item.variant}]" if item.variant else "")
        if found is None:
            add("excerpt_not_in_registry", f"{label} is not a registered source item")
            continue
        source_key, record = found
        if item.source_key is not None and item.source_key != source_key:
            add("excerpt_source_mismatch", f"{label} belongs to {source_key}, case says {item.source_key}")
        if item.locator != record["locator"]:
            add("excerpt_locator_mismatch", f"{label} locator {item.locator!r} != registry {record['locator']!r}")
        if item.text != record["text"]:
            add("excerpt_text_mismatch", f"{label} text differs from the registered source text")
        if item.trust is not None and item.trust != record["trust"]:
            add("excerpt_trust_mismatch", f"{label} trust {item.trust} != registry {record['trust']}")
        if (item.start_ms, item.end_ms) != (record["start_ms"], record["end_ms"]):
            add("excerpt_time_mismatch", f"{label} time range differs from the registry")
        source, version = registry.version(item.source_version_id)  # type: ignore[misc]
        if version["status"] != "ready":
            add("supplied_unusable_version", f"{label} comes from a {version['status']} version")
        if version.get("deleted"):
            add("supplied_deleted_source", f"{label} comes from a deleted source")
        if not version["is_active"] and item.source_version_id not in pinned:
            add("supplied_stale_version", f"{label} comes from an inactive version that the session does not pin")
        if owner and source.get("owner_account_id") and source["owner_account_id"] != owner:
            add("supplied_other_account", f"{label} belongs to another account")

    for held in case.withheld_evidence:
        label = f"withheld {held.evidence_id}"
        if held.reason == "not_found":
            if registry.any_evidence(held.evidence_id):
                add("withheld_reason_wrong", f"{label} is marked not_found but exists in the registry")
            continue
        found = registry.evidence(held.evidence_id, held.source_version_id)
        if found is None:
            add("withheld_not_in_registry", f"{label} is not a registered source item")
            continue
        source, version = registry.version(held.source_version_id)  # type: ignore[misc]
        if source["source_key"] != held.source_key:
            add("withheld_source_mismatch", f"{label} belongs to {source['source_key']}")
        reasons = {
            "denied_other_account": bool(owner and source.get("owner_account_id") and source["owner_account_id"] != owner),
            "deleted_source": bool(version.get("deleted")),
            "stale_inactive_version": version["status"] == "ready" and not version["is_active"] and held.source_version_id not in pinned,
            "failed_version": version["status"] == "failed",
            "version_not_pinned": bool(pinned) and held.source_version_id not in pinned,
        }
        if not reasons.get(held.reason, False):
            add("withheld_reason_wrong", f"{label}: registry facts do not support reason {held.reason}")
        if held.evidence_id in supplied:
            add("withheld_also_supplied", f"{label} is also supplied to the producer")

    known = supplied | {held.evidence_id for held in case.withheld_evidence}
    for assertion in case.assertions:
        if isinstance(assertion, MustCiteAny) and not set(assertion.evidence_ids) <= supplied:
            add("assertion_cites_unsupplied", f"must_cite_any names evidence not supplied: {sorted(set(assertion.evidence_ids) - supplied)}")
        if isinstance(assertion, MustNotCite) and not set(assertion.evidence_ids) <= known:
            add("assertion_unknown_evidence", f"must_not_cite names unknown evidence: {sorted(set(assertion.evidence_ids) - known)}")

    for turn in case.conversation:
        if turn.note == QUOTE_NOTE and turn.content not in registry.quotes:
            add("quote_not_in_spec", f"turn {turn.content!r} is marked as a walkthrough quote but is not one")
    if case.reference is not None:
        allowed = supplied_text + "\n" + _dialogue_text(case)
        for quoted in re.findall(r"[“\"]([^”\"]{12,})[”\"]", case.reference.text):
            if quoted not in allowed and quoted not in registry.quotes:
                add("reference_quote_unsupported",
                    f"reference quotes {quoted!r}, which is not in the evidence, the dialogue or the walkthrough")

    dialogue = _dialogue_text(case)
    excerpt_text = {item.evidence_id: item.text for item in case.source_excerpts}
    results: dict[str, tuple[Fraction, str]] = {}
    for calc in case.calculations:
        try:
            value, unit = compute(calc)
        except UnitError as error:
            add("calculation_unit_error", f"{calc.calc_id}: {error}")
            continue
        results[calc.calc_id] = (value, unit)
        if value != Fraction(str(calc.expected.value)) or unit != calc.expected.unit:
            add("calculation_wrong", f"{calc.calc_id}: computes {float(value):g} {unit}, case expects {calc.expected.value:g} {calc.expected.unit}")
        for operand in calc.operands:
            if operand.evidence_id is not None and operand.evidence_id.startswith("calc:"):
                earlier = results.get(operand.evidence_id[5:])
                if earlier is None:
                    add("calculation_input_unknown_step", f"{calc.calc_id}: {operand.evidence_id} is not an earlier step")
                elif earlier != (Fraction(str(operand.value)), operand.unit):
                    add("calculation_step_mismatch", f"{calc.calc_id}: {operand.evidence_id} is {float(earlier[0]):g} {earlier[1]}, not {operand.value:g} {operand.unit}")
                continue
            if operand.evidence_id is None:
                if not mentions_quantity(dialogue, operand.value, operand.unit):
                    add("calculation_input_unstated", f"{calc.calc_id}: {operand.value:g} {operand.unit} is in no evidence and not in the student's words")
            elif operand.evidence_id not in excerpt_text:
                add("calculation_input_unsupplied", f"{calc.calc_id}: cites {operand.evidence_id}, which is not supplied")
            elif not mentions_quantity(excerpt_text[operand.evidence_id], operand.value, operand.unit):
                add("calculation_input_not_in_evidence", f"{calc.calc_id}: {operand.value:g} {operand.unit} is not stated in {operand.evidence_id}")
        if calc.in_reference and case.reference is not None and not mentions_quantity(case.reference.text, calc.expected.value, calc.expected.unit):
            add("calculation_result_not_in_reference", f"{calc.calc_id}: the reference does not state {calc.expected.value:g} {calc.expected.unit}")

    if case.reference is not None:
        for failure in reference_self_check(case):
            add("reference_fails_own_assertion", failure)
    return issues


def reference_self_check(case: JudgeCase) -> list[str]:
    """The reference must pass the case's text-level assertions."""

    text = case.reference.text if case.reference else ""
    failures = []
    for assertion in case.assertions:
        if isinstance(assertion, StatesQuantity) and not mentions_quantity(text, assertion.value, assertion.unit):
            failures.append(f"reference omits required {assertion.value:g} {assertion.unit}")
        if isinstance(assertion, MustNotStateQuantity) and mentions_quantity(text, assertion.value, assertion.unit):
            failures.append(f"reference states forbidden {assertion.value:g} {assertion.unit}")
        if isinstance(assertion, MustNotContain):
            lowered = text.lower()
            failures += [f"reference contains forbidden phrase {phrase!r}" for phrase in assertion.phrases if phrase.lower() in lowered]
    return failures


def check_cases(cases: Iterable[JudgeCase], registry: Optional[Registry] = None) -> list[Issue]:
    registry = registry or Registry.load()
    return [issue for case in cases for issue in check_case(case, registry)]
