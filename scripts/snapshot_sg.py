"""Write the golden security-group snapshot to SSM (``make snapshot-sg``).

Run on a HEALTHY stack.  The remediator will only ever restore ingress rules
that appear in this snapshot, so it is the data half of the allowlist.

Usage:
    python scripts/snapshot_sg.py --param /beacon/<stack>/golden-sg \
        --region us-east-1 sg-1 sg-2
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3

from beacon.remediation import actions_sg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group_ids", nargs="+", help="security group ids to snapshot")
    parser.add_argument("--param", required=True, help="SSM parameter name to write")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args(argv)

    ec2 = boto3.client("ec2", region_name=args.region)
    ssm = boto3.client("ssm", region_name=args.region)

    snapshot = actions_sg.snapshot(args.group_ids, ec2_client=ec2)
    rule_count = sum(len(rules) for rules in snapshot.values())
    if rule_count == 0:
        print(
            "ERROR: no ingress rules found; refusing to write an empty golden "
            "snapshot. Run this on a healthy stack (make fix-demo first).",
            file=sys.stderr,
        )
        return 1

    value = json.dumps(snapshot, indent=1)
    ssm.put_parameter(Name=args.param, Type="String", Value=value, Overwrite=True)
    print(
        f"Wrote golden snapshot to {args.param}: {rule_count} rule(s) "
        f"across {len(snapshot)} group(s)"
    )
    print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
