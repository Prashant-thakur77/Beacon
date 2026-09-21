# AssemblyAI phase — the detailed plan (21 → 30 Sep 2026)

Deadline **Tue 30 Sep, 20:30 IST**. Five flat winners; judged on *Application of Technology, Presentation, Business Value, Originality*. The field already has four "voice SRE commander" entries and two "arguments must trace to what the user said" entries, so the story cannot be "voice + ops". It has to be the thing none of them have: **a night that ends with the fault fixed for good, and the proof of who said what.**

## 0. What the user actually goes through (and where each feature comes from)

Walk one real night for a solo backend engineer in Bengaluru on call for US traffic:

| Time | What happens today | What Beacon does after this plan | Feature |
|---|---|---|---|
| 03:12 | Phone buzzes: a CloudWatch email, or PagerDuty. They read it half-asleep on a phone, no laptop. | A **Telegram message** with the one-line cause, three buttons (*Talk · Fix 1 · Snooze 15*) and a link that opens the console on the incident. | Telegram (§2) |
| 03:13 | They open a laptop, hunt through logs, guess at what changed. | They tap *Talk* or send a **voice note in Hinglish**; Beacon answers in a voice note: cause, the change behind it, the one safe fix. | Telegram voice notes on AssemblyAI STT (§2) |
| 03:15 | They run a console command from memory, hoping it's the right one. | They say **"approve fix 1"** — in the browser (live voice via the Voice Agent API) or in a Telegram voice note — and the loop applies and verifies it. | already built; recorded consent (§4) |
| 03:20 | Alarm clears. They go back to bed, promising to "fix it properly tomorrow". Tomorrow never comes. | Beacon says: *the runtime fix is in; the durable fix is a pull request.* It opens **PR #41** on the infrastructure repo restoring the rule in the template and adds the postmortem. Nobody merges anything at 3 AM. | GitHub fix-at-source (§3) |
| 03:21 | — | "Handle this yourself next time?" — **"grant contract for seven days."** | already built |
| 07:00 | They try to remember what they approved. | The **morning report** lists the night, the approval quotes, the PR link, and the AssemblyAI **session recording** for each consent. | recordings in audit (§4) |
| Next week | The same fault, from the same bad deploy. | Under the contract, fixed and verified while they sleep; the PR is still open with a reminder. | already built + §3 |

Every feature below is one of these rows. Nothing is added for the sake of a tag list.

## 1. Stage 1 — Voice that feels finished (Mon 22 → Tue 23)

Goal: the live browser experience on the Voice Agent API is smooth enough to film, and it is deployed on the account (this path needs no Bedrock, which turns the account hold into a non-issue for the demo).

1. **Read-backs written for the ear.** The contract read-back currently speaks resource ids (30 s). Server returns `read_back_spoken` ("the R D S security group's port 5432 rule, seven days, three uses") alongside the exact `read_back`; the prompt speaks the short one, the screen shows the exact one. Same for blast radius.
2. **Barge-in that means something.** Speaking over the read-back yields `reply.done {interrupted}`; the console marks the reply *interrupted* and, if a proposal was pending, calls a new `cancel_proposal` tool — the interrupted read-back withdraws the fix. Test with a real mic in a headed browser; the drop-safety test stays automated.
3. **Hinglish end to end.** `language_codes: ["en","hi"]` (already), keyterms per incident (already), the grant phrase in Hinglish (`saat din ke liye contract do` — server already accepts it), the agent answers Hinglish with Hinglish (prompt already). Verify with recorded Hindi audio pushed through `input.audio` (a WAV → PCM24k script), since headless Chromium has no mic.
4. **Voice isolation and noise.** `voice_focus` on, tuned once in a noisy room; keyterms include the alarm name and both phrases.
5. **Deploy it.** `make set-assemblyai-key` (SSM SecureString) and `make deploy-console VOICE_BACKEND=assemblyai`; the HTTPS Lambda-URL console already exists. Console shows a *backend* toggle (AssemblyAI / AWS cascade) for the side-by-side.
6. **Latency overlay** in the Talk panel: time to first audio, tool round trip, end-to-end per turn, measured live (numbers for the deck).

Done when: a judge with the passcode can open the live URL, press Connect, and run the whole night by voice with nothing scripted.

**Status 21 Sep:** 1.1 spoken read-backs ✔ · 1.2 `cancel_proposal` + barge-in wiring ✔ (real-mic take still to film) · 1.3 Hinglish by audio ✔ (`scripts/dev/assemblyai_audio.py`) · 1.4 `voice_focus` pending a noisy-room test · 1.5 deployed, backend switch ✔ · 1.6 latency strip ✔. Results in `docs/assemblyai.md` § Stage 1 results.

