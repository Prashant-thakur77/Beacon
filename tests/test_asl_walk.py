"""Walk the real Step Functions definition against the real remediate handler.

A tiny ASL interpreter: enough of Task / Choice / Wait / Pass semantics
(Parameters with ``.$`` JSONPath, ResultSelector, ResultPath, Choices) to
prove the state machine and the Lambda agree on every payload shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import boto3
import pytest
import yaml
from moto import mock_aws

from beacon import approvals, store
from beacon.events import TriggerInfo, TriggerType
from beacon.remediation import actions_sg

TEMPLATE = Path(__file__).parent.parent / "remediation-template.yaml"
ALARM = "beacon-demo-infra-errors"
INCIDENTS = "beacon-incidents-test"
APPROVALS = "beacon-approvals-test"


class _Loader(yaml.SafeLoader):
    pass


def _tag(loader: Any, _suffix: str, node: Any) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_mapping(node, deep=True)


_Loader.add_multi_constructor("!", _tag)


def load_asl() -> dict[str, Any]:
    template = yaml.load(TEMPLATE.read_text(), Loader=_Loader)
    raw, subs = template["Resources"]["BeaconRemediateStateMachine"]["Properties"][
        "DefinitionString"
    ]
    for key in subs:
        raw = raw.replace("${" + key + "}", "fn")
    return json.loads(raw)


def _path(data: Any, path: str) -> Any:
    assert path.startswith("$"), path
    cur = data
    for part in [p for p in path[1:].split(".") if p]:
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(
                f"JSONPath {path}: missing '{part}' in "
                f"{list(cur) if isinstance(cur, dict) else type(cur)}"
            )
        cur = cur[part]
    return cur


def _resolve(template: Any, data: Any) -> Any:
    if isinstance(template, dict):
        out: dict[str, Any] = {}
        for k, v in template.items():
            if k.endswith(".$"):
                out[k[:-2]] = _path(data, v)
            else:
                out[k] = _resolve(v, data)
        return out
    if isinstance(template, list):
        return [_resolve(v, data) for v in template]
    return template


def _set_path(data: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    parts = [p for p in path[1:].split(".") if p]
    cur = data
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value
    return data


def run_machine(
    asl: dict[str, Any], start_input: dict[str, Any], invoke: Any, max_steps: int = 60
) -> tuple[str, dict[str, Any], list[str]]:
    state_name = asl["StartAt"]
    data: dict[str, Any] = dict(start_input)
    visited: list[str] = []
    for _ in range(max_steps):
        state = asl["States"][state_name]
        visited.append(state_name)
        kind = state["Type"]
        if kind == "Task":
            payload = _resolve(state["Parameters"]["Payload"], data)
            raw = {"Payload": invoke(payload), "StatusCode": 200}
            selected = (
                _resolve(state["ResultSelector"], raw)
                if "ResultSelector" in state
                else raw
            )
            data = (
                _set_path(data, state["ResultPath"], selected)
                if "ResultPath" in state
                else selected
            )
        elif kind == "Pass":
            data = (
                _set_path(data, state["ResultPath"], state["Result"])
                if "ResultPath" in state
                else state["Result"]
            )
        elif kind == "Wait":
            pass
        elif kind == "Choice":
            nxt = None
            for choice in state["Choices"]:
                value = _path(data, choice["Variable"])
                if (
                    "BooleanEquals" in choice
                    and value == choice["BooleanEquals"]
                    or (
                        "NumericLessThan" in choice
                        and isinstance(value, int | float)
                        and value < choice["NumericLessThan"]
                    )
                ):
                    nxt = choice["Next"]
                if nxt:
                    break
            state_name = nxt or state["Default"]
            continue
        else:
            raise AssertionError(f"unsupported state type {kind}")
        if state.get("End"):
            return state_name, data, visited
        state_name = state["Next"]
    raise AssertionError("state machine did not terminate")


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
        }.items():
            monkeypatch.setenv(var, val)
        ddb = boto3.client("dynamodb", region_name=region)
        for name, key in ((INCIDENTS, "incident_id"), (APPROVALS, "approval_id")):
            ddb.create_table(
                TableName=name,
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
        ec2 = boto3.client("ec2", region_name=region)
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
        ecs_sg = ec2.create_security_group(GroupName="e", Description="e", VpcId=vpc)[
            "GroupId"
        ]
        rds_sg = ec2.create_security_group(GroupName="r", Description="r", VpcId=vpc)[
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
        boto3.client("ssm", region_name=region).put_parameter(
            Name="/beacon/test/golden-sg",
            Type="String",
            Value=json.dumps(actions_sg.snapshot([rds_sg, ecs_sg], ec2_client=ec2)),
        )
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
        cw.set_alarm_state(AlarmName=ALARM, StateValue="ALARM", StateReason="t")
        sns = boto3.client("sns", region_name=region)
        monkeypatch.setenv("SNS_TOPIC_ARN", sns.create_topic(Name="t")["TopicArn"])
        incident_id = store.put_incident(
            "STATUS: High\nSUMMARY: x",
            TriggerInfo(trigger_type=TriggerType.ALARM, alarm_name=ALARM),
            table_name=INCIDENTS,
            status="remediating",
        )
        approval = approvals.create(
            incident_id,
            "sg.restore_ingress",
            params,
            source="voice",
            channel="typed",
            transcript_quote="approve fix 1",
            table_name=APPROVALS,
        )
        yield {
            "ec2": ec2,
            "cw": cw,
            "params": params,
            "incident_id": incident_id,
            "approval_id": approval["approval_id"],
        }


def _start_input(env: Any, **over: Any) -> dict[str, Any]:
    return {
        "approval_id": env["approval_id"],
        "incident_id": env["incident_id"],
        "action": "sg.restore_ingress",
        "params": env["params"],
        **over,
    }


def test_happy_path_reaches_resolve_with_real_payload_shapes(env: Any) -> None:
    from beacon import remediate

    def invoke(payload: dict[str, Any]) -> dict[str, Any]:
        # CloudWatch would flip the alarm an evaluation period after the fix
        if payload["step"] == "verify" and actions_sg.postcondition(
            env["params"], ec2_client=env["ec2"]
        ):
            env["cw"].set_alarm_state(
                AlarmName=ALARM, StateValue="OK", StateReason="recovered"
            )
        return remediate.handler(payload, None)

    final, data, visited = run_machine(load_asl(), _start_input(env), invoke)
    assert final == "Resolve"
    assert visited[:5] == [
        "DryRun",
        "DryRunOk",
        "RequireApproval",
        "ApprovalOk",
        "Execute",
    ]
    assert "Verify" in visited and visited.count("Wait30") >= 1
    assert (
        data["execute"]["Payload"]["ok"] is True
        and data["execute"]["Payload"]["executed_at"]
    )
    assert data["verify"]["Payload"]["ok"] is True
    assert data["resolve"]["Payload"]["handled_by"] == "voice"
    incident = store.get_incident(env["incident_id"], table_name=INCIDENTS)
    assert incident["status"] == "resolved"


def test_missing_approval_escalates_before_execute(env: Any) -> None:
    from beacon import remediate

    final, data, visited = run_machine(
        load_asl(),
        _start_input(env, approval_id="nope"),
        lambda p: remediate.handler(p, None),
    )
    assert final == "Escalate" and "Execute" not in visited
    assert "no approval" in data["escalate"]["Payload"]["reason"]
    assert actions_sg.postcondition(env["params"], ec2_client=env["ec2"]) is False


def test_verify_loop_is_bounded_then_escalates(env: Any) -> None:
    from beacon import remediate

    final, data, visited = run_machine(
        load_asl(), _start_input(env), lambda p: remediate.handler(p, None)
    )
    assert final == "Escalate"
    assert visited.count("Verify") == 6
    assert data["verify"]["Payload"]["attempts"] == 6
    assert "verify:" in data["escalate"]["Payload"]["reason"]
    assert (
        store.get_incident(env["incident_id"], table_name=INCIDENTS)["status"]
        == "escalated"
    )


def test_kill_switch_escalates_at_execute(env: Any, monkeypatch: Any) -> None:
    from beacon import remediate

    monkeypatch.setenv("APPLY_ENABLED", "false")
    final, data, visited = run_machine(
        load_asl(), _start_input(env), lambda p: remediate.handler(p, None)
    )
    assert final == "Escalate" and visited[-2] == "ExecuteOk"
    assert "apply disabled" in data["escalate"]["Payload"]["reason"]


def test_started_input_shape_matches_what_voice_and_handler_send() -> None:
    """The keys the ASL reads from $ are exactly what start_execution sends."""
    asl = load_asl()
    needed: set[str] = set()
    for state in asl["States"].values():
        for v in (state.get("Parameters", {}).get("Payload") or {}).values():
            if isinstance(v, str) and v.startswith("$.") and "." not in v[2:]:
                needed.add(v[2:])
    assert needed == {"approval_id", "incident_id", "action", "params"}
