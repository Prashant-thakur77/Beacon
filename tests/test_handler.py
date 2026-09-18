from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import boto3
from moto import mock_aws

from beacon.handler import handler


def _setup_env(monkeypatch: Any, sns_arn: str) -> None:
    monkeypatch.setenv("LOG_GROUP_PATTERNS", "/test/app")
    monkeypatch.setenv("SNS_TOPIC_ARN", sns_arn)
    monkeypatch.setenv("LOOKBACK_MINUTES", "5")
    monkeypatch.setenv("TOKEN_BUDGET", "100000")


def _seed_logs(client: Any, group: str, messages: list[str]) -> None:
    client.create_log_group(logGroupName=group)
    client.create_log_stream(logGroupName=group, logStreamName="stream-1")
    now_ms = int(time.time() * 1000)
    events = [
        {"timestamp": now_ms - (len(messages) - i) * 1000, "message": msg}
        for i, msg in enumerate(messages)
    ]
    client.put_log_events(
        logGroupName=group, logStreamName="stream-1", logEvents=events
    )


def _make_client_dispatcher(
    logs_client: Any, sns_client: Any, dynamodb_client: Any | None = None
) -> Any:
    """Route boto3.client(service) to pre-built moto clients.

    All beacon modules share the one ``boto3`` module, so patching
    ``beacon.x.boto3.client`` patches it for every module; a single
    dispatcher keeps each service on the right client.
    """

    def _dispatcher(service: str, **kwargs: Any) -> Any:
        if service == "logs":
            return logs_client
        if service == "sns":
            return sns_client
        if service == "dynamodb" and dynamodb_client is not None:
            return dynamodb_client
        raise ValueError(f"Unexpected service: {service}")

    return _dispatcher


@mock_aws
class TestHandlerIntegration:
    @patch("beacon.handler.resolve_log_groups", return_value=["/test/app"])
    @patch("beacon.handler.analyze_logs")
    @patch("litellm.completion")
    def test_schedule_event_full_pipeline(
        self,
        mock_completion: MagicMock,
        mock_analyze: MagicMock,
        _mock_resolve: MagicMock,
        schedule_event: dict,
        nova_response: str,
        cordon_output: str,
        monkeypatch: Any,
    ) -> None:
        logs_client = boto3.client("logs", region_name="us-east-1")
        sns_client = boto3.client("sns", region_name="us-east-1")
        topic = sns_client.create_topic(Name="test-topic")
        sns_arn = topic["TopicArn"]

        _setup_env(monkeypatch, sns_arn)
        _seed_logs(
            logs_client,
            "/test/app",
            [
                "INFO Processing batch 0",
                "INFO Processing batch 1",
                "ERROR Connection refused",
            ],
        )

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=nova_response))]
        mock_completion.return_value = mock_resp
        mock_analyze.return_value = cordon_output

        dispatcher = _make_client_dispatcher(logs_client, sns_client)
        with (
            patch("beacon.logs.boto3.client", side_effect=dispatcher),
            patch("beacon.notifier.boto3.client", side_effect=dispatcher),
        ):
            result = handler(schedule_event, None)

        assert result["statusCode"] == 200

    @patch("beacon.handler.resolve_log_groups", return_value=["/test/app"])
    @patch("litellm.completion")
    def test_returns_no_logs_when_group_empty(
        self,
        mock_completion: MagicMock,
        _mock_resolve: MagicMock,
        schedule_event: dict,
        monkeypatch: Any,
    ) -> None:
        logs_client = boto3.client("logs", region_name="us-east-1")
        sns_client = boto3.client("sns", region_name="us-east-1")
        topic = sns_client.create_topic(Name="test-topic")
        sns_arn = topic["TopicArn"]

        _setup_env(monkeypatch, sns_arn)
        logs_client.create_log_group(logGroupName="/test/app")
        logs_client.create_log_stream(
            logGroupName="/test/app", logStreamName="stream-1"
        )

        with patch("beacon.logs.boto3.client", return_value=logs_client):
            result = handler(schedule_event, None)

        assert result["body"] == "No logs found"
        mock_completion.assert_not_called()

    @patch(
        "beacon.handler.resolve_log_groups",
        return_value=["/aws/lambda/my-app"],
    )
    @patch("beacon.handler.analyze_logs")
    @patch("litellm.completion")
    def test_subscription_event_uses_raw_logs(
        self,
        mock_completion: MagicMock,
        mock_analyze: MagicMock,
        _mock_resolve: MagicMock,
        subscription_event: dict,
        nova_response: str,
        monkeypatch: Any,
    ) -> None:
        sns_client = boto3.client("sns", region_name="us-east-1")
        topic = sns_client.create_topic(Name="test-topic")
        sns_arn = topic["TopicArn"]

        monkeypatch.setenv("LOG_GROUP_PATTERNS", "/aws/lambda/my-app")
        monkeypatch.setenv("SNS_TOPIC_ARN", sns_arn)
        monkeypatch.setenv("TOKEN_BUDGET", "100000")

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=nova_response))]
        mock_completion.return_value = mock_resp

        with patch("beacon.notifier.boto3.client", return_value=sns_client):
            result = handler(subscription_event, None)

        assert result["statusCode"] == 200


