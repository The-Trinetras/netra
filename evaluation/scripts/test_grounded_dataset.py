"""netra-grounded-v1: sources, grounding checks, dataset rules and review gates.

Everything here is local and model-free. Where a test builds a broken case,
it is to prove the corresponding check catches that defect.
"""

import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

import author_synthetic_miniatures
import build_grounded_dataset
import build_source_registry
from dataset_checks import CATEGORIES, check_dataset
from eval_dataset import (
    DatasetSnapshot,
    LabelStatus,
    MustNotContain,
    Quantity,
    SourceExcerpt,
    StatesQuantity,
    WithheldEvidence,
    cases_hash,
    load_dataset,
)
from grounding import Registry, check_case, check_cases, mentions_quantity

REPO = Path(__file__).resolve().parents[2]
GROUNDED = REPO / "evaluation" / "datasets" / "netra_grounded_v1.json"
V1 = REPO / "evaluation" / "datasets" / "tutor_reference_v1.json"
V1_HASH = "e942307c144ac4f7c0a239e3368eae25b73fe1fb37d3d5c91a0ababc115f7572"


@pytest.fixture(scope="module")
def registry():
    return Registry.load()


@pytest.fixture(scope="module")
def grounded():
    return load_dataset(GROUNDED)


def codes(issues):
    return {issue.code for issue in issues}


# --- compatibility and reproducibility --------------------------------------------


def test_the_schema_extension_leaves_tutor_reference_v1_with_its_original_hash():
    assert load_dataset(V1).content_hash == V1_HASH


def test_sources_registry_and_dataset_regenerate_byte_for_byte():
    assert author_synthetic_miniatures.render() == author_synthetic_miniatures.TARGET.read_text(encoding="utf-8")
    assert build_source_registry.render(build_source_registry.build()) == build_source_registry.REGISTRY_PATH.read_text(encoding="utf-8")
    assert build_grounded_dataset.render(build_grounded_dataset.build()) == GROUNDED.read_text(encoding="utf-8")


def test_miniature_ids_follow_the_documented_formula():
    data = json.loads(author_synthetic_miniatures.TARGET.read_text(encoding="utf-8"))
    for source in data["sources"]:
        key = source["source_key"]
        assert source["source_id"] == str(uuid5(NAMESPACE_URL, f"netra-eval-synthetic:{key}/source"))
        for version in source["versions"]:
            label = f"v{version['version_number']}"
            assert version["source_version_id"] == str(uuid5(NAMESPACE_URL, f"netra-eval-synthetic:{key}/{label}"))


def test_m3_variant_trust_comes_from_m3_validators(registry):
    trust = {(item["evidence_id"], item["variant"]): item["trust"]
             for item in registry.sources["ohm-study-pack-m3"]["evidence"]}
    assert trust[("ev-fig02", None)] == "source_verified"
    assert trust[("ev-fig02", "unreadable_axes")] == "unreadable"
    assert trust[("ev-fig02", "swap_axes")] == "source_mismatch"
    assert trust[("ev-tbl01", "drop_row_2")] == "source_mismatch"
    assert trust[("ev-eq01", "operator_divide")] == "source_mismatch"


def test_placeholder_fixture_text_is_excluded_not_registered(registry):
    m1 = registry.sources["ohm-study-pack-m1-pdf"]
    assert [item["evidence_id"] for item in m1["excluded"]] == ["ev-fig02"]
    assert all(item["evidence_id"] != "ev-fig02" for item in m1["evidence"])


def test_every_registered_source_is_synthetic(registry):
    assert {s["permission"] for s in registry.sources.values()} == {"synthetic"}
    assert {s["origin"] for s in registry.sources.values()} == {"project_fixture", "new_synthetic_miniature"}


# --- the committed dataset --------------------------------------------------------


def test_the_grounded_dataset_has_no_errors_and_only_the_known_warning(grounded, registry):
    issues = check_cases(grounded.cases, registry) + check_dataset(grounded)
    assert [i for i in issues if i.severity == "error"] == []
    assert [(i.code, i.case_id) for i in issues] == [("duplicate_student_input", "m1-01-injection-in-retrieved-page")]


def test_nothing_in_the_draft_is_gold_reviewed_or_frozen(grounded):
    for case in grounded.cases:
        assert case.review_status == "unreviewed"
        if case.reference is not None:
            assert case.reference.status is LabelStatus.SUGGESTED
            assert case.reference.reviewer is None and not case.reference.source_checked
    assert not (REPO / "evaluation" / "locked" / "netra-grounded-v1.heldout.json").exists()


def test_exposed_cases_are_never_heldout_and_families_stay_in_one_split(grounded):
    splits_by_family = {}
    for case in grounded.cases:
        splits_by_family.setdefault(case.problem_family, set()).add(case.split)
        if case.prior_exposure:
            assert case.split != "heldout"
    assert all(len(splits) == 1 for splits in splits_by_family.values())
    assert all(case.provenance.origin == "new_synthetic_miniature" for case in grounded.by_split("heldout"))


def test_every_category_is_covered_and_heldout_candidates_span_most(grounded):
    categories = {case.category for case in grounded.cases}
    assert categories == set(CATEGORIES)
    heldout = {case.category for case in grounded.by_split("heldout")}
    assert {"explanation_calculation", "misconception_tutoring", "evidence_state", "citation_support",
            "transcript_visual", "unanswerable_uncertainty", "optional_check_multiturn",
            "embedded_instruction"} <= heldout


