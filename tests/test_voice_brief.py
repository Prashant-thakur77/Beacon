"""One brief, three channels.

The browser, the phone bridge and the spoken regression suite must open the same
agent. The brief is built on the server so that stays true; these tests are what
notice when a channel drifts.
"""

from __future__ import annotations

from typing import Any

from beacon import voice_brief

DRIFT: dict[str, Any] = {
    "incident_id": "inc-2026-09-28-01",
    "alarm_name": "payments-errors",
    "status": "awaiting_approval",
    "rca_json": {
        "status": "degraded",
        "spoken_summary": (
            "A security group rule on sg-0abc123 was removed eleven minutes ago. "
            "Payments cannot reach the database."
        ),
    },
    "changes": [
        {
            "event_name": "RevokeSecurityGroupIngress",
            "actor_short": "deploy-bot",
            "event_time": "03:01Z",
            "resource_ids": ["sg-0abc123"],
        }
    ],
    "diagnostics": {
        "missing_rules": [
            {
                "ip_protocol": "tcp",
                "from_port": 5432,
                "group_id": "sg-0abc123",
                "source_group_id": "sg-0def456",
            }
        ]
    },
}


def test_the_brief_names_the_cause_the_change_and_the_allowlisted_fix() -> None:
    brief = voice_brief.brief_context(DRIFT)
    assert "payments-errors" in brief
    assert "RevokeSecurityGroupIngress by deploy-bot" in brief
    assert "sg.restore_ingress" in brief
    assert "5432" in brief


def test_the_greeting_gives_the_cause_and_what_to_say_but_never_a_resource_id() -> None:
    greeting = voice_brief.greeting_for(DRIFT)
    assert "sg-0abc123" not in greeting, "ids are unspeakable at 3 AM"
    assert "the security group" in greeting
    assert "Say fix it" in greeting


def test_the_phone_greeting_says_who_is_calling() -> None:
    assert voice_brief.greeting_for(DRIFT, channel="phone").startswith(
        "This is Beacon calling"
    )
    assert not voice_brief.greeting_for(DRIFT).startswith("This is Beacon calling")


def test_an_incident_handled_under_a_contract_says_nobody_was_woken() -> None:
    resolved = {**DRIFT, "status": "resolved", "handled_by": "contract"}
    assert "you were not woken" in voice_brief.greeting_for(resolved)


def test_every_phrase_that_unlocks_a_change_is_a_key_term() -> None:
    """A phrase heard as 'approve fix to' is a refused approval and a wasted minute."""
    terms = voice_brief.keyterms_for(DRIFT)
    for phrase in (
        "approve fix one",
        "approve fix two",
        "grant contract for seven days",
        "open the pull request",
        "undo fix one",
    ):
        assert phrase in terms
    assert "payments-errors" in terms
    assert "sg-0def456" in terms
    assert len(terms) <= 50, "the API caps the key term list"


def test_the_phone_gets_the_rules_a_telephone_needs_and_the_browser_does_not() -> None:
    phone = voice_brief.session_brief(DRIFT, channel="phone")["system_prompt"]
    browser = voice_brief.session_brief(DRIFT)["system_prompt"]
    assert "telephone call" in phone
    assert "keypress is never consent" in phone
    assert "telephone call" not in browser
    # the rules that are not channel-specific are identical
    assert "agreement is not consent" in phone.lower() or "say exactly" in phone
    assert voice_brief.brief_context(DRIFT) in phone
    assert voice_brief.brief_context(DRIFT) in browser


def test_hindi_puts_hindi_first_in_the_language_list() -> None:
    assert voice_brief.session_brief(DRIFT, language="hi-IN")["language_codes"] == [
        "hi",
        "en",
    ]
    assert voice_brief.session_brief(DRIFT)["language_codes"] == ["en", "hi"]


def test_the_declared_tools_match_the_ones_the_lambda_will_actually_run() -> None:
    from beacon import voice_tools

    assert list(voice_brief.TOOL_NAMES) == [s["name"] for s in voice_tools.TOOL_SCHEMAS]
    assert set(voice_brief.TOOL_NAMES) == set(voice_tools.TOOL_FUNCTIONS)


def test_the_console_copy_and_the_served_prompt_are_the_same() -> None:
    """The console keeps a fallback copy; if it drifts, the phone agent differs."""
    import json
    import pathlib
    import re

    tsx = (
        pathlib.Path(__file__).resolve().parents[1]
        / "web/src/components/TalkDuplex.tsx"
    )
    block = tsx.read_text().split("const SYSTEM_PROMPT_HINT =", 1)[1].split(";\n", 1)[0]
    in_console = "".join(
        json.loads(f'"{part}"') for part in re.findall(r'"((?:[^"\\]|\\.)*)"', block)
    )
    assert re.sub(r"\s+", " ", in_console).strip() == voice_brief.system_prompt()
