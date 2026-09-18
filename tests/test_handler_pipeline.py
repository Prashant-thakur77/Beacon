"""End-to-end handler tests for the Night Shift additions: diagnostics and
change sources, deterministic action override, and the Sleep Contract path."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from beacon import contracts
from beacon.handler import handler
from beacon.remediation import actions_sg

INCIDENTS = "beacon-incidents-test"
CONTRACTS = "beacon-contracts-test"
APPROVALS = "beacon-approvals-test"
ALARM = "beacon-demo-infra-errors"


def _table(ddb: Any, name: str, key: str) -> None:
    ddb.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture()
def env(monkeypatch: Any, alarm_event: dict, nova_response: str) -> Any:
    with mock_aws():
        region = "us-east-1"
        sns = boto3.client("sns", region_name=region)
        topic = sns.create_topic(Name="t")["TopicArn"]
        sqs = boto3.client("sqs", region_name=region)
        queue = sqs.create_queue(QueueName="capture")["QueueUrl"]
        queue_arn = sqs.get_queue_attributes(
            QueueUrl=queue, AttributeNames=["QueueArn"]
        )["Attributes"]["QueueArn"]
        sns.subscribe(TopicArn=topic, Protocol="sqs", Endpoint=queue_arn)

        for var, val in {
            "LOG_GROUP_PATTERNS": "/test/app",
            "SNS_TOPIC_ARN": topic,
            "TOKEN_BUDGET": "100000",
            "INCIDENTS_ENABLED": "true",
            "INCIDENTS_TABLE_NAME": INCIDENTS,
            "CONTRACTS_TABLE_NAME": CONTRACTS,
            "APPROVALS_TABLE_NAME": APPROVALS,
            "GOLDEN_SG_PARAM": "/beacon/test/golden-sg",
            "APPLY_ENABLED": "true",
            "STATE_MACHINE_ARN": (
                "arn:aws:states:us-east-1:123456789012:stateMachine:beacon-remediate-test"
            ),
            "CHANGES_TABLE_NAME": "",
            "AWS_DEFAULT_REGION": region,
        }.items():
            monkeypatch.setenv(var, val)

        ddb = boto3.client("dynamodb", region_name=region)
        _table(ddb, INCIDENTS, "incident_id")
        _table(ddb, CONTRACTS, "contract_id")
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
        ec2.revoke_security_group_ingress(
            GroupId=rds_sg, IpPermissions=[actions_sg.ip_permission(params)]
        )

        logs = boto3.client("logs", region_name=region)
        logs.create_log_group(logGroupName="/test/app")
        logs.create_log_stream(logGroupName="/test/app", logStreamName="s")
        import time

        logs.put_log_events(
            logGroupName="/test/app",
            logStreamName="s",
            logEvents=[
                {
                    "timestamp": int(time.time() * 1000) - 1000,
                    "message": "ERROR db unreachable",
                }
            ],
        )

        event = dict(alarm_event)
        event["detail"] = {**event.get("detail", {}), "alarmName": ALARM}

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=nova_response))]

        def messages() -> list[str]:
            resp = sqs.receive_message(
                QueueUrl=queue, MaxNumberOfMessages=10, WaitTimeSeconds=0
            )
            return [json.loads(m["Body"])["Message"] for m in resp.get("Messages", [])]

        yield {
            "event": event,
            "ddb": ddb,
            "params": params,
            "messages": messages,
            "mock_resp": mock_resp,
            "rds_sg": rds_sg,
        }


def _run(env: Any, mock_completion: MagicMock) -> dict[str, Any]:
    mock_completion.return_value = env["mock_resp"]
    with (
        patch("beacon.handler.resolve_log_groups", return_value=["/test/app"]),
        patch("beacon.prefetch.run"),
    ):
        return handler(env["event"], None)


@patch("litellm.completion")
def test_prompt_gets_diagnostics_and_changes_sections(
    mock_completion: MagicMock, env: Any
) -> None:
    result = _run(env, mock_completion)
    assert result["statusCode"] == 200
    user_prompt = mock_completion.call_args.kwargs["messages"][1]["content"]
    assert (
        "[diagnostics]" in user_prompt
        and "MISSING ingress rule" in user_prompt
        and env["rds_sg"] in user_prompt
    )
    assert "[changes]" in user_prompt


@patch("litellm.completion")
def test_incident_carries_deterministic_action_over_model_guess(
    mock_completion: MagicMock, env: Any
) -> None:
    result = _run(env, mock_completion)
    item = env["ddb"].get_item(
        TableName=INCIDENTS, Key={"incident_id": {"S": result["incident_id"]}}
    )["Item"]
    from boto3.dynamodb.types import TypeDeserializer

    d = TypeDeserializer()
    incident = {k: d.deserialize(v) for k, v in item.items()}
    assert incident["status"] == "awaiting_engineer"
    assert incident["diagnostics"]["missing_rules"][0]["group_id"] == env["rds_sg"]
    # the fixture's model guess says sg-0abc123; the real ids from diagnostics win
    assert (
        incident["rca_json"]["beacon_json"]["suggested_action"] == "sg.restore_ingress"
    )
    assert (
        incident["rca_json"]["beacon_json"]["action_params"]["group_id"]
        == env["rds_sg"]
    )
    assert incident["rca_json"]["beacon_json"]["action_source"] == "diagnostics"
    events = [e["event"] for e in incident["timeline"]]
    assert "diagnostics_ran" in events and "changes_checked" in events
    assert incident["woken"] is True
    assert any(
        "awaiting your word" in m.lower() or "beacon" in m.lower()
        for m in env["messages"]()
    )


@patch(
    "beacon.handler._start_execution",
    return_value="arn:aws:states:us-east-1:123:execution:x:1",
)
@patch("litellm.completion")
def test_matching_sleep_contract_auto_remediates_and_does_not_wake(
    mock_completion: MagicMock, mock_sfn: MagicMock, env: Any
) -> None:
    granted = contracts.put(
        alarm_name=ALARM,
        action="sg.restore_ingress",
        params=env["params"],
        days=7,
        max_uses=3,
        transcript_quote="grant contract for seven days",
        granted_by="transcribe",
        incident_id="inc-0",
        table_name=CONTRACTS,
    )
    result = _run(env, mock_completion)
    assert result["contract_id"] == granted["contract_id"]
    from boto3.dynamodb.types import TypeDeserializer

    d = TypeDeserializer()
    item = env["ddb"].get_item(
        TableName=INCIDENTS, Key={"incident_id": {"S": result["incident_id"]}}
    )["Item"]
    incident = {k: d.deserialize(v) for k, v in item.items()}
    assert incident["status"] == "auto_remediating"
    assert incident["woken"] is False and incident["handled_by"] == "contract"
    assert incident["execution_arn"].startswith("arn:aws:states")
    mock_sfn.assert_called_once()
    started = mock_sfn.call_args.args[0]
    assert (
        started["action"] == "sg.restore_ingress" and started["params"] == env["params"]
    )
    approval = env["ddb"].get_item(
        TableName=APPROVALS, Key={"approval_id": {"S": started["approval_id"]}}
    )["Item"]
    assert approval["source"]["S"] == "contract"
    assert approval["transcript_quote"]["S"] == "grant contract for seven days"
    used = env["ddb"].get_item(
        TableName=CONTRACTS, Key={"contract_id": {"S": granted["contract_id"]}}
    )["Item"]
    assert used["uses"]["N"] == "1"
    body = "\n".join(env["messages"]())
    assert "not woken" in body.lower() or "sleep contract" in body.lower()


@patch("beacon.handler._start_execution")
@patch("litellm.completion")
def test_contract_match_with_apply_disabled_pages_instead(
    mock_completion: MagicMock, mock_sfn: MagicMock, env: Any, monkeypatch: Any
) -> None:
    monkeypatch.setenv("APPLY_ENABLED", "false")
    contracts.put(
        alarm_name=ALARM,
        action="sg.restore_ingress",
        params=env["params"],
        days=7,
        max_uses=3,
        transcript_quote="q",
        granted_by="typed",
        incident_id="inc-0",
        table_name=CONTRACTS,
    )
    result = _run(env, mock_completion)
    mock_sfn.assert_not_called()
    from boto3.dynamodb.types import TypeDeserializer

    d = TypeDeserializer()
    item = env["ddb"].get_item(
        TableName=INCIDENTS, Key={"incident_id": {"S": result["incident_id"]}}
    )["Item"]
    incident = {k: d.deserialize(v) for k, v in item.items()}
    assert incident["status"] == "awaiting_engineer" and incident["woken"] is True
    assert any(
        e["event"] == "contract_matched_apply_disabled" for e in incident["timeline"]
    )
