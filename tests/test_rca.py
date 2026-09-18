from __future__ import annotations

from beacon.rca import is_healthy, parse

STANDARD = """\
STATUS: High
SUMMARY: The demo web service cannot reach its PostgreSQL database.
AFFECTED COMPONENTS: beacon-demo-webapp, beacon-demo-db
EVIDENCE:
- 2026-09-18T20:01:02 ERROR POST /api/v2/payments 503 5003ms - db_pool=EXHAUSTED
- 2026-09-18T20:01:09 ERROR CRITICAL: Database unreachable. Host=db:5432
NEXT STEPS:
1. Check the RDS security group ingress rules for tcp/5432
2. Confirm the ECS task security group is still the allowed source
3. Restore the rule and watch the error rate
CHANGE CORRELATION: RevokeSecurityGroupIngress on sg-0abc by user/prashant
at 20:00:41, 21 seconds before the first error.
SPOKEN SUMMARY: Your demo web service in U S east 1 lost its database.
It looks like a security group rule was removed just before the errors started.
BEACON_JSON:
```json
{"fingerprint": "rds-sg-ingress-revoked", "suggested_action": "sg.restore_ingress",
 "action_params": {"group_id": "sg-0abc", "from_port": 5432, "to_port": 5432,
 "ip_protocol": "tcp", "source_group_id": "sg-0def"}, "confidence": 0.92,
 "correlated_change": "RevokeSecurityGroupIngress by user/prashant at 20:00:41"}
```
"""


def test_parse_extracts_status_summary_and_components() -> None:
    rca = parse(STANDARD)
    assert rca.status == "High"
    assert rca.summary.startswith("The demo web service cannot reach")
    assert rca.affected_components == ["beacon-demo-webapp", "beacon-demo-db"]


def test_parse_collects_multiline_evidence_and_steps() -> None:
    rca = parse(STANDARD)
    assert len(rca.evidence) == 2
    assert rca.evidence[0].startswith("2026-09-18T20:01:02 ERROR")
    assert rca.next_steps == [
        "Check the RDS security group ingress rules for tcp/5432",
        "Confirm the ECS task security group is still the allowed source",
        "Restore the rule and watch the error rate",
    ]


def test_parse_spoken_summary_spans_lines_until_next_header() -> None:
    rca = parse(STANDARD)
    assert rca.spoken_summary == (
        "Your demo web service in U S east 1 lost its database. "
        "It looks like a security group rule was removed just before the errors "
        "started."
    )


def test_parse_change_correlation_section() -> None:
    rca = parse(STANDARD)
    assert rca.change_correlation is not None
    assert rca.change_correlation.startswith("RevokeSecurityGroupIngress on sg-0abc")


def test_parse_beacon_json_tail() -> None:
    rca = parse(STANDARD)
    assert rca.beacon_json["suggested_action"] == "sg.restore_ingress"
    assert rca.beacon_json["action_params"]["group_id"] == "sg-0abc"
    assert rca.beacon_json["confidence"] == 0.92
    assert rca.fingerprint == "rds-sg-ingress-revoked"
    assert rca.suggested_action == "sg.restore_ingress"


def test_parse_tolerates_markdown_bold_headers() -> None:
    text = "**STATUS:** Critical\n**SUMMARY:** Everything is on fire.\n"
    rca = parse(text)
    assert rca.status == "Critical"
    assert rca.summary == "Everything is on fire."


def test_parse_without_json_tail_gives_empty_dict_and_none_action() -> None:
    text = "STATUS: Low\nSUMMARY: Minor noise.\nSPOKEN SUMMARY: All fine.\n"
    rca = parse(text)
    assert rca.beacon_json == {}
    assert rca.suggested_action is None
    assert rca.fingerprint is None


def test_parse_ignores_malformed_json_tail() -> None:
    text = "STATUS: Low\nSUMMARY: x\nBEACON_JSON:\n```json\n{not json\n```\n"
    rca = parse(text)
    assert rca.beacon_json == {}


def test_is_healthy_detects_healthy_status_case_insensitively() -> None:
    assert is_healthy("STATUS: Healthy\nSUMMARY: Normal operation.") is True
    assert is_healthy("**STATUS:** healthy\nSUMMARY: fine") is True
    assert is_healthy(STANDARD) is False
    assert is_healthy("no status line at all") is False


def test_parse_of_unstructured_text_keeps_it_as_summary() -> None:
    rca = parse("Something went wrong but I cannot say what.")
    assert rca.status == "Unknown"
    assert "Something went wrong" in rca.summary


def test_to_dict_round_trips_all_fields() -> None:
    d = parse(STANDARD).to_dict()
    assert d["status"] == "High"
    assert d["next_steps"][0].startswith("Check the RDS")
    assert d["beacon_json"]["fingerprint"] == "rds-sg-ingress-revoked"
    assert d["spoken_summary"].startswith("Your demo web service")
