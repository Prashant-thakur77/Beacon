from __future__ import annotations

from unittest.mock import MagicMock

import boto3
from moto import mock_aws

from beacon.config import BeaconConfig
from beacon.events import TriggerInfo, TriggerType
from beacon.notifier import notify


@mock_aws
class TestNotify:
    def _setup_sns(self) -> tuple[object, str]:
        client = boto3.client("sns", region_name="us-east-1")
        topic = client.create_topic(Name="beacon-test")
        arn = topic["TopicArn"]
        return client, arn

    def test_publishes_to_sns(self) -> None:
        client, arn = self._setup_sns()
        config = BeaconConfig(log_group_patterns=[], sns_topic_arn=arn)
        trigger = TriggerInfo(trigger_type=TriggerType.ALARM, alarm_name="TestAlarm")

        notify(
            analysis="STATUS: High\nSUMMARY: test",
            trigger=trigger,
            config=config,
            sns_client=client,  # type: ignore[arg-type]
        )

    def test_message_contains_analysis(self) -> None:
        mock_client = MagicMock()
        config = BeaconConfig(
            log_group_patterns=[],
            sns_topic_arn="arn:aws:sns:us-east-1:123:topic",
        )
        trigger = TriggerInfo(trigger_type=TriggerType.SCHEDULE)

        notify(
            analysis="STATUS: Critical\nSUMMARY: disk full",
            trigger=trigger,
            config=config,
            sns_client=mock_client,
        )

        call_kwargs = mock_client.publish.call_args.kwargs
        assert "STATUS: Critical" in call_kwargs["Message"]
        assert "disk full" in call_kwargs["Message"]

    def test_subject_contains_trigger_label(self) -> None:
        mock_client = MagicMock()
        config = BeaconConfig(
            log_group_patterns=[],
            sns_topic_arn="arn:aws:sns:us-east-1:123:topic",
        )
        trigger = TriggerInfo(trigger_type=TriggerType.ALARM, alarm_name="HighMemory")

        notify(
            analysis="test",
            trigger=trigger,
            config=config,
            sns_client=mock_client,
        )

        call_kwargs = mock_client.publish.call_args.kwargs
        assert "HighMemory" in call_kwargs["Subject"]
