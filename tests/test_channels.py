"""Webhook + PagerDuty fan-out: deep links, dedup keys, and never raising."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from beacon import channels


@pytest.fixture()
def posted(monkeypatch: Any) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    class _Resp:
        status = 202

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(req: Any, timeout: float = 0) -> _Resp:
        calls.append((req.full_url, json.loads(req.data.decode())))
        return _Resp()

    monkeypatch.setattr(channels.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_nothing_configured_sends_nothing(monkeypatch: Any, posted: Any) -> None:
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    monkeypatch.delenv("PAGERDUTY_ROUTING_KEY", raising=False)
    assert channels.send("page", "t", "x", incident_id="i1") == {}
    assert posted == []


def test_webhook_gets_a_slack_message_with_a_deep_link(
    monkeypatch: Any, posted: Any
) -> None:
    monkeypatch.setenv("WEBHOOK_URL", "https://hooks.example/abc")
    monkeypatch.setenv("DASHBOARD_URL", "https://console.example/")
    out = channels.send("page", "Beacon - Alarm", "db unreachable", incident_id="inc-1")
    assert out == {"webhook": True}
    url, body = posted[0]
    assert url == "https://hooks.example/abc"
    assert body["blocks"][0]["type"] == "header" and "Beacon - Alarm" in body["text"]
    button = body["blocks"][2]["elements"][0]
    assert button["url"] == "https://console.example/#board/inc-1"
    assert button["style"] == "primary"


def test_pagerduty_triggers_on_page_and_resolves_with_the_same_dedup_key(
    monkeypatch: Any, posted: Any
) -> None:
    monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "rk")
    monkeypatch.setenv("DASHBOARD_URL", "https://console.example")
    channels.send("page", "Beacon - Alarm", "db unreachable", incident_id="inc-1")
    channels.send("resolved", "Beacon - Resolved", "verified", incident_id="inc-1")
    trig, res = posted[0][1], posted[1][1]
    assert (
        trig["event_action"] == "trigger" and trig["payload"]["severity"] == "critical"
    )
    assert trig["dedup_key"] == res["dedup_key"] == "beacon:inc-1"
    assert res["event_action"] == "resolve" and "payload" not in res
    assert trig["links"][0]["href"] == "https://console.example/#board/inc-1"


def test_contract_events_do_not_page_pagerduty(monkeypatch: Any, posted: Any) -> None:
    monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "rk")
    assert channels.send("contract", "not woken", "handled", incident_id="i") == {}
    assert posted == []


def test_a_failing_channel_never_raises(monkeypatch: Any) -> None:
    monkeypatch.setenv("WEBHOOK_URL", "https://hooks.example/abc")

    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("down")

    monkeypatch.setattr(channels.urllib.request, "urlopen", boom)
    assert channels.send("page", "t", "x", incident_id="i") == {"webhook": False}
    _ = io  # keep the import list honest
