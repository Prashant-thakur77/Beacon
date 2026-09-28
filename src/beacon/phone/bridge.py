"""The phone leg: a real telephone call answered by the AssemblyAI Voice Agent.

Beacon's browser console holds a socket to ``wss://agents.assemblyai.com/v1/ws``
and forwards every ``tool.call`` to the voice Lambda. This module does exactly the
same thing with a telephone call on the other side instead of a laptop microphone,
so an engineer with no laptop -- in bed, in a taxi, on a train -- reaches the same
agent, the same nine tools and the same consent rules.

Why a separate process rather than another Lambda: a call is a stateful,
full-duplex, minutes-long stream. Lambda bills for wall-clock time and cannot hold
a socket open to two peers at once. So the bridge is a small always-on service
(Fargate, or a laptop during a demo) and the Lambda stays the place where a tool
is allowed to run.

Why Amazon Connect is not the media path: Connect can stream a caller's audio *out*
to Kinesis Video Streams, but it cannot stream arbitrary audio *in* -- a contact
flow can only play a Polly prompt or an S3 file. Full duplex with an external
agent therefore needs a SIP media leg (Chime SDK Voice Connector) or a carrier that
offers bidirectional media over a WebSocket. Connect keeps the job it is good at in
``beacon.caller``: placing the outbound call that wakes the human.

Two things are deliberately *not* delegated to the agent:

* **A keypress is never consent.** DTMF can acknowledge, re-brief or hang up. It can
  never approve a change, because the approval artifact has to be words in a
  recording, and ``1`` is not a sentence anybody can be held to.
* **The caller's audio is the only consent channel.** Only the caller's microphone
  is ever fed to ``input.audio``, so the transcript the consent check reads cannot
  contain the agent reading a phrase back to itself. The stereo recording proves it:
  the caller is on the left channel, the agent on the right.
"""

from __future__ import annotations

import array
import asyncio
import base64
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from beacon.phone import codec

logger = logging.getLogger(__name__)

WS_URL = "wss://agents.assemblyai.com/v1/ws"
VOICE = "jane"
# The Voice Agent API speaks G.711 mu-law natively -- it offers audio/pcm,
# audio/pcma and audio/pcmu -- and a phone line is mu-law already. So the call's
# audio is forwarded in both directions untouched: no resampling, no transcoding,
# and none of the quality a trip through 24 kHz and back would cost. Asked directly
# on 28 Sep 2026; 'pcm_mulaw' (which the streaming STT API uses) is refused here.
PHONE_ENCODING = "audio/pcmu"
# A tool.result may only be sent once the reply that asked for it is done, which is
# the same rule the browser transport follows.
_TOOL_TIMEOUT = 60
# Consent tools need a clean transcript; a phone line is noisier than a laptop mic,
# so the bar is the same but the agent is told to repeat the phrase slowly.
CONSENT_TOOLS = ("approve_fix", "grant_sleep_contract", "undo_fix", "open_fix_pr")


class Leg(Protocol):
    """One telephone call, in 8 kHz mu-law frames.

    Implemented by a carrier adapter (``providers.twilio``) and by the replay
    adapter (``providers.replay``) that makes the whole path testable with no
    carrier, no phone number and no account.
    """

    caller: str

    def inbound(self) -> AsyncIterator[bytes]:
        """mu-law frames from the caller, until the call ends."""
        ...

    async def play(self, ulaw: bytes) -> None:
        """Send mu-law audio to the caller."""
        ...

    async def clear(self) -> None:
        """Drop whatever is still queued for the caller -- barge-in."""
        ...

    async def hangup(self) -> None: ...


ToolRunner = Callable[
    [str, dict[str, Any], str, float | None], Awaitable[dict[str, Any]]
]
"""``(tool_name, args, transcript, confidence) -> the voice Lambda's JSON``."""


