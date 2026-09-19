# Arun's prompt — desktop client and user-facing media

Paste everything below the line into Claude Code (Opus 5), started at the root of
your own clone of https://github.com/The-Trinetras/netra on a Windows machine with
the .NET SDK 10.0.401 and NVDA installed.

---

You are the Claude Code session for Arun. For this completion push Arun owns the
**M5 desktop client** (WPF, accessibility, keyboard and NVDA, microphone,
playback, client protocol), because Sumedhaa is unavailable, plus the
**user-facing M3 pieces** Arun already wrote: YouTube search, MathML and review of
media labels. Arshad (Coordinator, speech server side, tracing, Tutor,
evaluation, the Modal judge) and Ashlin (data, jobs, infrastructure, media
pipeline) work in parallel in their own clones with their own sessions. **Voice
input is the team's top frontend priority**, ahead of further keyboard and NVDA
polish. Build working, tested code; do not stop at mockups or plans.

## Read first, in order

1. `CLAUDE.md`, `AGENTS.md`, `README.md`.
2. `docs/team/prompts/completion/README.md` — the plan, steps and working rules.
3. `docs/team/integration-status.md` — "Decisions made (20 September 2026)" are
   final; do not reopen them. Also "Decisions needed" and "Open items found".
4. `docs/team/integration-checklist.md` (definition of done),
   `docs/architecture/current-scope.md`, `message-flow.md`, `shared/contracts/`.
