"""``ecs.force_redeploy``: roll an ECS service to fresh tasks."""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from beacon.remediation.base import ActionResult

logger = logging.getLogger(__name__)


def _ecs(client: Any | None) -> Any:
    return client if client is not None else boto3.client("ecs")


def _describe(params: dict[str, Any], client: Any) -> dict[str, Any] | None:
    resp = client.describe_services(
        cluster=params["cluster"], services=[params["service"]]
    )
    services = resp.get("services", [])
    return services[0] if services else None


def blast_radius(params: dict[str, Any]) -> str:
    return (
        f"1 ECS service: {params['service']} in cluster {params['cluster']} gets a "
        "rolling force-new-deployment (same task definition). Brief capacity churn."
    )


def is_configured(params: dict[str, Any]) -> bool:
    """The data allowlist for ECS: only services named in REMEDIABLE_ECS_SERVICES."""
    raw = os.environ.get("REMEDIABLE_ECS_SERVICES", "")
    allowed = {t.strip() for t in raw.split(",") if "/" in t}
    return f"{params.get('cluster')}/{params.get('service')}" in allowed


def precondition(
    params: dict[str, Any], *, ecs_client: Any | None = None
) -> str | None:
    if not is_configured(params):
        return (
            f"{params.get('cluster')}/{params.get('service')} is not configured as "
            "remediable (REMEDIABLE_ECS_SERVICES)"
        )
    service = _describe(params, _ecs(ecs_client))
    if service is None:
        return "service not found"
    if service.get("status") != "ACTIVE":
        return f"service status is {service.get('status')}, not ACTIVE"
    return None


def dry_run(params: dict[str, Any], *, ecs_client: Any | None = None) -> ActionResult:
    """ECS has no DryRun flag; the check is that the service is ACTIVE."""
    error = precondition(params, ecs_client=ecs_client)
    if error:
        return ActionResult(ok=False, code="PreconditionFailed", detail=error)
    return ActionResult(ok=True, code="ServiceActive", detail="service is ACTIVE")


def execute(params: dict[str, Any], *, ecs_client: Any | None = None) -> ActionResult:
    try:
        resp = _ecs(ecs_client).update_service(
            cluster=params["cluster"],
            service=params["service"],
            forceNewDeployment=True,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        return ActionResult(
            ok=False, code=code, detail=exc.response["Error"].get("Message", "")
        )
    deployments = resp.get("service", {}).get("deployments", [])
    deployment_id = deployments[0].get("id", "") if deployments else ""
    return ActionResult(ok=True, code="DeploymentStarted", detail=deployment_id)


def postcondition(params: dict[str, Any], *, ecs_client: Any | None = None) -> bool:
    service = _describe(params, _ecs(ecs_client))
    if service is None:
        return False
    return service.get("status") == "ACTIVE" and bool(service.get("deployments"))
