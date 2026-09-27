# The last three days — plan to submit (27 → 30 Sep 2026)

Deadline **Tue 30 Sep, 20:30 IST**. The build is done and deployed; what is left is the half of the score that is not code. lablab judges on four axes, and the [how-to-win guide](https://lablab.ai/guide/how-to-win-an-ai-hackathon) is explicit about what each one wants:

| Criterion | What they say wins | Where we stand (27 Sep) |
|---|---|---|
| **Presentation** | *"Judges reward clarity over production value. A 4-minute video that explains the problem, shows the solution working, and articulates the business case."* | **Gap.** The only film is the First Commit cut (2:49), AWS-flavoured, no AssemblyAI, no business case. Script for the new cut is written; narration, capture and assembly are not done. |
| **Business value** | A specific target user, a TAM figure, a revenue model, why it needs AI | **Done on paper** (`business-case.md`), not yet in the film, the deck or the repo front page. |
| **Application of technology** | Deployed URL, repo with real commits across the window, AI doing something genuinely novel | **Strong.** Live console on AWS, 121 commits 18–27 Sep, nine tools on the Voice Agent API. Needs: the live site to be judge-proof at any hour. |
| **Originality** | *"Does this solve the problem in a way that only became possible with this generation of models?"* | **Strong but under-stated.** Barge-in withdrawing a fix, consent from the transcript, the PR as the end of the incident — none of it is on the repo's front page. |

So: **the code is not the bottleneck; the artefacts are.** Everything below is ordered by score-per-hour.

---

## Phase 0 · Restore the machine (27 Sep, ~40 min, automated)

The sandbox was reset twice; AWS was never touched.

1. `git checkout assemblyai` (121 commits, v0.3.0) ✔
2. Rewrite `.beacon.env` with the sixteen deploy flags ✔
3. `make setup` — uv venv, CPU torch, cordon, `npm ci` (running)
4. Re-vendor the film assets the repo does not carry: Geist woff2 + `three.module.js` into `video/scenes/` — and **commit them this time**, so the next reset costs nothing
5. Verify: `pytest` green, live `/health` = 0.3.0, voice URL mints a token, Telegram webhook still registered

**Done when** `make test` passes and the live console answers on all six routes.

## Phase 1 · Make the live site judge-proof (27 Sep, ~2 h)

A judge opens the URL at an unknown hour, probably without a microphone, possibly on a phone. Today they might land on an empty board or a half-finished incident.

1. **Clean the board.** `make fix-demo` on the incident left open from the capture attempt; leave the account showing a *finished* night: resolved incident, the Sleep Contract, the audit row with **▶ Listen**, and the pull request link.
2. **Judge banner.** The top strip already says "Live on AWS · judge passcode in the submission". Make it link to a one-screen *How to judge this in 90 seconds* panel that spells out: press Connect and say *"fix it"*; or press **Run the night** for the scripted replay if you have no mic.
3. **No-microphone path.** Verify `?night=1` (scripted night) and the replay bundle still play end to end on the deployed site, since that is what most judges will actually use.
4. **Phone.** Check the board, Talk panel and audit at 390 px; the film will show a phone, so the site must survive one.
5. **Cost guard.** Confirm the $20 budget alarm and that the demo stack is the only thing costing money (~$0.75/day).

**Done when** a stranger with the URL and the passcode can see the whole story in 90 seconds, with or without a mic.

## Phase 2 · The film (27–29 Sep, the big one)

Target **4:30**, cut exactly to the structure the guide asks for: 0:00–0:30 problem · 0:30–2:30 demo · 2:30–4:00 business case · 4:00–4:30 who and next. Script: `video/script-assemblyai.md` (20 rows, each row one narration file).

1. **Narration** — Chatterbox on the RTX 3050, `SCRIPT=script-assemblyai.md VO=vo-aai`. ~20 lines, a few minutes on GPU. Listen to each; re-roll any line that mangles a number.
2. **Scenes** — six new ones are built and rendering (`problem-stats`, `who`, `market`, `revenue`, `whyai`, `next`) plus the existing `phone`, `title`, `safety`, `close`. Render each to the length of its narration.
3. **Live capture with a real voice** — `video/capture_aai.py` feeds a Polly-synthesised engineer track into Chromium *as the microphone* (`--use-file-for-fake-audio-capture`), so the console hears actual speech, AssemblyAI transcribes it, the tools run and the agent answers — recorded at 1920×1080 with a JSON of DOM milestones for the cut points. One line is placed deliberately on top of the read-back, so **the barge-in in the film is a real interruption**, not an edit.
4. **The agent's own voice** — keep one raw clip of AssemblyAI's TTS answering, unnarrated. For a voice hackathon, the product should be heard at least once.
5. **Telegram** — the one shot only a human can take: a phone screen recording of the real chat with @GoodNightShiftbot, sending a Hinglish voice note and getting the reply. *(If it does not arrive, fall back to a screen capture of Telegram Web driven the same way.)*
6. **GitHub PR** — screen capture of PR #2 on `beacon-demo-infra`: the eleven-line template diff and the body quoting the approval.
7. **Assemble** — `assemble.py`: per-scene clips cut to narration, xfade 0.7 s, ducked music bed (Kevin MacLeod, *Immersed*, CC BY), loudness-normalised. Export 1080p H.264.
8. **Thumbnail + metadata** — cover frame from the Night Board mid-approval; title, description, chapters.

**Done when** a 4:30 MP4 exists whose demo section is genuinely live footage, and the first thirty seconds state the problem with numbers.

## Phase 3 · The deck (29 Sep, ~2 h)

Ten slides, structure per the guide (problem, solution, demo, market, revenue, team, next steps) — the content is already written in `docs/assemblyai-deck.md`.

1. Render each slide from the **same scene engine** as the film, so deck and film are visually one thing: `scenes.html?scene=…` → PNG at 1920×1080.
2. Stitch to a PDF (and a PPTX if the form insists on one).
3. Slide 1 doubles as the submission cover image.

**Done when** `docs/assets/beacon-deck.pdf` exists and every number on it is sourced.

## Phase 4 · The repository as a front page (28–29 Sep, ~3 h)

This is where "Application of Technology" is actually judged, because it is what a technical judge opens first.

1. **README rebuilt for a stranger with four minutes**: hero line, the 90-second judge path, live URL + passcode, one GIF of the barge-in, the *Built on AssemblyAI* table, architecture diagram, the business case in three lines, how to run it locally with no AWS account.
2. **Architecture diagram** — one authored SVG (not just Mermaid) showing the three planes: the voice socket, the safety path, the remediation loop. Rendered to PNG for the README and reused as a deck slide.
3. **GIFs** — barge-in withdrawing the fix; the Telegram voice note; **▶ Listen** playing a recording. Small, looping, under the fold.
4. **Repo furniture** — description, topics (`voice-agents`, `assemblyai`, `aws`, `sre`, `incident-response`), social preview image, pinned release **v0.3.0** carrying the film, the deck PDF and the postmortem sample.
5. **Docs index** — one page that routes: judges → `assemblyai-submission.md`; engineers → `architecture.md`, `safety.md`; the curious → `telegram.md`, `fix-at-source.md`, `business-case.md`.
6. **Versions and deployments, visible** — CHANGELOG entries for v0.1/0.2/0.3, the deploy workflow, and a table of what is deployed where (console, voice, dashboard, Telegram webhook) with its URL and version.

**Done when** someone who has never seen the project can, from the README alone, explain what it does, see it running, and find the test that proves the risky claim.

## Phase 5 · Submit (29 Sep evening; 30 Sep is buffer only)

1. Fill the lablab form from `docs/assemblyai-submission.md` — title, short and long description, business-value field, links, tags.
2. Attach: film (YouTube, unlisted → public), deck PDF, live URL + passcode, repo link on the `assemblyai` branch.
3. Final pass: every link opens in a private window; the live site is in its finished state; the README's first screen matches what the video says.
4. Post-submission: leave the account up until judging ends, then tear down in the documented order.

**Hard stop:** submit on **29 Sep**, treat the 30th as reserve. Anything not done by 29 Sep evening is cut, in this order: PPTX (PDF is enough) → extra GIFs → the Telegram phone shot (fall back to Telegram Web).

---

## What only the human can do

| Item | Why | When |
|---|---|---|
| Phone screen recording of the Telegram thread | A real thumb sending a real voice note is the shot | before 29 Sep |
| One real-mic take in a headed browser (optional) | Proof there is no fake-mic trickery; 20 seconds is enough | before 29 Sep |
| YouTube upload (title/description/thumbnail supplied) | Account access | 29 Sep |
| lablab form submission | Account access | 29 Sep |
| Bedrock support case | Unblocks the model-written RCA; not on the critical path | any time |
