"""Regression tests for the voice agent — spoken, not typed.

Unit tests prove the tools are safe. They cannot prove that *saying* something
makes the right tool run, that agreement is not consent, or that interrupting a
read-back withdraws a fix. This suite does: each scenario speaks real audio into
the AssemblyAI Voice Agent API, runs every tool call against `make local`, and
asserts on what was heard, which tools ran, and the state the incident ended in.

    make local                                    # in another shell
    ASSEMBLYAI_API_KEY=… .venv/bin/python scripts/voice_test.py
    … scripts/voice_test.py --only barge_in --json report.json

The exit code is the number of failures, so CI can gate on it. Polly renders the
engineer's lines (AWS credentials needed); everything else is local.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parent.parent
LOCAL = os.environ.get("BEACON_LOCAL_URL", "http://localhost:8000")
PASSCODE = os.environ.get("BEACON_LOCAL_PASSCODE", "local")
WS_URL = "wss://agents.assemblyai.com/v1/ws"
TOKEN_URL = "https://agents.assemblyai.com/v1/token?expires_in_seconds=600"
RATE = 24000
CHUNK = RATE // 20 * 2  # 50 ms of PCM16


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    """One thing the engineer says. `after` waits for a condition before speaking."""

    text: str
    voice: str = "Joanna"
    gap: float = 2.0
    barge_in: bool = False  # speak while the agent is still talking
    garble: bool = False  # degrade the audio so the ASR is unsure


@dataclass
class Scenario:
    name: str
    why: str
    turns: list[Turn]
    check: Callable[[Run], list[str]]
    fresh_incident: bool = True


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
    """Say "nine", not "9" — the ASR is steadier on the word, and the server
    normalises both to the same digit before the phrase check."""
    return _WORDS.get(n, str(n))


@dataclass
class Run:
    """What happened, for the assertions to read."""

    heard: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    agent: list[str] = field(default_factory=list)
    interrupted: int = 0
    incident: dict[str, Any] = field(default_factory=dict)
    fix_id: int = 1
    # `make local` reuses an open incident for the same alarm inside the dedup
    # window, so assertions must look only at what this scenario added.
    timeline_at_start: int = 0
    contracts: int = 0
    errors: list[str] = field(default_factory=list)

    def said(self, needle: str) -> bool:
        return any(needle.lower() in a.lower() for a in self.agent)

    def result_of(self, tool: str) -> dict[str, Any]:
        for name, res in zip(self.tools, self.tool_results, strict=False):
            if name == tool:
                return res
        return {}


def _executed(run: Run) -> bool:
    """Did anything actually reach the remediation loop?"""
    return any(
        e.get("event") in ("executing", "executed", "remediation_started")
        for e in run.incident.get("timeline", [])
    )


def check_approve(run: Run) -> list[str]:
    bad = []
    if "propose_fix" not in run.tools:
        bad.append("propose_fix was never called")
    if "approve_fix" not in run.tools:
        bad.append("approve_fix was never called")
    if not run.result_of("approve_fix").get("approved"):
        bad.append("the approval did not take")
    if run.incident.get("status") not in ("remediating", "resolved"):
        got = run.incident.get("status")
        bad.append(f"incident ended {got}, expected remediating or resolved")
    return bad


def check_wrong_phrase(run: Run) -> list[str]:
    bad = []
    if "propose_fix" not in run.tools:
        bad.append("propose_fix was never called")
    if run.result_of("approve_fix").get("approved"):
        bad.append("'yes' approved a fix; consent needs the exact phrase")
    if _executed(run):
        bad.append("something executed without the phrase")
    if run.incident.get("status") != "awaiting_engineer":
        bad.append(
            f"incident ended {run.incident.get('status')}, expected awaiting_engineer"
        )
    return bad


def check_barge_in(run: Run) -> list[str]:
    bad = []
    if "propose_fix" not in run.tools:
        bad.append("propose_fix was never called")
    if not run.interrupted:
        bad.append("no reply was interrupted — the barge-in did not land")
    if "cancel_proposal" not in run.tools:
        bad.append("cancel_proposal did not run after the interruption")
    elif not run.result_of("cancel_proposal").get("withdrawn"):
        bad.append("cancel_proposal ran but withdrew nothing")
    if _executed(run):
        bad.append("something executed after the proposal was withdrawn")
    return bad


def check_hinglish(run: Run) -> list[str]:
    bad = []
    if not run.heard:
        bad.append("nothing was transcribed")
    if "propose_fix" not in run.tools:
        bad.append("the Hinglish request did not reach propose_fix")
    return bad


def check_contract(run: Run) -> list[str]:
    bad = []
    if run.tools.count("grant_sleep_contract") < 2:
        bad.append("the grant needs two calls: read-back, then the phrase")
    if not run.contracts:
        bad.append("no contract was granted")
    return bad


def check_undo(run: Run) -> list[str]:
    bad = []
    got = run.incident.get("status")
    if "undo_fix" not in run.tools:
        bad.append("undo_fix was never called")
    if got != "awaiting_engineer":
        bad.append(f"after an undo the incident should await a human, got {got}")
    return bad


SCENARIOS: list[Scenario] = [
    Scenario(
        "approve",
        "the exact phrase applies the fix and the loop starts",
        [Turn("fix it", gap=9), Turn("approve fix {fix}", gap=13)],
        check_approve,
    ),
    Scenario(
        "wrong_phrase",
        "agreement is not consent: 'yes, do it' must not apply anything",
        [
            Turn("fix it", gap=9),
            Turn("yes, do it", gap=12),
            Turn("go ahead please", gap=10),
        ],
        check_wrong_phrase,
    ),
    Scenario(
        "barge_in",
        "speaking over the read-back withdraws the proposed fix",
        [Turn("fix it", gap=9), Turn("no, wait, stop", gap=2.5, barge_in=True)],
        check_barge_in,
    ),
    Scenario(
        "hinglish",
        "a Hinglish request reaches the same tool",
        [Turn("isko fix kar do", voice="Kajal", gap=12)],
        check_hinglish,
    ),
    Scenario(
        "contract",
        "a Sleep Contract needs the read-back and then the exact phrase",
        [
            Turn("fix it", gap=9),
            Turn("approve fix {fix}", gap=14),
            Turn("grant contract for seven days", gap=12),
            Turn("grant contract for seven days", gap=12),
        ],
        check_contract,
    ),
    Scenario(
        "undo",
        "an applied fix can be reversed by phrase",
        [
            Turn("fix it", gap=9),
            Turn("approve fix {fix}", gap=14),
            Turn("undo fix {fix}", gap=22),
        ],
        check_undo,
    ),
]


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def _get(url: str, headers: dict[str, str] | None = None, tries: int = 3) -> Any:
    """A home network blip should not fail a scenario; retry briefly."""
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=25) as resp:  # noqa: S310
                return json.loads(resp.read().decode())
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return None


def _post(url: str, body: dict[str, Any]) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"content-type": "application/json", "x-beacon-passcode": PASSCODE},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


_voice_cache: dict[tuple[str, str, bool, float], bytes] = {}


def speech(text: str, voice: str, garble: bool, lead: float = 0.7) -> bytes:
    """PCM16 at RATE for one line. `garble` muffles it so the ASR loses confidence."""
    key = (text, voice, garble, lead)
    if key in _voice_cache:
        return _voice_cache[key]
    import boto3

    pcm = (
        boto3.client("polly")
        .synthesize_speech(
            Text=text,
            OutputFormat="pcm",
            SampleRate="16000",
            VoiceId=voice,
            Engine="neural",
        )["AudioStream"]
        .read()
    )
    filters = f"aresample={RATE}"
    if garble:
        # a bad line: band-limited, quiet, with noise over it
        filters = (
            f"aresample={RATE},highpass=f=300,lowpass=f=1800,volume=0.35,"
            f"aecho=0.8:0.7:40:0.5"
        )
    lead = b"\x00\x00" * int(
        RATE * 0.7
    )  # a clean onset: turn detection clips a cold start
    out = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-af",
            filters,
            "-f",
            "s16le",
            "-ar",
            str(RATE),
            "-ac",
            "1",
            "pipe:1",
        ],
        input=pcm,
        capture_output=True,
        check=True,
    ).stdout
    _voice_cache[key] = out
    return out


def system_prompt() -> str:
    src = (ROOT / "web/src/components/TalkDuplex.tsx").read_text()
    block = src.split("const SYSTEM_PROMPT_HINT =", 1)[1].split(";\n", 1)[0]
    import re

    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', block)).replace('\\"', '"')


def brief(incident: dict[str, Any]) -> str:
    rca = incident.get("rca_json") or {}
    summary = rca.get("spoken_summary") or rca.get("summary", "")
    return (
        f"Incident {incident['incident_id']} on alarm {incident.get('alarm_name')}, "
        f"status {incident['status']}. {summary}"
    )


async def run_scenario(sc: Scenario, key: str, verbose: bool) -> tuple[Run, float]:
    import websockets

    if sc.fresh_incident:
        # The remediation loop of the previous scenario writes to the same incident
        # for a while (`make local` reuses an open incident for the same alarm).
        # Wait for it to finish, so one scenario cannot fail another.
        for _ in range(40):
            latest = _get(f"{LOCAL}/dash/incidents")["incidents"]
            if not latest or latest[0].get("status") != "remediating":
                break
            await asyncio.sleep(1.5)
        _post(f"{LOCAL}/local/break", {})
        await asyncio.sleep(1.5)
    incident = _get(f"{LOCAL}/dash/incidents")["incidents"][0]
    inc_id = incident["incident_id"]
    timeline_at_start = len(incident.get("timeline") or [])
    tools = json.loads((ROOT / "web/src/tools.json").read_text())
    token = _get(TOKEN_URL, {"Authorization": f"Bearer {key}"})["token"]

    run = Run(timeline_at_start=timeline_at_start)
    started = time.time()
    state = {"busy": False, "pending": 0, "last": ""}

    async with websockets.connect(
        f"{WS_URL}?token={token}", open_timeout=20, ping_interval=None
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "system_prompt": f"{system_prompt()}\n\n{brief(incident)}",
                        "greeting": "",
                        "input": {
                            "format": {"encoding": "audio/pcm"},
                            "turn_detection": {"vad_threshold": 0.5},
                            "transcription_mode": "balanced",
                            "language_codes": ["en", "hi"],
                            "keyterms": [
                                "approve fix one",
                                "approve fix two",
                                "undo fix one",
                                "grant contract for seven days",
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
        )

        pending: list[dict[str, Any]] = []

        async def reader() -> None:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "transcript.user":
                    state["last"] = str(msg.get("text", ""))
                    run.heard.append(state["last"])
                    if verbose:
                        print(f"      heard  {state['last']}")
                elif kind == "transcript.agent":
                    run.agent.append(str(msg.get("text", "")))
                elif kind == "reply.started":
                    state["busy"] = True
                elif kind == "tool.call":
                    name = str(msg.get("name"))
                    run.tools.append(name)
                    state["pending"] += 1
                    out = await asyncio.to_thread(
                        _post,
                        f"{LOCAL}/voice/tools/{name}",
                        {
                            "incident_id": inc_id,
                            "session_id": "voice-test",
                            "args": msg.get("arguments") or {},
                            "transcript": state["last"],
                            "channel": "assemblyai",
                        },
                    )
                    result = out.get("result", {})
                    run.tool_results.append(result)
                    if name == "propose_fix" and isinstance(result.get("fix_id"), int):
                        run.fix_id = int(result["fix_id"])
                    if verbose:
                        print(f"      tool   {name} → {str(out.get('result'))[:90]}")
                    pending.append(
                        {
                            "call_id": msg.get("call_id"),
                            "result": json.dumps(out.get("result", {})),
                        }
                    )
                    state["pending"] -= 1
                    if not state["busy"]:
                        for r in pending:
                            await ws.send(json.dumps({"type": "tool.result", **r}))
                        pending.clear()
                elif kind == "reply.done":
                    state["busy"] = False
                    if msg.get("status") == "interrupted":
                        run.interrupted += 1
                    for r in pending:
                        await ws.send(json.dumps({"type": "tool.result", **r}))
                    pending.clear()
                elif kind in ("session.error", "error"):
                    run.errors.append(json.dumps(msg)[:200])

        outbox: asyncio.Queue[bytes] = asyncio.Queue()
        silence = b"\x00" * CHUNK

        async def sender() -> None:
            while True:
                chunk = outbox.get_nowait() if not outbox.empty() else silence
                await ws.send(
                    json.dumps(
                        {
                            "type": "input.audio",
                            "audio": base64.b64encode(chunk).decode(),
                        }
                    )
                )
                await asyncio.sleep(0.05)

        rtask = asyncio.create_task(reader())
        stask = asyncio.create_task(sender())

        async def settle(limit: float) -> None:
            t = time.time()
            await asyncio.sleep(2.0)
            while (state["busy"] or state["pending"]) and time.time() - t < limit:
                await asyncio.sleep(0.2)

        await asyncio.sleep(2.0)
        await settle(20)  # the greeting, if any
        await asyncio.sleep(1.0)
        for turn in sc.turns:
            if turn.barge_in:
                # wait until the agent is mid-sentence, then talk over it
                t = time.time()
                while not state["busy"] and time.time() - t < 30:
                    await asyncio.sleep(0.2)
                await asyncio.sleep(turn.gap)
            else:
                await settle(turn.gap + 25)
                await asyncio.sleep(1.0)  # let the reply's audio drain before speaking
            text = turn.text.replace("{fix}", spoken_number(run.fix_id))
            lead = 0.05 if turn.barge_in else 0.7
            pcm = await asyncio.to_thread(speech, text, turn.voice, turn.garble, lead)
            if verbose:
                print(f"      say    {text!r}")
            for i in range(0, len(pcm), CHUNK):
                outbox.put_nowait(pcm[i : i + CHUNK])
            while not outbox.empty():
                await asyncio.sleep(0.1)
        await settle(45)
        await asyncio.sleep(3)
        await ws.send(json.dumps({"type": "session.end"}))
        stask.cancel()
        rtask.cancel()

    run.incident = _get(f"{LOCAL}/dash/incidents/{inc_id}")["incident"]
    run.contracts = len(_get(f"{LOCAL}/dash/contracts")["contracts"])
    return run, time.time() - started


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--json", default="")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "")
    if not key:
        print("set ASSEMBLYAI_API_KEY", file=sys.stderr)
        return 2

    picked = [s for s in SCENARIOS if not a.only or s.name in a.only]
    print(f"\n  Voice regression: {len(picked)} scenario(s) against {LOCAL}\n")
    report, failures = [], 0
    for sc in picked:
        print(f"  ▸ {sc.name}: {sc.why}")
        try:
            run, secs = await run_scenario(sc, key, a.verbose)
            problems = sc.check(run) + [f"socket: {e}" for e in run.errors]
        except Exception as exc:  # a crashed scenario is a failure, not a stack trace
            run, secs, problems = Run(), 0.0, [f"{type(exc).__name__}: {exc}"]
        ok = not problems
        failures += 0 if ok else 1
        verdict = "PASS" if ok else "FAIL"
        print(f"    {verdict}  {secs:.0f}s  heard={run.heard}")
        print(f"          tools={run.tools}")
        for p in problems:
            print(f"      ✗ {p}")
        print()
        report.append(
            {
                "scenario": sc.name,
                "why": sc.why,
                "pass": ok,
                "seconds": round(secs, 1),
                "heard": run.heard,
                "tools": run.tools,
                "problems": problems,
                "status": run.incident.get("status"),
            }
        )

    print(f"  {len(picked) - failures}/{len(picked)} passed\n")
    if a.json:
        Path(a.json).write_text(
            json.dumps(
                {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": report},
                indent=1,
            )
        )
        print(f"  report → {a.json}\n")
    return failures


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
