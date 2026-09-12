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
Apply CLAUDE.md and backend-data.md for shared persistence/job mechanics.
Tutor is Netra's second and only other agent.
Quizzes, grading validation, scheduling, projections and evaluators are not agents.

## Tutor boundaries

Tutor owns the teaching objective, explanation, hints and pedagogical adaptation.
Learning service owns validated assessment commits and learning-status derivation.
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

Current learning status is derived using an explicit versioned application policy.
Only these initial labels are permitted:
- not_assessed
- needs_review
- developing
- demonstrated_recently

Do not invent probabilities, mastery scores, confidence thresholds or new labels.
Discussing a topic, reading a summary or generating a quiz is not an assessment.
An unavailable history service is not evidence of not_assessed.
If derivation/scheduling policy is unspecified, report it and leave an explicit stub.
Do not turn illustrative handbook intervals or thresholds into hidden product policy.

## Quiz privacy and persistence

Validate that questions and reference answers are supported by the selected evidence.
Keep public question content separate from private answers, rubrics and grading notes.
Persist the pending question before delivering it to the client.
Never include private fields in public contracts, TTS input or ordinary logs.
Do not leak reference answers before the student's answer is finalized.
Use approved feedback policy for any later explanation or answer disclosure.
Duplicate submission/replay must not create another assessment attempt.

## Review and Neo4j projection

Keep exposure, assessment and review-due records semantically separate.
Derive review scheduling from the approved versioned policy.
Respect explicit student skip/defer choices.
Do not silently create concepts or prerequisite relationships from Tutor output.

Project committed canonical records through the PostgreSQL outbox/job mechanism.
Neo4j updates are idempotent, version-aware and rebuildable.
A projection failure never reverses an already committed assessment.
Older events cannot overwrite newer projected state.
Use canonical PostgreSQL records when the existing reduced-mode path permits it.

## Evaluation

Keep evaluation outside the live student response path.
Use human/source-checked references and explicit scoring rubrics.
Distinguish factual support, teaching quality and observed learning outcomes.
Model-judge scores are not ground truth or probabilities of correctness.
Preserve held-out cases; do not tune prompts against them and call them unseen.
Do not claim causal learning improvement from unsupported comparisons.
No evaluator network calls or model downloads without explicit authorization.

## Verification focus

Check answer-key isolation, persisted pending questions, transcript correction,
assisted attempts, duplicate submissions, derivation policy and projection replay.
Report missing policy, unavailable checks and limitations explicitly.
