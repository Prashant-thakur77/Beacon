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


def channelled(*triples: tuple[str, float, str]) -> list[dict[str, Any]]:
    return [
        {"text": t, "confidence": c, "start": i * 400, "channel": ch}
        for i, (t, c, ch) in enumerate(triples)
    ]


def test_the_agents_own_read_back_is_not_scored_as_the_engineers_consent() -> None:
    """The agent says the phrase too: it reads the number back and asks for it.

    A mono transcript cannot tell whose mouth it came from. Scoring every channel
    would let "Please say approve fix two" stand in for an approval the engineer
    never gave, which is the one substitution this whole artifact exists to prevent.
    """
    only_the_agent = channelled(
        ("please", 0.99, "2"),
        ("say", 0.99, "2"),
        ("approve", 0.98, "2"),
        ("fix", 0.99, "2"),
        ("two", 0.97, "2"),
    )
    verdict = attest.verify_confidence(only_the_agent, [{"quote": "approve fix two"}])
    assert verdict["checks"][0]["confidence"] is None
    assert "not found" in verdict["checks"][0]["why"]
    assert not verdict["ok"]


def test_the_engineers_channel_is_scored_even_when_the_agent_said_it_better() -> None:
    both = channelled(
        ("approve", 0.99, "2"),
        ("fix", 0.99, "2"),
        ("two", 0.99, "2"),
        ("approve", 0.91, "1"),
        ("fix", 0.88, "1"),
        ("two", 0.86, "1"),
    )
    check = attest.verify_confidence(both, [{"quote": "approve fix two"}])["checks"][0]
    assert check["confidence"] == 0.86, "the engineer's weakest word, not the agent's"
    assert check["weakest_word"] == "two"


def test_a_single_channel_recording_still_scores() -> None:
    """A browser session mixes both sides into one channel.

    Dropping unchannelled words would score nothing at all there.
    """
    check = attest.verify_confidence(
        words(("approve", 0.95), ("fix", 0.93), ("two", 0.9)),
        [{"quote": "approve fix two"}],
    )["checks"][0]
    assert check["confidence"] == 0.9


def test_the_session_is_re_transcribed_per_channel(monkeypatch: Any) -> None:
    """A mono transcription of a two-channel recording buries the quieter speaker.

    A live phone approval whose recording plainly contains "Approve fix 2." at 40 s
    came back from the attest route as "not in the recording of the session": the
    agent's synthesised voice peaks at 0 dB against the caller's -25 dB, and the
    downmix lost the caller. The same audio transcribed per channel returns the
    phrase on channel 1.
    """
    from beacon import aai

    sent: dict[str, Any] = {}

    def fake_json(method: str, url: str, body: Any = None) -> Any:
        if method == "POST":
            sent.update(body or {})
            return {"id": "t-1"}
        return {
            "status": "completed",
            "id": "t-1",
            "text": "Approve fix 2.",
            "audio_duration": 95,
            "words": [
                {"text": "Approve", "confidence": 0.94, "start": 40100, "channel": "1"},
                {"text": "fix", "confidence": 0.99, "start": 40400, "channel": "1"},
                {"text": "2.", "confidence": 0.88, "start": 40700, "channel": "1"},
            ],
        }

    monkeypatch.setattr(aai, "audio_url", lambda _s: "https://x/audio.ogg")
    monkeypatch.setattr(aai, "_json", fake_json)
    heard = aai.audit_session("sess_x", keyterms=["approve fix 2"])

    assert sent["dual_channel"] is True
    assert [w["channel"] for w in heard["words"]] == ["1", "1", "1"]
    check = attest.verify_confidence(heard["words"], [{"quote": "Approve fix 2."}])
    assert check["checks"][0]["confidence"] == 0.88
