from __future__ import annotations

import json
from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon import contracts, dashboard_api, store
from beacon.events import TriggerInfo, TriggerType
from tests.test_function_urls import url_event

INCIDENTS = "beacon-incidents-test"
CONTRACTS = "beacon-contracts-test"
APPROVALS = "beacon-approvals-test"
PARAMS = {
    "group_id": "sg-rds",
    "ip_protocol": "tcp",
    "from_port": 5432,
    "to_port": 5432,
    "source_group_id": "sg-ecs",
}


def _table(ddb: Any, name: str, key: str) -> None:
    ddb.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture()
def env(monkeypatch: Any) -> Any:
    with mock_aws():
        for var, val in {
            "AWS_DEFAULT_REGION": "us-east-1",
            "INCIDENTS_TABLE_NAME": INCIDENTS,
            "CONTRACTS_TABLE_NAME": CONTRACTS,
            "APPROVALS_TABLE_NAME": APPROVALS,
            "PASSCODE": "nightshift",
            "BASE_STACK_NAME": "test",
        }.items():
            monkeypatch.setenv(var, val)
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        for name, key in (
            (INCIDENTS, "incident_id"),
            (CONTRACTS, "contract_id"),
            (APPROVALS, "approval_id"),
        ):
            _table(ddb, name, key)
        trig = TriggerInfo(
            trigger_type=TriggerType.ALARM, alarm_name="beacon-demo-infra-errors"
        )
        a = store.put_incident(
            "STATUS: High\nSUMMARY: a",
            trig,
            table_name=INCIDENTS,
            rca_json={"status": "High", "summary": "a", "beacon_json": {}},
            changes=[
                {
                    "event_name": "RevokeSecurityGroupIngress",
                    "actor": "arn:aws:iam::123456789012:user/prashant",
                    "actor_short": "user/prashant",
                    "resource_ids": ["sg-rds"],
                }
            ],
        )
        store.update_status(
            a,
            "resolved",
            table_name=INCIDENTS,
            extra={
                "resolved_at": "2026-09-18T20:10:00+00:00",
                "handled_by": "voice",
                "conversation": [
                    {"role": "user", "content": [{"text": "secret chat"}]}
                ],
                "execution_arn": (
                    "arn:aws:states:us-east-1:123456789012:execution:"
                    "beacon-remediate-test:run1"
                ),
            },
        )
        b = store.put_incident(
            "STATUS: High\nSUMMARY: b",
            trig,
            table_name=INCIDENTS,
            rca_json={"status": "High", "summary": "b", "beacon_json": {}},
            status="auto_remediating",
            woken=False,
        )
        store.update_status(
            b,
            "resolved",
            table_name=INCIDENTS,
            extra={
                "resolved_at": "2026-09-18T21:03:00+00:00",
                "handled_by": "contract",
            },
        )
        contract = contracts.put(
            alarm_name="beacon-demo-infra-errors",
            action="sg.restore_ingress",
            params=PARAMS,
            days=7,
            max_uses=3,
            transcript_quote="grant contract for seven days",
            granted_by="transcribe",
            incident_id=a,
            table_name=CONTRACTS,
        )
        yield {"a": a, "b": b, "contract": contract, "ddb": ddb}


def _get(path: str) -> dict[str, Any]:
    resp = dashboard_api.handler(url_event("GET", path), None)
    return {
        "status": resp["statusCode"],
        "body": json.loads(resp["body"]) if resp.get("body") else None,
    }


def test_incidents_list_newest_first_without_conversation_or_raw_rca(env: Any) -> None:
    out = _get("/incidents")
    assert out["status"] == 200
    items = out["body"]["incidents"]
    assert [i["incident_id"] for i in items] == [env["b"], env["a"]]
    assert "conversation" not in items[1] and "rca" not in items[1]
    assert items[0]["woken"] is False and items[0]["handled_by"] == "contract"


def test_incident_detail_redacts_account_ids_and_actor_arns(env: Any) -> None:
    out = _get(f"/incidents/{env['a']}")
    assert out["status"] == 200
    inc = out["body"]["incident"]
    body_text = json.dumps(inc)
    assert "123456789012" not in body_text
    assert inc["changes"][0]["actor"] == "user/prashant"
    assert "conversation" not in inc
    assert _get("/incidents/nope")["status"] == 404


def test_tally_counts_handled_median_and_humans_woken(env: Any) -> None:
    out = _get("/tally")
    assert out["status"] == 200
    t = out["body"]
    assert t["incidents_handled"] == 2 and t["resolved"] == 2
    assert t["humans_woken"] == 1
    assert (
        t["median_minutes_to_recovery"] is not None
        and t["median_minutes_to_recovery"] >= 0
    )


def test_contracts_list_and_revoke_with_passcode(env: Any) -> None:
    out = _get("/contracts")
    assert (
        out["status"] == 200
        and out["body"]["contracts"][0]["contract_id"] == env["contract"]["contract_id"]
    )
    assert out["body"]["contracts"][0]["scope"] == PARAMS

    denied = dashboard_api.handler(
        url_event("DELETE", f"/contracts/{env['contract']['contract_id']}"), None
    )
    assert denied["statusCode"] == 401
    ok = dashboard_api.handler(
        url_event(
            "DELETE",
            f"/contracts/{env['contract']['contract_id']}",
            headers={"x-beacon-passcode": "nightshift"},
        ),
        None,
    )
    assert ok["statusCode"] == 200
    assert _get("/contracts")["body"]["contracts"] == []


def test_safety_lists_allowlist_and_kill_switch_state(env: Any, mocker: Any) -> None:
    mocker.patch(
        "beacon.dashboard_api._apply_flags",
        return_value={"triage": True, "voice": True, "remediate": False},
    )
    out = _get("/safety")
    assert out["status"] == 200
    ids = [a["id"] for a in out["body"]["allowlist"]]
    assert ids == ["sg.restore_ingress", "ecs.force_redeploy"]
    assert out["body"]["allowlist"][0]["iam_actions"] == [
        "ec2:AuthorizeSecurityGroupIngress"
    ]
    assert out["body"]["apply_enabled"] == {
        "triage": True,
        "voice": True,
        "remediate": False,
    }


def test_execution_route_is_graceful_without_step_functions(env: Any) -> None:
    out = _get(f"/incidents/{env['a']}/execution")
    assert out["status"] == 200
    assert out["body"]["execution_arn"].endswith("run1")
    assert isinstance(out["body"]["events"], list)


def test_redact_helper() -> None:
    data = {
        "actor": "arn:aws:sts::123456789012:assumed-role/beacon-remediator-x/y",
        "note": "acct 123456789012 here",
        "nested": [{"arn": "arn:aws:iam::123456789012:user/p"}],
    }
    out = dashboard_api.redact(data)
    assert out["actor"] == "role/beacon-remediator-x"
    assert "123456789012" not in json.dumps(out)
    assert out["nested"][0]["arn"] == "user/p"
