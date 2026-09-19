"""Lambda for the remediation write path (``beacon-remediate-<stack>``).

Runs under ``BeaconRemediatorRole``, the only role with the two allowlisted
write actions.  The voice Lambda invokes it for a proposal-time dry-run;
the Step Functions loop invokes one ``step`` per state; ``step: all`` runs
the whole loop inline (the fallback when Step Functions is unavailable).

Event shape: ``{"step": <name>, "approval_id", "incident_id", "action",
"params", ...}``.  Every step returns ``{"ok": bool, ...}``.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import boto3

from beacon import approvals, observability, store
from beacon.remediation import registry
from beacon.remediation.base import ParamError
from beacon.remediation.verify import verify_all

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _incidents_table() -> str:
    return _env("INCIDENTS_TABLE_NAME")


def _approvals_table() -> str:
    return _env("APPROVALS_TABLE_NAME")


def _apply_enabled() -> bool:
    return _env("APPLY_ENABLED", "true").lower() == "true"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _caller_arn() -> str:
    try:
        return str(boto3.client("sts").get_caller_identity()["Arn"])
    except Exception:
        logger.exception("get_caller_identity failed")
        return "unknown"


def _timeline(
    incident_id: str, event: str, detail: dict[str, Any] | None = None
) -> None:
    if not incident_id or not _incidents_table():
        return
    try:
        store.append_timeline(
            incident_id, event, table_name=_incidents_table(), detail=detail
        )
    except Exception:
        logger.exception("timeline append failed for %s", incident_id)


def _notify(subject: str, message: str) -> None:
    topic = _env("SNS_TOPIC_ARN")
    if not topic:
        logger.warning("SNS_TOPIC_ARN not set; notification skipped: %s", subject)
        return
    try:
        boto3.client("sns").publish(
            TopicArn=topic, Subject=subject[:100], Message=message
        )
    except Exception:
        logger.exception("SNS publish failed")


def _validated(event: dict[str, Any]) -> tuple[registry.ActionSpec, dict[str, Any]]:
    action_id = str(event.get("action", ""))
    params = registry.validate_params(action_id, event.get("params") or {})
    spec = registry.get_action(action_id)
    assert spec is not None  # validate_params already rejected unknown ids
    return spec, params


def _error(event: dict[str, Any], message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "action": event.get("action"), **extra}


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def dryrun(event: dict[str, Any]) -> dict[str, Any]:
    """Precondition + provider dry-run for one action, under this Lambda's role."""
    try:
        spec, params = _validated(event)
    except ParamError as exc:
        return _error(event, str(exc))

    error = spec.precondition(params)
    if error:
        return _error(event, error, action=spec.id, params=params)

    result = spec.dry_run(params)
    return {
        "ok": result.ok,
        "code": result.code,
        "detail": result.detail,
        "error": None
        if result.ok
        else f"dry run failed: {result.code} {result.detail}",
        "action": spec.id,
        "params": params,
        "blast_radius": spec.blast_radius(params),
        "role": _caller_arn(),
    }


def require_approval(event: dict[str, Any]) -> dict[str, Any]:
    """Refuse to continue without a valid approval record for exactly this action."""
    try:
        spec, params = _validated(event)
    except ParamError as exc:
        return _error(event, str(exc))
    record = approvals.get(
        str(event.get("approval_id", "")), table_name=_approvals_table()
    )
    if record is None:
        return _error(event, "no approval record")
    why = approvals.is_valid(record, spec.id, params)
    if why:
        return _error(event, why)
    return {
        "ok": True,
        "approval": {
            key: record.get(key)
            for key in (
                "approval_id",
                "source",
                "channel",
                "transcript_quote",
                "granted_at",
                "contract_id",
            )
        },
    }