class TestIsHealthy:
    def test_tolerates_markdown_bold_status(self) -> None:
        from beacon.handler import _is_healthy

        assert _is_healthy("**STATUS:** Healthy\n**SUMMARY:** Normal operation.")

    def test_false_for_non_healthy(self) -> None:
        from beacon.handler import _is_healthy

        assert not _is_healthy("STATUS: High\nSUMMARY: broken")


def _create_incidents_table(client: Any, name: str) -> None:
    client.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "incident_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "incident_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@mock_aws
class TestIncidentStoreWithoutConnect:
    def _env(self, monkeypatch: Any, sns_arn: str) -> None:
        _setup_env(monkeypatch, sns_arn)
        monkeypatch.setenv("INCIDENTS_ENABLED", "true")
        monkeypatch.setenv("INCIDENTS_TABLE_NAME", "beacon-incidents-test")
        monkeypatch.setenv("CONNECT_ENABLED", "false")

    @patch("beacon.prefetch.run")
    @patch("beacon.handler.resolve_log_groups", return_value=["/test/app"])
    @patch("beacon.handler.analyze_logs")
    @patch("litellm.completion")
    def test_alarm_event_stores_incident_with_rca_json_and_timeline(
        self,
        mock_completion: MagicMock,
        mock_analyze: MagicMock,
        _mock_resolve: MagicMock,
        mock_prefetch: MagicMock,
        alarm_event: dict,
        nova_response: str,
        cordon_output: str,
        monkeypatch: Any,
    ) -> None:
        logs_client = boto3.client("logs", region_name="us-east-1")
        sns_client = boto3.client("sns", region_name="us-east-1")
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        sns_arn = sns_client.create_topic(Name="test-topic")["TopicArn"]
        _create_incidents_table(ddb, "beacon-incidents-test")
        self._env(monkeypatch, sns_arn)
        _seed_logs(logs_client, "/test/app", ["INFO ok", "ERROR Connection refused"])

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=nova_response))]
        mock_completion.return_value = mock_resp
        mock_analyze.return_value = cordon_output

        dispatcher = _make_client_dispatcher(logs_client, sns_client, ddb)
        with patch("boto3.client", side_effect=dispatcher):
            result = handler(alarm_event, None)

        assert result["statusCode"] == 200
        items = ddb.scan(TableName="beacon-incidents-test")["Items"]
        assert len(items) == 1
        item = items[0]
        assert item["status"]["S"] == "awaiting_engineer"
        assert item["rca_json"]["M"]["status"]["S"] == "High"
        events = [e["M"]["event"]["S"] for e in item["timeline"]["L"]]
        assert events[0] == "alarm_received"
        assert "logs_fetched" in events
        assert "rca_ready" in events
        assert events[-1] == "sns_sent"
        mock_prefetch.assert_called_once()
        assert "incident_id" in result

    @patch("beacon.prefetch.run")
    @patch("beacon.handler.resolve_log_groups", return_value=["/test/app"])
    @patch("beacon.handler.analyze_logs")
    @patch("litellm.completion")
    def test_repeat_alarm_within_window_is_deduplicated(
        self,
        mock_completion: MagicMock,
        mock_analyze: MagicMock,
        _mock_resolve: MagicMock,
        _mock_prefetch: MagicMock,
        alarm_event: dict,
        nova_response: str,
        cordon_output: str,
        monkeypatch: Any,
    ) -> None:
        logs_client = boto3.client("logs", region_name="us-east-1")
        sns_client = boto3.client("sns", region_name="us-east-1")
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        sns_arn = sns_client.create_topic(Name="test-topic")["TopicArn"]
        _create_incidents_table(ddb, "beacon-incidents-test")
        self._env(monkeypatch, sns_arn)
        _seed_logs(logs_client, "/test/app", ["ERROR Connection refused"])

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=nova_response))]
        mock_completion.return_value = mock_resp
        mock_analyze.return_value = cordon_output

        dispatcher = _make_client_dispatcher(logs_client, sns_client, ddb)
        with patch("boto3.client", side_effect=dispatcher):
            first = handler(alarm_event, None)
            second = handler(alarm_event, None)

        assert first["statusCode"] == 200
        assert second["body"].startswith("Duplicate incident")
        assert len(ddb.scan(TableName="beacon-incidents-test")["Items"]) == 1
        assert mock_completion.call_count == 1
