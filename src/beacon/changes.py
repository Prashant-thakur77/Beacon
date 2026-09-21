"""Change ledger: CloudTrail write calls delivered by EventBridge.

``cloudtrail:LookupEvents`` lags minutes; the EventBridge rule on
``AWS API Call via CloudTrail`` delivers much sooner.  ``ledger_handler``
stores each write call as one DynamoDB row so triage can say what changed
right before the alarm.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

import boto3
from boto3.dynamodb.types import TypeDeserializer

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_TTL_DAYS = 30
_ID_RE = re.compile(
    r"\b(sg-[0-9a-f]+|i-[0-9a-f]+|vpc-[0-9a-f]+|subnet-[0-9a-f]+|eni-[0-9a-f]+)\b"
)
_REMEDIATOR_MARKER = "beacon-remediator-"
_deserializer = TypeDeserializer()


def _ids_in(obj: Any) -> list[str]:
    """Collect AWS resource ids (sg-, i-, vpc-...) from a request payload."""
    found: list[str] = []
    stack = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str):
            found.extend(_ID_RE.findall(current))
    return list(dict.fromkeys(found))


def short_actor(arn: str) -> str:
    """``arn:aws:iam::123:user/prashant`` -> ``user/prashant`` (account id dropped)."""
    if _REMEDIATOR_MARKER in arn:
        return "beacon remediation"
    tail = arn.rsplit(":", 1)[-1]
    if tail.startswith("assumed-role/"):
        parts = tail.split("/")
        return f"role/{parts[1]}" if len(parts) > 1 else tail
    return tail


def ledger_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """EventBridge target: write one row per CloudTrail write API call."""
    if event.get("detail-type") != "AWS API Call via CloudTrail":
        return {"ok": False, "reason": "not a CloudTrail event"}
    detail = event.get("detail") or {}
    if detail.get("readOnly") is True or not detail.get("eventName"):
        return {"ok": False, "reason": "read-only or malformed"}

    table = os.environ.get("CHANGES_TABLE_NAME", "")
    if not table:
        return {"ok": False, "reason": "CHANGES_TABLE_NAME not set"}

    event_time = str(detail.get("eventTime") or event.get("time") or "")
    event_id = str(detail.get("eventID") or event.get("id") or "")
    actor_arn = str((detail.get("userIdentity") or {}).get("arn") or "unknown")
    request_params = detail.get("requestParameters") or {}
    item: dict[str, Any] = {
        "pk": {"S": "change"},
        "sk": {"S": f"{event_time}#{event_id}"},
        "event_name": {"S": str(detail["eventName"])},
        "event_source": {"S": str(detail.get("eventSource", ""))},
        "event_time": {"S": event_time},
        "actor": {"S": actor_arn},
        "actor_short": {"S": short_actor(actor_arn)},
        "by_beacon": {"BOOL": _REMEDIATOR_MARKER in actor_arn},
        "user_agent": {"S": str(detail.get("userAgent", ""))[:200]},
        "source_ip": {"S": str(detail.get("sourceIPAddress", ""))},
        "resource_ids": {"L": [{"S": rid} for rid in _ids_in(request_params)]},
        "error_code": {"S": str(detail.get("errorCode", ""))},
        "ttl": {"N": str(int(time.time()) + _TTL_DAYS * 86400)},
    }
    boto3.client("dynamodb").put_item(TableName=table, Item=item)
    logger.info(
        "ledger: %s by %s at %s",
        detail["eventName"],
        short_actor(actor_arn),
        event_time,
    )
    return {"ok": True, "sk": item["sk"]["S"]}


# ---------------------------------------------------------------------------
# Query side (triage Lambda + voice agent)
# ---------------------------------------------------------------------------

_DESTRUCTIVE_PREFIXES = (
    "Revoke",
    "Delete",
    "Deregister",
    "Terminate",
    "Stop",
    "Modify",
    "Update",
)


def _rank(event_name: str) -> int:
    for i, prefix in enumerate(_DESTRUCTIVE_PREFIXES):
        if event_name.startswith(prefix):
            return i
    return len(_DESTRUCTIVE_PREFIXES)


def _lookup_events(
    minutes: int, before: datetime, region: str | None = None
) -> list[dict[str, Any]]:
    """Slow path: CloudTrail LookupEvents (lags 5-15 min)."""
    client = (
        boto3.client("cloudtrail", region_name=region)
        if region
        else boto3.client("cloudtrail")
    )
    rows: list[dict[str, Any]] = []
    try:
        resp = client.lookup_events(
            LookupAttributes=[{"AttributeKey": "ReadOnly", "AttributeValue": "false"}],
            StartTime=before - timedelta(minutes=minutes),
            EndTime=before,
            MaxResults=50,
        )
    except Exception:
        logger.exception("cloudtrail lookup_events failed")
        return rows
    for ev in resp.get("Events", []):
        try:
            detail = json.loads(ev.get("CloudTrailEvent", "{}"))
        except json.JSONDecodeError:
            detail = {}
        actor = str(
            (detail.get("userIdentity") or {}).get("arn")
            or ev.get("Username")
            or "unknown"
        )
        when = ev.get("EventTime")
        rows.append(
            {
                "event_name": str(ev.get("EventName", "")),
                "event_source": str(ev.get("EventSource", "")),
                "event_time": when.isoformat()
                if hasattr(when, "isoformat")
                else str(when),
                "actor": actor,
                "actor_short": short_actor(actor),
                "by_beacon": _REMEDIATOR_MARKER in actor,
                "resource_ids": [
                    r.get("ResourceName", "")
                    for r in ev.get("Resources", [])
                    if r.get("ResourceName")
                ],
                "user_agent": str(detail.get("userAgent", ""))[:200],
                "source": "cloudtrail-lookup",
            }
        )
    return rows


def recent(
    minutes: int = 60,
    *,
    before: datetime | None = None,
    table_name: str | None = None,
    dynamodb_client: Any | None = None,
    lookup_fallback: bool = False,
    affected_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Write API calls in ``[before - minutes, before]``, most relevant first.

    A change that names one of ``affected_ids`` (the resources diagnostics found
    broken) outranks everything else, so a deploy of an unrelated Lambda never
    hides the revoke that took the rule away. Then most destructive, then time.

    Reads the EventBridge-fed ledger; optionally falls back to
    ``cloudtrail:LookupEvents`` when the ledger has nothing (delivery lag).
    """
    affected = {str(i) for i in affected_ids if i}
    end = before or datetime.now(tz=UTC)
    start = end - timedelta(minutes=minutes)
    table = table_name or os.environ.get("CHANGES_TABLE_NAME", "")
    rows: list[dict[str, Any]] = []
    if table:
        client = (
            dynamodb_client if dynamodb_client is not None else boto3.client("dynamodb")
        )
        kwargs: dict[str, Any] = {
            "TableName": table,
            "KeyConditionExpression": "pk = :pk AND sk BETWEEN :start AND :end",
            "ExpressionAttributeValues": {
                ":pk": {"S": "change"},
                ":start": {"S": start.isoformat()},
                ":end": {"S": end.isoformat() + "~"},
            },
        }
        try:
            while True:
                resp = client.query(**kwargs)
                for raw in resp.get("Items", []):
                    row = {k: _deserializer.deserialize(v) for k, v in raw.items()}
                    row.pop("pk", None)
                    row.pop("ttl", None)
                    row["source"] = "ledger"
                    rows.append(row)
                if not resp.get("LastEvaluatedKey"):
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        except Exception:
            logger.exception("change ledger query failed")
    if not rows and lookup_fallback:
        rows = _lookup_events(minutes, end)
    rows.sort(
        key=lambda r: (
            0 if affected & {str(i) for i in r.get("resource_ids", [])} else 1,
            _rank(str(r.get("event_name", ""))),
            str(r.get("event_time", "")),
        )
    )
    return rows


def _seconds_between(a: str, b: datetime) -> str:
    try:
        delta = (b - datetime.fromisoformat(a.replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return ""
    return (
        f"{int(delta)} s before the alarm"
        if delta >= 0
        else f"{int(-delta)} s after the alarm"
    )


def format_changes(
    rows: list[dict[str, Any]], *, alarm_at: datetime | None = None
) -> str:
    """Prompt-friendly lines for the ``[changes]`` section."""
    if not rows:
        return "No write API calls recorded in the window."
    lines = [
        f"{len(rows)} write API call(s) recorded before the alarm "
        "(most destructive first):"
    ]
    for row in rows[:20]:
        ids = ",".join(str(i) for i in row.get("resource_ids", [])) or "-"
        actor = str(row.get("actor_short", "unknown"))
        tag = " [beacon remediation]" if row.get("by_beacon") else ""
        when = str(row.get("event_time", ""))
        rel = f" ({_seconds_between(when, alarm_at)})" if alarm_at else ""
        lines.append(f"- {when}{rel}: {row.get('event_name')} on {ids} by {actor}{tag}")
    return "\n".join(lines)
