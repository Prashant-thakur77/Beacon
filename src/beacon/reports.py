"""Documents made from the records, with no model call: the postmortem for one
incident, the audit log of every consent, and the morning report for a night.

All three are pure functions over the DynamoDB rows so they are testable, and
they work on a deployment whose model access is unavailable.
"""

from __future__ import annotations

import csv
import io
import json
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

_IST = timedelta(hours=5, minutes=30)


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _span(start: Any, end: Any) -> str:
    a, b = _dt(start), _dt(end)
    if a is None or b is None:
        return "n/a"
    secs = int((b - a).total_seconds())
    if secs < 60:
        return f"{secs} s"
    return f"{secs // 60} m {secs % 60} s"


def _hhmm(value: Any) -> str:
    d = _dt(value)
    return (d + _IST).strftime("%H:%M:%S IST") if d else "?"


def _rca(incident: dict[str, Any]) -> dict[str, Any]:
    rca = incident.get("rca_json") or {}
    if isinstance(rca, str):
        try:
            rca = json.loads(rca)
        except json.JSONDecodeError:
            rca = {}
    return dict(rca) if isinstance(rca, dict) else {}


# ---------------------------------------------------------------------------
# Postmortem
# ---------------------------------------------------------------------------


def _attested(att: dict[str, Any]) -> str:
    """One clause on where the words came from (confidence, recording, file)."""
    bits: list[str] = []
    conf = att.get("confidence")
    if isinstance(conf, int | float):
        bits.append(f"heard at {int(float(conf) * 100)}% confidence")
    sid = str(att.get("session_id") or "")
    if sid.startswith("sess_"):
        bits.append(f"AssemblyAI session recording `{sid}`")
    if att.get("telegram_file_id"):
        bits.append("Telegram voice note on file")
    return f" Attested: {', '.join(bits)}." if bits else ""


