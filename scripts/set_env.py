"""Set one environment variable on a Lambda function, preserving the rest.

``update-function-configuration --environment`` replaces the whole map, so
this reads the current variables first.  Waits until the update is live.

Usage: python scripts/set_env.py <function-name> <KEY> <value> [--region us-east-1]
"""

from __future__ import annotations

import argparse
import sys
import time

import boto3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("function")
    parser.add_argument("key")
    parser.add_argument("value")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args(argv)

    client = boto3.client("lambda", region_name=args.region)
    current = client.get_function_configuration(FunctionName=args.function)
    variables = dict(current.get("Environment", {}).get("Variables", {}))
    variables[args.key] = args.value
    client.update_function_configuration(
        FunctionName=args.function, Environment={"Variables": variables}
    )
    for _ in range(30):
        state = client.get_function_configuration(FunctionName=args.function)
        if state.get("LastUpdateStatus") == "Successful":
            print(f"{args.function}: {args.key}={args.value}")
            return 0
        time.sleep(2)
    print(f"{args.function}: update still pending", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
