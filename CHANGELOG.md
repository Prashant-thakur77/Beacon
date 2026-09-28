# Changelog

## v0.6.0 — 28 Sep 2026 (the phone, and the transcript as the authorisation)

Added
- **A phone channel** (`src/beacon/phone/`). A telephone call is bridged into the *same* AssemblyAI Voice Agent socket the console uses — same nine tools, same consent rules. The API takes G.711 µ-law natively (`audio/pcmu`) and a phone line is µ-law already, so the audio is forwarded **untouched in both directions**: no resampling, no transcoding. The bridge holds no IAM role, touches no table and decides nothing; every `tool.call` becomes `POST /tools/<name>` on the voice Lambda, because a new channel must not become a new way around the rules. Write-up: `docs/phone.md`.
- **Two rules the phone adds.** A keypress can acknowledge, re-brief, repeat or hang up; it can never approve, and pressing anything else makes the agent say so — an approval has to be words somebody can be held to. And only the caller's audio is ever streamed to the transcriber, so the transcript the consent check reads cannot contain the agent reading a phrase back to itself.
- **Consent certificates** (`certificate.py`). Every applied change ships one artifact answering *why was this allowed*: the phrase, how clearly it was heard and **whether that number gated the change or only reviewed it**, which allowlisted action it authorised, the parameters that passed a dry run, the CloudWatch checks that had to agree, the CloudTrail entry that caused the fault, and a link to play the words back. Printed into the pull request ahead of the diff. Its digest detects an edited certificate and is **not** a signature.
- **Word-level confidence on every channel** (`aai.audit_session`, `POST /sessions/<id>/attest`). A live turn carries no confidence — `transcript.user` has text and no number — so a browser or phone approval was applied without one while a Telegram voice note had to clear 85 %. AssemblyAI now re-transcribes its own recording afterwards and scores each phrase by its **weakest word**. A poor score is flagged, not undone: by then the fix is applied and verified, and waiting a minute for a transcript before touching production would be the wrong trade at 3 AM.
- **One voice, not two.** `dual_channel` and `speaker_labels` can be requested together — verified, not assumed — and the speaker comes back as channel-then-speaker (`1A`, `2A`), so a second person on the caller's side arrives as `1B`. A second voice near the approval is flagged rather than treated as a forgery. A browser session cannot be checked this way at all, and the certificate says nothing rather than guessing.
- **One brief for every channel** (`voice_brief.py`, `GET /brief/<id>`), with a test pinning the console's fallback copy to the served text — three channels opening sessions from three copies of one prompt is a drift waiting to happen.
- `make phone-test`: five scripted calls at telephone quality through the real Voice Agent API, each leaving a two-channel WAV and a verdict on which channel the approval came from. `scripts/twilio_probe.py` proves the carrier side without a carrier.
- `tests/test_claims.py`: the README's claims checked against the code — tool count, allowlist size (and that it is stated correctly in the console too), one confidence bar, release links, and that the retired transcoding claim cannot come back.

Fixed
- **The agent ignored its own approval rule.** A real recording caught it hearing *"Approve fix 3"* and answering that the fix needs the spoken phrase. The prompt now says that if the words contain "approve fix" and a number, `approve_fix` must be called before the agent says anything — and that the tool decides whether the phrase counts, not the model.
- **Key terms primed the wrong phrase.** They listed `approve fix one` and `approve fix two` and stopped, while proposals are numbered per incident — so an incident on its eighth attempt asked for *"approve fix eight"* with the transcriber primed for neither.
- **The attestation could not read a split phrase.** A transcriber returned `"Approve"`, `"fix"`, `"one."` as three utterances and the per-utterance search reported that nobody had authorised a change the caller had plainly authorised. It searches each channel's text joined now.
- **"Two allowlisted actions"** in the console FAQ and the README safety list, when the IAM policy grants three. Now test-locked.
- The console reported a version hard-coded in a CloudFormation template, so it announced 0.5.0 while the Lambda beside it — from the same image — announced 0.6.0.

## v0.5.0 — 28 Sep 2026 (the core answers a second fault)

Added
- **A second fault class, end to end.** `diagnose.py` now proposes `ecs.force_redeploy` when a service is short of tasks or its newest deployment failed, and — only when nothing else explains the alarm and exactly one service is remediable — offers a **clearly-labelled last-resort restart**. Security-group drift still outranks both: it names the exact rule that vanished. Until now the action existed but nothing ever proposed it, so any non-drift fault produced an incident with no fix.
- **Honesty about what a restart is.** The degraded brief, the proposal (`is_last_resort`, `reason`), the agent's prompt and the fix card all say plainly when Beacon is offering a remedy rather than a diagnosis. Spoken live: *"Nothing explains the alarm, and this is a restart rather than a fix."*

Fixed
- **The ECS post-condition meant nothing** — "the service is ACTIVE and has a deployment" is true of a service that is still crashlooping. It now requires the new deployment to have finished rolling out, every task running, and the old deployment gone.
- **The verification budget is per action.** A restored rule proves itself in a minute; a replaced task has to start, warm up and then produce clean CloudWatch periods. A live run escalated a restart that had actually worked — the wrong answer in the wrong direction. The state machine now takes its wait and attempt cap from the action (`registry.verify_budget`), and the same wedge fault now resolves on attempt 5 with all three checks genuinely true.