def execute(event: dict[str, Any]) -> dict[str, Any]:
    """Run the action exactly once per approval; retries replay the stored result."""
    if not _apply_enabled():
        return _error(event, "apply disabled (APPLY_ENABLED=false)")
    try:
        spec, params = _validated(event)
    except ParamError as exc:
        return _error(event, str(exc))

    approval_id = str(event.get("approval_id", ""))
    incident_id = str(event.get("incident_id", ""))
    table = _approvals_table()
    record = approvals.get(approval_id, table_name=table)
    if record is None:
        return _error(event, "no approval record")
    if record.get("execute_result"):
        return {**record["execute_result"], "idempotent_replay": True}
    why = approvals.is_valid(record, spec.id, params)
    if why:
        return _error(event, why)
    if not approvals.mark_used(approval_id, table_name=table):
        again = approvals.get(approval_id, table_name=table) or {}
        if again.get("execute_result"):
            return {**again["execute_result"], "idempotent_replay": True}
        return _error(event, "approval already used")

    _timeline(
        incident_id,
        "executing",
        {"action": spec.id, "params": params, "approval_id": approval_id},
    )
    try:
        result = spec.execute(params)
    except (
        Exception
    ) as exc:  # the approval is consumed; record why so a retry replays it
        logger.exception("execute raised")
        failed: dict[str, Any] = {
            "ok": False,
            "code": type(exc).__name__,
            "detail": str(exc),
            "executed_at": _now_iso(),
            "action": spec.id,
            "params": params,
            "approval_id": approval_id,
            "idempotent_replay": False,
            "error": f"execute failed: {type(exc).__name__}: {exc}",
        }
        approvals.record_execution(approval_id, failed, table_name=table)
        _timeline(
            incident_id,
            "execute_failed",
            {"code": failed["code"], "detail": failed["detail"]},
        )
        return failed
    executed_at = _now_iso()
    out: dict[str, Any] = {
        "ok": result.ok,
        "code": result.code,
        "detail": result.detail,
        "executed_at": executed_at,
        "action": spec.id,
        "params": params,
        "approval_id": approval_id,
        "idempotent_replay": False,
        "error": None
        if result.ok
        else f"execute failed: {result.code} {result.detail}",
    }
    approvals.record_execution(approval_id, out, table_name=table)
    _timeline(
        incident_id,
        "executed" if result.ok else "execute_failed",
        {"code": result.code, "detail": result.detail, "executed_at": executed_at},
    )
    if result.ok and incident_id and _incidents_table():
        store.update_status(
            incident_id,
            "remediating",
            table_name=_incidents_table(),
            extra={"executed_at": executed_at},
        )
    return out


def verify(event: dict[str, Any]) -> dict[str, Any]:
    """One verification attempt: alarm OK after the fix, metric zero, post-condition."""
    attempts = int(event.get("attempts") or 0) + 1
    try:
        spec, params = _validated(event)
    except ParamError as exc:
        return {**_error(event, str(exc)), "attempts": attempts, "checks": []}
    incident_id = str(event.get("incident_id", ""))
    executed_at = datetime.fromisoformat(str(event.get("executed_at") or _now_iso()))

    alarm_name = _env("VERIFY_ALARM_NAME")
    if incident_id and _incidents_table():
        incident = store.get_incident(incident_id, table_name=_incidents_table())
        alarm_name = str(incident.get("alarm_name") or alarm_name)
    if not alarm_name:
        return {
            "ok": False,
            "attempts": attempts,
            "checks": [],
            "error": "incident has no alarm to verify",
        }

    result = verify_all(
        alarm_name, after=executed_at, postcondition=lambda: spec.postcondition(params)
    )
    checks = result.to_dict()["checks"]
    _timeline(
        incident_id,
        "verify_attempt",
        {"attempt": attempts, "ok": result.ok, "checks": checks},
    )
    return {
        "ok": result.ok,
        "attempts": attempts,
        "checks": checks,
        "alarm_name": alarm_name,
    }


def _loop_input(event: dict[str, Any]) -> dict[str, Any]:
    return event.get("input") or event


def _approval_of(inp: dict[str, Any]) -> dict[str, Any]:
    approval_id = str(inp.get("approval_id") or "")
    if not approval_id or not _approvals_table():
        return {}
    return approvals.get(approval_id, table_name=_approvals_table()) or {}


def resolve(event: dict[str, Any]) -> dict[str, Any]:
    """Mark the incident resolved and tell the humans (or tell them they slept)."""
    inp = _loop_input(event)
    incident_id = str(inp.get("incident_id", ""))
    record = _approval_of(inp)
    source = str(record.get("source") or "voice")
    handled_by = "contract" if source == "contract" else "voice"
    resolved_at = _now_iso()
    verify_payload = (inp.get("verify") or {}).get("Payload") or {}

    if incident_id and _incidents_table():
        store.update_status(
            incident_id,
            "resolved",
            table_name=_incidents_table(),
            extra={"resolved_at": resolved_at, "handled_by": handled_by},
        )
    _timeline(
        incident_id,
        "resolved",
        {"handled_by": handled_by, "attempts": verify_payload.get("attempts")},
    )

    quote = str(record.get("transcript_quote") or "")
    lines = [
        f"Resolved. Beacon fixed incident {incident_id} and verified the recovery.",
        f"Action: {inp.get('action')} {inp.get('params')}",
        f'Approved via {source} ({record.get("channel", "-")}): "{quote}"',
        "Verified: the alarm returned to OK after the fix, the error metric is at "
        "zero, "
        "and the post-condition holds.",
    ]
    if handled_by == "contract":
        lines.insert(
            1,
            f"Handled under Sleep Contract {record.get('contract_id')}. "
            "You were not woken.",
        )
    dashboard = _env("DASHBOARD_URL")
    if dashboard:
        lines.append(f"Timeline: {dashboard}?incident={incident_id}")
    _notify(f"Beacon - Resolved: {inp.get('action')}", "\n".join(lines))
    return {"ok": True, "resolved_at": resolved_at, "handled_by": handled_by}


