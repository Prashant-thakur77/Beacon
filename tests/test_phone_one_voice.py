"""Consent should come from the person who was called.

A two-channel recording already proves the approval came from the caller's side
rather than from the agent reading the phrase back. It does not, on its own, say
whether the caller was alone. AssemblyAI labels a dual-channel transcript by channel
*and* speaker — `1A`, `2A` — so a second person on the caller's side arrives as
`1B`, and the question becomes a fact about the recording.
"""

from __future__ import annotations

from typing import Any

from beacon.phone import attest


def utt(channel: str, speaker: str, text: str, start: int) -> dict[str, Any]:
    return {
        "channel": channel,
        "speaker": speaker,
        "text": text,
        "start": start,
        "confidence": 0.96,
    }


ALONE = [
    utt("2", "2A", "Say exactly: approve fix one.", 1_000),
    utt("1", "1A", "Approve fix one.", 5_000),
    utt("2", "2A", "Applied, and verifying.", 8_000),
]

SOMEBODY_ELSE = [
    utt("2", "2A", "Say exactly: approve fix one.", 1_000),
    utt("1", "1B", "Just say it, go on.", 4_000),
    utt("1", "1A", "Approve fix one.", 5_000),
    utt("2", "2A", "Applied, and verifying.", 8_000),
]


def test_a_caller_on_their_own_passes() -> None:
    found = attest.voices_around(ALONE, "Approve fix one")
    assert found["ok"] is True
    assert found["voices"] == ["1A"]


def test_a_second_voice_on_the_callers_side_is_found() -> None:
    found = attest.voices_around(SOMEBODY_ELSE, "Approve fix one")
    assert found["ok"] is False
    assert found["voices"] == ["1A", "1B"]
    assert "2 voices" in found["why"]


def test_the_agent_is_never_mistaken_for_somebody_in_the_room() -> None:
    """The agent is on the other channel by construction, so it cannot count."""
    found = attest.voices_around(ALONE, "Approve fix one")
    assert "2A" not in found["voices"]


def test_a_remark_far_from_the_approval_is_not_held_against_the_caller() -> None:
    early = [
        utt("1", "1B", "Morning.", 5_000),
        *[
            utt(u["channel"], u["speaker"], u["text"], u["start"] + 90_000)
            for u in ALONE
        ],
    ]
    found = attest.voices_around(early, "Approve fix one")
    assert found["ok"] is True, "a voice ninety seconds earlier is not in the room now"


def test_a_phrase_that_is_not_on_the_callers_channel_is_undecidable_not_a_pass() -> (
    None
):
    only_agent = [utt("2", "2A", "Say exactly: approve fix one.", 1_000)]
    found = attest.voices_around(only_agent, "Approve fix one")
    assert found["ok"] is None
    assert "not on the caller's channel" in found["why"]


def test_an_approval_with_company_is_flagged_but_not_called_a_forgery() -> None:
    """Somebody did say the phrase. It is the room the reviewer should be told about."""
    verdict = attest.verify(SOMEBODY_ELSE, ["approve_fix"])
    assert not verdict["ok"]
    problem = verdict["problems"][0]
    assert "2 voices" in problem
    assert "1A, 1B" in problem
    check = verdict["checks"][0]
    assert check["ok"] is True, "the phrase was on the caller's channel"
    assert check["one_voice"]["ok"] is False


def test_an_approval_alone_carries_the_one_voice_result_too() -> None:
    verdict = attest.verify(ALONE, ["approve_fix"])
    assert verdict["ok"]
    assert verdict["checks"][0]["one_voice"]["ok"] is True
