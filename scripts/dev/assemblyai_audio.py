"""Push spoken audio (English or Hinglish) through the AssemblyAI Voice Agent API
the way the console does, and run every tool call against ``make local``.

Headless Chromium has no microphone, so this is how the Hinglish path gets tested:
Polly (Kajal, en-IN neural, handles Hinglish) speaks each line, the PCM is streamed
as ``input.audio`` at 24 kHz in 50 ms chunks, and each ``tool.call`` is forwarded to
``POST /voice/tools/<name>`` with the transcript the API produced -- the same
consent path the browser uses. Prints transcripts, tool calls and timings.

Usage (needs AWS creds for Polly and a running ``make local``):
    ASSEMBLYAI_API_KEY=... .venv/bin/python scripts/dev/assemblyai_audio.py \
        "kya problem hai" "isko fix kar do" "approve fix {fix}" \
        "haan" "saat din ke liye contract do"
"""

from __future__ import annotations

import asyncio
import audioop
import base64
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LOCAL = os.environ.get("BEACON_LOCAL_URL", "http://localhost:8000")
PASSCODE = os.environ.get("BEACON_LOCAL_PASSCODE", "local")
WS_URL = "wss://agents.assemblyai.com/v1/ws"
TOKEN_URL = "https://agents.assemblyai.com/v1/token?expires_in_seconds=600"
RATE = 24000
CHUNK = RATE // 20 * 2  # 50 ms of 16-bit mono

DEFAULT_LINES = [
    "kya hua hai",
    "isko fix kar do",
    "approve fix {fix}",
    "haan",
    "saat din ke liye contract do",
]


def _get(url: str, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


def _post(url: str, body: dict[str, Any]) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "x-beacon-passcode": PASSCODE},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


def system_prompt() -> str:
    """The console's SYSTEM_PROMPT_HINT, read out of the TSX so there is one source."""
    src = (ROOT / "web/src/components/TalkDuplex.tsx").read_text()
    block = src.split("const SYSTEM_PROMPT_HINT =", 1)[1].split(";\n", 1)[0]
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', block)).replace('\\"', '"')


def brief(incident: dict[str, Any]) -> str:
    rca = incident.get("rca_json") or {}
    missing = ((incident.get("diagnostics") or {}).get("missing_rules") or [{}])[0]
    summary = rca.get("spoken_summary") or rca.get("summary", "")
    return (
        f"Incident {incident['incident_id']} on alarm {incident.get('alarm_name')}, "
        f"status {incident['status']}. Summary: {summary} "
        f"Drift: rule {missing.get('ip_protocol', 'tcp')}/{missing.get('from_port')} "
        f"from {missing.get('source_group_id')} is missing on "
        f"{missing.get('group_id')}."
    )


