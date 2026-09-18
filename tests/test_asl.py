"""The Step Functions definition is validated from the template itself, so the
test and the deploy always read the same source."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

TEMPLATE = Path(__file__).parent.parent / "remediation-template.yaml"


class _CfnLoader(yaml.SafeLoader):
    pass


def _tag(loader: Any, tag_suffix: str, node: Any) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_mapping(node, deep=True)


_CfnLoader.add_multi_constructor("!", _tag)


def _definition() -> dict[str, Any]:
    template = yaml.load(TEMPLATE.read_text(), Loader=_CfnLoader)
    machine = template["Resources"]["BeaconRemediateStateMachine"]["Properties"]
    raw, substitutions = machine["DefinitionString"]
    for key in substitutions:
        raw = raw.replace(
            "${" + key + "}", f"arn:aws:lambda:us-east-1:123:function:{key}"
        )
    return json.loads(raw)


def test_definition_is_valid_json_with_expected_states() -> None:
    asl = _definition()
    assert asl["StartAt"] == "DryRun"
    assert set(asl["States"]) == {
        "DryRun",
        "DryRunOk",
        "RequireApproval",
        "ApprovalOk",
        "Execute",
        "ExecuteOk",
        "InitVerify",
        "Wait30",
        "Verify",
        "Verified",
        "Resolve",
        "Escalate",
    }


def test_every_transition_targets_an_existing_state() -> None:
    asl = _definition()
    names = set(asl["States"])
    for name, state in asl["States"].items():
        targets = []
        if "Next" in state:
            targets.append(state["Next"])
        if "Default" in state:
            targets.append(state["Default"])
        for choice in state.get("Choices", []):
            targets.append(choice["Next"])
        for target in targets:
            assert target in names, f"{name} -> {target} does not exist"
        assert state.get("End") or targets, f"{name} is a dead end"


def test_verify_loop_is_bounded_and_every_failure_escalates() -> None:
    asl = _definition()
    verified = asl["States"]["Verified"]
    assert verified["Default"] == "Escalate"
    bound = [c for c in verified["Choices"] if "NumericLessThan" in c][0]
    assert bound["NumericLessThan"] == 6 and bound["Next"] == "Wait30"
    for gate in ("DryRunOk", "ApprovalOk", "ExecuteOk"):
        assert asl["States"][gate]["Default"] == "Escalate"


def test_execute_only_runs_after_dry_run_and_approval() -> None:
    asl = _definition()
    assert asl["States"]["DryRunOk"]["Choices"][0]["Next"] == "RequireApproval"
    assert asl["States"]["ApprovalOk"]["Choices"][0]["Next"] == "Execute"
    for step_state in (
        "DryRun",
        "RequireApproval",
        "Execute",
        "Verify",
        "Resolve",
        "Escalate",
    ):
        payload = asl["States"][step_state]["Parameters"]["Payload"]
        assert (
            payload["step"]
            == {
                "DryRun": "dryrun",
                "RequireApproval": "require_approval",
                "Execute": "execute",
                "Verify": "verify",
                "Resolve": "resolve",
                "Escalate": "escalate",
            }[step_state]
        )
