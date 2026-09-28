"""Twilio Media Streams: a real phone number, bidirectional audio over a WebSocket.

Twilio is the media path because it will stream a live call in *both* directions
over a socket, which is what a voice agent needs and what Amazon Connect does not
offer (see ``beacon.phone.bridge``). Nothing here needs the Twilio SDK: the media
protocol is JSON over a WebSocket, and placing a call is one form post.

The protocol, as Twilio sends it:

    connected                                  -- socket accepted
    start   {streamSid, callSid, customParameters}
    media   {payload: base64 8 kHz mu-law, 20 ms}
    dtmf    {digit}
    stop

and as we answer it:

    media   {streamSid, media: {payload}}      -- audio to the caller
    clear   {streamSid}                        -- drop what is queued: barge-in
    mark    {streamSid, mark: {name}}          -- playback checkpoint

One process serves two things on one port: ``GET /twiml`` returns the TwiML that
tells Twilio to open the stream, and ``/media`` is the stream itself. Behind a
TLS terminator, because Twilio requires ``wss://``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

TWIML_PATH = "/twiml"
MEDIA_PATH = "/media"
API = "https://api.twilio.com/2010-04-01"


def twiml(stream_url: str, *, incident_id: str = "", say: str = "") -> str:
    """Tell Twilio to hand us the call's audio, in both directions.

    ``<Connect><Stream>`` is the bidirectional form; ``<Start><Stream>`` would only
    give us a copy of the caller, which cannot answer them.

    ``say`` speaks one line before the stream opens. It costs a second and earns
    two things: the caller knows the line is alive while the socket and the agent
    session come up, and when something is wrong it separates "Twilio never ran our
    TwiML" from "Twilio ran it and would not open the stream" — which otherwise look
    identical from here, because neither reaches our log.
    """
    params = (
        f'\n      <Parameter name="incident_id" value="{incident_id}" />'
        if incident_id
        else ""
    )
    spoken = f"  <Say>{say}</Say>\n" if say else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        f"{spoken}"
        "  <Connect>\n"
        f'    <Stream url="{stream_url}">{params}\n'
        "    </Stream>\n"
        "  </Connect>\n"
        "</Response>\n"
    )


class TwilioLeg:
    """A ``Leg`` over one Twilio Media Stream socket."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.caller = ""
        self.call_sid = ""
        self.stream_sid = ""
        self.params: dict[str, str] = {}
        self._ready = asyncio.Event()
        self._closed = False

    async def wait_for_start(self, timeout: float = 10.0) -> None:
        """Block until ``start`` has named the stream, so ``play`` can address it."""
        await asyncio.wait_for(self._ready.wait(), timeout)

    async def inbound(self) -> AsyncIterator[bytes]:
        async for raw in self.ws:
            msg = json.loads(raw)
            event = msg.get("event")
            if event == "start":
                start = msg.get("start") or {}
                self.stream_sid = str(start.get("streamSid") or "")
                self.call_sid = str(start.get("callSid") or "")
                self.params = dict(start.get("customParameters") or {})
                self.caller = str(self.params.get("from") or self.call_sid or "caller")
                self._ready.set()
                logger.info("call %s streaming as %s", self.call_sid, self.stream_sid)
            elif event == "media":
                payload = ((msg.get("media") or {}).get("payload")) or ""
                if payload:
                    yield base64.b64decode(payload)
            elif event == "dtmf":
                digit = str((msg.get("dtmf") or {}).get("digit") or "")
                if digit:
                    yield b"DTMF:" + digit.encode()
            elif event == "stop":
                logger.info("call %s stopped", self.call_sid)
                return

    async def play(self, ulaw: bytes) -> None:
        if self._closed or not self.stream_sid:
            return
        await self.ws.send(
            json.dumps(
                {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": base64.b64encode(ulaw).decode()},
                }
            )
        )

    async def clear(self) -> None:
        """Barge-in: Twilio buffers what we sent, so it has to be told to drop it."""
        if self.stream_sid and not self._closed:
            await self.ws.send(
                json.dumps({"event": "clear", "streamSid": self.stream_sid})
            )

    async def hangup(self) -> None:
        self._closed = True
        try:
            await self.ws.close()
        except Exception:  # the socket may already be gone
            logger.debug("close on an already-closed stream", exc_info=True)


# -- placing the call ------------------------------------------------------


def _auth() -> tuple[str, str, str]:
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    number = os.environ.get("TWILIO_FROM_NUMBER", "")
    if not (sid and token and number):
        raise RuntimeError(
            "set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER"
        )
    return sid, token, number


def place_call(to: str, twiml_url: str) -> str:
    """Dial *to* and point the call at our TwiML. Returns the call SID.

    This is the outbound path -- Beacon waking the engineer. The inbound path needs
    no code at all: point the number's voice webhook at the same TwiML URL and the
    engineer can simply call Beacon back.
    """
    sid, token, number = _auth()
    # No `Method` here on purpose. Twilio fetches the TwiML with POST, which is
    # fine — `beacon.phone.server` serves the webhook with aiohttp and reads the
    # form body. Asking for GET would be refused anyway: a trial account may not
    # set that parameter ("trial accounts have limited parameter access").
    body = urllib.parse.urlencode({"To": to, "From": number, "Url": twiml_url}).encode()
    req = urllib.request.Request(
        f"{API}/Accounts/{sid}/Calls.json", data=body, method="POST"
    )
    basic = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req.add_header("Authorization", f"Basic {basic}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            return str(json.loads(resp.read().decode()).get("sid") or "")
    except urllib.error.HTTPError as exc:
        # Twilio says exactly what is wrong; a traceback says nothing useful.
        detail = json.loads(exc.read().decode() or "{}")
        raise RuntimeError(
            f"Twilio refused the call ({exc.code}): "
            f"{detail.get('message') or 'no reason given'}"
            + (f" — {detail.get('more_info')}" if detail.get("more_info") else "")
        ) from None
