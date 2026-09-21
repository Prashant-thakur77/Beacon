"""Postmortem, audit log and morning report: documents from the records."""

from __future__ import annotations

from typing import Any

from beacon import reports

INCIDENT: dict[str, Any] = {
    "incident_id": "inc-1",
    "alarm_name": "beacon-demo-infra-errors",
    "timestamp": "2026-09-20T21:10:00+00:00",
    "status": "resolved",
    "rca_json": {
        "status": "High",
        "summary": "The demo web service lost connectivity to its PostgreSQL database.",
        "evidence": ["21:41:03 ERROR POST /api/v2/payments 503"],
        "change_correlation": (
            "RevokeSecurityGroupIngress on sg-08861ee93fee43c6f "
            "2 min 40 s before the first error"
        ),
    },
    "changes": [
        {
            "event_name": "RevokeSecurityGroupIngress",
            "actor_short": "user/prashant",
            "event_time": "2026-09-20T21:07:20+00:00",
            "resource_ids": ["sg-08861ee93fee43c6f"],
        }
    ],
    "proposals": [
        {
            "fix_id": 1,
            "action": "sg.restore_ingress",
            "blast_radius": "1 ingress rule on 1 security group",
            "dry_run": {"ok": True, "code": "DryRunOperation"},
        }
    ],
    "executed_at": "2026-09-20T21:12:30+00:00",
    "resolved_at": "2026-09-20T21:13:40+00:00",
    "handled_by": "engineer",
    "woken": True,
    "usage": {"cost_inr": 0.13},
    "timeline": [
        {"t": "2026-09-20T21:10:00+00:00", "event": "alarm_received"},
        {
            "t": "2026-09-20T21:11:10+00:00",
            "event": "fix_proposed",
            "detail": {"fix_id": 1},
        },
        {
            "t": "2026-09-20T21:12:20+00:00",
            "event": "approved",
            "detail": {"quote": "approve fix 1", "channel": "typed"},
        },
        {"t": "2026-09-20T21:12:30+00:00", "event": "executed"},
        {
            "t": "2026-09-20T21:13:05+00:00",
            "event": "verify_attempt",
            "detail": {
                "attempt": 1,
                "ok": False,
                "checks": [{"name": "alarm_ok_after_fix", "ok": False}],
            },
        },
        {
            "t": "2026-09-20T21:13:40+00:00",
            "event": "verify_attempt",
            "detail": {
                "attempt": 2,
                "ok": True,
                "checks": [
                    {"name": "alarm_ok_after_fix", "ok": True},
                    {"name": "metric_zero", "ok": True},
                    {"name": "postcondition", "ok": True},
                ],
            },
        },
        {"t": "2026-09-20T21:13:40+00:00", "event": "resolved"},
    ],
}
CONTRACT: dict[str, Any] = {
    "contract_id": "c-1",
    "alarm_name": "beacon-demo-infra-errors",
    "action": "sg.restore_ingress",
    "days": 7,
    "max_uses": 3,
    "uses": 1,
    "status": "active",
    "granted_at": "2026-09-20T21:14:00+00:00",
    "expires_at": "2026-09-27T21:14:00+00:00",
    "transcript_quote": "grant contract for seven days",
    "incident_id": "inc-1",
    "params": {"group_id": "sg-08861ee93fee43c6f"},
}
APPROVAL: dict[str, Any] = {
    "approval_id": "a-1",
    "kind": "approval",
    "incident_id": "inc-1",
    "fix_id": 1,
    "action": "sg.restore_ingress",
    "params": {"group_id": "sg-08861ee93fee43c6f"},
    "source": "voice",
    "channel": "typed",
    "transcript_quote": "approve fix 1",
    "granted_at": "2026-09-20T21:12:20+00:00",
    "used_at": "2026-09-20T21:12:30+00:00",
    "result": {"ok": True, "code": "OK"},
}


def test_postmortem_has_every_section_and_the_facts() -> None:
    md = reports.postmortem(INCIDENT, contracts=[CONTRACT], approvals=[APPROVAL])
    for heading in (
        "# Postmortem",
        "## Summary",
        "## Timeline",
        "## Root cause",
        "## What changed",
        "## The fix",
        "## Verification",
        "## Sleep Contract",
        "## Cost",
    ):
        assert heading in md, heading
    assert "beacon-demo-infra-errors" in md and "sg.restore_ingress" in md
    assert "RevokeSecurityGroupIngress" in md and "user/prashant" in md
    assert '"approve fix 1"' in md and "typed" in md
    assert "3 m 40 s" in md  # alarm → resolved
    assert "attempt 2" in md.lower() and "alarm_ok_after_fix" in md
    assert "grant contract for seven days" in md
    assert "₹0.13" in md


