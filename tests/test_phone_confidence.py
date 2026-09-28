"""How clearly the words that authorised a change were actually heard.

The live socket reports no confidence, so until now an approval spoken in the
browser or down a phone was acted on without one, while the same words sent as a
Telegram voice note had to clear 85%. These tests pin the second opinion that
closes the gap -- and the rule that a phrase is only as good as its weakest word.
"""

from __future__ import annotations

from typing import Any

from beacon.phone import attest


def words(*pairs: tuple[str, float]) -> list[dict[str, Any]]:
    return [
        {"text": t, "confidence": c, "start": i * 400} for i, (t, c) in enumerate(pairs)
    ]


CLEAN = words(("Okay", 0.97), ("approve", 0.99), ("fix", 0.98), ("one", 0.96))
MUMBLED = words(("approve", 0.94), ("fix", 0.91), ("one", 0.62))


def test_a_clearly_spoken_phrase_clears_the_bar() -> None:
    found = attest.phrase_confidence(CLEAN, "approve fix one")
    assert found is not None
    assert found["confidence"] == 0.96
    assert found["weakest_word"] == "one"


def test_a_phrase_is_only_as_good_as_its_weakest_word() -> None:
    """The mean would be 82%; the word that decides which fix runs was heard at 62%."""
    found = attest.phrase_confidence(MUMBLED, "approve fix one")
    assert found is not None
    assert found["confidence"] == 0.62
    assert found["mean"] > found["confidence"]
    assert found["weakest_word"] == "one"


def test_punctuation_and_case_do_not_decide_whether_a_change_was_authorised() -> None:
    spoken = words(("Approve", 0.95), ("fix,", 0.93), ("One.", 0.92))
    found = attest.phrase_confidence(spoken, "approve fix one")
    assert found is not None and found["confidence"] == 0.92


def test_a_phrase_that_is_not_in_the_recording_is_not_a_low_score() -> None:
    """Louder than a bad number: the words that unlocked the change are missing."""
    assert attest.phrase_confidence(CLEAN, "undo fix one") is None


def test_the_best_occurrence_wins_when_the_phrase_was_repeated() -> None:
    """Refused at 62%, said again clearly — the clear one is what authorised it."""
    twice = MUMBLED + words(("approve", 0.98), ("fix", 0.97), ("one", 0.96))
    found = attest.phrase_confidence(twice, "approve fix one")
    assert found is not None and found["confidence"] == 0.96


def test_an_applied_change_heard_clearly_passes_the_audit() -> None:
    verdict = attest.verify_confidence(CLEAN, [{"quote": "approve fix one"}])
    assert verdict["ok"]
    assert verdict["checks"][0]["ok"]


def test_an_applied_change_heard_poorly_is_flagged_not_hidden() -> None:
    verdict = attest.verify_confidence(MUMBLED, [{"quote": "approve fix one"}])
    assert not verdict["ok"]
    assert "62%" in verdict["problems"][0]
    assert "below 85%" in verdict["problems"][0]


def test_a_change_whose_phrase_is_absent_is_the_loudest_finding() -> None:
    verdict = attest.verify_confidence(CLEAN, [{"quote": "grant contract for 7 days"}])
    assert not verdict["ok"]
    assert "not in the recording" in verdict["problems"][0]


def test_the_bar_is_the_same_one_the_voice_note_gate_uses() -> None:
    """One standard, whichever channel carried the words."""
    from beacon.voice_turn import _MIN_CONSENT_CONFIDENCE

    assert attest.MIN_CONFIDENCE == _MIN_CONSENT_CONFIDENCE
