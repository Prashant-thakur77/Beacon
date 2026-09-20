from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon import contracts, dashboard_api, store
from beacon.events import TriggerInfo, TriggerType
from tests.test_function_urls import url_event

INCIDENTS = "beacon-incidents-test"
CONTRACTS = "beacon-contracts-test"
APPROVALS = "beacon-approvals-test"
PARAMS = {
    "group_id": "sg-rds",
    "ip_protocol": "tcp",
    "from_port": 5432,
    "to_port": 5432,
    "source_group_id": "sg-ecs",
}


def _table(ddb: Any, name: str, key: str) -> None:
    ddb.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture()
def env(monkeypatch: Any) -> Any:
    dashboard_api._cache.clear()
    monkeypatch.setattr(dashboard_api, "_CACHE_SECONDS", 0.0)
    with mock_aws():
        for var, val in {
            "AWS_DEFAULT_REGION": "us-east-1",
            "INCIDENTS_TABLE_NAME": INCIDENTS,
            "CONTRACTS_TABLE_NAME": CONTRACTS,
            "APPROVALS_TABLE_NAME": APPROVALS,
            "PASSCODE": "nightshift",
            "BASE_STACK_NAME": "test",
        }.items():
            monkeypatch.setenv(var, val)
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        for name, key in (
            (INCIDENTS, "incident_id"),
            (CONTRACTS, "contract_id"),
            (APPROVALS, "approval_id"),
        ):
            _table(ddb, name, key)
        trig = TriggerInfo(
            trigger_type=TriggerType.ALARM, alarm_name="beacon-demo-infra-errors"
        )
        a = store.put_incident(
            "STATUS: High\nSUMMARY: a",
            trig,
            table_name=INCIDENTS,
            rca_json={"status": "High", "summary": "a", "beacon_json": {}},
            changes=[
                {
                    "event_name": "RevokeSecurityGroupIngress",
                    "actor": "arn:aws:iam::123456789012:user/prashant",
                    "actor_short": "user/prashant",
                    "resource_ids": ["sg-rds"],
                }
            ],
        )
        store.update_status(
            a,
            "resolved",
            table_name=INCIDENTS,
            extra={
                "resolved_at": (
                    datetime.now(tz=UTC) + timedelta(minutes=3)
                ).isoformat(),
                "handled_by": "voice",
                "conversation": [
                    {"role": "user", "content": [{"text": "secret chat"}]}
                ],
                "execution_arn": (
                    "arn:aws:states:us-east-1:123456789012:execution:"
                    "beacon-remediate-test:run1"
                ),
            },
        )
        b = store.put_incident(
            "STATUS: High\nSUMMARY: b",
            trig,
            table_name=INCIDENTS,
            rca_json={"status": "High", "summary": "b", "beacon_json": {}},
            status="auto_remediating",
            woken=False,
        )
        store.update_status(
            b,
            "resolved",
            table_name=INCIDENTS,
            extra={
                "resolved_at": (
                    datetime.now(tz=UTC) + timedelta(minutes=2)
                ).isoformat(),
                "handled_by": "contract",
            },
        )
        contract = contracts.put(
            alarm_name="beacon-demo-infra-errors",
            action="sg.restore_ingress",
            params=PARAMS,
            days=7,
            max_uses=3,
            transcript_quote="grant contract for seven days",
            granted_by="transcribe",
            incident_id=a,
            table_name=CONTRACTS,
        )
        yield {"a": a, "b": b, "contract": contract, "ddb": ddb}


def _get(path: str) -> dict[str, Any]:
    resp = dashboard_api.handler(url_event("GET", path), None)
    return {
        "status": resp["statusCode"],
        "body": json.loads(resp["body"]) if resp.get("body") else None,
    }


def test_incidents_list_newest_first_without_conversation_or_raw_rca(env: Any) -> None:
    out = _get("/incidents")
    assert out["status"] == 200
    items = out["body"]["incidents"]
    assert [i["incident_id"] for i in items] == [env["b"], env["a"]]
    assert "conversation" not in items[1] and "rca" not in items[1]
    assert items[0]["woken"] is False and items[0]["handled_by"] == "contract"


