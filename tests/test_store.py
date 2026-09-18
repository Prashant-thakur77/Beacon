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
