from __future__ import annotations

import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import boto3

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

    from beacon.config import BeaconConfig
    from beacon.events import TriggerInfo

logger = logging.getLogger(__name__)

_TTL_DAYS = 7


def put_incident(
    analysis: str,
    trigger: TriggerInfo,
    config: BeaconConfig,
    *,
    dynamodb_client: DynamoDBClient | None = None,
) -> str:
    """Store a new incident record and return its generated UUID.

    Sets ``prefetch_status`` to ``"pending"`` and a 7-day TTL.
    """
    if dynamodb_client is None:
        dynamodb_client = boto3.client("dynamodb")

    incident_id = str(uuid.uuid4())
    ttl = int((datetime.now(tz=UTC) + timedelta(days=_TTL_DAYS)).timestamp())

    item: dict[str, Any] = {
        "incident_id": {"S": incident_id},
        "rca": {"S": analysis},
        "trigger_type": {"S": trigger.trigger_type.value},
        "timestamp": {"S": datetime.now(tz=UTC).isoformat()},
        "ttl": {"N": str(ttl)},
        "prefetch_status": {"S": "pending"},
    }
    if trigger.alarm_name:
        item["alarm_name"] = {"S": trigger.alarm_name}
    if trigger.alarm_reason:
        item["alarm_reason"] = {"S": trigger.alarm_reason}
    if config.log_group_patterns:
        item["log_groups"] = {"L": [{"S": g} for g in config.log_group_patterns]}

    dynamodb_client.put_item(TableName=config.incidents_table_name, Item=item)
    logger.info("Stored incident %s in DynamoDB", incident_id)
    return incident_id


def get_incident(
    incident_id: str,
    config: BeaconConfig,
    *,
    dynamodb_client: DynamoDBClient | None = None,
) -> dict[str, Any]:
    """Read an incident record by ID and deserialize it into a plain dict.

    Returns an empty dict if the item does not exist.  The ``cached_data``
    field is automatically JSON-decoded if present.
    """
    if dynamodb_client is None:
        dynamodb_client = boto3.client("dynamodb")

    resp = dynamodb_client.get_item(
        TableName=config.incidents_table_name,
        Key={"incident_id": {"S": incident_id}},
    )
    raw = resp.get("Item", {})
    return _deserialize_item(raw)


def update_cached_data(
    incident_id: str,
    cached_data: dict[str, Any],
    config: BeaconConfig,
    *,
    status: str = "complete",
    dynamodb_client: DynamoDBClient | None = None,
) -> None:
    """Write pre-fetched investigation data to an incident record.

    Serializes *cached_data* as JSON and sets ``prefetch_status`` to
    *status* (``"complete"`` or ``"failed"``).
    """
    if dynamodb_client is None:
        dynamodb_client = boto3.client("dynamodb")

    dynamodb_client.update_item(
        TableName=config.incidents_table_name,
        Key={"incident_id": {"S": incident_id}},
        UpdateExpression="SET cached_data = :cd, prefetch_status = :ps",
        ExpressionAttributeValues={
            ":cd": {"S": json.dumps(cached_data, default=str)},
            ":ps": {"S": status},
        },
    )
    logger.info("Updated cached data for incident %s (status=%s)", incident_id, status)


def _deserialize_item(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten DynamoDB attribute-value format into plain dicts."""
    result: dict[str, Any] = {}
    for key, value in item.items():
        if "S" in value:
            result[key] = value["S"]
        elif "N" in value:
            result[key] = value["N"]
        elif "L" in value:
            result[key] = [_deserialize_value(v) for v in value["L"]]
        elif "M" in value:
            result[key] = _deserialize_item(value["M"])
        else:
            result[key] = value

    if "cached_data" in result and isinstance(result["cached_data"], str):
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            result["cached_data"] = json.loads(result["cached_data"])

    return result


def _deserialize_value(value: dict[str, Any]) -> Any:
    """Recursively unwrap a single DynamoDB attribute value."""
    if "S" in value:
        return value["S"]
    if "N" in value:
        return value["N"]
    if "L" in value:
        return [_deserialize_value(v) for v in value["L"]]
    if "M" in value:
        return _deserialize_item(value["M"])
    return value
