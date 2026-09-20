"""Record a full scripted night from ``make local`` into the replay bundle.

Drives the same lines "Run the night" types, over the local HTTP API, and
writes ``web/public/replay/incident-001.json`` with everything the console can
show without a server: incidents, turns, contracts, tally, safety, the audit
rows, the morning report and a server-rendered postmortem per incident.

Usage: PORT=8765 PASSCODE=local .venv/bin/python scripts/export_night.py
(the local server must be freshly started so the night starts clean).
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BASE = f"http://localhost:{os.environ.get('PORT', '8765')}"
PASS = os.environ.get("PASSCODE", "local")
OUT = Path(__file__).parent.parent / "web" / "public" / "replay" / "incident-001.json"
LINES = ["can you fix it", "approve fix 1", "yes", "grant contract for seven days"]


def call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"content-type": "application/json", "x-beacon-passcode": PASS},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw.startswith(("{", "[")) else raw


def turn(incident_id: str, session: str, **body: Any) -> dict[str, Any]:
    out = call(
        "POST",
        "/voice/turn",
        {"incident_id": incident_id, "session_id": session, **body},
    )
    assert "reply_text" in out, out
    return dict(out)


def main() -> None:
    first = call("GET", "/dash/incidents")["incidents"][0]["incident_id"]
    turns: dict[str, list[dict[str, Any]]] = {first: []}
    pace = float(
        os.environ.get("PACE_SECONDS", "25")
    )  # the loop is inline; let the clock move
    time.sleep(pace)
    turns[first].append(turn(first, "night", mode="brief"))
    for line in LINES:
        time.sleep(pace)
        turns[first].append(turn(first, "night", text=line, channel="typed"))
        status = call("GET", f"/dash/incidents/{first}")["incident"]["status"]
        if line == "approve fix 1" and status == "resolved":
            turns[first].append(turn(first, "night", mode="event", event="resolved"))
    time.sleep(pace)
    call("POST", "/local/break", {})
    time.sleep(pace)
    incidents = call("GET", "/dash/incidents")["incidents"]
    second = next(i["incident_id"] for i in incidents if i["incident_id"] != first)
    turns[second] = [turn(second, "night2", mode="brief")]

    full = [
        call("GET", f"/dash/incidents/{i['incident_id']}")["incident"]
        for i in incidents
    ]
    from beacon import reports  # the night these incidents belong to, not "latest"

    night = reports.night_of(full[0]["timestamp"])
    bundle = {
        "recorded_at": datetime.now(tz=UTC).isoformat(),
        "incidents": full,
        "turns": turns,
        "contracts": call("GET", "/dash/contracts")["contracts"],
        "tally": call("GET", "/dash/tally"),
        "safety": call("GET", "/dash/safety"),
        "analytics": call("GET", "/dash/analytics"),
        "audit": call("GET", "/dash/audit")["rows"],
        "report": call("GET", f"/dash/report/latest?night={night}"),
        "postmortems": {
            i["incident_id"]: call(
                "GET", f"/dash/incidents/{i['incident_id']}/postmortem"
            )
            for i in incidents
        },
    }
    OUT.write_text(json.dumps(bundle, indent=1, default=str) + "\n")
    n_turns = sum(len(v) for v in turns.values())
    print(
        f"wrote {OUT} · {len(full)} incidents · {n_turns} turns · "
        f"{len(bundle['audit'])} audit rows · {bundle['tally']['humans_woken']} woken"
    )


if __name__ == "__main__":
    main()
