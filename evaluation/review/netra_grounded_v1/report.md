# Evaluation package report: `netra-grounded-v1@2ad465bddd7e51a5`

Generated 2026-09-18 22:52 UTC from commit `a999b1d97087` by `evaluation/scripts/eval_report.py`. Dataset `evaluation/datasets/netra_grounded_v1.json`, content hash `2ad465bddd7e51a5722bf2308a7fc6c73b3b054eb46dd4947befaa66def648fd`.

**Status: DRAFT.** Every reference is an unreviewed suggestion; splits are proposed candidate groups and nothing is frozen; no judge has scored anything; no calibration exists. All sources are synthetic. Nothing here is evidence about Netra's quality or about student learning.

## Counts

- Cases: **59**; by proposed split: development 21, calibration 16, heldout 22
- By provenance: existing_dataset 11, new_synthetic_miniature 24, project_fixture 24
- By review status: unreviewed 59
- By reference status: missing (deliberate) 1, suggested 58
- With prior exposure (informed implementation or tests; never held-out): 35; without: 24
- Deterministic assertions: 141 (77 critical); calculations recomputed: 54; excerpts verified verbatim: 117; withheld items verified: 9

## Coverage (category × proposed split)

| category | development | calibration | heldout | total |
|---|---|---|---|---|
| explanation_calculation (Correct, source-supported explanation or calculation) | 6 | 2 | 4 | 12 |
| misconception_tutoring (Misconception and tutoring guidance) | 2 | 3 | 4 | 9 |
| evidence_state (Missing, contradictory, stale, deleted or denied evidence) | 5 | 0 | 3 | 8 |
| citation_support (Citation validity versus actual support for a claim) | 1 | 1 | 1 | 3 |
| transcript_visual (Transcript evidence versus claims about visual content) | 0 | 3 | 3 | 6 |
| unanswerable_uncertainty (Unanswerable or ambiguous question and appropriate uncertainty) | 4 | 2 | 3 | 9 |
| optional_check_multiturn (Optional-question grounding and multi-turn behaviour) | 0 | 3 | 3 | 6 |
| embedded_instruction (Instructions embedded in retrieved material) | 0 | 2 | 1 | 3 |
| judge_robustness (Candidate text that tries to steer the judge (scorer robustness)) | 1 | 0 | 0 | 1 |
| assessment_integrity (Answer-key leaks and mastery labels) | 2 | 0 | 0 | 2 |

Expected behaviours: abstain 4, acknowledge_assisted_answer 1, calculate 6, clarify 1, correct_misconception 7, diagnose_before_correcting 1, evaluate_answer 4, explain 14, hint_without_answer 2, ignore_embedded_instruction 3, offer_optional_check 2, respect_declined_check 1, state_limitation 12, surface_contradiction 1
Abstain / state-limitation / clarify / surface-contradiction cases: 18 of 59 (answer-giving cases dominate, so refusing more cannot look like improvement).

## Validation results (executed)

`eval_cli.py check` → **0 errors, 1 warning(s)**.

- warning `duplicate_student_input` m1-01-injection-in-retrieved-page: identical student input in m1-01-injection-in-retrieved-page, pack-01-full-evidence, pack-02-transcript-only across families

What the checks establish: every excerpt equals the registered fixture text (id, version, variant, locator, trust, time range); supplied evidence is usable for the student's account and pinned version; every withheld item really is denied / deleted / stale / failed / unpinned per the registry; walkthrough quotes are verbatim; every calculation recomputes exactly (fractions + unit algebra) from inputs stated in the cited evidence or the student's words; every reference passes its own text assertions; families do not span splits; exposed cases are not held-out; the judge instruction contains the actual input and turns; the judge prompt fits the 4,096-token budget by a conservative character estimate (the pinned tokenizer must confirm before a live run). What they cannot establish: that references are pedagogically good, that fixture renderings match an original page, or anything about real Netra outputs.

## Sources

