from __future__ import annotations

from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon.remediation import actions_ecs


@pytest.fixture()
def ecs_env(monkeypatch: Any) -> Any:
    monkeypatch.setenv("REMEDIABLE_ECS_SERVICES", "beacon-demo/beacon-demo-webapp")
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
        yield ecs, {"cluster": "beacon-demo", "service": "beacon-demo-webapp"}


def test_dry_run_passes_for_active_service_and_fails_for_missing(ecs_env: Any) -> None:
    ecs, params = ecs_env
    assert actions_ecs.dry_run(params, ecs_client=ecs).ok is True
    # an unconfigured service fails the data allowlist before anything is looked up
    missing = actions_ecs.dry_run({**params, "service": "nope"}, ecs_client=ecs)
    assert missing.ok is False and "not configured" in missing.detail
    # a configured but absent service fails on lookup
    ecs.delete_service(cluster="beacon-demo", service="beacon-demo-webapp", force=True)
    gone = actions_ecs.dry_run(params, ecs_client=ecs)
    assert gone.ok is False and (
        "not found" in gone.detail or "not ACTIVE" in gone.detail
    )


def test_execute_starts_a_deployment_and_postcondition_holds(ecs_env: Any) -> None:
    ecs, params = ecs_env
    result = actions_ecs.execute(params, ecs_client=ecs)
    assert result.ok is True and result.code == "DeploymentStarted"
    assert actions_ecs.postcondition(params, ecs_client=ecs) is True


def test_precondition_requires_the_service_to_be_configured_as_remediable(
    ecs_env: Any, monkeypatch: Any
) -> None:
    ecs, params = ecs_env
    monkeypatch.setenv("REMEDIABLE_ECS_SERVICES", "other-cluster/other-service")
    assert "not configured" in str(actions_ecs.precondition(params, ecs_client=ecs))
    monkeypatch.setenv("REMEDIABLE_ECS_SERVICES", "beacon-demo/beacon-demo-webapp")
    assert actions_ecs.precondition(params, ecs_client=ecs) is None
