"""Create/update the CloudWatch dashboard ``beacon-<stack>`` from Beacon's EMF metrics.

Usage: python scripts/make_dashboard.py --stack beacon --region us-east-1
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3

NS = "Beacon"


def _widget(
    title: str,
    metrics: list[list[str]],
    x: int,
    y: int,
    w: int = 8,
    h: int = 6,
    stat: str = "Sum",
    view: str = "timeSeries",
) -> dict:
    return {
        "type": "metric",
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "properties": {
            "title": title,
            "region": "${region}",
            "view": view,
            "stacked": False,
            "stat": stat,
            "period": 60,
            "metrics": metrics,
        },
    }


def build(stack: str, region: str) -> dict:
    voice = ["service", "beacon-voice-turn"]
    rem = ["service", "beacon-remediate"]
    body = {
        "widgets": [
            {
                "type": "text",
                "x": 0,
                "y": 0,
                "width": 24,
                "height": 2,
                "properties": {
                    "markdown": (
                        f"# Beacon Night Shift — `{stack}`\n"
                        "EMF metrics from the voice-turn and remediate Lambdas "
                        "(Powertools for AWS Lambda). "
                        "Every number here is a real invocation."
                    )
                },
            },
            _widget(
                "Humans woken vs incidents resolved",
                [
                    [NS, "Resolved", *rem],
                    [NS, "HumansWoken", *rem],
                    [NS, "Escalated", *rem],
                ],
                0,
                2,
            ),
            _widget(
                "Seconds from execute to verified",
                [[NS, "RemediationSeconds", *rem]],
                8,
                2,
                stat="Average",
            ),
            _widget(
                "Verify attempts per loop",
                [[NS, "VerifyAttempts", *rem]],
                16,
                2,
                stat="Maximum",
            ),
            _widget(
                "Voice turn latency (ms)",
                [
                    [NS, "TurnLatencyMs", *voice],
                    [NS, "AgentLatencyMs", *voice],
                    [NS, "TtsLatencyMs", *voice],
                ],
                0,
                8,
                stat="Average",
            ),
            _widget(
                "Tool calls · approvals · contracts",
                [
                    [NS, "ToolCalls", *voice],
                    [NS, "Approvals", *voice],
                    [NS, "ContractsGranted", *voice],
                ],
                8,
                8,
            ),
            _widget(
                "Turns by channel",
                [
                    [NS, "TurnsTranscribe", *voice],
                    [NS, "TurnsWebspeech", *voice],
                    [NS, "TurnsTyped", *voice],
                ],
                16,
                8,
            ),
        ]
    }
    return json.loads(json.dumps(body).replace("${region}", region))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack", default="beacon")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args(argv)
    name = f"beacon-{args.stack}"
    cw = boto3.client("cloudwatch", region_name=args.region)
    cw.put_dashboard(
        DashboardName=name, DashboardBody=json.dumps(build(args.stack, args.region))
    )
    print(
        f"Dashboard {name}: https://{args.region}.console.aws.amazon.com/cloudwatch/home?region={args.region}#dashboards:name={name}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
