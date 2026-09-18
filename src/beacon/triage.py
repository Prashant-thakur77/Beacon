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
    return str(response.choices[0].message.content)


def get_system_prompt() -> str:
    """Public accessor for the cached triage system prompt."""
    return _load_system_prompt()


def build_trigger_context(trigger: TriggerInfo) -> str:
    """Public accessor for building trigger context from a TriggerInfo."""
    return trigger.format_context()
