# Task queue (agent)

Ordered by demo risk. Each task is self-contained: file list + definition of done. Tick when `bash scripts/gate.sh` is green and the commit is made. Human deploy steps live in `docs/human-runbook.md`.

## A1 — Friday night (before the human's late deploys)
- [x] A0 git baseline, Makefile image tags + guard, host arch, RDS version param, remediable tags
- [x] `rca.py` parser + triage prompt (CHANGE CORRELATION, BEACON_JSON) + fixture
- [x] store: status / rca_json / timeline / 30 d TTL / append_timeline / update_status / find_open_incident; handler stores incidents without Connect + dedup
- [x] remediation registry (2 actions), `actions_sg`, `actions_ecs`, `remediate.py` dryrun step
- [x] `Dockerfile.agent`, `agent` extra, `scripts/smoke_strands.py`, `make smoke-strands`
- [x] `remediation-template.yaml` v0 + ASL test + `changes.ledger_handler` + make deploy-remediation / snapshot-sg / tag-remediable / dry-run / changes / incidents
- [x] `console-template.yaml` v0 + stub `voice_turn` / `dashboard_api` (health, warm) + make deploy-console / console-config / set-passcode
- [x] runbook §2 (Friday 23:00 deploys), `make capture-run`, `make check-reduction`

## A2 — overnight: the closed loop (must-tier first)
- [x] `approvals.py`: create_proposal / get_proposal (5-min expiry) / create / get / mark_used; explicit table name; tests (moto)
- [x] `remediation/verify.py`: verify_alarm(alarm, after_ts) + verify_metric(namespace, metric, dims) + postcondition -> VerifyResult{ok, checks[]}; tests
- [x] `remediate.py` steps: require_approval, execute (APPLY_ENABLED, executed_at, Powertools idempotency on approval_id), verify (attempts), resolve, escalate, all (inline runner); timeline + status writes; tests
- [x] `remediation/runner.py` run_loop(approval_id) inline fallback; test drives dryrun -> execute -> verify -> resolved with patched checks
- [x] `contracts.py`: put / match(alarm, action, params) with golden-snapshot membership / use (conditional) / revoke / list; tests
- [x] `diagnose.py`: sg_drift(golden) -> MissingRule[]; format_diagnostics(); tests
- [x] `changes.py`: recent(minutes, before=alarm_ts) from ledger (destructive verbs first, remediator tagged) + lookup_events fallback + format lines; tests
- [x] handler: `[diagnostics]` + `[changes]` sources (bypass Cordon), deterministic action override from diagnostics, contract match -> approvals.create(source=contract) -> contracts.use -> sfn.start_execution -> notify(variant=contract); `notifier.notify(variant, link)`; tests
- [x] `tests/test_template_safety.py`: voice role has no EC2/ECS write actions; remediator role has exactly the allowlisted writes with the tag condition + the security-group-rule statement; triage role has PutItem approvals / UpdateItem contracts / states:StartExecution / X-Ray
- [x] make targets: propose, approve, replay-approval, demo-alarm, demo-reset, demo-sleep, demo-rehearse, apply-on / apply-off (3 functions, env merge, .beacon.env), warm

## A3 — overnight: the voice agent + console
- [x] `turn_context.py` (contextvar: incident_id, session_id, transcript, channel, passcode_ok, contract_readback_pending)
- [x] `voice_tools.py`: get_incident_brief, get_evidence(kind), propose_fix (invokes remediate dryrun), approve_fix(fix_id, phrase) transcript-verified, grant_sleep_contract(days, max_uses) read-back gated, check_recovery; TOOL_SCHEMAS; evidence store; tests with FakeAgent
- [x] `voice_turn.py`: POST /session (passcode -> STS creds for MicRole), POST /turn (Strands Agent, streaming=False, Polly mp3 + sentence speech marks, conversation persisted, EMF metrics), tool_only mode; `voice_loop.py` litellm fallback; tests
- [x] `dashboard_api.py`: /incidents, /incidents/{id}, /incidents/{id}/execution, /contracts, DELETE /contracts/{id}, /tally, /safety; redact(); tests
- [x] `prompts/voice_system.txt` rewrite (action-capable, [E#] citations, contract read-back rule, Hinglish-friendly)
- [x] `web/` Vite + React + TS: theme, polling hooks, Night Board (tally, feed, timeline, archived-run card), Talk (typed input, Polly playback, tool chips, fix card, evidence dock), replay loader; `npm run build` green
- [x] Transcribe streaming transport (`pcm-worklet.js`, `transcribe.ts`), Web Speech transport, speech-mark sentence sync
- [x] Contracts + Safety screens, moon badge, sparkline

## A4+ — Saturday
- [x] `make local` (FastAPI + moto + scripted agent) for Build It — found and fixed two status-race bugs
- [x] README rewrite; `docs/safety.md`, `docs/demo-script.md`, `docs/submission.md`, `docs/blog.md`; CI updated
- [ ] fixes from H4/H6 (human deploy feedback) — **needs the human's AWS runs**
- [ ] `tests/fixtures/real/` + refreshed `web/public/replay/incident-001.json` from a real run (`make capture-run`)
- [ ] `docs/assemblyai.md` + branch `assemblyai` scaffold (`web/src/voice/transport.ts` interface, `/tools/<name>` route) — after the First Commit submission
- [ ] cover image `docs/assets/cover.svg`, architecture SVG for the video overlay