def escalate(event: dict[str, Any]) -> dict[str, Any]:
    """Verification failed (or a gate refused): page a human."""
    inp = _loop_input(event)
    incident_id = str(inp.get("incident_id", ""))
    reason = str(inp.get("reason") or "")
    for stage in ("dryrun", "approval", "execute", "verify"):
        payload = (inp.get(stage) or {}).get("Payload") or {}
        if payload and not payload.get("ok"):
            reason = (
                reason or f"{stage}: {payload.get('error') or 'checks did not pass'}"
            )
            if stage == "verify":
                failed = [c for c in payload.get("checks", []) if not c.get("ok")]
                if failed:
                    reason = "verify: " + "; ".join(
                        str(c.get("detail")) for c in failed
                    )
    if incident_id and _incidents_table():
        store.update_status(
            incident_id,
            "escalated",
            table_name=_incidents_table(),
            extra={"escalated_at": _now_iso()},
        )
    _timeline(incident_id, "escalated", {"reason": reason})
    lines = [
        f"Beacon could not verify recovery for incident {incident_id}. "
        "A human is needed.",
        f"Reason: {reason or 'unknown'}",
        f"Action attempted: {inp.get('action')} {inp.get('params')}",
    ]
    dashboard = _env("DASHBOARD_URL")
    if dashboard:
        lines.append(f"Timeline: {dashboard}?incident={incident_id}")
    _notify("Beacon - ESCALATED: human needed", "\n".join(lines))
    return {"ok": True, "escalated": True, "reason": reason}


def run_all(event: dict[str, Any]) -> dict[str, Any]:
    """Inline loop: the same steps Step Functions would drive, in one invocation."""
    wait = float(_env("VERIFY_WAIT_SECONDS", "30"))
    max_attempts = int(_env("VERIFY_MAX_ATTEMPTS", "6"))
    state: dict[str, Any] = dict(event)

    for stage, fn in (
        ("dryrun", dryrun),
        ("approval", require_approval),
        ("execute", execute),
    ):
        payload = fn(event)
        state[stage] = {"Payload": payload}
        if not payload.get("ok"):
            escalate({"input": state})
            return {
                "ok": False,
                "final": "escalated",
                "stage": stage,
                "error": payload.get("error"),
            }

    attempts = 0
    payload = {"ok": False, "attempts": 0, "checks": []}
    while attempts < max_attempts:
        if wait:
            time.sleep(wait)
        payload = verify(
            {
                **event,
                "executed_at": state["execute"]["Payload"]["executed_at"],
                "attempts": attempts,
            }
        )
        attempts = int(payload.get("attempts", attempts + 1))
        state["verify"] = {"Payload": payload}
        if payload.get("ok"):
            resolve({"input": state})
            return {"ok": True, "final": "resolved", "attempts": attempts}
    escalate({"input": state})
    return {"ok": False, "final": "escalated", "stage": "verify", "attempts": attempts}


_STEPS = {
    "dryrun": dryrun,
    "require_approval": require_approval,
    "execute": execute,
    "verify": verify,
    "resolve": resolve,
    "escalate": escalate,
    "all": run_all,
}


def _emit_outcome_metrics(
    step: str, event: dict[str, Any], result: dict[str, Any]
) -> None:
    """One EMF line per step; Resolve/Escalate carry the loop's headline numbers."""
    observability.metric(f"Step{step.title().replace('_', '')}", 1, unit="Count")
    if step == "verify":
        observability.metric(
            "VerifyAttempts", float(result.get("attempts", 0)), unit="Count"
        )
    if step == "resolve":
        inp = _loop_input(event)
        executed = ((inp.get("execute") or {}).get("Payload") or {}).get("executed_at")
        if executed:
            try:
                since = datetime.now(tz=UTC) - datetime.fromisoformat(str(executed))
                observability.metric(
                    "RemediationSeconds", since.total_seconds(), unit="Seconds"
                )
            except ValueError:
                pass
        woken = 0.0 if result.get("handled_by") == "contract" else 1.0
        observability.metric("HumansWoken", woken, unit="Count")
        observability.metric("Resolved", 1, unit="Count")
    if step == "escalate":
        observability.metric("Escalated", 1, unit="Count")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    step = str(event.get("step", ""))
    fn = _STEPS.get(step)
    if fn is None:
        return {"ok": False, "error": f"unknown step: {step or '<missing>'}"}
    logger.info(
        "remediate step=%s action=%s incident=%s",
        step,
        event.get("action"),
        event.get("incident_id"),
    )
    # ``service`` is the only dimension: the dashboard and the Escalated alarm
    # query that dimension set; the action rides along as searchable metadata.
    with observability.metrics_scope(service="beacon-remediate"):
        observability.metadata("action", str(event.get("action", "")))
        observability.metadata("incident_id", str(event.get("incident_id", "")))
        result = fn(event)
        _emit_outcome_metrics(step, event, result)
    return result
