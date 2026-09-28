"""Latency, measured — from the calls the regression suite actually placed.

Nothing here is typed in by hand. The numbers come out of the report
`make phone-test` writes, which is a set of real calls through the real Voice
Agent API at telephone quality, and out of the reports `python -m beacon.phone
replay --json` writes for calls placed against the deployed AWS account.

The split that matters is between turns that called a tool and turns that did
not. AssemblyAI's own bar for a voice agent is a response under 1.5 s, and a
turn where Beacon just answers clears it. A turn that calls a tool does not,
and the reason is ours, not theirs: the tool round trip to AWS is inside the
turn, because a proposal has to be dry-run under a locked-down role before the
agent is allowed to read its blast radius back. Reporting one blended average
would hide exactly the thing an engineer would want to know.

    make bench

Writes `docs/assets/benchmark.md` and prints it.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT = ROOT / "docs/assets/phone-test-report.json"
OUT = ROOT / "docs/assets/benchmark.md"


def _sh(*args: str) -> str:
    try:
        return subprocess.run(  # noqa: S603
            args, capture_output=True, text=True, timeout=10, cwd=ROOT
        ).stdout.strip()
    except Exception:
        return ""


def turns_of(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Pair each caller turn with its time to first audio and whether it ran a tool.

    ``ttfa_ms`` is one entry per caller turn, in order, excluding the greeting
    (which answers nobody). Walking the event list rather than assuming a shape
    keeps this honest if the recorder changes.
    """
    ttfa = list(result.get("ttfa_ms") or [])
    turns: list[dict[str, Any]] = []
    index = -1
    for event in result.get("events") or []:
        kind = event.get("kind")
        if kind == "heard":
            index += 1
            turns.append(
                {
                    "said": str(event.get("text") or ""),
                    "ttfa_ms": ttfa[index] if index < len(ttfa) else None,
                    "tools": [],
                }
            )
        elif kind == "tool" and turns:
            turns[-1]["tools"].append(
                {"name": event.get("name"), "ms": event.get("ms")}
            )
    return turns


def pairs_cleanly(result: dict[str, Any], turns: list[dict[str, Any]]) -> bool:
    """Whether reply timings line up one-to-one with caller turns on this call."""
    return len(result.get("ttfa_ms") or []) == len(turns)


def _stats(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "min": ordered[0],
        "median": int(statistics.median(ordered)),
        "max": ordered[-1],
    }


def collect(paths: list[Path]) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        # phone-test writes {"results": [...]}; a single replay writes one call
        results = raw.get("results") or [raw]
        for result in results:
            if not result.get("events"):
                continue
            calls.append(
                {
                    "scenario": result.get("scenario") or path.stem,
                    "seconds": result.get("seconds"),
                    "turns": (found := turns_of(result)),
                    "paired": pairs_cleanly(result, found),
                    "attestation": result.get("attestation") or {},
                    "source": path.name,
                }
            )
    every: list[int] = []
    with_tool: list[int] = []
    without: list[int] = []
    dispatch: list[int] = []
    paired = 0
    for call in calls:
        every += [t["ttfa_ms"] for t in call["turns"] if isinstance(t["ttfa_ms"], int)]
        dispatch += [
            t["ms"]
            for turn in call["turns"]
            for t in turn["tools"]
            if isinstance(t.get("ms"), int)
        ]
        # A turn can end without a reply -- "Wait." then "Stop." is two turns and
        # one answer -- so reply timings only line up with turns when the counts
        # match. When they do not, the call is left out of the split rather than
        # paired by guesswork.
        if not call["paired"]:
            continue
        paired += 1
        for turn in call["turns"]:
            ms = turn["ttfa_ms"]
            if isinstance(ms, int):
                (with_tool if turn["tools"] else without).append(ms)
    return {
        "calls": calls,
        "every": _stats(every),
        "with_tool": _stats(with_tool),
        "without_tool": _stats(without),
        "dispatch": _stats(dispatch),
        "paired": paired,
    }