5. `.claude/rules/client.md`, `.claude/rules/multimedia.md`, `docs/team/M5.md`,
   `docs/team/handoffs/M5.md` (including "Playback and WebView dependency
   choice"), `docs/team/handoffs/M3.md`.
6. `client/src/Netra.Desktop/` and `client/tests/`, especially `Speech/`,
   `Audio/`, `Networking/`, `Library/`, `Study/`, `ViewModels/`, `Views/`; and
   `api/src/netra_api/multimedia/discovery/`, `multimedia/equations/`. Source and
   tests are the truth; if a document disagrees, say so in one line and follow
   the code.

## Ground rules

- Accessibility is correctness: every control reachable by keyboard, with
  meaningful automation names, predictable focus, and no competing speech between
  Netra and NVDA. STOP silences local playback immediately; a cancelled or stale
  generation never resumes. Only an accepted final transcript submits a turn;
  interim text never does. Distinguish sent, played and acknowledged audio.
- No provider keys, AX or Modal credentials, or direct provider calls in the
  client; credentials stay server-side except the device credential in Windows
  Credential Manager. Never log tokens.
- `slice/` is the kit spine: never modify it. `demo/notes/` is the event slice:
  out of scope.
- One branch per item, `arun/<item-id>`, from the latest `main`. Commit when the
  item's tests pass, with a short plain message ending in the Co-Authored-By line
  your harness supplies. Never push, force, reset or discard; Arun pushes.
- Shared contracts need the other affected person's review (usually Arshad): say
  who in your report.
- Tests: `dotnet test client/Netra.sln` (the first restore needs Arun's yes), live
  client tests with `NETRA_LIVE_SERVER_INFO`, and for Python changes
  `python -m pytest -p no:cacheprovider --ignore=tests/test_integration.py`. Every
  behaviour you add gets a test that fails without it; check that by breaking the
  code once and restoring it.
- Never read `.env` or print keys.
- Ask Arun first, and state the cost, before: any NuGet or Python dependency, any
  live provider call, anything in Arshad's or Ashlin's areas.

## Work queue

Work top to bottom. Skip an item that is blocked, say by whom, and take the next.

### Step 1 — contracts (day one, with Arshad and Ashlin)

- Review Arshad's microphone protocol (C1), access-code exchange (C2) and speech
  media type and frame limit (C3) from the client side before they merge.
- Review Ashlin's job-status (C5) and evidence payload (C6) contracts.
- **C7 YouTube search:** the discovery request and numbered result shape
  (M5-DISCOVERY) served by the existing Tavily adapter. Arshad and Ashlin review.
- **C8 Video time and readiness** (M5-VIDEO): actual player time, playback
  readiness and analysis readiness as separate facts, pause-and-describe and
  resume. Arshad reviews the session fields.

### Step 2 — foundations

- **F1 NuGet lock** (M5-LOCK): `packages.lock.json` with locked restore; pin
  WebView2 at review time (approved, free).
- **F2 Sign-in screen** (D-CRED): first-run access-code entry, exchange on C2,
  device credential stored in Windows Credential Manager through the existing
  `WindowsCredentialManagerSource`, clear spoken errors, no token in logs.
- **F3 Voice capture** (D-MIC), top priority: capture with the Windows WinMM
  `waveIn` API (no NuGet) in `Speech/MicrophoneCapture.cs`; push-to-talk sends C1
  frames; press-to-interrupt and STOP keep working; the UI stops saying voice is
  unavailable only when it really works. Pair with Arshad's Deepgram adapter for
  the end-to-end test.

### Step 3 — the core journey on real services

- **F4 Speech playback:** play real ElevenLabs segments through the existing
  assembler and playback queue; measure start latency per segment; decide on the
  NAudio route (M5-AUDIO-DEP) only if the measurement says the current path is too
  slow.
- **F5 Upload and status:** replace `FixtureSourcePreparationService` with real
  uploads on C5 and polled Processing → Ready / Failed status, announced
  accessibly; cancelling sends nothing.
- **F6 Exploration:** real figure, table and equation exploration from C6 replacing
  the `Study/DetectedObject` fixtures; AI descriptions announced as such; exact
  return position. **MathML** (M3-MATHML-1): agree the fidelity with Arshad, then
  implement `render_mathml` (`multimedia/equations/mathml.py` raises today).

### Step 4 — video

- **F7 YouTube search:** the C7 route on the existing Tavily adapter (YouTube
  domains only) and an accessible numbered results screen replacing the
  `IVideoDiscoveryService` fixture; the selection is kept exactly.
- **F8 Player:** a WebView2 player locked to the YouTube embed origin (no devtools,
  new windows or other sites), controlled through the IFrame API
  (`pause`, `seekTo`, `getCurrentTime`), with keyboard controls in WPF; captures
  the actual time; pause-and-describe keeps the video paused and resumes the exact
  position; playback and analysis readiness shown separately (C8).
- **F9** A lecture question from the client, answered with timestamps from
  Ashlin's pipeline and Arshad's Coordinator tools.

### Step 5 — quality and measurement

- **F10** Hotkey conflicts with NVDA (M5-SHORTCUT, placeholders Ctrl+Alt+N and F9);
  session resume after an app restart with Arshad (D-open-2); the open client
  items D-open-1, D-open-3 and D-open-5.
- **F11** STOP-to-silence on one client clock (M5-AUDIBLE, at least 20 samples),
  and responsiveness with Arshad's tracing on, off and stalled.

### Step 6 — people

- **P1** Prepare a 20-minute session script, a consent line and an observation
  sheet in `docs/team/evidence/` for blind or low-vision students using NVDA,
  keyboard and voice: sign in, open a source, ask by voice, explore a table, answer
  a check, press STOP, return, play and question a lecture.
- **P2** After each real session Arun runs, record only what happened, what you
  changed and the before/after. Never invent results; if nobody has tested, the
  record says so.
- **P3** Review the synthetic media labels against the real media (M3-LBL-1) with
  Arun.
- **E1** With Arshad and Ashlin, run the integration checklist on the deployed
  build.

## After each item, report

Item id; branch and commit; files changed; exact commands and results; what you
broke to prove the tests work; what ran on fixtures, a real local server, live
providers or with a person; decisions or blockers and who owns them;
`git diff --stat`. Update the matching row in `docs/team/integration-status.md` and
`docs/team/handoffs/M5.md`. Then continue with the next unblocked item unless Arun
says stop. Never go past a step that needs Arun's yes without asking.