@dataclass
class CallRecord:
    """What the call did, kept for the audit page and the postmortem."""

    started_at: float = field(default_factory=time.time)
    caller: str = ""
    incident_id: str = ""
    aai_session_id: str = ""
    turns: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    dtmf: list[str] = field(default_factory=list)
    interruptions: int = 0
    # Turn end to the agent's first audio, once per turn. A turn that calls a tool
    # includes the tool's round trip, so one number for a whole call would be a
    # different measurement each time; the spread is the honest report.
    ttfa_ms: list[float] = field(default_factory=list)
    ended_reason: str = ""

    @property
    def first_audio_ms(self) -> float | None:
        return min(self.ttfa_ms) if self.ttfa_ms else None

    def seconds(self) -> float:
        return round(time.time() - self.started_at, 1)


class StereoRecorder:
    """The call as two separate channels: caller left, agent right.

    Single-channel call recordings are ambiguous about who said a phrase, and the
    one phrase that matters is the one that authorised a change to production. Two
    channels remove the ambiguity from the artifact itself, and AssemblyAI's
    ``dual_channel`` transcription then labels every utterance by channel without
    guessing at speakers.
    """

    def __init__(self) -> None:
        self.caller = array.array("h")
        self.agent = array.array("h")
        self._t0 = time.monotonic()

    def add_caller(self, ulaw: bytes) -> None:
        self._write(self.caller, ulaw)

    def add_agent(self, ulaw: bytes) -> None:
        self._write(self.agent, ulaw)

    def _write(self, channel: array.array[int], ulaw: bytes) -> None:
        """Place audio where it belongs on the call's clock, not where it arrived.

        The caller's frames arrive every 20 ms because a carrier sends them that way.
        The agent's arrive in bursts -- the API generates a sentence faster than it
        takes to say it -- and a jitter buffer plays them out at speaking speed. So
        each channel is written at whichever is later: the wall clock, or the end of
        what is already on that channel. Appending on arrival instead produced a
        recording half again as long as the call, with the agent running ahead of the
        person it was talking to.
        """
        now = int((time.monotonic() - self._t0) * codec.PHONE_RATE)
        gap = max(now, len(channel)) - len(channel)
        if gap > 0:
            channel.extend(array.array("h", bytes(2 * gap)))
        channel.extend(codec.ulaw_to_pcm(ulaw))

    def _align(self) -> None:
        """Run both channels to the same length before writing the file."""
        longest = max(len(self.caller), len(self.agent))
        for channel in (self.caller, self.agent):
            if len(channel) < longest:
                channel.extend(array.array("h", bytes(2 * (longest - len(channel)))))

    def wav(self) -> bytes:
        """A 16-bit stereo WAV at 8 kHz -- what a carrier would have recorded."""
        import io
        import wave

        self._align()
        interleaved = array.array("h", bytes(4 * len(self.caller)))
        interleaved[0::2] = self.caller
        interleaved[1::2] = self.agent
        buf = io.BytesIO()
        with wave.open(buf, "wb") as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(codec.PHONE_RATE)
            out.writeframes(interleaved.tobytes())
        return buf.getvalue()

    def seconds(self) -> float:
        return round(max(len(self.caller), len(self.agent)) / codec.PHONE_RATE, 1)