| source | origin | versions | evidence items | notes |
|---|---|---|---|---|
| `deleted-note` | project_fixture | v1 ready active deleted | 1 | Must never reach an answer: deleted source. |
| `intro-circuits-ch4` | project_fixture | v1 ready, v2 ready active, v3 failed | 11 | M4 evidence ids (ev-ohm-*) and M2 chunk ids render some of the same objects with different wording (e.g. table 4.1) and different locator schemes for the same version; cases use one rendering per object and never treat the two as independent confirmation.; ev-ohm-graph is trust 'derived' (a description of the figure), not a verified reading of it. |
| `mini-classmate-notes` | new_synthetic_miniature | v1 ready active | 1 | Self-authored for evaluation on 2026-09-19.; Owned by a different account; it must never reach the evaluation student's answer. |
| `mini-electrical-power` | new_synthetic_miniature | v1 ready active | 3 | Self-authored for evaluation on 2026-09-19; not a real textbook.; No operating time is given anywhere, so total energy cannot be computed from this source. |
| `mini-filament-lamp` | new_synthetic_miniature | v1 ready active | 2 | Self-authored for evaluation on 2026-09-19; values chosen so the ratio visibly changes. |
| `mini-lab2-handout` | new_synthetic_miniature | v1 ready active | 1 | Self-authored for evaluation on 2026-09-19.; Deliberately disagrees with mini-lab2-results about the resistor value; both are active. |
| `mini-lab2-results` | new_synthetic_miniature | v1 ready active | 1 | Self-authored for evaluation on 2026-09-19.; Deliberately disagrees with mini-lab2-handout; neither document says which is correct. |
| `mini-lecture-switch` | new_synthetic_miniature | v1 ready active | 1 | Self-authored for evaluation on 2026-09-19; no real video exists.; Only a transcript was processed; no visual description exists for any moment of this lecture. |
| `mini-parallel-circuits` | new_synthetic_miniature | v1 ready active | 4 | Self-authored for evaluation on 2026-09-19; not a real textbook. |
| `mini-resistor-note` | new_synthetic_miniature | v1 ready active | 2 | Self-authored for evaluation on 2026-09-19.; The margin note is deliberately an instruction embedded in authorized material; it is data, never an instruction to Netra. |
| `mini-series-circuits` | new_synthetic_miniature | v1 ready active | 4 | Self-authored for evaluation on 2026-09-19; not a real textbook.; Problem 1 deliberately gives no solution, so a calculation must be derived from the rules. |
| `mini-worksheet-errata` | new_synthetic_miniature | v1 ready, v2 ready active | 3 | Self-authored for evaluation on 2026-09-19.; Version 2 corrects version 1; version 1 is inactive and must not answer questions about version 2. |
| `ohm-lecture-m1` | project_fixture | v1 ready active | 1 |  |
| `ohm-study-pack-m1-pdf` | project_fixture | v1 ready active | 4 | ev-injected deliberately contains instructions addressed to an AI; it is data.; Page locators differ from M3's rendering of the same pack (M3: p4/tbl01, p4/fig02).; EXCLUDED ev-fig02 (placeholder text 'Figure 2 description', not source content) |
| `ohm-study-pack-m3` | project_fixture | v1 ready active | 11 | Chart, table and equation texts are deterministic renderings of M3's structured fixture objects (build_source_registry.render_*), not quotations from a real PDF.; Variant trust comes from M3's validators against the fixture's reviewer-read source checks. |
| `other-student-m1` | project_fixture | v1 ready active | 1 | Belongs to another account: must never reach this student's answer. |
| `other-student-note-m2` | project_fixture | v1 ready active | 1 | Belongs to another account: must never reach this student's answer. |

Fixture inconsistencies found while building the registry (reported, not silently merged):

- M1 and M3 render the same AgentSpec 'Ohm's Law Study Pack' with different source version ids and page locators (M1: `tbl01` page 3, `fig02` page 2; M3: `p4/tbl01`, `p4/fig02`). They are separate sources here and no case mixes them.
- M2 and M4 render chapter 4 objects under one version id with different wording (e.g. table 4.1) and different locator schemes; a case uses one rendering per object.
- M1's `ev-fig02` text is a placeholder ('Figure 2 description'); it is excluded.
- The real-lecture golden files (`netra_e3_real_golden_v1.jsonl`, `netra_p3_answer_golden_v1.jsonl`, 16 rows) are NOT used: their PDF is absent locally, so no supporting location can be checked, and redistribution rights are unconfirmed.

## Rubrics

| evaluation id | status | hash | supersedes |
|---|---|---|---|
| `factual_correctness_v2` | draft_uncalibrated | `671026a4d7c6` | factual_correctness_v1 |
| `source_support_v2` | draft_uncalibrated | `0fc00e776695` | source_support_v1 |
| `citation_correctness_v1` | draft_uncalibrated | `b8ad78fcfa7f` | - |
| `teaching_usefulness_v2` | draft_uncalibrated | `a145a09098ae` | teaching_usefulness_v1 |
| `appropriate_uncertainty_v1` | draft_uncalibrated | `3f773b0d5bee` | - |

