from __future__ import annotations

import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

    from beacon.config import BeaconConfig
    from beacon.events import TriggerInfo

logger = logging.getLogger(__name__)

_TTL_DAYS = 30
_TERMINAL_STATUSES = ("resolved", "escalated", "closed")

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _client(dynamodb_client: DynamoDBClient | None) -> DynamoDBClient:
    return dynamodb_client if dynamodb_client is not None else boto3.client("dynamodb")


def _resolve_table(config: BeaconConfig | None, table_name: str | None) -> str:
    """Return the explicit table name, else the one from config.

    New Lambdas (voice, remediation, dashboard) pass ``table_name`` and never
    build a :class:`BeaconConfig`, which requires the triage env vars.
    """
    if table_name:
        return table_name
    if config is not None and config.incidents_table_name:
        return config.incidents_table_name
    raise ValueError("A DynamoDB table name is required (table_name or config)")


def _to_dynamo(value: Any) -> Any:
    """Serialize a JSON-like value to DynamoDB attribute-value format.

    Floats are converted to ``Decimal`` (DynamoDB rejects float).
    """
    normalised = json.loads(json.dumps(value, default=str), parse_float=Decimal)
    return _serializer.serialize(normalised)


def put_incident(
    analysis: str,
    trigger: TriggerInfo,
    config: BeaconConfig | None = None,
    *,
    dynamodb_client: DynamoDBClient | None = None,
    table_name: str | None = None,
    rca_json: dict[str, Any] | None = None,
    timeline: list[dict[str, Any]] | None = None,
    diagnostics: dict[str, Any] | None = None,
    changes: list[dict[str, Any]] | None = None,
    status: str = "awaiting_engineer",
    woken: bool = True,
) -> str:
    """Store a new incident record and return its generated UUID.

    Sets ``prefetch_status`` to ``"pending"``, a 30-day TTL, the lifecycle
    ``status`` and the structured ``rca_json``/``timeline`` used by the
    dashboard and the voice agent.
    """
    client = _client(dynamodb_client)
    table = _resolve_table(config, table_name)

    incident_id = str(uuid.uuid4())
    now = _now()
    ttl = int((now + timedelta(days=_TTL_DAYS)).timestamp())

    item: dict[str, Any] = {
        "incident_id": {"S": incident_id},
        "rca": {"S": analysis},
        "trigger_type": {"S": trigger.trigger_type.value},
        "timestamp": {"S": now.isoformat()},
        "ttl": {"N": str(ttl)},
        "prefetch_status": {"S": "pending"},
        "status": {"S": status},
        "woken": {"BOOL": woken},
        "timeline": _to_dynamo(timeline or []),
    }
    if trigger.alarm_name:
        item["alarm_name"] = {"S": trigger.alarm_name}
    if trigger.alarm_reason:
        item["alarm_reason"] = {"S": trigger.alarm_reason}
    if config is not None and config.log_group_patterns:
        item["log_groups"] = {"L": [{"S": g} for g in config.log_group_patterns]}
    if rca_json is not None:
        item["rca_json"] = _to_dynamo(rca_json)
    if diagnostics is not None:
        item["diagnostics"] = _to_dynamo(diagnostics)
    if changes is not None:
        item["changes"] = _to_dynamo(changes)

    client.put_item(TableName=table, Item=item)
    logger.info("Stored incident %s in DynamoDB", incident_id)
    return incident_id


def get_incident(
    incident_id: str,
    config: BeaconConfig | None = None,
    *,
    table_name: str | None = None,
    dynamodb_client: DynamoDBClient | None = None,
) -> dict[str, Any]:
    """Read an incident record by ID and deserialize it into a plain dict.

    Returns an empty dict if the item does not exist.  The ``cached_data``
    field is automatically JSON-decoded if present.
    """
    client = _client(dynamodb_client)
    resp = client.get_item(
        TableName=_resolve_table(config, table_name),
        Key={"incident_id": {"S": incident_id}},
    )
    return _deserialize_item(resp.get("Item", {}))


def append_timeline(
    incident_id: str,
    event: str,
    *,
    table_name: str,
    detail: dict[str, Any] | None = None,
    dynamodb_client: DynamoDBClient | None = None,
) -> dict[str, Any]:
    """Append one ``{t, event, detail?}`` entry to the incident timeline."""
    entry: dict[str, Any] = {"t": _now().isoformat(), "event": event}
    if detail:
        entry["detail"] = detail
    _client(dynamodb_client).update_item(
        TableName=table_name,
        Key={"incident_id": {"S": incident_id}},
        UpdateExpression="SET #tl = list_append(if_not_exists(#tl, :empty), :entry)",
        ExpressionAttributeNames={"#tl": "timeline"},
        ExpressionAttributeValues={
            ":empty": {"L": []},
            ":entry": {"L": [_to_dynamo(entry)]},
        },
    )
    return entry


