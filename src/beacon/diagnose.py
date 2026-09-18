"""Deterministic diagnostics that run before the model sees the logs.

Today: security-group drift against the golden snapshot.  The output is a
``[diagnostics]`` section for the triage prompt plus structured data the
handler uses to override the model's suggested action with exact ids.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import boto3

from beacon.remediation import actions_sg


@dataclass(frozen=True, slots=True)
class MissingRule:
    group_id: str
    ip_protocol: str
    from_port: int | None
    to_port: int | None
    source_group_id: str | None = None
    cidr: str | None = None

    def describe(self) -> str:
        source = self.source_group_id or self.cidr or "?"
        return (
            f"{self.ip_protocol} {self.from_port}-{self.to_port} "
            f"from {source} into {self.group_id}"
        )


def _rule_present(rule: dict[str, Any], current: list[dict[str, Any]]) -> bool:
    for existing in current:
        if existing.get("IpProtocol") != rule.get("IpProtocol"):
            continue
        if existing.get("FromPort") != rule.get("FromPort") or existing.get(
            "ToPort"
        ) != rule.get("ToPort"):
            continue
        wanted_groups = {p.get("GroupId") for p in rule.get("UserIdGroupPairs", [])}
        wanted_cidrs = {r.get("CidrIp") for r in rule.get("IpRanges", [])}
        have_groups = {p.get("GroupId") for p in existing.get("UserIdGroupPairs", [])}
        have_cidrs = {r.get("CidrIp") for r in existing.get("IpRanges", [])}
        if wanted_groups <= have_groups and wanted_cidrs <= have_cidrs:
            return True
    return False


def sg_drift(
    golden: dict[str, Any], *, ec2_client: Any | None = None
) -> list[MissingRule]:
    """Golden rules that are not present on the live security groups."""
    client = ec2_client if ec2_client is not None else boto3.client("ec2")
    group_ids = list(golden)
    if not group_ids:
        return []
    current = actions_sg.snapshot(group_ids, ec2_client=client)
    missing: list[MissingRule] = []
    for group_id, rules in golden.items():
        live = current.get(group_id, [])
        for rule in rules:
            if _rule_present(rule, live):
                continue
            pairs = rule.get("UserIdGroupPairs", [])
            ranges = rule.get("IpRanges", [])
            missing.append(
                MissingRule(
                    group_id=group_id,
                    ip_protocol=str(rule.get("IpProtocol")),
                    from_port=rule.get("FromPort"),
                    to_port=rule.get("ToPort"),
                    source_group_id=pairs[0].get("GroupId") if pairs else None,
                    cidr=ranges[0].get("CidrIp") if ranges else None,
                )
            )
    return missing


def format_diagnostics(missing: list[MissingRule], golden: dict[str, Any]) -> str:
    total = sum(len(rules) for rules in golden.values())
    plural = "rule" if total == 1 else "rules"
    if not missing:
        return (
            "Security group check: No security group drift. "
            f"All {total} golden {plural} "
            f"present on {len(golden)} group(s)."
        )
    lines = [
        f"Security group drift vs golden snapshot: {len(missing)} of {total} "
        f"golden {plural} MISSING."
    ]
    for rule in missing:
        lines.append(f"- MISSING ingress rule: {rule.describe()}")
    return "\n".join(lines)


def suggested_fix(missing: list[MissingRule]) -> tuple[str, dict[str, Any]] | None:
    """The first restorable missing rule as an allowlisted action + params."""
    for rule in missing:
        if (
            rule.source_group_id
            and rule.from_port is not None
            and rule.to_port is not None
        ):
            return (
                "sg.restore_ingress",
                {
                    "group_id": rule.group_id,
                    "ip_protocol": rule.ip_protocol,
                    "from_port": int(rule.from_port),
                    "to_port": int(rule.to_port),
                    "source_group_id": rule.source_group_id,
                },
            )
    return None


def run(golden: dict[str, Any], *, ec2_client: Any | None = None) -> dict[str, Any]:
    """Everything the handler needs: text for the prompt + structured result."""
    missing = sg_drift(golden, ec2_client=ec2_client)
    fix = suggested_fix(missing)
    return {
        "missing_rules": [asdict(m) for m in missing],
        "suggested_action": fix[0] if fix else None,
        "action_params": fix[1] if fix else None,
        "text": format_diagnostics(missing, golden),
    }