def test_postmortem_for_an_escalated_incident_says_so() -> None:
    inc = {
        **INCIDENT,
        "status": "escalated",
        "resolved_at": None,
        "timeline": INCIDENT["timeline"][:5]
        + [
            {
                "t": "2026-09-20T21:16:00+00:00",
                "event": "escalated",
                "detail": {"reason": "verify failed 6 times"},
            }
        ],
    }
    md = reports.postmortem(inc, contracts=[], approvals=[APPROVAL])
    assert "Escalated" in md and "verify failed 6 times" in md
    assert "## Sleep Contract" in md and "No contract" in md


def test_audit_rows_are_ordered_and_quote_the_transcript() -> None:
    rows = reports.audit(
        approvals=[APPROVAL], contracts=[CONTRACT], incidents=[INCIDENT]
    )
    assert [r["kind"] for r in rows] == ["contract", "approval"]  # newest first
    a = rows[1]
    assert (
        a["quote"] == "approve fix 1"
        and a["channel"] == "typed"
        and a["executed"] is True
    )
    assert a["alarm_name"] == "beacon-demo-infra-errors"
    c = rows[0]
    assert c["quote"] == "grant contract for seven days" and c["uses"] == "1/3"


def test_audit_csv_has_a_header_and_one_line_per_row() -> None:
    rows = reports.audit(
        approvals=[APPROVAL], contracts=[CONTRACT], incidents=[INCIDENT]
    )
    csv = reports.audit_csv(rows)
    lines = csv.strip().splitlines()
    assert lines[0].startswith("at,kind,alarm_name,action,quote,channel")
    assert len(lines) == 3


def test_morning_report_summarises_the_night() -> None:
    second = {
        **INCIDENT,
        "incident_id": "inc-2",
        "timestamp": "2026-09-20T23:40:00+00:00",
        "executed_at": "2026-09-20T23:40:50+00:00",
        "resolved_at": "2026-09-20T23:41:30+00:00",
        "handled_by": "contract",
        "woken": False,
        "contract_id": "c-1",
    }
    rep = reports.morning_report(
        [INCIDENT, second],
        contracts=[CONTRACT],
        night_of="2026-09-20",
        now="2026-09-21T01:30:00+00:00",
    )
    assert rep["night_of"] == "2026-09-20" and rep["incidents"] == 2
    assert rep["resolved"] == 2 and rep["escalated"] == 0
    assert rep["humans_woken"] == 1 and rep["handled_by_contract"] == 1
    assert rep["median_minutes_to_recovery"] is not None
    assert rep["contracts_used"] == [
        {"contract_id": "c-1", "alarm_name": "beacon-demo-infra-errors", "uses": "1/3"}
    ]
    text = rep["text"]
    assert (
        "Good morning" in text
        and "1 human woken" in text
        and "handled under a Sleep Contract" in text
    )
    assert rep["subject"].startswith("Beacon morning report")


def test_morning_report_for_a_quiet_night() -> None:
    rep = reports.morning_report(
        [], contracts=[], night_of="2026-09-21", now="2026-09-22T01:30:00+00:00"
    )
    assert rep["incidents"] == 0 and "quiet" in rep["text"].lower()


def test_reports_carry_the_pull_request() -> None:
    from beacon import reports

    inc = {
        "incident_id": "i1",
        "alarm_name": "beacon-demo-infra-errors",
        "timestamp": "2026-09-21T20:41:00+00:00",
        "resolved_at": "2026-09-21T20:44:00+00:00",
        "status": "resolved",
        "rca_json": {"summary": "x"},
        "timeline": [
            {"event": "resolved", "at": "2026-09-21T20:44:00+00:00", "detail": {}},
            {
                "event": "pr_opened",
                "at": "2026-09-21T20:46:00+00:00",
                "detail": {"url": "https://github.com/o/r/pull/2", "number": 2},
            },
        ],
    }
    pm = reports.postmortem(inc, contracts=[], approvals=[])
    assert "## Durable fix" in pm and "https://github.com/o/r/pull/2" in pm
    report = reports.morning_report(
        [inc], contracts=[], now="2026-09-22T01:30:00+00:00"
    )
    assert report["pull_requests"] == ["https://github.com/o/r/pull/2"]
    assert "Durable fix: pull request https://github.com/o/r/pull/2" in report["text"]
    assert "1 pull request(s) opened for review" in report["text"]
