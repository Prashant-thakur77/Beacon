"""Who said the sentence that changed production.

A single-channel recording of a voice agent is ambiguous in a way that matters:
the agent reads the approval phrase back ("say exactly: approve fix one"), so the
phrase appears in the recording twice, once from each party. A transcript that does
not separate them cannot prove which occurrence authorised the change.

Beacon's phone bridge records the caller and the agent on separate channels, so the
question has a mechanical answer. This module asks it: for each phrase that unlocks
a change, which channel said it, and did the human say it at all.

The live consent check does not depend on this -- only the caller's microphone is
ever sent to the Voice Agent API, so the transcript the Lambda checks is the
caller's by construction. This is the artifact that lets somebody else verify that
claim afterwards, which is a different and more useful thing than trusting it.
"""

from __future__ import annotations

import re
from typing import Any

CALLER_CHANNEL = "1"
AGENT_CHANNEL = "2"

# The phrases that, spoken by the engineer, change something. Kept here rather than
# inferred, because a phrase list that drifts from the tools is a silent hole.
CONSENT_PATTERNS = {
    "approve_fix": re.compile(r"\bapprove\s+fix\s+(\w+)\b", re.I),
    "grant_sleep_contract": re.compile(
        r"\bgrant\s+contract\s+for\s+(\w+)\s+days?\b", re.I
    ),
    "undo_fix": re.compile(r"\bundo\s+fix\s+(\w+)\b", re.I),
    "open_fix_pr": re.compile(r"\bopen\s+the\s+pull\s+request\b", re.I),
}


# The bar a spoken approval has to clear. The same number the Telegram voice-note
# gate uses, so a phrase is held to one standard whichever channel carried it.
MIN_CONFIDENCE = 0.85


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _words_of(phrase: str) -> list[str]:
    return [w for w in re.sub(r"[^\w\s]", " ", phrase.lower()).split() if w]


def phrase_confidence(
    words: list[dict[str, Any]], phrase: str
) -> dict[str, Any] | None:
    """How clearly the words that authorised a change were actually heard.

    Takes the word list from ``aai.audit_session`` and finds the run of words
    matching *phrase*. The score is the **lowest** word in that run, not the mean:
    "approve fix one" heard as "approve fix" plus a guess is not 90% correct, it is
    wrong in the one place that decides which fix gets applied.

    ``None`` when the phrase is not in the recording at all, which is a louder
    finding than a low score.
    """
    want = _words_of(phrase)
    if not want or not words:
        return None
    spoken = [_words_of(str(w.get("text") or "")) for w in words]
    flat = [(w[0] if w else "", i) for i, w in enumerate(spoken)]
    best: dict[str, Any] | None = None
    for start in range(len(flat) - len(want) + 1):
        if [t for t, _ in flat[start : start + len(want)]] != want:
            continue
        run = [words[i] for _, i in flat[start : start + len(want)]]
        scores = [
            float(w["confidence"])
            for w in run
            if isinstance(w.get("confidence"), int | float)
        ]
        if not scores:
            continue
        found = {
            "phrase": phrase,
            "confidence": round(min(scores), 4),
            "mean": round(sum(scores) / len(scores), 4),
            "weakest_word": run[scores.index(min(scores))].get("text"),
            "start_ms": run[0].get("start"),
        }
        if best is None or found["confidence"] > best["confidence"]:
            best = found
    return best


def attribute(utterances: list[dict[str, Any]]) -> dict[str, Any]:
    """Split a dual-channel transcript into who said what, and find the phrases.

    ``utterances`` is what ``aai.transcribe_call`` returns. Channels are named as
    AssemblyAI names them for a two-channel file: ``"1"`` is the left channel, which
    the bridge records the caller on.
    """
    found: dict[str, list[dict[str, Any]]] = {}
    for utterance in utterances:
        channel = str(utterance.get("channel") or "")
        text = _norm(str(utterance.get("text") or ""))
        for tool, pattern in CONSENT_PATTERNS.items():
            for match in pattern.finditer(text):
                found.setdefault(tool, []).append(
                    {
                        "channel": channel,
                        "who": "caller" if channel == CALLER_CHANNEL else "agent",
                        "said": match.group(0),
                        "start_ms": utterance.get("start"),
                        "confidence": utterance.get("confidence"),
                    }
                )
    return {
        "caller_said": [
            _norm(str(u.get("text") or ""))
            for u in utterances
            if str(u.get("channel") or "") == CALLER_CHANNEL
        ],
        "agent_said": [
            _norm(str(u.get("text") or ""))
            for u in utterances
            if str(u.get("channel") or "") != CALLER_CHANNEL
        ],
        "phrases": found,
    }


# How far either side of the approval to look for a second voice. Long enough to
# catch somebody prompting the engineer, short enough that an unrelated remark
# earlier in the call is not held against them.
CONSENT_WINDOW_MS = 15_000