class PhoneBridge:
    """Pumps one call between a telephony leg and one AssemblyAI agent session."""

    def __init__(
        self,
        leg: Leg,
        *,
        brief: dict[str, Any],
        token: str,
        run_tool: ToolRunner,
        record: bool = True,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.leg = leg
        self.brief = brief
        self.token = token
        self.run_tool = run_tool
        self.recorder = StereoRecorder() if record else None
        self.on_event = on_event or (lambda kind, data: None)
        self.call = CallRecord(
            caller=leg.caller, incident_id=str(brief.get("incident_id", ""))
        )
        self._ws: Any = None
        self._reply_open = False
        self._pending: list[dict[str, str]] = []
        self._transcript = ""
        self._confidence: float | None = None
        self._turn_ended_at: float | None = None
        self._done = asyncio.Event()

    # -- session setup ----------------------------------------------------

    def _session_update(self) -> str:
        from beacon import voice_tools

        return json.dumps(
            {
                "type": "session.update",
                "session": {
                    "system_prompt": self.brief["system_prompt"],
                    "greeting": self.brief.get("greeting", ""),
                    "input": {
                        "format": {"encoding": PHONE_ENCODING},
                        # A phone line is noisier than a laptop and callers pause
                        # mid-sentence when they are half awake, so turns close
                        # later than they do in the browser.
                        "turn_detection": {
                            "vad_threshold": 0.65,
                            "min_silence": 700,
                            "max_silence": 2200,
                        },
                        "language_codes": self.brief.get(
                            "language_codes", ["en", "hi"]
                        ),
                        **(
                            {"keyterms": self.brief["keyterms"]}
                            if self.brief.get("keyterms")
                            else {}
                        ),
                    },
                    "output": {"voice": VOICE, "format": {"encoding": PHONE_ENCODING}},
                    "tools": [
                        {
                            "type": "function",
                            "name": schema["name"],
                            "description": schema["description"],
                            "parameters": schema["parameters"],
                            "execution_mode": "interactive",
                            "timeout_seconds": _TOOL_TIMEOUT,
                        }
                        for schema in voice_tools.TOOL_SCHEMAS
                    ],
                },
            }
        )

    # -- the two pumps ----------------------------------------------------

    async def run(self) -> CallRecord:
        import websockets

        async with websockets.connect(f"{WS_URL}?token={self.token}") as ws:
            self._ws = ws
            await ws.send(self._session_update())
            caller = asyncio.create_task(self._pump_caller())
            agent = asyncio.create_task(self._pump_agent())
            try:
                await asyncio.wait({caller, agent}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (caller, agent):
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        if not self.call.ended_reason:
            self.call.ended_reason = "socket closed"
        self.on_event(
            "call.ended",
            {"reason": self.call.ended_reason, "seconds": self.call.seconds()},
        )
        return self.call

    async def _pump_caller(self) -> None:
        """Caller's mu-law -> 24 kHz PCM -> ``input.audio``."""
        async for frame in self.leg.inbound():
            if frame.startswith(b"DTMF:"):
                await self._dtmf(frame[5:].decode())
                continue
            if self.recorder:
                self.recorder.add_caller(frame)
            # Straight through: the carrier's frame is already what the API wants.
            await self._ws.send(
                json.dumps(
                    {
                        "type": "input.audio",
                        "audio": base64.b64encode(frame).decode(),
                    }
                )
            )
        self.call.ended_reason = self.call.ended_reason or "caller hung up"

    async def _pump_agent(self) -> None:
        async for raw in self._ws:
            msg = json.loads(raw)
            await self._on_message(msg)
            if self._done.is_set():
                return

    async def _on_message(self, msg: dict[str, Any]) -> None:
        kind = str(msg.get("type", ""))
        if kind == "session.ready":
            self.call.aai_session_id = str(msg.get("session_id") or "")
            self.on_event("session.ready", {"session_id": self.call.aai_session_id})

        elif kind == "input.speech.started":
            # The caller spoke over the agent: stop playing at once, so the line
            # behaves like a phone call and not like a voicemail.
            if self._reply_open:
                await self.leg.clear()
            self.on_event("caller.speaking", {})

        elif kind == "transcript.user":
            self._transcript = str(msg.get("transcript") or msg.get("text") or "")
            conf = msg.get("confidence")
            self._confidence = float(conf) if isinstance(conf, int | float) else None
            self._turn_ended_at = time.perf_counter()
            self.call.turns.append(
                {
                    "who": "caller",
                    "text": self._transcript,
                    "confidence": self._confidence,
                }
            )
            self.on_event(
                "heard", {"text": self._transcript, "confidence": self._confidence}
            )

        elif kind == "reply.started":
            self._reply_open = True

        elif kind == "reply.audio":
            data = msg.get("data") or msg.get("audio") or ""
            if data:
                ulaw = base64.b64decode(data)
                if self.recorder:
                    self.recorder.add_agent(ulaw)
                await self.leg.play(ulaw)
                if self._turn_ended_at is not None:
                    self.call.ttfa_ms.append(
                        round((time.perf_counter() - self._turn_ended_at) * 1000)
                    )
                    self._turn_ended_at = None

        elif kind == "transcript.agent":
            said = str(msg.get("transcript") or msg.get("text") or "")
            self.call.turns.append({"who": "agent", "text": said})
            self.on_event("said", {"text": said})

        elif kind == "tool.call":
            await self._tool(msg)

        elif kind == "reply.done":
            self._reply_open = False
            if msg.get("status") == "interrupted":
                self.call.interruptions += 1
                self.on_event("interrupted", {})
            await self._flush()

        elif kind == "session.error":
            self.call.ended_reason = (
                f"session error: {msg.get('code') or msg.get('error')}"
            )
            self.on_event("error", dict(msg))
            self._done.set()

        elif kind == "session.ended":
            self.call.ended_reason = self.call.ended_reason or "session ended"
            self._done.set()

    # -- tools ------------------------------------------------------------

    async def _tool(self, call: dict[str, Any]) -> None:
        name = str(call.get("name", ""))
        raw_args = call.get("arguments") or {}
        if isinstance(raw_args, str):
            with contextlib.suppress(json.JSONDecodeError):
                raw_args = json.loads(raw_args)
        args: dict[str, Any] = dict(raw_args) if isinstance(raw_args, dict) else {}
        started = time.perf_counter()
        try:
            out = await self.run_tool(
                name, dict(args), self._transcript, self._confidence
            )
        except Exception as exc:  # a failed tool is an answer, not a dropped call
            logger.exception("tool %s failed", name)
            out = {"ok": False, "result": {"error": str(exc)}}
        ms = round((time.perf_counter() - started) * 1000)
        self.call.tools.append(
            {"name": name, "args": args, "ms": ms, "ok": out.get("ok")}
        )
        self.on_event(
            "tool", {"name": name, "args": args, "ms": ms, "result": out.get("result")}
        )
        self._pending.append(
            {
                "call_id": str(call.get("call_id", "")),
                "result": json.dumps(out.get("result", {})),
            }
        )
        if not self._reply_open:
            await self._flush()

    async def _flush(self) -> None:
        while self._pending:
            result = self._pending.pop(0)
            await self._ws.send(json.dumps({"type": "tool.result", **result}))

    # -- keypad -----------------------------------------------------------

    async def _dtmf(self, digit: str) -> None:
        """Acknowledge, re-brief or hang up. Never approve."""
        self.call.dtmf.append(digit)
        self.on_event("dtmf", {"digit": digit})
        if digit == "9":
            self.call.ended_reason = "caller pressed 9"
            await self.leg.hangup()
            self._done.set()
            return
        instructions = {
            "1": (
                "The caller pressed 1 to acknowledge. Say in one sentence that the "
                "incident is acknowledged and you will stay on the line."
            ),
            "2": (
                "The caller pressed 2 and wants the brief again. Repeat the one-line "
                "cause and the exact phrase they would say to approve the fix."
            ),
            "0": "The caller pressed 0. Repeat your last sentence, more slowly.",
        }.get(digit)
        if instructions:
            await self._ws.send(
                json.dumps({"type": "reply.create", "instructions": instructions})
            )
        else:
            await self._ws.send(
                json.dumps(
                    {
                        "type": "reply.create",
                        "instructions": (
                            f"The caller pressed {digit}, which does nothing. Say "
                            "in one short sentence that a keypress cannot approve a "
                            "change, and give them the exact phrase to say instead."
                        ),
                    }
                )
            )

    async def say(self, content: str) -> None:
        """Speak a system update into the call (a verify loop finishing, say)."""
        await self._ws.send(
            json.dumps(
                {
                    "type": "reply.create",
                    "instructions": (
                        f"System update, not the caller speaking: {content} "
                        "Tell the caller in one sentence."
                    ),
                }
            )
        )