def synth(polly: Any, text: str) -> bytes:
    """Polly PCM16 at 16 kHz -> 24 kHz, with a little silence on both ends."""
    out = polly.synthesize_speech(
        Text=text,
        OutputFormat="pcm",
        SampleRate="16000",
        VoiceId="Kajal",
        Engine="neural",
    )["AudioStream"].read()
    pcm, _ = audioop.ratecv(out, 2, 1, 16000, RATE, None)
    pad = b"\x00" * (RATE * 2 // 2)  # 500 ms
    return pad + pcm + pad


async def run(lines: list[str]) -> int:
    import boto3
    import websockets

    key = os.environ["ASSEMBLYAI_API_KEY"]
    polly = boto3.client("polly", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    tools = json.loads((ROOT / "web/src/tools.json").read_text())
    incident = _get(f"{LOCAL}/dash/incidents")["incidents"][0]
    inc_id = incident["incident_id"]
    state: dict[str, Any] = {"fix": "1", "busy": False, "pending": False, "last": ""}
    log: list[tuple[float, str, str]] = []
    t0 = time.time()

    def note(kind: str, text: str) -> None:
        log.append((time.time() - t0, kind, text))
        print(f"{time.time() - t0:6.1f} {kind:10} {text}", flush=True)

    async def connect() -> Any:
        tok = _get(TOKEN_URL, {"Authorization": f"Bearer {key}"})["token"]
        return await websockets.connect(
            f"{WS_URL}?token={tok}", open_timeout=15, ping_interval=None
        )

    link: dict[str, Any] = {"ws": await connect()}
    if True:  # one block, so the body keeps its indentation
        ws = link["ws"]
        session_update = json.dumps(
            {
                "type": "session.update",
                "session": {
                    "system_prompt": f"{system_prompt()}\n\n{brief(incident)}",
                    "greeting": "Beacon here. Bolo, kya karna hai?",
                    "input": {
                        "format": {"encoding": "audio/pcm"},
                        "turn_detection": {"vad_threshold": 0.5},
                        "transcription_mode": "balanced",
                        "language_codes": ["hi", "en"],
                        "keyterms": [
                            incident.get("alarm_name") or "",
                            "approve fix one",
                            "approve fix two",
                            "grant contract for seven days",
                            "saat din ke liye contract do",
                            "Beacon",
                        ],
                    },
                    "output": {
                        "voice": "jane",
                        "format": {"encoding": "audio/pcm"},
                    },
                    "tools": [
                        {
                            "type": "function",
                            "name": t["name"],
                            "description": t["description"],
                            "parameters": t["parameters"],
                            "execution_mode": "interactive",
                            "timeout_seconds": 60,
                        }
                        for t in tools
                    ],
                },
            }
        )
        await ws.send(session_update)

        pending_results: list[dict[str, Any]] = []
        audio_out = 0
        agent_pcm: list[bytes] = []

        async def reader() -> None:
            nonlocal audio_out
            async for raw in link["ws"]:
                if isinstance(raw, bytes):
                    audio_out += len(raw)
                    if state.get("first_audio") is None:
                        state["first_audio"] = time.time()
                        lag = time.time() - state["turn_end"]
                        note("audio", f"first audio {lag:.1f}s after turn end")
                    continue
                try:
                    msg = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                kind = msg.get("type")
                if kind == "reply.audio":
                    b64 = msg.get("data") or msg.get("audio") or ""
                    audio_out += len(b64)
                    agent_pcm.append(base64.b64decode(b64))
                    if state.get("first_audio") is None:
                        state["first_audio"] = time.time()
                        lag = time.time() - state["turn_end"]
                        note("audio", f"first audio {lag:.1f}s after turn end")
                elif kind == "session.ready":
                    state["resume_token"] = msg.get("resume_token")
                    state["session_id"] = msg.get("session_id")
                elif kind == "session.resumed":
                    note(
                        "resumed",
                        json.dumps({k: v for k, v in msg.items() if k != "config"})[
                            :160
                        ],
                    )
                elif kind == "input.speech.stopped":
                    state["turn_end"] = time.time()
                    state["first_audio"] = None
                elif kind == "transcript.user":
                    state["last"] = str(msg.get("text", ""))
                    state.setdefault("turn_end", time.time())
                    note("heard", state["last"])
                elif kind == "transcript.agent":
                    note("agent", str(msg.get("text", ""))[:160])
                elif kind == "reply.started":
                    state["busy"] = True
                    state["first_audio"] = None
                    state.setdefault("turn_end", time.time())
                elif kind == "tool.call":
                    state["pending"] = True
                    name, args = msg.get("name"), msg.get("arguments") or {}
                    note("tool.call", f"{name} {json.dumps(args)}")
                    started = time.time()
                    out = await asyncio.to_thread(
                        _post,
                        f"{LOCAL}/voice/tools/{name}",
                        {
                            "incident_id": inc_id,
                            "session_id": "audio-test",
                            "args": args,
                            "transcript": state["last"],
                            "channel": "assemblyai",
                        },
                    )
                    result = out.get("result", {})
                    if name == "propose_fix" and "fix_id" in result:
                        state["fix"] = str(result["fix_id"])
                    summary = (out.get("tool_events") or [{}])[0].get("summary", "")
                    note("tool.done", f"{summary} ({time.time() - started:.1f}s)")
                    pending_results.append(
                        {"call_id": msg.get("call_id"), "result": json.dumps(result)}
                    )
                    if not state["busy"]:
                        for r in pending_results:
                            await link["ws"].send(
                                json.dumps({"type": "tool.result", **r})
                            )
                        pending_results.clear()
                        state["pending"] = False
                elif kind == "reply.done":
                    state["busy"] = False
                    note("reply.done", str(msg.get("status")))
                    for r in pending_results:
                        await link["ws"].send(json.dumps({"type": "tool.result", **r}))
                    pending_results.clear()
                    state["pending"] = False
                elif kind in ("session.error", "error"):
                    note("ERROR", json.dumps(msg))

        async def guarded_reader() -> None:
            try:
                await reader()
            except Exception as exc:  # noqa: BLE001 - surfaced, not hidden
                note("READER", f"{type(exc).__name__}: {exc}")
                raise

        tasks: dict[str, Any] = {"reader": asyncio.create_task(guarded_reader())}

        # A microphone never stops: stream silence between utterances so turn detection
        # sees the end of each one (and the socket stays alive).
        outbox: asyncio.Queue[bytes] = asyncio.Queue()
        silence = b"\x00" * CHUNK

        async def sender() -> None:
            while True:
                chunk = outbox.get_nowait() if not outbox.empty() else silence
                await link["ws"].send(
                    json.dumps(
                        {
                            "type": "input.audio",
                            "audio": base64.b64encode(chunk).decode(),
                        }
                    )
                )
                await asyncio.sleep(0.05)

        tasks["sender"] = asyncio.create_task(sender())

        async def drop_and_resume() -> None:
            """Kill the socket like a Wi-Fi blip, then resume the same session."""
            tasks["reader"].cancel()
            tasks["sender"].cancel()
            link["ws"].transport.abort()
            note("drop", "socket aborted mid-turn")
            await asyncio.sleep(1.0)
            link["ws"] = await connect()
            await link["ws"].send(
                json.dumps(
                    {"type": "session.resume", "session_id": state["session_id"]}
                )
            )
            first = json.loads(await asyncio.wait_for(link["ws"].recv(), 15))
            note("resume", json.dumps({k: str(v)[:70] for k, v in first.items()}))
            if first.get("type") == "session.error":
                # grace window gone / credential refused: a fresh session, same config
                link["ws"] = await connect()
                await link["ws"].send(session_update)
                note("resume", "refused; started a fresh session")
            tasks["reader"] = asyncio.create_task(guarded_reader())
            tasks["sender"] = asyncio.create_task(sender())

        async def settle(max_s: float = 60) -> None:
            t = time.time()
            await asyncio.sleep(2.5)
            while (state["busy"] or state["pending"]) and time.time() - t < max_s:
                await asyncio.sleep(0.2)
            await asyncio.sleep(1.0)

        await settle(20)
        for line in lines:
            if line == "!drop":
                await drop_and_resume()
                await settle(30)
                continue
            text = line.replace("{fix}", state["fix"])
            note("you", text)
            pcm = await asyncio.to_thread(synth, polly, text)
            for i in range(0, len(pcm), CHUNK):
                outbox.put_nowait(pcm[i : i + CHUNK])
            while not outbox.empty():
                await asyncio.sleep(0.1)
            if lines[lines.index(line) + 1 : lines.index(line) + 2] == ["!drop"]:
                continue  # the next step kills the socket before the reply lands
            await settle(75)
            await settle(45)
        await link["ws"].send(json.dumps({"type": "session.end"}))
        tasks["sender"].cancel()
        tasks["reader"].cancel()
        await link["ws"].close()

    if save := os.environ.get("BEACON_SAVE_AGENT_WAV"):
        import wave

        with wave.open(save, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"".join(agent_pcm))
        secs = sum(map(len, agent_pcm)) / RATE / 2
        print(f"agent audio -> {save} ({secs:.1f}s at {RATE} Hz)")
    inc = _get(f"{LOCAL}/dash/incidents")["incidents"][0]
    contracts = _get(f"{LOCAL}/dash/contracts")["contracts"]
    print(
        f"\nincident status: {inc['status']} | contracts: {len(contracts)}"
        f" | audio bytes out: {audio_out}"
    )
    heard = [t for _, k, t in log if k == "heard"]
    calls = [t.split()[0] for _, k, t in log if k == "tool.call"]
    print("heard:", heard)
    print("tools:", calls)
    print("session:", state.get("session_id"))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1:] or DEFAULT_LINES)))
