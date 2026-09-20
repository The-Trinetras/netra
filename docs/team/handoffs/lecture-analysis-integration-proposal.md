# Lecture pause-and-describe integration proposal

20 September 2026. Review proposal for the reported Lecture tab failure, not
approval of new contracts or a claim of working video analysis.

## What the current code does

`LectureViewModel.DescribeAsync` pauses the embedded player, records its actual
position, and displays a hardcoded unavailable message. It never sends an
analysis request. `App.xaml.cs` constructs it with only the player controller.
The screenshot therefore reflects the implemented limitation, not a failed
YouTube playback request.

The backend already has `StoredVideoEvidenceService.capability_report` and
`evidence_at_player_time`. They authorize canonical video/evidence identities
and distinguish visual evidence, transcript-only evidence, and missing evidence.
The C8 payloads and `public_readiness` projection exist, but the readiness route,
`turn.submit.video_context`, and `session.snapshot.active_video` are absent from
the active protocol. `shared/contracts/video/v1/README.md` explicitly requires
Arshad's review before route/protocol use. The completion prompts and current
integration register still identify that review; no later C8 approval was found.

Two additional gaps mean mounting the readiness route alone will not fix this:

- Opening a YouTube link produces a client YouTube identity, not an authorized
  Netra `VideoAsset`. The C7 selection draft also returns no canonical `VideoRef`.
- `worker/main.py` registers document handlers, not the existing YouTube media,
  video-indexing and description jobs. New selections cannot currently progress
  through the complete analysis pipeline via production worker composition.

The separate Marengo factory bug is repaired in this change: configured video
search now constructs the existing adapter instead of always passing `None`.
That enables retrieval over already indexed, stored video evidence; it does not
implement the missing desktop/selection/job boundaries.

## Boundaries requiring review

| Owner/reviewer | Proposed concrete boundary | Required decision |
|---|---|---|
| M1/M5, Arshad | Adopt C8's existing `PausedPlayerTime`, `ActiveVideo`, `VideoReadiness`, and `VideoMoment` shapes | Approve the three optional protocol fields and readiness route below; preserve existing request/version/replay rules |
| M1/M2/M3/M5 | Register a selected or pasted YouTube identity as an account-owned source/version/video, returning C8 `VideoRef` | Approve a registration route and typed request/response; neither C7 selection nor C8 currently defines this handoff |
| M1/M2/M3 | Start preparation for that authorized canonical video through the existing durable job queue | Approve the request boundary, video stage/job payloads, and source-version readiness policy; selecting/playing alone must not start paid processing |
| M1/M2 | Persist `active_video` alongside existing session data | Review the migration, update/clear rules, replay behavior and reconnect snapshot; never silently repin the open PDF |

Recommended registration boundary for review:
`POST /v1/sessions/{session_id}/videos` accepts `request_id`,
`expected_session_version`, and an exact validated `youtube_video_id`; returns
the same request identity, canonical session version, and `video: VideoRef`.
Use it for both pasted links and the exact selected C7 result. Registration
authorizes account/session scope, records canonical source identity, and does
not call analysis providers. Repeated request identities replay the same result.

Recommended preparation boundary for review:
`POST /v1/sessions/{session_id}/videos/{video_id}/analysis` accepts `request_id`
and `expected_session_version`, checks explicit analysis intent and the existing
processing gate, and returns a reference through the existing job-status shape.
The client requests this only when the student asks for analysis. Reuse completed
preparation and idempotently resume existing work; do not enqueue duplicates.
These route and payload proposals are not part of the committed contracts yet.

## Implementation after the boundary review

1. M1/M5 adopt C8 without an aggregate readiness flag:
   `GET /v1/sessions/{session_id}/videos/{video_id}/readiness`, authenticated and
   account/session-bound, returns `public_readiness(report, source_version_id=...)`.
   Add optional `video_context: PausedPlayerTime` to `turn.submit`,
   `active_video: ActiveVideo | null` to snapshots, and `video_moments` to response
   segments. An absent context retains the current non-video turn behavior.
2. M2 implements the reviewed canonical registration/preparation boundary and
   source/version lifecycle. M3/M2 wire the existing Tunelio -> private S3 ->
   Twelve Labs adapters, `IndexVideoJob`, `DeriveVideoEvidenceJob`, candidate and
   binding sinks, and lease-aware stage recorder into worker composition. Keep
   remote operation identity, retries and external calls outside DB transactions.
   Marengo 3.0/Pegasus 1.5 and the Tunelio opt-in are already recorded decisions;
   no provider/model/dependency change is proposed.
3. M1 authorizes the `VideoRef`, verifies the exact source version and canonical
   duration, accepts the captured time under ordinary turn idempotency/version
   rules, and calls `evidence_at_player_time`. Include authorized evidence in the
   existing Coordinator path and shared turn budget. Label Pegasus output as an
   AI description; transcript-only data never supports a visual claim. A missing
   moment yields a specific limitation, not an unrelated general explanation.
4. M5 injects a backend lecture service into `LectureViewModel`, obtains the
   canonical `VideoRef`, and polls readiness only while preparation is pending.
   Describe, typed questions and accepted final ASR pause first and capture actual
   player time, then submit the same selected identity/time in `video_context`.
   Ignore responses for a superseded selection. Explain and speak readiness or
   preparation failures through the existing accessible status/speech path.
5. Persist the accepted pause position with normal replay protection. Keep the
   video paused during/after the answer and STOP; only Continue seeks to that
   exact captured time and resumes. Reconnect restores the reviewed snapshot
   reference. Define clearing an active video explicitly without changing the
   separate reading return position.

## Acceptance evidence

Before any live run, offline tests must cover authenticated/foreign video IDs,
source-version mismatch, duplicate registration/preparation/turns, stale selected
video responses, captured positions including zero/end boundaries, transcript-only
and no-evidence outcomes, provider/worker failure, cancelled speech, reconnect,
and exact explicit resume. Test registration through worker candidates and the
real API/client DTO boundary with labeled synthetic media and fake providers.

The current bounded repair is checked with:
`python -m pytest api/tests/multimedia/test_stored_video_service.py api/tests/multimedia/test_video_wiring.py -q`.
The new regression runs the production factory against explicit offline storage
and provider doubles, checks the configured search request, and checks stored
moment evidence remains readable with absent configuration or an unavailable SDK.
Database-backed tests are excluded by the repository's normal `not integration`
selection. Live YouTube/Tunelio/Twelve Labs, private storage, worker deployment,
microphone/speakers and Windows/NVDA still require their own authorized checks.

No contract, migration, dependency, provider configuration or production service
is changed by this proposal. Its approval scope is implementation of the reviewed
boundaries; it does not authorize live provider calls or deployment.
