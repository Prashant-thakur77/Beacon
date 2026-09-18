from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from beacon.config import BeaconConfig
from beacon.events import TriggerInfo, TriggerType
from beacon.store import get_incident, put_incident, update_cached_data


@pytest.fixture()
def _dynamodb_table(voice_config: BeaconConfig):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=voice_config.incidents_table_name,
            KeySchema=[{"AttributeName": "incident_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "incident_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


def test_put_and_get_incident(_dynamodb_table, voice_config: BeaconConfig):
    trigger = TriggerInfo(
        trigger_type=TriggerType.ALARM,
        alarm_name="HighCPU",
        alarm_reason="Threshold breached",
    )
    incident_id = put_incident(
        "STATUS: High\nSUMMARY: CPU spike",
        trigger,
        voice_config,
        dynamodb_client=_dynamodb_table,
    )
    assert incident_id

    incident = get_incident(incident_id, voice_config, dynamodb_client=_dynamodb_table)
    assert incident["rca"] == "STATUS: High\nSUMMARY: CPU spike"
    assert incident["alarm_name"] == "HighCPU"
    assert incident["trigger_type"] == "alarm"
    assert incident["prefetch_status"] == "pending"


def test_update_cached_data(_dynamodb_table, voice_config: BeaconConfig):
    trigger = TriggerInfo(trigger_type=TriggerType.ALARM, alarm_name="Test")
    incident_id = put_incident(
        "STATUS: Medium\nSUMMARY: test",
        trigger,
        voice_config,
        dynamodb_client=_dynamodb_table,
    )

    cached = {
        "metrics": [{"query_key": "CPU for web-server", "value": 85}],
        "logs": [],
        "status": [],
    }
    update_cached_data(
        incident_id,
        cached,
        voice_config,
        status="complete",
        dynamodb_client=_dynamodb_table,
    )

    incident = get_incident(incident_id, voice_config, dynamodb_client=_dynamodb_table)
    assert incident["prefetch_status"] == "complete"
    assert isinstance(incident["cached_data"], dict)
    assert incident["cached_data"]["metrics"][0]["query_key"] == "CPU for web-server"


def test_get_nonexistent_incident(_dynamodb_table, voice_config: BeaconConfig):
    incident = get_incident(
        "does-not-exist", voice_config, dynamodb_client=_dynamodb_table
    )
    assert incident == {}


def _alarm_trigger(name: str = "HighCPU") -> TriggerInfo:
    return TriggerInfo(trigger_type=TriggerType.ALARM, alarm_name=name)


def test_put_incident_stores_status_rca_json_timeline_and_30_day_ttl(
    _dynamodb_table, voice_config: BeaconConfig
):
    import time

    from beacon.store import get_incident, put_incident

    incident_id = put_incident(
        "STATUS: High\nSUMMARY: CPU spike",
        _alarm_trigger(),
        voice_config,
        dynamodb_client=_dynamodb_table,
        rca_json={"status": "High", "summary": "CPU spike", "beacon_json": {}},
        timeline=[{"t": "2026-09-18T20:00:00+00:00", "event": "alarm_received"}],
        diagnostics={"missing_rules": []},
        changes=[{"event_name": "RevokeSecurityGroupIngress"}],
    )
    incident = get_incident(incident_id, voice_config, dynamodb_client=_dynamodb_table)
    assert incident["status"] == "awaiting_engineer"
    assert incident["rca_json"]["status"] == "High"
    assert incident["timeline"][0]["event"] == "alarm_received"
    assert incident["diagnostics"] == {"missing_rules": []}
    assert incident["changes"][0]["event_name"] == "RevokeSecurityGroupIngress"
    assert incident["woken"] is True
    ttl_days = (int(incident["ttl"]) - time.time()) / 86400
    assert 29.9 < ttl_days < 30.1


def test_append_timeline_adds_event_with_timestamp_and_detail(
    _dynamodb_table, voice_config: BeaconConfig
):
    from beacon.store import append_timeline, get_incident, put_incident

    table = voice_config.incidents_table_name
    incident_id = put_incident(
        "STATUS: High\nSUMMARY: x",
        _alarm_trigger(),
        voice_config,
        dynamodb_client=_dynamodb_table,
    )
    append_timeline(
        incident_id,
        "rca_ready",
        table_name=table,
        detail={"status": "High"},
        dynamodb_client=_dynamodb_table,
    )
    append_timeline(
        incident_id, "sns_sent", table_name=table, dynamodb_client=_dynamodb_table
    )
    incident = get_incident(
        incident_id, table_name=table, dynamodb_client=_dynamodb_table
    )
    events = [e["event"] for e in incident["timeline"]]
    assert events[-2:] == ["rca_ready", "sns_sent"]
    assert incident["timeline"][-2]["detail"] == {"status": "High"}
    assert incident["timeline"][-1]["t"].startswith("20")


def test_update_status_sets_status_and_extra_attributes(
    _dynamodb_table, voice_config: BeaconConfig
):
    from beacon.store import get_incident, put_incident, update_status

    table = voice_config.incidents_table_name
    incident_id = put_incident(
        "STATUS: High\nSUMMARY: x",
        _alarm_trigger(),
        voice_config,
        dynamodb_client=_dynamodb_table,
    )
    update_status(
        incident_id,
        "resolved",
        table_name=table,
        extra={"resolved_at": "2026-09-18T20:05:00+00:00", "handled_by": "voice"},
        dynamodb_client=_dynamodb_table,
    )
    incident = get_incident(
        incident_id, table_name=table, dynamodb_client=_dynamodb_table
    )
    assert incident["status"] == "resolved"
    assert incident["resolved_at"] == "2026-09-18T20:05:00+00:00"
    assert incident["handled_by"] == "voice"


def test_find_open_incident_returns_recent_non_terminal_for_same_alarm(
    _dynamodb_table, voice_config: BeaconConfig
):
    from beacon.store import find_open_incident, put_incident, update_status

    table = voice_config.incidents_table_name
    assert (
        find_open_incident("HighCPU", table_name=table, dynamodb_client=_dynamodb_table)
        is None
    )

    open_id = put_incident(
        "STATUS: High\nSUMMARY: x",
        _alarm_trigger("HighCPU"),
        voice_config,
        dynamodb_client=_dynamodb_table,
    )
    found = find_open_incident(
        "HighCPU", table_name=table, dynamodb_client=_dynamodb_table
    )
    assert found is not None and found["incident_id"] == open_id

    assert (
        find_open_incident(
            "OtherAlarm", table_name=table, dynamodb_client=_dynamodb_table
        )
        is None
    )

    update_status(
        open_id, "resolved", table_name=table, dynamodb_client=_dynamodb_table
    )
    assert (
        find_open_incident("HighCPU", table_name=table, dynamodb_client=_dynamodb_table)
        is None
    )


def test_find_open_incident_ignores_incidents_older_than_window(
    _dynamodb_table, voice_config: BeaconConfig
):
    from beacon.store import find_open_incident, put_incident

    table = voice_config.incidents_table_name
    old_id = put_incident(
        "STATUS: High\nSUMMARY: x",
        _alarm_trigger("HighCPU"),
        voice_config,
        dynamodb_client=_dynamodb_table,
    )
    _dynamodb_table.update_item(
        TableName=table,
        Key={"incident_id": {"S": old_id}},
        UpdateExpression="SET #ts = :t",
        ExpressionAttributeNames={"#ts": "timestamp"},
        ExpressionAttributeValues={":t": {"S": "2026-09-18T00:00:00+00:00"}},
    )
    assert (
        find_open_incident(
            "HighCPU",
            table_name=table,
            within_minutes=10,
            dynamodb_client=_dynamodb_table,
        )
        is None
    )