## 2. Stage 2 — Telegram: the page where the engineer actually is (Wed 24 → Thu 25)

Why Telegram: it is on every Indian engineer's phone, voice notes are the native way people talk to each other at night, bots are free, and it has inline buttons. WhatsApp Business is the same idea with a longer approval path; Telegram first, WhatsApp as a documented follow-up.

Mechanics (all serverless, one new Lambda Function URL as the bot webhook):
1. **Page.** When triage stores an incident, `channels.send("page")` also posts to the engineer's chat: one-line cause, alarm, three inline buttons — *Talk* (deep link to `#board/<id>`), *Fix 1* (sends the proposal read-back as a message, never applies), *Snooze 15*. Escalations and "not woken" contract runs post too.
2. **Voice notes in.** The engineer replies with a voice note. The bot downloads the OGG/Opus file, sends it to **AssemblyAI pre-recorded STT** (Universal-3.5 Pro, `language_detection` or `language_codes: [en, hi]`, keyterms from the incident) and gets a transcript **with word-level confidence** (pre-recorded transcripts have it, unlike the realtime agent) — so the confidence gate exists here after all.
3. **Same tools, same consent.** The transcript goes to `POST /tools/<name>` exactly like the browser: `approve fix 1` in a voice note is checked against the transcript the STT produced, and the audio file's id is stored on the approval record. Low-confidence phrase → the bot replies "I heard *approve fix one* at 71% — send it once more, or type it."
4. **Voice notes out.** Beacon's reply goes back as text plus a voice note (Polly today; AssemblyAI TTS through the Voice Agent API is real-time only, so the async path uses Polly — say so in the writeup).
5. **Typed fallback.** Any text message is a turn too. `/status`, `/contracts`, `/report` commands map to the dashboard API.
6. **Identity.** One bot, an allowlist of Telegram user ids per deployment (`TELEGRAM_ALLOWED_IDS`, NoEcho); the passcode is never typed into Telegram.

Done when: a voice note saying "approve fix one" in Hinglish applies a fix on the local demo, and the audit row shows the transcript, the confidence and the file id.

**Status 21 Sep:** built and tested (`telegram.py`, `telegram_bot.py`, route `POST /telegram/webhook`, templates, `make set-telegram-token` / `set-telegram-webhook`, `docs/telegram.md`); *Snooze 15* became *Ack* (records `acknowledged`, no re-page) because a snooze that does not re-page would lie. Deployment waits on a bot token + user id.

## 3. Stage 3 — Fix at the source: the pull request (Thu 25 → Sat 27)

The runtime fix (restore the rule) is a patch. The cause was a change; the durable fix lives in the infrastructure code. This is the feature the other entries do not have, and it is the "business value" row: no more 3 AM fixes that evaporate.

1. **`open_fix_pr` tool** (allowlisted, its own consent phrase: *"open the pull request"*). It never merges. It:
   - clones the configured repo (a GitHub App installation token, `GITHUB_APP_*` NoEcho, scoped to one repo, `contents:write` + `pull_requests:write` only),
   - applies a **deterministic patch** for the action that ran — for `sg.restore_ingress` on a CloudFormation template that is the demo's own `demo/demo-infra-template.yaml`: add the `AWS::EC2::SecurityGroupIngress` resource with the exact params from the golden snapshot; for `ecs.force_redeploy`: no code change, so the PR carries only the postmortem,
   - writes `docs/incidents/<date>-<alarm>.md` (the postmortem the API already generates),
   - opens a PR titled *"Restore RDS ingress from ECS (Beacon incident …)"* whose body quotes the approval ("approve fix 1", channel, time, AssemblyAI session id), the verify checks, and the blast radius,
   - posts the PR link back to the console, Telegram and the morning report.
2. **Where the model is and is not.** The patch is produced by code (the params are the same ones that passed the dry run). The model only decides *whether to call the tool* — and only after the phrase. If you want the LLM Gateway anywhere, it is here, optionally, to write the PR description prose from the postmortem facts, clearly labelled.
3. **Reverting the bad change** (stretch): when the change ledger shows the revoke came from a specific CloudTrail principal and the repo has a commit that removed the rule in the same window, the PR references it and proposes a revert instead of an addition.
4. **Demo repo.** Point it at a fork of Beacon itself so the demo PR is real and visible.

Done when: "open the pull request" in a voice turn produces a green-CI PR on GitHub within 30 s, and the audit shows the phrase that opened it.

