"""Operational hardening, proven from the templates: logs, alarms, limits."""

from __future__ import annotations

from typing import Any

import pytest

from tests.test_template_safety import _load

TEMPLATES = ("template.yaml", "remediation-template.yaml", "console-template.yaml")


@pytest.fixture(scope="module")
def stacks() -> dict[str, dict[str, Any]]:
    return {name: _load(name) for name in TEMPLATES}


def _resources(t: dict[str, Any], kind: str) -> dict[str, dict[str, Any]]:
    return {k: v for k, v in t["Resources"].items() if v["Type"] == kind}


def _ref_name(value: Any) -> str:
    return value["Fn::Ref"] if isinstance(value, dict) else str(value)


def test_every_lambda_logs_to_an_explicit_group_with_short_retention(
    stacks: dict[str, dict[str, Any]],
) -> None:
    for name, t in stacks.items():
        groups = _resources(t, "AWS::Logs::LogGroup")
        assert groups, f"{name}: no log groups"
        for g in groups.values():
            assert 1 <= g["Properties"]["RetentionInDays"] <= 30, name
        for fn_name, fn in _resources(t, "AWS::Lambda::Function").items():
            cfg = fn["Properties"].get("LoggingConfig")
            assert cfg, f"{name}: {fn_name} has no LoggingConfig"
            assert _ref_name(cfg["LogGroup"]) in groups, f"{name}: {fn_name}"
            assert cfg.get("LogFormat") == "JSON", f"{name}: {fn_name}"


def test_every_table_has_point_in_time_recovery(
    stacks: dict[str, dict[str, Any]],
) -> None:
    tables = _resources(stacks["remediation-template.yaml"], "AWS::DynamoDB::Table")
    assert len(tables) >= 4
    for name, table in tables.items():
        pitr = table["Properties"].get("PointInTimeRecoverySpecification", {})
        assert pitr.get("PointInTimeRecoveryEnabled") is True, name


def test_public_and_privileged_functions_have_bounded_concurrency(
    stacks: dict[str, dict[str, Any]],
) -> None:
    remediate = stacks["remediation-template.yaml"]["Resources"][
        "BeaconRemediateFunction"
    ]
    voice = stacks["console-template.yaml"]["Resources"]["BeaconVoiceTurnFunction"]
    dashboard = stacks["console-template.yaml"]["Resources"]["BeaconDashboardFunction"]
    for fn in (remediate, voice, dashboard):
        limit = fn["Properties"].get("ReservedConcurrentExecutions")
        assert isinstance(limit, int) and 1 <= limit <= 50


def test_lambda_errors_and_escalations_page_the_sns_topic(
    stacks: dict[str, dict[str, Any]],
) -> None:
    alarms: dict[str, dict[str, Any]] = {}
    for t in stacks.values():
        alarms.update(_resources(t, "AWS::CloudWatch::Alarm"))
    error_alarms = [
        a
        for a in alarms.values()
        if a["Properties"].get("MetricName") == "Errors"
        and a["Properties"].get("Namespace") == "AWS/Lambda"
    ]
    assert len(error_alarms) >= 4, "each Beacon function needs an Errors alarm"
    escalated = [
        a for a in alarms.values() if a["Properties"].get("MetricName") == "Escalated"
    ]
    assert escalated and escalated[0]["Properties"]["Namespace"] == "Beacon"
    for a in error_alarms + escalated:
        assert a["Properties"]["AlarmActions"], "alarm without an action"
        assert a["Properties"].get("TreatMissingData") == "notBreaching"


def test_console_sends_security_headers_and_a_csp(
    stacks: dict[str, dict[str, Any]],
) -> None:
    console = stacks["console-template.yaml"]
    policies = _resources(console, "AWS::CloudFront::ResponseHeadersPolicy")
    assert len(policies) == 1
    cfg = next(iter(policies.values()))["Properties"]["ResponseHeadersPolicyConfig"]
    sec = cfg["SecurityHeadersConfig"]
    assert sec["StrictTransportSecurity"]["AccessControlMaxAgeSec"] >= 31536000
    assert sec["ContentTypeOptions"]["Override"] is True
    assert sec["FrameOptions"]["FrameOption"] == "DENY"
    csp = sec["ContentSecurityPolicy"]["ContentSecurityPolicy"]
    csp_text = csp["Fn::Sub"] if isinstance(csp, dict) else csp
    assert "default-src 'self'" in csp_text
    assert "wss://transcribestreaming." in csp_text
    assert "frame-ancestors 'none'" in csp_text
    assert "media-src 'self' blob: data:" in csp_text
    dist = next(iter(_resources(console, "AWS::CloudFront::Distribution").values()))
    behaviour = dist["Properties"]["DistributionConfig"]["DefaultCacheBehavior"]
    assert _ref_name(behaviour["ResponseHeadersPolicyId"]) in policies


def test_demo_database_password_lives_in_secrets_manager() -> None:
    demo = _load("demo/demo-infra-template.yaml")
    assert "DBPassword" not in demo.get("Parameters", {})
    db = demo["Resources"]["DemoDatabase"]["Properties"]
    assert db.get("ManageMasterUserPassword") is True
    assert "MasterUserPassword" not in db
    container = demo["Resources"]["TaskDefinition"]["Properties"][
        "ContainerDefinitions"
    ][0]
    env_names = {e["Name"] for e in container.get("Environment", [])}
    assert "DB_PASSWORD" not in env_names
    secrets = {s["Name"]: s["ValueFrom"] for s in container["Secrets"]}
    assert "MasterUserSecret.SecretArn" in str(secrets["DB_PASSWORD"])
    exec_role = demo["Resources"]["TaskExecutionRole"]["Properties"]
    actions = {
        a
        for p in exec_role["Policies"]
        for s in p["PolicyDocument"]["Statement"]
        for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])
    }
    assert actions == {"secretsmanager:GetSecretValue"}