def test_incident_detail_redacts_account_ids_and_actor_arns(env: Any) -> None:
    out = _get(f"/incidents/{env['a']}")
    assert out["status"] == 200
    inc = out["body"]["incident"]
    body_text = json.dumps(inc)
    assert "123456789012" not in body_text
    assert inc["changes"][0]["actor"] == "user/prashant"
    assert "conversation" not in inc
    assert _get("/incidents/nope")["status"] == 404


def test_tally_counts_handled_median_and_humans_woken(env: Any) -> None:
    out = _get("/tally")
    assert out["status"] == 200
    t = out["body"]
    assert t["incidents_handled"] == 2 and t["resolved"] == 2
    assert t["humans_woken"] == 1
    assert (
        t["median_minutes_to_recovery"] is not None
        and t["median_minutes_to_recovery"] >= 0
    )


def test_contracts_list_and_revoke_with_passcode(env: Any) -> None:
    out = _get("/contracts")
    assert (
        out["status"] == 200
        and out["body"]["contracts"][0]["contract_id"] == env["contract"]["contract_id"]
    )
    assert out["body"]["contracts"][0]["scope"] == PARAMS

    denied = dashboard_api.handler(
        url_event("DELETE", f"/contracts/{env['contract']['contract_id']}"), None
    )
    assert denied["statusCode"] == 401
    ok = dashboard_api.handler(
        url_event(
            "DELETE",
            f"/contracts/{env['contract']['contract_id']}",
            headers={"x-beacon-passcode": "nightshift"},
        ),
        None,
    )
    assert ok["statusCode"] == 200
    assert _get("/contracts")["body"]["contracts"] == []


def test_safety_lists_allowlist_and_kill_switch_state(env: Any, mocker: Any) -> None:
    mocker.patch(
        "beacon.dashboard_api._apply_flags",
        return_value={"triage": True, "voice": True, "remediate": False},
    )
    out = _get("/safety")
    assert out["status"] == 200
    ids = [a["id"] for a in out["body"]["allowlist"]]
    assert ids == ["sg.restore_ingress", "sg.revoke_ingress", "ecs.force_redeploy"]
    by_id = {a["id"]: a for a in out["body"]["allowlist"]}
    assert by_id["sg.restore_ingress"]["inverse"] == "sg.revoke_ingress"
    assert by_id["sg.revoke_ingress"]["undo_of"] == "sg.restore_ingress"
    assert by_id["ecs.force_redeploy"]["inverse"] is None
    assert out["body"]["allowlist"][0]["iam_actions"] == [
        "ec2:AuthorizeSecurityGroupIngress"
    ]
    assert out["body"]["apply_enabled"] == {
        "triage": True,
        "voice": True,
        "remediate": False,
    }


def test_execution_route_is_graceful_without_step_functions(env: Any) -> None:
    out = _get(f"/incidents/{env['a']}/execution")
    assert out["status"] == 200
    assert out["body"]["execution_arn"].endswith("run1")
    assert isinstance(out["body"]["events"], list)


def test_redact_helper() -> None:
    data = {
        "actor": "arn:aws:sts::123456789012:assumed-role/beacon-remediator-x/y",
        "note": "acct 123456789012 here",
        "nested": [{"arn": "arn:aws:iam::123456789012:user/p"}],
    }
    out = dashboard_api.redact(data)
    assert out["actor"] == "role/beacon-remediator-x"
    assert "123456789012" not in json.dumps(out)
    assert out["nested"][0]["arn"] == "user/p"


def test_tally_reports_cost_in_rupees_and_sleep_protected(env: Any) -> None:
    from beacon import store

    # a night incident (IST) handled under contract with token usage recorded
    store.update_status(
        env["b"],
        "resolved",
        table_name=INCIDENTS,
        extra={
            "usage": {
                "input_tokens": 12000,
                "output_tokens": 1500,
                "embedding_tokens": 8000,
            },
            "timestamp": "2026-09-18T21:30:00+00:00",  # 03:00 IST
        },
    )
    store.update_status(
        env["a"],
        "resolved",
        table_name=INCIDENTS,
        extra={"usage": {"input_tokens": 6000, "output_tokens": 800}},
    )
    out = _get("/tally")["body"]
    assert out["cost_inr_total"] > 0
    assert out["cost_inr_per_incident"] == pytest.approx(
        out["cost_inr_total"] / 2, rel=1e-6
    )
    assert out["sleep_protected_hours"] >= 1
    assert out["night_incidents_not_woken"] == 1


