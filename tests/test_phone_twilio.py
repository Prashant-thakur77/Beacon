"""The carrier adapter: Twilio's media protocol, without Twilio.

The socket is faked, so the parts that matter are checkable: the TwiML asks for a
bidirectional stream (a one-way stream cannot answer the caller), keypresses arrive
as keypresses rather than audio, and barge-in tells the carrier to drop what it has
already buffered.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from beacon.phone.providers import twilio


class FakeWs:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            for event in self.events:
                yield json.dumps(event)

        return gen()


START = {
    "event": "start",
    "start": {
        "streamSid": "MZ123",
        "callSid": "CA456",
        "customParameters": {"incident_id": "inc-9", "from": "+919876543210"},
    },
}


def collect(leg: twilio.TwilioLeg) -> list[bytes]:
    async def run() -> list[bytes]:
        return [frame async for frame in leg.inbound()]

    return asyncio.run(run())


def test_the_twiml_opens_a_two_way_stream_and_names_the_incident() -> None:
    xml = twilio.twiml("wss://beacon.example/media", incident_id="inc-9")
    # <Connect> is bidirectional; <Start> would only copy the caller to us
    assert "<Connect>" in xml and "<Start>" not in xml
    assert 'url="wss://beacon.example/media"' in xml
    assert 'name="incident_id" value="inc-9"' in xml


def test_the_start_event_names_the_call_before_any_audio_is_sent() -> None:
    leg = twilio.TwilioLeg(FakeWs([START]))
    collect(leg)
    assert leg.stream_sid == "MZ123"
    assert leg.call_sid == "CA456"
    assert leg.caller == "+919876543210"
    assert leg.params["incident_id"] == "inc-9"


def test_audio_arrives_as_mu_law_and_keypresses_arrive_as_keypresses() -> None:
    payload = base64.b64encode(b"\xff" * 160).decode()
    leg = twilio.TwilioLeg(
        FakeWs(
            [
                {"event": "connected"},
                START,
                {"event": "media", "media": {"payload": payload}},
                {"event": "dtmf", "dtmf": {"digit": "1"}},
                {"event": "stop"},
                {"event": "media", "media": {"payload": payload}},
            ]
        )
    )
    frames = collect(leg)
    assert frames == [b"\xff" * 160, b"DTMF:1"], "stop must end the call"


def test_playing_audio_addresses_the_stream_the_carrier_named() -> None:
    ws = FakeWs([START])
    leg = twilio.TwilioLeg(ws)
    collect(leg)
    asyncio.run(leg.play(b"\x7f" * 160))
    assert ws.sent[-1]["event"] == "media"
    assert ws.sent[-1]["streamSid"] == "MZ123"
    assert base64.b64decode(ws.sent[-1]["media"]["payload"]) == b"\x7f" * 160


def test_barge_in_tells_the_carrier_to_drop_what_it_has_buffered() -> None:
    """Twilio queues what we sent; without `clear` the agent keeps talking."""
    ws = FakeWs([START])
    leg = twilio.TwilioLeg(ws)
    collect(leg)
    asyncio.run(leg.clear())
    assert ws.sent[-1] == {"event": "clear", "streamSid": "MZ123"}


def test_nothing_is_played_before_the_stream_is_named() -> None:
    ws = FakeWs([])
    asyncio.run(twilio.TwilioLeg(ws).play(b"\x7f" * 160))
    assert ws.sent == []


def test_placing_a_call_needs_credentials_and_says_which_ones(
    monkeypatch: Any,
) -> None:
    import pytest

    for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="TWILIO_ACCOUNT_SID"):
        twilio.place_call("+911234567890", "https://beacon.example/twiml")
