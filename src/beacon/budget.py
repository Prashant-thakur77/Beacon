from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import litellm

if TYPE_CHECKING:
    from beacon.config import BeaconConfig

logger = logging.getLogger(__name__)

_FORMATTING_OVERHEAD_TOKENS = 500
_MIN_PERCENTILE = 0.01
_FALLBACK_CHARS_PER_TOKEN = 4
_FALLBACK_CONTEXT_WINDOW = 1_000_000


@dataclass(frozen=True, slots=True)
class SourcePlan:
    """Per-log-group plan produced by the token budget planner.

    If ``needs_reduction`` is True, the log text must be passed through
    Cordon at the given ``anomaly_percentile`` before analysis.
    """

    log_group: str
    log_text: str
    token_count: int
    needs_reduction: bool
    anomaly_percentile: float | None = None


def estimate_tokens(text: str, model: str) -> int:
    """Estimate the token count for *text* using litellm's tokenizer.

    Falls back to a simple character-based heuristic (4 chars per token)
    if the tokenizer is unavailable for the given model.
    """
    try:
        return int(litellm.token_counter(model=model, text=text))
    except Exception:
        return len(text) // _FALLBACK_CHARS_PER_TOKEN


def _get_model_context_window(model: str) -> int:
    """Look up the maximum input token limit for *model* via litellm.

    Returns a 1M fallback if the model info is unavailable.
    """
    try:
        info = litellm.get_model_info(model=model)
        max_tokens = info.get("max_input_tokens")
        return int(max_tokens) if max_tokens is not None else _FALLBACK_CONTEXT_WINDOW
    except Exception:
        return _FALLBACK_CONTEXT_WINDOW


def compute_available_tokens(
    config: BeaconConfig,
    system_prompt: str,
    trigger_context: str,
) -> int:
    """Calculate how many tokens are available for log content.

    Subtracts the output reservation, system prompt, trigger context,
    and a formatting overhead from either the explicit token budget or
    the model's context window.
    """
    model = config.litellm_model
    context_window = (
        config.token_budget
        if config.token_budget > 0
        else _get_model_context_window(model)
    )
    reserved = (
        config.max_output_tokens
        + estimate_tokens(system_prompt, model)
        + estimate_tokens(trigger_context, model)
        + _FORMATTING_OVERHEAD_TOKENS
    )
    return max(context_window - reserved, 0)


def plan_token_budget(
    log_sources: dict[str, str],
    available_tokens: int,
    config: BeaconConfig,
) -> list[SourcePlan]:
    """Allocate the token budget across log sources using greedy fair-share.

    Sources that fit within their fair share keep full logs.  Sources that
    exceed it are assigned a Cordon anomaly percentile proportional to
    their share of the remaining budget (clamped to a 1% minimum).
    """
    if not log_sources:
        return []

    model = config.litellm_model
    counted: list[tuple[str, str, int]] = [
        (group, text, estimate_tokens(text, model))
        for group, text in log_sources.items()
    ]

    total = sum(tc for _, _, tc in counted)
    if total <= available_tokens:
        return [
            SourcePlan(
                log_group=group,
                log_text=text,
                token_count=tc,
                needs_reduction=False,
            )
            for group, text, tc in counted
        ]

    # Greedy fair-share: small sources that fit keep their full text
    sorted_sources = sorted(counted, key=lambda x: x[2])
    remaining_budget = available_tokens
    remaining_count = len(sorted_sources)
    plans: list[SourcePlan] = []

    for group, text, tc in sorted_sources:
        fair_share = remaining_budget // max(remaining_count, 1)
        if tc <= fair_share:
            plans.append(
                SourcePlan(
                    log_group=group,
                    log_text=text,
                    token_count=tc,
                    needs_reduction=False,
                )
            )
            remaining_budget -= tc
        else:
            percentile = max(fair_share / tc, _MIN_PERCENTILE)
            percentile = min(percentile, 1.0)
            plans.append(
                SourcePlan(
                    log_group=group,
                    log_text=text,
                    token_count=tc,
                    needs_reduction=True,
                    anomaly_percentile=percentile,
                )
            )
            remaining_budget -= fair_share
        remaining_count -= 1

    return plans
