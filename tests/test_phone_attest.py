"""Who said the sentence that changed production.

The agent reads the approval phrase back, so the phrase appears in the recording
twice -- once from each party. These tests pin down the thing that makes the
artifact verifiable afterwards: only the caller's channel counts as consent.
"""

from __future__ import annotations

from typing import Any

from beacon.phone import attest


def utterance(
    channel: str, text: str, start: int = 0, confidence: float = 0.96
) -> dict[str, Any]:
    return {"channel": channel, "text": text, "confidence": confidence, "start": start}


READ_BACK_THEN_APPROVAL = [
    utterance("2", "The rule is missing. Say exactly: approve fix one.", 1000),
    utterance("1", "Approve fix one.", 5000),
    utterance(
        "2", "Applied. CloudWatch agrees. Should I handle this myself next time?", 7000
    ),
    utterance("1", "Grant contract for seven days.", 12000),
]


def test_the_approval_is_attributed_to_the_caller_not_to_the_read_back() -> None:
    verdict = attest.verify(READ_BACK_THEN_APPROVAL, ["propose_fix", "approve_fix"])
    assert verdict["ok"]
    check = next(c for c in verdict["checks"] if c["tool"] == "approve_fix")
    assert check["caller_occurrences"] == 1
    assert check["agent_occurrences"] == 1, (
        "the read-back is in the recording, as it should be"
    )
    assert check["quote"] == "Approve fix one"


def test_a_change_whose_phrase_only_the_agent_said_is_a_failure() -> None:
    """The failure this exists to catch: the agent talking itself into a change."""
    only_the_agent = [utterance("2", "Say exactly: approve fix one.", 1000)]
    verdict = attest.verify(only_the_agent, ["approve_fix"])
    assert not verdict["ok"]
    assert "not on the caller's channel" in verdict["problems"][0]
    assert "only the agent said it" in verdict["problems"][0]


def test_tools_that_change_nothing_need_no_phrase() -> None:
    verdict = attest.verify(
        [utterance("1", "What is going on?")],
        ["get_incident_brief", "get_evidence", "propose_fix", "check_recovery"],
    )
    assert verdict["ok"]
    assert verdict["checks"] == [], "only consent tools are attributed"


def test_every_consent_tool_has_a_phrase_pattern() -> None:
    """A consent tool with no pattern would pass attribution without being checked."""
    from beacon.phone.bridge import CONSENT_TOOLS

    assert set(CONSENT_TOOLS) == set(attest.CONSENT_PATTERNS)


def test_the_two_channels_are_separated_for_the_audit_page() -> None:
    attributed = attest.attribute(READ_BACK_THEN_APPROVAL)
    assert attributed["caller_said"] == [
        "Approve fix one.",
        "Grant contract for seven days.",
    ]
    assert len(attributed["agent_said"]) == 2
    assert set(attributed["phrases"]) == {"approve_fix", "grant_sleep_contract"}


def test_a_contract_granted_by_the_caller_is_attributed_with_its_own_phrase() -> None:
    verdict = attest.verify(READ_BACK_THEN_APPROVAL, ["grant_sleep_contract"])
    check = verdict["checks"][0]
    assert check["ok"] and check["quote"] == "Grant contract for seven days"


def test_a_phrase_split_across_utterances_is_still_found() -> None:
    """A transcriber may cut a channel anywhere, and on a real call it did.

    The caller's channel came back as "Approve", "fix", "one." — three separate
    utterances — and a per-utterance search reported that nobody had authorised a
    change that the caller had plainly authorised.
    """
    chopped = [
        utterance("2", "Say exactly: approve fix one.", 1000),
        utterance("1", "Approve", 5000),
        utterance("1", "fix", 5400),
        utterance("1", "one.", 5800),
    ]
    verdict = attest.verify(chopped, ["approve_fix"])
    assert verdict["ok"], verdict["problems"]
    check = verdict["checks"][0]
    assert check["caller_occurrences"] == 1
    assert check["agent_occurrences"] == 1, "the read-back is still attributed"


def test_the_one_voice_check_survives_a_split_phrase_too() -> None:
    chopped = [
        utterance("1", "Approve", 5000),
        utterance("1", "fix one.", 5400),
    ]
    for u in chopped:
        u["speaker"] = "1A"
    found = attest.voices_around(chopped, "Approve fix one")
    assert found["ok"] is True and found["voices"] == ["1A"]


def test_a_hinglish_approval_is_attributed_and_quoted_as_it_was_said() -> None:
    """A live Hinglish call was applied by the gate and flagged by this audit.

    The caller's channel came back as "अप्रूव फिक्स थ्री।", which no English regex
    finds, so the audit reported "approve_fix ran but its phrase is not on the
    caller's channel" for a change the caller had correctly authorised. A false
    alarm on the safety artifact is nearly as bad as a miss.

    The phrases are matched in comparable form; the quote stays verbatim, because
    the quote is the evidence.
    """
    verdict = attest.verify(
        [
            {"channel": "2", "text": "Please say approve fix three.", "start": 30000},
            {"channel": "1", "text": "अप्रूव", "start": 40000},
            {"channel": "1", "text": "फिक्स थ्री।", "start": 40500},
        ],
        ["approve_fix"],
    )
    check = verdict["checks"][0]
    assert check["ok"] and check["caller_occurrences"] == 1
    assert check["quote"] == "अप्रूव फिक्स थ्री", (
        "the audit quotes what was said, not a normalised rewrite of it"
    )
    assert verdict["caller_said"] == ["अप्रूव", "फिक्स थ्री।"]
