"""The allowlist.  Nothing outside ``REGISTRY`` can be dry-run or executed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from beacon.remediation import actions_ecs, actions_sg
from beacon.remediation.base import ActionResult, ParamError

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "REGISTRY",
    "ActionResult",
    "ActionSpec",
    "ParamError",
    "confirmation_phrase",
    "get_action",
    "iam_write_actions",
    "undo_phrase",
    "validate_params",
]


@dataclass(frozen=True, slots=True)
class ActionSpec:
    id: str
    description: str
    params_schema: dict[str, type]
    iam_actions: tuple[str, ...]
    blast_radius: Callable[[dict[str, Any]], str]
    precondition: Callable[..., str | None]
    dry_run: Callable[..., ActionResult]
    execute: Callable[..., ActionResult]
    postcondition: Callable[..., bool]
    # The action that undoes this one with the same params ("undo fix <n>"), if any.
    inverse: str | None = None


REGISTRY: dict[str, ActionSpec] = {
    "sg.restore_ingress": ActionSpec(
        id="sg.restore_ingress",
        description=(
            "Restore one security-group ingress rule that exists in the golden snapshot"
        ),
        params_schema={
            "group_id": str,
            "ip_protocol": str,
            "from_port": int,
            "to_port": int,
            "source_group_id": str,
        },
        iam_actions=("ec2:AuthorizeSecurityGroupIngress",),
        blast_radius=actions_sg.blast_radius,
        precondition=actions_sg.precondition,
        dry_run=actions_sg.dry_run,
        execute=actions_sg.execute,
        postcondition=actions_sg.postcondition,
        inverse="sg.revoke_ingress",
    ),
    "sg.revoke_ingress": ActionSpec(
        id="sg.revoke_ingress",
        description=(
            "Undo: remove the ingress rule Beacon restored earlier in this incident"
        ),
        params_schema={
            "group_id": str,
            "ip_protocol": str,
            "from_port": int,
            "to_port": int,
            "source_group_id": str,
        },
        iam_actions=("ec2:RevokeSecurityGroupIngress",),
        blast_radius=actions_sg.revoke_blast_radius,
        precondition=actions_sg.precondition,  # only rules the snapshot knows about
        dry_run=actions_sg.revoke_dry_run,
        execute=actions_sg.revoke_execute,
        postcondition=actions_sg.revoke_postcondition,
    ),
    "ecs.force_redeploy": ActionSpec(
        id="ecs.force_redeploy",
        description="Force a new deployment of one ECS service (same task definition)",
        params_schema={"cluster": str, "service": str},
        iam_actions=("ecs:UpdateService",),
        blast_radius=actions_ecs.blast_radius,
        precondition=actions_ecs.precondition,
        dry_run=actions_ecs.dry_run,
        execute=actions_ecs.execute,
        postcondition=actions_ecs.postcondition,
    ),
}


def get_action(action_id: str) -> ActionSpec | None:
    return REGISTRY.get(action_id)


def validate_params(action_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Return *params* if they exactly match the action's schema, else raise."""
    spec = REGISTRY.get(action_id)
    if spec is None:
        raise ParamError(f"action '{action_id}' is not allowlisted")
    for key in spec.params_schema:
        if key not in params:
            raise ParamError(f"missing param: {key}")
    for key, value in params.items():
        expected = spec.params_schema.get(key)
        if expected is None:
            raise ParamError(f"unexpected param: {key}")
        if isinstance(value, bool) or not isinstance(value, expected):
            raise ParamError(f"param {key} must be {expected.__name__}")
    return dict(params)


def confirmation_phrase(fix_id: int) -> str:
    """The exact phrase the engineer must say to approve proposal *fix_id*."""
    return f"approve fix {fix_id}"


def undo_phrase(fix_id: int) -> str:
    """The exact phrase that undoes an applied fix."""
    return f"undo fix {fix_id}"


def iam_write_actions() -> set[str]:
    """Union of write actions any allowlisted action needs (the remediator role)."""
    return {action for spec in REGISTRY.values() for action in spec.iam_actions}
