"""Build ``web/public/replay/incident-001.json`` from real captured runs.

``make capture-run`` saves ``tests/fixtures/real/incident-<ts>.json`` (one
incident item each, conversation included) and ``contracts.json``.  This
turns them into the console's replay bundle: public incident fields only,
the conversation replayed as agent turns, account ids and ARNs redacted.

    make build-replay      # then make console-config to publish it
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from beacon.dashboard_api import SAFETY_RULES, redact
from beacon.remediation import registry

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REAL = ROOT / "tests" / "fixtures" / "real"
DEFAULT_OUT = ROOT / "web" / "public" / "replay" / "incident-001.json"
_PRIVATE = ("rca", "conversation", "cached_data")
_CITATION_RE = re.compile(r"\[(E\d+)\]")


def _load_incidents(real: Path) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(real.glob("incident-*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("incident_id"):
            rows[str(data["incident_id"])] = data  # latest capture of an id wins
    return sorted(
        rows.values(), key=lambda r: str(r.get("timestamp", "")), reverse=True
    )


def _turns(incident: dict[str, Any]) -> list[dict[str, Any]]:
    """Replay the stored Converse-shaped conversation as TurnResponse objects."""
    turns: list[dict[str, Any]] = []
    tool_events: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    public = _public(incident)
    for message in incident.get("conversation") or []:
        for block in message.get("content", []):
            if "toolUse" in block:
                use = block["toolUse"]
                pending[str(use.get("toolUseId"))] = {
                    "name": use.get("name"),
                    "args": use.get("input") or {},
                }
            elif "toolResult" in block:
                res = block["toolResult"]
                call = pending.pop(str(res.get("toolUseId")), {"name": "?", "args": {}})
                payload = (res.get("content") or [{}])[0].get("json") or {}
                cards = payload.get("evidence") if isinstance(payload, dict) else None
                first_id = None
                for card in cards or []:
                    if isinstance(card, dict) and card.get("id"):
                        evidence.append(card)
                        first_id = first_id or card["id"]
                summary = payload.get("summary") if isinstance(payload, dict) else None
                tool_events.append(
                    {
                        "name": call["name"],
                        "args": call["args"],
                        "summary": str(
                            summary or payload.get("instruction", "")
                            if isinstance(payload, dict)
                            else ""
                        )[:120],
                        "evidence_id": first_id,
                    }
                )
            elif "text" in block and message.get("role") == "assistant":
                text = str(block["text"])
                turns.append(
                    {
                        "reply_text": text,
                        "spoken_text": _CITATION_RE.sub("", text)
                        .replace("  ", " ")
                        .strip(),
                        "cited": _CITATION_RE.findall(text),
                        "audio_b64": None,
                        "speech_marks": [],
                        "voice": None,
                        "tts_error": "replay: browser voice",
                        "tool_events": tool_events,
                        "evidence": evidence,
                        "incident": public,
                        "turn": len(turns) + 1,
                    }
                )
                tool_events, evidence = [], []
    return turns


def _public(incident: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in incident.items() if k not in _PRIVATE}


def _minutes(a: Any, b: Any) -> float | None:
    try:
        return round(
            (
                datetime.fromisoformat(str(b)) - datetime.fromisoformat(str(a))
            ).total_seconds()
            / 60,
            1,
        )
    except ValueError:
        return None


def build_bundle(real: Path) -> dict[str, Any]:
    incidents = _load_incidents(real)
    contracts: list[dict[str, Any]] = []
    cpath = real / "contracts.json"
    if cpath.exists():
        try:
            contracts = json.loads(cpath.read_text()).get("contracts", [])
        except json.JSONDecodeError:
            contracts = []
    resolved = [i for i in incidents if i.get("status") == "resolved"]
    durations = [
        m
        for m in (_minutes(i.get("timestamp"), i.get("resolved_at")) for i in resolved)
        if m is not None
    ]
    tally = {
        "incidents_handled": len(incidents),
        "resolved": len(resolved),
        "escalated": sum(1 for i in incidents if i.get("status") == "escalated"),
        "humans_woken": sum(1 for i in incidents if i.get("woken", True)),
        "handled_by_contract": sum(
            1 for i in incidents if i.get("handled_by") == "contract"
        ),
        "median_minutes_to_recovery": statistics.median(durations)
        if durations
        else None,
    }
    bundle = {
        "recorded_at": min(
            (str(i.get("timestamp")) for i in incidents),
            default=datetime.now().isoformat(),
        ),
        "incidents": [_public(i) for i in incidents],
        "turns": {str(i["incident_id"]): _turns(i) for i in incidents},
        "contracts": contracts,
        "tally": tally,
        "safety": {
            "allowlist": [
                {
                    "id": s.id,
                    "description": s.description,
                    "params": {k: t.__name__ for k, t in s.params_schema.items()},
                    "iam_actions": list(s.iam_actions),
                }
                for s in registry.REGISTRY.values()
            ],
            "apply_enabled": {"triage": True, "voice": True, "remediate": True},
            "rules": SAFETY_RULES,
        },
    }
    return redact(bundle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", default=str(DEFAULT_REAL))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)
    bundle = build_bundle(Path(args.real))
    if not bundle["incidents"]:
        print(
            f"no incident-*.json under {args.real}; run make capture-run first",
            file=sys.stderr,
        )
        return 1
    Path(args.out).write_text(json.dumps(bundle, indent=1, default=str) + "\n")
    turns = sum(len(t) for t in bundle["turns"].values())
    print(f"wrote {args.out}: {len(bundle['incidents'])} incident(s), {turns} turn(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
