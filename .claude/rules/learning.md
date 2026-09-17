---
paths:
  - "api/**/learning/**"
  - "api/**/tutor/**"
  - "worker/src/netra_worker/jobs/learning_projection/**"
  - "worker/src/netra_worker/jobs/review_scheduler/**"
  - "evaluation/**"
---

# Tutor and learning rules

Owner: M4 Learning.
Read [current scope](../../docs/architecture/current-scope.md), [ownership](../../docs/team/ownership.md)
and the corresponding M1–M5 guide before implementation. These rules share the
canonical authorities used by root AGENTS.md and CLAUDE.md.
Apply CLAUDE.md and backend-data.md for shared persistence/job mechanics.
Tutor is Netra's second and only other agent.
Optional checks, grading validation, projections and evaluators are not agents.
Legacy scheduling code is retained but outside current product requirements.

## Tutor boundaries

Tutor owns the teaching objective, explanation, hints and pedagogical adaptation.
Learning service owns validated factual activity/answer/assistance commits.
Tutor cannot assign mastery, grant access, change identity or mutate Neo4j.
Do not duplicate Coordinator routing or Session service state ownership.

Use the approved Groq adapter and configured openai/gpt-oss-120b model.
Do not silently substitute a provider/model or alter reasoning configuration.
Validate Coordinator/Tutor handoffs against shared/contracts/agent/.
Receive bounded relevant dialogue, the original utterance and authorized evidence IDs.
Do not receive entire Coordinator history, private reasoning or credentials.
Inherited work retains the originating turn's permissions, deadline and budgets.

## Lesson state and teaching

Persist lesson identity, target concepts, delivered explanation, pending question,
hints/assistance used and the next permitted step using existing domain models.
Session service owns the active-lesson reference and reading return position.
Keep lesson progress separate from network connection state.
Reconnect/return-to-question restores the same persisted question.

Answer the requested question before introducing unnecessary prerequisite checks.
Offer additional depth and ask before switching into a quiz.
Distinguish answers from repeat, skip, navigation, consent and transcript correction.
Only a substantive finalized answer to an existing question is assessable.
Preserve assisted versus independent attempts.
Do not infer disability, general ability or durable understanding from one response.

## Assessment authority

Assessment history is canonical, append-oriented evidence in PostgreSQL.
Preserve question/rubric versions, response, assistance, outcome and source evidence.
Apply the existing correction policy without erasing the original audit history.
Tutor proposes learning events or grades.
Learning service validates ownership, question identity, evidence, rubric,
response finality and replay identity before committing.
Do not interpret a proposed model grade as a successful database write.

Record delivered study activity, each answer, student-stated reasoning, feedback
and assistance separately. Adapt to observed responses; never store an inferred
misconception as an established fact. Discussing a topic or generating a question
is not an assessment. An unavailable history service is not evidence of no history.
“Studied — understanding not tested” is factual activity wording, not a new enum.
Automatic learning labels and spaced-review scheduling are removed requirements.
Existing legacy status enums, policy classes and review code remain untouched;
do not complete old thresholds/intervals or fabricate labels to fit a handoff.
Coordinate missing factual-history schemas with M1/M2; report the gap.

## Quiz privacy and persistence

Validate that questions and reference answers are supported by the selected evidence.
Keep public question content separate from private answers, rubrics and grading notes.
Persist the pending question before delivering it to the client.
Never include private fields in public contracts, TTS input or ordinary logs.
Do not leak reference answers before the student's answer is finalized.
Use approved feedback policy for any later explanation or answer disclosure.
Duplicate submission/replay must not create another assessment attempt.

## History and Neo4j projection

Keep delivered activity, answers and assistance semantically separate.
Respect a declined optional check; do not create a grade for it.
Do not schedule spaced review or delete existing legacy review artifacts.
Do not silently create concepts or prerequisite relationships from Tutor output.

Project committed canonical records through the PostgreSQL outbox/job mechanism.
Neo4j updates are idempotent, version-aware and rebuildable.
A projection failure never reverses an already committed assessment.
Older events cannot overwrite newer projected state.
Use canonical PostgreSQL records when the existing reduced-mode path permits it.

## Evaluation

Keep evaluation outside the live student response path.
Prioritize deterministic source/evidence checks, original-media review, state and
recovery tests and accessibility tasks. Follow the
[model/evaluation plan](../../docs/architecture/model-evaluation-plan.md): the
primary model judge is Prometheus-2 7B hosted on Modal/A100 40 GB, isolated from
student turns. M4 owns its deployment source and HTTP adapter; M2 reviews the
separate GPU environment, authentication and credit/stop controls. Lightning AI
and Kaggle are alternatives; no AWS GPU or silent Gemini/GLM fallback. Integration
and live calibration remain pending. Do not add the Ragas package:
it requires full LangChain and OpenAI SDKs (see runtime-baseline.md "Evaluation
dependencies"). Implement Ragas-style metrics in evaluation scripts instead.
Use permitted synthetic/public fixtures, source-checked references, the checkpoint's
grading template, anchored 1–5 rubrics and strict result parsing. Report exhausted
credits as unscored cases and incomplete model evaluation while continuing human
review. No paid overage or private student data in hosted fixtures. Use
explicit scoring rubrics. Blindfolded sighted testing is an interaction exercise,
not proof of blind-student usability.
Distinguish factual support, teaching quality and observed learning outcomes.
Model-judge scores are not ground truth or probabilities of correctness.
Preserve held-out cases; do not tune prompts against them and call them unseen.
Do not claim causal learning improvement from unsupported comparisons.
No evaluator network calls or model downloads without explicit authorization.

## Verification focus

Check answer-key isolation, persisted pending questions, transcript correction,
assisted attempts, untested study, stated reasoning, duplicate submissions and
projection replay. Check that adaptation uses observed evidence without mastery claims.
Report missing policy, unavailable checks and limitations explicitly.
