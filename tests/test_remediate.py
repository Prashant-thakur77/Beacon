from __future__ import annotations

from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon.remediate import handler


@pytest.fixture()
def env(monkeypatch: Any) -> Any:
    with mock_aws():
        ec2 = boto3.client("ec2", region_name="us-east-1")
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
        ecs_sg = ec2.create_security_group(GroupName="ecs", Description="e", VpcId=vpc)[
            "GroupId"
        ]
        rds_sg = ec2.create_security_group(GroupName="rds", Description="r", VpcId=vpc)[
            "GroupId"
        ]
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        monkeypatch.setenv("GOLDEN_SG_PARAM", "/beacon/test/golden-sg")
        ssm = boto3.client("ssm", region_name="us-east-1")
        import json

        ssm.put_parameter(
            Name="/beacon/test/golden-sg",
            Type="String",
            Value=json.dumps(
                {
                    rds_sg: [
                        {
                            "IpProtocol": "tcp",
                            "FromPort": 5432,
                            "ToPort": 5432,
                            "UserIdGroupPairs": [{"GroupId": ecs_sg}],
                        }
                    ]
                }
            ),
        )
        params = {
            "group_id": rds_sg,
            "ip_protocol": "tcp",
            "from_port": 5432,
            "to_port": 5432,
            "source_group_id": ecs_sg,
        }
        yield {"ec2": ec2, "params": params}


def test_dryrun_step_passes_for_allowlisted_action_in_golden_snapshot(env: Any) -> None:
    result = handler(
        {"step": "dryrun", "action": "sg.restore_ingress", "params": env["params"]},
        None,
    )
    assert result["ok"] is True
    assert result["action"] == "sg.restore_ingress"
    assert result["code"] in ("DryRunOperation", "InvalidPermission.Duplicate")
    assert "arn:aws:" in result["role"]
    assert "5432" in result["blast_radius"]


def test_dryrun_step_rejects_unknown_action(env: Any) -> None:
    result = handler({"step": "dryrun", "action": "rds.reboot", "params": {}}, None)
    assert result["ok"] is False
    assert "not allowlisted" in result["error"]


def test_dryrun_step_rejects_rule_outside_golden_snapshot(env: Any) -> None:
    params = {**env["params"], "from_port": 22, "to_port": 22}
    result = handler(
        {"step": "dryrun", "action": "sg.restore_ingress", "params": params}, None
    )
    assert result["ok"] is False
    assert "golden snapshot" in result["error"]


def test_dryrun_step_rejects_invalid_params(env: Any) -> None:
    params = {**env["params"], "from_port": "5432"}
    result = handler(
        {"step": "dryrun", "action": "sg.restore_ingress", "params": params}, None
    )
    assert result["ok"] is False
    assert "from_port" in result["error"]


def test_unknown_step_is_an_error(env: Any) -> None:
    result = handler({"step": "launch_nukes"}, None)
    assert result["ok"] is False
    assert "unknown step" in result["error"]