def voices_around(
    utterances: list[dict[str, Any]],
    phrase: str,
    *,
    channel: str = CALLER_CHANNEL,
    window_ms: int = CONSENT_WINDOW_MS,
) -> dict[str, Any]:
    """Was the caller alone when they authorised the change?

    AssemblyAI labels a two-channel transcript by channel *and* speaker -- ``1A``,
    ``2A`` -- so a second person on the caller's side comes back as ``1B``. This
    looks only at the caller's channel, inside a window around the phrase, and
    reports every distinct voice it finds.

    The agent is on the other channel by construction, so it is never mistaken for a
    person in the room; and a colleague who says nothing is invisible here, which is
    worth being plain about rather than implying the room was empty.
    """
    mine = [u for u in utterances if str(u.get("channel") or "") == channel]
    anchor = next(
        (
            u
            for u in mine
            if _norm(str(u.get("text") or "")).lower().find(phrase.lower()) >= 0
        ),
        None,
    )
    if anchor is None:
        pattern = re.compile(r"\s+".join(map(re.escape, _words_of(phrase))), re.I)
        anchor = next(
            (u for u in mine if pattern.search(_norm(str(u.get("text") or "")))), None
        )
    if anchor is None:
        return {
            "ok": None,
            "why": "the phrase is not on the caller's channel",
            "voices": [],
        }
    at = anchor.get("start")
    near = mine
    if isinstance(at, int | float):
        near = [
            u
            for u in mine
            if isinstance(u.get("start"), int | float)
            and abs(float(u["start"]) - float(at)) <= window_ms
        ]
    voices = sorted({str(u.get("speaker") or "") for u in near if u.get("speaker")})
    return {
        "ok": len(voices) <= 1,
        "voices": voices,
        "spoke": [
            {"speaker": u.get("speaker"), "text": _norm(str(u.get("text") or ""))}
            for u in near
        ],
        "window_ms": window_ms,
        "why": (
            "one voice on the caller's channel"
            if len(voices) <= 1
            else f"{len(voices)} voices on the caller's channel around the approval"
        ),
    }


def verify_confidence(
    words: list[dict[str, Any]], approvals: list[dict[str, Any]]
) -> dict[str, Any]:
    """Check each applied change against how clearly its phrase was heard.

    The live socket reports no confidence, so an approval spoken in the browser or
    on the phone is acted on without one while the same words in a Telegram voice
    note must clear 85%. This is the second opinion that closes that gap: after the
    fact, from AssemblyAI's own recording of the session.

    Nothing is undone by a poor score -- the fix has been applied and verified by
    then. It is *flagged*, which is what an audit is for.
    """
    checks: list[dict[str, Any]] = []
    problems: list[str] = []
    for approval in approvals:
        phrase = str(approval.get("quote") or approval.get("phrase") or "").strip()
        if not phrase:
            continue
        found = phrase_confidence(words, phrase)
        if found is None:
            checks.append(
                {
                    "phrase": phrase,
                    "ok": False,
                    "confidence": None,
                    "why": "not found in the session recording",
                }
            )
            problems.append(f"{phrase!r} is not in the recording of the session")
            continue
        ok = found["confidence"] >= MIN_CONFIDENCE
        checks.append({**found, "ok": ok})
        if not ok:
            problems.append(
                f"{phrase!r} was heard at {found['confidence']:.0%} "
                f"(weakest word {found['weakest_word']!r}), "
                f"below {MIN_CONFIDENCE:.0%}"
            )
    return {"ok": not problems, "checks": checks, "problems": problems}


def verify(
    utterances: list[dict[str, Any]], executed_tools: list[str]
) -> dict[str, Any]:
    """Check every change made during the call against the caller's own channel.

    For each consent tool that actually ran, the caller's channel must contain its
    phrase. A phrase that appears only on the agent's channel is the failure this
    exists to catch: the agent talked itself into a change.

    Returns ``{"ok", "checks": [...], "problems": [...]}`` -- ``ok`` is false if any
    executed consent tool has no phrase on the caller's channel.
    """
    attributed = attribute(utterances)
    checks: list[dict[str, Any]] = []
    problems: list[str] = []
    for tool in dict.fromkeys(executed_tools):
        if tool not in CONSENT_PATTERNS:
            continue
        hits = attributed["phrases"].get(tool, [])
        by_caller = [h for h in hits if h["who"] == "caller"]
        by_agent = [h for h in hits if h["who"] == "agent"]
        ok = bool(by_caller)
        alone = (
            voices_around(utterances, by_caller[0]["said"])
            if by_caller
            else {"ok": None}
        )
        checks.append(
            {
                "tool": tool,
                "ok": ok,
                "caller_occurrences": len(by_caller),
                "agent_occurrences": len(by_agent),
                "quote": by_caller[0]["said"] if by_caller else None,
                "one_voice": alone,
            }
        )
        if not ok:
            problems.append(
                f"{tool} ran but its phrase is not on the caller's channel"
                + (f" (only the agent said it, {len(by_agent)}x)" if by_agent else "")
            )
        elif alone.get("ok") is False:
            # Not a failure of consent — somebody did say the phrase. It is a fact
            # about the room that whoever reviews this change should be told.
            voices: list[str] = list(alone.get("voices") or [])
            problems.append(
                f"{tool} was approved with {len(voices)} voices on the caller's "
                f"channel ({', '.join(voices)}); consent should come from the "
                "person who was called"
            )
    return {"ok": not problems, "checks": checks, "problems": problems, **attributed}
