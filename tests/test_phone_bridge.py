"""The phone bridge: a scripted socket on one side, a scripted caller on the other.

No carrier, no AssemblyAI, no AWS: a fake socket replays the message sequence the
Voice Agent API sends, so the rules the bridge enforces are testable -- a keypress
is never consent, the caller's audio is the only thing that reaches the transcriber,
and talking over the agent clears what is queued for the line.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from beacon.phone import codec
from beacon.phone.bridge import PhoneBridge, StereoRecorder
from beacon.phone.providers.replay import Line, ReplayLeg

BRIEF = {
    "incident_id": "inc-1",
    "system_prompt": "be brief",
    "greeting": "hello",
    "keyterms": ["approve fix one"],
    "language_codes": ["en", "hi"],
}


class FakeSocket:
    """The Voice Agent API, scripted. Records what the bridge sent it."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = script
        self.sent: list[dict[str, Any]] = []
        self._out: asyncio.Queue[str | None] = asyncio.Queue()
        for msg in script:
            self._out.put_nowait(json.dumps(msg))
        self._out.put_nowait(None)

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def __aiter__(self) -> FakeSocket:
        return self

    async def __anext__(self) -> str:
        item = await self._out.get()
        if item is None:
            raise StopAsyncIteration
        return item

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.sent if m.get("type") == kind]


async def _pump(bridge: PhoneBridge, socket: FakeSocket) -> Any:
    """Drive the bridge without opening a real socket."""
    bridge._ws = socket  # noqa: SLF001 -- the socket is the seam being faked
    caller = asyncio.create_task(bridge._pump_caller())  # noqa: SLF001
    agent = asyncio.create_task(bridge._pump_agent())  # noqa: SLF001
    await asyncio.wait({caller, agent}, timeout=10, return_when=asyncio.ALL_COMPLETED)
    for task in (caller, agent):
        task.cancel()
    return bridge.call


def run(bridge: PhoneBridge, socket: FakeSocket) -> Any:
    """The tests are synchronous; the bridge is not. One event loop per test."""
    return asyncio.run(_pump(bridge, socket))


def bridge_for(
    socket: FakeSocket, lines: list[Line], tool_result: Any = None
) -> tuple[PhoneBridge, list[tuple[str, dict[str, Any], str]]]:
    calls: list[tuple[str, dict[str, Any], str]] = []

    async def run_tool(
        name: str, args: dict[str, Any], transcript: str, confidence: float | None
    ) -> dict[str, Any]:
        calls.append((name, args, transcript))
        return tool_result or {"ok": True, "result": {"applied": True}}

    return (
        PhoneBridge(
            ReplayLeg(lines, quiet_ms=60, lead_ms=20, patience_s=0.3, max_seconds=6),
            brief=BRIEF,
            token="t",
            run_tool=run_tool,
        ),
        calls,
    )


def a_word() -> bytes:
    """A second of tone, standing in for the caller saying something."""
    import array
    import math

    pcm = array.array(
        "h", [int(9000 * math.sin(2 * math.pi * 300 * i / 8000)) for i in range(8000)]
    )
    return codec.pcm_to_ulaw(pcm)


def test_the_callers_audio_reaches_the_transcriber_untouched() -> None:
    """The API takes mu-law natively, so the carrier's frame is forwarded as is.

    Transcoding to 24 kHz and back was work the call did not need, and it cost
    real quality at the top of the phone band on the way through.
    """
    socket = FakeSocket([{"type": "session.ready", "session_id": "sess_x"}])
    word = a_word()
    bridge, _ = bridge_for(socket, [Line(ulaw=word, label="fix it")])
    record = run(bridge, socket)
    audio = socket.of_type("input.audio")
    assert audio, "nothing was streamed to the Voice Agent API"
    sent = b"".join(base64.b64decode(chunk["audio"]) for chunk in audio)
    # one 20 ms telephony frame in, the same 160 mu-law bytes out
    assert len(base64.b64decode(audio[0]["audio"])) == codec.PHONE_FRAME_BYTES
    assert word in sent, "the caller's own bytes must arrive unaltered"
    assert record.aai_session_id == "sess_x"


def test_the_session_asks_for_mu_law_in_both_directions() -> None:
    """audio/pcmu, which the API offers alongside audio/pcm and audio/pcma."""
    from beacon.phone.bridge import PHONE_ENCODING

    bridge, _ = bridge_for(FakeSocket([]), [])
    session = json.loads(bridge._session_update())["session"]  # noqa: SLF001
    assert PHONE_ENCODING == "audio/pcmu"
    assert session["input"]["format"]["encoding"] == PHONE_ENCODING
    assert session["output"]["format"]["encoding"] == PHONE_ENCODING


def test_a_keypress_can_acknowledge_but_never_approve() -> None:
    socket = FakeSocket([{"type": "session.ready", "session_id": "sess_x"}])
    bridge, calls = bridge_for(socket, [Line(dtmf="1"), Line(dtmf="5")])
    record = run(bridge, socket)
    assert record.dtmf == ["1", "5"]
    assert calls == [], "a keypress must not run a tool"
    spoken = " ".join(m["instructions"] for m in socket.of_type("reply.create"))
    assert "acknowledged" in spoken
    assert "keypress cannot approve" in spoken