## v0.4.0 — 27–28 Sep 2026 (deeper into AssemblyAI)

Added
- **Tests that speak** (`make voice-test`): six spoken regression scenarios — the exact phrase applies a fix, *"yes, do it"* does not, barge-in withdraws the proposal, Hinglish reaches the same tool, a Sleep Contract needs the read-back then the phrase, and an applied fix can be undone. Each renders the engineer's lines with Polly, streams them into the Voice Agent API as microphone audio, runs every `tool.call` against `make local`, and asserts on what was heard, which tools ran and how the incident ended. Report at `docs/assets/voice-test-report.json`; write-up in `docs/voice-testing.md`.
- **Session summaries** (`aai.py`, `POST /sessions/<id>/summary`): AssemblyAI transcribes its own recording of a voice session and summarises it; the result is cached on the incident and printed in the postmortem as *the night in the engineer's words*. A **Summarise the session** control sits beside **▶ Listen** in the audit.
- **PII redaction on voice notes**: consent is still checked against the words as heard, in memory, but the redacted text is what is written to the approval row, the audit, the postmortem and the pull request.
- Console: phrase chips (questions vs consent phrases, the approve chip matching the incident's latest proposal), a confidence meter on what was heard, and a noisy-room turn-detection preset.

Fixed
- `local/break` is idempotent, so a scenario can start by breaking an already-broken demo.
- Voice-suite assertions are scoped to the scenario, since `make local` reuses an open incident inside its dedup window.

## v0.3.0 — 21–22 Sep 2026 (AssemblyAI Voice Agent phase, branch `assemblyai`)

Added
- Full-duplex voice on the AssemblyAI Voice Agent API: continuous mic, turn detection, barge-in, the nine tools as client-side functions executed on the voice Lambda from the transcript; Hinglish in and out; backend switch (AssemblyAI / AWS cascade); measured latency strip; noisy-room preset.
- `cancel_proposal`: interrupting a read-back withdraws the proposed fix; the refusal explains it.
- Telegram: pages with *Talk · Fix 1 · Ack*, voice notes transcribed by AssemblyAI pre-recorded STT with word-level confidence (min-word gate 85 %), phrase router onto the same tools, Polly voice-note replies, `/status /contracts /report /use`; webhook on the voice Lambda behind a secret and a user allowlist.
- `open_fix_pr`: the pull request that fixes the cause in the CloudFormation template (deterministic patch mapped through logical-id tags) plus the postmortem; body quotes the approval, verify checks and CloudTrail change; resolved incidents only; never merges.
- Attestation on approvals and contracts (backend, confidence, session id, voice-note file id); `GET /recordings/<id>` and **▶ Listen** in the audit; postmortems and the morning report cite recordings and pull requests.
- Degraded triage when Bedrock is unavailable (deterministic sources, `model_unavailable` event); change ledger ranks changes touching the broken resources first.
- Harnesses: `scripts/dev/assemblyai_loop.py` (browser, `!interrupt`) and `scripts/dev/assemblyai_audio.py` (Polly speech through `input.audio`, `!drop`).

Changed
- License: MIT.

## Unreleased — 20 Sep 2026 evening sprint

- Console: Wispr-Flow-inspired editorial theme and motion, landing page, Analytics, Safety Controls/Proof, mobile nav, deep links, filters, keyboard shortcuts, 404/meta, archived-night on empty deployments.
- Postmortem generator (`GET /incidents/{id}/postmortem`, `#postmortem/<id>`), audit log (`GET /audit`, `#audit`, CSV/JSON), morning report (`GET /report/latest`, `#report`, 07:00 IST SNS email).
- HTTPS console via a Function URL proxy while CloudFront is unavailable; redactor no longer eats numeric UUID segments; triage image import-graph test.

## v0.2.0 — 20 Sep 2026 (First Commit submission)

Added
- Triage on Bedrock: Cordon + Nova 2 Multimodal Embeddings log reduction, Nova 2 Lite root-cause analysis, golden-snapshot security-group drift check, CloudTrail → EventBridge change ledger.
- Verified remediation loop on Step Functions (DryRun → RequireApproval → Execute → Wait → Verify ×6 → Resolve/Escalate) with a registry of two allowlisted actions, EC2 DryRun under the write-only remediator role, exactly-once execution and three-part verification.
- Approvals checked against the raw transcript; Sleep Contracts (read-back, exact grant phrase, resource scope, TTL, use counter); the second incident handled with nobody woken.
- Strands voice agent on Nova 2 Lite with six tools; Transcribe streaming (STS-scoped) and Polly with speech marks; litellm fallback engine.
- Console: Night Board, Talk, Analytics (`GET /analytics`), Contracts, Safety (Controls/Proof), landing page; `?night=1` scripted night; replay mode.
- Three CloudFormation stacks + demo workload (Fargate + RDS), git-SHA image tags with a deploy guard, `make local` against moto, 250 tests including template safety/ops tests.
- Production hardening: fail-closed passcode, bounded AWS clients, named JSON log groups, error/escalation alarms, PITR, CSP and security headers, Secrets Manager for the demo DB password, pinned image dependencies.
- New-account fixes: conditional reserved concurrency, Function URL `InvokeFunction` permission, S3-website mode and an HTTPS Lambda-URL proxy while CloudFront is unavailable.

Known limitations
- Live Bedrock calls and CloudFront depend on AWS clearing the account's new-account verification.
