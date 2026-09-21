# Safety and control

Beacon can change production. This document is the complete list of what stops it from doing the wrong thing, where each control lives, and which test proves it.

## Threat model

An LLM-driven agent with write access can fail in four ways: it acts without consent, it acts on the wrong resource, it acts more than once, or it declares success without proof. Each control below closes one of those.

## Controls

| # | Control | Where | Proven by |
|---|---|---|---|
| 1 | **Allowlist by code.** Only `sg.restore_ingress` and `ecs.force_redeploy` exist. Unknown action ids and params that do not match the schema exactly are rejected before any AWS call. | `src/beacon/remediation/registry.py` | `tests/test_registry.py` |
| 2 | **Allowlist by data.** A security-group restore must match a rule in the golden snapshot written on a healthy stack (SSM `/beacon/<stack>/golden-sg`). Beacon cannot invent a rule. | `actions_sg.precondition`, `make snapshot-sg` | `tests/test_remediate.py::test_dryrun_step_rejects_rule_outside_golden_snapshot` |
| 3 | **Dry run under the executing role.** `propose_fix` invokes the remediate Lambda for the dry run, because EC2 only returns `DryRunOperation` to a caller that is actually authorised. The state machine dry-runs again before Execute. | `voice_tools.propose_fix`, `remediate.dryrun` | `tests/test_actions_sg.py`, `tests/test_asl.py` |
| 4 | **Consent from the transcript, not the model.** `approve_fix` reads `TurnContext.transcript` (the raw text of the current turn) and requires the exact phrase `approve fix <n>`; the model's `confirmation_phrase` argument is ignored for the decision. | `src/beacon/turn_context.py`, `voice_tools.approve_fix` | `tests/test_voice_tools.py::test_approve_fix_requires_exact_phrase_in_the_raw_transcript` |
| 5 | **Stronger gate for standing approvals.** A Sleep Contract needs a read-back turn (alarm, action, exact resources, days, uses) and then the explicit phrase `grant contract for <n> days` or a Hinglish equivalent. Loose affirmatives only trigger the read-back. | `voice_tools.grant_sleep_contract` | `tests/test_voice_tools.py::test_grant_sleep_contract_needs_read_back_then_explicit_phrase` |
| 6 | **Approval is a record, not a prompt.** The `RequireApproval` state reads the approvals table and fails without a valid record whose action and params hash match. Approvals expire (15 min) and carry the channel and the verbatim quote. | `approvals.py`, `remediate.require_approval` | `tests/test_remediate_steps.py::test_require_approval_needs_a_valid_matching_record` |
| 7 | **Executes exactly once.** `mark_used` is a conditional write; the execute result is stored on the approval and retries replay it (`idempotent_replay: true`). | `remediate.execute` | `tests/test_remediate_steps.py::test_execute_restores_rule_once_and_is_idempotent_on_retry` |
| 8 | **Two roles, one direction.** The voice/agent role has no EC2/ECS write actions; it can only invoke the remediate Lambda and start the state machine. The remediator role holds only the allowlisted writes with `aws:ResourceTag/beacon:remediable = true`, plus the `security-group-rule/*` statement (a rule that does not exist yet cannot carry a tag). | `console-template.yaml`, `remediation-template.yaml` | `tests/test_template_safety.py` (parses the templates) |
| 9 | **Verification is three checks, all required.** Alarm `OK` with `StateUpdatedTimestamp` later than the execute time (a forced `set-alarm-state` or a stale OK cannot pass); the alarm's own metric at zero in the latest period; the action's post-condition (the rule is present). Up to six attempts 30 s apart, then Escalate pages a human. | `remediation/verify.py` | `tests/test_verify.py` |
| 10 | **Contracts are scoped and expire.** Match requires alarm name, action and the params hash; params must still be in the golden snapshot; `expires_at` is checked in code (DynamoDB TTL lags); `use` is a conditional increment so two triage runs cannot both consume the last use. | `contracts.py`, `handler._matching_contract` | `tests/test_contracts.py`, `tests/test_handler_pipeline.py` |
| 11 | **Kill switch on every write path.** `APPLY_ENABLED=false` is honoured by the triage contract branch, `approve_fix` / `grant_sleep_contract`, and the Execute step. `make apply-off` sets it on all three functions and remembers it for the next deploy. `/safety` shows the state per function. | `handler.py`, `voice_tools.py`, `remediate.py`, Makefile | `tests/test_remediate_steps.py::test_execute_honours_the_kill_switch`, `test_handler_pipeline.py::test_contract_match_with_apply_disabled_pages_instead` |
| 12 | **Public URL hygiene.** `/session` and `/turn` need a passcode; sessions are capped at 30 turns per incident; the dashboard redacts the account id and reduces actor ARNs to `type/name`. | `voice_turn.py`, `dashboard_api.redact` | `tests/test_voice_turn.py`, `tests/test_dashboard_api.py` |

## What Beacon deliberately cannot do

- Run any action not in the registry, or with params outside the schema.
- Restore a security-group rule that was never in the golden snapshot.
- Approve on its own, or on a paraphrase: the phrase must be in the transcript.
- Execute twice for one approval.
- Report "recovered" while the alarm is still in ALARM, or on an OK that predates the fix.
- Act under a contract for a different alarm, action, or resource than the one granted.

## Operational notes

- `make dry-run` on a healthy stack returns `InvalidPermission.Duplicate`, which counts as PASSED: the permission check runs before the duplicate check.
- If the remediator gets `UnauthorizedOperation` naming a `security-group-rule` ARN, the second IAM statement is missing; if it names the `security-group`, the tag is missing (`make tag-remediable`).
- The Connect/Lex phone path is read-only and not part of the remediation model.
