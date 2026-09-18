from __future__ import annotations

import json
from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon import approvals, contracts, store, voice_tools
from beacon.events import TriggerInfo, TriggerType
from beacon.turn_context import TurnContext, turn_context

INCIDENTS = "beacon-incidents-test"
APPROVALS = "beacon-approvals-test"
CONTRACTS = "beacon-contracts-test"
ALARM = "beacon-demo-infra-errors"
PARAMS = {
    "group_id": "sg-rds",
    "ip_protocol": "tcp",
    "from_port": 5432,
    "to_port": 5432,
    "source_group_id": "sg-ecs",
}
RCA = {
    "status": "High",
    "summary": "The demo web service lost its database.",
    "affected_components": ["beacon-demo-webapp"],
    "evidence": ["ERROR db unreachable"],
    "next_steps": ["restore the rule"],
    "change_correlation": "RevokeSecurityGroupIngress by user/prashant 21 s before",
    "spoken_summary": "Your demo service in U S east 1 lost its database.",
    "beacon_json": {
        "suggested_action": "sg.restore_ingress",
        "action_params": PARAMS,
        "action_source": "diagnostics",
        "confidence": 0.9,
    },
}


def _table(ddb: Any, name: str, key: str) -> None:
    ddb.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture()
def env(monkeypatch: Any, mocker: Any) -> Any:
    with mock_aws():
        for var, val in {
            "AWS_DEFAULT_REGION": "us-east-1",
            "INCIDENTS_TABLE_NAME": INCIDENTS,
            "APPROVALS_TABLE_NAME": APPROVALS,
            "CONTRACTS_TABLE_NAME": CONTRACTS,
            "APPLY_ENABLED": "true",
            "REMEDIATE_FUNCTION_ARN": (
                "arn:aws:lambda:us-east-1:123:function:beacon-remediate-test"
            ),
            "STATE_MACHINE_ARN": (
                "arn:aws:states:us-east-1:123:stateMachine:beacon-remediate-test"
            ),
        }.items():
            monkeypatch.setenv(var, val)
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        for name, key in (
            (INCIDENTS, "incident_id"),
            (APPROVALS, "approval_id"),
            (CONTRACTS, "contract_id"),
        ):
            _table(ddb, name, key)
        incident_id = store.put_incident(
            "STATUS: High\nSUMMARY: x",
            TriggerInfo(trigger_type=TriggerType.ALARM, alarm_name=ALARM),
            table_name=INCIDENTS,
            rca_json=RCA,
            diagnostics={
                "missing_rules": [{"group_id": "sg-rds"}],
                "suggested_action": "sg.restore_ingress",
                "action_params": PARAMS,
            },
            changes=[
                {
                    "event_name": "RevokeSecurityGroupIngress",
                    "actor_short": "user/prashant",
                    "event_time": "2026-09-18T20:00:41+00:00",
                    "resource_ids": ["sg-rds"],
                }
            ],
        )
        store.update_cached_data(
            incident_id,
            {
                "metrics": [{"query_key": "ErrorCount", "datapoints": [{"value": 4}]}],
                "logs": [],
                "status": [],
            },
            table_name=INCIDENTS,
        )
        dryrun = mocker.patch(
            "beacon.voice_tools._invoke_remediate",
            return_value={
                "ok": True,
                "code": "DryRunOperation",
                "detail": "",
                "blast_radius": "1 ingress rule on 1 security group",
                "role": "arn:aws:sts::123:assumed-role/beacon-remediator-test/x",
                "action": "sg.restore_ingress",
                "params": PARAMS,
            },
        )
        sfn = mocker.patch(
            "beacon.voice_tools._start_execution",
            return_value="arn:aws:states:us-east-1:123:execution:x:1",
        )
        yield {"incident_id": incident_id, "ddb": ddb, "dryrun": dryrun, "sfn": sfn}