def test_pressing_nine_hangs_up() -> None:
    socket = FakeSocket([{"type": "session.ready", "session_id": "sess_x"}])
    bridge, _ = bridge_for(socket, [Line(dtmf="9"), Line(ulaw=a_word(), label="hello")])
    record = run(bridge, socket)
    assert record.ended_reason == "caller pressed 9"
    assert socket.of_type("reply.create") == []


def test_a_tool_call_carries_the_transcript_and_is_answered_after_the_reply() -> None:
    socket = FakeSocket(
        [
            {"type": "session.ready", "session_id": "sess_x"},
            {
                "type": "transcript.user",
                "transcript": "approve fix one",
                "confidence": 0.97,
            },
            {"type": "reply.started"},
            {
                "type": "tool.call",
                "call_id": "c1",
                "name": "approve_fix",
                "arguments": {"fix_id": 1, "confirmation_phrase": "approve fix one"},
            },
            {"type": "reply.done", "status": "completed"},
        ]
    )
    bridge, calls = bridge_for(socket, [])
    run(bridge, socket)
    assert calls and calls[0][0] == "approve_fix"
    assert calls[0][2] == "approve fix one", "the tool must see what the caller said"
    results = socket.of_type("tool.result")
    assert [r["call_id"] for r in results] == ["c1"]
    # the result is only sent once the reply that asked for it is finished
    assert socket.sent.index(results[0]) > 0


def test_speaking_over_the_agent_clears_the_line() -> None:
    socket = FakeSocket(
        [
            {"type": "session.ready", "session_id": "sess_x"},
            {"type": "reply.started"},
            {"type": "input.speech.started"},
            {"type": "reply.done", "status": "interrupted"},
        ]
    )
    leg = ReplayLeg([], quiet_ms=60, lead_ms=20, patience_s=0.3, max_seconds=4)
    bridge = PhoneBridge(
        leg,
        brief=BRIEF,
        token="t",
        run_tool=lambda *_: asyncio.sleep(0, {"ok": True, "result": {}}),
    )
    record = run(bridge, socket)
    assert leg.cleared == 1, (
        "queued audio must be dropped, or the agent talks over the caller"
    )
    assert record.interruptions == 1


def test_a_failing_tool_answers_the_agent_instead_of_dropping_the_call() -> None:
    socket = FakeSocket(
        [
            {"type": "session.ready", "session_id": "sess_x"},
            {
                "type": "tool.call",
                "call_id": "c9",
                "name": "propose_fix",
                "arguments": "{}",
            },
        ]
    )

    async def boom(*_: Any) -> dict[str, Any]:
        raise RuntimeError("the Lambda is cold")

    bridge = PhoneBridge(
        ReplayLeg([], quiet_ms=60, lead_ms=20, patience_s=0.3, max_seconds=4),
        brief=BRIEF,
        token="t",
        run_tool=boom,
    )
    run(bridge, socket)
    results = socket.of_type("tool.result")
    assert results and "the Lambda is cold" in results[0]["result"]


def test_the_session_declares_the_nine_tools_and_a_phone_turn_detector() -> None:
    from beacon import voice_tools

    bridge, _ = bridge_for(FakeSocket([]), [])
    session = json.loads(bridge._session_update())["session"]  # noqa: SLF001
    assert [t["name"] for t in session["tools"]] == [
        s["name"] for s in voice_tools.TOOL_SCHEMAS
    ]
    assert all(t["execution_mode"] == "interactive" for t in session["tools"])
    # a phone line is noisier and callers pause: turns close later than in a browser
    assert session["input"]["turn_detection"]["vad_threshold"] > 0.5
    assert session["input"]["turn_detection"]["min_silence"] >= 700


def test_the_recording_keeps_the_caller_and_the_agent_on_separate_channels() -> None:
    import io
    import wave

    recorder = StereoRecorder()
    recorder.add_caller(codec.silence_ulaw(100))
    recorder.add_agent(a_word())
    with wave.open(io.BytesIO(recorder.wav())) as wav:
        assert wav.getnchannels() == 2
        assert wav.getframerate() == codec.PHONE_RATE
        # both channels run to the same length: the agent is not shifted to the front
        assert wav.getnframes() == max(len(recorder.caller), len(recorder.agent))


def test_the_recording_is_as_long_as_the_call_not_as_long_as_the_bursts() -> None:
    """A sentence is generated faster than said; the file must not compress it."""
    recorder = StereoRecorder()
    # four seconds of agent speech, handed over all at once, as the API does
    for _ in range(4):
        recorder.add_agent(a_word())
    assert 3.9 < recorder.seconds() < 4.3
    # a burst does not push the caller's channel out past it
    recorder.add_caller(codec.silence_ulaw(100))
    assert 3.9 < recorder.seconds() < 4.4
