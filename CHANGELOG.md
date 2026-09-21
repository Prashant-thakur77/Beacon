# Changelog

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
