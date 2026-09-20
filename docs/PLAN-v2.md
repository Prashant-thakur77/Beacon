# PLAN v2 — making Beacon better for both hackathons (written Fri 18 Sep, 23:50 IST)

`docs/PLAN.md` got us to code-complete. This plan is about **score per hour** from here to Sunday 20:00 (First Commit) and to 30 Sep (AssemblyAI). Two constraints decide everything: the human's AWS time is the scarcest resource (nothing is deployed yet), and every improvement has to survive a live video take.

Ranking rule: judge criterion it moves × probability it works on the first take ÷ human hours it needs. Sandbox hours are cheap; human hours are not.

---

## 0. The reality check first: the minimum Ship It path

If deploying starts Saturday afternoon instead of Friday night, this is the shortest path that still wins Ship It rather than Build It:

```
make deploy-demo (12 min, unattended)   make setup-image + setup-agent-image (parallel, ~30 min)
make deploy-remediation (3 min)         make deploy INCIDENTS_ENABLED=true (2 min)
make snapshot-sg && make dry-run        make set-passcode && make deploy-console (10 min, unattended)
make demo-reset && make break-demo      → cycle 1 in the browser (typed input is fine)
```
~75 minutes of wall-clock, ~25 of attention. Cycle 2 (the Sleep Contract) can be recorded from `make local` or replay with an honest caption if the real one runs out of time. **Record cycle 1 on real AWS no matter what; it is the hard gate.**

`make preflight` (below) is the one command to run before recording.

---

## 1. First Commit: ranked improvements

| # | Improvement | Moves | Owner / effort | Why it scores |
|---|---|---|---|---|
| 1 | **`make preflight`**: one command that checks creds, model access, CloudTrail logging, all four stacks `*_COMPLETE`, image tags current, `dry-run` PASSED, `apply-on`, Lambdas warm, console URL + both Function URLs healthy, alarm OK. Prints a green/red table. | Execution, Demo | sandbox 1 h · human 0 | Sunday morning has no slack; a red row at 09:05 beats a dead take at 10:40. |
| 2 | **Cost and sleep on the tally**: `₹ per incident` from Bedrock token usage (Strands `result.metrics` → incident `usage` → dashboard `/tally`), and **"sleep protected: N h"** = night-hours incidents handled under contract. IST timestamps on the board. | Idea & Impact, Best UI | sandbox 2 h · human 0 | Judges remember one number. "Cents per incident, zero humans woken" is the pitch in two tiles. The India angle stops being a sentence and becomes a metric. |
| 3 | **Recovery sparkline** on the fix card: `ErrorCount` datapoints from the alarm's metric via a new `GET /incidents/{id}/metric`, drawn to zero with the execute time marked. | Best UI, Execution | sandbox 2 h · human 0 | The one visual that proves "verified" without reading the timeline. Also the strongest 3-second video shot. |
| 4 | **Observability that shows on camera**: Powertools `Tracer` on voice-turn and remediate (subsegment per tool and per Bedrock/Polly call), `Metrics` EMF (`TurnLatencyMs`, `BedrockTokens`, `RemediationSeconds`, `VerifyAttempts`, `HumansWoken`), and `make dashboard` that creates a CloudWatch dashboard from those metrics. | Built on AWS, Execution | sandbox 2 h · human 5 min | AWS judges weight technical implementation ~50%; an X-Ray trace map and an EMF dashboard are two shots that prove the calls are real. |
| 5 | **`docs/LEARNINGS.md`**: a timestamped log kept during the build (what broke, what we changed, mentor quotes from the workshop). The last 15 s of the video and the "Learning" criterion read from it. | Learning | sandbox 30 min + human notes Saturday | This criterion is scored and almost every team hand-waves it. Dated, specific entries win it. |
| 6 | **Judge mode on the live URL**: with no passcode the board shows a "How to judge this in 90 seconds" card (what to click, what to say, the replay button, the Safety tab) and reads are fully public. | Ship It, Demo | sandbox 1 h · human 0 | A judge who opens the URL two weeks later with no context still sees the product working. |
| 7 | **Hinglish on the AWS path** (`?lang=hi-IN`): Transcribe `hi-IN`, Kajal already speaks Hindi, the system prompt already code-switches, the grant phrase already accepts `saat din ke liye contract do`. One toggle, one line in the video. | Idea & Impact, Originality (both hackathons) | sandbox 30 min · human 5 min to test | Concrete India differentiator no other team will have on screen. Keep the *approval* phrase English (exact-match safety) and let the conversation be Hinglish. |
| 8 | **Real replay bundle** from a real run (`make capture-run` → `scripts/build_replay.py` → `web/public/replay/incident-001.json`). | Ship It, honesty | sandbox 1 h · human runs it once | The archived-run card then shows a genuine incident with real timestamps, which is what the plan promised judges. |
| 9 | **Second allowlisted action demoed**: `make break-demo-deploy` (force a bad task definition) → `ecs.force_redeploy` proposed and verified. | Execution, Originality | sandbox 2 h · human 30 min + a retake | Proves the allowlist generalises beyond one scripted fix. Only if cycle 1 and 2 are green by Saturday 18:30; otherwise keep it in the writeup as "tested with moto". |
| 10 | **Bedrock AgentCore Memory for contract history** (ask the mentors Saturday 11:00). | Built on AWS (fashion) | human decision · sandbox 3 h | Only with a mentor's yes and a doc link; otherwise it is roadmap text. |

