"""A carrier that isn't one: speak Twilio's Media Streams protocol at our own server.

`make phone-test` proves the bridge with the replay leg. This proves the half that
only a carrier normally exercises — the TwiML the webhook answers with, the
WebSocket handshake on the same port, `TwilioLeg`, and `PhoneService.handle` — by
being a client that sends exactly what Twilio sends and reads exactly what Twilio
reads. After this passes, the only thing left unproven about a real call is the
carrier itself.

    # one shell
    python -m beacon.phone serve --base-url http://localhost:8000/voice \
        --passcode local --public-url http://localhost:8080 --incident <id>
    # another
    .venv/bin/python scripts/twilio_probe.py --incident <id> --out call.wav
"""

from __future__ import annotations

import argparse
import array
import asyncio
import base64
import json
import sys
import urllib.request
import wave
from pathlib import Path

from beacon.phone import codec

FRAME_S = codec.FRAME_MS / 1000


def twiml_check(base: str) -> list[str]:
    """The webhook has to answer with a two-way stream, or the caller is never heard."""
    problems: list[str] = []
    with urllib.request.urlopen(f"{base}/twiml", timeout=10) as resp:  # noqa: S310
        body = resp.read().decode()
        if resp.status != 200:
            problems.append(f"/twiml answered {resp.status}")
    if "<Connect>" not in body:
        problems.append("the TwiML is not <Connect> — a <Start> stream cannot answer")
    if "<Stream" not in body or "url=" not in body:
        problems.append("the TwiML names no stream URL")
    print(body.strip())
    return problems


async def place(args: argparse.Namespace) -> int:
    import websockets

    from beacon.phone.__main__ import CallState, build_lines

    base = args.base.rstrip("/")
    problems = twiml_check(base)
    ws_url = base.replace("http://", "ws://").replace("https://", "wss://") + "/media"

    state = CallState()
    lines = build_lines(args.scenario, state)
    heard = array.array("h")  # what the carrier would have played to the caller
    played = 0
    # A carrier holds a jitter buffer: the server hands over a sentence far faster
    # than it takes to say, and the caller hears it at speaking speed. Without
    # modelling that, the probe talks over an utterance it has only received —
    # which the bridge correctly reads as barge-in, and the call falls apart.
    play_until = 0.0
    # How many separate replies the agent has begun. Waiting only for the line to
    # go quiet is not enough: quiet is the state immediately after the caller stops
    # talking, so the next line goes out into a reply that has not started yet and
    # cancels it. The replay leg learned this the same way.
    replies = 0

    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"event": "connected", "protocol": "Call"}))
        await ws.send(
            json.dumps(
                {
                    "event": "start",
                    "sequenceNumber": "1",
                    "streamSid": "MZprobe",
                    "start": {
                        "streamSid": "MZprobe",
                        "callSid": "CAprobe",
                        "tracks": ["inbound"],
                        "mediaFormat": {
                            "encoding": "audio/x-mulaw",
                            "sampleRate": 8000,
                            "channels": 1,
                        },
                        "customParameters": {
                            "incident_id": args.incident,
                            "from": args.caller,
                        },
                    },
                }
            )
        )

        stop = asyncio.Event()

        async def read() -> None:
            nonlocal played, play_until, replies
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("event") == "media":
                    payload = (msg.get("media") or {}).get("payload") or ""
                    chunk = codec.ulaw_to_pcm(base64.b64decode(payload))
                    heard.extend(chunk)
                    played += len(chunk)
                    now = asyncio.get_running_loop().time()
                    if play_until < now - 0.25:
                        replies += 1
                        print(f"  agent reply {replies} starts")
                    play_until = max(now, play_until) + len(chunk) / codec.PHONE_RATE
                elif msg.get("event") == "clear":
                    # what Twilio does with `clear`: drop the buffer, stop talking
                    play_until = asyncio.get_running_loop().time()
                    heard.extend(array.array("h", bytes(2 * codec.PHONE_RATE // 4)))
                    print("  carrier dropped the queue (barge-in)")

        async def write() -> None:
            # the lead-in a real line has before the first word, then the greeting
            silence = codec.silence_ulaw(codec.FRAME_MS)
            for _ in range(35):
                await send(silence)
            while replies == 0 and not stop.is_set():
                await send(silence)
            while asyncio.get_running_loop().time() - play_until < 0.9:
                await send(silence)
            for line in lines:
                if line.dtmf:
                    print(f"\n  keypress {line.dtmf}")
                    await ws.send(
                        json.dumps({"event": "dtmf", "dtmf": {"digit": line.dtmf}})
                    )
                    continue
                # Wait for a reply to this turn to begin and then finish *playing*,
                # not merely to finish arriving, then leave a beat.
                owed = replies
                waited = 0.0
                while waited < 40.0 and not stop.is_set():
                    await send(silence)
                    waited += FRAME_S
                    now = asyncio.get_running_loop().time()
                    if replies > owed and now - play_until > 0.9:
                        break
                audio = line.ulaw or (line.render() if line.render else b"")
                print(f"\n  caller: {state.fill(line.label)!r}")
                for frame in codec.frames(audio):
                    await send(frame)
            for _ in range(100):
                await send(silence)
            await ws.send(json.dumps({"event": "stop", "streamSid": "MZprobe"}))
            stop.set()

        async def send(ulaw: bytes) -> None:
            await ws.send(
                json.dumps(
                    {
                        "event": "media",
                        "streamSid": "MZprobe",
                        "media": {"payload": base64.b64encode(ulaw).decode()},
                    }
                )
            )
            await asyncio.sleep(FRAME_S)

        reader = asyncio.create_task(read())
        await write()
        reader.cancel()

    if args.out and heard:
        with wave.open(args.out, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(codec.PHONE_RATE)
            out.writeframes(heard.tobytes())
        print(f"\n  what the caller would have heard → {args.out}")

    if played == 0:
        problems.append("the server never sent any audio to the caller")
    for problem in problems:
        print(f"  ✗ {problem}")
    print(f"\n  {'FAIL' if problems else 'PASS'} — the carrier side of the call\n")
    return len(problems)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--incident", required=True)
    ap.add_argument("--scenario", default="approve")
    ap.add_argument("--caller", default="+919015954507")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    return asyncio.run(place(args))


if __name__ == "__main__":
    sys.exit(main())
