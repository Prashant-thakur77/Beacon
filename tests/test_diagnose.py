from __future__ import annotations

from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon import diagnose
from beacon.remediation import actions_sg


@pytest.fixture()
def env() -> Any:
    with mock_aws():
        ec2 = boto3.client("ec2", region_name="us-east-1")
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
        yield {
            "ec2": ec2,
            "params": params,
            "golden": golden,
            "rds_sg": rds_sg,
            "ecs_sg": ecs_sg,
        }


def test_no_drift_on_a_healthy_stack(env: Any) -> None:
    missing = diagnose.sg_drift(env["golden"], ec2_client=env["ec2"])
    assert missing == []
    text = diagnose.format_diagnostics(missing, env["golden"])
    assert "No security group drift" in text and "1 golden rule" in text


def test_revoked_rule_is_reported_with_exact_ids(env: Any) -> None:
    p = env["params"]
    env["ec2"].revoke_security_group_ingress(
        GroupId=p["group_id"], IpPermissions=[actions_sg.ip_permission(p)]
    )
    missing = diagnose.sg_drift(env["golden"], ec2_client=env["ec2"])
    assert len(missing) == 1
    rule = missing[0]
    assert rule.group_id == env["rds_sg"] and rule.source_group_id == env["ecs_sg"]
    assert rule.from_port == 5432 and rule.ip_protocol == "tcp"

    text = diagnose.format_diagnostics(missing, env["golden"])
    assert (
        "MISSING" in text
        and env["rds_sg"] in text
        and env["ecs_sg"] in text
        and "5432" in text
    )


def test_suggested_fix_maps_missing_rule_to_restore_ingress(env: Any) -> None:
    p = env["params"]
    env["ec2"].revoke_security_group_ingress(
        GroupId=p["group_id"], IpPermissions=[actions_sg.ip_permission(p)]
    )
    missing = diagnose.sg_drift(env["golden"], ec2_client=env["ec2"])
    fix = diagnose.suggested_fix(missing)
    assert fix is not None
    action, params = fix
    assert action == "sg.restore_ingress" and params == p
    assert diagnose.suggested_fix([]) is None


def test_run_returns_section_and_structured_data(env: Any) -> None:
    p = env["params"]
    env["ec2"].revoke_security_group_ingress(
        GroupId=p["group_id"], IpPermissions=[actions_sg.ip_permission(p)]
    )
    result = diagnose.run(env["golden"], ec2_client=env["ec2"])
    assert result["missing_rules"][0]["group_id"] == env["rds_sg"]
    assert result["suggested_action"] == "sg.restore_ingress"
    assert result["action_params"] == p
    assert "MISSING" in result["text"]


@pytest.fixture()
def ecs_env(monkeypatch: Any) -> Any:
    with mock_aws():
        ecs = boto3.client("ecs", region_name="us-east-1")
        ecs.create_cluster(clusterName="beacon-demo")
        ecs.register_task_definition(
            family="webapp",
            containerDefinitions=[{"name": "webapp", "image": "x", "memory": 128}],
        )
        ecs.create_service(
            cluster="beacon-demo",
            serviceName="beacon-demo-webapp",
            taskDefinition="webapp",
            desiredCount=1,
        )
        monkeypatch.setenv("REMEDIABLE_ECS_SERVICES", "beacon-demo/beacon-demo-webapp")
        yield ecs


def test_ecs_health_lists_remediable_services_with_exact_ids(ecs_env: Any) -> None:
    services = diagnose.ecs_health(ecs_client=ecs_env)
    assert len(services) == 1
    svc = services[0]
    assert svc["cluster"] == "beacon-demo" and svc["service"] == "beacon-demo-webapp"
    assert svc["status"] == "ACTIVE" and svc["desired"] == 1
    assert "deployments" in svc and svc["action"] == "ecs.force_redeploy"
    assert svc["action_params"] == {
        "cluster": "beacon-demo",
        "service": "beacon-demo-webapp",
    }


def test_ecs_health_is_empty_without_configuration(monkeypatch: Any) -> None:
    monkeypatch.delenv("REMEDIABLE_ECS_SERVICES", raising=False)
    assert diagnose.ecs_health() == []


def test_run_includes_ecs_section_and_keeps_sg_fix_priority(
    env: Any, ecs_env: Any
) -> None:
    p = env["params"]
    result = diagnose.run(env["golden"], ec2_client=env["ec2"], ecs_client=ecs_env)
    assert "Remediable ECS services" in result["text"]
    assert "beacon-demo/beacon-demo-webapp" in result["text"]
    assert result["ecs_services"][0]["service"] == "beacon-demo-webapp"
    # no drift: no deterministic fix; the model still gets exact ECS ids
    assert result["suggested_action"] is None
    env["ec2"].revoke_security_group_ingress(
        GroupId=p["group_id"], IpPermissions=[actions_sg.ip_permission(p)]
    )
    result = diagnose.run(env["golden"], ec2_client=env["ec2"], ecs_client=ecs_env)
    assert result["suggested_action"] == "sg.restore_ingress"