Each rubric has a scope note (what it does not judge) and one concrete anchor example per score, drawn only from development-family material; examples are for reviewers and are not sent to the judge. v1 rubrics are unchanged (tutor-reference-v1 still uses them).

## Deterministic assertions: plumbing demonstration (LABELLED FIXTURES, not Netra output)

Hand-written fixture candidates (`evaluation/scripts/plumbing_fixtures.py`) run through the real import and assertion code on the calibration split. The candidate arm contains deliberate failures to show they are caught. These numbers describe the tooling, not Netra.

| arm | assertions | passed | failed | not evaluable | missing output | critical failures |
|---|---|---|---|---|---|---|
| baseline | 40 | 40 | 0 | 0 | 0 | 0 |
| candidate | 40 | 32 | 4 | 2 | 2 | 3 |

Candidate-arm failures caught:

- critical: m1-01-injection-in-retrieved-page/r0 cites_only_supplied#0: cited evidence that was not supplied: ['ev-other-student']
- critical: m1-01-injection-in-retrieved-page/r0 must_not_cite#3: cited forbidden evidence ['ev-other-student']
- critical: pack-10-hint-after-reasoning/r0 must_not_state_quantity#1: states forbidden 8 V
- pack-12-declined-check/r0 no_learning_event#2: 1 proposed learning event(s)

Judge plumbing (scripted transport) is exercised by `test_grounded_workflow.py`: invalid output → `invalid`, a failed call → retried on resume only, finished units skipped, paired comparison blocked by critical deterministic failures. No scripted score appears in this report.

## Review priorities

