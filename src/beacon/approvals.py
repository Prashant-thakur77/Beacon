"""Approval ledger (``beacon-approvals-<stack>``).

Two kinds of rows share the table:

* **proposals** (``proposal#<incident>#<fix_id>``): what Beacon offered to do,
  with the dry-run result and blast radius; valid for five minutes.
* **approvals** (uuid): the engineer's consent, carrying the verbatim
  transcript, the channel and the exact action + params it covers; valid
  for fifteen minutes and usable exactly once.

The Step Functions ``RequireApproval`` state refuses to execute without a
valid approval row, so approval is a record, not a prompt.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.exceptions import ClientError

from beacon.store import _deserialize_item, _to_dynamo

logger = logging.getLogger(__name__)

PROPOSAL_TTL_SECONDS = 300
APPROVAL_TTL_SECONDS = 900
_ROW_TTL_DAYS = 30


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _client(dynamodb_client: Any | None) -> Any:
    return dynamodb_client if dynamodb_client is not None else boto3.client("dynamodb")


def params_hash(action: str, params: dict[str, Any]) -> str:
    """Stable identity of (action, params) so contracts and approvals can match."""
    canonical = json.dumps(
        {"action": action, "params": params}, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _proposal_key(incident_id: str, fix_id: int) -> str:
    return f"proposal#{incident_id}#{fix_id}"


def _put(table_name: str, record: dict[str, Any], client: Any) -> None:
    item = {key: _to_dynamo(value) for key, value in record.items()}
    client.put_item(TableName=table_name, Item=item)


def _get_raw(table_name: str, approval_id: str, client: Any) -> dict[str, Any] | None:
    resp = client.get_item(
        TableName=table_name, Key={"approval_id": {"S": approval_id}}
    )
    raw = resp.get("Item")
    return _deserialize_item(raw) if raw else None


def create_proposal(
    incident_id: str,
    fix_id: int,
    action: str,
    params: dict[str, Any],
    *,
    blast_radius: str,
    dry_run: dict[str, Any],
    table_name: str,
    dynamodb_client: Any | None = None,
    ttl_seconds: int = PROPOSAL_TTL_SECONDS,
) -> dict[str, Any]:
    now = _now()
    record: dict[str, Any] = {
        "approval_id": _proposal_key(incident_id, fix_id),
        "kind": "proposal",
        "incident_id": incident_id,
        "fix_id": fix_id,
        "action": action,
        "params": params,
        "params_hash": params_hash(action, params),
        "blast_radius": blast_radius,
        "dry_run": dry_run,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        "ttl": int((now + timedelta(days=_ROW_TTL_DAYS)).timestamp()),
    }
    _put(table_name, record, _client(dynamodb_client))
    return record


def withdraw_proposal(
    incident_id: str,
    fix_id: int,
    *,
    reason: str,
    table_name: str,
    dynamodb_client: Any | None = None,
) -> bool:
    """Expire a proposal now (barge-in withdrew it); False if there was none."""
    client = _client(dynamodb_client)
    key = _proposal_key(incident_id, fix_id)
    if (
        get_proposal(incident_id, fix_id, table_name=table_name, dynamodb_client=client)
        is None
    ):
        return False
    client.update_item(
        TableName=table_name,
        Key={"approval_id": {"S": key}},
        UpdateExpression="SET expires_at = :now, withdrawn_reason = :r",
        ExpressionAttributeValues={
            ":now": {"S": _now().isoformat()},
            ":r": {"S": reason[:200]},
        },
    )
    return True


def get_proposal_raw(
    incident_id: str,
    fix_id: int,
    *,
    table_name: str,
    dynamodb_client: Any | None = None,
) -> dict[str, Any] | None:
    """The proposal row whether or not it is still live (to explain a refusal)."""
    return _get_raw(
        table_name, _proposal_key(incident_id, fix_id), _client(dynamodb_client)
    )


def get_proposal(
    incident_id: str,
    fix_id: int,
    *,
    table_name: str,
    dynamodb_client: Any | None = None,
) -> dict[str, Any] | None:
    """Return the proposal if it exists and has not expired."""
    record = _get_raw(
        table_name, _proposal_key(incident_id, fix_id), _client(dynamodb_client)
    )
    if record is None:
        return None
    if str(record.get("expires_at", "")) <= _now().isoformat():
        return None
    return record


def create(
    incident_id: str,
    action: str,
    params: dict[str, Any],
    *,
    source: str,
    channel: str,
    transcript_quote: str,
    table_name: str,
    dynamodb_client: Any | None = None,
    ttl_seconds: int = APPROVAL_TTL_SECONDS,
    fix_id: int | None = None,
    contract_id: str | None = None,
    attestation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record consent for exactly one (action, params) on one incident."""
    now = _now()
    record: dict[str, Any] = {
        "approval_id": str(uuid.uuid4()),
        "kind": "approval",
        "incident_id": incident_id,
        "fix_id": fix_id,
        "action": action,
        "params": params,
        "params_hash": params_hash(action, params),
        "source": source,
        "channel": channel,
        "transcript_quote": transcript_quote,
        "attestation": dict(attestation or {}),
        "contract_id": contract_id,
        "granted_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        "used_at": None,
        "ttl": int((now + timedelta(days=_ROW_TTL_DAYS)).timestamp()),
    }
    _put(table_name, record, _client(dynamodb_client))
    logger.info(
        "approval %s created for %s (%s via %s)",
        record["approval_id"],
        action,
        source,
        channel,
    )
    return record