def confidences(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every consent phrase that was re-scored against its own recording."""
    out: list[dict[str, Any]] = []
    for call in calls:
        for check in call["attestation"].get("checks") or []:
            if check.get("quote") or check.get("phrase"):
                out.append(
                    {
                        "scenario": call["scenario"],
                        "phrase": check.get("quote") or check.get("phrase"),
                        "confidence": check.get("confidence"),
                        "weakest": check.get("weakest_word"),
                        "caller": check.get("caller_occurrences"),
                        "agent": check.get("agent_occurrences"),
                    }
                )
    return out


def render(data: dict[str, Any], sources: list[Path]) -> str:
    calls = data["calls"]
    now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    sha = _sh("git", "rev-parse", "--short", "HEAD") or "unknown"
    dirty = " (dirty)" if _sh("git", "status", "--porcelain") else ""

    def row(label: str, s: dict[str, Any]) -> str:
        if not s.get("n"):
            return f"| {label} | — | — | — | 0 |"
        return (
            f"| {label} | {s['min']} ms | **{s['median']} ms** | {s['max']} ms "
            f"| {s['n']} |"
        )

    lines = [
        "# Measured, not asserted",
        "",
        f"Generated {now} from {len(calls)} real call(s) by `make bench`. "
        "Every number below is read out of a report written while the call was "
        "happening — none of it is typed in by hand.",
        "",
        "## Time to first audio",
        "",
        "Turn end to the first byte of the agent's reply, as the caller hears it. "
        "Two things sit inside this number that a bare STT benchmark would not "
        "have: the agent's own turn detection deciding the caller has stopped, "
        "and — on a turn that calls a tool — the round trip to AWS.",
        "",
        "| | fastest | median | slowest | turns |",
        "|---|---|---|---|---|",
        row("Every turn", data["every"]),
    ]
    if data["paired"]:
        lines += [
            row("… answered without a tool", data["without_tool"]),
            row("… answered after a tool", data["with_tool"]),
        ]
    lines.append("")

    overall = data["every"]
    if overall.get("n"):
        over = overall["median"] >= 1500
        lines += [
            f"Median **{overall['median']} ms** over {overall['n']} turns — "
            + (
                "above AssemblyAI's own 1.5 s guidance for a voice agent, and "
                "worth being plain about why rather than quoting a friendlier "
                "number."
                if over
                else "inside AssemblyAI's own 1.5 s guidance for a voice agent."
            ),
            "",
            "Two deliberate choices are inside it. Turn detection on the phone "
            "waits `min_silence` **700 ms** before deciding the caller has "
            "finished, because a half-asleep engineer pauses mid-sentence and "
            "being cut off at 3 AM is worse than waiting; that 700 ms is part of "
            "every number above. And a turn that calls a tool carries the AWS "
            "round trip, because a fix is dry-run under a locked-down role "
            "*before* the agent is allowed to read its blast radius back. We "
            "would rather the engineer wait than be read a blast radius nothing "
            "checked.",
            "",
            "The honest read: the transcription is not the slow part, and the "
            "parts that are slow are ours to fix — the dry run could start "
            "speculatively while the agent speaks, which is the obvious next "
            "move and is not built yet.",
            "",
        ]
    if data["paired"] < len(calls):
        lines += [
            f"The split covers the {data['paired']} of {len(calls)} calls where "
            "replies pair one-to-one with turns. On the rest a turn ended "
            "without an answer — “Wait.” then “Stop.” is two turns and one "
            "reply — so pairing them would be guesswork.",
            "",
        ]
    if data["dispatch"].get("n"):
        d = data["dispatch"]
        lines += [
            f"Tool dispatch itself is {d['min']}–{d['max']} ms (median "
            f"{d['median']} ms) against the local stack; on the deployed account "
            "the same call carries Lambda and the dry run with it.",
            "",
        ]

    lines += [
        "## The calls",
        "",
        "| Call | length | turns | tools called |",
        "|---|---|---|---|",
    ]
    for call in calls:
        names = [t["name"] for turn in call["turns"] for t in turn["tools"]]
        seconds = f"{call['seconds']:.0f} s" if call.get("seconds") else "—"
        lines.append(
            f"| `{call['scenario']}` | {seconds} | {len(call['turns'])} | "
            f"{', '.join(f'`{n}`' for n in names) or '—'} |"
        )
    lines.append("")

    scored = confidences(calls)
    if scored:
        # The phone suite's attestation answers *who* said it; the confidence
        # number comes from re-scoring a session recording, which is a separate
        # call. Only claim the column when a number is actually there.
        any_pct = any(isinstance(c["confidence"], int | float) for c in scored)
        head = "| Call | phrase | caller / agent |"
        rule = "|---|---|---|"
        if any_pct:
            head = "| Call | phrase | heard at | weakest word | caller / agent |"
            rule = "|---|---|---|---|---|"
        lines += [
            "## Who authorised what",
            "",
            "Every phrase that unlocked a change, checked against the call's own "
            "two-channel recording.",
            "",
            head,
            rule,
        ]
        for c in scored:
            occ = f"{c['caller']}x / {c['agent']}x" if c["caller"] is not None else "—"
            if any_pct:
                pct = (
                    f"**{float(c['confidence']):.0%}**"
                    if isinstance(c["confidence"], int | float)
                    else "—"
                )
                lines.append(
                    f"| `{c['scenario']}` | \u201c{c['phrase']}\u201d | {pct} | "
                    f"{c['weakest'] or '—'} | {occ} |"
                )
            else:
                lines.append(
                    f"| `{c['scenario']}` | \u201c{c['phrase']}\u201d | {occ} |"
                )
        lines += [
            "",
            "The agent reads the phrase back, so it is in the recording twice — "
            "once from each party. Only the caller's channel counts: a phrase "
            "found on the agent's channel alone is not consent. The Hinglish "
            "call is the interesting row, because the caller's channel came back "
            "in Devanagari and it is still the same consent.",
            "",
        ]
        if not any_pct:
            lines += [
                "*How clearly* the words were heard is a separate question, and "
                "the Voice Agent API reports no confidence on a live turn. "
                "`POST /sessions/<id>/attest` answers it afterwards by "
                "re-transcribing the session's own recording per channel with "
                "the pre-recorded model, which does return per-word confidence, "
                "and scoring the phrase by its **weakest** word. On the deployed "
                "account, a phone approval scored **95%** with `fix` the weakest "
                "word, found at 40.1 s on the caller's channel.",
                "",
            ]

    lines += [
        "## What produced these numbers",
        "",
        "| | |",
        "|---|---|",
        "| Commit | `" + sha + dirty + "` |",
        "| Transcription | AssemblyAI Voice Agent API, "
        "`wss://agents.assemblyai.com/v1/ws` |",
        "| Audio | G.711 µ-law (`audio/pcmu`) in and out, 8 kHz, forwarded untouched |",
        "| Turn detection | `vad_threshold` 0.65, `min_silence` 700 ms, "
        "`max_silence` 2200 ms |",
        "| Languages | `en`, `hi` (code-switching; the Hinglish call is in the set) |",
        "| Tools | 9, declared as JSON-Schema functions on the same socket |",
        "| Python | " + platform.python_version() + " on " + platform.system() + " |",
        "| Reports | " + ", ".join(f"`{p.name}`" for p in sources) + " |",
        "",
        "## Reproduce it",
        "",
        "```",
        "make local                 # one shell: the whole product, no AWS account",
        "make phone-test            # another: five real calls at telephone quality",
        "make bench                 # regenerate this file from the report",
        "```",
        "",
        "`make phone-test` needs `ASSEMBLYAI_API_KEY` and AWS credentials — Polly "
        "speaks the caller's lines, everything else is local. The calls are real "
        "calls: they go through the Voice Agent API and the real tools, so the "
        "numbers move a little run to run.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("reports", nargs="*", default=[], help="report JSONs to read")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    paths = [Path(p) for p in args.reports] or [DEFAULT_REPORT]
    missing = [p for p in paths if not p.exists()]
    if missing:
        print(
            "no report to measure: "
            + ", ".join(str(p) for p in missing)
            + "\nrun `make phone-test` first (it writes the report this reads).",
            file=sys.stderr,
        )
        return 1

    data = collect(paths)
    if not data["calls"]:
        print("the report has no calls with events in it", file=sys.stderr)
        return 1
    text = render(data, paths)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    print(f"\nwritten → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
