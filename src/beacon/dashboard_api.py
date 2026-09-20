"""Read-only dashboard API behind a Function URL (``beacon-dashboard-<stack>``).

Public reads for the console.  Every response goes through :func:`redact`
so the account id and full actor ARNs never leave the account.  The only
mutation is revoking a Sleep Contract, which needs the passcode.
"""

from __future__ import annotations

import json
import logging
import os
import re
import statistics
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from importlib import metadata
from typing import Any

import boto3
from aws_lambda_powertools.event_handler import (
    LambdaFunctionUrlResolver,
    Response,
)

from beacon import contracts, store
from beacon.remediation import registry

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

SERVICE = "beacon-dashboard"
_ACCOUNT_RE = re.compile(r"\b\d{12}\b")
_ARN_RE = re.compile(
    r"arn:aws:(?:iam|sts)::\d{12}:(assumed-role/([^/\s\"]+)/[^\s\"]*|[^\s\"]+)"
)
_PRIVATE_FIELDS = ("conversation", "rca", "cached_data")

SAFETY_RULES = [
    "Only allowlisted actions can run; params must match the schema exactly.",
    "Security-group restores must exist in the golden snapshot taken on a healthy stack.",  # noqa: E501
    "Every action is dry-run under the write-only remediator role before execution.",
    "Approval is checked against the engineer's raw transcript, never the model's claim.",  # noqa: E501
    "One approval executes exactly once (Powertools idempotency on approval_id).",
    "Verification needs alarm OK after the fix, error metric zero, and the post-condition.",  # noqa: E501
    "Sleep Contracts are scoped to alarm + action + exact resources, expire, and count uses.",  # noqa: E501
    "APPLY_ENABLED=false stops every write path: triage contract branch, voice approvals, Execute.",  # noqa: E501
]


# CORS lives on the Function URL (console-template.yaml), scoped to the console
# origin; setting it here too would duplicate the headers in every response.
app = LambdaFunctionUrlResolver()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _json(status: int, body: dict[str, Any]) -> Response[str]:
    return Response(
        status_code=status,
        content_type="application/json",
        body=json.dumps(redact(body), default=str),
    )


def _passcode_ok(headers: dict[str, str]) -> bool:
    expected = _env("PASSCODE")
    if not expected:
        return True
    given = headers.get("x-beacon-passcode") or headers.get("X-Beacon-Passcode") or ""
    return given == expected


def _redact_str(value: str) -> str:
    def _arn(match: re.Match[str]) -> str:
        if match.group(2):
            return f"role/{match.group(2)}"
        return match.group(1)

    value = _ARN_RE.sub(_arn, value)
    return _ACCOUNT_RE.sub("************", value)


def redact(value: Any) -> Any:
    """Strip account ids and reduce IAM/STS ARNs to ``type/name`` recursively."""
    if isinstance(value, str):
        return _redact_str(value)
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    return value


def _public(incident: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in incident.items() if k not in _PRIVATE_FIELDS}


_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_SECONDS = 2.0


def _scan_incidents(table: str) -> list[dict[str, Any]]:
    client = boto3.client("dynamodb")
    kwargs: dict[str, Any] = {"TableName": table}
    rows: list[dict[str, Any]] = []
    while True:
        resp = client.scan(**kwargs)
        rows.extend(store._deserialize_item(raw) for raw in resp.get("Items", []))
        if not resp.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    rows.sort(key=lambda r: str(r.get("timestamp", "")), reverse=True)
    return rows


def _all_incidents() -> list[dict[str, Any]]:
    """Incident list with a per-container cache: many viewers poll every 3 s."""
    table = _env("INCIDENTS_TABLE_NAME")
    now = time.monotonic()
    hit = _cache.get(table)
    if hit and now - hit[0] < _CACHE_SECONDS:
        return hit[1]
    rows = _scan_incidents(table)
    _cache[table] = (now, rows)
    return rows


def _minutes(start: Any, end: Any) -> float | None:
    try:
        a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((b - a).total_seconds() / 60, 1)


