from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon.remediation import verify

ALARM = "beacon-demo-infra-errors"
DIMS = [{"Name": "Service", "Value": "webapp"}]


@pytest.fixture()
def cw() -> Any:
    with mock_aws():
        client = boto3.client("cloudwatch", region_name="us-east-1")
        client.put_metric_alarm(
            AlarmName=ALARM,
            Namespace="BeaconDemoInfra",
            MetricName="ErrorCount",
            Dimensions=DIMS,
            Statistic="Sum",
            Period=60,
            EvaluationPeriods=1,
            Threshold=3,
            ComparisonOperator="GreaterThanOrEqualToThreshold",
            TreatMissingData="notBreaching",
        )
        yield client


def test_alarm_check_requires_ok_state_updated_after_the_fix(cw: Any) -> None:
    cw.set_alarm_state(AlarmName=ALARM, StateValue="OK", StateReason="test")
    before = datetime.now(tz=UTC) - timedelta(minutes=5)
    check = verify.verify_alarm(ALARM, after=before, cloudwatch_client=cw)
    assert check.ok is True and check.name == "alarm_ok_after_fix"
    assert check.metric["namespace"] == "BeaconDemoInfra"
    assert check.metric["metric_name"] == "ErrorCount"

    future = datetime.now(tz=UTC) + timedelta(minutes=5)
    stale = verify.verify_alarm(ALARM, after=future, cloudwatch_client=cw)
    assert stale.ok is False and "before" in stale.detail


def test_alarm_check_fails_while_in_alarm_or_missing(cw: Any) -> None:
    cw.set_alarm_state(AlarmName=ALARM, StateValue="ALARM", StateReason="test")
    check = verify.verify_alarm(
        ALARM, after=datetime.now(tz=UTC) - timedelta(hours=1), cloudwatch_client=cw
    )
    assert check.ok is False and "ALARM" in check.detail
    missing = verify.verify_alarm(
        "nope", after=datetime.now(tz=UTC), cloudwatch_client=cw
    )
    assert missing.ok is False and "not found" in missing.detail


def test_metric_check_passes_on_zero_or_no_datapoints_and_fails_on_errors(
    cw: Any,
) -> None:
    metric = {
        "namespace": "BeaconDemoInfra",
        "metric_name": "ErrorCount",
        "dimensions": DIMS,
    }
    empty = verify.verify_metric(metric, cloudwatch_client=cw)
    assert empty.ok is True and "no datapoints" in empty.detail

    cw.put_metric_data(
        Namespace="BeaconDemoInfra",
        MetricData=[
            {
                "MetricName": "ErrorCount",
                "Dimensions": DIMS,
                "Value": 4,
                "Unit": "Count",
                "Timestamp": datetime.now(tz=UTC) - timedelta(seconds=90),
            }
        ],
    )
    bad = verify.verify_metric(metric, cloudwatch_client=cw, lookback_minutes=10)
    assert bad.ok is False and "4" in bad.detail


def test_verify_all_combines_three_checks(cw: Any) -> None:
    cw.set_alarm_state(AlarmName=ALARM, StateValue="OK", StateReason="test")
    result = verify.verify_all(
        ALARM,
        after=datetime.now(tz=UTC) - timedelta(minutes=1),
        postcondition=lambda: True,
        cloudwatch_client=cw,
    )
    assert result.ok is True
    assert [c.name for c in result.checks] == [
        "alarm_ok_after_fix",
        "metric_zero",
        "postcondition",
    ]
    failed = verify.verify_all(
        ALARM,
        after=datetime.now(tz=UTC) - timedelta(minutes=1),
        postcondition=lambda: False,
        cloudwatch_client=cw,
    )
    assert failed.ok is False and failed.checks[-1].ok is False
    assert failed.to_dict()["checks"][2]["name"] == "postcondition"


def test_metric_check_ignores_old_errors_when_the_recent_window_is_quiet(
    cw: Any,
) -> None:
    """A metric filter without a default value emits nothing after recovery; an
    error four minutes ago with silence since must count as recovered."""
    metric = {
        "namespace": "BeaconDemoInfra",
        "metric_name": "ErrorCount",
        "dimensions": DIMS,
        "period": 60,
    }
    cw.put_metric_data(
        Namespace="BeaconDemoInfra",
        MetricData=[
            {
                "MetricName": "ErrorCount",
                "Dimensions": DIMS,
                "Value": 5,
                "Unit": "Count",
                "Timestamp": datetime.now(tz=UTC) - timedelta(minutes=4),
            }
        ],
    )
    quiet = verify.verify_metric(metric, cloudwatch_client=cw, lookback_minutes=10)
    assert quiet.ok is True and "no datapoints" in quiet.detail
