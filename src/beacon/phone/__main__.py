"""``python -m beacon.phone`` -- run the phone channel, or prove it works.

    # a real number, once a carrier is configured (see docs/phone.md). Given a
    # --dashboard-url and no --incident, an inbound call reaches whatever is open.
    python -m beacon.phone serve --base-url $VOICE_URL \\
        --public-url https://beacon.example --dashboard-url $DASHBOARD_URL

    # wake somebody
    python -m beacon.phone dial --to +911234567890 --twiml-url https://beacon.example/twiml

    # the same call with no carrier: scripted speech at telephone quality, and a
    # two-channel recording that says who approved what
    python -m beacon.phone replay --base-url http://localhost:8000 --incident inc-123 \\
        --scenario approve --out call.wav
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from beacon.phone import codec, server, tools
from beacon.phone.bridge import PhoneBridge
from beacon.phone.providers.replay import Line, ReplayLeg

log = logging.getLogger("beacon.phone")

# Polly writes 8 kHz PCM directly, which is exactly what the phone network carries:
# no resampling, no ffmpeg, and the caller's lines are degraded the way a real call
# degrades them before the agent ever hears a word.
CALLER_VOICE = os.environ.get("BEACON_PHONE_VOICE", "Joanna")
HINGLISH_VOICE = os.environ.get("BEACON_PHONE_VOICE_HI", "Kajal")


def say(text: str, *, voice: str = "") -> bytes:
    from typing import cast

    import boto3
    from mypy_boto3_polly.literals import VoiceIdType  # noqa: TC002

    pcm = (
        boto3.client("polly")
        .synthesize_speech(
            Text=text,
            OutputFormat="pcm",
            SampleRate="8000",
            VoiceId=cast("VoiceIdType", voice or CALLER_VOICE),
            Engine="neural",
        )["AudioStream"]
        .read()
    )
    import array

    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    return codec.pcm_to_ulaw(samples)


# -- the scripted calls ----------------------------------------------------

_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}


def spoken_number(n: int) -> str:
    """Nobody says 'approve fix 4' out loud; they say 'four'."""
    return _WORDS.get(n, str(n))


class CallState:
    """What the caller has learned during the call, and can therefore say.

    The only thing so far is the fix number, which the agent invents at propose time.
    """

    def __init__(self) -> None:
        self.fix_id = 1

    def note(self, tool: str, result: dict[str, Any]) -> None:
        if tool == "propose_fix" and isinstance(result.get("fix_id"), int):
            self.fix_id = int(result["fix_id"])

    def fill(self, text: str) -> str:
        return text.replace("{fix}", spoken_number(self.fix_id))


SCENARIOS: dict[str, list[dict[str, Any]]] = {
    # The night as it is meant to go: cause, proposal, the exact phrase, verified.
    "approve": [
        {"text": "What is going on?"},
        {"text": "Can you fix it?"},
        {"text": "Approve fix {fix}."},
        {"text": "Is it fixed?"},
    ],
    # Agreement is not consent, on the phone as in the browser.
    "wrong_phrase": [
        {"text": "Fix it."},
        {"text": "Yes, do it."},
    ],
    # Talking over the read-back withdraws the fix, and the phrase then fails.
    "barge_in": [
        {"text": "Fix it."},
        {"text": "No, wait, stop.", "interrupt": True},
        {"text": "Approve fix {fix}."},
    ],
    # The channel a phone actually gets used in.
    "hinglish": [
        {"text": "Kya hua hai?", "voice": HINGLISH_VOICE},
        {"text": "Isko fix kar do.", "voice": HINGLISH_VOICE},
    ],
    # The take for the film: a person on a phone at 3 AM, not a test harness. Same
    # socket, same tools, same consent rules as every other scenario here — the
    # lines are just the ones somebody would actually say, with the pauses and the
    # half-sentences left in.
    "film": [
        {"text": "Hello?"},
        {"text": "Okay, what's going on?"},
        {"text": "Why? What changed?"},
        {"text": "Right. What are my options?"},
        {"text": "Approve fix {fix}."},
        {"text": "Is it actually fixed?"},
        {"text": "Good. Handle it yourself next time."},
    ],
    # A keypress acknowledges; it must never approve. The caller deliberately never
    # says the phrase here, so the only thing that could have approved the fix is the
    # keypress -- and nothing may be applied.
    "keypad": [
        {"dtmf": "1"},
        {"text": "Fix it."},
        {"dtmf": "5"},
        {"text": "I pressed five. Did that do it?"},
    ],
}


def build_lines(name: str, state: CallState | None = None) -> list[Line]:
    """The caller's script. Lines naming ``{fix}`` are rendered when they are due."""
    state = state or CallState()
    out: list[Line] = []
    for step in SCENARIOS[name]:
        if step.get("dtmf"):
            out.append(Line(dtmf=str(step["dtmf"]), label=f"press {step['dtmf']}"))
            continue
        text = str(step["text"])
        voice = str(step.get("voice") or "")
        if "{fix}" in text:

            def late(template: str = text, chosen: str = voice) -> bytes:
                assert state is not None
                return say(state.fill(template), voice=chosen)

            out.append(
                Line(
                    label=text,
                    interrupt=bool(step.get("interrupt")),
                    render=late,
                )
            )
        else:
            out.append(
                Line(
                    ulaw=say(text, voice=voice),
                    label=text,
                    interrupt=bool(step.get("interrupt")),
                )
            )
    return out


# -- commands --------------------------------------------------------------


