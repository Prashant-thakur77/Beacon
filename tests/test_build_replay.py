"""scripts/build_replay.py turns captured real runs into the console's replay bundle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.build_replay import build_bundle


def _incident(
    iid: str, status: str, handled_by: str, woken: bool, ts: str
) -> dict[str, Any]:
    return {
        "incident_id": iid,
        "alarm_name": "beacon-demo-infra-errors",
        "status": status,
        "timestamp": ts,
        "woken": woken,
        "handled_by": handled_by,
        "resolved_at": ts.replace("T21", "T21").replace(":00:", ":03:"),
        "rca": "STATUS: High\nSUMMARY: raw text that must not ship",
        "rca_json": {
            "status": "High",
            "summary": "The demo web service lost its database.",
            "beacon_json": {},
        },
        "timeline": [
            {"t": ts, "event": "alarm_received"},
            {"t": ts, "event": "resolved", "detail": {"handled_by": handled_by}},
        ],
        "conversation": [
            {"role": "user", "content": [{"text": "what changed"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "t1",
                            "name": "get_evidence",
                            "input": {"kind": "changes"},
                        }
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "content": [
                                {
                                    "json": {
                                        "kind": "changes",
                                        "evidence": [
                                            {
                                                "id": "E1",
                                                "kind": "changes",
                                                "title": "Recent changes",
                                                "payload": [],
                                            }
                                        ],
                                        "data": [],
                                    }
                                }
                            ],
                            "status": "success",
                        }
                    }
                ],
            },
            {
                "role": "assistant",
                "content": [{"text": "One write call landed before the alarm [E1]."}],
            },
        ],
        "changes": [
            {
                "event_name": "RevokeSecurityGroupIngress",
                "actor": "arn:aws:iam::123456789012:user/prashant",
                "actor_short": "user/prashant",
            }
        ],
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }


def test_bundle_has_incidents_turns_contracts_tally_and_redacts(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    a = _incident("aaa", "resolved", "voice", True, "2026-09-19T21:00:00+00:00")
    b = _incident("bbb", "resolved", "contract", False, "2026-09-19T22:00:00+00:00")
    (real / "incident-1.json").write_text(json.dumps(a))
    (real / "incident-2.json").write_text(json.dumps(b))
    contracts = [
        {
            "contract_id": "c1",
            "alarm_name": "beacon-demo-infra-errors",
            "action": "sg.restore_ingress",
            "scope": {"group_id": "sg-1"},
            "uses": 1,
            "max_uses": 3,
            "granted_at": a["timestamp"],
            "expires_at": "2026-09-26T21:00:00+00:00",
            "transcript_quote": "grant contract for seven days",
            "granted_by": "transcribe",
            "incident_id": "aaa",
        }
    ]
    (real / "contracts.json").write_text(json.dumps({"contracts": contracts}))

    bundle = build_bundle(real)
    assert [i["incident_id"] for i in bundle["incidents"]] == ["bbb", "aaa"]
    assert (
        "rca" not in bundle["incidents"][0]
        and "conversation" not in bundle["incidents"][0]
    )
    assert "123456789012" not in json.dumps(bundle)
    assert bundle["recorded_at"] == a["timestamp"]

    turns = bundle["turns"]["aaa"]
    assert len(turns) == 1
    assert turns[0]["reply_text"] == "One write call landed before the alarm [E1]."
    assert turns[0]["cited"] == ["E1"]
    assert turns[0]["tool_events"][0]["name"] == "get_evidence"
    assert turns[0]["evidence"][0]["id"] == "E1"
    assert turns[0]["audio_b64"] is None

    assert bundle["contracts"][0]["contract_id"] == "c1"
    assert (
        bundle["tally"]["incidents_handled"] == 2
        and bundle["tally"]["humans_woken"] == 1
    )
    assert bundle["tally"]["handled_by_contract"] == 1
    assert bundle["safety"]["allowlist"][0]["id"] == "sg.restore_ingress"


def test_bundle_is_empty_but_valid_without_captures(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path)
    assert (
        bundle["incidents"] == []
        and bundle["turns"] == {}
        and bundle["contracts"] == []
    )
