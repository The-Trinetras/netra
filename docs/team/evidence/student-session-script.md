# P1 — student session script (20 minutes)

For one blind or low-vision student who uses NVDA, with Netra on Windows.
Read the parts in quotes aloud. The facilitator does **not** describe the
screen, touch the keyboard, or suggest a key before a task says so; any help
given is written on the observation sheet as assistance, in the student's
and facilitator's own words.

## Before the student arrives (10 minutes, facilitator only)

- Windows computer with NVDA running at the student's usual settings (or NVDA
  defaults, noted on the sheet), speakers or headphones, a working microphone.
- Netra built from the commit under test (write the commit on the sheet), the
  server reachable, `NETRA_API_ENDPOINT` set, and **no** saved sign-in
  (`cmdkey /delete:Netra:api`), so the session starts at sign-in.
- One unused access code for a test account; that account has the Ohm's law
  source (with its table of current and voltage) ready to study.
- A public YouTube lecture link that allows embedding, noted on the sheet.
- The observation sheet open for this participant code (P01, P02, …).
- Check which parts are live and which are fixture today (Preferences and
  status says so) and note it on the sheet.

## Consent (1 minute)

> "Thank you for helping. We are testing Netra, a study app, not you. It
> takes about 20 minutes. I will ask you to do a few study tasks and I will
> take written notes of what happens and what you say. I will not record
> audio or the screen, and your name will not be written down; you are
> participant P-number. When you speak to Netra, what you say is sent to
> Netra's server to be turned into text, as it would be when you study. You
> can skip any task, ask me to stop, or stop at any time, and nothing
> happens if you do. Is that all right with you?"

Write "yes" or "no" on the sheet. On "no", stop and thank them.

## Tasks (17 minutes)

Time boxes are a guide. When a task's time is up, say "Let's move on", mark
where the student got to, and go to the next task.

1. **Sign in (2 min).**
   > "Netra has just opened. Please sign in with this access code." (Read the
   > code aloud, character by character, twice.)

   Watch: whether NVDA reads the dialog and lands in the access code box;
   whether a mistyped code can be found and fixed; what is said after
   signing in.

2. **Open a source (2 min).**
   > "Open the Ohm's law source for study."

   Watch: finding the Library and the list; whether "ready to study" is
   clear; whether Enter or Open for study works; what is said after it opens.

3. **Ask by voice, then stop Netra (3 min).**
   > "Ask Netra a question about Ohm's law out loud. To speak, hold down F9
   > while you talk, and let go when you finish." (First time only you may
   > say the key; note it as instruction, not assistance.)
   > When Netra starts answering: "Now make Netra stop talking."

   Watch: whether the student knows Netra is listening; whether NVDA speaks
   while they talk; whether what Netra heard is read back; whether Escape (or
   another way) stops speech at once and nothing old starts again.

4. **Explore the table (3 min).**
   > "In this source there is a table of current and voltage. Find it and tell
   > me the voltage when the current is 2 amperes."

   Watch: whether the Study tab and the table are found; whether rows and
   columns can be understood; whether returning to reading keeps the place.
   Note on the sheet if the table shown is fixture data.

5. **Answer a check question (2 min).** Only if Netra offers one.
   > "If Netra asks you a question, answer it any way you like."

   Watch: whether the question is noticed; answering by typing or voice; what
   Netra says back. If no question is offered, write "not offered" (a check
   is asked only when it is supported by the source).

6. **Where am I and back to reading (1 min).**
   > "Find out where you are in the source, then go back to where you were
   > reading."

   Watch: whether the student finds a way (buttons, keys, or F1 for the
   list); whether "where am I" and "back to reading" say the same place.

7. **Play and question a lecture (4 min).**
   > "Open this lecture in Netra and play it." (Paste the link for them into
   > the Lecture tab's link box only if they ask; note it as assistance.)
   > After about a minute: "Ask Netra something about what the lecturer just
   > said." Afterwards: "Carry on watching from where it stopped."

   Watch: whether playing and pausing are found (K, buttons); whether the
   lecture pauses before Netra listens or answers; whether it stays paused
   while Netra speaks; whether continuing starts at the same moment (ask:
   "Did it carry on from the same place?"); whether "Playback" and "Analysis"
   are understood. Note whether the question used the lecture time
   (not possible until C8 lands; write what Netra actually said).

## Debrief (2 minutes)

> "What was easiest? What was hardest or most confusing? Was there a time
> Netra and your screen reader talked over each other? Is there anything you
> expected Netra to do that it did not?"

Write their answers in their own words. Thank them.

## After the session (facilitator)

Complete the observation sheet the same day, then add an entry to
[session-log.md](session-log.md): what happened, what was changed because of
it, and the result of re-testing that change. Do not merge observations from
different students into one "typical" result.
