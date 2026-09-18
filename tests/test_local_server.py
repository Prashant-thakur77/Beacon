"""``make local``: the whole product without an AWS account (Build It track)."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client(monkeypatch: Any) -> Any:
    monkeypatch.setenv("BEACON_LOCAL_PASSCODE", "local")
    from scripts.local_server import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def test_config_and_health(client: Any) -> None:
    cfg = client.get("/config.json").json()
    assert cfg["voiceUrl"].endswith("/voice") and cfg["dashboardUrl"].endswith("/dash")
    assert client.get("/dash/health").json()["service"] == "beacon-dashboard"
    assert client.get("/voice/health").json()["service"] == "beacon-voice-turn"


def test_seeded_incident_is_listed_and_tally_counts_it(client: Any) -> None:
    incidents = client.get("/dash/incidents").json()["incidents"]
    assert len(incidents) == 1
    inc = incidents[0]
    assert (
        inc["status"] == "awaiting_engineer"
        and inc["alarm_name"] == "beacon-demo-infra-errors"
    )
    assert inc["diagnostics"]["missing_rules"][0]["group_id"].startswith("sg-")
    assert client.get("/dash/tally").json()["incidents_handled"] == 1


def test_full_loop_runs_against_moto(client: Any) -> None:
    inc = client.get("/dash/incidents").json()["incidents"][0]["incident_id"]
    headers = {"x-beacon-passcode": "local"}

    brief = client.post(
        "/voice/turn",
        json={"incident_id": inc, "session_id": "s", "mode": "brief"},
        headers=headers,
    ).json()
    assert (
        brief["reply_text"] and brief["tool_events"][0]["name"] == "get_incident_brief"
    )

    fix = client.post(
        "/voice/turn",
        json={
            "incident_id": inc,
            "session_id": "s",
            "text": "can you fix it",
            "channel": "typed",
        },
        headers=headers,
    ).json()
    assert any(t["name"] == "propose_fix" for t in fix["tool_events"])
    assert fix["incident"]["proposals"][0]["dry_run"]["ok"] is True

    approved = client.post(
        "/voice/turn",
        json={
            "incident_id": inc,
            "session_id": "s",
            "text": "approve fix 1",
            "channel": "typed",
        },
        headers=headers,
    ).json()
    assert any(t["name"] == "approve_fix" for t in approved["tool_events"])

    detail = client.get(f"/dash/incidents/{inc}").json()["incident"]
    assert detail["status"] == "resolved"
    events = [e["event"] for e in detail["timeline"]]
    assert (
        "executed" in events and "verify_attempt" in events and events[-1] == "resolved"
    )

    granted = client.post(
        "/voice/turn",
        json={"incident_id": inc, "session_id": "s", "text": "yes", "channel": "typed"},
        headers=headers,
    ).json()
    assert any(t["name"] == "grant_sleep_contract" for t in granted["tool_events"])
    granted2 = client.post(
        "/voice/turn",
        json={
            "incident_id": inc,
            "session_id": "s",
            "text": "grant contract for seven days",
            "channel": "typed",
        },
        headers=headers,
    ).json()
    assert any("granted" in t["summary"] for t in granted2["tool_events"])
    assert len(client.get("/dash/contracts").json()["contracts"]) == 1


def test_local_break_creates_second_incident_handled_under_contract(
    client: Any,
) -> None:
    inc = client.get("/dash/incidents").json()["incidents"][0]["incident_id"]
    headers = {"x-beacon-passcode": "local"}
    for text in (
        "can you fix it",
        "approve fix 1",
        "yes",
        "grant contract for seven days",
    ):
        client.post(
            "/voice/turn",
            json={
                "incident_id": inc,
                "session_id": "s",
                "text": text,
                "channel": "typed",
            },
            headers=headers,
        )
    resp = client.post("/local/break", headers=headers).json()
    assert resp["ok"] is True
    incidents = client.get("/dash/incidents").json()["incidents"]
    assert len(incidents) == 2
    newest = incidents[0]
    assert (
        newest["handled_by"] == "contract"
        and newest["woken"] is False
        and newest["status"] == "resolved"
    )
    assert client.get("/dash/tally").json()["humans_woken"] == 1


def test_turn_requires_passcode(client: Any) -> None:
    inc = client.get("/dash/incidents").json()["incidents"][0]["incident_id"]
    assert (
        client.post(
            "/voice/turn", json={"incident_id": inc, "session_id": "s", "text": "hi"}
        ).status_code
        == 401
    )
