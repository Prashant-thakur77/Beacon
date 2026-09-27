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


def test_postcondition_waits_for_the_new_deployment_to_settle(ecs_env: Any) -> None:
    """A redeploy is not "done" the moment it is asked for.

    Immediately after `execute` the rollout is in progress and no task is running
    yet, so the post-condition must say no — otherwise the verify loop would call a
    still-crashlooping service recovered.
    """
    ecs, params = ecs_env
    result = actions_ecs.execute(params, ecs_client=ecs)
    assert result.ok is True and result.code == "DeploymentStarted"
    assert actions_ecs.postcondition(params, ecs_client=ecs) is False

    class Settled:
        """What the service looks like once the rollout has finished."""

        def describe_services(self, **_: Any) -> dict[str, Any]:
            return {
                "services": [
                    {
                        "serviceName": params["service"],
                        "status": "ACTIVE",
                        "desiredCount": 1,
                        "runningCount": 1,
                        "deployments": [
                            {
                                "status": "PRIMARY",
                                "rolloutState": "COMPLETED",
                                "runningCount": 1,
                                "desiredCount": 1,
                            }
                        ],
                    }
                ]
            }

    assert actions_ecs.postcondition(params, ecs_client=Settled()) is True

    class StillDraining(Settled):
        def describe_services(self, **_: Any) -> dict[str, Any]:
            out = super().describe_services()
            out["services"][0]["deployments"].append(
                {"status": "ACTIVE", "rolloutState": "COMPLETED"}
            )
            return out

    # old tasks still draining: not settled
    assert actions_ecs.postcondition(params, ecs_client=StillDraining()) is False


def test_precondition_requires_the_service_to_be_configured_as_remediable(
    ecs_env: Any, monkeypatch: Any
) -> None:
    ecs, params = ecs_env
    monkeypatch.setenv("REMEDIABLE_ECS_SERVICES", "other-cluster/other-service")
    assert "not configured" in str(actions_ecs.precondition(params, ecs_client=ecs))
    monkeypatch.setenv("REMEDIABLE_ECS_SERVICES", "beacon-demo/beacon-demo-webapp")
    assert actions_ecs.precondition(params, ecs_client=ecs) is None


def test_each_action_gets_its_own_verification_budget() -> None:
    """Escalating a fix that worked is its own kind of wrong.

    A restored rule works the instant it lands; a replaced task has to start, warm
    up and then produce clean CloudWatch periods. A single global window either
    escalates good restarts or waits far too long on a rule that will never come back.
    """
    from beacon.remediation import registry

    sg_wait, sg_attempts = registry.verify_budget("sg.restore_ingress")
    ecs_wait, ecs_attempts = registry.verify_budget("ecs.force_redeploy")
    assert ecs_wait * ecs_attempts > sg_wait * sg_attempts * 2
    assert registry.verify_budget("nonexistent") == (30, 6)
