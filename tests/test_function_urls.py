from __future__ import annotations

import json
from typing import Any


def url_event(
    method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    """Minimal Lambda Function URL (API Gateway v2 payload) event."""
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"content-type": "application/json", **(headers or {})},
        "requestContext": {
            "http": {"method": method, "path": path, "sourceIp": "1.2.3.4"},
            "requestId": "r1",
            "stage": "$default",
        },
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
    }


def test_voice_turn_health_route_answers() -> None:
    from beacon.voice_turn import handler

    resp = handler(url_event("GET", "/health"), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["service"] == "beacon-voice-turn"


def test_voice_turn_warm_ping_short_circuits() -> None:
    from beacon.voice_turn import handler

    assert handler({"mode": "warm"}, None) == {"ok": True, "warm": True}


def test_voice_turn_unknown_route_is_404() -> None:
    from beacon.voice_turn import handler

    assert handler(url_event("GET", "/nope"), None)["statusCode"] == 404


def test_dashboard_health_route_answers() -> None:
    from beacon.dashboard_api import handler

    resp = handler(url_event("GET", "/health"), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["service"] == "beacon-dashboard"
