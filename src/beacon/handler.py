from __future__ import annotations

import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime

os.environ.setdefault("TQDM_DISABLE", "1")  # noqa: E402

from typing import Any  # noqa: E402

from beacon import changes, rca
from beacon.analyzer import analyze_logs
from beacon.budget import SourcePlan, compute_available_tokens, plan_token_budget
from beacon.config import BeaconConfig
from beacon.events import TriggerInfo, TriggerType, parse_event
from beacon.logs import fetch_logs, resolve_log_groups
from beacon.notifier import notify
from beacon.triage import build_trigger_context, get_system_prompt, last_usage, triage

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
            logger.info(
                "Cordon reduced %s to top %.0f%% (model %s)",
                plan.log_group,
                plan.anomaly_percentile * 100,
                config.embedding_model_id,
            )
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

    # Deterministic sources come first: they outrank anything inferred from logs.
    diagnostics = _run_diagnostics(config, timeline)
    change_rows = _recent_changes(trigger, config, timeline)
    prefix_sections = []
    if diagnostics is not None:
        prefix_sections.append(f"[diagnostics]\n{diagnostics['text']}")
    if change_rows is not None:
        alarm_at = _alarm_time(trigger)
        prefix_sections.append(
            f"[changes]\n{changes.format_changes(change_rows, alarm_at=alarm_at)}"
        )
    if prefix_sections:
        combined_input = "\n\n".join([*prefix_sections, combined_input])

    analysis = triage(combined_input, trigger, config)
    parsed = rca.parse(analysis)
    _apply_deterministic_action(parsed, diagnostics)
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

    result: dict[str, Any] = {"statusCode": 200, "body": "Analysis complete"}
    contract = _matching_contract(parsed, trigger, config, timeline)

    if contract is not None:
        # Handled while they sleep: no page, the loop runs, the morning email says so.
        outcome = _remediate_under_contract(
            analysis,
            parsed,
            trigger,
            config,
            timeline,
            diagnostics,
            change_rows,
            contract,
        )
        result.update(outcome)
        logger.info("Incident handled under Sleep Contract %s", contract["contract_id"])
        return result

    notify(analysis, trigger, config, link=_dashboard_link(config))
    timeline.append(_event("sns_sent"))

    if config.incidents_enabled or config.connect_enabled:
        incident_id = _store_and_investigate(
            analysis, parsed, trigger, config, timeline, diagnostics, change_rows
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
    diagnostics: dict[str, Any] | None = None,
    change_rows: list[dict[str, Any]] | None = None,
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
            diagnostics=diagnostics,
            changes=change_rows,
        )
        _store_usage(incident_id, config)
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


def _store_usage(incident_id: str, config: BeaconConfig) -> None:
    """Attach the triage model's token counts (for the ₹-per-incident tally)."""
    if not last_usage:
        return
    from beacon import store

    try:
        store.add_usage(
            incident_id, dict(last_usage), table_name=config.incidents_table_name
        )
    except Exception:
        logger.exception("usage write failed")


