from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon import approvals

TABLE = "beacon-approvals-test"
SG_PARAMS = {
    "group_id": "sg-0abc",
    "ip_protocol": "tcp",
    "from_port": 5432,
    "to_port": 5432,
    "source_group_id": "sg-0def",
}


@pytest.fixture()
def table() -> Any:
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "approval_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "approval_id", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield ddb


def test_params_hash_is_canonical_and_action_scoped() -> None:
    a = approvals.params_hash("sg.restore_ingress", SG_PARAMS)
    b = approvals.params_hash(
        "sg.restore_ingress", dict(reversed(list(SG_PARAMS.items())))
    )
    c = approvals.params_hash("ecs.force_redeploy", SG_PARAMS)
    assert a == b and a != c and len(a) == 64


def test_create_and_get_proposal_round_trip(table: Any) -> None:
    created = approvals.create_proposal(
        "inc-1",
        1,
        "sg.restore_ingress",
        SG_PARAMS,
        blast_radius="1 rule",
        dry_run={"ok": True, "code": "DryRunOperation"},
        table_name=TABLE,
        dynamodb_client=table,
    )
    assert created["fix_id"] == 1 and created["kind"] == "proposal"
    fetched = approvals.get_proposal(
        "inc-1", 1, table_name=TABLE, dynamodb_client=table
    )
    assert fetched is not None
    assert fetched["params"] == SG_PARAMS
    assert fetched["dry_run"]["ok"] is True
    assert fetched["params_hash"] == approvals.params_hash(
        "sg.restore_ingress", SG_PARAMS
    )
    assert (
        approvals.get_proposal("inc-1", 2, table_name=TABLE, dynamodb_client=table)
        is None
    )


def test_expired_proposal_is_not_returned(table: Any, monkeypatch: Any) -> None:
    approvals.create_proposal(
        "inc-1",
        1,
        "sg.restore_ingress",
        SG_PARAMS,
        blast_radius="x",
        dry_run={"ok": True},
        table_name=TABLE,
        dynamodb_client=table,
        ttl_seconds=300,
    )
    later = datetime.now(tz=UTC) + timedelta(seconds=301)
    monkeypatch.setattr(approvals, "_now", lambda: later)
    assert (
        approvals.get_proposal("inc-1", 1, table_name=TABLE, dynamodb_client=table)
        is None
    )


def test_create_approval_records_source_and_transcript_quote(table: Any) -> None:
    record = approvals.create(
        "inc-1",
        "sg.restore_ingress",
        SG_PARAMS,
        source="voice",
        channel="transcribe",
        transcript_quote="approve fix one",
        table_name=TABLE,
        dynamodb_client=table,
    )
    assert record["approval_id"]
    fetched = approvals.get(
        record["approval_id"], table_name=TABLE, dynamodb_client=table
    )
    assert fetched is not None
    assert fetched["source"] == "voice"
    assert fetched["channel"] == "transcribe"
    assert fetched["transcript_quote"] == "approve fix one"
    assert fetched["incident_id"] == "inc-1"
    assert fetched["used_at"] is None
    assert int(fetched["ttl"]) > 0


def test_get_unknown_approval_is_none(table: Any) -> None:
    assert approvals.get("nope", table_name=TABLE, dynamodb_client=table) is None


def test_mark_used_succeeds_once(table: Any) -> None:
    record = approvals.create(
        "inc-1",
        "sg.restore_ingress",
        SG_PARAMS,
        source="cli",
        channel="cli",
        transcript_quote="approve fix 1",
        table_name=TABLE,
        dynamodb_client=table,
    )
    assert (
        approvals.mark_used(
            record["approval_id"], table_name=TABLE, dynamodb_client=table
        )
        is True
    )
    assert (
        approvals.mark_used(
            record["approval_id"], table_name=TABLE, dynamodb_client=table
        )
        is False
    )
    fetched = approvals.get(
        record["approval_id"], table_name=TABLE, dynamodb_client=table
    )
    assert fetched is not None and fetched["used_at"]


def test_is_valid_rejects_expired_used_or_mismatched(
    table: Any, monkeypatch: Any
) -> None:
    record = approvals.create(
        "inc-1",
        "sg.restore_ingress",
        SG_PARAMS,
        source="voice",
        channel="typed",
        transcript_quote="approve fix 1",
        table_name=TABLE,
        dynamodb_client=table,
        ttl_seconds=900,
    )
    fetched = approvals.get(
        record["approval_id"], table_name=TABLE, dynamodb_client=table
    )
    assert fetched is not None
    assert approvals.is_valid(fetched, "sg.restore_ingress", SG_PARAMS) is None
    assert "action" in str(approvals.is_valid(fetched, "ecs.force_redeploy", SG_PARAMS))
    assert "params" in str(
        approvals.is_valid(
            fetched, "sg.restore_ingress", {**SG_PARAMS, "from_port": 22}
        )
    )
    approvals.mark_used(record["approval_id"], table_name=TABLE, dynamodb_client=table)
    used = approvals.get(record["approval_id"], table_name=TABLE, dynamodb_client=table)
    assert used is not None and "used" in str(
        approvals.is_valid(used, "sg.restore_ingress", SG_PARAMS)
    )
    later = datetime.now(tz=UTC) + timedelta(seconds=901)
    monkeypatch.setattr(approvals, "_now", lambda: later)
    assert "expired" in str(
        approvals.is_valid(fetched, "sg.restore_ingress", SG_PARAMS)
    )
