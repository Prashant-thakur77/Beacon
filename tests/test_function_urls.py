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


def test_the_brief_route_refuses_without_the_passcode() -> None:
    from beacon.voice_turn import handler

    assert handler(url_event("GET", "/brief/inc-1"), None)["statusCode"] == 401


def test_the_brief_route_serves_one_brief_to_every_channel(monkeypatch: Any) -> None:
    """The phone bridge has no copy of the prompt; this route is where it gets one."""
    from beacon import store, voice_turn

    incident = {
        "incident_id": "inc-1",
        "alarm_name": "payments-errors",
        "status": "awaiting_approval",
        "rca_json": {
            "spoken_summary": "A rule on sg-0abc is gone. Payments are failing."
        },
    }
    monkeypatch.setenv("PASSCODE", "open-sesame")
    monkeypatch.setattr(store, "get_incident", lambda *_a, **_k: dict(incident))

    def brief(channel: str = "") -> dict[str, Any]:
        event = url_event(
            "GET", "/brief/inc-1", headers={"x-beacon-passcode": "open-sesame"}
        )
        if channel:
            event["rawQueryString"] = f"channel={channel}"
            event["queryStringParameters"] = {"channel": channel}
        resp = voice_turn.handler(event, None)
        assert resp["statusCode"] == 200, resp["body"]
        return dict(json.loads(resp["body"]))

    browser = brief()
    phone = brief("phone")
    assert browser["tools"] == phone["tools"]
    assert "approve fix one" in phone["keyterms"]
    assert "telephone call" in phone["system_prompt"]
    assert "telephone call" not in browser["system_prompt"]
    assert phone["greeting"].startswith("This is Beacon calling")


def test_the_brief_route_404s_for_an_incident_that_does_not_exist(
    monkeypatch: Any,
) -> None:
    from beacon import store, voice_turn

    monkeypatch.setenv("PASSCODE", "open-sesame")
    monkeypatch.setattr(store, "get_incident", lambda *_a, **_k: {})
    event = url_event(
        "GET", "/brief/inc-nope", headers={"x-beacon-passcode": "open-sesame"}
    )
    assert voice_turn.handler(event, None)["statusCode"] == 404


def test_the_attest_route_refuses_without_the_passcode() -> None:
    from beacon.voice_turn import handler

    event = url_event("POST", "/sessions/sess_" + "a" * 32 + "/attest", {})
    assert handler(event, None)["statusCode"] == 401


def test_the_attest_route_says_so_when_no_phrase_was_ever_recorded(
    monkeypatch: Any,
) -> None:
    """An incident nobody approved by voice has nothing to re-check."""
    from beacon import store, voice_turn

    monkeypatch.setenv("PASSCODE", "open-sesame")
    monkeypatch.setattr(
        store,
        "get_incident",
        lambda *_a, **_k: {"incident_id": "inc-1", "timeline": []},
    )
    event = url_event(
        "POST",
        "/sessions/sess_" + "a" * 32 + "/attest",
        {"incident_id": "inc-1"},
        headers={"x-beacon-passcode": "open-sesame"},
    )
    body = json.loads(voice_turn.handler(event, None)["body"])
    assert body["ok"] and body["checks"] == []


def test_the_attest_route_flags_an_approval_that_was_heard_poorly(
    monkeypatch: Any,
) -> None:
    """The gap this closes: a live approval is applied with no confidence at all."""
    from beacon import aai, store, voice_turn

    monkeypatch.setenv("PASSCODE", "open-sesame")
    incident = {
        "incident_id": "inc-1",
        "status": "resolved",
        "alarm_name": "payments-errors",
        "timeline": [
            {
                "event": "approved",
                "detail": {
                    "fix_id": 1,
                    "channel": "assemblyai",
                    "transcript_quote": "approve fix one",
                    "approval_id": "a-1",
                },
            },
        ],
    }
    monkeypatch.setattr(store, "get_incident", lambda *_a, **_k: dict(incident))
    saved: dict[str, Any] = {}
    monkeypatch.setattr(
        store,
        "update_status",
        lambda *_a, **kw: saved.update(kw.get("extra") or {}),
    )
    monkeypatch.setattr(
        aai,
        "audit_session",
        lambda *_a, **_k: {
            "words": [
                {"text": "approve", "confidence": 0.95, "start": 0},
                {"text": "fix", "confidence": 0.93, "start": 400},
                {"text": "one", "confidence": 0.61, "start": 800},
            ],
            "transcript_id": "t-1",
        },
    )
    event = url_event(
        "POST",
        "/sessions/sess_" + "b" * 32 + "/attest",
        {"incident_id": "inc-1"},
        headers={"x-beacon-passcode": "open-sesame"},
    )
    body = json.loads(voice_turn.handler(event, None)["body"])
    assert not body["ok"]
    assert "61%" in body["problems"][0]
    # and it is kept on the incident, not just returned
    assert saved["voice_attestation"]["sess_" + "b" * 32]["checks"][0]["ok"] is False


