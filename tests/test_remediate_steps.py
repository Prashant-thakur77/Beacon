from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon import approvals, store
from beacon.events import TriggerInfo, TriggerType
from beacon.remediate import handler
from beacon.remediation import actions_sg

ALARM = "beacon-demo-infra-errors"
INCIDENTS = "beacon-incidents-test"
APPROVALS = "beacon-approvals-test"


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
        region = "us-east-1"
        for var, val in {
            "AWS_DEFAULT_REGION": region,
            "INCIDENTS_TABLE_NAME": INCIDENTS,
            "APPROVALS_TABLE_NAME": APPROVALS,
            "GOLDEN_SG_PARAM": "/beacon/test/golden-sg",
            "APPLY_ENABLED": "true",
            "VERIFY_WAIT_SECONDS": "0",
            "VERIFY_MAX_ATTEMPTS": "3",
        }.items():
            monkeypatch.setenv(var, val)

        ddb = boto3.client("dynamodb", region_name=region)
        _table(ddb, INCIDENTS, "incident_id")
        _table(ddb, APPROVALS, "approval_id")

        ec2 = boto3.client("ec2", region_name=region)
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
        ecs_sg = ec2.create_security_group(GroupName="ecs", Description="e", VpcId=vpc)[
            "GroupId"
        ]
        rds_sg = ec2.create_security_group(GroupName="rds", Description="r", VpcId=vpc)[
            "GroupId"
        ]
        params = {
            "group_id": rds_sg,
            "ip_protocol": "tcp",
            "from_port": 5432,
            "to_port": 5432,
            "source_group_id": ecs_sg,
        }
        ec2.authorize_security_group_ingress(
            GroupId=rds_sg, IpPermissions=[actions_sg.ip_permission(params)]
        )
        golden = actions_sg.snapshot([rds_sg, ecs_sg], ec2_client=ec2)
        boto3.client("ssm", region_name=region).put_parameter(
            Name="/beacon/test/golden-sg", Type="String", Value=json.dumps(golden)
        )
        # break it
        ec2.revoke_security_group_ingress(
            GroupId=rds_sg, IpPermissions=[actions_sg.ip_permission(params)]
        )

        cw = boto3.client("cloudwatch", region_name=region)
        cw.put_metric_alarm(
            AlarmName=ALARM,
            Namespace="BeaconDemoInfra",
            MetricName="ErrorCount",
            Statistic="Sum",
            Period=60,
            EvaluationPeriods=1,
            Threshold=3,
            ComparisonOperator="GreaterThanOrEqualToThreshold",
            TreatMissingData="notBreaching",
        )
        cw.set_alarm_state(AlarmName=ALARM, StateValue="ALARM", StateReason="test")

        sns = boto3.client("sns", region_name=region)
        topic = sns.create_topic(Name="beacon-alerts-test")["TopicArn"]
        monkeypatch.setenv("SNS_TOPIC_ARN", topic)
        sqs = boto3.client("sqs", region_name=region)
        queue = sqs.create_queue(QueueName="capture")["QueueUrl"]
        queue_arn = sqs.get_queue_attributes(
            QueueUrl=queue, AttributeNames=["QueueArn"]
        )["Attributes"]["QueueArn"]
        sns.subscribe(TopicArn=topic, Protocol="sqs", Endpoint=queue_arn)

        incident_id = store.put_incident(
            "STATUS: High\nSUMMARY: db unreachable",
            TriggerInfo(trigger_type=TriggerType.ALARM, alarm_name=ALARM),
            table_name=INCIDENTS,
            status="remediating",
        )
        approval = approvals.create(
            incident_id,
            "sg.restore_ingress",
            params,
            source="voice",
            channel="transcribe",
            transcript_quote="approve fix one",
            table_name=APPROVALS,
        )

        def messages() -> list[str]:
            resp = sqs.receive_message(
                QueueUrl=queue, MaxNumberOfMessages=10, WaitTimeSeconds=0
            )
            return [json.loads(m["Body"])["Message"] for m in resp.get("Messages", [])]

        yield {
            "ec2": ec2,
            "cw": cw,
            "params": params,
            "incident_id": incident_id,
            "approval_id": approval["approval_id"],
            "messages": messages,
        }


def _base(env: Any, step: str, **extra: Any) -> dict[str, Any]:
    return {
        "step": step,
        "approval_id": env["approval_id"],
        "incident_id": env["incident_id"],
        "action": "sg.restore_ingress",
        "params": env["params"],
        **extra,
    }


def test_require_approval_needs_a_valid_matching_record(env: Any) -> None:
    ok = handler(_base(env, "require_approval"), None)
    assert ok["ok"] is True and ok["approval"]["source"] == "voice"
    assert ok["approval"]["transcript_quote"] == "approve fix one"

    missing = handler({**_base(env, "require_approval"), "approval_id": "nope"}, None)
    assert missing["ok"] is False and "no approval" in missing["error"]

    other = handler(
        {
            **_base(env, "require_approval"),
            "params": {**env["params"], "from_port": 22, "to_port": 22},
        },
        None,
    )
    assert other["ok"] is False and "different params" in other["error"]


