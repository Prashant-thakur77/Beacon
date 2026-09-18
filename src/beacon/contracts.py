"""Sleep Contracts: standing approvals granted by voice.

A contract lets Beacon run one allowlisted action for one alarm on exactly
the resources named in ``params``, a limited number of times, until it
expires.  The engineer's own words are stored as evidence.  Matching is by
alarm name, action and the params hash; the golden-snapshot check for the
params stays with the caller (``handler``) so this module stays generic.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.exceptions import ClientError

from beacon.approvals import params_hash
from beacon.store import _deserialize_item, _to_dynamo

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _client(dynamodb_client: Any | None) -> Any:
    return dynamodb_client if dynamodb_client is not None else boto3.client("dynamodb")


def put(
    *,
    alarm_name: str,
    action: str,
    params: dict[str, Any],
    days: int,
    max_uses: int,
    transcript_quote: str,
    granted_by: str,
    incident_id: str,
    table_name: str,
    dynamodb_client: Any | None = None,
) -> dict[str, Any]:
    now = _now()
    expires = now + timedelta(days=days)
    record: dict[str, Any] = {
        "contract_id": str(uuid.uuid4()),
        "alarm_name": alarm_name,
        "action": action,
        "params": params,
        "params_hash": params_hash(action, params),
        "granted_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "expires_at_epoch": int(expires.timestamp()),
        "days": days,
        "max_uses": max_uses,
        "uses": 0,
        "status": "active",
        "transcript_quote": transcript_quote,
        "granted_by": granted_by,
        "incident_id": incident_id,
        "last_used_at": None,
    }
    _client(dynamodb_client).put_item(
        TableName=table_name, Item={k: _to_dynamo(v) for k, v in record.items()}
    )
    logger.info(
        "contract %s granted for %s / %s (%d days, %d uses)",
        record["contract_id"],
        alarm_name,
        action,
        days,
        max_uses,
    )
    return record


def _scan(table_name: str, client: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"TableName": table_name}
    while True:
        resp = client.scan(**kwargs)
        items.extend(_deserialize_item(raw) for raw in resp.get("Items", []))
        if not resp.get("LastEvaluatedKey"):
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _is_live(record: dict[str, Any]) -> bool:
    return (
        record.get("status") == "active"
        and str(record.get("expires_at", "")) > _now().isoformat()
        and int(record.get("uses", 0)) < int(record.get("max_uses", 0))
    )


def list_active(
    *, table_name: str, dynamodb_client: Any | None = None
) -> list[dict[str, Any]]:
    rows = [r for r in _scan(table_name, _client(dynamodb_client)) if _is_live(r)]
    return sorted(rows, key=lambda r: str(r.get("granted_at", "")))


def match(
    alarm_name: str,
    action: str,
    params: dict[str, Any],
    *,
    table_name: str,
    dynamodb_client: Any | None = None,
) -> dict[str, Any] | None:
    """The live contract covering exactly this alarm + action + params, if any."""
    wanted = params_hash(action, params)
    for record in list_active(table_name=table_name, dynamodb_client=dynamodb_client):
        if (
            record.get("alarm_name") == alarm_name
            and record.get("action") == action
            and record.get("params_hash") == wanted
        ):
            return record
    return None


def use(
    contract_id: str, *, table_name: str, dynamodb_client: Any | None = None
) -> bool:
    """Atomically consume one use; False if exhausted, expired or revoked."""
    try:
        _client(dynamodb_client).update_item(
            TableName=table_name,
            Key={"contract_id": {"S": contract_id}},
            UpdateExpression="SET #u = #u + :one, last_used_at = :now",
            ConditionExpression="#u < max_uses AND expires_at > :now AND #s = :active",
            ExpressionAttributeNames={"#u": "uses", "#s": "status"},
            ExpressionAttributeValues={
                ":one": {"N": "1"},
                ":now": {"S": _now().isoformat()},
                ":active": {"S": "active"},
            },
        )
    except ClientError as exc:
        if (
            exc.response.get("Error", {}).get("Code")
            == "ConditionalCheckFailedException"
        ):
            return False
        raise
    return True


def revoke(
    contract_id: str, *, table_name: str, dynamodb_client: Any | None = None
) -> None:
    _client(dynamodb_client).update_item(
        TableName=table_name,
        Key={"contract_id": {"S": contract_id}},
        UpdateExpression="SET #s = :revoked, revoked_at = :now",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":revoked": {"S": "revoked"},
            ":now": {"S": _now().isoformat()},
        },
    )
