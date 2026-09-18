"""Verification: "recovered" is a claim Beacon must prove, not a boolean read.

Three checks, all required:

1. the alarm that paged is ``OK`` **and** its state changed after the fix
   was executed (a forced ``set-alarm-state`` or a stale OK cannot pass);
2. the alarm's own metric has no errors in the latest complete period;
3. the action's post-condition holds (for example the rule is present).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import boto3

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VerifyCheck:
    name: str
    ok: bool
    detail: str = ""
    metric: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VerifyResult:
    ok: bool
    checks: list[VerifyCheck]

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [asdict(c) for c in self.checks]}


def _cw(client: Any | None) -> Any:
    return client if client is not None else boto3.client("cloudwatch")


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def verify_alarm(
    alarm_name: str, *, after: datetime, cloudwatch_client: Any | None = None
) -> VerifyCheck:
    """OK, and the OK transition happened after *after* (the execute time)."""
    resp = _cw(cloudwatch_client).describe_alarms(AlarmNames=[alarm_name])
    alarms = resp.get("MetricAlarms", [])
    if not alarms:
        return VerifyCheck("alarm_ok_after_fix", False, f"alarm {alarm_name} not found")
    alarm = alarms[0]
    metric = {
        "namespace": alarm.get("Namespace", ""),
        "metric_name": alarm.get("MetricName", ""),
        "dimensions": alarm.get("Dimensions", []),
        "period": alarm.get("Period", 60),
        "statistic": alarm.get("Statistic", "Sum"),
    }
    state = alarm.get("StateValue", "UNKNOWN")
    updated = alarm.get("StateUpdatedTimestamp")
    if state != "OK":
        return VerifyCheck(
            "alarm_ok_after_fix", False, f"alarm state is {state}", metric
        )
    if updated is None or updated.astimezone(UTC) <= after.astimezone(UTC):
        return VerifyCheck(
            "alarm_ok_after_fix",
            False,
            f"alarm is OK but its state changed at {_iso(updated)}, "
            f"before the fix at {_iso(after)}",
            metric,
        )
    return VerifyCheck(
        "alarm_ok_after_fix",
        True,
        f"alarm OK since {_iso(updated)} (fix at {_iso(after)})",
        metric,
    )


def verify_metric(
    metric: dict[str, Any],
    *,
    lookback_minutes: int = 5,
    cloudwatch_client: Any | None = None,
) -> VerifyCheck:
    """The alarm's metric shows zero in its most recent datapoint.

    A metric filter emits nothing when nothing matches, so "no datapoints"
    means "no errors" and passes (the alarm's own ``notBreaching`` semantics).
    """
    if not metric.get("namespace") or not metric.get("metric_name"):
        return VerifyCheck("metric_zero", False, "alarm has no metric to check")
    end = datetime.now(tz=UTC)
    period = int(metric.get("period") or 60)
    resp = _cw(cloudwatch_client).get_metric_statistics(
        Namespace=metric["namespace"],
        MetricName=metric["metric_name"],
        Dimensions=metric.get("dimensions", []),
        StartTime=end - timedelta(minutes=lookback_minutes),
        EndTime=end,
        Period=period,
        Statistics=[metric.get("statistic", "Sum")],
    )
    points = sorted(resp.get("Datapoints", []), key=lambda p: p["Timestamp"])
    label = f"{metric['namespace']}/{metric['metric_name']}"
    if not points:
        return VerifyCheck(
            "metric_zero",
            True,
            f"{label}: no datapoints in the last {lookback_minutes} min (no errors)",
            metric,
        )
    latest = points[-1]
    value = float(latest.get(metric.get("statistic", "Sum"), 0) or 0)
    if value > 0:
        return VerifyCheck(
            "metric_zero",
            False,
            f"{label} latest value is {value:g} at {_iso(latest['Timestamp'])}",
            metric,
        )
    return VerifyCheck(
        "metric_zero",
        True,
        f"{label} latest value is 0 at {_iso(latest['Timestamp'])}",
        metric,
    )


def verify_all(
    alarm_name: str,
    *,
    after: datetime,
    postcondition: Callable[[], bool],
    cloudwatch_client: Any | None = None,
) -> VerifyResult:
    """Run the three checks; all must pass."""
    alarm_check = verify_alarm(
        alarm_name, after=after, cloudwatch_client=cloudwatch_client
    )
    metric_check = verify_metric(
        alarm_check.metric, cloudwatch_client=cloudwatch_client
    )
    try:
        post_ok = bool(postcondition())
        post_detail = (
            "post-condition holds" if post_ok else "post-condition does not hold"
        )
    except Exception as exc:  # the check itself failing is a failed check
        logger.exception("postcondition raised")
        post_ok, post_detail = False, f"post-condition check failed: {exc}"
    checks = [
        alarm_check,
        metric_check,
        VerifyCheck("postcondition", post_ok, post_detail),
    ]
    return VerifyResult(ok=all(c.ok for c in checks), checks=checks)