# --- grounding checks catch what they claim to --------------------------------


def _case(grounded, case_id, **update):
    return grounded.case(case_id).model_copy(update=update)


def test_an_altered_excerpt_is_caught(grounded, registry):
    case = grounded.case("ch4-13-resistance-from-table")
    altered = [case.source_excerpts[0].model_copy(update={"text": case.source_excerpts[0].text.replace("6 V", "7 V")}),
               *case.source_excerpts[1:]]
    assert "excerpt_text_mismatch" in codes(check_case(case.model_copy(update={"source_excerpts": altered}), registry))


def test_supplying_another_accounts_or_a_stale_version_is_caught(grounded, registry):
    case = grounded.case("ch4-20-other-students-note")
    other = registry.sources["other-student-note-m2"]["evidence"][0]
    leak = SourceExcerpt(evidence_id=other["evidence_id"], source_version_id=other["source_version_id"],
                         locator=other["locator"], text=other["text"], source_key="other-student-note-m2")
    found = codes(check_case(case.model_copy(update={"source_excerpts": [*case.source_excerpts, leak]}), registry))
    assert {"supplied_other_account", "withheld_also_supplied"} <= found

    stale_case = grounded.case("ch4-16-stale-version-offered")
    stale = registry.sources["intro-circuits-ch4"]["evidence"]
    v1 = next(item for item in stale if item["evidence_id"] == build_grounded_dataset.STATEMENT_V1)
    stale_excerpt = SourceExcerpt(evidence_id=v1["evidence_id"], source_version_id=v1["source_version_id"],
                                  locator=v1["locator"], text=v1["text"], source_key="intro-circuits-ch4")
    assert "supplied_stale_version" in codes(check_case(stale_case.model_copy(update={"source_excerpts": [stale_excerpt]}), registry))


def test_a_withheld_item_with_the_wrong_reason_is_caught(grounded, registry):
    case = grounded.case("ch4-19-deleted-note")
    wrong = [WithheldEvidence(**{**case.withheld_evidence[0].model_dump(), "reason": "failed_version"})]
    assert "withheld_reason_wrong" in codes(check_case(case.model_copy(update={"withheld_evidence": wrong}), registry))


def test_wrong_calculations_and_ungrounded_inputs_are_caught(grounded, registry):
    case = grounded.case("series-01-total-and-current")
    wrong = case.calculations[0].model_copy(update={"expected": Quantity(value=6, unit="ohm")})
    assert "calculation_wrong" in codes(check_case(case.model_copy(update={"calculations": [wrong, *case.calculations[1:]]}), registry))
    invented = case.calculations[0].model_copy(update={"operands": [Quantity(value=7, unit="ohm", evidence_id="s1-prob1"),
                                                                   case.calculations[0].operands[1]]})
    found = codes(check_case(case.model_copy(update={"calculations": [invented]}), registry))
    assert "calculation_input_not_in_evidence" in found


def test_a_reference_that_breaks_its_own_assertions_is_caught(grounded, registry):
    case = grounded.case("pack-10-hint-after-reasoning")
    leaked = case.reference.model_copy(update={"text": case.reference.text + " It's 8 volts."})
    assert "reference_fails_own_assertion" in codes(check_case(case.model_copy(update={"reference": leaked}), registry))


def test_invented_quotes_are_caught(grounded, registry):
    case = grounded.case("pack-09-copied-current-value")
    turns = [case.conversation[0].model_copy(update={"content": "A line the walkthrough never says."}), *case.conversation[1:]]
    assert "quote_not_in_spec" in codes(check_case(case.model_copy(update={"conversation": turns}), registry))
    quoted = case.reference.model_copy(update={"text": "As the chapter puts it, “resistance is the opposite of current”."})
    assert "reference_quote_unsupported" in codes(check_case(case.model_copy(update={"reference": quoted}), registry))


def test_quantity_matching_respects_number_and_unit_boundaries():
    assert mentions_quantity("It gives 8 V.", 8, "V") and mentions_quantity("8 volts", 8, "V")
    assert not mentions_quantity("It gives 18 V.", 8, "V")
    assert not mentions_quantity("2 Apples", 2, "A") and mentions_quantity("2 A through it", 2, "A")
    assert mentions_quantity("0.5 A", 0.5, "A") and mentions_quantity("1.0 A", 1, "A")
    assert mentions_quantity("2 Ω", 2, "ohm") and mentions_quantity("2 ohms", 2, "ohm")


def test_dataset_rules_catch_leakage_exposure_and_unseen_input(grounded):
    moved = [case.model_copy(update={"split": "calibration"}) if case.case_id == "series-01-total-and-current" else case
             for case in grounded.cases]
    exposed = [case.model_copy(update={"split": "heldout"}) if case.case_id == "ch4-13-resistance-from-table" else case
               for case in grounded.cases]
    hidden = [case.model_copy(update={"instruction": "Explain."}) if case.case_id == "lamp-01-is-it-ohmic" else case
              for case in grounded.cases]

    def issues(cases):
        return codes(check_dataset(DatasetSnapshot(dataset_name="t", content_hash=cases_hash(cases), cases=tuple(cases))))

    assert "family_spans_splits" in issues(moved)
    assert {"exposed_case_in_heldout", "family_spans_splits"} <= issues(exposed)
    assert "instruction_omits_input" in issues(hidden)