def _alarm_time(trigger: TriggerInfo) -> datetime:
    """When the alarm changed state (falls back to now)."""
    if trigger.alarm_time:
        try:
            return datetime.fromisoformat(trigger.alarm_time.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(tz=UTC)


def _dashboard_link(config: BeaconConfig) -> str:
    return config.dashboard_url


def _run_diagnostics(
    config: BeaconConfig, timeline: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Security-group drift vs the golden snapshot; None when no snapshot exists."""
    if not config.incidents_enabled:
        return None
    from beacon import diagnose
    from beacon.remediation import actions_sg

    golden = actions_sg.load_golden_snapshot() or {}
    if not golden and not os.environ.get("REMEDIABLE_ECS_SERVICES"):
        timeline.append(_event("diagnostics_skipped", reason="nothing configured"))
        return None
    try:
        result = diagnose.run(golden)
    except Exception:
        logger.exception("diagnostics failed")
        timeline.append(_event("diagnostics_failed"))
        return None
    timeline.append(
        _event(
            "diagnostics_ran",
            missing_rules=len(result["missing_rules"]),
            suggested_action=result["suggested_action"],
        )
    )
    return result


def _recent_changes(
    trigger: TriggerInfo, config: BeaconConfig, timeline: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    """Write API calls before the alarm from the ledger (LookupEvents fallback)."""
    if not config.incidents_enabled:
        return None
    try:
        rows = changes.recent(60, before=_alarm_time(trigger), lookup_fallback=True)
    except Exception:
        logger.exception("change lookup failed")
        timeline.append(_event("changes_failed"))
        return None
    timeline.append(
        _event(
            "changes_checked",
            count=len(rows),
            top=rows[0].get("event_name") if rows else None,
            source=rows[0].get("source") if rows else None,
        )
    )
    return rows


def _apply_deterministic_action(
    parsed: rca.RcaJson, diagnostics: dict[str, Any] | None
) -> None:
    """Exact ids from diagnostics beat whatever the model guessed.

    When diagnostics found nothing deterministic, the model's own proposal
    survives only if it names an allowlisted action on a configured resource
    (schema-valid params, ECS service in REMEDIABLE_ECS_SERVICES, SG rule in
    the golden snapshot); otherwise it is dropped, never executed.
    """
    from beacon.remediation import actions_ecs, actions_sg, registry
    from beacon.remediation.base import ParamError

    if diagnostics and diagnostics.get("suggested_action"):
        parsed.beacon_json["suggested_action"] = diagnostics["suggested_action"]
        parsed.beacon_json["action_params"] = diagnostics["action_params"]
        parsed.beacon_json["action_source"] = "diagnostics"
        parsed.beacon_json.setdefault(
            "fingerprint", diagnostics["suggested_action"].replace(".", "-")
        )
        return
    action = parsed.beacon_json.get("suggested_action")
    params = parsed.beacon_json.get("action_params")
    if not action:
        return
    ok = False
    try:
        valid = registry.validate_params(
            str(action), params if isinstance(params, dict) else {}
        )
        if action == "ecs.force_redeploy":
            ok = actions_ecs.is_configured(valid)
        elif action == "sg.restore_ingress":
            golden = actions_sg.load_golden_snapshot() or {}
            ok = actions_sg.in_golden_snapshot(valid, golden)
    except ParamError:
        ok = False
    if ok:
        parsed.beacon_json["action_source"] = "model"
    else:
        logger.warning("model proposed %s on unconfigured resources; dropped", action)
        parsed.beacon_json["suggested_action"] = None
        parsed.beacon_json["action_params"] = None
        parsed.beacon_json["action_source"] = "rejected"


def _matching_contract(
    parsed: rca.RcaJson,
    trigger: TriggerInfo,
    config: BeaconConfig,
    timeline: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """A live Sleep Contract covering this alarm + action + exact params, if allowed."""
    if not (config.incidents_enabled and trigger.alarm_name):
        return None
    action = parsed.suggested_action
    params = parsed.beacon_json.get("action_params")
    if not action or not isinstance(params, dict):
        return None
    table = os.environ.get("CONTRACTS_TABLE_NAME", "")
    if not table:
        return None
    from beacon import contracts
    from beacon.remediation import actions_sg, registry

    try:
        registry.validate_params(action, params)
        contract = contracts.match(trigger.alarm_name, action, params, table_name=table)
    except Exception:
        logger.exception("contract match failed")
        return None
    if contract is None:
        return None
    if action == "sg.restore_ingress":
        golden = actions_sg.load_golden_snapshot() or {}
        if not actions_sg.in_golden_snapshot(params, golden):
            timeline.append(
                _event("contract_ignored", reason="params not in golden snapshot")
            )
            return None
    if not config.apply_enabled:
        timeline.append(
            _event(
                "contract_matched_apply_disabled", contract_id=contract["contract_id"]
            )
        )
        return None
    return contract


def _start_execution(payload: dict[str, Any]) -> str:
    """Start the Step Functions remediation loop; returns the execution ARN."""
    import json

    import boto3

    arn = os.environ.get("STATE_MACHINE_ARN", "")
    if not arn:
        raise RuntimeError("STATE_MACHINE_ARN not set")
    resp = boto3.client("stepfunctions").start_execution(
        stateMachineArn=arn, input=json.dumps(payload, default=str)
    )
    return str(resp["executionArn"])


def _remediate_under_contract(
    analysis: str,
    parsed: rca.RcaJson,
    trigger: TriggerInfo,
    config: BeaconConfig,
    timeline: list[dict[str, Any]],
    diagnostics: dict[str, Any] | None,
    change_rows: list[dict[str, Any]] | None,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Consume one contract use, record the approval, start the loop, say so."""
    from beacon import approvals, contracts, store

    action = str(parsed.suggested_action)
    params = dict(parsed.beacon_json["action_params"])
    contracts_table = os.environ["CONTRACTS_TABLE_NAME"]
    approvals_table = os.environ.get("APPROVALS_TABLE_NAME", "")

    if not contracts.use(contract["contract_id"], table_name=contracts_table):
        timeline.append(
            _event("contract_exhausted", contract_id=contract["contract_id"])
        )
        notify(analysis, trigger, config, link=_dashboard_link(config))
        incident_id = _store_and_investigate(
            analysis, parsed, trigger, config, timeline, diagnostics, change_rows
        )
        return {"incident_id": incident_id} if incident_id else {}

    timeline.append(
        _event(
            "contract_matched",
            contract_id=contract["contract_id"],
            uses_left=int(contract.get("max_uses", 0))
            - int(contract.get("uses", 0))
            - 1,
        )
    )
    incident_id = store.put_incident(
        analysis,
        trigger,
        config,
        rca_json=parsed.to_dict(),
        timeline=timeline,
        diagnostics=diagnostics,
        changes=change_rows,
        status="auto_remediating",
        woken=False,
    )
    _store_usage(incident_id, config)
    approval = approvals.create(
        incident_id,
        action,
        params,
        source="contract",
        channel="contract",
        transcript_quote=str(contract.get("transcript_quote", "")),
        contract_id=str(contract["contract_id"]),
        table_name=approvals_table,
    )
    payload = {
        "approval_id": approval["approval_id"],
        "incident_id": incident_id,
        "action": action,
        "params": params,
    }
    try:
        execution_arn = _start_execution(payload)
    except Exception:
        logger.exception("could not start remediation loop; paging instead")
        store.update_status(
            incident_id,
            "awaiting_engineer",
            table_name=config.incidents_table_name,
            extra={"woken": True},
        )
        notify(analysis, trigger, config, link=_dashboard_link(config))
        return {"incident_id": incident_id}

    latest = store.get_incident(incident_id, table_name=config.incidents_table_name)
    status = str(latest.get("status") or "auto_remediating")
    if status not in ("resolved", "escalated"):  # inline mode may already be done
        status = "auto_remediating"
    store.update_status(
        incident_id,
        status,
        table_name=config.incidents_table_name,
        extra={"execution_arn": execution_arn, "handled_by": "contract"},
    )
    store.append_timeline(
        incident_id,
        "remediation_started",
        table_name=config.incidents_table_name,
        detail={"execution_arn": execution_arn, "source": "contract"},
    )
    notify(
        analysis,
        trigger,
        config,
        link=_dashboard_link(config),
        variant="contract",
        contract=contract,
    )
    return {
        "incident_id": incident_id,
        "contract_id": contract["contract_id"],
        "execution_arn": execution_arn,
    }


def _is_healthy(analysis: str) -> bool:
    """Return True if the parsed RCA ``STATUS`` is ``Healthy``."""
    return rca.is_healthy(analysis)