Cut list (do not spend Saturday on these): light theme, mobile tabs, Amazon Connect phone path, Nova 2 Sonic bidi, Slack, a rollback button, multi-incident heatmap.

---

## 2. India build: what "for India" concretely means here

- **The pitch**: 3 AM IST is peak traffic for US customers; Indian on-call engineers are the ones this page hits. Say it in the first 12 seconds and show IST on the clock.
- **Hinglish** in the conversation (item 7), English for the exact consent phrases (safety).
- **₹ on the board** (item 2): cost per incident in rupees, cost of the demo weekend in the writeup (~₹400 of the ₹8,000 credits).
- **Small-team framing**: no SRE team, no PagerDuty budget, one person and a browser. The Sleep Contract is the feature that scales a team of one.
- **Blog on AWS Builder Center** (already drafted) with the India angle in the intro; publish v1 before submitting, iterate after.

---

## 3. AssemblyAI (21–30 Sep): what makes it a top-5 entry, not a re-skin

Everything in `docs/assemblyai.md` stands. The three additions that convert "same product, different voice" into an *Application of Technology* score:

1. **Show both paths on one incident, live** (`?voice=aws` vs `?voice=assemblyai`) with the latency strip visible: STT ms / agent ms / TTS ms. Judges from AssemblyAI want to see what Universal-3 Pro + managed turn-taking changed, not just that it worked.
2. **Behaviours that only a full-duplex agent can do, each tied to safety**: barge-in during the read-back withdraws the proposal; a mumbled approval (confidence < 0.85) is refused and re-asked; the agent speaks first when CloudWatch clears the alarm; drop the socket mid-approval and nothing executes. Each has a test and a 10-second clip.
3. **Keyterms per incident**: resource ids, alarm name and the consent phrases fed to Universal-3 Pro in `session.update`, with an on-screen "heard: approve fix one (0.97)" — the transcript as a safety artifact, made visible.

Plus the deliverables that hackathon requires and First Commit did not: a 10-slide deck and a cover image (item 8's real screenshot).

---

## 4. Schedule fit (remaining windows)

| Window | Sandbox (agent) | Human |
|---|---|---|
| Fri night → Sat 07:30 | items 1, 4, 5, 6, 7, and the `build_replay` script (8) | runbook §0–§2 if awake; sleep |
| Sat 08:00–10:30 | fixes from your paste-backs | runbook §3: images, stacks, `dry-run`, cycle 1 from the CLI, `deploy-console` |
| Sat 11:00–14:00 | items 2, 3 | workshop: ask about AgentCore Memory, Transcribe `hi-IN` streaming, CloudTrail→EventBridge latency, Nova tool-use tips; write `docs/LEARNINGS.md` notes |
| Sat 14:00–19:00 | fixes; item 9 only if green by 18:30 | local mic smoke → live console → cycle 1 and 2 in the browser → insurance footage |
| Sat 20:30–00:00 | docs, replay bundle from `capture-run`, `make dashboard` | two timed rehearsals, scope freeze 23:00 |
| Sun 08:00–13:00 | hotfixes only | `make preflight`, record |
| Sun 13:00–16:30 | — | edit, upload, push, blog v1, **submit by 16:30** |
| Sun 16:30–19:30 | AssemblyAI branch prep | iterate video/writeup, `make apply-off` |

---

## 5. Definition of "better" we will actually check

- Video: every one of the five criteria has a shot a judge can point at (checklist in `docs/demo-script.md`).
- Live URL: opens to a working product with no instructions, in a fresh browser, two weeks later.
- `make preflight` all green before the recording starts.
- `bash scripts/gate.sh` green at every commit.
- `docs/LEARNINGS.md` has at least eight dated entries by Sunday noon.
