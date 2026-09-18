"""One Strands Agents turn with one tool on Amazon Nova 2 Lite.

Run by the human with real AWS credentials (``make smoke-strands``). It is the
Friday-night go/no-go for using Strands as the voice engine: if Nova 2 Lite
does not call the tool, the plan switches ``VOICE_ENGINE`` to the litellm
fallback loop that uses the same tool schemas.

Prints the tool calls made, the final answer and the wall-clock latency.
Exit code 0 = PASS (tool was called), 1 = FAIL.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from strands import Agent, tool
from strands.models import BedrockModel

SYSTEM_PROMPT = (
    "You are Beacon, an on-call assistant. When asked about a CloudWatch alarm, "
    "you MUST call get_alarm_state before answering. Answer in one short sentence."
)


@tool
def get_alarm_state(alarm_name: str) -> dict[str, Any]:
    """Return the current state of a CloudWatch alarm (stubbed for this smoke test).

    Args:
        alarm_name: The exact CloudWatch alarm name.
    """
    return {"alarm_name": alarm_name, "state": "ALARM", "reason": "ErrorCount >= 3"}


def _tool_calls(messages: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for message in messages:
        for block in message.get("content", []):
            if isinstance(block, dict) and "toolUse" in block:
                names.append(str(block["toolUse"].get("name")))
    return names


def main() -> int:
    model_id = os.environ.get("NOVA_MODEL_ID", "us.amazon.nova-2-lite-v1:0")
    region = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    streaming = os.environ.get("STRANDS_STREAMING", "false").lower() == "true"

    model = BedrockModel(
        model_id=model_id,
        region_name=region,
        temperature=0.3,
        max_tokens=300,
        streaming=streaming,
    )
    agent = Agent(
        model=model,
        tools=[get_alarm_state],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
    )

    started = time.time()
    result = agent("What is the current state of the alarm beacon-demo-infra-errors?")
    latency = time.time() - started

    calls = _tool_calls(agent.messages)
    print(f"model      : {model_id} (region {region}, streaming={streaming})")
    print(f"tool calls : {calls}")
    print(f"answer     : {str(result).strip()}")
    print(f"latency    : {latency:.1f}s")

    if "get_alarm_state" not in calls:
        print("FAIL: the model did not call the tool")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
