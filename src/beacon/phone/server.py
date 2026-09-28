"""The always-on half of the phone channel.

One process, one port, two jobs: answer Twilio's webhook with the TwiML that opens
a media stream, and be the other end of that stream. Each accepted call gets a
``PhoneBridge`` against a fresh AssemblyAI session, and when it ends the recording
is transcribed per channel so the approval can be attributed to the human who gave
it.

    python -m beacon.phone serve --base-url https://<voice-fn-url> --port 8080

Run it behind TLS (Twilio requires ``wss://``); in production that is a Fargate
task behind an ALB, and during a demo it is this process plus a tunnel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from beacon.phone import attest, tools
from beacon.phone.bridge import PhoneBridge
from beacon.phone.providers import twilio

logger = logging.getLogger(__name__)


class PhoneService:
    """Accepts calls and runs one bridge per call."""

    def __init__(
        self,
        *,
        base_url: str,
        passcode: str,
        public_url: str,
        default_incident: str = "",
        recordings_dir: str = "",
    ) -> None:
        self.base_url = base_url
        self.passcode = passcode
        self.public_url = public_url.rstrip("/")
        self.default_incident = default_incident
        self.recordings_dir = recordings_dir
        self.calls: list[dict[str, Any]] = []

    # -- the webhook -------------------------------------------------------

    def process_request(self, connection: Any, request: Any) -> Any:
        """Answer plain HTTP on the same port; let the media path upgrade."""
        path = request.path.split("?")[0]
        if path == twilio.MEDIA_PATH:
            return None  # proceed with the WebSocket handshake
        if path == twilio.TWIML_PATH:
            stream = self.public_url.replace("https://", "wss://").replace(
                "http://", "ws://"
            )
            body = twilio.twiml(
                f"{stream}{twilio.MEDIA_PATH}", incident_id=self.default_incident
            ).encode()
            return connection.respond(200, body.decode())
        if path == "/health":
            return connection.respond(
                200, json.dumps({"ok": True, "calls": len(self.calls)})
            )
        return connection.respond(404, "not found\n")

    # -- one call ----------------------------------------------------------

    async def handle(self, ws: Any) -> None:
        leg = twilio.TwilioLeg(ws)
        pump = asyncio.create_task(self._drain_until_start(leg))
        try:
            await leg.wait_for_start()
        except TimeoutError:
            logger.warning("a stream opened but never started")
            pump.cancel()
            return
        finally:
            pump.cancel()
        incident_id = leg.params.get("incident_id") or self.default_incident
        if not incident_id:
            logger.error("no incident for call %s; hanging up", leg.call_sid)
            await leg.hangup()
            return
        await self.run_call(leg, incident_id)

    async def _drain_until_start(self, leg: twilio.TwilioLeg) -> None:
        """``start`` arrives on the same stream as the audio, so it has to be read."""
        async for _ in leg.inbound():
            return

    async def run_call(self, leg: Any, incident_id: str) -> dict[str, Any]:
        brief = await asyncio.to_thread(
            tools.fetch_brief,
            self.base_url,
            passcode=self.passcode,
            incident_id=incident_id,
            channel="phone",
        )
        token = await asyncio.to_thread(
            tools.mint_token, self.base_url, passcode=self.passcode
        )
        session_id = f"phone-{getattr(leg, 'call_sid', '') or os.urandom(4).hex()}"
        bridge = PhoneBridge(
            leg,
            brief=brief,
            token=token,
            run_tool=tools.http_runner(
                self.base_url,
                passcode=self.passcode,
                incident_id=incident_id,
                session_id=session_id,
            ),
            on_event=lambda kind, data: logger.info("%s %s", kind, data),
        )
        record = await bridge.run()
        out = await self._close_out(bridge, record, incident_id)
        self.calls.append(out)
        return out

    async def _close_out(
        self, bridge: PhoneBridge, record: Any, incident_id: str
    ) -> dict[str, Any]:
        """Keep the recording, and check who authorised what."""
        out: dict[str, Any] = {
            "incident_id": incident_id,
            "caller": record.caller,
            "seconds": record.seconds(),
            "turns": len(record.turns),
            "tools": [t["name"] for t in record.tools],
            "interruptions": record.interruptions,
            "dtmf": record.dtmf,
            "first_audio_ms": record.first_audio_ms,
            "ended": record.ended_reason,
            "aai_session_id": record.aai_session_id,
        }
        if bridge.recorder is None:
            return out
        wav = bridge.recorder.wav()
        if self.recordings_dir:
            import pathlib

            path = (
                pathlib.Path(self.recordings_dir)
                / f"{incident_id}-{int(record.started_at)}.wav"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(wav)
            out["recording"] = str(path)
        try:
            out["attestation"] = await asyncio.to_thread(
                attest_call, wav, [t["name"] for t in record.tools], bridge.brief
            )
        except Exception as exc:
            logger.warning("could not attribute the call: %s", exc)
            out["attestation"] = {"ok": None, "error": str(exc)}
        return out


def attest_call(
    wav: bytes, executed: list[str], brief: dict[str, Any]
) -> dict[str, Any]:
    """Upload the two-channel recording, transcribe per channel, attribute consent."""
    from beacon import aai

    url = aai.upload(wav)
    transcript = aai.transcribe_call(url, keyterms=brief.get("keyterms") or [])
    verdict = attest.verify(transcript["utterances"], executed)
    verdict["transcript_id"] = transcript.get("transcript_id")
    verdict["seconds"] = transcript.get("seconds")
    return verdict


async def serve(
    service: PhoneService, *, host: str = "0.0.0.0", port: int = 8080
) -> None:
    from websockets.asyncio.server import serve as ws_serve

    async with ws_serve(
        service.handle,
        host,
        port,
        process_request=service.process_request,
        ping_interval=20,
    ):
        logger.info(
            "phone bridge on %s:%s  twiml=%s%s",
            host,
            port,
            service.public_url,
            twilio.TWIML_PATH,
        )
        await asyncio.Future()