def postmortem(
    incident: dict[str, Any],
    *,
    contracts: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
) -> str:
    """Markdown postmortem for one incident, from the timeline and records."""
    rca = _rca(incident)
    alarm = incident.get("alarm_name") or "incident"
    status = str(incident.get("status") or "unknown")
    timeline = [e for e in incident.get("timeline") or [] if isinstance(e, dict)]
    inc_id = incident.get("incident_id")
    mine_approvals = [a for a in approvals if a.get("incident_id") == inc_id]
    granted_here = [c for c in contracts if c.get("incident_id") == inc_id]
    used_contract = next(
        (c for c in contracts if c.get("contract_id") == incident.get("contract_id")),
        None,
    )

    out: list[str] = []
    out.append(f"# Postmortem: {alarm}")
    out.append("")
    out.append(
        f"Incident `{inc_id}` · {_hhmm(incident.get('timestamp'))} · "
        f"status **{status}**"
    )
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append(str(rca.get("summary") or "No root-cause summary was recorded."))
    if status == "escalated":
        reason = next(
            (
                (e.get("detail") or {}).get("reason")
                for e in timeline
                if e.get("event") == "escalated"
            ),
            None,
        )
        out.append("")
        out.append(f"**Escalated to a human.** Reason: {reason or 'not recorded'}.")
    out.append("")
    out.append("## Timeline")
    out.append("")
    out.append("| Time (IST) | Event | Detail |")
    out.append("|---|---|---|")
    for e in timeline:
        detail = e.get("detail") or {}
        if e.get("event") == "verify_attempt":
            checks = ", ".join(
                f"{c.get('name')}={'ok' if c.get('ok') else 'no'}"
                for c in detail.get("checks", [])
            )
            verdict = "passed" if detail.get("ok") else "not yet"
            txt = f"attempt {detail.get('attempt')} · {verdict} · {checks}"
        elif e.get("event") == "approved":
            txt = f'"{detail.get("quote", "")}" via {detail.get("channel", "?")}'
        else:
            txt = ", ".join(f"{k}={v}" for k, v in detail.items()) if detail else ""
        out.append(f"| {_hhmm(e.get('t'))} | {e.get('event')} | {txt} |")
    out.append("")
    out.append(
        "Alarm to recovery: **"
        + _span(incident.get("timestamp"), incident.get("resolved_at"))
        + "**"
        if incident.get("resolved_at")
        else "Alarm to recovery: not recovered."
    )
    out.append("")
    out.append("## Root cause")
    out.append("")
    out.append(
        str(rca.get("change_correlation") or rca.get("summary") or "Not recorded.")
    )
    evidence = rca.get("evidence") or []
    if evidence:
        out.append("")
        out.append("Evidence:")
        out.append("")
        out.extend(f"- `{line}`" for line in evidence[:8])
    out.append("")
    out.append("## What changed")
    out.append("")
    changes = incident.get("changes") or []
    if changes:
        for c in changes[:6]:
            ids = ", ".join(c.get("resource_ids") or [])
            out.append(
                f"- `{c.get('event_name')}` by {c.get('actor_short', '?')} at "
                f"{_hhmm(c.get('event_time'))} on {ids or 'n/a'}"
            )
    else:
        out.append("No write calls were found in the change ledger before the alarm.")
    out.append("")
    out.append("## The fix")
    out.append("")
    proposals = incident.get("proposals") or []
    if proposals:
        p = proposals[-1]
        dry = p.get("dry_run") or {}
        out.append(
            f"Proposed `{p.get('action')}` (fix {p.get('fix_id')}). Blast radius: "
            f"{p.get('blast_radius', 'n/a')}. Dry run: "
            f"{'passed' if dry.get('ok') else 'failed'} ({dry.get('code', '?')})."
        )
    else:
        out.append("No fix was proposed.")
    for a in mine_approvals:
        out.append("")
        out.append(
            f"Approved by {a.get('source', '?')} via {a.get('channel', '?')} at "
            f'{_hhmm(a.get("granted_at"))}: "{a.get("transcript_quote", "")}". '
            f"Executed: {'yes' if a.get('used_at') else 'no'}."
            + _attested(a.get("attestation") or {})
        )
    if used_contract:
        out.append("")
        out.append(
            f"Run under Sleep Contract `{used_contract.get('contract_id')}` "
            f'("{used_contract.get("transcript_quote", "")}"); nobody was woken.'
        )
    out.append("")
    out.append("## Verification")
    out.append("")
    verifies = [e for e in timeline if e.get("event") == "verify_attempt"]
    if verifies:
        last = verifies[-1].get("detail") or {}
        out.append(
            f"{len(verifies)} attempt(s); final attempt {last.get('attempt')} "
            f"{'passed' if last.get('ok') else 'failed'}:"
        )
        out.append("")
        for c in last.get("checks", []):
            out.append(f"- {c.get('name')}: {'ok' if c.get('ok') else 'not met'}")
    else:
        out.append("No verification ran.")
    out.append("")
    out.append("## Sleep Contract")
    out.append("")
    if granted_here:
        c = granted_here[0]
        out.append(
            f"Granted for {c.get('days')} days, {c.get('max_uses')} uses, on "
            f"`{c.get('alarm_name')}` / `{c.get('action')}`: "
            f'"{c.get("transcript_quote", "")}" (expires {_hhmm(c.get("expires_at"))}).'
        )
    else:
        out.append("No contract was granted from this incident.")
    out.append("")
    out.append("## Cost")
    out.append("")
    usage = incident.get("usage") or {}
    cost = usage.get("cost_inr")
    out.append(
        f"Model cost for this incident: ₹{float(cost):.2f}."
        if cost is not None
        else "Model cost not recorded."
    )
    out.append("")
    out.append("_Generated by Beacon from the incident record; no model was involved._")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def audit(
    *,
    approvals: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    incidents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Every consent record, newest first, with the words that granted it."""
    alarm_of = {i.get("incident_id"): i.get("alarm_name") for i in incidents}
    rows: list[dict[str, Any]] = []
    for a in approvals:
        if a.get("kind") not in (None, "approval"):
            continue
        rows.append(
            {
                "at": a.get("granted_at"),
                "kind": "approval",
                "id": a.get("approval_id"),
                "incident_id": a.get("incident_id"),
                "alarm_name": alarm_of.get(a.get("incident_id")),
                "action": a.get("action"),
                "quote": a.get("transcript_quote", ""),
                "channel": a.get("channel"),
                "source": a.get("source"),
                "executed": bool(a.get("used_at")),
                "executed_at": a.get("used_at"),
                "result": (a.get("result") or {}).get("code"),
                "contract_id": a.get("contract_id"),
                "attestation": a.get("attestation") or {},
            }
        )
    for c in contracts:
        rows.append(
            {
                "at": c.get("granted_at"),
                "kind": "contract",
                "id": c.get("contract_id"),
                "incident_id": c.get("incident_id"),
                "alarm_name": c.get("alarm_name"),
                "action": c.get("action"),
                "quote": c.get("transcript_quote", ""),
                "channel": "voice",
                "source": c.get("granted_by"),
                "status": c.get("status"),
                "uses": f"{c.get('uses', 0)}/{c.get('max_uses', 0)}",
                "expires_at": c.get("expires_at"),
                "attestation": c.get("attestation") or {},
            }
        )
    rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    return rows


_CSV_FIELDS = (
    "at",
    "kind",
    "alarm_name",
    "action",
    "quote",
    "channel",
    "source",
    "executed",
    "result",
    "status",
    "uses",
    "expires_at",
    "incident_id",
    "id",
)


def audit_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(
            {k: ("" if r.get(k) is None else r.get(k)) for k in _CSV_FIELDS}
        )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Morning report
# ---------------------------------------------------------------------------


def night_of(ts: Any) -> str:
    """Nights turn over at noon IST: a 23:30 page and its 03:00 repeat are one night."""
    d = _dt(ts)
    if d is None:
        return "unknown"
    local = d + _IST - timedelta(hours=12)
    return local.strftime("%Y-%m-%d")


def morning_report(
    incidents: list[dict[str, Any]],
    *,
    contracts: list[dict[str, Any]],
    night_of: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Summarise one night; ``night_of`` defaults to the night that just ended."""
    ref = _dt(now) or datetime.now(tz=UTC)
    night = night_of or globals()["night_of"](ref - timedelta(hours=12))
    rows = [i for i in incidents if globals()["night_of"](i.get("timestamp")) == night]
    rows.sort(key=lambda i: str(i.get("timestamp") or ""))
    resolved = [i for i in rows if i.get("status") == "resolved"]
    escalated = [i for i in rows if i.get("status") == "escalated"]
    woken = sum(1 for i in rows if i.get("woken") is not False)
    by_contract = [i for i in rows if i.get("handled_by") == "contract"]
    minutes = []
    for i in resolved:
        a, b = _dt(i.get("timestamp")), _dt(i.get("resolved_at"))
        if a and b:
            minutes.append((b - a).total_seconds() / 60)
    median = round(statistics.median(minutes), 1) if minutes else None
    cost = round(
        sum(float((i.get("usage") or {}).get("cost_inr") or 0) for i in rows), 2
    )
    used_ids = {i.get("contract_id") for i in by_contract}
    contracts_used = [
        {
            "contract_id": c.get("contract_id"),
            "alarm_name": c.get("alarm_name"),
            "uses": f"{c.get('uses', 0)}/{c.get('max_uses', 0)}",
        }
        for c in contracts
        if c.get("contract_id") in used_ids
    ]

    lines = [f"Good morning. Night of {night}."]
    if not rows:
        lines.append("A quiet night: no incidents, nobody woken.")
    else:
        lines.append(
            f"{len(rows)} incident(s): {len(resolved)} resolved, "
            f"{len(escalated)} escalated. "
            f"{woken} human woken, {len(by_contract)} handled under a Sleep Contract."
        )
        if median is not None:
            lines.append(f"Median time to recovery: {median} min.")
        for i in rows:
            how = (
                "handled under your contract, you were not woken"
                if i.get("handled_by") == "contract"
                else "resolved with your approval"
                if i.get("status") == "resolved"
                else "escalated to a human"
                if i.get("status") == "escalated"
                else str(i.get("status"))
            )
            took = (
                _span(i.get("timestamp"), i.get("resolved_at"))
                if i.get("resolved_at")
                else "open"
            )
            lines.append(
                f"- {_hhmm(i.get('timestamp'))} {i.get('alarm_name')}: {how} ({took})."
            )
        lines.append(f"Model cost for the night: ₹{cost:.2f}.")
    subject = f"Beacon morning report · {night} · " + (
        "quiet night" if not rows else f"{len(rows)} incident(s), {woken} woken"
    )
    return {
        "night_of": night,
        "generated_at": ref.isoformat(),
        "incidents": len(rows),
        "resolved": len(resolved),
        "escalated": len(escalated),
        "humans_woken": woken,
        "handled_by_contract": len(by_contract),
        "median_minutes_to_recovery": median,
        "cost_inr": cost,
        "contracts_used": contracts_used,
        "incident_ids": [i.get("incident_id") for i in rows],
        "subject": subject,
        "text": "\n".join(lines),
    }
