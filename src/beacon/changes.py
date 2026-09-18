"""Change ledger: CloudTrail write calls delivered by EventBridge.

``cloudtrail:LookupEvents`` lags minutes; the EventBridge rule on
``AWS API Call via CloudTrail`` delivers much sooner.  ``ledger_handler``
stores each write call as one DynamoDB row so triage can say what changed
right before the alarm.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_TTL_DAYS = 30
_ID_RE = re.compile(
    r"\b(sg-[0-9a-f]+|i-[0-9a-f]+|vpc-[0-9a-f]+|subnet-[0-9a-f]+|eni-[0-9a-f]+)\b"
)
_REMEDIATOR_MARKER = "beacon-remediator-"


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
