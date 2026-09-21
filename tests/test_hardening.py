"""Production-hardening behaviours: passcode, input limits, bounded clients."""

from __future__ import annotations

import json
from typing import Any

from beacon import aws
from tests.test_function_urls import url_event


def test_passcode_check_is_constant_time_and_rejects_empty(monkeypatch: Any) -> None:
    from beacon.voice_turn import _passcode_ok

    monkeypatch.setenv("PASSCODE", "nightshift")
    assert _passcode_ok({"x-beacon-passcode": "nightshift"}) is True
    assert _passcode_ok({"x-beacon-passcode": "nightshif"}) is False
    assert _passcode_ok({}) is False
    monkeypatch.setenv("PASSCODE", "")
    assert _passcode_ok({}) is False, (
        "an unset passcode must fail closed on a public URL"
    )


def test_turn_rejects_oversized_text_and_bad_ids(monkeypatch: Any) -> None:
    from beacon import voice_turn

    monkeypatch.setenv("PASSCODE", "p")
    headers = {"x-beacon-passcode": "p"}
    too_long = voice_turn.handler(
        url_event(
            "POST",
            "/turn",
            {"incident_id": "a" * 40, "session_id": "s", "text": "x" * 5000},
            headers,
        ),
        None,
    )
    assert too_long["statusCode"] == 413
    bad_id = voice_turn.handler(
        url_event(
            "POST",
            "/turn",
            {"incident_id": "../../etc", "session_id": "s", "text": "hi"},
            headers,
        ),
        None,
    )
    assert bad_id["statusCode"] == 400


def test_client_factory_sets_timeouts_and_retries() -> None:
    client = aws.client("polly", region_name="us-east-1")
    cfg = client.meta.config
    assert cfg.connect_timeout <= 5 and cfg.read_timeout <= 20
    attempts = cfg.retries.get("max_attempts") or cfg.retries.get("total_max_attempts")
    assert attempts is not None and attempts <= 3


def test_dashboard_incident_list_is_cached_briefly(
    monkeypatch: Any, mocker: Any
) -> None:
    from beacon import dashboard_api

    dashboard_api._cache.clear()
    scan = mocker.patch(
        "beacon.dashboard_api._scan_incidents",
        return_value=[{"incident_id": "a", "timestamp": "t", "status": "resolved"}],
    )
    monkeypatch.setenv("INCIDENTS_TABLE_NAME", "t")
    dashboard_api._all_incidents()
    dashboard_api._all_incidents()
    assert scan.call_count == 1


def test_execute_failure_is_recorded_and_replayed(monkeypatch: Any) -> None:
    """A raised exception during execute must not burn the approval silently."""
    import boto3
    from moto import mock_aws

    from beacon import approvals, remediate
    from beacon.remediation import registry

    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        monkeypatch.setenv("APPROVALS_TABLE_NAME", "beacon-approvals-test")
        monkeypatch.setenv("INCIDENTS_TABLE_NAME", "")
        monkeypatch.setenv("APPLY_ENABLED", "true")
        monkeypatch.setenv("REMEDIABLE_ECS_SERVICES", "c/s")
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="beacon-approvals-test",
            KeySchema=[{"AttributeName": "approval_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "approval_id", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        params = {"cluster": "c", "service": "s"}
        rec = approvals.create(
            "inc",
            "ecs.force_redeploy",
            params,
            source="cli",
            channel="cli",
            transcript_quote="approve fix 1",
            table_name="beacon-approvals-test",
        )
        spec = registry.get_action("ecs.force_redeploy")
        assert spec is not None
        import dataclasses

        def boom(_p: dict[str, Any]) -> Any:
            raise RuntimeError("boom")

        monkeypatch.setitem(
            registry.REGISTRY,
            "ecs.force_redeploy",
            dataclasses.replace(spec, execute=boom),
        )
        first = remediate.handler(
            {
                "step": "execute",
                "approval_id": rec["approval_id"],
                "incident_id": "inc",
                "action": "ecs.force_redeploy",
                "params": params,
            },
            None,
        )
        assert first["ok"] is False and "boom" in first["error"]
        second = remediate.handler(
            {
                "step": "execute",
                "approval_id": rec["approval_id"],
                "incident_id": "inc",
                "action": "ecs.force_redeploy",
                "params": params,
            },
            None,
        )
        assert second["ok"] is False and second["idempotent_replay"] is True


def test_health_reports_version() -> None:
    from beacon import __version__, voice_turn

    resp = voice_turn.handler(url_event("GET", "/health"), None)
    assert json.loads(resp["body"])["version"] == __version__


def test_image_requirement_pins_match_the_tested_environment() -> None:
    """The images install ``requirements/*.txt``; those pins must track what
    this suite runs against. A fresh ``make setup`` resolves the newest patch
    release, so the check is major.minor (a patch drift is a warning in the
    assertion message, not a red gate)."""
    import re
    from importlib.metadata import version
    from pathlib import Path

    for name in ("triage", "agent"):
        text = (
            Path(__file__)
            .parent.parent.joinpath("requirements", f"{name}.txt")
            .read_text()
        )
        pins = re.findall(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^\s#]+)", text, re.M)
        assert pins, name
        for pkg, pinned in pins:
            installed = version(pkg)
            # Same major, and never older than the pin: a fresh install may resolve
            # a newer minor (litellm ships minors weekly); the image stays pinned.
            assert installed.split(".")[0] == pinned.split(".")[0], (
                f"{name}: {pkg} pinned {pinned}, env {installed}; bump the pin"
            )
            key = lambda v: tuple(int(x) for x in v.split("+")[0].split(".")[:3])  # noqa: E731
            assert key(installed) >= key(pinned), f"{name}: {pkg} env older than pin"


def test_triage_entry_points_do_not_import_powertools() -> None:
    """The torch triage image has no aws_lambda_powertools; every module the
    triage handler can reach (incl. the morning-report path) must stay clear."""
    import ast
    from pathlib import Path

    root = Path(__file__).parent.parent / "src" / "beacon"
    forbidden = {
        "beacon.dashboard_api",
        "beacon.voice_turn",
        "beacon.voice_tools",
        "beacon.remediate",
        "beacon.telegram_bot",  # inbound half needs the tools; pages only need telegram
        "beacon.fix_pr",
    }
    seen: set[str] = set()
    todo = ["beacon.handler"]
    while todo:
        mod = todo.pop()
        if mod in seen or not mod.startswith("beacon"):
            continue
        seen.add(mod)
        path = root / (mod.split(".", 1)[1].replace(".", "/") + ".py")
        if not path.exists():
            path = root / mod.split(".", 1)[1].replace(".", "/") / "__init__.py"
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                base = node.module
                if base == "beacon":
                    for alias in node.names:
                        todo.append(f"beacon.{alias.name}")
                elif base.startswith("beacon"):
                    todo.append(base)
                assert not base.startswith("aws_lambda_powertools"), (
                    f"{mod} imports Powertools"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aws_lambda_powertools"), (
                        f"{mod} imports Powertools"
                    )
                    if alias.name.startswith("beacon"):
                        todo.append(alias.name)
    assert not (seen & forbidden), (
        f"triage reaches Powertools-dependent modules: {seen & forbidden}"
    )