def test_metric_route_returns_datapoints_and_execute_marker(
    env: Any, mocker: Any
) -> None:
    from datetime import UTC, datetime, timedelta

    from beacon import store

    now = datetime.now(tz=UTC)
    store.update_status(
        env["a"],
        "resolved",
        table_name=INCIDENTS,
        extra={
            "executed_at": (now - timedelta(minutes=3)).isoformat(),
            "alarm_name": "beacon-demo-infra-errors",
        },
    )
    mocker.patch(
        "beacon.dashboard_api._alarm_metric_series",
        return_value=[
            {"t": (now - timedelta(minutes=5)).isoformat(), "v": 4},
            {"t": (now - timedelta(minutes=4)).isoformat(), "v": 6},
            {"t": (now - timedelta(minutes=2)).isoformat(), "v": 1},
            {"t": (now - timedelta(minutes=1)).isoformat(), "v": 0},
        ],
    )
    out = _get(f"/incidents/{env['a']}/metric")
    assert out["status"] == 200
    assert [p["v"] for p in out["body"]["points"]] == [4, 6, 1, 0]
    assert out["body"]["executed_at"]
    assert out["body"]["metric"]["metric_name"] == "ErrorCount"


def test_health_reports_version_and_region(env: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("BEACON_VERSION", "0.2.0-test")
    out = _get("/health")
    assert out["status"] == 200
    assert out["body"]["ok"] is True
    assert out["body"]["version"] == "0.2.0-test"
    assert out["body"]["region"] == "us-east-1"
    assert out["body"]["stack"] == "test"


def test_analytics_groups_nights_and_counts_humans_contracts_cost(env: Any) -> None:
    # incident a: woken, resolved by voice, with a proposal 40 s after the alarm;
    # incident b: handled under contract at 03:00 IST (same night as a 23:30 page)
    store.update_status(
        env["a"],
        "resolved",
        table_name=INCIDENTS,
        extra={
            "timestamp": "2026-09-18T18:00:00+00:00",  # 23:30 IST
            "resolved_at": "2026-09-18T18:03:00+00:00",
            "usage": {"input_tokens": 6000, "output_tokens": 800},
            "timeline": [
                {"t": "2026-09-18T18:00:00+00:00", "event": "alarm_received"},
                {"t": "2026-09-18T18:00:40+00:00", "event": "fix_proposed"},
            ],
        },
    )
    store.update_status(
        env["b"],
        "resolved",
        table_name=INCIDENTS,
        extra={
            "timestamp": "2026-09-18T21:30:00+00:00",  # 03:00 IST, next calendar day
            "resolved_at": "2026-09-18T21:31:00+00:00",
            "usage": {"input_tokens": 12000, "output_tokens": 1500},
        },
    )
    out = _get("/analytics")
    assert out["status"] == 200
    a = out["body"]
    assert [n["night"] for n in a["nights"]] == ["2026-09-18"]
    night = a["nights"][0]
    assert night["incidents"] == 2 and night["resolved"] == 2
    assert night["woken"] == 1 and night["under_contract"] == 1
    assert night["median_minutes_to_recovery"] == pytest.approx(2.0)
    assert night["p90_minutes_to_recovery"] == pytest.approx(2.8)
    assert a["recovery"]["p50_minutes"] == pytest.approx(2.0)
    assert a["first_proposal"] == {"count": 1, "mean_seconds": 40.0}
    assert a["outcomes"] == {"resolved": 2, "escalated": 0, "in_progress": 0}
    assert a["humans"] == {"woken": 1, "under_contract": 1}
    assert a["cost"]["total_inr"] > 0
    assert a["incidents"][-1]["cost_inr_cumulative"] == pytest.approx(
        a["cost"]["total_inr"]
    )
    assert a["top_alarms"] == [{"alarm_name": "beacon-demo-infra-errors", "count": 2}]
    assert len(a["contracts"]) == 1
    c = a["contracts"][0]
    assert c["uses_left"] == 3 and c["max_uses"] == 3
    assert 6 * 24 < c["hours_left"] <= 7 * 24
    assert "123456789012" not in json.dumps(a)


def test_analytics_is_empty_but_well_formed_on_a_fresh_deployment() -> None:
    a = dashboard_api.build_analytics([], [])
    assert a["nights"] == [] and a["incidents"] == [] and a["contracts"] == []
    assert a["recovery"]["p50_minutes"] is None
    assert a["first_proposal"]["mean_seconds"] is None
    assert a["cost"] == {"total_inr": 0.0, "per_incident_inr": 0.0}
    assert a["top_alarms"] == []


def test_night_of_turns_over_at_noon_ist() -> None:
    assert dashboard_api._night_of("2026-09-18T18:00:00+00:00") == "2026-09-18"
    assert dashboard_api._night_of("2026-09-18T21:30:00+00:00") == "2026-09-18"
    assert (
        dashboard_api._night_of("2026-09-19T05:00:00+00:00") == "2026-09-18"
    )  # 10:30 IST
    assert (
        dashboard_api._night_of("2026-09-19T08:00:00+00:00") == "2026-09-19"
    )  # 13:30 IST
    assert dashboard_api._night_of("garbage") is None


def test_safety_controls_name_real_files_and_tests(env: Any, mocker: Any) -> None:
    from pathlib import Path

    mocker.patch("beacon.dashboard_api._apply_flags", return_value={})
    controls = _get("/safety")["body"]["controls"]
    assert len(controls) == 9
    root = Path(__file__).resolve().parent.parent
    for c in controls:
        assert (root / c["file"]).exists(), c["file"]
        test_file, test_name = c["test"].split("::")
        assert f"def {test_name}(" in (root / test_file).read_text(), c["test"]
    assert [c["rule"] for c in controls[:8]] == dashboard_api.SAFETY_RULES


def test_redact_leaves_uuids_with_numeric_segments_alone() -> None:
    uuid = "94399b40-6161-474b-97ee-658469239225"
    data = {
        "contract_id": uuid,
        "arn": "arn:aws:states:us-east-1:123456789012:execution:x:" + uuid,
        "note": "account 123456789012 and id 658469239225a and 658469239225-1",
        "sg": "sg-058469239225",
    }
    out = dashboard_api.redact(data)
    assert out["contract_id"] == uuid
    assert out["arn"].endswith(uuid)
    assert "123456789012" not in out["arn"] and "123456789012" not in out["note"]
    assert "658469239225a" in out["note"] and "658469239225-1" in out["note"]
    assert out["sg"] == "sg-058469239225"


def test_postmortem_audit_and_report_routes(env: Any) -> None:
    from beacon import approvals

    incident_id = env["a"]
    approvals.create(
        incident_id,
        "sg.restore_ingress",
        {"group_id": "sg-1"},
        source="voice",
        channel="typed",
        transcript_quote="approve fix 1",
        table_name=APPROVALS,
    )
    pm = dashboard_api.handler(
        url_event("GET", f"/incidents/{incident_id}/postmortem"), None
    )
    assert pm["statusCode"] == 200
    assert pm["headers"]["Content-Type"].startswith("text/markdown")
    assert "# Postmortem" in pm["body"] and '"approve fix 1"' in pm["body"]
    assert "grant contract for seven days" in pm["body"]  # the fixture's contract

    au = json.loads(dashboard_api.handler(url_event("GET", "/audit"), None)["body"])
    assert sorted(r["kind"] for r in au["rows"]) == ["approval", "contract"]
    assert au["count"] == 2
    ev = url_event("GET", "/audit")
    ev["rawQueryString"] = "format=csv"
    ev["queryStringParameters"] = {"format": "csv"}
    csv = dashboard_api.handler(ev, None)
    assert csv["headers"]["Content-Type"].startswith("text/csv")
    assert "approve fix 1" in csv["body"]

    rep = json.loads(
        dashboard_api.handler(url_event("GET", "/report/latest"), None)["body"]
    )
    assert "night_of" in rep and "Good morning" in rep["text"]
    missing = dashboard_api.handler(
        url_event("GET", "/incidents/nope/postmortem"), None
    )
    assert missing["statusCode"] == 404
