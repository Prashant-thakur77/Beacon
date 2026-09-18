"""Lambda for the remediation write path (``beacon-remediate-<stack>``).

Runs under ``BeaconRemediatorRole``, the only role with the two allowlisted
write actions.  Invoked by the voice Lambda for a proposal-time dry-run and
by the Step Functions loop for each state.  Event shape::

    {"step": "dryrun", "action": "sg.restore_ingress", "params": {...}}
"""

from __future__ import annotations

import logging
from typing import Any

import boto3

from beacon.remediation import registry
from beacon.remediation.base import ParamError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _caller_arn() -> str:
    try:
        return str(boto3.client("sts").get_caller_identity()["Arn"])
    except Exception:
        logger.exception("get_caller_identity failed")
        return "unknown"


def _validated(event: dict[str, Any]) -> tuple[registry.ActionSpec, dict[str, Any]]:
    action_id = str(event.get("action", ""))
    params = registry.validate_params(action_id, event.get("params") or {})
    spec = registry.get_action(action_id)
    assert spec is not None  # validate_params already rejected unknown ids
    return spec, params


def dryrun(event: dict[str, Any]) -> dict[str, Any]:
    """Precondition + provider dry-run for one action, under this Lambda's role."""
    try:
        spec, params = _validated(event)
    except ParamError as exc:
        return {"ok": False, "error": str(exc), "action": event.get("action")}

    error = spec.precondition(params)
    if error:
        return {"ok": False, "error": error, "action": spec.id, "params": params}

    result = spec.dry_run(params)
    return {
        "ok": result.ok,
        "code": result.code,
        "detail": result.detail,
        "error": None
        if result.ok
        else f"dry run failed: {result.code} {result.detail}",
        "action": spec.id,
        "params": params,
        "blast_radius": spec.blast_radius(params),
        "role": _caller_arn(),
    }


_STEPS = {"dryrun": dryrun}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    step = str(event.get("step", ""))
    fn = _STEPS.get(step)
    if fn is None:
        return {"ok": False, "error": f"unknown step: {step or '<missing>'}"}
    logger.info("remediate step=%s action=%s", step, event.get("action"))
    return fn(event)
