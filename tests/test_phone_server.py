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


def service(**kw: Any) -> PhoneService:
    return PhoneService(
        base_url="http://voice.invalid",
        passcode="p",
        public_url="https://beacon.example",
        **kw,
    )


def twiml_for(svc: PhoneService, host: str = "", proto: str = "https") -> str:
    return svc.twiml_document(host, proto)


def test_the_webhook_hands_back_a_wss_stream_on_the_public_host() -> None:
    """Twilio requires wss; an http public URL would produce a stream it refuses."""
    body = twiml_for(service(default_incident="inc-1"))
    assert 'url="wss://beacon.example/media"' in body
    assert 'name="incident_id" value="inc-1"' in body


def test_the_stream_host_comes_from_the_request_the_carrier_actually_made() -> None:
    """A tunnel gets a new hostname on every restart; being told one at startup
    fails silently — the call connects and the stream goes nowhere."""
    svc = service(default_incident="inc-1")
    body = twiml_for(svc, host="abc-def.trycloudflare.com")
    assert 'url="wss://abc-def.trycloudflare.com/media"' in body
    assert "beacon.example" not in body


def test_a_plain_http_forwarder_gets_ws_not_wss() -> None:
    svc = service(default_incident="inc-1")
    assert 'url="ws://localhost:8080/media"' in twiml_for(
        svc, host="localhost:8080", proto="http"
    )


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


def test_the_routes_the_carrier_needs_are_all_served_on_one_port() -> None:
    """Twilio fetches the TwiML with POST, so the webhook must accept any method."""
    import inspect

    from beacon.phone import server as mod

    source = inspect.getsource(mod.serve)
    assert 'add_route("*", twilio.TWIML_PATH' in source, "the webhook must take POST"
    assert 'request.method == "POST"' in source, "the form body must be read"
    assert "add_get(twilio.MEDIA_PATH" in source
