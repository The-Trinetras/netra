# Windows verification of the desktop client

**Status: not yet run.** Everything on branches F1 to F10 was built for
Windows and tested on macOS (the portable test project), which is not Windows,
WPF, NVDA, WebView2, microphone or speaker evidence. Run this on a Windows 11
machine and record the outcome in [integration status](../integration-status.md)
and the [M5 handoff](../handoffs/M5.md), with the commit.

## 1. Prepare (once)

- Windows 11, .NET SDK **10.0.401** (`client/global.json`), NVDA, the Microsoft
  Edge WebView2 Runtime (Evergreen; preinstalled on Windows 11), a microphone
  and speakers or headphones.
- Merge or check out the branches in order: `arun/F1-nuget-lock` →
  `arun/F3-voice-capture` → `arun/C8-video-contract` → `arun/F2-sign-in` →
  `arun/F8-lecture-player` → `arun/F10-accessibility` (each contains the ones
  before it; `arun/F10-accessibility` alone has them all).

## 2. Build and automated tests

Run these one at a time from the repository root.

```bash
dotnet restore client/Netra.sln --locked-mode
```

```bash
dotnet build client/Netra.sln --no-restore
```

```bash
dotnet test client/Netra.sln --no-restore
```

The restore downloads the locked NuGet packages on a machine that does not
have them yet; it must not change either `packages.lock.json`.

**What to expect (from the macOS run, not a Windows result):** the portable
run reported 280 test cases (273 passed, 7 skipped). On Windows,
`Netra.Desktop.Tests` runs those same cases against the real WPF assembly
plus 10 WPF view cases, so expect **290 cases: 285 passed, 5 skipped** (3
live-server tests without `NETRA_LIVE_SERVER_INFO`, and the 2 opt-in
credential tests). The portable project is not run on Windows. Any failure,
or a different count, is worth recording exactly.

Optional, writes and deletes one random throw-away entry in your own vault:

```bash
set NETRA_TEST_CREDENTIAL_WRITE=1 && dotnet test client/Netra.sln --no-restore --filter SaveReadAndDeleteRoundTripInTheRealVault
```

With a local server (slice D in integration status), set
`NETRA_LIVE_SERVER_INFO` and run the suite again for the 3 live tests.

## 3. Hand checks with NVDA

Start the app with `NETRA_API_ENDPOINT` set (live) or unset (offline). For
each line, write **pass**, **fail** (what NVDA said) or **not tested**.

### Start-up and sign-in (F2)

- [ ] With no saved sign-in (`cmdkey /delete:Netra:api`), "Sign in to Netra"
      opens by itself; NVDA reads the title and the instruction and lands in
      "Access code, edit".
- [ ] A wrong code is refused with the spoken reason; focus returns to the
      code, selected; the code can be read back and corrected.
- [ ] A good code (needs Arshad's C2 on the server) signs in; `cmdkey /list`
      shows `Netra:api`; restarting Netra does not ask again.
- [ ] Preferences and status: "Sign out of Netra on this computer…" asks
      first (No is the default); after Yes, the conversation and source list
      are empty and `Netra:api` is gone.

### Keyboard and shortcuts (F10)

- [ ] Preferences and status names the activation shortcut Windows granted
      (expected Control+Alt+Shift+N). From another app it brings Netra to
      the front **without** opening the microphone.
- [ ] **Control+Alt+N still starts or restarts NVDA** (Netra no longer takes it).
- [ ] F1 opens the shortcut list on its first item; each item is read in full.
- [ ] Control+1 to Control+5 open the five tabs, and focus lands inside each.
- [ ] Escape, Control+P, Control+Shift+P, Control+Left/Right, Control+R,
      Control+L, Control+B, Control+U and Control+Q each do their command
      from any tab; none clashes with an NVDA command (check with NVDA's
      input help, NVDA+1, off).
- [ ] While typing in the message box, Control+Left/Right still move by word.

### Voice (F3; end to end needs Arshad's C1 and Deepgram)

- [ ] Offline or against today's server, holding F9 says voice input is not
      available (in words), and **no microphone indicator** appears in the
      Windows taskbar before a server accepts a capture.
- [ ] Nothing is spoken by NVDA while F9 is held (captions only on screen).
- [ ] Alt+Tab while holding F9 discards the capture.
- [ ] With C1 on the server: a spoken question becomes one "You (voice)" line
      and "Heard: …" is read; Escape during capture sends nothing.

### Lecture (F8)

- [ ] Paste a public lecture link, press Enter: "Lecture ready: <title>".
      K plays and pauses, J and L move 10 seconds, T reads the time in words.
- [ ] Tab never lands inside the video; NVDA stays on Netra's controls.
- [ ] **Known risk to check:** play a lecture, switch to the Conversation
      tab (Control+4) and back (Control+3). The lecture must keep its place;
      WPF's TabControl takes the hidden tab out of the visual tree, and it is
      not yet shown that WebView2 keeps the page loaded when that happens. If
      the video reloads, record it; the fix is to keep the player in the tree.
- [ ] Clicking the YouTube logo or "Watch on YouTube" opens nothing.
- [ ] Holding F9 or sending a typed question pauses the lecture first; it
      stays paused while Netra answers; C continues from the same moment
      (write the time before and after).
- [ ] A video that disallows embedding says "The video's owner does not allow
      it to be played in other apps." "Playback" and "Analysis" are read as
      two separate lines.
- [ ] Without the WebView2 Runtime (or offline), the Playback line explains
      why, and the rest of Netra works.

### Audio (existing; M5-AUDIBLE, F11)

- [ ] Escape silences Netra's speech at once; old speech never resumes after
      Continue or a reconnect.

## 4. Record

Copy the checklist with your marks into integration status (date, commit,
Windows and NVDA versions, live or fixture per check). Fixes found here go on
new branches; keep the failing observation next to the fix.
