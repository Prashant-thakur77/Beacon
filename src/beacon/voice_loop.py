"""Fallback voice engine: a plain litellm tool-calling loop.

Same tools, same ``TOOL_SCHEMAS``, same message shape as the Strands agent
(Bedrock Converse blocks), so ``voice_turn`` cannot tell them apart.
Selected with ``VOICE_ENGINE=litellm`` if Strands + Nova tool use misbehaves.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from beacon import voice_tools

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 4


def _to_openai(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converse-shaped history -> OpenAI-shaped messages for litellm."""
    out: list[dict[str, Any]] = []
    for message in history:
        for block in message.get("content", []):
            if "text" in block:
                out.append({"role": message["role"], "content": block["text"]})
            elif "toolUse" in block:
                use = block["toolUse"]
                out.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": use["toolUseId"],
                                "type": "function",
                                "function": {
                                    "name": use["name"],
                                    "arguments": json.dumps(use["input"]),
                                },
                            }
                        ],
                    }
                )
            elif "toolResult" in block:
                res = block["toolResult"]
                payload = res.get("content", [{}])[0].get("json", {})
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": res["toolUseId"],
                        "content": json.dumps(payload, default=str),
                    }
                )
    return out


class LiteLLMAgent:
    """Duck-types the parts of ``strands.Agent`` that ``voice_turn`` uses."""

    def __init__(self, *, system_prompt: str, history: list[dict[str, Any]]) -> None:
        self.system_prompt = system_prompt
        self.messages: list[dict[str, Any]] = list(history)
        model = os.environ.get("NOVA_MODEL_ID", "us.amazon.nova-2-lite-v1:0")
        self.model = model if "/" in model else f"bedrock/{model}"
        self.tools = [
            {"type": "function", "function": t} for t in voice_tools.TOOL_SCHEMAS
        ]

    def __call__(self, text: str) -> str:
        import litellm

        self.messages.append({"role": "user", "content": [{"text": text}]})
        for _ in range(_MAX_ITERATIONS):
            response: Any = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    *_to_openai(self.messages),
                ],
                tools=self.tools,
                temperature=0.3,
                max_tokens=500,
            )
            message = response.choices[0].message
            calls = getattr(message, "tool_calls", None) or []
            if not calls:
                reply = str(message.content or "").strip()
                self.messages.append(
                    {"role": "assistant", "content": [{"text": reply}]}
                )
                return reply
            for call in calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = voice_tools.dispatch(name, args)
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "toolUse": {
                                    "toolUseId": call.id,
                                    "name": name,
                                    "input": args,
                                }
                            }
                        ],
                    }
                )
                self.messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "toolResult": {
                                    "toolUseId": call.id,
                                    "content": [{"json": result}],
                                    "status": "success",
                                }
                            }
                        ],
                    }
                )
        reply = (
            "I ran out of steps while investigating. "
            "Ask me again and I will pick up from here."
        )
        self.messages.append({"role": "assistant", "content": [{"text": reply}]})
        return reply
