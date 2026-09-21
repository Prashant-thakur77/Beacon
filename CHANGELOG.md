# Changelog

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
