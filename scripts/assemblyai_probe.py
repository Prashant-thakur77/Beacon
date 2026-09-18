"""Day-1 spike for the AssemblyAI Voice Agent API: connect, configure, listen, report.

Answers the questions the transport depends on, from one run:
  * which auth works from a client (header, query token, first message)
  * the exact event names the server emits (compare with web/src/voice/assemblyai.ts)
  * whether a text input event is accepted (typed fallback)
  * whether a client-side tool.call arrives for an echo tool and what its shape is

Usage:
    ASSEMBLYAI_API_KEY=... .venv/bin/python scripts/assemblyai_probe.py [--seconds 20] [--say "brief me"]

Prints one line per event and a summary. Nothing here touches AWS.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from collections import Counter
from typing import Any

WS_URL = "wss://agents.assemblyai.com/v1/ws"
TOKEN_URL = "https://api.assemblyai.com/v2/realtime/token"

ECHO_TOOL = {
    "type": "function",
    "name": "echo",
    "description": "Echo back what the user said. Call this whenever the user asks you to echo.",
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}


def mint_token(api_key: str) -> str | None:
    req = urllib.request.Request(
        TOKEN_URL,
        data=json.dumps({"expires_in": 600}).encode(),
        headers={"authorization": api_key, "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return str(json.loads(resp.read().decode()).get("token"))
    except Exception as exc:
        print(f"[token] mint failed: {exc}")
        return None


async def probe(api_key: str, seconds: int, say: str | None) -> int:
    try:
        import websockets
    except ImportError:
        print("pip install websockets", file=sys.stderr)
        return 2

    attempts: list[tuple[str, str, dict[str, str]]] = [
        ("header", WS_URL, {"Authorization": f"Bearer {api_key}"}),
    ]
    token = mint_token(api_key)
    if token:
        attempts.append(("query-token", f"{WS_URL}?token={token}", {}))
    attempts.append(("query-key", f"{WS_URL}?api_key={api_key}", {}))

    for label, url, headers in attempts:
        print(f"\n[connect] trying {label} ...")
        try:
            async with websockets.connect(
                url, additional_headers=headers, open_timeout=10
            ) as ws:
                print(f"[connect] {label}: OPEN")
                return await session(ws, seconds, say)
        except Exception as exc:
            print(f"[connect] {label}: {type(exc).__name__}: {exc}")
    print("\nNo auth variant connected. Check the key and the docs' auth section.")
    return 1


async def session(ws: Any, seconds: int, say: str | None) -> int:
    await ws.send(
        json.dumps(
            {
                "type": "session.update",
                "session": {
                    "system_prompt": "You are a terse test agent. If the user asks you to echo, call the echo tool.",
                    "greeting": "Probe connected.",
                    "input": {
                        "format": {"encoding": "audio/pcm"},
                        "turn_detection": {"vad_threshold": 0.5},
                        "language_codes": ["en", "hi"],
                    },
                    "output": {"voice": "ivy", "format": {"encoding": "audio/pcm"}},
                    "tools": [ECHO_TOOL],
                },
            }
        )
    )
    seen: Counter[str] = Counter()
    samples: dict[str, Any] = {}
    audio_bytes = 0
    started = time.time()
    said = False
    tool_call: dict[str, Any] | None = None
    reply_done_after_call = False

    while time.time() - started < seconds:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
        except TimeoutError:
            if say and not said and time.time() - started > 3:
                said = True
                # try the text-input shape the transport assumes; if the server rejects it, we learn the real one
                await ws.send(json.dumps({"type": "input.text", "text": say}))
                print(f"[send] input.text {say!r}")
            continue
        except Exception as exc:
            print(f"[recv] closed: {exc}")
            break
        try:
            msg = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            audio_bytes += len(raw)
            continue
        kind = str(msg.get("type", "?"))
        seen[kind] += 1
        if kind == "reply.audio":
            audio_bytes += len(msg.get("audio", ""))
        elif kind not in samples:
            samples[kind] = {
                k: (v if len(str(v)) < 120 else str(v)[:120] + "…")
                for k, v in msg.items()
            }
            print(f"[event] {json.dumps(samples[kind])}")
        if kind == "tool.call":
            tool_call = msg
            print(f"[tool.call] {json.dumps(msg)}")
        if kind == "reply.done" and tool_call and not reply_done_after_call:
            reply_done_after_call = True
            await ws.send(
                json.dumps(
                    {
                        "type": "tool.result",
                        "call_id": tool_call.get("call_id"),
                        "result": json.dumps({"echoed": tool_call.get("arguments")}),
                    }
                )
            )
            print("[send] tool.result after reply.done")
        if kind in ("session.error", "error"):
            print(f"[error] {json.dumps(msg)}")

    print("\n=== summary ===")
    for kind, n in seen.most_common():
        print(f"  {n:4d}  {kind}")
    print(f"  audio bytes received: {audio_bytes}")
    print(f"  tool.call seen: {bool(tool_call)}")
    expected = {
        "session.ready",
        "transcript.user.delta",
        "transcript.user",
        "reply.audio",
        "transcript.agent",
        "reply.done",
        "tool.call",
    }
    missing = sorted(expected - set(seen))
    print(
        f"  expected by web/src/voice/assemblyai.ts but not seen: {missing or 'none'}"
    )
    print(
        "  (a missing event may just not have been triggered; compare the names that DID appear)"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--say", default="please echo hello world")
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "")
    if not key:
        print("set ASSEMBLYAI_API_KEY", file=sys.stderr)
        return 2
    return asyncio.run(probe(key, args.seconds, args.say))


if __name__ == "__main__":
    sys.exit(main())