def _ctx(
    incident_id: str,
    transcript: str,
    channel: str = "transcribe",
    passcode_ok: bool = True,
) -> TurnContext:
    return TurnContext(
        incident_id=incident_id,
        session_id="s1",
        transcript=transcript,
        channel=channel,
        passcode_ok=passcode_ok,
    )


def test_tool_schemas_cover_the_six_tools_with_json_schema() -> None:
    names = [t["name"] for t in voice_tools.TOOL_SCHEMAS]
    assert names == [
        "get_incident_brief",
        "get_evidence",
        "propose_fix",
        "approve_fix",
        "grant_sleep_contract",
        "check_recovery",
    ]
    for tool in voice_tools.TOOL_SCHEMAS:
        assert tool["parameters"]["type"] == "object"
        assert tool["description"]
    approve = next(t for t in voice_tools.TOOL_SCHEMAS if t["name"] == "approve_fix")
    assert set(approve["parameters"]["required"]) == {"fix_id", "confirmation_phrase"}


def test_get_incident_brief_returns_rca_and_evidence_card(env: Any) -> None:
    with turn_context(_ctx(env["incident_id"], "brief me")):
        out = voice_tools.get_incident_brief()
    assert out["status"] == "High" and "database" in out["spoken_summary"]
    assert out["alarm_name"] == ALARM
    assert out["change_correlation"].startswith("RevokeSecurityGroupIngress")
    assert out["evidence"][0]["id"] == "E1" and out["evidence"][0]["kind"] == "rca"


def test_get_evidence_kinds_and_numbering(env: Any) -> None:
    with turn_context(_ctx(env["incident_id"], "what changed")) as ctx:
        changes = voice_tools.get_evidence("changes")
        metrics = voice_tools.get_evidence("metrics")
        drift = voice_tools.get_evidence("diagnostics")
        assert changes["evidence"][0][
            "kind"
        ] == "changes" and "RevokeSecurityGroupIngress" in json.dumps(changes)
        assert metrics["evidence"][0]["kind"] == "metrics"
        assert drift["evidence"][0]["kind"] == "diagnostics" and "sg-rds" in json.dumps(
            drift
        )
        ids = [e["id"] for e in ctx.evidence]
        assert ids == ["E1", "E2", "E3"]
        unknown = voice_tools.get_evidence("secrets")
        assert "error" in unknown


def test_propose_fix_dry_runs_under_remediator_and_stores_proposal(env: Any) -> None:
    with turn_context(_ctx(env["incident_id"], "can you fix it")):
        out = voice_tools.propose_fix()
    assert out["fix_id"] == 1 and out["action"] == "sg.restore_ingress"
    assert out["dry_run"]["ok"] is True and "remediator" in out["dry_run"]["role"]
    assert out["confirmation_phrase"] == "approve fix 1"
    assert out["blast_radius"].startswith("1 ingress rule")
    env["dryrun"].assert_called_once()
    assert env["dryrun"].call_args.args[0]["step"] == "dryrun"
    stored = approvals.get_proposal(env["incident_id"], 1, table_name=APPROVALS)
    assert stored is not None and stored["params"] == PARAMS


def test_propose_fix_refuses_when_no_safe_action(env: Any) -> None:
    store.update_status(
        env["incident_id"],
        "awaiting_engineer",
        table_name=INCIDENTS,
        extra={"rca_json": {**RCA, "beacon_json": {}}},
    )
    with turn_context(_ctx(env["incident_id"], "fix it")):
        out = voice_tools.propose_fix()
    assert "no safe fix" in out["error"]
    env["dryrun"].assert_not_called()


