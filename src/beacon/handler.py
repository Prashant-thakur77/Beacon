from __future__ import annotations

import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime

os.environ.setdefault("TQDM_DISABLE", "1")  # noqa: E402

from typing import Any  # noqa: E402

from beacon import rca
from beacon.analyzer import analyze_logs
from beacon.budget import SourcePlan, compute_available_tokens, plan_token_budget
from beacon.config import BeaconConfig
from beacon.events import TriggerInfo, TriggerType, parse_event
from beacon.logs import fetch_logs, resolve_log_groups
from beacon.notifier import notify
from beacon.triage import build_trigger_context, get_system_prompt, triage

logger = logging.getLogger(__name__)


def _fetch_all_logs(config: BeaconConfig, trigger: TriggerInfo) -> dict[str, str]:
    """Fetch log text for every configured log group.

    For subscription triggers, raw logs delivered in the event are used
    directly instead of re-fetching from CloudWatch.
    """
    log_groups = resolve_log_groups(config.log_group_patterns)
    if not log_groups:
        logger.warning("No log groups matched the configured patterns")
        return {}

    log_sources: dict[str, str] = {}
    for log_group in log_groups:
        if trigger.raw_logs and trigger.log_group == log_group:
            log_sources[log_group] = trigger.raw_logs
            continue
        lookback = trigger.lookback_minutes or config.lookback_minutes
        text = fetch_logs(log_group, lookback)
        if text.strip():
            log_sources[log_group] = text
    return log_sources


def _build_section_label(plan: SourcePlan) -> str:
    """Build a human-readable header like ``[/aws/lambda/foo] (reduced to top 45%)``."""
    if plan.needs_reduction and plan.anomaly_percentile is not None:
        return f"[{plan.log_group}] (reduced to top {plan.anomaly_percentile:.0%})"
    return f"[{plan.log_group}] (full logs)"


def _process_sources(
    plans: list[SourcePlan],
    config: BeaconConfig,
    timeline: list[dict[str, Any]] | None = None,
) -> str:
    """Combine all source plans into a single labeled text block.

    Sources that need reduction are passed through Cordon; others are
    included as raw logs.  Each reduction is recorded on *timeline*.
    """
    sections: list[str] = []
    for plan in plans:
        label = _build_section_label(plan)
        if plan.needs_reduction and plan.anomaly_percentile is not None:
            reduced = analyze_logs(plan.log_text, plan.anomaly_percentile, config)
            sections.append(f"{label}\n{reduced}")
            if timeline is not None:
                timeline.append(
                    _event(
                        "reduced",
                        log_group=plan.log_group,
                        percentile=round(plan.anomaly_percentile, 3),
                        model=config.embedding_model_id,
                    )
                )
        else:
            sections.append(f"{label}\n{plan.log_text}")
    return "\n\n".join(sections)


def _event(name: str, **detail: Any) -> dict[str, Any]:
    """Build one incident timeline entry with a UTC timestamp."""
    entry: dict[str, Any] = {"t": datetime.now(tz=UTC).isoformat(), "event": name}
    if detail:
        entry["detail"] = detail
    return entry


def _configure_logging() -> None:
    """Set up logging and suppress noisy third-party loggers."""
    logging.basicConfig(level=logging.INFO)
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    for name in (
        "sentence_transformers",
        "transformers",
        "LiteLLM",
        "litellm",
        "botocore",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point: analyse logs, notify via SNS, store the incident."""
    _configure_logging()
    config = BeaconConfig.from_env()
    trigger = parse_event(event, config)
    timeline: list[dict[str, Any]] = [
        _event(
            "alarm_received"
            if trigger.trigger_type == TriggerType.ALARM
            else "triggered",
            trigger_type=trigger.trigger_type.value,
            alarm_name=trigger.alarm_name,
        )
    ]

    duplicate = _find_duplicate(trigger, config)
    if duplicate:
        logger.info("Open incident %s already exists for this alarm", duplicate)
        return {
            "statusCode": 200,
            "body": f"Duplicate incident {duplicate}",
            "incident_id": duplicate,
        }

    log_sources = _fetch_all_logs(config, trigger)
    if not log_sources:
        logger.info("No logs found for any configured log group")
        return {"statusCode": 200, "body": "No logs found"}
    timeline.append(
        _event(
            "logs_fetched",
            groups=len(log_sources),
            lines=sum(text.count("\n") + 1 for text in log_sources.values()),
        )
    )

    system_prompt = get_system_prompt()
    trigger_context = build_trigger_context(trigger)
    available = compute_available_tokens(config, system_prompt, trigger_context)
    plans = plan_token_budget(log_sources, available, config)

    combined_input = _process_sources(plans, config, timeline)

    analysis = triage(combined_input, trigger, config)
    parsed = rca.parse(analysis)
    timeline.append(
        _event(
            "rca_ready",
            status=parsed.status,
            model=config.nova_model_id,
            suggested_action=parsed.suggested_action,
        )
    )

    if _is_healthy(analysis) and trigger.trigger_type == TriggerType.SCHEDULE:
        logger.info("Scheduled scan found no issues, skipping notification")
        return {"statusCode": 200, "body": "Healthy, no notification sent"}

    notify(analysis, trigger, config)
    timeline.append(_event("sns_sent"))

    result: dict[str, Any] = {"statusCode": 200, "body": "Analysis complete"}
    if config.incidents_enabled or config.connect_enabled:
        incident_id = _store_and_investigate(
            analysis, parsed, trigger, config, timeline
        )
        if incident_id:
            result["incident_id"] = incident_id

    logger.info("Analysis complete and published to SNS")
    return result


def _find_duplicate(trigger: TriggerInfo, config: BeaconConfig) -> str | None:
    """Return the id of an open incident for the same alarm, if any.

    Alarm re-evaluations and forced state changes must not create a second
    incident (and a second page) for one outage.  Failures are logged and
    treated as "no duplicate" so triage still runs.
    """
    if not (config.incidents_enabled and trigger.alarm_name):
        return None
    from beacon import store

    try:
        existing = store.find_open_incident(
            trigger.alarm_name, table_name=config.incidents_table_name
        )
    except Exception:
        logger.exception("Duplicate check failed; continuing with triage")
        return None
    return str(existing["incident_id"]) if existing else None


def _store_and_investigate(
    analysis: str,
    parsed: rca.RcaJson,
    trigger: TriggerInfo,
    config: BeaconConfig,
    timeline: list[dict[str, Any]],
) -> str | None:
    """Store the incident, pre-fetch investigation data, optionally call.

    The outbound call (Connect, only when enabled) and the pre-fetch run in
    parallel so cached data is ready before the engineer picks up.  Failures
    are logged but never block the SNS notification that was already sent.
    """
    from beacon import caller, prefetch, store

    try:
        incident_id = store.put_incident(
            analysis,
            trigger,
            config,
            rca_json=parsed.to_dict(),
            timeline=timeline,
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures: list[Future[Any]] = [
                pool.submit(prefetch.run, incident_id, analysis, trigger, config)
            ]
            if config.connect_enabled:
                futures.append(
                    pool.submit(caller.start_voice_call, incident_id, config)
                )
            for future in futures:
                future.result()
        return incident_id
    except Exception:
        logger.exception("Incident pipeline failed, SNS notification was still sent")
        return None


def _is_healthy(analysis: str) -> bool:
    """Return True if the parsed RCA ``STATUS`` is ``Healthy``."""
    return rca.is_healthy(analysis)