# Nova 2 Lite list price (USD per 1M tokens) and a fixed conversion, so the
# tally shows an honest order of magnitude, not a bill.  Both are env-tunable.
def _price(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def cost_inr(usage: dict[str, Any] | None) -> float:
    """Rupees for one incident's model usage (input, output, embedding tokens)."""
    if not usage:
        return 0.0
    usd = (
        float(usage.get("input_tokens", 0)) / 1e6 * _price("PRICE_INPUT_PER_M", 0.06)
        + float(usage.get("output_tokens", 0))
        / 1e6
        * _price("PRICE_OUTPUT_PER_M", 0.24)
        + float(usage.get("embedding_tokens", 0))
        / 1e6
        * _price("PRICE_EMBED_PER_M", 0.02)
    )
    return round(usd * _price("USD_INR", 84.0), 4)


def _is_night_ist(iso: str) -> bool:
    """22:00-07:00 IST: the hours a page costs sleep."""
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return False
    minutes = when.hour * 60 + when.minute + 330  # UTC -> IST
    hour = (minutes // 60) % 24
    return hour >= 22 or hour < 7


def _parse(iso: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _night_of(iso: Any) -> str | None:
    """Label the night an incident belongs to: IST date, with the day turning
    over at noon so 23:30 and 03:00 the next morning are the same night."""
    when = _parse(iso)
    if when is None:
        return None
    return (when + timedelta(hours=5, minutes=30) - timedelta(hours=12)).strftime(
        "%Y-%m-%d"
    )


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = (len(ordered) - 1) * pct
    lo, hi = int(idx), min(int(idx) + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo), 2)


def _seconds_to_first_proposal(row: dict[str, Any]) -> float | None:
    """Alarm to the first dry-run fix on the table, from the timeline."""
    start = _parse(row.get("timestamp"))
    if start is None:
        return None
    for ev in row.get("timeline") or []:
        if ev.get("event") == "fix_proposed":
            at = _parse(ev.get("t"))
            if at is not None:
                return max(0.0, round((at - start).total_seconds(), 1))
    return None


def _version() -> str:
    explicit = _env("BEACON_VERSION")
    if explicit:
        return explicit
    try:
        return metadata.version("beacon")
    except metadata.PackageNotFoundError:
        return "dev"


def build_analytics(
    rows: list[dict[str, Any]], live_contracts: list[dict[str, Any]]
) -> dict[str, Any]:
    """Operations analytics from the incident rows and the live contracts.

    Pure, so the console can mirror it client-side in replay mode and the
    tests can pin the numbers without a table.
    """
    nights: dict[str, dict[str, Any]] = {}
    per_incident: list[dict[str, Any]] = []
    ttr_all: list[float] = []
    proposal_secs: list[float] = []
    cumulative = 0.0
    for row in sorted(rows, key=lambda r: str(r.get("timestamp", ""))):
        night = _night_of(row.get("timestamp")) or "unknown"
        bucket = nights.setdefault(
            night,
            {
                "night": night,
                "incidents": 0,
                "resolved": 0,
                "escalated": 0,
                "woken": 0,
                "under_contract": 0,
                "cost_inr": 0.0,
                "_ttr": [],
            },
        )
        status = str(row.get("status", ""))
        ttr = (
            _minutes(row.get("timestamp"), row.get("resolved_at"))
            if status == "resolved" and row.get("resolved_at")
            else None
        )
        cost = cost_inr(row.get("usage"))
        cumulative = round(cumulative + cost, 4)
        under_contract = row.get("handled_by") == "contract"
        woken = bool(row.get("woken", True))
        bucket["incidents"] += 1
        bucket["resolved"] += status == "resolved"
        bucket["escalated"] += status == "escalated"
        bucket["woken"] += woken
        bucket["under_contract"] += under_contract
        bucket["cost_inr"] = round(bucket["cost_inr"] + cost, 4)
        if ttr is not None:
            bucket["_ttr"].append(ttr)
            ttr_all.append(ttr)
        first = _seconds_to_first_proposal(row)
        if first is not None:
            proposal_secs.append(first)
        per_incident.append(
            {
                "incident_id": row.get("incident_id"),
                "alarm_name": row.get("alarm_name"),
                "timestamp": row.get("timestamp"),
                "night": night,
                "status": status,
                "woken": woken,
                "under_contract": under_contract,
                "minutes_to_recovery": ttr,
                "seconds_to_first_proposal": first,
                "cost_inr": cost,
                "cost_inr_cumulative": cumulative,
            }
        )
    night_rows = []
    for bucket in nights.values():
        ttrs = bucket.pop("_ttr")
        bucket["median_minutes_to_recovery"] = _percentile(ttrs, 0.5)
        bucket["p90_minutes_to_recovery"] = _percentile(ttrs, 0.9)
        night_rows.append(bucket)
    night_rows.sort(key=lambda b: b["night"])
    alarms = Counter(str(r.get("alarm_name") or "unknown") for r in rows)
    outcomes = Counter(str(r.get("status", "")) for r in rows)
    now = datetime.now(tz=UTC)
    contract_rows = []
    for c in live_contracts:
        expires = _parse(c.get("expires_at"))
        contract_rows.append(
            {
                "contract_id": c.get("contract_id"),
                "alarm_name": c.get("alarm_name"),
                "action": c.get("action"),
                "uses": int(c.get("uses") or 0),
                "max_uses": int(c.get("max_uses") or 0),
                "uses_left": max(
                    0, int(c.get("max_uses") or 0) - int(c.get("uses") or 0)
                ),
                "expires_at": c.get("expires_at"),
                "hours_left": round((expires - now).total_seconds() / 3600, 1)
                if expires
                else None,
            }
        )
    return {
        "generated_at": now.isoformat(),
        "nights": night_rows,
        "incidents": per_incident,
        "recovery": {
            "count": len(ttr_all),
            "p50_minutes": _percentile(ttr_all, 0.5),
            "p90_minutes": _percentile(ttr_all, 0.9),
            "max_minutes": max(ttr_all) if ttr_all else None,
        },
        "first_proposal": {
            "count": len(proposal_secs),
            "mean_seconds": round(statistics.fmean(proposal_secs), 1)
            if proposal_secs
            else None,
        },
        "outcomes": {
            "resolved": outcomes.get("resolved", 0),
            "escalated": outcomes.get("escalated", 0),
            "in_progress": sum(
                v for k, v in outcomes.items() if k not in ("resolved", "escalated")
            ),
        },
        "humans": {
            "woken": sum(1 for r in rows if r.get("woken", True)),
            "under_contract": sum(1 for r in rows if r.get("handled_by") == "contract"),
        },
        "cost": {
            "total_inr": cumulative,
            "per_incident_inr": round(cumulative / len(rows), 4) if rows else 0.0,
        },
        "top_alarms": [
            {"alarm_name": name, "count": n} for name, n in alarms.most_common(5)
        ],
        "contracts": contract_rows,
    }


def _alarm_metric_series(alarm_name: str, minutes: int = 30) -> list[dict[str, Any]]:
    """Datapoints of the alarm's own metric (what Verify looks at), oldest first."""
    cw = boto3.client("cloudwatch")
    alarms = cw.describe_alarms(AlarmNames=[alarm_name]).get("MetricAlarms", [])
    if not alarms:
        return []
    alarm = alarms[0]
    end = datetime.now(tz=UTC)
    stat = alarm.get("Statistic", "Sum")
    resp = cw.get_metric_statistics(
        Namespace=alarm["Namespace"],
        MetricName=alarm["MetricName"],
        Dimensions=alarm.get("Dimensions", []),
        StartTime=end - timedelta(minutes=minutes),
        EndTime=end,
        Period=int(alarm.get("Period") or 60),
        Statistics=[stat],
    )
    points = sorted(resp.get("Datapoints", []), key=lambda p: p["Timestamp"])
    return [
        {"t": p["Timestamp"].isoformat(), "v": float(p.get(stat, 0) or 0)}
        for p in points
    ]


def _apply_flags() -> dict[str, bool | None]:
    """APPLY_ENABLED as deployed on the three write-path functions."""
    stack = _env("BASE_STACK_NAME", "beacon")
    client = boto3.client("lambda")
    out: dict[str, bool | None] = {}
    for label, fn in (
        ("triage", f"beacon-{stack}"),
        ("voice", f"beacon-voice-turn-{stack}"),
        ("remediate", f"beacon-remediate-{stack}"),
    ):
        try:
            env = (
                client.get_function_configuration(FunctionName=fn)
                .get("Environment", {})
                .get("Variables", {})
            )
            out[label] = str(env.get("APPLY_ENABLED", "true")).lower() != "false"
        except Exception:
            out[label] = None
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": SERVICE,
        "version": _version(),
        "region": _env("AWS_REGION", _env("AWS_DEFAULT_REGION", "")),
        "stack": _env("BASE_STACK_NAME", ""),
    }


@app.get("/incidents")
def incidents() -> Response[str]:
    rows = [_public(r) for r in _all_incidents()[:50]]
    return _json(200, {"incidents": rows})


@app.get("/incidents/<incident_id>")
def incident(incident_id: str) -> Response[str]:
    row = store.get_incident(incident_id, table_name=_env("INCIDENTS_TABLE_NAME"))
    if not row:
        return _json(404, {"error": "not found"})
    return _json(200, {"incident": _public(row)})


@app.get("/incidents/<incident_id>/execution")
def execution(incident_id: str) -> Response[str]:
    row = store.get_incident(incident_id, table_name=_env("INCIDENTS_TABLE_NAME"))
    if not row:
        return _json(404, {"error": "not found"})
    arn = str(row.get("execution_arn") or "")
    events: list[dict[str, Any]] = []
    status = None
    if arn:
        try:
            sfn = boto3.client("stepfunctions")
            status = sfn.describe_execution(executionArn=arn).get("status")
            history = sfn.get_execution_history(
                executionArn=arn, maxResults=200, reverseOrder=False
            )
            for ev in history.get("events", []):
                name = None
                for key in ("stateEnteredEventDetails", "stateExitedEventDetails"):
                    if key in ev:
                        name = ev[key].get("name")
                events.append(
                    {"t": ev.get("timestamp"), "type": ev.get("type"), "state": name}
                )
        except Exception as exc:
            logger.warning("execution history unavailable: %s", exc)
    return _json(200, {"execution_arn": arn, "status": status, "events": events})


@app.get("/incidents/<incident_id>/metric")
def incident_metric(incident_id: str) -> Response[str]:
    row = store.get_incident(incident_id, table_name=_env("INCIDENTS_TABLE_NAME"))
    if not row:
        return _json(404, {"error": "not found"})
    alarm_name = str(row.get("alarm_name") or "")
    points: list[dict[str, Any]] = []
    if alarm_name:
        try:
            points = _alarm_metric_series(alarm_name)
        except Exception as exc:
            logger.warning("metric series unavailable: %s", exc)
    return _json(
        200,
        {
            "alarm_name": alarm_name,
            "metric": {"namespace": "BeaconDemoInfra", "metric_name": "ErrorCount"},
            "points": points,
            "executed_at": row.get("executed_at"),
            "resolved_at": row.get("resolved_at"),
        },
    )


@app.get("/tally")
def tally() -> Response[str]:
    rows = _all_incidents()
    resolved = [r for r in rows if r.get("status") == "resolved"]
    durations = [
        m
        for m in (_minutes(r.get("timestamp"), r.get("resolved_at")) for r in resolved)
        if m is not None
    ]
    costs = [cost_inr(r.get("usage")) for r in rows]
    night_not_woken = [
        r
        for r in rows
        if r.get("woken") is False and _is_night_ist(str(r.get("timestamp", "")))
    ]
    # each night incident handled without a page protects ~1 h of sleep
    # (the time an engineer typically stays up after a 3 AM page)
    per_page = float(os.environ.get("SLEEP_HOURS_PER_PAGE", "1.0"))
    return _json(
        200,
        {
            "incidents_handled": len(rows),
            "resolved": len(resolved),
            "escalated": sum(1 for r in rows if r.get("status") == "escalated"),
            "humans_woken": sum(1 for r in rows if r.get("woken", True)),
            "handled_by_contract": sum(
                1 for r in rows if r.get("handled_by") == "contract"
            ),
            "median_minutes_to_recovery": statistics.median(durations)
            if durations
            else None,
            "cost_inr_total": round(sum(costs), 4),
            "cost_inr_per_incident": round(sum(costs) / len(rows), 4) if rows else 0.0,
            "night_incidents_not_woken": len(night_not_woken),
            "sleep_protected_hours": round(len(night_not_woken) * per_page, 1),
        },
    )


@app.get("/analytics")
def analytics() -> Response[str]:
    live = contracts.list_active(table_name=_env("CONTRACTS_TABLE_NAME"))
    return _json(200, build_analytics(_all_incidents(), live))


@app.get("/contracts")
def list_contracts() -> Response[str]:
    rows = contracts.list_active(table_name=_env("CONTRACTS_TABLE_NAME"))
    out = [
        {
            "contract_id": r["contract_id"],
            "alarm_name": r.get("alarm_name"),
            "action": r.get("action"),
            "scope": r.get("params"),
            "uses": r.get("uses"),
            "max_uses": r.get("max_uses"),
            "granted_at": r.get("granted_at"),
            "expires_at": r.get("expires_at"),
            "transcript_quote": r.get("transcript_quote"),
            "granted_by": r.get("granted_by"),
            "incident_id": r.get("incident_id"),
        }
        for r in rows
    ]
    return _json(200, {"contracts": out})


@app.delete("/contracts/<contract_id>")
def revoke_contract(contract_id: str) -> Response[str]:
    if not _passcode_ok(dict(app.current_event.headers)):
        return _json(401, {"error": "passcode required"})
    contracts.revoke(contract_id, table_name=_env("CONTRACTS_TABLE_NAME"))
    return _json(200, {"ok": True, "contract_id": contract_id})


@app.get("/safety")
def safety() -> Response[str]:
    allowlist = [
        {
            "id": spec.id,
            "description": spec.description,
            "params": {k: t.__name__ for k, t in spec.params_schema.items()},
            "iam_actions": list(spec.iam_actions),
        }
        for spec in registry.REGISTRY.values()
    ]
    return _json(
        200,
        {
            "allowlist": allowlist,
            "apply_enabled": _apply_flags(),
            "rules": SAFETY_RULES,
        },
    )


def handler(event: dict[str, Any], context: Any) -> Any:
    if isinstance(event, dict) and event.get("mode") == "warm":
        return {"ok": True, "warm": True}
    return app.resolve(event, context)