def test_execute_honours_the_kill_switch(env: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("APPLY_ENABLED", "false")
    result = handler(_base(env, "execute"), None)
    assert result["ok"] is False and "apply disabled" in result["error"]
    assert actions_sg.postcondition(env["params"], ec2_client=env["ec2"]) is False


def test_execute_restores_rule_once_and_is_idempotent_on_retry(env: Any) -> None:
    first = handler(_base(env, "execute"), None)
    assert first["ok"] is True and first["executed_at"]
    assert actions_sg.postcondition(env["params"], ec2_client=env["ec2"]) is True

    record = approvals.get(env["approval_id"], table_name=APPROVALS)
    assert (
        record is not None
        and record["used_at"]
        and record["execute_result"]["ok"] is True
    )

    retry = handler(_base(env, "execute"), None)
    assert retry["ok"] is True and retry["executed_at"] == first["executed_at"]
    assert retry["idempotent_replay"] is True

    incident = store.get_incident(env["incident_id"], table_name=INCIDENTS)
    assert [e["event"] for e in incident["timeline"]].count("executed") == 1


def test_execute_refuses_without_valid_approval(env: Any) -> None:
    result = handler({**_base(env, "execute"), "approval_id": "nope"}, None)
    assert result["ok"] is False and "no approval" in result["error"]
    assert actions_sg.postcondition(env["params"], ec2_client=env["ec2"]) is False


def test_verify_counts_attempts_and_needs_all_three_checks(env: Any) -> None:
    executed_at = (datetime.now(tz=UTC) - timedelta(minutes=1)).isoformat()
    still_broken = handler(
        _base(env, "verify", executed_at=executed_at, attempts=0), None
    )
    assert still_broken["ok"] is False and still_broken["attempts"] == 1
    names = [c["name"] for c in still_broken["checks"]]
    assert names == ["alarm_ok_after_fix", "metric_zero", "postcondition"]

    handler(_base(env, "execute"), None)
    env["cw"].set_alarm_state(AlarmName=ALARM, StateValue="OK", StateReason="recovered")
    fixed = handler(_base(env, "verify", executed_at=executed_at, attempts=1), None)
    assert fixed["ok"] is True and fixed["attempts"] == 2
    incident = store.get_incident(env["incident_id"], table_name=INCIDENTS)
    assert [e["event"] for e in incident["timeline"]].count("verify_attempt") == 2


def test_resolve_and_escalate_update_status_and_notify(env: Any) -> None:
    executed = {
        "Payload": {"ok": True, "executed_at": datetime.now(tz=UTC).isoformat()}
    }
    verified = {"Payload": {"ok": True, "attempts": 1, "checks": []}}
    resolved = handler(
        {
            "step": "resolve",
            "input": {**_base(env, "resolve"), "execute": executed, "verify": verified},
        },
        None,
    )
    assert resolved["ok"] is True
    incident = store.get_incident(env["incident_id"], table_name=INCIDENTS)
    assert (
        incident["status"] == "resolved"
        and incident["resolved_at"]
        and incident["handled_by"] == "voice"
    )
    body = "\n".join(env["messages"]())
    assert "Resolved" in body and "approve fix one" in body

    escalated = handler(
        {
            "step": "escalate",
            "input": {
                **_base(env, "escalate"),
                "verify": {"Payload": {"ok": False, "attempts": 3, "checks": []}},
            },
        },
        None,
    )
    assert escalated["ok"] is True and escalated["escalated"] is True
    incident = store.get_incident(env["incident_id"], table_name=INCIDENTS)
    assert incident["status"] == "escalated"
    assert any("human" in m.lower() for m in env["messages"]())


def test_inline_runner_drives_the_whole_loop(env: Any, monkeypatch: Any) -> None:
    # The alarm clears while the loop waits between verify attempts (as in
    # production); an OK from before the fix would not count.
    monkeypatch.setenv("VERIFY_WAIT_SECONDS", "0.01")
    from beacon import remediate

    def _alarm_clears(_seconds: float) -> None:
        env["cw"].set_alarm_state(
            AlarmName=ALARM, StateValue="OK", StateReason="recovered"
        )

    monkeypatch.setattr(remediate.time, "sleep", _alarm_clears)
    result = handler(_base(env, "all"), None)
    assert result["ok"] is True and result["final"] == "resolved"
    assert actions_sg.postcondition(env["params"], ec2_client=env["ec2"]) is True
    incident = store.get_incident(env["incident_id"], table_name=INCIDENTS)
    assert incident["status"] == "resolved"
    events = [e["event"] for e in incident["timeline"]]
    assert (
        "executed" in events and "verify_attempt" in events and events[-1] == "resolved"
    )
