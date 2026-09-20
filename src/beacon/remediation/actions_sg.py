"""``sg.restore_ingress``: put back one security-group ingress rule.

The inverse of ``demo/trigger.sh break``.  Every call is scoped to one
rule on one group, and the rule must exist in the golden snapshot taken
on a healthy stack (``make snapshot-sg``), so the action can only restore
what an operator already blessed.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from beacon.remediation.base import ActionResult

logger = logging.getLogger(__name__)

_DRY_RUN_OK_CODES = ("DryRunOperation", "InvalidPermission.Duplicate")


def _ec2(client: Any | None) -> Any:
    return client if client is not None else boto3.client("ec2")


def ip_permission(params: dict[str, Any]) -> dict[str, Any]:
    """Build the EC2 ``IpPermission`` structure for *params*."""
    return {
        "IpProtocol": params["ip_protocol"],
        "FromPort": params["from_port"],
        "ToPort": params["to_port"],
        "UserIdGroupPairs": [{"GroupId": params["source_group_id"]}],
    }


def _matches(rule: dict[str, Any], params: dict[str, Any]) -> bool:
    if rule.get("IpProtocol") != params["ip_protocol"]:
        return False
    if (
        rule.get("FromPort") != params["from_port"]
        or rule.get("ToPort") != params["to_port"]
    ):
        return False
    return any(
        pair.get("GroupId") == params["source_group_id"]
        for pair in rule.get("UserIdGroupPairs", [])
    )


def blast_radius(params: dict[str, Any]) -> str:
    return (
        f"1 ingress rule on 1 security group: allow {params['ip_protocol']}/"
        f"{params['from_port']}-{params['to_port']} from {params['source_group_id']} "
        f"into {params['group_id']}. Nothing else changes."
    )


def dry_run(params: dict[str, Any], *, ec2_client: Any | None = None) -> ActionResult:
    """Ask EC2 whether the caller may authorize this rule, without doing it.

    ``DryRunOperation`` means the permission check passed; so does
    ``InvalidPermission.Duplicate`` (the rule already exists, the check
    ran first).  ``UnauthorizedOperation`` means the role cannot do it.
    """
    try:
        _ec2(ec2_client).authorize_security_group_ingress(
            GroupId=params["group_id"],
            IpPermissions=[ip_permission(params)],
            DryRun=True,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        message = exc.response.get("Error", {}).get("Message", "")
        return ActionResult(ok=code in _DRY_RUN_OK_CODES, code=code, detail=message)
    return ActionResult(ok=True, code="Succeeded", detail="DryRun flag was ignored")


def execute(params: dict[str, Any], *, ec2_client: Any | None = None) -> ActionResult:
    """Authorize the rule.  An already-present rule counts as success."""
    try:
        _ec2(ec2_client).authorize_security_group_ingress(
            GroupId=params["group_id"], IpPermissions=[ip_permission(params)]
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        message = exc.response.get("Error", {}).get("Message", "")
        if code == "InvalidPermission.Duplicate":
            return ActionResult(ok=True, code=code, detail="rule was already present")
        logger.warning("authorize_security_group_ingress failed: %s %s", code, message)
        return ActionResult(ok=False, code=code, detail=message)
    return ActionResult(ok=True, code="Authorized", detail="rule added")


def revoke_dry_run(
    params: dict[str, Any], *, ec2_client: Any | None = None
) -> ActionResult:
    """Ask EC2 whether the caller may revoke this rule, without doing it."""
    try:
        _ec2(ec2_client).revoke_security_group_ingress(
            GroupId=params["group_id"],
            IpPermissions=[ip_permission(params)],
            DryRun=True,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        message = exc.response.get("Error", {}).get("Message", "")
        return ActionResult(
            ok=code in ("DryRunOperation", "InvalidPermission.NotFound"),
            code=code,
            detail=message,
        )
    return ActionResult(ok=True, code="Succeeded", detail="DryRun flag was ignored")


def revoke_execute(
    params: dict[str, Any], *, ec2_client: Any | None = None
) -> ActionResult:
    """Remove the rule again (the inverse of ``execute``); an absent rule is success."""
    try:
        _ec2(ec2_client).revoke_security_group_ingress(
            GroupId=params["group_id"], IpPermissions=[ip_permission(params)]
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        message = exc.response.get("Error", {}).get("Message", "")
        if code == "InvalidPermission.NotFound":
            return ActionResult(ok=True, code=code, detail="rule was already absent")
        logger.warning("revoke_security_group_ingress failed: %s %s", code, message)
        return ActionResult(ok=False, code=code, detail=message)
    return ActionResult(ok=True, code="Revoked", detail="rule removed")


def revoke_blast_radius(params: dict[str, Any]) -> str:
    return (
        f"1 ingress rule removed from 1 security group: {params['ip_protocol']}/"
        f"{params['from_port']}-{params['to_port']} from {params['source_group_id']} "
        f"on {params['group_id']}. The fault it fixed will return."
    )


def revoke_postcondition(
    params: dict[str, Any], *, ec2_client: Any | None = None
) -> bool:
    """True when the rule is gone."""
    return not postcondition(params, ec2_client=ec2_client)


def postcondition(params: dict[str, Any], *, ec2_client: Any | None = None) -> bool:
    """True when the rule is present on the group right now."""
    resp = _ec2(ec2_client).describe_security_groups(GroupIds=[params["group_id"]])
    for group in resp.get("SecurityGroups", []):
        if any(_matches(rule, params) for rule in group.get("IpPermissions", [])):
            return True
    return False


def snapshot(group_ids: list[str], *, ec2_client: Any | None = None) -> dict[str, Any]:
    """Capture the current ingress rules of *group_ids* (the golden snapshot)."""
    resp = _ec2(ec2_client).describe_security_groups(GroupIds=group_ids)
    result: dict[str, list[dict[str, Any]]] = {}
    for group in resp.get("SecurityGroups", []):
        rules = []
        for rule in group.get("IpPermissions", []):
            rules.append(
                {
                    "IpProtocol": rule.get("IpProtocol"),
                    "FromPort": rule.get("FromPort"),
                    "ToPort": rule.get("ToPort"),
                    "UserIdGroupPairs": [
                        {"GroupId": p.get("GroupId")}
                        for p in rule.get("UserIdGroupPairs", [])
                    ],
                    "IpRanges": [
                        {"CidrIp": r.get("CidrIp")} for r in rule.get("IpRanges", [])
                    ],
                }
            )
        result[group["GroupId"]] = rules
    return result


def in_golden_snapshot(params: dict[str, Any], snapshot_data: dict[str, Any]) -> bool:
    """True when the exact rule exists in the golden snapshot for this group."""
    rules = snapshot_data.get(params["group_id"], [])
    return any(_matches(rule, params) for rule in rules)


def load_golden_snapshot(
    *, param_name: str | None = None, ssm_client: Any | None = None
) -> dict[str, Any] | None:
    """Read the golden snapshot JSON from SSM; ``None`` if unavailable."""
    name = param_name or os.environ.get("GOLDEN_SG_PARAM", "")
    if not name:
        return None
    client = ssm_client if ssm_client is not None else boto3.client("ssm")
    try:
        value = client.get_parameter(Name=name)["Parameter"]["Value"]
        parsed = json.loads(value)
    except (ClientError, json.JSONDecodeError, KeyError):
        logger.exception("Golden snapshot %s unavailable", name)
        return None
    return parsed if isinstance(parsed, dict) else None


def precondition(
    params: dict[str, Any], *, ssm_client: Any | None = None
) -> str | None:
    """Return an error message when the rule may not be restored, else None."""
    snapshot_data = load_golden_snapshot(ssm_client=ssm_client)
    if snapshot_data is None:
        return "golden snapshot unavailable (run make snapshot-sg on a healthy stack)"
    if not in_golden_snapshot(params, snapshot_data):
        return "rule is not in the golden snapshot for this security group"
    return None