def update_status(
    incident_id: str,
    status: str,
    *,
    table_name: str,
    extra: dict[str, Any] | None = None,
    dynamodb_client: DynamoDBClient | None = None,
) -> None:
    """Set the lifecycle ``status`` plus any extra top-level attributes."""
    names = {"#status": "status"}
    values: dict[str, Any] = {":status": {"S": status}}
    sets = ["#status = :status"]
    for i, (key, value) in enumerate((extra or {}).items()):
        names[f"#e{i}"] = key
        values[f":e{i}"] = _to_dynamo(value)
        sets.append(f"#e{i} = :e{i}")
    _client(dynamodb_client).update_item(
        TableName=table_name,
        Key={"incident_id": {"S": incident_id}},
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def add_usage(
    incident_id: str,
    usage: dict[str, int],
    *,
    table_name: str,
    dynamodb_client: DynamoDBClient | None = None,
) -> None:
    """Atomically add token counts to ``usage.<key>`` on the incident."""
    parts = {k: int(v) for k, v in usage.items() if v}
    if not parts:
        return
    names = {"#u": "usage"}
    values: dict[str, Any] = {":zero": {"N": "0"}, ":empty": {"M": {}}}
    sets = []
    for i, (key, value) in enumerate(parts.items()):
        names[f"#k{i}"] = key
        values[f":v{i}"] = {"N": str(value)}
        sets.append(f"#u.#k{i} = if_not_exists(#u.#k{i}, :zero) + :v{i}")
    client = _client(dynamodb_client)
    key_expr = {"incident_id": {"S": incident_id}}
    client.update_item(
        TableName=table_name,
        Key=key_expr,
        UpdateExpression="SET #u = if_not_exists(#u, :empty)",
        ExpressionAttributeNames={"#u": "usage"},
        ExpressionAttributeValues={":empty": {"M": {}}},
    )
    client.update_item(
        TableName=table_name,
        Key=key_expr,
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues={k: v for k, v in values.items() if k != ":empty"},
    )


def find_open_incident(
    alarm_name: str,
    *,
    table_name: str,
    within_minutes: int = 10,
    dynamodb_client: DynamoDBClient | None = None,
) -> dict[str, Any] | None:
    """Return the newest non-terminal incident for *alarm_name* in the window.

    Used to deduplicate alarm re-evaluations (and ``set-alarm-state`` flaps)
    so one outage produces one incident.  The table is small, so a filtered
    scan is fine.
    """
    since = (_now() - timedelta(minutes=within_minutes)).isoformat()
    client = _client(dynamodb_client)
    kwargs: dict[str, Any] = {
        "TableName": table_name,
        "FilterExpression": (
            "alarm_name = :alarm AND #ts >= :since AND NOT (#st IN (:r, :e, :c))"
        ),
        "ExpressionAttributeNames": {"#ts": "timestamp", "#st": "status"},
        "ExpressionAttributeValues": {
            ":alarm": {"S": alarm_name},
            ":since": {"S": since},
            ":r": {"S": _TERMINAL_STATUSES[0]},
            ":e": {"S": _TERMINAL_STATUSES[1]},
            ":c": {"S": _TERMINAL_STATUSES[2]},
        },
    }
    items: list[dict[str, Any]] = []
    while True:
        resp = client.scan(**kwargs)
        items.extend(_deserialize_item(raw) for raw in resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    if not items:
        return None
    return max(items, key=lambda item: str(item.get("timestamp", "")))


def update_cached_data(
    incident_id: str,
    cached_data: dict[str, Any],
    config: BeaconConfig | None = None,
    *,
    status: str = "complete",
    table_name: str | None = None,
    dynamodb_client: DynamoDBClient | None = None,
) -> None:
    """Write pre-fetched investigation data to an incident record.

    Serializes *cached_data* as JSON and sets ``prefetch_status`` to
    *status* (``"complete"`` or ``"failed"``).
    """
    _client(dynamodb_client).update_item(
        TableName=_resolve_table(config, table_name),
        Key={"incident_id": {"S": incident_id}},
        UpdateExpression="SET cached_data = :cd, prefetch_status = :ps",
        ExpressionAttributeValues={
            ":cd": {"S": json.dumps(cached_data, default=str)},
            ":ps": {"S": status},
        },
    )
    logger.info("Updated cached data for incident %s (status=%s)", incident_id, status)


def _from_decimal(value: Any) -> Any:
    """Turn DynamoDB ``Decimal`` numbers back into int/float recursively."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [_from_decimal(v) for v in value]
    if isinstance(value, dict):
        return {k: _from_decimal(v) for k, v in value.items()}
    return value


def _deserialize_item(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten DynamoDB attribute-value format into plain dicts.

    Numbers come back as int/float, except ``ttl`` which stays a string for
    backwards compatibility with callers that ``int()`` it.
    """
    result: dict[str, Any] = {}
    for key, value in item.items():
        plain = _from_decimal(_deserializer.deserialize(value))
        if key == "ttl":
            plain = str(plain)
        result[key] = plain

    if "cached_data" in result and isinstance(result["cached_data"], str):
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            result["cached_data"] = json.loads(result["cached_data"])

    return result