def get(
    approval_id: str, *, table_name: str, dynamodb_client: Any | None = None
) -> dict[str, Any] | None:
    return _get_raw(table_name, approval_id, _client(dynamodb_client))


def is_valid(record: dict[str, Any], action: str, params: dict[str, Any]) -> str | None:
    """Return None when the approval covers this action/params now, else why not."""
    if record.get("kind") != "approval":
        return "not an approval record"
    if record.get("used_at"):
        return "approval already used"
    if str(record.get("expires_at", "")) <= _now().isoformat():
        return "approval expired"
    if record.get("action") != action:
        return "approval is for a different action"
    if record.get("params_hash") != params_hash(action, params):
        return "approval is for different params"
    return None


def mark_used(
    approval_id: str, *, table_name: str, dynamodb_client: Any | None = None
) -> bool:
    """Atomically stamp ``used_at``; False if it was already used."""
    try:
        _client(dynamodb_client).update_item(
            TableName=table_name,
            Key={"approval_id": {"S": approval_id}},
            UpdateExpression="SET used_at = :now",
            ConditionExpression=(
                "attribute_exists(approval_id) AND "
                "(attribute_not_exists(used_at) OR used_at = :null)"
            ),
            ExpressionAttributeValues={
                ":now": {"S": _now().isoformat()},
                ":null": {"NULL": True},
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


def list_for_incident(
    incident_id: str, *, table_name: str, dynamodb_client: Any | None = None
) -> list[dict[str, Any]]:
    """Every approval row for one incident, oldest first (a scan; rows are few)."""
    client = _client(dynamodb_client)
    rows: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "TableName": table_name,
        "FilterExpression": "incident_id = :i",
        "ExpressionAttributeValues": {":i": {"S": incident_id}},
    }
    while True:
        resp = client.scan(**kwargs)
        rows.extend(_deserialize_item(raw) for raw in resp.get("Items", []))
        if not resp.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    rows.sort(key=lambda r: str(r.get("granted_at") or r.get("created_at") or ""))
    return rows


def record_execution(
    approval_id: str,
    result: dict[str, Any],
    *,
    table_name: str,
    dynamodb_client: Any | None = None,
) -> None:
    """Store the execute outcome on the approval so retries replay it."""
    _client(dynamodb_client).update_item(
        TableName=table_name,
        Key={"approval_id": {"S": approval_id}},
        UpdateExpression="SET execute_result = :r, executed_at = :t",
        ExpressionAttributeValues={
            ":r": _to_dynamo(result),
            ":t": {"S": str(result.get("executed_at") or _now().isoformat())},
        },
    )
