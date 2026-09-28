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


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


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
        checks.append(
            {
                "tool": tool,
                "ok": ok,
                "caller_occurrences": len(by_caller),
                "agent_occurrences": len(by_agent),
                "quote": by_caller[0]["said"] if by_caller else None,
            }
        )
        if not ok:
            problems.append(
                f"{tool} ran but its phrase is not on the caller's channel"
                + (f" (only the agent said it, {len(by_agent)}x)" if by_agent else "")
            )
    return {"ok": not problems, "checks": checks, "problems": problems, **attributed}
