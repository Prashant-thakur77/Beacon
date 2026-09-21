"""Fix at the source: the deterministic patch and the pull request."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from beacon import fix_pr

TEMPLATE_WITH_RULE = """AWSTemplateFormatVersion: "2010-09-09"
Resources:
  EcsSecurityGroup:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: tasks
      VpcId: !Ref DemoVPC
  RdsSecurityGroup:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: db
      VpcId: !Ref DemoVPC
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 5432
          ToPort: 5432
          SourceSecurityGroupId: !Ref EcsSecurityGroup
Outputs:
  RdsSecurityGroupId:
    Value: !Ref RdsSecurityGroup
"""

TEMPLATE_WITHOUT_RULE = TEMPLATE_WITH_RULE.replace(
    """      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 5432
          ToPort: 5432
          SourceSecurityGroupId: !Ref EcsSecurityGroup
""",
    "",
)

PARAMS = {
    "group_id": "sg-rds",
    "source_group_id": "sg-ecs",
    "ip_protocol": "tcp",
    "from_port": 5432,
    "to_port": 5432,
}


@pytest.fixture()
def logical(mocker: Any) -> Any:
    return mocker.patch(
        "beacon.fix_pr.logical_ids",
        return_value={"sg-rds": "RdsSecurityGroup", "sg-ecs": "EcsSecurityGroup"},
    )


def test_template_declares_rule_sees_inline_and_standalone_rules() -> None:
    kw = {
        "group_logical": "RdsSecurityGroup",
        "source_logical": "EcsSecurityGroup",
        "port": 5432,
        "protocol": "tcp",
    }
    assert fix_pr.template_declares_rule(TEMPLATE_WITH_RULE, **kw) is True
    assert fix_pr.template_declares_rule(TEMPLATE_WITHOUT_RULE, **kw) is False
    patched = fix_pr.patch_template(
        TEMPLATE_WITHOUT_RULE,
        fix_pr.ingress_resource_text(incident_id="abc", **kw),
    )
    assert fix_pr.template_declares_rule(patched, **kw) is True
    # inserted before Outputs, indented like the other resources
    assert patched.index("BeaconRdsSecurityGroupRestored5432Ingress") < patched.index(
        "Outputs:"
    )
    assert "      GroupId: !Ref RdsSecurityGroup" in patched
    assert "      SourceSecurityGroupId: !Ref EcsSecurityGroup" in patched


def test_plan_patch_is_decided_by_code(logical: Any) -> None:
    missing = fix_pr.plan_patch(
        action="sg.restore_ingress",
        params=PARAMS,
        incident_id="abc",
        template_text=TEMPLATE_WITHOUT_RULE,
    )
    assert (
        missing["kind"] == "template_patch"
        and "Restored5432Ingress" in missing["patched"]
    )
    present = fix_pr.plan_patch(
        action="sg.restore_ingress",
        params=PARAMS,
        incident_id="abc",
        template_text=TEMPLATE_WITH_RULE,
    )
    assert present["kind"] == "postmortem_only" and "out-of-band" in present["reason"]
    ecs = fix_pr.plan_patch(
        action="ecs.force_redeploy",
        params={"cluster": "c", "service": "s"},
        incident_id="abc",
        template_text=TEMPLATE_WITH_RULE,
    )
    assert ecs["kind"] == "postmortem_only" and "no code change" in ecs["reason"]
    logical.return_value = {}
    unmanaged = fix_pr.plan_patch(
        action="sg.restore_ingress",
        params=PARAMS,
        incident_id="abc",
        template_text=TEMPLATE_WITHOUT_RULE,
    )
    assert (
        unmanaged["kind"] == "postmortem_only"
        and "CloudFormation" in unmanaged["reason"]
    )


def test_said_phrase() -> None:
    assert fix_pr.said_phrase("Okay, open the pull request.")
    assert fix_pr.said_phrase("open a PR")
    assert fix_pr.said_phrase("pull request kholo")
    assert not fix_pr.said_phrase("what is a pull request")


class FakeGitHub:
    """Enough of the REST API for branch + contents + pulls, with a fixed base."""

    def __init__(self, template: str | None) -> None:
        self.template = template
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.files: dict[str, str] = {}

    def __call__(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((method, path, body))
        if method == "GET" and path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "basesha"}}
        if method == "POST" and path.endswith("/git/refs"):
            return {"ref": body["ref"]}
        if method == "GET" and "/contents/" in path:
            file_path = path.split("/contents/")[1].split("?")[0]
            if (
                file_path == "demo/demo-infra-template.yaml"
                and self.template is not None
            ):
                return {
                    "content": base64.b64encode(self.template.encode()).decode(),
                    "sha": "tplsha",
                }
            raise RuntimeError(f"github GET {path}: 404 not found")
        if method == "PUT" and "/contents/" in path:
            file_path = path.split("/contents/")[1]
            self.files[file_path] = base64.b64decode(body["content"]).decode()
            return {"content": {"path": file_path}}
        if method == "POST" and path.endswith("/pulls"):
            return {"html_url": "https://github.com/o/r/pull/41", "number": 41}
        raise AssertionError(f"unexpected {method} {path}")


INCIDENT = {
    "incident_id": "abcdef12-0000-0000-0000-000000000000",
    "alarm_name": "beacon-demo-infra-errors",
    "timestamp": "2026-09-21T15:41:00+00:00",
    "status": "resolved",
    "rca_json": {"summary": "The rule vanished."},
    "changes": [
        {
            "event_name": "RevokeSecurityGroupIngress",
            "actor_short": "user/deployer",
            "event_time": "2026-09-21T15:40:41Z",
            "resource_ids": ["sg-rds"],
        }
    ],
    "timeline": [
        {
            "event": "verify_attempt",
            "detail": {
                "attempt": 5,
                "checks": [
                    {"name": "alarm OK", "ok": True},
                    {"name": "rule present", "ok": True},
                ],
            },
        }
    ],
}
APPROVAL = {
    "transcript_quote": "approve fix 1",
    "channel": "assemblyai",
    "granted_at": "2026-09-21T15:50:00+00:00",
    "attestation": {"confidence": 0.96, "session_id": "sess-1"},
}


def test_open_pr_patches_the_template_and_adds_the_postmortem(
    monkeypatch: pytest.MonkeyPatch, logical: Any, mocker: Any
) -> None:
    monkeypatch.setenv("FIX_PR_REPO", "o/r")
    monkeypatch.setenv("GITHUB_PR_TOKEN", "ghp_x")
    gh = FakeGitHub(TEMPLATE_WITHOUT_RULE)
    mocker.patch("beacon.fix_pr.gh", side_effect=gh)
    out = fix_pr.open_pr(
        INCIDENT,
        action="sg.restore_ingress",
        params=PARAMS,
        postmortem_md="# Postmortem\n",
        approval=APPROVAL,
        link="https://console/#board/abc",
    )
    assert out["url"] == "https://github.com/o/r/pull/41" and out["number"] == 41
    assert (
        out["kind"] == "template_patch" and out["branch"] == "beacon/incident-abcdef12"
    )
    assert out["files"] == [
        "demo/demo-infra-template.yaml",
        "docs/incidents/2026-09-21-beacon-demo-infra-errors-abcdef12.md",
    ]
    assert (
        "BeaconRdsSecurityGroupRestored5432Ingress"
        in gh.files["demo/demo-infra-template.yaml"]
    )
    assert (
        gh.files["docs/incidents/2026-09-21-beacon-demo-infra-errors-abcdef12.md"]
        == "# Postmortem\n"
    )
    pr = [b for m, p, b in gh.calls if m == "POST" and p.endswith("/pulls")][0]
    assert pr["head"] == "beacon/incident-abcdef12" and pr["base"] == "main"
    assert pr["title"].startswith(
        "Restore RdsSecurityGroup ingress from EcsSecurityGroup"
    )
    body = pr["body"]
    assert (
        "“approve fix 1” — via **assemblyai**" in body
        and "96%" in body
        and "sess-1" in body
    )
    assert "2/2 checks on attempt 5" in body and "RevokeSecurityGroupIngress" in body
    assert "Nothing is merged by Beacon" in body


def test_open_pr_without_a_code_change_carries_the_postmortem_only(
    monkeypatch: pytest.MonkeyPatch, logical: Any, mocker: Any
) -> None:
    monkeypatch.setenv("FIX_PR_REPO", "o/r")
    monkeypatch.setenv("GITHUB_PR_TOKEN", "ghp_x")
    gh = FakeGitHub(TEMPLATE_WITH_RULE)
    mocker.patch("beacon.fix_pr.gh", side_effect=gh)
    out = fix_pr.open_pr(
        INCIDENT,
        action="sg.restore_ingress",
        params=PARAMS,
        postmortem_md="# PM\n",
        approval=None,
        link="",
    )
    assert out["kind"] == "postmortem_only" and out["files"] == [
        "docs/incidents/2026-09-21-beacon-demo-infra-errors-abcdef12.md"
    ]
    assert "demo/demo-infra-template.yaml" not in gh.files
    pr = [b for m, p, b in gh.calls if m == "POST" and p.endswith("/pulls")][0]
    assert (
        pr["title"].startswith("Postmortem:")
        and "out-of-band" in pr["body"]
        and "_no approval record_" in pr["body"]
    )


def test_open_pr_refuses_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIX_PR_REPO", raising=False)
    monkeypatch.delenv("GITHUB_PR_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        fix_pr.open_pr(
            INCIDENT,
            action="sg.restore_ingress",
            params=PARAMS,
            postmortem_md="",
            approval=None,
            link="",
        )
    assert json.dumps(PARAMS)  # params stay JSON-serialisable for the PR body
