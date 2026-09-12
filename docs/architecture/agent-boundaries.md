# Agent boundaries

## Authority

Read with the [Engineering Plan](Netra_Final_Engineering_Plan.md), [CLAUDE.md](../../CLAUDE.md), [Coordinator rules](../../.claude/rules/coordinator.md), [Learning rules](../../.claude/rules/learning.md), [runtime baseline](runtime-baseline.md) and [agent contracts](../../shared/contracts/agent/v1/). These describe exactly two agents with different state and permissions.

## Agent vs non-agent

| Classification | Components |
|---|---|
| Agent | Coordinator; Tutor. Each chooses bounded actions toward its distinct goal. |
| Deterministic application logic | Command routing, navigation, authorization, cancellation, version checks, learning-policy validation and derivation. |
| Service or bounded tool | Identity, Session, Learning, retrieval, speech, source access and multimedia operations. A model inside a tool does not create another agent. |
| Workflow or worker | Ingestion, job execution, outbox delivery, projection updates and review scheduling. |
| Infrastructure or UI | LangGraph orchestration/checkpointing, provider adapters, databases, projections and WPF components. |
| Evaluator | Offline evaluation; does not direct the student response path. |

## Coordinator

**Owner:** M1. Coordinates study tasks, selects authorized sources, compares evidence, requests permitted actions, clarifies ambiguity and hands teaching to Tutor. Gemini is accessed through the configured adapter; the Coordinator model ID is the approved configured pin `gemini-3.8-flash` (2026-09-12, `api/src/netra_api/coordinator/providers/gemini.py`) — a configuration decision, not an independently verified claim that this model id currently exists on the provider's API.

Inputs are the original accepted utterance, trusted account/session context, active source location, allowed source identifiers, bounded relevant dialogue and selected tool schemas. Identity comes from the application, never model arguments. Retrieved text, titles and summaries remain untrusted data.

The plan's permitted tool surface is contextual, not an unrestricted grant:

| Capability | Plan tool names and boundary |
|---|---|
| Read evidence and learning context | `search`, `describe_visual`, `get_student_concepts`, `get_concept_prerequisites`; bounded and authorized. |
| Source/session discovery | `recall_session`, `get_toc`, `search_youtube`, `search_drive`, `search_web`; discovery does not grant ingestion permission. |
| Request application mutations | `navigate`, `set_preference`, `run_ingestion_pipeline`; owning services validate access, versions, operation identity and applicable quota. |
| Teaching handoff | `delegate_to_tutor`; only through the approved typed handoff. |

These names identify plan capabilities, not new wire schemas or a claim that all tools are implemented. The runtime exposes only the permitted subset for the current context.

The originating answer turn has at most **4 attempted model decisions, 6 tool invocations and 20 seconds**. Concurrent dispatches, retries, fallback and Tutor delegation share the same counters/deadline. Neither checkpoint resume nor fallback resets the budget. A later accepted student answer is a new turn; replay is not. Long authorized work becomes a durable job.

Coordinator cannot choose identity, grant access, own mutable session records, commit learning policy, assign mastery, execute arbitrary code or bypass tool authorization. Session service owns navigation/session mutations; Learning service owns validated learning writes.

## Tutor

**Owner:** M4. Uses the approved Groq adapter to explain, adapt teaching to answers, request evidence, propose quizzes, assess against a rubric and select the next teaching step. It does not take over source/session control.

Private lesson state includes lesson identity, target concepts, explanation already delivered, pending question, hints used and next permitted step. Session service owns the active-lesson pointer and session return context. Lesson state remains separate from connection state.

Tutor receives a bounded learning goal, original utterance, evidence references, explanation level, relevant dialogue and assessment summaries. It resolves evidence through services; an agent-supplied body under an evidence ID is not trusted. Its permitted plan tools are `search`, `describe_visual`, `get_student_concepts`, `get_concept_prerequisites`, `generate_quiz` and the proposal-only `update_student_knowledge` capability.

Tutor proposes learning events; Learning service verifies authorization, question/attempt identity, final answer, rubric and replay safety before committing. Questions are persisted before delivery. Public question/hint/feedback content is separated from private answers and grading material. Generation of a question or mention of a concept is not independent assessment evidence.

Tutor cannot directly assign mastery, invent concepts/prerequisites, set probabilistic confidence as learning truth, mutate reading position, grant source access or write directly to PostgreSQL/Neo4j. Initial derived labels are only `not_assessed`, `needs_review`, `developing`, `demonstrated_recently`. Exact derivation thresholds, review intervals and sufficient-grounding criteria are **Pending approved contract/policy decision.**

## Handoff and application authority

Both directions conform to [Coordinator → Tutor](../../shared/contracts/agent/v1/coordinator_to_tutor.schema.json) and [Tutor → Coordinator](../../shared/contracts/agent/v1/tutor_to_coordinator.schema.json). Validate at both boundaries; preserve correlation, lesson identity, evidence versions and the originating deadline. Use references/IDs and bounded dialogue, not entire source documents or histories.

Tutor returns public segments, evidence IDs, pending-question reference where applicable and proposed learning events. A short decision summary is not private chain of thought. There is no unrestricted agent chat, copied system-prompt exchange or private reasoning exchange. Handoff modes (`explain`, `continue_lesson`, `check_understanding`, `evaluate_answer`) are defined by the handoff contract and remain distinct from the session interaction-mode vocabulary (`idle`, `reading`, `tutor_lesson`, `quiz`; approved 2026-09-12, see [data ownership](data-ownership.md)) — a handoff mode describes what the Tutor was asked to do for one turn, not the session's persisted learning-flow state.

Application code owns authorization, authoritative writes, cancellation, quota and version enforcement. Agents receive bounded services/tools, never raw database connections, credentials or unrestricted executors. Model-written SQL is neither generated for execution nor executed. Provider automatic tool execution must not bypass this boundary. Retrieved prompt injection remains untrusted content regardless of which agent passes it onward.

## Deterministic commands

`stop`, `pause`, `continue`, `next`, `previous`, `repeat`, `where am I`, `back to reading`, `undo jump` and `return to question` bypass LLM reasoning. Contract spellings remain defined in shared/contracts. Interim ASR never triggers them; only the accepted final utterance becomes a turn. Local STOP does not wait for ASR, an agent or the server.

See [message flow](message-flow.md) for cancellation, handoff and replay boundaries; [data ownership](data-ownership.md) for authoritative state.
