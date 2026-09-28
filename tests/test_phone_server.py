"""The webhook an inbound call hits.

An outbound call carries its incident: Beacon rang about a particular thing. An
inbound call carries nothing, so the number has to decide what the caller means —
and the case that matters is the engineer who was paged, went back to sleep, and
calls back into a different fault an hour later.
"""

from __future__ import annotations

import json
from typing import Any

from beacon.phone.server import PhoneService


class FakeConnection:
    """websockets' connection object, as far as `process_request` uses it."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.body = ""

    def respond(self, status: int, body: str) -> tuple[int, str]:
        self.status, self.body = status, body
        return status, body


class FakeRequest:
    def __init__(self, path: str) -> None:
        self.path = path


def service(**kw: Any) -> PhoneService:
    return PhoneService(
        base_url="http://voice.invalid",
        passcode="p",
        public_url="https://beacon.example",
        **kw,
    )


def twiml_for(svc: PhoneService) -> str:
    conn = FakeConnection()
    svc.process_request(conn, FakeRequest("/twiml"))
    assert conn.status == 200
    return conn.body


def test_the_media_path_is_left_alone_so_the_socket_can_upgrade() -> None:
    conn = FakeConnection()
    assert service().process_request(conn, FakeRequest("/media")) is None
    assert conn.status is None


def test_the_webhook_hands_back_a_wss_stream_on_the_public_host() -> None:
    """Twilio requires wss; an http public URL would produce a stream it refuses."""
    body = twiml_for(service(default_incident="inc-1"))
    assert 'url="wss://beacon.example/media"' in body
    assert 'name="incident_id" value="inc-1"' in body


def test_a_pinned_incident_is_used_without_asking_anybody() -> None:
    svc = service(default_incident="inc-1", dashboard_url="http://dash.invalid")
    # no network: a pinned incident must not trigger a lookup
    assert svc.live_incident() == "inc-1"


def test_an_inbound_call_reaches_whatever_still_needs_a_human(monkeypatch: Any) -> None:
    svc = service(dashboard_url="http://dash.invalid")
    monkeypatch.setattr(
        svc,
        "live_incident",
        lambda: "inc-live",
    )
    assert 'value="inc-live"' in twiml_for(svc)


def test_the_newest_unresolved_incident_wins_over_a_newer_resolved_one(
    monkeypatch: Any,
) -> None:
    from beacon.phone import server

    listing = {
        "incidents": [
            {"incident_id": "inc-done", "status": "resolved"},
            {"incident_id": "inc-open", "status": "awaiting_engineer"},
            {"incident_id": "inc-older", "status": "awaiting_approval"},
        ]
    }

    class Resp:
        def read(self) -> bytes:
            return json.dumps(listing).encode()

        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(server.urllib.request, "urlopen", lambda *a, **k: Resp())
    assert service(dashboard_url="http://dash.invalid").live_incident() == "inc-open"


def test_a_quiet_night_says_so_instead_of_opening_a_silent_line(
    monkeypatch: Any,
) -> None:
    """A caller who hears nothing blames us; a caller who hears a sentence does not."""
    from beacon.phone import server

    monkeypatch.setattr(
        server.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("dashboard is down")),
    )
    body = twiml_for(service(dashboard_url="http://dash.invalid"))
    assert "<Say>" in body and "no open incident" in body
    assert "<Stream" not in body


def test_health_reports_how_many_calls_have_been_taken() -> None:
    conn = FakeConnection()
    svc = service()
    svc.calls.append({"incident_id": "inc-1"})
    svc.process_request(conn, FakeRequest("/health"))
    assert json.loads(conn.body) == {"ok": True, "calls": 1}


def test_anything_else_is_a_404_not_a_stream() -> None:
    conn = FakeConnection()
    service().process_request(conn, FakeRequest("/wp-login.php"))
    assert conn.status == 404
