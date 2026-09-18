"""The safety model, proven from the CloudFormation templates themselves.

Judges (and future maintainers) should not have to trust the README: these
tests parse the real templates and assert the two-role split.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parent.parent
WRITE_ACTIONS = {"ec2:AuthorizeSecurityGroupIngress", "ecs:UpdateService"}
FORBIDDEN_PREFIXES = (
    "ec2:",
    "ecs:",
    "rds:",
    "iam:",
    "lambda:Update",
    "lambda:Delete",
    "s3:Put",
    "s3:Delete",
)
FORBIDDEN_READ_OK = (
    "ec2:Describe",
    "ecs:Describe",
    "rds:Describe",
    "ecs:List",
    "rds:List",
)


class _CfnLoader(yaml.SafeLoader):
    pass


def _tag(loader: Any, suffix: str, node: Any) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return {"Fn::" + suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {"Fn::" + suffix: loader.construct_sequence(node, deep=True)}
    return {"Fn::" + suffix: loader.construct_mapping(node, deep=True)}


_CfnLoader.add_multi_constructor("!", _tag)


def _load(name: str) -> dict[str, Any]:
    return yaml.load((ROOT / name).read_text(), Loader=_CfnLoader)


def _statements(role: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for policy in role["Properties"].get("Policies", []):
        out.extend(policy["PolicyDocument"]["Statement"])
    return out


def _actions(stmt: dict[str, Any]) -> list[str]:
    actions = stmt.get("Action", [])
    return [actions] if isinstance(actions, str) else list(actions)


def _all_actions(role: dict[str, Any]) -> set[str]:
    return {
        a for s in _statements(role) if s.get("Effect") == "Allow" for a in _actions(s)
    }


def _is_write(action: str) -> bool:
    return action.startswith(FORBIDDEN_PREFIXES) and not action.startswith(
        FORBIDDEN_READ_OK
    )


def _sub_text(value: Any) -> str:
    if isinstance(value, dict) and "Fn::Sub" in value:
        return str(value["Fn::Sub"])
    return str(value)


# ---------------------------------------------------------------- remediator


@pytest.fixture(scope="module")
def remediation() -> dict[str, Any]:
    return _load("remediation-template.yaml")


@pytest.fixture(scope="module")
def console() -> dict[str, Any]:
    return _load("console-template.yaml")


@pytest.fixture(scope="module")
def base() -> dict[str, Any]:
    return _load("template.yaml")


def test_remediator_write_actions_are_exactly_the_allowlist(
    remediation: dict[str, Any],
) -> None:
    role = remediation["Resources"]["BeaconRemediatorRole"]
    writes = {a for a in _all_actions(role) if _is_write(a)}
    assert writes == WRITE_ACTIONS


def test_remediator_sg_action_has_tag_condition_and_rule_statement(
    remediation: dict[str, Any],
) -> None:
    role = remediation["Resources"]["BeaconRemediatorRole"]
    sg_statements = [
        s
        for s in _statements(role)
        if "ec2:AuthorizeSecurityGroupIngress" in _actions(s)
    ]
    assert len(sg_statements) == 2, (
        "need one tagged security-group statement and one security-group-rule statement"
    )
    by_resource = {_sub_text(s["Resource"]): s for s in sg_statements}
    group_stmt = next(
        s for r, s in by_resource.items() if r.endswith("security-group/*")
    )
    rule_stmt = next(
        s for r, s in by_resource.items() if r.endswith("security-group-rule/*")
    )
    assert (
        group_stmt["Condition"]["StringEquals"]["aws:ResourceTag/beacon:remediable"]
        == "true"
    )
    assert "Condition" not in rule_stmt


def test_remediator_ecs_action_is_tag_scoped(remediation: dict[str, Any]) -> None:
    role = remediation["Resources"]["BeaconRemediatorRole"]
    ecs = [s for s in _statements(role) if "ecs:UpdateService" in _actions(s)]
    assert len(ecs) == 1
    assert (
        ecs[0]["Condition"]["StringEquals"]["aws:ResourceTag/beacon:remediable"]
        == "true"
    )


def test_remediate_function_runs_under_the_remediator_role_with_long_timeout(
    remediation: dict[str, Any],
) -> None:
    fn = remediation["Resources"]["BeaconRemediateFunction"]["Properties"]
    assert fn["Role"] == {"Fn::GetAtt": ["BeaconRemediatorRole", "Arn"]} or fn[
        "Role"
    ] == {"Fn::GetAtt": "BeaconRemediatorRole.Arn"}
    assert fn["Timeout"] >= 300
    assert fn["Environment"]["Variables"]["APPLY_ENABLED"] == {
        "Fn::Ref": "ApplyEnabled"
    } or "ApplyEnabled" in str(fn["Environment"]["Variables"]["APPLY_ENABLED"])


def test_state_machine_can_only_invoke_the_remediate_function(
    remediation: dict[str, Any],
) -> None:
    role = remediation["Resources"]["BeaconStateMachineRole"]
    assert _all_actions(role) == {"lambda:InvokeFunction"}


# ---------------------------------------------------------------- voice / dashboard


def test_voice_role_has_no_write_actions(console: dict[str, Any]) -> None:
    role = console["Resources"]["BeaconVoiceTurnRole"]
    writes = {a for a in _all_actions(role) if _is_write(a)}
    assert writes == set(), f"voice role must be read-only, found {writes}"


def test_voice_role_reaches_writes_only_through_the_remediate_lambda_and_state_machine(
    console: dict[str, Any],
) -> None:
    policy = console["Resources"]["BeaconVoiceTurnAssumePolicy"]["Properties"][
        "PolicyDocument"
    ]
    actions = {a for s in policy["Statement"] for a in _actions(s)}
    assert actions == {"sts:AssumeRole", "lambda:InvokeFunction"}
    invoke = next(
        s for s in policy["Statement"] if "lambda:InvokeFunction" in _actions(s)
    )
    assert invoke["Resource"] == {"Fn::Ref": "RemediateFunctionArn"}
    role_actions = _all_actions(console["Resources"]["BeaconVoiceTurnRole"])
    assert "states:StartExecution" in role_actions


def test_mic_role_only_allows_transcribe_streaming(console: dict[str, Any]) -> None:
    role = console["Resources"]["BeaconMicRole"]
    assert _all_actions(role) == {
        "transcribe:StartStreamTranscription",
        "transcribe:StartStreamTranscriptionWebSocket",
    }
    trust = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    # Trust the account root but only for the voice role's ARN: equivalent to
    # naming the role, without the IAM "Invalid principal" race on first deploy.
    assert _sub_text(trust["Principal"]["AWS"]).endswith(":root")
    cond = trust["Condition"]["ArnEquals"]["aws:PrincipalArn"]
    assert "beacon-voice-turn-" in _sub_text(cond)


def test_dashboard_role_is_read_only_except_contract_revoke(
    console: dict[str, Any],
) -> None:
    role = console["Resources"]["BeaconDashboardRole"]
    actions = _all_actions(role)
    assert {a for a in actions if _is_write(a)} == set()
    mutating = {
        a
        for a in actions
        if not (
            a.startswith(
                (
                    "dynamodb:Get",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "states:Describe",
                    "states:Get",
                    "lambda:Get",
                    "cloudwatch:Get",
                )
            )
        )
    }
    assert mutating == {"dynamodb:DeleteItem"}
    delete_stmt = next(
        s for s in _statements(role) if "dynamodb:DeleteItem" in _actions(s)
    )
    assert "beacon-contracts-" in _sub_text(delete_stmt["Resource"])


def test_every_traced_function_role_can_write_xray(
    console: dict[str, Any], remediation: dict[str, Any], base: dict[str, Any]
) -> None:
    for template in (console, remediation, base):
        resources = template["Resources"]
        for name, res in resources.items():
            if res.get("Type") != "AWS::Lambda::Function":
                continue
            if res["Properties"].get("TracingConfig", {}).get("Mode") != "Active":
                continue
            role_ref = res["Properties"]["Role"]["Fn::GetAtt"]
            role_name = (
                role_ref[0]
                if isinstance(role_ref, list)
                else str(role_ref).split(".")[0]
            )
            actions = _all_actions(resources[role_name])
            assert {"xray:PutTraceSegments", "xray:PutTelemetryRecords"} <= actions, (
                f"{name} is traced but {role_name} lacks X-Ray grants"
            )


# -------------------------------------------- triage role (positive grants)


def test_triage_role_has_the_grants_the_contract_path_needs(
    base: dict[str, Any],
) -> None:
    role = base["Resources"]["BeaconFunctionRole"]
    actions = _all_actions(role)
    assert {
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:Scan",
        "states:StartExecution",
        "cloudtrail:LookupEvents",
        "ssm:GetParameter",
    } <= actions
    ddb = next(s for s in _statements(role) if "dynamodb:PutItem" in _actions(s))
    resources = " ".join(_sub_text(r) for r in ddb["Resource"])
    for table in (
        "beacon-incidents-",
        "beacon-approvals-",
        "beacon-contracts-",
        "beacon-changes-",
    ):
        assert table in resources
    assert {a for a in actions if _is_write(a)} == set(), (
        "the triage role must not hold remediation write actions"
    )


def test_no_role_anywhere_has_wildcard_write(
    console: dict[str, Any], remediation: dict[str, Any], base: dict[str, Any]
) -> None:
    for template in (console, remediation, base):
        for name, res in template["Resources"].items():
            if res.get("Type") != "AWS::IAM::Role":
                continue
            for stmt in _statements(res):
                for action in _actions(stmt):
                    assert action != "*" and not action.endswith(":*"), (
                        f"{name} grants {action}"
                    )
