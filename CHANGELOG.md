# Changelog

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
