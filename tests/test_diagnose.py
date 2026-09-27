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
    # No drift, and moto's service reports no running task: that is a diagnosis of
    # its own, so a redeploy is proposed and labelled with the reason.
    assert result["suggested_action"] == "ecs.force_redeploy"
    assert result["action_confidence"] == "ecs_health"
    assert "tasks running" in (result["action_reason"] or "")
    env["ec2"].revoke_security_group_ingress(
        GroupId=p["group_id"], IpPermissions=[actions_sg.ip_permission(p)]
    )
    result = diagnose.run(env["golden"], ec2_client=env["ec2"], ecs_client=ecs_env)
    # drift is the specific diagnosis and outranks any restart
    assert result["suggested_action"] == "sg.restore_ingress"
    assert result["action_confidence"] == "drift"


def test_unhealthy_service_is_proposed_before_any_last_resort_restart() -> None:
    """Missing tasks are a diagnosis; a healthy-looking service is only a guess."""
    sick = {
        "cluster": "c",
        "service": "web",
        "status": "ACTIVE",
        "desired": 3,
        "running": 1,
        "deployments": [],
        "action_params": {"cluster": "c", "service": "web"},
    }
    found = diagnose.unhealthy_services([sick])
    assert found and "1 of 3 tasks running" in found[0][1]

    failed = {**sick, "running": 3, "deployments": [{"rollout": "FAILED"}]}
    assert "failed" in diagnose.unhealthy_services([failed])[0][1]

    healthy = {**sick, "running": 3, "deployments": [{"rollout": "COMPLETED"}]}
    assert diagnose.unhealthy_services([healthy]) == []
    # …and a healthy one is still restartable as a last resort, but only if it is
    # the only remediable service configured
    assert diagnose.restartable_service([healthy]) is healthy
    assert (
        diagnose.restartable_service([healthy, {**healthy, "service": "other"}]) is None
    )