def test_approve_fix_requires_exact_phrase_in_the_raw_transcript(env: Any) -> None:
    with turn_context(_ctx(env["incident_id"], "can you fix it")):
        voice_tools.propose_fix()
    with turn_context(_ctx(env["incident_id"], "yes do it")):
        refused = voice_tools.approve_fix(1, "approve fix 1")
    assert refused["approved"] is False and "say" in refused["error"]
    env["sfn"].assert_not_called()

    with turn_context(_ctx(env["incident_id"], "okay, approve fix one")):
        ok = voice_tools.approve_fix(1, "approve fix one")
    assert ok["approved"] is True and ok["execution_arn"].startswith("arn:aws:states")
    env["sfn"].assert_called_once()
    payload = env["sfn"].call_args.args[0]
    assert payload["action"] == "sg.restore_ingress" and payload["params"] == PARAMS
    record = approvals.get(payload["approval_id"], table_name=APPROVALS)
    assert record is not None
    assert (
        record["transcript_quote"] == "okay, approve fix one"
        and record["channel"] == "transcribe"
    )
    incident = store.get_incident(env["incident_id"], table_name=INCIDENTS)
    assert incident["status"] == "remediating" and incident["execution_arn"]


def test_approve_fix_refuses_without_passcode_or_with_apply_disabled(
    env: Any, monkeypatch: Any
) -> None:
    with turn_context(_ctx(env["incident_id"], "fix it")):
        voice_tools.propose_fix()
    with turn_context(_ctx(env["incident_id"], "approve fix 1", passcode_ok=False)):
        assert voice_tools.approve_fix(1, "approve fix 1")["approved"] is False
    monkeypatch.setenv("APPLY_ENABLED", "false")
    with turn_context(_ctx(env["incident_id"], "approve fix 1")):
        out = voice_tools.approve_fix(1, "approve fix 1")
    assert out["approved"] is False and "disabled" in out["error"]
    env["sfn"].assert_not_called()


def test_approve_fix_refuses_unknown_or_expired_proposal(env: Any) -> None:
    with turn_context(_ctx(env["incident_id"], "approve fix 2")):
        out = voice_tools.approve_fix(2, "approve fix 2")
    assert out["approved"] is False and "no proposal" in out["error"]


def test_grant_sleep_contract_needs_read_back_then_explicit_phrase(env: Any) -> None:
    with turn_context(_ctx(env["incident_id"], "yes")):
        first = voice_tools.grant_sleep_contract(7, 3)
    assert first["granted"] is False and first["read_back_pending"] is True
    assert "sg-rds" in first["read_back"] and "seven days" in first["read_back"].lower()
    incident = store.get_incident(env["incident_id"], table_name=INCIDENTS)
    assert incident["contract_readback_pending"]["days"] == 7

    with turn_context(_ctx(env["incident_id"], "sure go ahead")):
        loose = voice_tools.grant_sleep_contract(7, 3)
    assert loose["granted"] is False

    with turn_context(_ctx(env["incident_id"], "grant contract for seven days")):
        granted = voice_tools.grant_sleep_contract(7, 3)
    assert granted["granted"] is True
    live = contracts.match(ALARM, "sg.restore_ingress", PARAMS, table_name=CONTRACTS)
    assert (
        live is not None and live["transcript_quote"] == "grant contract for seven days"
    )
    assert live["max_uses"] == 3 and live["days"] == 7


def test_grant_sleep_contract_accepts_hinglish_phrase(env: Any) -> None:
    with turn_context(_ctx(env["incident_id"], "haan")):
        voice_tools.grant_sleep_contract(7, 3)
    with turn_context(_ctx(env["incident_id"], "haan, saat din ke liye contract do")):
        granted = voice_tools.grant_sleep_contract(7, 3)
    assert granted["granted"] is True


def test_check_recovery_reports_status_and_verify_checks(env: Any) -> None:
    store.update_status(
        env["incident_id"],
        "resolved",
        table_name=INCIDENTS,
        extra={"resolved_at": "2026-09-18T20:10:00+00:00"},
    )
    store.append_timeline(
        env["incident_id"],
        "verify_attempt",
        table_name=INCIDENTS,
        detail={
            "attempt": 2,
            "ok": True,
            "checks": [{"name": "alarm_ok_after_fix", "ok": True}],
        },
    )
    with turn_context(_ctx(env["incident_id"], "is it fixed")):
        out = voice_tools.check_recovery()
    assert out["status"] == "resolved" and out["last_verify"]["attempt"] == 2
