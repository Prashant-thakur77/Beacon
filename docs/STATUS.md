# Status — Sat 19 Sep 2026, 14:00 IST

## What works (verified in the sandbox)

| Area | Evidence |
|---|---|
| Backend, all three stacks, safety model | 242 tests; `bash scripts/gate.sh` green (ruff, mypy --strict, cfn-lint, pytest, web `tsc`) |
| Production hardening | `tests/test_hardening.py` (fail-closed passcode, limits, bounded clients, recorded execute failures) and `tests/test_template_ops.py` (log groups, alarms, concurrency, PITR, CSP, CORS, Secrets Manager); README "Production notes" |
| The Step Functions loop against the real handler | `tests/test_asl_walk.py` drives the real ASL through `remediate.handler` |
| The whole product with no AWS account | `make local LOCAL_PORT=8765`: both incident cycles driven over HTTP, second one "not woken"; `?night=1` plays the whole night in the browser unattended (verified headless via CDP: 9 turns, 2 incidents, 1 human woken, contract listed) |
| Console (light editorial theme, 20 Sep) | Wispr-Flow-inspired design system in `web/src/theme.css` (cream ground, EB Garamond statements, Figtree body, lavender CTA, coral/amber/deep-green/pink state); screenshots at 1440, 1024 and 400 px of board, analytics, contracts, safety in `~/beacon-shots/ui-*.png`; no horizontal scroll at any width (probed via CDP) |
| Analytics view (`#analytics`) | `GET /analytics` on the dashboard API (`tests/test_dashboard_api.py`: nights, p50/p90 recovery, woken vs contract, cost per incident + cumulative, alarm-to-first-proposal, outcomes, top alarms, contract usage); the console falls back to the same maths in the browser for replay bundles and for a deployed API that predates the route; every chart has an empty state and a skeleton |
| Both container images | `docker build` of `Dockerfile` and `Dockerfile.agent` succeed; imports smoke-tested |
| Voice protocol facts | Nova 2 Sonic, Transcribe, Polly, AssemblyAI Voice Agent event shapes fetched from the docs on 18 Sep |

## What is NOT verified, because it needs AWS or a person

These are not known to be broken — they are unproven. In order of risk:

1. **Nothing has been deployed.** Every CloudFormation template has passed cfn-lint and the safety tests, but none has been created in an account. First deploy is runbook §0–§2; the two go/no-go gates are `make dry-run` and `make smoke-strands`.
2. **Nova 2 Lite tool use through Strands on a real call.** `make smoke-strands` decides; the litellm fallback is an env flip.
3. **Transcribe streaming from the browser** (STS creds, worklet, `en-IN`). Fallbacks: Web Speech, typed. The `?stt=` selector and the `STT_LANGUAGE=en-US` redeploy path exist.
4. **CloudTrail → EventBridge delivery latency** on the change ledger. Measured in runbook §2 step 8; the UI re-polls, the RCA never depends on it.
5. **Fargate task replacement time** for the `ecs.force_redeploy` demo vs the 6×30 s verify window. Optional cycle; escalation is honest.
6. **AssemblyAI token path** (browsers cannot set the `Authorization` header on a WebSocket). `voice_turn._mint_assemblyai_token` is the single function to adjust after the day-1 spike.

## Known limitations (by design, documented)

- Polly `Kajal` neural is assumed available in us-east-1; automatic fallback to `Joanna`.
- The demo image must be rebuilt (`make deploy-demo`) for the wedge flag.
- Amazon Connect and Nova 2 Sonic bidi are not on any critical path.

## Next (sandbox side, in order)

1. ~~AssemblyAI day-1 spike prep~~ done (`scripts/assemblyai_probe.py` on the branch).
2. ~~Slides skeleton~~ done (`docs/assemblyai-deck.md` on the branch; `.pptx` on 25 Sep).
3. **Cover image** from a real Night Board screenshot once a real run exists (`docs/assets/night-board.png` from local mode is the placeholder).
4. Item 10 (AgentCore Memory) only on a mentor's yes.

## Next (your side, in order)

Runbook §0 → §1 (`make dry-run`, `make smoke-strands`) → §2 → Saturday §3 → `make preflight` → record → `make capture-run && make build-replay && make console-config` → push `main` → blog → submit. Paste any error verbatim.
