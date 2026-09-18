from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon import contracts

TABLE = "beacon-contracts-test"
ALARM = "beacon-demo-infra-errors"
PARAMS = {
    "group_id": "sg-rds",
    "ip_protocol": "tcp",
    "from_port": 5432,
    "to_port": 5432,
    "source_group_id": "sg-ecs",
}


@pytest.fixture()
def table() -> Any:
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "contract_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "contract_id", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield ddb


def _grant(table: Any, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(
        alarm_name=ALARM,
        action="sg.restore_ingress",
        params=PARAMS,
        days=7,
        max_uses=3,
        transcript_quote="grant contract for seven days",
        granted_by="transcribe",
        incident_id="inc-1",
        table_name=TABLE,
        dynamodb_client=table,
    )
    kwargs.update(overrides)
    return contracts.put(**kwargs)


def test_put_and_match_round_trip(table: Any) -> None:
    granted = _grant(table)
    assert (
        granted["contract_id"]
        and granted["uses"] == 0
        and granted["status"] == "active"
    )
    found = contracts.match(
        ALARM, "sg.restore_ingress", PARAMS, table_name=TABLE, dynamodb_client=table
    )
    assert found is not None and found["contract_id"] == granted["contract_id"]
    assert found["transcript_quote"] == "grant contract for seven days"
    assert found["expires_at"] > datetime.now(tz=UTC).isoformat()
    assert int(found["expires_at_epoch"]) > 0


def test_match_is_scoped_to_alarm_action_and_exact_params(table: Any) -> None:
    _grant(table)
    assert (
        contracts.match(
            "other-alarm",
            "sg.restore_ingress",
            PARAMS,
            table_name=TABLE,
            dynamodb_client=table,
        )
        is None
    )
    assert (
        contracts.match(
            ALARM, "ecs.force_redeploy", PARAMS, table_name=TABLE, dynamodb_client=table
        )
        is None
    )
    assert (
        contracts.match(
            ALARM,
            "sg.restore_ingress",
            {**PARAMS, "from_port": 22},
            table_name=TABLE,
            dynamodb_client=table,
        )
        is None
    )


def test_use_increments_until_max_then_refuses(table: Any) -> None:
    granted = _grant(table, max_uses=2)
    cid = granted["contract_id"]
    assert contracts.use(cid, table_name=TABLE, dynamodb_client=table) is True
    assert contracts.use(cid, table_name=TABLE, dynamodb_client=table) is True
    assert contracts.use(cid, table_name=TABLE, dynamodb_client=table) is False
    assert (
        contracts.match(
            ALARM, "sg.restore_ingress", PARAMS, table_name=TABLE, dynamodb_client=table
        )
        is None
    )


def test_expired_and_revoked_contracts_do_not_match(
    table: Any, monkeypatch: Any
) -> None:
    granted = _grant(table, days=1)
    contracts.revoke(granted["contract_id"], table_name=TABLE, dynamodb_client=table)
    assert (
        contracts.match(
            ALARM, "sg.restore_ingress", PARAMS, table_name=TABLE, dynamodb_client=table
        )
        is None
    )

    fresh = _grant(table, days=1)
    later = datetime.now(tz=UTC) + timedelta(days=1, minutes=1)
    monkeypatch.setattr(contracts, "_now", lambda: later)
    assert (
        contracts.match(
            ALARM, "sg.restore_ingress", PARAMS, table_name=TABLE, dynamodb_client=table
        )
        is None
    )
    assert (
        contracts.use(fresh["contract_id"], table_name=TABLE, dynamodb_client=table)
        is False
    )


def test_list_active_returns_only_live_contracts(table: Any) -> None:
    a = _grant(table)
    b = _grant(table, alarm_name="other")
    contracts.revoke(b["contract_id"], table_name=TABLE, dynamodb_client=table)
    active = contracts.list_active(table_name=TABLE, dynamodb_client=table)
    assert [c["contract_id"] for c in active] == [a["contract_id"]]
