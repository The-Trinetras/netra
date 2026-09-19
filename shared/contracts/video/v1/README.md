# C8 video time and readiness — review draft

20 September 2026. Drafted by Arun (M5-VIDEO); **Arshad reviews the session
fields** and the protocol additions below. Nothing here is mounted, added to
`protocol/v1` or consumed by the desktop yet. Executable mirror:
`api/src/netra_api/multimedia/video/wire.py`.

Three facts stay separate, per selected video (current scope, "Video and
accessibility gates"; `multimedia/video/readiness.py`):

1. **Actual player time** — where the student's player stood when paused.
2. **Playback readiness** — can the student play it in Netra.
3. **Analysis readiness** — does Netra hold evidence for it, and can that
   evidence support visual claims.

No payload has an aggregate "ready" flag, and none infers one fact from another.

## Shapes

| Schema | Direction | Carries |
|---|---|---|
| `VideoRef` (inside the others) | both | `video_id`, `source_version_id`: Netra's canonical `VideoAsset` identity, never a provider asset id |
| `paused_player_time.schema.json` | client → server | the player's own paused position (`position_ms`, optional `duration_ms`) |
| `active_video.schema.json` | server → client | the video being studied and the last recorded `paused_at_ms` |
| `video_readiness.schema.json` | server → client | `playback` and `analysis` verdicts, apart, plus the server's `student_summary` sentence |
| `video_moment.schema.json` | server → client | a time range an answer relies on (for F9) |

`position_ms` is read from the player after the pause took effect (IFrame API
`getCurrentTime()` × 1000, rounded down, or the local media player's position).
It is never elapsed wall-clock time and never where Netra last spoke; it is the
`captured_player_time_ms` that `timestamps.window_around` requires.

## Proposed additions (Arshad decides)

| Where | Addition | Semantics |
|---|---|---|
| `protocol/v1` `turn.submit` | optional `video_context: PausedPlayerTime` | Present when the question was asked about a paused video. The server authorizes the `VideoRef` like any other id and resolves evidence around `position_ms`. Absent means "not about a video moment". |
| `protocol/v1` `session.snapshot` | optional `active_video: ActiveVideo` (null when none) | Session service records the video and `paused_at_ms` when it commits a turn that carried `video_context`, under the normal version rules. The client cues the player there after a reconnect or restart. Reference-only, like the rest of the snapshot. |
| HTTP | `GET /v1/sessions/{session_id}/videos/{video_id}/readiness` → `VideoReadiness` | Bearer credential and account–session binding first; unknown or foreign videos fail closed with the existing `error` payload. Polled, like job status (INT-10c); the client re-polls while analysis is `indexing_in_progress` or `not_indexed`. |
| `protocol/v1` `response.segment` | optional `video_moments: [VideoMoment]` | For F9 lecture answers. Whether a moment rests on an AI description comes from the evidence payload (C6). |

`VideoReadiness.playback` is the server's verdict only (access, media present,
embeddable as far as discovery knows). The player adds what only it can observe
— for YouTube, IFrame errors 101/150 mean `embedding_not_permitted`, 100 means
`media_missing`, 5 means `unsupported_container` — and shows that instead.
Reporting those observations back to the server is **not** proposed.

## Client behaviour this contract supports (F8)

- **Pause first.** A question about the video (typed, push-to-talk or the
  Describe key) pauses the player locally before any network call, then reads
  the actual time. Netra never speaks over the lecture.
- **Pause-and-describe** is an ordinary `turn.submit` in the student's words
  (the Describe key submits "Describe what is on screen now.") with
  `video_context`. No new intent vocabulary. Any AI description is labelled as
  such (decision M1-M3-V).
- **Stays paused.** The video stays paused while Netra answers and after it
  finishes. STOP silences Netra; it does not resume the video.
- **Exact resume.** Only an explicit "continue video" seeks to exactly the
  captured `position_ms` and plays. A second question while still paused reuses
  the same position.
- **Readiness shown apart.** Two separate lines ("Playback: …", "Analysis: …")
  plus the server's `student_summary`, never merged into one "ready".

## Review items

- Arshad: the two protocol v1 additions, the Session service write of
  `active_video` (which turns set it, version semantics, retention), the
  readiness route, and whether `video_moments` belongs on `response.segment`.
- Ashlin (for information): `VideoRef` assumes every selected video, YouTube
  included, gets a `VideoAsset` (`video_id`, `source_version_id`) through the
  selected-video handoff in C7's review. A YouTube id alone is not a Netra video.
- No shared contract, route, session field or database schema changes in this
  item. Local synthetic evidence only.
