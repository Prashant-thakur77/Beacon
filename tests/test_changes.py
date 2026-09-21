from __future__ import annotations

from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon import changes
from beacon.changes import ledger_handler

CLOUDTRAIL_EVENT = {
    "version": "0",
    "id": "evt-1",
    "detail-type": "AWS API Call via CloudTrail",
    "source": "aws.ec2",
    "account": "123456789012",
    "time": "2026-09-18T20:00:41Z",
    "region": "us-east-1",
    "detail": {
        "eventVersion": "1.09",
        "eventTime": "2026-09-18T20:00:41Z",
        "eventSource": "ec2.amazonaws.com",
        "eventName": "RevokeSecurityGroupIngress",
        "awsRegion": "us-east-1",
        "sourceIPAddress": "1.2.3.4",
        "userAgent": "aws-cli/2.17.0",
        "eventID": "abcd-1234",
        "userIdentity": {
            "type": "IAMUser",
            "arn": "arn:aws:iam::123456789012:user/prashant",
            "userName": "prashant",
        },
        "requestParameters": {
            "groupId": "sg-0abc123",
            "ipPermissions": {
                "items": [{"ipProtocol": "tcp", "fromPort": 5432, "toPort": 5432}]
            },
        },
        "responseElements": {"_return": True},
    },
}


@pytest.fixture()
def changes_table(monkeypatch: Any) -> Any:
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="beacon-changes-test",
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
        monkeypatch.setenv("CHANGES_TABLE_NAME", "beacon-changes-test")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        yield ddb


def test_ledger_handler_writes_one_row_per_event(changes_table: Any) -> None:
    result = ledger_handler(CLOUDTRAIL_EVENT, None)
    assert result["ok"] is True

    items = changes_table.scan(TableName="beacon-changes-test")["Items"]
    assert len(items) == 1
    row = items[0]
    assert row["pk"]["S"] == "change"
    assert row["sk"]["S"] == "2026-09-18T20:00:41Z#abcd-1234"
    assert row["event_name"]["S"] == "RevokeSecurityGroupIngress"
    assert row["event_source"]["S"] == "ec2.amazonaws.com"
    assert row["actor"]["S"] == "arn:aws:iam::123456789012:user/prashant"
    assert row["actor_short"]["S"] == "user/prashant"
    assert row["resource_ids"]["L"][0]["S"] == "sg-0abc123"
    assert row["user_agent"]["S"].startswith("aws-cli")
    assert int(row["ttl"]["N"]) > 0


def test_ledger_handler_ignores_read_only_and_malformed_events(
    changes_table: Any,
) -> None:
    read_only = {
        **CLOUDTRAIL_EVENT,
        "detail": {**CLOUDTRAIL_EVENT["detail"], "readOnly": True},
    }
    assert ledger_handler(read_only, None)["ok"] is False
    assert ledger_handler({"detail-type": "Something else"}, None)["ok"] is False
    assert changes_table.scan(TableName="beacon-changes-test")["Count"] == 0


def test_ledger_handler_tags_remediator_role_events(changes_table: Any) -> None:
    event = {
        **CLOUDTRAIL_EVENT,
        "detail": {
            **CLOUDTRAIL_EVENT["detail"],
            "eventName": "AuthorizeSecurityGroupIngress",
            "eventID": "efgh-5678",
            "userIdentity": {
                "type": "AssumedRole",
                "arn": (
                    "arn:aws:sts::123456789012:assumed-role/"
                    "beacon-remediator-beacon/beacon-remediate-beacon"
                ),
            },
        },
    }
    ledger_handler(event, None)
    row = changes_table.scan(TableName="beacon-changes-test")["Items"][0]
    assert row["by_beacon"]["BOOL"] is True
    assert row["actor_short"]["S"] == "beacon remediation"


def test_recent_ranks_changes_touching_the_affected_resource_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Lambda deploy after the revoke must not hide the revoke on the broken SG."""
    rows = [
        {
            "event_name": "RevokeSecurityGroupIngress",
            "event_time": "2026-09-21T14:20:00Z",
            "resource_ids": ["sg-db"],
        },
        {
            "event_name": "UpdateFunctionCode20150331v2",
            "event_time": "2026-09-21T14:47:00Z",
            "resource_ids": [],
        },
        {
            "event_name": "DeleteAlarms",
            "event_time": "2026-09-21T14:30:00Z",
            "resource_ids": ["other"],
        },
    ]

    class FakeDdb:
        def query(self, **kwargs: object) -> dict[str, object]:
            from boto3.dynamodb.types import TypeSerializer

            ser = TypeSerializer()
            return {
                "Items": [{k: ser.serialize(v) for k, v in r.items()} for r in rows]
            }

    out = changes.recent(
        60, table_name="t", dynamodb_client=FakeDdb(), affected_ids=["sg-db"]
    )
    assert [r["event_name"] for r in out][:2] == [
        "RevokeSecurityGroupIngress",
        "DeleteAlarms",
    ]
    plain = changes.recent(60, table_name="t", dynamodb_client=FakeDdb())
    assert (
        plain[0]["event_name"] == "RevokeSecurityGroupIngress"
    )  # Revoke outranks Delete
