"""Turn a real call recording into the film's `call` scene data.

The scene draws two waveforms and quotes what was said. Both come from the WAV and
the report `make phone-test` writes, so the shape of the speech on screen is the
shape of the speech that happened — not an illustration of it.

    .venv/bin/python scripts/call_scene_data.py [scenario] [report.json]
"""

from __future__ import annotations

import array
import json
import math
import pathlib
import sys
import wave

BUCKETS = 720
ROOT = pathlib.Path(__file__).resolve().parent.parent


def envelope(channel: array.array[int]) -> list[float]:
    """RMS per bucket, peak-normalised per channel so both are readable."""
    step = max(len(channel) // BUCKETS, 1)
    out = []
    for i in range(BUCKETS):
        chunk = channel[i * step : (i + 1) * step]
        rms = math.sqrt(sum(s * s for s in chunk) / len(chunk)) if chunk else 0.0
        out.append(rms / 32768)
    peak = max(out) or 1.0
    return [round(min(v / peak, 1.0), 3) for v in out]


def main() -> int:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "approve"
    report_path = pathlib.Path(
        sys.argv[2]
        if len(sys.argv) > 2
        else ROOT / "docs/assets/phone-test-report.json"
    )
    report = json.loads(report_path.read_text())
    run = next(r for r in report["results"] if r["scenario"] == scenario)
    wav_path = pathlib.Path(run["recording"])

    with wave.open(str(wav_path)) as handle:
        rate, frames = handle.getframerate(), handle.getnframes()
        raw = array.array("h")
        raw.frombytes(handle.readframes(frames))

    attestation = run.get("attestation") or {}
    data = {
        "scenario": scenario,
        "seconds": round(frames / rate, 1),
        "rate": rate,
        "caller": envelope(raw[0::2]),
        "agent": envelope(raw[1::2]),
        "caller_said": attestation.get("caller_said", run.get("heard", [])),
        "agent_said": attestation.get("agent_said", []),
        "tools": run["tools"],
        "checks": attestation.get("checks", []),
    }
    for out in (
        ROOT / "video/scenes/call.json",
        pathlib.Path.home() / "beacon-video/scenes/call.json",
    ):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data))
    check = data["checks"][0] if data["checks"] else None
    print(
        f"{wav_path.name}: {data['seconds']}s · "
        f"{len(data['caller_said'])} caller lines · "
        f"tools {' → '.join(data['tools'])}"
        + (
            f" · {check['tool']} on the caller's channel "
            f"({check['caller_occurrences']}x; agent {check['agent_occurrences']}x)"
            if check
            else " · no consent tool ran"
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
