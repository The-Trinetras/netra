# C7 YouTube discovery — review draft

20 September 2026. Drafted by Arun; **Arshad and Ashlin review required**.
Not registered in the API or consumed by the desktop yet. No existing protocol
message, route, session field or database schema changes in this item.

## Proposed HTTP boundary

| Operation | Proposed route | JSON body / success response |
|---|---|---|
| Search | `POST /v1/sessions/{session_id}/youtube/search` | `search_request.schema.json` / `search_response.schema.json` |
| Select | `POST /v1/sessions/{session_id}/youtube/selection` | `selection_request.schema.json` / `selection_response.schema.json` |

Both require the existing Bearer credential and account–session binding before
any provider call or lookup. No credential in a URL/body. Use the existing
`protocol/v1/error.schema.json` for safe failures. Arshad reviews HTTP status
mapping with the existing API client; these routes are proposals, not live URLs.

Each request has a stable `request_id` and `expected_session_version`. Retransmit
the same logical action under the same identity/body; a committed duplicate
replays the same result and must not call Tavily or move the version again.
Conflicting reuse fails; version conflicts return the canonical current version.
Arshad owns serialization, replay storage and session-version mutation policy.

Search invokes the existing `TavilyYouTubeDiscovery` with YouTube domains only.
`query_text` is retained exactly (1–500 characters, not all whitespace);
`max_results` defaults to 5 and is bounded to 20, matching `DiscoveryQuery`.
The adapter's configured raw-result limit can yield fewer results. Empty results
are a successful search. Failure must not silently replace the last completed list.

The response contains an immutable `result_set_id`, UTC-offset-aware
`produced_at`, request correlation and canonical session version. Results retain
the adapter's order, contiguous one-based `result_ordinal` values and unique
11-character `youtube_video_id` identities. Validators must enforce order and
identity uniqueness in addition to JSON Schema's structural checks.
Titles are untrusted accessible display text, never HTML/instructions. Unknown
channel, duration or embeddability remains null. No snippets, raw provider
objects, URLs, analysis claims or evidence IDs are returned. The player constructs
its own allowed embed URL from the validated ID.

Selection submits **only the original list ID and ordinal**, never a replacement
title/video ID. The server resolves against that exact authenticated stored list;
a stale, expired, superseded or foreign list fails through the existing safe error
vocabulary. It never selects the same ordinal from a newer list. Recheck access
and retain the exact selected result, list ID and selection time in the success
response and canonical state. A failed selection must preserve the prior selection.
Search/selection alone never enqueue analysis or assert playback readiness.

## Integration review

- Arshad: approve route names, errors, replay, session version and storage of
  stable discovery lists/selection. Existing `session/result_sets.py` stores
  **zero-based evidence references**, requires a source version and caps at 10;
  it cannot silently store these **one-based YouTube results**, which may be
  searched before opening a source. Keep the existing snapshot reference shape
  until a separately reviewed session change; do not invent evidence IDs.
- Ashlin: review persistence/lifetime and the later selected-video-to-source/job
  handoff. No source or job ID exists merely because Tavily found a video.
- Arun: after review, F7 maps these fields into accessible numbered results;
  a late response cannot replace a newer search, selection uses its original
  `result_set_id`, and refresh preserves exact identity. Channel is not assumed
  to be the lecturer. No client provider credentials or calls.

Executable mirror: `api/src/netra_api/multimedia/discovery/wire.py`. Tests compare
the committed schema structures with that mirror and exercise strict JSON
parsing, stable numbering, field exclusion and adapter-to-public projection.
This is local synthetic evidence only; it proves neither HTTP authorization,
durable replay nor live Tavily behavior. Do not implement F7 against this draft
until affected-owner review is recorded.
