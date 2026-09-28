"""The session brief, built once on the server for every voice channel.

The browser, the phone bridge and a replayed test all open the same kind of
AssemblyAI Voice Agent session, and they must open it with the *same* rules: the
same system prompt, the same nine tools, the same key terms, the same greeting.
That text used to live only in the React component, which meant a channel added
later would quietly get a different agent -- and "the phone agent behaved
differently from the one in the demo" is precisely the bug nobody finds until a
judge finds it.

So the brief is assembled here, from the incident record, and served over
``GET /voice/brief/<incident_id>``. The console prefers that route and falls back
to its own copy; the phone bridge has no copy at all.

Nothing here talks to AWS: it takes the incident dict and returns JSON, so it is
cheap to test and safe to import from the triage image.
"""

from __future__ import annotations

import re
from functools import cache
from importlib.resources import files
from typing import Any

# The phone is a worse channel than a laptop and the agent has to know it: no
# screen to read a fix card from, a caller who may be in a car, and a line that
# might drop mid-approval.
PHONE_ADDENDUM = (
    "You are on a telephone call, not a browser. There is no screen: never refer to "
    "a card, a button, a tab or a colour, and never say 'as you can see'. "
    "Say the fix number and the exact approval phrase twice, slowly, the first time "
    "you give it. "
    "Keep every turn to one or two short sentences, because the caller cannot scroll "
    "back. "
    "If the caller says they will call back, or asks you to hang up, say one closing "
    "sentence and stop talking. "
    "A keypress is never consent: if the caller presses a key instead of speaking the "
    "phrase, say that the fix needs the spoken phrase."
)

_SG_RE = re.compile(r"\s+sg-[0-9a-f]+")

# What the agent is allowed to do, in the order the console declares them.
TOOL_NAMES = (
    "get_incident_brief",
    "get_evidence",
    "propose_fix",
    "approve_fix",
    "cancel_proposal",
    "undo_fix",
    "grant_sleep_contract",
    "open_fix_pr",
    "check_recovery",
)


@cache
def system_prompt() -> str:
    """The channel-independent rules the agent runs under."""
    return (
        files("beacon")
        .joinpath("prompts/voice_agent.txt")
        .read_text(encoding="utf-8")
        .replace("\n", " ")
        .strip()
    )


def _first(items: Any) -> dict[str, Any]:
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return dict(items[0])
    return {}


def brief_context(incident: dict[str, Any]) -> str:
    """The incident as prose the agent is given before the first word is spoken.

    Cheaper than a tool round trip and, more importantly, it means the first turn
    of a 3 AM call is an answer rather than a lookup.
    """
    rca = incident.get("rca_json") or {}
    change = _first(incident.get("changes"))
    missing = _first((incident.get("diagnostics") or {}).get("missing_rules"))
    summary = str(rca.get("spoken_summary") or rca.get("summary") or "").strip()
    lines = [
        f"Incident {incident.get('incident_id', 'unknown')} on alarm "
        f"{incident.get('alarm_name') or 'unknown'}, "
        f"status {incident.get('status', 'unknown')}, "
        f"severity {rca.get('status') or 'unknown'}.",
        f"Summary: {summary}" if summary else "",
        (
            f"Change ledger: {change.get('event_name')} by "
            f"{change.get('actor_short') or 'unknown'} "
            f"at {change.get('event_time') or '?'} "
            f"on {', '.join(str(r) for r in change.get('resource_ids') or [])}."
            if change
            else ""
        ),
        (
            f"Drift: rule {missing.get('ip_protocol') or 'tcp'}"
            f"/{missing.get('from_port') or '?'} "
            f"from {missing.get('source_group_id') or '?'} is missing on "
            f"{missing.get('group_id') or '?'}; "
            "the allowlisted fix is sg.restore_ingress."
            if missing
            else ""
        ),
    ]
    return " ".join(line for line in lines if line)


def greeting_for(incident: dict[str, Any], *, channel: str = "browser") -> str:
    """The agent's first sentence: the cause, then what to say to act on it."""
    rca = incident.get("rca_json") or {}
    status = incident.get("status")
    if incident.get("handled_by") == "contract" and status == "resolved":
        return (
            "This one was handled under your Sleep Contract; you were not woken. "
            "Ask me anything about it."
        )
    if status == "resolved":
        return (
            "This incident is resolved. Ask me what happened, or whether to handle "
            "it myself next time."
        )
    first = re.split(
        r"(?<=\.)\s", str(rca.get("spoken_summary") or rca.get("summary") or "")
    )[0]
    if not first:
        return "Beacon here. Ask me what is going on."
    opening = (
        "This is Beacon calling about your production alarm. "
        if channel == "phone"
        else ""
    )
    cause = _SG_RE.sub(" the security group", first)
    return f"{opening}{cause} Say fix it, and I will propose the fix."


def keyterms_for(incident: dict[str, Any]) -> list[str]:
    """Words the transcriber must not guess at: the consent phrases and the ids.

    Every phrase that unlocks a change is in here, because a phrase heard as
    "approve fix to" is a refused approval and a second wasted minute at 3 AM.
    """
    rules = (incident.get("diagnostics") or {}).get("missing_rules") or []
    ids = [
        str(rule.get(field) or "")
        for rule in rules
        if isinstance(rule, dict)
        for field in ("group_id", "source_group_id")
    ]
    terms = [
        str(incident.get("alarm_name") or ""),
        *ids,
        "approve fix one",
        "approve fix two",
        "grant contract for seven days",
        "open the pull request",
        "undo fix one",
        "Beacon",
    ]
    seen: dict[str, None] = {}
    for term in terms:
        if term:
            seen.setdefault(term, None)
    return list(seen)[:50]


def session_brief(
    incident: dict[str, Any],
    *,
    channel: str = "browser",
    language: str = "en",
) -> dict[str, Any]:
    """Everything a voice channel needs to open a session, as JSON."""
    prompt = system_prompt()
    if channel == "phone":
        prompt = f"{prompt} {PHONE_ADDENDUM}"
    codes = ["hi", "en"] if language.startswith("hi") else ["en", "hi"]
    return {
        "incident_id": incident.get("incident_id"),
        "channel": channel,
        "system_prompt": f"{prompt}\n\n{brief_context(incident)}",
        "greeting": greeting_for(incident, channel=channel),
        "keyterms": keyterms_for(incident),
        "language_codes": codes,
        "tools": list(TOOL_NAMES),
    }