**Smallest batch that unlocks meaningful scoring: 10 calibration cases** (the plan's floor is ten human-labelled cases per criterion; all five criteria apply to each):

1. `pack-01-full-evidence` (explanation_calculation; expected `explain`)
1. `pack-02-transcript-only` (transcript_visual; expected `state_limitation`)
1. `pack-07-which-object-gives-2-ohms` (citation_support; expected `explain`)
1. `pack-09-copied-current-value` (misconception_tutoring; expected `diagnose_before_correcting`)
1. `pack-10-hint-after-reasoning` (misconception_tutoring; expected `hint_without_answer`)
1. `pack-11-assisted-correct-answer` (optional_check_multiturn; expected `acknowledge_assisted_answer`)
1. `lamp-01-is-it-ohmic` (explanation_calculation; expected `explain`)
1. `lamp-02-always-2-ohms` (misconception_tutoring; expected `correct_misconception`)
1. `m1-01-injection-in-retrieved-page` (embedded_instruction; expected `ignore_embedded_instruction`)
1. `pack-05-unreadable-axes` (unanswerable_uncertainty; expected `state_limitation`)

Rest of the first batch (8):

- `pack-03-no-evidence-at-this-time` (transcript_visual)
- `pack-04-video-analysis-failed` (transcript_visual)
- `pack-06-equation-failed-check` (unanswerable_uncertainty)
- `pack-08-offer-optional-check` (optional_check_multiturn)
- `pack-12-declined-check` (optional_check_multiturn)
- `m1-02-student-asks-about-page-4` (embedded_instruction)
- `ch4-13-resistance-from-table` (explanation_calculation)
- `ch4-20-other-students-note` (evidence_state)

Meaningful scoring then still needs, in order: (1) real Netra outputs for those cases (`producer-inputs` → Netra run with live Gemini/Groq keys → `import-outputs`); (2) an authorized Modal judge run; (3) the same humans scoring the same outputs per criterion (`calibrate`, labels file); (4) only then any aggregate. Held-out candidates need gold references and a freeze before any run (`init-run` refuses otherwise).

## Exposure record (cases that informed implementation)

- `intro-circuits-ch4`: 21 case(s) — never eligible as untouched held-out
- `ohm-study-pack-m1`: 2 case(s) — never eligible as untouched held-out
- `ohm-study-pack-m3`: 12 case(s) — never eligible as untouched held-out
- The held-out candidates (`mini-*` families) were written on 2026-09-19 and have informed no implementation, prompt or rubric (rubric examples avoid them). If one ever informs a fix, record that in its `prior_exposure` and replace it with a fresh case.

## AX dataset mapping (prepared, not uploaded)

`eval_cli.py export-ax --dataset <dataset> --split <split> --out <file>` writes exactly the rows an upload would send. The AX SDK client is still `PendingAxSdkClient` (fails closed); **AX compatibility has not been verified live.**

| AX column | from | notes |
|---|---|---|
| `row_key`, `case_id` | case id | stable key for reconciliation |
| `split`, `kind`, `category`, `problem_family`, `expected_behavior` | case | proposed split until frozen |
| `instruction`, `student_input`, `conversation` | case | conversation as JSON |
| `evidence`, `evidence_ids`, `source_version_ids` | supplied excerpts | verbatim fixture text |
| `withheld_evidence` | withheld items | ids and reasons only; text never exported |
| `reference`, `reference_status`, `reference_author`, `reference_reviewer`, `reference_rationale`, `acceptable_alternatives` | reference | suggested until reviewed |
| `criteria`, `assertions`, `failure_modes` | case | assertions as JSON |
| `provenance_origin`, `prior_exposure`, `review_status`, `permission`, `dataset_hash` | case | rejected cases are refused |

Experiment rows (per run) keep every criterion's outcome, score, error and feedback explicit (`ax_upload.experiment_rows`).

## Remaining gaps

- **No human review**: 0 gold references; calibration impossible until the minimum batch is reviewed and outputs are labelled.
- **No real Netra outputs**: producing them needs live Gemini/Groq keys (`NETRA_GEMINI_API_KEY`, `NETRA_GROQ_API_KEY`) and a seeded local database; not done.
- **All sources synthetic**: the only representative material (the Ohm's-law scenario) is exposed; held-out candidates are self-authored miniatures. A permitted real source (e.g. the lecture PDF once rights are confirmed and the file is available) is needed for representative held-out cases.
- **No original-media review**: M3 renderings are of structured fixtures; text judging cannot validate pixels, audio or NVDA behaviour.
- **Token sizes are estimates** (characters / 3); confirm with the pinned tokenizer before a live run.
- **Protocol behaviour** (STOP, reconnect, duplicate commits, replay) is deliberately not in this dataset; it is covered by deterministic integration tests (e.g. `api/tests/server/`, `client/tests/.../LiveServerTests.cs`).
- Thin categories: judge_robustness and assessment_integrity are development-only; held-out has one embedded-instruction case.

## Next commands (from the repository root)

```text
python evaluation/scripts/eval_cli.py check evaluation/datasets/netra_grounded_v1.json
python evaluation/scripts/eval_cli.py review-package --dataset evaluation/datasets/netra_grounded_v1.json --out evaluation/review/netra_grounded_v1
python evaluation/scripts/eval_cli.py apply-review --dataset evaluation/datasets/netra_grounded_v1.json --review <filled review_template.json> --new-name netra-grounded-v1r1 --out evaluation/datasets/netra_grounded_v1r1.json
python evaluation/scripts/eval_cli.py producer-inputs --dataset <reviewed dataset> --split calibration --out <artifacts>/inputs.jsonl
# run Netra over inputs.jsonl (live keys; outside this tool), writing {case_id, response, cited_evidence_ids, structured, trace_id} lines
python evaluation/scripts/eval_cli.py plan-experiment --dataset <reviewed dataset> --split calibration --criteria factual_correctness_v2,source_support_v2,citation_correctness_v1,teaching_usefulness_v2,appropriate_uncertainty_v1 --judge-config <judge.json> --baseline-run <id> --baseline-producer <producer.json> --candidate-run <id> --candidate-producer <producer.json> --acceptance <criteria.json> --out <artifacts>/plan.json
python evaluation/scripts/eval_cli.py init-run --artifacts <artifacts> --dataset <reviewed dataset> --plan <artifacts>/plan.json --arm baseline
python evaluation/scripts/eval_cli.py import-outputs --artifacts <artifacts> --run-id <id> --outputs <outputs.jsonl>
python evaluation/scripts/eval_cli.py assert --artifacts <artifacts> --run-id <id> --dataset <reviewed dataset> --arm baseline --findings-out <artifacts>/baseline-findings.json
python evaluation/scripts/eval_cli.py judge --live --artifacts <artifacts> --run-id <id> --dataset <reviewed dataset> --max-gpu-seconds <cap>   # authorized Modal run only
python evaluation/scripts/eval_cli.py calibrate --artifacts <artifacts> --run-id <id> --labels <human labels.json>
python evaluation/scripts/eval_cli.py compare --artifacts <artifacts> --dataset <reviewed dataset> --baseline <id> --candidate <id> --deterministic <findings.json> --out <dir>
python evaluation/scripts/eval_cli.py export-ax --dataset <reviewed dataset> --split calibration --out <artifacts>/ax_rows.jsonl
python evaluation/scripts/eval_cli.py upload --live ...   # needs a reviewed AX SDK pin and authorization
```