def test_every_consent_tool_puts_its_phrase_where_attestation_looks_for_it() -> None:
    """The route read ``detail.quote``; three of the four writers use another key.

    That mismatch is invisible in a hand-written fixture, so this test reads the
    event name and the key out of each consent tool itself. On the live account the
    bug meant the attest button answered "no consent phrase was recorded" for every
    voice approval ever made -- while the audit row beside it quoted the words.
    """
    import ast
    from pathlib import Path

    from beacon.voice_turn import _CONSENT_TOOLS, consent_phrases

    tree = ast.parse(Path("src/beacon/voice_tools.py").read_text(encoding="utf-8"))
    wrote: dict[str, tuple[str, str]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name not in _CONSENT_TOOLS:
            continue
        for call in ast.walk(fn):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "append_timeline"
            ):
                continue
            names = [
                n.value
                for n in ast.walk(call.args[1])
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            detail = next((k for k in call.keywords if k.arg == "detail"), None)
            keys = (
                [
                    k.value
                    for k in detail.value.keys  # type: ignore[union-attr]
                    if isinstance(k, ast.Constant)
                ]
                if detail
                else []
            )
            key = next((k for k in ("transcript_quote", "quote") if k in keys), "")
            if names and key:
                wrote[fn.name] = (names[0], key)

    missing = set(_CONSENT_TOOLS) - set(wrote)
    assert not missing, f"no timeline phrase found for {sorted(missing)}"

    for tool, (event, key) in sorted(wrote.items()):
        phrases = consent_phrases(
            {"timeline": [{"event": event, "detail": {key: "approve fix one"}}]}
        )
        assert [(p["quote"], p["event"]) for p in phrases] == [
            ("approve fix one", event)
        ], (
            f"{tool} records its phrase as {event}.detail.{key} "
            "and attestation cannot see it"
        )


def test_the_voice_agent_is_only_credited_when_a_recording_could_prove_it() -> None:
    """ "assemblyai-voice-agent" on a certificate is a claim, not a transport label.

    The console sends channel "assemblyai" for every tool call in that backend, and
    falls back to its own local session id before AssemblyAI has issued one. Naming
    the Voice Agent in both cases put a claim on the certificate that no recording
    could support.
    """
    from beacon.voice_turn import _transcriber

    sess = "sess_" + "a" * 32
    assert _transcriber("assemblyai", sess) == "assemblyai-voice-agent"
    assert _transcriber("assemblyai", "d-izo4r0qs") == "assemblyai"
    assert _transcriber("typed", sess) == "typed"
    assert _transcriber("telegram", "") == "telegram"


def test_a_typed_phrase_is_not_reported_as_a_mishearing(monkeypatch: Any) -> None:
    """Typed words are not in the session's audio, and that is not a bad hearing.

    Before this, the console's typed line rode in as channel "assemblyai" with a
    confidence of 1, so the certificate read "heard at 100%, before the change was
    applied" for words nobody ever said.
    """
    from beacon import aai, store, voice_turn

    monkeypatch.setenv("PASSCODE", "open-sesame")
    incident = {
        "incident_id": "inc-1",
        "status": "resolved",
        "timeline": [
            {
                "event": "approved",
                "detail": {"transcript_quote": "approve fix 1", "channel": "typed"},
            }
        ],
    }
    monkeypatch.setattr(store, "get_incident", lambda *_a, **_k: dict(incident))
    called = []
    monkeypatch.setattr(aai, "audit_session", lambda *a, **k: called.append(a) or {})
    event = url_event(
        "POST",
        "/sessions/sess_" + "c" * 32 + "/attest",
        {"incident_id": "inc-1"},
        headers={"x-beacon-passcode": "open-sesame"},
    )
    body = json.loads(voice_turn.handler(event, None)["body"])
    assert body["ok"] and body["problems"] == []
    assert body["typed"][0]["quote"] == "approve fix 1"
    assert "typed, not spoken" in body["note"]
    assert not called, "it should not pay for a transcript it cannot use"
