from __future__ import annotations

from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon.remediation import actions_sg


@pytest.fixture()
def sg_env() -> Any:
    with mock_aws():
        ec2 = boto3.client("ec2", region_name="us-east-1")
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
        ecs_sg = ec2.create_security_group(
            GroupName="ecs", Description="ecs", VpcId=vpc
        )["GroupId"]
        rds_sg = ec2.create_security_group(
            GroupName="rds", Description="rds", VpcId=vpc
        )["GroupId"]
        params = {
            "group_id": rds_sg,
            "ip_protocol": "tcp",
            "from_port": 5432,
            "to_port": 5432,
            "source_group_id": ecs_sg,
        }
        ec2.authorize_security_group_ingress(
            GroupId=rds_sg,
            IpPermissions=[actions_sg.ip_permission(params)],
        )
        yield ec2, params


def _revoke(ec2: Any, params: dict[str, Any]) -> None:
    ec2.revoke_security_group_ingress(
        GroupId=params["group_id"], IpPermissions=[actions_sg.ip_permission(params)]
    )


def test_postcondition_reflects_rule_presence(sg_env: Any) -> None:
    ec2, params = sg_env
    assert actions_sg.postcondition(params, ec2_client=ec2) is True
    _revoke(ec2, params)
    assert actions_sg.postcondition(params, ec2_client=ec2) is False


def test_dry_run_passes_when_rule_is_missing_and_caller_is_authorised(
    sg_env: Any,
) -> None:
    ec2, params = sg_env
    _revoke(ec2, params)
    result = actions_sg.dry_run(params, ec2_client=ec2)
    assert result.ok is True
    assert result.code in ("DryRunOperation", "InvalidPermission.Duplicate")


def test_dry_run_passes_when_rule_already_present(sg_env: Any) -> None:
    ec2, params = sg_env
    result = actions_sg.dry_run(params, ec2_client=ec2)
    assert result.ok is True


def test_dry_run_fails_on_unauthorised_operation(sg_env: Any, mocker: Any) -> None:
    from botocore.exceptions import ClientError

    ec2, params = sg_env
    mocker.patch.object(
        ec2,
        "authorize_security_group_ingress",
        side_effect=ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "not allowed"}},
            "AuthorizeSecurityGroupIngress",
        ),
    )
    result = actions_sg.dry_run(params, ec2_client=ec2)
    assert result.ok is False
    assert result.code == "UnauthorizedOperation"


def test_execute_restores_the_rule_and_is_idempotent(sg_env: Any) -> None:
    ec2, params = sg_env
    _revoke(ec2, params)
    first = actions_sg.execute(params, ec2_client=ec2)
    assert first.ok is True and actions_sg.postcondition(params, ec2_client=ec2)
    second = actions_sg.execute(params, ec2_client=ec2)
    assert second.ok is True
    assert second.code == "InvalidPermission.Duplicate"


def test_in_golden_snapshot_requires_exact_rule_match() -> None:
    params = {
        "group_id": "sg-rds",
        "ip_protocol": "tcp",
        "from_port": 5432,
        "to_port": 5432,
        "source_group_id": "sg-ecs",
    }
    snapshot = {
        "sg-rds": [
            {
                "IpProtocol": "tcp",
                "FromPort": 5432,
                "ToPort": 5432,
                "UserIdGroupPairs": [{"GroupId": "sg-ecs"}],
            }
        ]
    }
    assert actions_sg.in_golden_snapshot(params, snapshot) is True
    assert (
        actions_sg.in_golden_snapshot(
            {**params, "from_port": 22, "to_port": 22}, snapshot
        )
        is False
    )
    assert (
        actions_sg.in_golden_snapshot({**params, "group_id": "sg-other"}, snapshot)
        is False
    )


def test_snapshot_from_describe_output_keeps_only_rules(sg_env: Any) -> None:
    ec2, params = sg_env
    snapshot = actions_sg.snapshot([params["group_id"]], ec2_client=ec2)
    assert list(snapshot) == [params["group_id"]]
    assert snapshot[params["group_id"]][0]["FromPort"] == 5432
    assert (
        snapshot[params["group_id"]][0]["UserIdGroupPairs"][0]["GroupId"]
        == params["source_group_id"]
    )
