"""The six tools the voice agent can call, and their JSON schemas.

``TOOL_SCHEMAS`` is the contract shared by every voice backend (Strands on
Nova 2 Lite today, the litellm fallback loop, AssemblyAI's Voice Agent API
later).  The functions are plain Python so any backend can dispatch to them.

Safety rules enforced here, not in the model:

* ``propose_fix`` only proposes the allowlisted action the incident's
  diagnostics/RCA named, dry-run under the remediator role.
* ``approve_fix`` executes only if the *raw transcript of this turn*
  contains the exact confirmation phrase, the passcode was presented, and
  ``APPLY_ENABLED`` is true.
* ``grant_sleep_contract`` needs a read-back turn first, then an explicit
  grant phrase (English or Hinglish); loose affirmatives never grant.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

import boto3

from beacon import approvals, contracts, observability, store
from beacon.remediation import registry
from beacon.remediation.base import ParamError
from beacon.turn_context import current

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# English number words only: Hindi "do" (give) and "saat" (seven) must not be
# rewritten or the Hinglish grant phrase stops matching.
_NUMBER_WORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}
_GRANT_PATTERNS = (
    re.compile(r"\bgrant (?:the )?contract for \d+ days?\b"),
    re.compile(r"\bgrant (?:the )?contract\b"),
    re.compile(r"\bcontract (?:do|de do|dedo|grant karo)\b"),
    re.compile(r"\bcontract (?:grant|de) (?:kar|kar do|karo)\b"),
)


def _incidents_table() -> str:
    return os.environ.get("INCIDENTS_TABLE_NAME", "")


def _approvals_table() -> str:
    return os.environ.get("APPROVALS_TABLE_NAME", "")


def _contracts_table() -> str:
    return os.environ.get("CONTRACTS_TABLE_NAME", "")


def _apply_enabled() -> bool:
    return os.environ.get("APPLY_ENABLED", "true").lower() != "false"


def _normalise(text: str) -> str:
    words = re.sub(r"[^a-z0-9\s]", " ", text.lower()).split()
    return " ".join(_NUMBER_WORDS.get(w, w) for w in words)


def _incident() -> dict[str, Any]:
    ctx = current()
    incident = store.get_incident(ctx.incident_id, table_name=_incidents_table())
    if not incident:
        raise LookupError(f"incident {ctx.incident_id} not found")
    return incident


def _rca(incident: dict[str, Any]) -> dict[str, Any]:
    rca = incident.get("rca_json") or {}
    return rca if isinstance(rca, dict) else {}


@observability.span("lambda.remediate_dryrun")
def _invoke_remediate(payload: dict[str, Any]) -> dict[str, Any]:
    """Synchronous invoke of beacon-remediate (the only role that may dry-run)."""
    arn = os.environ.get("REMEDIATE_FUNCTION_ARN", "")
    if not arn:
        return {"ok": False, "error": "REMEDIATE_FUNCTION_ARN not set"}
    resp = boto3.client("lambda").invoke(
        FunctionName=arn,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode(),
    )
    body = resp["Payload"].read()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"remediate returned non-JSON: {body[:200]!r}"}
    return (
        parsed if isinstance(parsed, dict) else {"ok": False, "error": "bad response"}
    )


def _start_execution(payload: dict[str, Any]) -> str:
    arn = os.environ.get("STATE_MACHINE_ARN", "")
    if not arn:
        raise RuntimeError("STATE_MACHINE_ARN not set")
    resp = boto3.client("stepfunctions").start_execution(
        stateMachineArn=arn, input=json.dumps(payload, default=str)
    )
    return str(resp["executionArn"])


def _tool_event(
    name: str, args: dict[str, Any], summary: str, evidence_id: str | None = None
) -> None:
    current().tool_events.append(
        {"name": name, "args": args, "summary": summary, "evidence_id": evidence_id}
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@observability.span("tool:get_incident_brief")
def get_incident_brief() -> dict[str, Any]:
    """The incident in one call: status, summary, spoken summary, cause, fix."""
    incident = _incident()
    rca = _rca(incident)
    beacon_json = rca.get("beacon_json") or {}
    card = current().add_evidence(
        "rca",
        f"RCA ({rca.get('status', '?')})",
        {
            "summary": rca.get("summary"),
            "evidence": rca.get("evidence", []),
            "next_steps": rca.get("next_steps", []),
        },
    )
    out = {
        "incident_id": incident["incident_id"],
        "alarm_name": incident.get("alarm_name"),
        "status": rca.get("status", "Unknown"),
        "incident_status": incident.get("status"),
        "summary": rca.get("summary", ""),
        "spoken_summary": rca.get("spoken_summary", ""),
        "affected_components": rca.get("affected_components", []),
        "change_correlation": rca.get("change_correlation"),
        "suggested_action": beacon_json.get("suggested_action"),
        "evidence": [card],
    }
    _tool_event("get_incident_brief", {}, f"briefed on {out['alarm_name']}", card["id"])
    return out


@observability.span("tool:get_evidence")
def get_evidence(kind: str) -> dict[str, Any]:
    """Evidence of one kind: logs, metrics, changes, diagnostics, or rca."""
    incident = _incident()
    rca = _rca(incident)
    cached = incident.get("cached_data") or {}
    if isinstance(cached, str):
        try:
            cached = json.loads(cached)
        except json.JSONDecodeError:
            cached = {}
    kind = kind.strip().lower()
    if kind == "logs":
        payload: Any = {
            "rca_evidence": rca.get("evidence", []),
            "log_queries": cached.get("logs", []),
        }
        title = "Log evidence"
    elif kind == "metrics":
        payload = cached.get("metrics", [])
        title = "Metrics"
    elif kind == "changes":
        payload = incident.get("changes") or []
        title = "Recent changes (CloudTrail)"
    elif kind == "diagnostics":
        payload = incident.get("diagnostics") or {}
        title = "Security group drift"
    elif kind == "rca":
        payload = {
            k: rca.get(k)
            for k in ("summary", "evidence", "next_steps", "change_correlation")
        }
        title = "RCA"
    else:
        return {
            "error": (
                f"unknown evidence kind '{kind}'; "
                "use logs, metrics, changes, diagnostics or rca"
            )
        }
    card = current().add_evidence(kind, title, payload)
    _tool_event("get_evidence", {"kind": kind}, f"{title}: {card['id']}", card["id"])
    return {"kind": kind, "evidence": [card], "data": payload}


@observability.span("tool:propose_fix")
def propose_fix() -> dict[str, Any]:
    """Propose the one allowlisted fix, dry-run under the remediator role."""
    incident = _incident()
    beacon_json = _rca(incident).get("beacon_json") or {}
    action = beacon_json.get("suggested_action")
    params = beacon_json.get("action_params")
    if not action or not isinstance(params, dict):
        _tool_event("propose_fix", {}, "no safe fix known")
        return {"error": "no safe fix known for this incident; investigate manually"}
    try:
        params = registry.validate_params(str(action), params)
    except ParamError as exc:
        return {"error": f"no safe fix: {exc}"}

    existing = [p for p in (incident.get("proposals") or []) if isinstance(p, dict)]
    fix_id = len(existing) + 1
    result = _invoke_remediate(
        {
            "step": "dryrun",
            "action": action,
            "params": params,
            "incident_id": incident["incident_id"],
        }
    )
    spec = registry.get_action(str(action))
    blast = str(
        result.get("blast_radius") or (spec.blast_radius(params) if spec else "")
    )
    dry_run = {
        "ok": bool(result.get("ok")),
        "code": result.get("code"),
        "detail": result.get("detail") or result.get("error"),
        "role": result.get("role", ""),
    }
    proposal = approvals.create_proposal(
        incident["incident_id"],
        fix_id,
        str(action),
        params,
        blast_radius=blast,
        dry_run=dry_run,
        table_name=_approvals_table(),
    )
    summary = {
        "fix_id": fix_id,
        "action": action,
        "params": params,
        "blast_radius": blast,
        "dry_run": dry_run,
        "expires_at": proposal["expires_at"],
    }
    store.update_status(
        incident["incident_id"],
        str(incident.get("status") or "awaiting_engineer"),
        table_name=_incidents_table(),
        extra={"proposals": [*existing, summary]},
    )
    store.append_timeline(
        incident["incident_id"],
        "fix_proposed",
        table_name=_incidents_table(),
        detail={
            "fix_id": fix_id,
            "action": action,
            "dry_run": dry_run,
            "blast_radius": blast,
        },
    )
    card = current().add_evidence("dry_run", f"Dry run of fix {fix_id}", dry_run)
    _tool_event(
        "propose_fix",
        {},
        f"fix {fix_id}: {action} (dry run {'PASSED' if dry_run['ok'] else 'FAILED'})",
        card["id"],
    )
    return {
        **summary,
        "confirmation_phrase": registry.confirmation_phrase(fix_id),
        "evidence": [card],
        "instruction": (
            f"Read back the blast radius and ask the engineer to say exactly "
            f"'{registry.confirmation_phrase(fix_id)}' to approve."
            if dry_run["ok"]
            else "The dry run failed; do not offer to apply this fix."
        ),
    }


@observability.span("tool:approve_fix")
def approve_fix(fix_id: int, confirmation_phrase: str) -> dict[str, Any]:
    """Execute proposal *fix_id* if the engineer really said the phrase."""
    ctx = current()
    phrase = registry.confirmation_phrase(int(fix_id))
    spoken = _normalise(ctx.transcript)
    refusal: dict[str, Any] = {"approved": False, "fix_id": int(fix_id)}
    if not ctx.passcode_ok:
        _tool_event("approve_fix", {"fix_id": fix_id}, "refused: no passcode")
        return {**refusal, "error": "the session is not authorised (passcode missing)"}
    if not _apply_enabled():
        _tool_event("approve_fix", {"fix_id": fix_id}, "refused: apply disabled")
        return {**refusal, "error": "remediation is disabled (APPLY_ENABLED=false)"}
    if phrase not in spoken:
        _tool_event(
            "approve_fix", {"fix_id": fix_id}, "refused: phrase not in transcript"
        )
        return {**refusal, "error": f"I need you to say exactly '{phrase}' to approve"}

    incident = _incident()
    proposal = approvals.get_proposal(
        incident["incident_id"], int(fix_id), table_name=_approvals_table()
    )
    if proposal is None:
        return {
            **refusal,
            "error": (
                f"no proposal {fix_id} (or it expired); ask me to propose the fix again"
            ),
        }
    if not (proposal.get("dry_run") or {}).get("ok"):
        return {**refusal, "error": "that fix failed its dry run; it cannot be applied"}

    record = approvals.create(
        incident["incident_id"],
        str(proposal["action"]),
        dict(proposal["params"]),
        source="voice",
        channel=ctx.channel,
        transcript_quote=ctx.transcript.strip(),
        fix_id=int(fix_id),
        table_name=_approvals_table(),
    )
    payload = {
        "approval_id": record["approval_id"],
        "incident_id": incident["incident_id"],
        "action": proposal["action"],
        "params": proposal["params"],
    }
    store.append_timeline(
        incident["incident_id"],
        "approved",
        table_name=_incidents_table(),
        detail={
            "fix_id": int(fix_id),
            "channel": ctx.channel,
            "transcript_quote": ctx.transcript.strip(),
            "approval_id": record["approval_id"],
        },
    )
    try:
        execution_arn = _start_execution(payload)
    except Exception as exc:
        logger.exception("start_execution failed")
        return {**refusal, "error": f"could not start the remediation loop: {exc}"}
    latest = store.get_incident(incident["incident_id"], table_name=_incidents_table())
    if latest.get("status") in ("resolved", "escalated"):
        # the loop ran inline (local / REMEDIATION_MODE=inline) and already finished
        extra: dict[str, Any] = {"execution_arn": execution_arn, "handled_by": "voice"}
        store.update_status(
            incident["incident_id"],
            str(latest["status"]),
            table_name=_incidents_table(),
            extra=extra,
        )
    else:
        store.update_status(
            incident["incident_id"],
            "remediating",
            table_name=_incidents_table(),
            extra={
                "execution_arn": execution_arn,
                "handled_by": "voice",
                "execute_requested_at": record["granted_at"],
            },
        )
    _tool_event(
        "approve_fix",
        {"fix_id": fix_id},
        f'approved via {ctx.channel}: "{ctx.transcript.strip()}"',
    )
    return {
        "approved": True,
        "fix_id": int(fix_id),
        "approval_id": record["approval_id"],
        "execution_arn": execution_arn,
        "transcript_quote": ctx.transcript.strip(),
        "instruction": (
            "Tell the engineer the fix is being applied and verified; "
            "you will report when the alarm clears."
        ),
    }


def _read_back(
    incident: dict[str, Any],
    action: str,
    params: dict[str, Any],
    days: int,
    max_uses: int,
) -> str:
    scope = ", ".join(f"{k} {v}" for k, v in params.items())
    day_word = {
        1: "one",
        2: "two",
        3: "three",
        5: "five",
        7: "seven",
        14: "fourteen",
        30: "thirty",
    }.get(days, str(days))
    return (
        f"Sleep Contract read-back: when alarm {incident.get('alarm_name')} "
        "fires again, "
        f"Beacon may run {action} on exactly {scope}, at most {max_uses} times, "
        f"for {day_word} days. Say 'grant contract for {day_word} days' to confirm."
    )


@observability.span("tool:grant_sleep_contract")
def grant_sleep_contract(days: int = 7, max_uses: int = 3) -> dict[str, Any]:
    """Grant a scoped, expiring standing approval; read-back first, then the phrase."""
    ctx = current()
    incident = _incident()
    days = max(1, min(int(days), 30))
    max_uses = max(1, min(int(max_uses), 10))
    beacon_json = _rca(incident).get("beacon_json") or {}
    action = beacon_json.get("suggested_action")
    params = beacon_json.get("action_params")
    if not action or not isinstance(params, dict):
        return {
            "granted": False,
            "error": "no allowlisted action on this incident to contract",
        }
    try:
        params = registry.validate_params(str(action), params)
    except ParamError as exc:
        return {"granted": False, "error": f"cannot contract: {exc}"}

    pending = incident.get("contract_readback_pending") or {}
    spoken = _normalise(ctx.transcript)
    said_grant = any(p.search(spoken) for p in _GRANT_PATTERNS)

    if not pending or not said_grant or not ctx.passcode_ok or not _apply_enabled():
        read_back = _read_back(incident, str(action), params, days, max_uses)
        store.update_status(
            incident["incident_id"],
            str(incident.get("status") or "resolved"),
            table_name=_incidents_table(),
            extra={
                "contract_readback_pending": {
                    "days": days,
                    "max_uses": max_uses,
                    "action": action,
                }
            },
        )
        _tool_event(
            "grant_sleep_contract",
            {"days": days, "max_uses": max_uses},
            "read-back issued, awaiting explicit grant phrase",
        )
        error = None
        if not ctx.passcode_ok:
            error = "the session is not authorised (passcode missing)"
        elif not _apply_enabled():
            error = "remediation is disabled (APPLY_ENABLED=false)"
        return {
            "granted": False,
            "read_back_pending": True,
            "read_back": read_back,
            "error": error,
            "instruction": (
                "Read the read-back aloud verbatim and wait for the exact phrase."
            ),
        }

    record = contracts.put(
        alarm_name=str(incident.get("alarm_name")),
        action=str(action),
        params=params,
        days=int(pending.get("days", days)),
        max_uses=int(pending.get("max_uses", max_uses)),
        transcript_quote=ctx.transcript.strip(),
        granted_by=ctx.channel,
        incident_id=incident["incident_id"],
        table_name=_contracts_table(),
    )
    store.update_status(
        incident["incident_id"],
        str(incident.get("status") or "resolved"),
        table_name=_incidents_table(),
        extra={"contract_readback_pending": None, "contract_id": record["contract_id"]},
    )
    store.append_timeline(
        incident["incident_id"],
        "contract_granted",
        table_name=_incidents_table(),
        detail={
            "contract_id": record["contract_id"],
            "days": record["days"],
            "max_uses": record["max_uses"],
            "transcript_quote": record["transcript_quote"],
        },
    )
    _tool_event(
        "grant_sleep_contract",
        {"days": record["days"], "max_uses": record["max_uses"]},
        f'granted: "{record["transcript_quote"]}"',
    )
    return {
        "granted": True,
        "contract_id": record["contract_id"],
        "expires_at": record["expires_at"],
        "days": record["days"],
        "max_uses": record["max_uses"],
        "scope": params,
        "instruction": (
            "Confirm the contract in one sentence and wish the engineer good night."
        ),
    }


@observability.span("tool:check_recovery")
def check_recovery() -> dict[str, Any]:
    """Where the remediation loop is: status, last verify attempt, timings."""
    incident = _incident()
    verifies = [
        e for e in incident.get("timeline", []) if e.get("event") == "verify_attempt"
    ]
    last = (verifies[-1].get("detail") or {}) if verifies else {}
    out = {
        "status": incident.get("status"),
        "executed_at": incident.get("executed_at"),
        "resolved_at": incident.get("resolved_at"),
        "handled_by": incident.get("handled_by"),
        "last_verify": {
            "attempt": last.get("attempt"),
            "ok": last.get("ok"),
            "checks": last.get("checks", []),
        }
        if last
        else None,
    }
    _tool_event("check_recovery", {}, f"status {out['status']}")
    return out


# ---------------------------------------------------------------------------
# Schemas (the cross-backend contract)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_incident_brief",
        "description": (
            "Get the incident summary, severity, likely cause and the suggested fix. "
            "Call this first."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_evidence",
        "description": (
            "Fetch evidence of one kind for the current incident: logs, metrics, "
            "changes (CloudTrail write calls before the alarm), diagnostics "
            "(security group drift), or rca."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["logs", "metrics", "changes", "diagnostics", "rca"],
                }
            },
            "required": ["kind"],
        },
    },
    {
        "name": "propose_fix",
        "description": (
            "Propose the one allowlisted fix for this incident. Runs a dry run under "
            "the remediator role and returns the blast radius and the exact "
            "confirmation phrase. Never applies anything."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "approve_fix",
        "description": (
            "Apply a proposed fix. Only succeeds if the engineer literally said "
            "'approve fix <n>' in this turn; the server checks the transcript. "
            "Then a verified remediation loop runs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fix_id": {
                    "type": "integer",
                    "description": "The fix number from propose_fix",
                },
                "confirmation_phrase": {
                    "type": "string",
                    "description": "What the engineer said, e.g. 'approve fix 1'",
                },
            },
            "required": ["fix_id", "confirmation_phrase"],
        },
    },
    {
        "name": "grant_sleep_contract",
        "description": (
            "Grant Beacon a standing approval to run this same fix for this alarm "
            "next time without waking anyone. First call returns a read-back to "
            "speak aloud; it is granted only when the engineer then says "
            "'grant contract for <n> days'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days the contract lasts (1-30)",
                    "default": 7,
                },
                "max_uses": {
                    "type": "integer",
                    "description": "How many times it may be used (1-10)",
                    "default": 3,
                },
            },
            "required": [],
        },
    },
    {
        "name": "check_recovery",
        "description": (
            "Check whether the remediation loop has verified recovery "
            "(alarm OK after the fix, error metric zero, post-condition)."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]

TOOL_FUNCTIONS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_incident_brief": get_incident_brief,
    "get_evidence": get_evidence,
    "propose_fix": propose_fix,
    "approve_fix": approve_fix,
    "grant_sleep_contract": grant_sleep_contract,
    "check_recovery": check_recovery,
}


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one tool by name with JSON args (used by every backend)."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name}"}
    try:
        return fn(**args)
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}
    except LookupError as exc:
        return {"error": str(exc)}