**Status 21 Sep:** built (`fix_pr.py`, `open_fix_pr` consent tool, `pr_opened` timeline event + PR link on the card, phrase routes in Telegram and the local agent, `make set-fix-pr-token`, `docs/fix-at-source.md`). First real PR opened by voice against `Prashant-thakur77/beacon-demo-infra` from `make local` (postmortem-only there, since moto security groups carry no CloudFormation tags); the live-account run produces the template patch.

## 4. Stage 4 — Proof: recordings, replay, tests (Sat 27 → Sun 28)

1. **Session recordings in the audit.** The Voice Agent API stores each session's recording and timeline. Store the `session_id` on every approval and contract made in a live voice session; the audit row gets a *listen* link (fetched through our Lambda with the key, never exposed) and the postmortem cites it. The transcript is the safety artifact — now it is an audio artifact too.
2. **AssemblyAI webhooks** (session started/ended) → a timeline event per incident, so the morning report can say "one voice session, 4 min 12 s".
3. **Drop-safety** stays a real test: kill the socket after `approve fix 1` is spoken but before `tool.result`; assert nothing executed and the approval record does not exist. Plus `session.resume` with the resume token: reconnect and continue.
4. **Bluejay simulations** against the deployed agent: a scripted caller says the whole night, including a mumbled approval and an interruption; the run's transcript becomes a regression fixture. This is "tests for the voice agent" — nobody else will have it.
5. **Replay bundle** re-exported from a real AssemblyAI night, so the archived night on the live site is the real thing.

## 5. Stage 5 — Presentation (Sun 28 → Mon 29)

- **Film (3 min)**: same pipeline as the first film, new scenes: the Telegram voice note, barge-in withdrawing a fix, the PR appearing on GitHub, the recording link in the audit. Narration by Chatterbox again; product shots from a real headed browser session with a real mic (the human records that one take).
- **Deck (10 slides)** from `assemblyai-deck.md`, updated with Telegram, the PR, and the latency numbers.
- **Cover image** from the Night Board mid-approval; **README** section "Built on AssemblyAI" listing exactly which API does what (Voice Agent API for the live console, pre-recorded STT for Telegram voice notes, session recordings and webhooks for the audit, Bluejay for tests).
- **Submit Mon 29 evening**; Tue 30 is buffer only.

## 6. How this scores

| Criterion | What the judges see |
|---|---|
| Application of technology | Voice Agent API (tools, turn detection, resume, recordings, webhooks) + pre-recorded STT with confidence for voice notes + Bluejay — used where each fits, not everywhere. |
| Presentation | One night, told twice: the film and the live URL; the deck's numbers are measured. |
| Business value | Minutes to recovery, humans not woken, and a PR that stops the repeat; the morning report a manager can read. |
| Originality | Consent from the transcript with the recording as proof; barge-in that withdraws a fix; Sleep Contracts; the pull request as the end of the incident, not the alarm clearing. |

## 7. Risks and cuts

| Risk | Plan |
|---|---|
| AssemblyAI managed LLM ignores a rule (asks permission instead of calling a tool) | Few-shot lines in the prompt fixed it once; keep a prompt regression run (`scripts/dev/assemblyai_loop.py`) in CI-lite before each deploy. Fallback: "Connect your own LLM" to an OpenAI-compatible endpoint if it regresses. |
| Telegram file download / OGG decoding in Lambda | Send the file URL straight to AssemblyAI (it accepts URLs); no decoding on our side. |
| GitHub App setup time | Start with a fine-grained PAT on the demo fork; App later if time allows. |
| Time | Stage order is value order; if Stage 4 slips, ship recordings-in-audit only and drop Bluejay. Stage 3 is not optional — it is the pitch. |
| Account still under Bedrock hold | The AssemblyAI path does not need it; triage RCA for the live demo comes from the deterministic diagnostics (the brief already reads well without the model). |

## 8. Daily checklist

- Mon 22: §1.1–1.3, deploy with `VOICE_BACKEND=assemblyai`, latency overlay.
- Tue 23: §1.2 real-mic barge-in, `cancel_proposal`, Hinglish audio test, backend toggle.
- Wed 24: Telegram bot Lambda, page with buttons, deep links.
- Thu 25: voice notes in/out with pre-recorded STT and confidence; `/commands`; `open_fix_pr` skeleton.
- Fri 26: `open_fix_pr` on the demo fork, postmortem in the PR, links everywhere.
- Sat 27: recordings in the audit, webhooks, drop-safety + resume, replay export.
- Sun 28: Bluejay run, film shoot + assembly, deck.
- Mon 29: cover, README, submission; buffer for fixes.
