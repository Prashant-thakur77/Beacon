from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING, Any

import litellm

if TYPE_CHECKING:
    from beacon.config import BeaconConfig
    from beacon.events import TriggerInfo


@cache
def _load_system_prompt() -> str:
    """Load and cache the triage system prompt from ``prompts/triage.txt``."""
    resource = files("beacon").joinpath("prompts/triage.txt")
    return resource.read_text(encoding="utf-8")


def triage(
    log_content: str,
    trigger: TriggerInfo,
    config: BeaconConfig,
) -> str:
    """Send log content to Nova 2 Lite and return a structured RCA.

    Combines the triage system prompt with trigger context and log data,
    then calls litellm at temperature 0.3.  The response follows the
    STATUS / SUMMARY / EVIDENCE / NEXT STEPS format defined in
    ``prompts/triage.txt``.
    """
    system_prompt = _load_system_prompt()
    trigger_context = trigger.format_context()

    user_prompt = (
        f"--- TRIGGER CONTEXT ---\n{trigger_context}\n\n--- LOG DATA ---\n{log_content}"
    )

    response: Any = litellm.completion(
        model=config.litellm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=config.max_output_tokens,
        temperature=0.3,
    )
    _record_usage(response)
    return str(response.choices[0].message.content)


last_usage: dict[str, int] = {}


def _record_usage(response: Any) -> None:
    """Keep the last call's token counts so the handler can store them."""
    usage = getattr(response, "usage", None)
    try:
        last_usage.clear()
        last_usage.update(
            {
                "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            }
        )
    except (TypeError, ValueError):
        last_usage.clear()


def get_system_prompt() -> str:
    """Public accessor for the cached triage system prompt."""
    return _load_system_prompt()


def build_trigger_context(trigger: TriggerInfo) -> str:
    """Public accessor for building trigger context from a TriggerInfo."""
    return trigger.format_context()
