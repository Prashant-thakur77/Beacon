from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon import changes

TABLE = "beacon-changes-test"


def _row(
    ddb: Any,
    when: datetime,
    name: str,
    actor: str = "user/prashant",
    ids: list[str] | None = None,
    by_beacon: bool = False,
) -> None:
    ddb.put_item(
        TableName=TABLE,
        Item={
            "pk": {"S": "change"},
            "sk": {"S": f"{when.isoformat()}#{name}-{when.timestamp()}"},
            "event_name": {"S": name},
            "event_source": {"S": "ec2.amazonaws.com"},
            "event_time": {"S": when.isoformat()},
            "actor": {"S": f"arn:aws:iam::123:{actor}"},
            "actor_short": {"S": actor},
            "by_beacon": {"BOOL": by_beacon},
            "user_agent": {"S": "aws-cli/2"},
            "source_ip": {"S": "1.2.3.4"},
            "resource_ids": {"L": [{"S": i} for i in (ids or [])]},
            "error_code": {"S": ""},
            "ttl": {"N": str(int(time.time()) + 3600)},
        },
    )


@pytest.fixture()
def table(monkeypatch: Any) -> Any:
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setenv("CHANGES_TABLE_NAME", TABLE)
        yield ddb


def test_recent_returns_events_in_window_before_the_alarm_destructive_first(
    table: Any,
) -> None:
    now = datetime.now(tz=UTC)
    alarm_at = now - timedelta(minutes=2)
    _row(table, now - timedelta(minutes=40), "CreateTags")  # too old
    _row(
        table,
        now - timedelta(minutes=5),
        "AuthorizeSecurityGroupIngress",
        ids=["sg-1"],
        by_beacon=True,
    )
    _row(table, now - timedelta(minutes=4), "RevokeSecurityGroupIngress", ids=["sg-1"])
    _row(
        table, now - timedelta(minutes=1), "DeleteSecurityGroup", ids=["sg-9"]
    )  # after the alarm

    rows = changes.recent(30, before=alarm_at, table_name=TABLE, dynamodb_client=table)
    assert [r["event_name"] for r in rows] == [
        "RevokeSecurityGroupIngress",
        "AuthorizeSecurityGroupIngress",
    ]
    assert rows[0]["resource_ids"] == ["sg-1"]
    assert rows[1]["by_beacon"] is True


def test_recent_without_before_uses_now(table: Any) -> None:
    now = datetime.now(tz=UTC)
    _row(table, now - timedelta(minutes=3), "ModifyDBInstance")
    rows = changes.recent(10, table_name=TABLE, dynamodb_client=table)
    assert len(rows) == 1


def test_format_changes_lines_are_prompt_friendly(table: Any) -> None:
    now = datetime.now(tz=UTC)
    _row(table, now - timedelta(minutes=4), "RevokeSecurityGroupIngress", ids=["sg-1"])
    rows = changes.recent(30, table_name=TABLE, dynamodb_client=table)
    text = changes.format_changes(rows, alarm_at=now - timedelta(minutes=2))
    assert (
        "RevokeSecurityGroupIngress" in text
        and "sg-1" in text
        and "user/prashant" in text
    )
    assert "before the alarm" in text
    assert (
        changes.format_changes([], alarm_at=now)
        == "No write API calls recorded in the window."
    )


def test_lookup_events_fallback_when_ledger_is_empty(table: Any, mocker: Any) -> None:
    fake = mocker.patch(
        "beacon.changes._lookup_events",
        return_value=[
            {
                "event_name": "RevokeSecurityGroupIngress",
                "event_time": "2026-09-18T20:00:41+00:00",
                "actor_short": "user/prashant",
                "resource_ids": ["sg-1"],
                "by_beacon": False,
                "source": "cloudtrail-lookup",
            }
        ],
    )
    rows = changes.recent(
        30, table_name=TABLE, dynamodb_client=table, lookup_fallback=True
    )
    assert rows[0]["source"] == "cloudtrail-lookup"
    fake.assert_called_once()
