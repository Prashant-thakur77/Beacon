"""Regression tests for the phone channel — spoken, over a telephone-quality line.

`scripts/voice_test.py` proves the browser path. This proves the phone path, which
is a different set of risks: audio degraded to 8 kHz mu-law, a caller who cannot see
a screen, a keypad that must never approve anything, and a recording that has to be
attributable afterwards.

Each scenario places a scripted call against `make local` through the real
AssemblyAI Voice Agent API, then asserts on what was heard, which tools ran, the
state the incident ended in, and — for the calls that change something — which
channel of the recording the approval came from.

    make local                                   # in another shell
    ASSEMBLYAI_API_KEY=… .venv/bin/python scripts/phone_test.py
    … scripts/phone_test.py --only barge_in

The exit code is the number of failures. Polly speaks the caller's lines (AWS
credentials needed); everything else is local.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from beacon.phone import server, tools
from beacon.phone.bridge import PhoneBridge
from beacon.phone.providers.replay import ReplayLeg

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parent.parent
LOCAL = "http://localhost:8000"
PASSCODE = "local"


@dataclass
class Run:
    """Everything one call produced, for the checks to read."""

    scenario: str = ""
    incident_id: str = ""
    heard: list[str] = field(default_factory=list)
    said: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    dtmf: list[str] = field(default_factory=list)
    interruptions: int = 0
    cleared: int = 0
    ttfa_ms: list[float] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    spoken: list[dict[str, Any]] = field(default_factory=list)
    incident: dict[str, Any] = field(default_factory=dict)
    approvals: int = 0
    attestation: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0
    wav: Path | None = None
    error: str = ""

    def ran(self, name: str) -> bool:
        return any(t["name"] == name for t in self.tools)

    def result_of(self, name: str) -> dict[str, Any]:
        for event in reversed(self.tools):
            if event["name"] == name:
                return dict(event.get("result") or {})
        return {}

    def agent_said(self, *needles: str) -> bool:
        joined = " ".join(self.said).lower()
        return any(n.lower() in joined for n in needles)


# ---------------------------------------------------------------------------
# What each call has to prove
# ---------------------------------------------------------------------------


def check_approve(run: Run) -> list[str]:
    bad = []
    if not run.ran("propose_fix"):
        bad.append("never proposed a fix")
    if not run.ran("approve_fix"):
        bad.append("the spoken phrase did not reach approve_fix")
    elif not run.result_of("approve_fix").get("approved"):
        bad.append(f"approve_fix refused: {run.result_of('approve_fix')}")
    if run.incident.get("status") != "resolved":
        bad.append(f"incident ended {run.incident.get('status')}, not resolved")
    if run.approvals < 1:
        bad.append("nothing was written to the approvals ledger")
    if run.attestation and not run.attestation.get("ok"):
        bad.append(f"attestation: {run.attestation.get('problems')}")
    if run.attestation:
        check = next(
            (c for c in run.attestation["checks"] if c["tool"] == "approve_fix"), None
        )
        if check and check["caller_occurrences"] < 1:
            bad.append("the approval is not on the caller's channel")
    return bad


def check_wrong_phrase(run: Run) -> list[str]:
    """Agreement is not consent, on a phone as in a browser."""
    bad = []
    if not run.ran("propose_fix"):
        bad.append("never proposed a fix")
    if run.result_of("approve_fix").get("approved"):
        bad.append("'yes, do it' applied a change")
    if run.approvals:
        bad.append("an approval was recorded for words that were not the phrase")
    if run.incident.get("status") == "resolved":
        bad.append("the incident was resolved without the phrase")
    if not run.agent_said("approve fix"):
        bad.append("the agent never told the caller the phrase to say")
    return bad


def check_barge_in(run: Run) -> list[str]:
    bad = []
    if run.interruptions < 1:
        bad.append("speaking over the agent did not register as an interruption")
    if run.cleared < 1:
        bad.append("the carrier was never told to drop the queued audio")
    if run.result_of("approve_fix").get("approved"):
        bad.append("the phrase still worked after the proposal was withdrawn")
    if run.incident.get("status") == "resolved":
        bad.append("a withdrawn fix was applied anyway")
    return bad


def check_hinglish(run: Run) -> list[str]:
    bad = []
    if not run.heard:
        bad.append("nothing was transcribed")
    if not run.ran("propose_fix"):
        bad.append("'isko fix kar do' did not reach propose_fix")
    return bad


def check_keypad(run: Run) -> list[str]:
    """A keypress may acknowledge. It may never approve.

    The caller never says the approval phrase in this call, so anything that got
    applied was applied by a keypress -- which is the failure being hunted.
    """
    bad = []
    if run.dtmf != ["1", "5"]:
        bad.append(f"the keypresses did not arrive: {run.dtmf}")
    if not run.agent_said("acknowledg"):
        bad.append("pressing 1 did not acknowledge")
    if not run.agent_said("keypress", "cannot approve", "say"):
        bad.append("pressing 5 was not refused in words")
    if not run.ran("propose_fix"):
        bad.append("the spoken line never reached propose_fix")
    if run.ran("approve_fix"):
        bad.append("a keypress reached approve_fix")
    if run.approvals:
        bad.append("an approval was recorded without anyone speaking the phrase")
    if run.incident.get("status") == "resolved":
        bad.append("the incident was resolved by a keypress")
    return bad


CHECKS: dict[str, Callable[[Run], list[str]]] = {
    "approve": check_approve,
    "wrong_phrase": check_wrong_phrase,
    "barge_in": check_barge_in,
    "hinglish": check_hinglish,
    "keypad": check_keypad,
}


# ---------------------------------------------------------------------------
# The local stack
# ---------------------------------------------------------------------------


def _req(method: str, path: str, body: Any = None) -> Any:
    req = urllib.request.Request(
        f"{LOCAL}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"content-type": "application/json", "x-beacon-passcode": PASSCODE},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.URLError:
            if attempt == 2:
                raise
            time.sleep(2)
    return {}


def revoke_contracts() -> int:
    """A contract granted on one call silently fixes the next incident."""
    contracts = _req("GET", "/dash/contracts").get("contracts", [])
    for contract in contracts:
        _req("DELETE", f"/dash/contracts/{contract['contract_id']}")
    return len(contracts)


def fresh_incident() -> dict[str, Any]:
    _req("POST", "/local/break")
    for _ in range(30):
        time.sleep(1)
        incidents = _req("GET", "/dash/incidents").get("incidents", [])
        if incidents and incidents[0].get("status") in (
            "awaiting_engineer",
            "awaiting_approval",
        ):
            return dict(incidents[0])
    raise RuntimeError("the local stack did not produce an incident to call about")


def approvals_for(incident_id: str) -> int:
    rows = _req("GET", "/dash/audit").get("rows", [])
    return sum(1 for row in rows if row.get("incident_id") == incident_id)


# ---------------------------------------------------------------------------
# One call
# ---------------------------------------------------------------------------


async def place(scenario: str, incident_id: str, out_dir: Path, attest: bool) -> Run:
    from beacon.phone.__main__ import CallState, build_lines

    run = Run(scenario=scenario, incident_id=incident_id)
    # The caller says the fix number the agent actually proposed. Proposals are
    # numbered per incident, so a script hard-coding "one" is refused the moment an
    # incident has had an earlier proposal -- which looks exactly like a real bug.
    state = CallState()
    brief = tools.fetch_brief(
        f"{LOCAL}/voice", passcode=PASSCODE, incident_id=incident_id, channel="phone"
    )
    token = tools.mint_token(f"{LOCAL}/voice", passcode=PASSCODE)
    # A person waits a beat longer than 900 ms after a long read-back before saying
    # the phrase that applies a change. The shorter gap put the approval on top of a
    # turn the agent had not finished, and the agent answered neither it nor the
    # line after it — visible in the recording, where the caller says four things
    # and the agent replies three times.
    leg = ReplayLeg(
        build_lines(scenario, state), quiet_ms=1600, tail_s=2.5, max_seconds=220
    )

    def note(kind: str, data: dict[str, Any]) -> None:
        # Keep the whole stream: when a scenario fails it is almost always the
        # order of events that explains it, and nothing else recovers that later.
        run.events.append(
            {"kind": kind, **{k: v for k, v in data.items() if k != "result"}}
        )
        if kind == "heard":
            run.heard.append(str(data.get("text") or ""))
        elif kind == "said":
            run.said.append(str(data.get("text") or ""))
        elif kind == "tool":
            run.tools.append(dict(data))
            state.note(str(data.get("name")), dict(data.get("result") or {}))

    bridge = PhoneBridge(
        leg,
        brief=brief,
        token=token,
        run_tool=tools.http_runner(
            f"{LOCAL}/voice",
            passcode=PASSCODE,
            incident_id=incident_id,
            session_id=f"phone-{scenario}",
        ),
        on_event=note,
    )
    record = await bridge.run()
    run.spoken = leg.timeline
    run.dtmf = record.dtmf
    run.interruptions = record.interruptions
    run.cleared = leg.cleared
    run.ttfa_ms = record.ttfa_ms
    run.seconds = record.seconds()

    if bridge.recorder is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        run.wav = out_dir / f"{scenario}.wav"
        wav = bridge.recorder.wav()
        run.wav.write_bytes(wav)
        if attest:
            try:
                run.attestation = await asyncio.to_thread(
                    server.attest_call, wav, [t["name"] for t in record.tools], brief
                )
            except Exception as exc:
                run.error = f"attestation unavailable: {exc}"

    # Let a verify loop finish before reading the incident, or the state is a race.
    await asyncio.sleep(3)
    run.incident = _req("GET", f"/dash/incidents/{incident_id}").get("incident", {})
    run.approvals = approvals_for(incident_id)
    return run


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", action="append", default=[], choices=sorted(CHECKS))
    ap.add_argument("--out", default=str(Path.home() / "beacon-video/phone"))
    ap.add_argument("--json", default="")
    ap.add_argument("--no-attest", dest="attest", action="store_false")
    args = ap.parse_args()

    picked = args.only or list(CHECKS)
    print(f"\n  Phone regression: {len(picked)} call(s) against {LOCAL}\n")
    results: list[dict[str, Any]] = []
    failures = 0

    for scenario in picked:
        print(f"  ▸ {scenario}")
        revoked = revoke_contracts()
        if revoked:
            print(f"    (revoked {revoked} contract(s) first)")
        try:
            incident = fresh_incident()
            run = await place(
                scenario, incident["incident_id"], Path(args.out), args.attest
            )
            problems = CHECKS[scenario](run)
        except Exception as exc:
            run = Run(scenario=scenario, error=str(exc))
            problems = [f"{type(exc).__name__}: {exc}"]

        verdict = "PASS" if not problems else "FAIL"
        failures += bool(problems)
        print(
            f"    {verdict}  {run.seconds:.0f}s  tools={[t['name'] for t in run.tools]}"
        )
        for line in run.heard:
            print(f"          heard {line!r}")
        if run.attestation.get("checks"):
            for check in run.attestation["checks"]:
                mark = "ok" if check["ok"] else "✗"
                counts = (
                    f"({check['caller_occurrences']}x; "
                    f"agent {check['agent_occurrences']}x)"
                )
                print(
                    f"          {mark} {check['tool']} "
                    f"from the caller's channel {counts}"
                )
        for problem in problems:
            print(f"      ✗ {problem}")
        if run.error:
            print(f"      · {run.error}")
        results.append(
            {
                "scenario": scenario,
                "ok": not problems,
                "problems": problems,
                "incident_id": run.incident_id,
                "status": run.incident.get("status"),
                "tools": [t["name"] for t in run.tools],
                "heard": run.heard,
                "dtmf": run.dtmf,
                "interruptions": run.interruptions,
                "carrier_clears": run.cleared,
                "ttfa_ms": run.ttfa_ms,
                "seconds": run.seconds,
                "events": run.events,
                "spoken": run.spoken,
                "recording": str(run.wav) if run.wav else None,
                "attestation": run.attestation,
            }
        )

    print(f"\n  {len(picked) - failures}/{len(picked)} passed\n")
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "channel": "phone",
                    "results": results,
                },
                indent=2,
                default=str,
            )
        )
        print(f"  report → {args.json}\n")
    return failures


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