async def cmd_replay(args: argparse.Namespace) -> int:
    brief = tools.fetch_brief(
        args.base_url,
        passcode=args.passcode,
        incident_id=args.incident,
        channel="phone",
    )
    token = tools.mint_token(args.base_url, passcode=args.passcode)
    state = CallState()
    leg = ReplayLeg(build_lines(args.scenario, state), max_seconds=args.max_seconds)
    session_id = f"phone-{args.scenario}-{os.urandom(3).hex()}"
    events: list[dict[str, Any]] = []

    def note(kind: str, data: dict[str, Any]) -> None:
        events.append({"kind": kind, **data})
        if kind == "heard":
            print(f"  caller → {data.get('text')!r}")
        elif kind == "said":
            print(f"  agent  → {data.get('text')!r}")
        elif kind == "tool":
            state.note(data["name"], dict(data.get("result") or {}))
            print(f"  tool   → {data['name']} ({data['ms']} ms)")
        elif kind in ("interrupted", "dtmf", "error"):
            print(f"  {kind}: {data or ''}")

    bridge = PhoneBridge(
        leg,
        brief=brief,
        token=token,
        run_tool=tools.http_runner(
            args.base_url,
            passcode=args.passcode,
            incident_id=args.incident,
            session_id=session_id,
        ),
        on_event=note,
    )
    print(f"calling about {args.incident} — scenario {args.scenario}")
    record = await bridge.run()
    ttfa = record.ttfa_ms
    latency = (
        f"reply latency {int(min(ttfa))}–{int(max(ttfa))} ms "
        f"({len(ttfa)} turns; a turn that calls a tool includes its round trip)"
        if ttfa
        else "no agent audio"
    )
    print(
        f"\n{record.seconds()} s · {len(record.turns)} turns · "
        f"tools {[t['name'] for t in record.tools]} · "
        f"interruptions {record.interruptions} · {latency}"
    )

    report: dict[str, Any] = {
        "scenario": args.scenario,
        "incident_id": args.incident,
        "seconds": record.seconds(),
        "aai_session_id": record.aai_session_id,
        "first_audio_ms": record.first_audio_ms,
        "ttfa_ms": record.ttfa_ms,
        "interruptions": record.interruptions,
        "dtmf": record.dtmf,
        "tools": record.tools,
        "turns": record.turns,
        "events": events,
        "ended": record.ended_reason,
    }
    if bridge.recorder is not None:
        wav = bridge.recorder.wav()
        Path(args.out).write_bytes(wav)
        print(f"recording → {args.out}  ({bridge.recorder.seconds()} s, 2 channels)")
        if args.attest:
            try:
                verdict = server.attest_call(
                    wav, [t["name"] for t in record.tools], brief
                )
                report["attestation"] = verdict
                print("\nwho authorised what (channel 1 = caller, 2 = agent):")
                for check in verdict["checks"]:
                    mark = "ok  " if check["ok"] else "FAIL"
                    print(
                        f"  {mark} {check['tool']}: caller said it "
                        f"{check['caller_occurrences']}x, "
                        f"agent {check['agent_occurrences']}x"
                        + (f" — {check['quote']!r}" if check["quote"] else "")
                    )
                for problem in verdict["problems"]:
                    print(f"  ! {problem}")
            except Exception as exc:
                print(f"  attestation unavailable: {exc}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str))
        print(f"report → {args.json}")
    return 0 if record.turns else 1


async def cmd_serve(args: argparse.Namespace) -> int:
    service = server.PhoneService(
        base_url=args.base_url,
        passcode=args.passcode,
        public_url=args.public_url,
        default_incident=args.incident or "",
        dashboard_url=args.dashboard_url,
        recordings_dir=args.recordings,
    )
    await server.serve(service, host=args.host, port=args.port)
    return 0


def cmd_dial(args: argparse.Namespace) -> int:
    from beacon.phone.providers import twilio

    sid = twilio.place_call(args.to, args.twiml_url)
    print(f"calling {args.to} — call {sid}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(prog="beacon.phone", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--base-url",
            default=os.environ.get("BEACON_VOICE_URL", "http://localhost:8000"),
        )
        p.add_argument("--passcode", default=os.environ.get("BEACON_PASSCODE", "local"))

    r = sub.add_parser("replay", help="a scripted call, no carrier needed")
    common(r)
    r.add_argument("--incident", required=True)
    r.add_argument("--scenario", default="approve", choices=sorted(SCENARIOS))
    r.add_argument("--out", default="call.wav")
    r.add_argument("--json", default="")
    r.add_argument("--max-seconds", type=float, default=180.0)
    r.add_argument("--no-attest", dest="attest", action="store_false")

    s = sub.add_parser("serve", help="answer real calls from a carrier")
    common(s)
    s.add_argument(
        "--public-url", required=True, help="how the carrier reaches this process"
    )
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument(
        "--incident",
        default="",
        help="pin every call to this incident; omit to answer about whatever is open",
    )
    s.add_argument(
        "--dashboard-url",
        default=os.environ.get("BEACON_DASHBOARD_URL", ""),
        help="where to look up the live incident when a call names none (inbound)",
    )
    s.add_argument(
        "--recordings", default="", help="directory to keep call recordings in"
    )

    d = sub.add_parser("dial", help="place an outbound call")
    d.add_argument("--to", required=True)
    d.add_argument("--twiml-url", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "dial":
        return cmd_dial(args)
    if args.cmd == "serve":
        return asyncio.run(cmd_serve(args))
    return asyncio.run(cmd_replay(args))


if __name__ == "__main__":
    sys.exit(main())
