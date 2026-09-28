"""The consent certificate: why one change to production was allowed to happen.

A voice agent that can change infrastructure has a problem no chatbot has. Six weeks
later somebody asks *who told it to do that* — an auditor, a postmortem, a customer,
a regulator — and "the model decided to" is not an answer anybody accepts. The answer
has to be an artifact: portable, dated, and checkable by a person who was not there
and does not trust you.

That artifact is almost entirely AssemblyAI's work, which is the point:

* **what was said** — the phrase, from the transcript, never from the model's argument
* **how clearly** — per-word confidence, scored by the weakest word in the phrase
* **through which channel** — and on a phone, which side of a two-channel recording
* **the recording itself** — so the words can be played back, not just read

Around that sits the part code decides: the one allowlisted action, the parameters
that passed a dry run, and the CloudWatch checks that had to agree before anything
was called recovered.

Nothing here computes anything new. It is an assembly of records that already exist,
in one shape, so that a change can be justified without a tour of four systems.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA = "beacon.consent-certificate/1"


def _confidence_of(
    approval: dict[str, Any], attested: dict[str, Any]
) -> dict[str, Any]:
    """How clearly the phrase was heard, and where that number came from.

    A voice note is scored at the time, because the pre-recorded API returns
    per-word confidence. A live turn is not -- the Voice Agent API sends no number
    with ``transcript.user`` -- so it is scored afterwards against AssemblyAI's own
    recording of the session. Saying which of the two produced the number matters:
    one gated the change, the other reviewed it.
    """
    attestation = attest_of(approval, attested)
    if attestation is not None:
        return {
            "value": attestation.get("confidence"),
            "weakest_word": attestation.get("weakest_word"),
            "measured": "after the fact, from the session recording",
            "gated_the_change": False,
        }
    live = (approval.get("attestation") or {}).get("confidence")
    if isinstance(live, int | float):
        return {
            "value": round(float(live), 4),
            "weakest_word": None,
            "measured": "at the time, before the change was applied",
            "gated_the_change": True,
        }
    return {
        "value": None,
        "weakest_word": None,
        "measured": "not measured",
        "gated_the_change": False,
        "note": (
            "a live turn carries no confidence; run POST /sessions/<id>/attest "
            "to score it against the recording"
        ),
    }


def attest_of(
    approval: dict[str, Any], attested: dict[str, Any]
) -> dict[str, Any] | None:
    """The after-the-fact check for this approval's phrase, if one was run."""
    phrase = str(approval.get("transcript_quote") or "").strip().lower()
    for verdict in (attested or {}).values():
        for check in verdict.get("checks") or []:
            if str(check.get("phrase") or "").strip().lower() == phrase:
                return dict(check)
    return None


def build(
    incident: dict[str, Any],
    approval: dict[str, Any],
    *,
    recording_url: str | None = None,
) -> dict[str, Any]:
    """Assemble the certificate for one approval on one incident."""
    attestation = approval.get("attestation") or {}
    attested = incident.get("voice_attestation") or {}
    empty: dict[str, Any] = {}
    proposal: dict[str, Any] = next(
        (
            p
            for p in reversed(incident.get("proposals") or [])
            if p.get("fix_id") == approval.get("fix_id")
        ),
        empty,
    )
    last = (incident.get("verification") or {}).get("last") or {}
    checks = last.get("checks") or []
    change = (incident.get("changes") or [{}])[0]

    body: dict[str, Any] = {
        "schema": SCHEMA,
        "incident_id": incident.get("incident_id"),
        "alarm": incident.get("alarm_name"),
        "consent": {
            "phrase": approval.get("transcript_quote"),
            "channel": approval.get("channel"),
            "spoken_at": str(approval.get("granted_at") or "")[:19],
            "transcriber": attestation.get("stt"),
            "session_id": attestation.get("session_id"),
            "confidence": _confidence_of(approval, attested),
        },
        "change": {
            "action": approval.get("action") or proposal.get("action"),
            "params": approval.get("params") or proposal.get("params") or {},
            "blast_radius": proposal.get("blast_radius"),
            "dry_run": (proposal.get("dry_run") or {}).get("code"),
        },
        "caused_by": (
            {
                "event": change.get("event_name"),
                "actor": change.get("actor_short"),
                "at": change.get("event_time"),
                "resources": change.get("resource_ids") or [],
            }
            if change
            else None
        ),
        "verification": {
            "attempt": last.get("attempt"),
            "passed": sum(1 for c in checks if c.get("ok")),
            "of": len(checks),
            "checks": [
                {
                    "name": c.get("name"),
                    "ok": bool(c.get("ok")),
                    "detail": c.get("detail"),
                }
                for c in checks
            ],
            "resolved_at": incident.get("resolved_at"),
        },
        "proof": {
            "recording": recording_url,
            "approval_id": approval.get("approval_id"),
        },
    }
    body["certificate_id"] = _fingerprint(body)
    return body


def _fingerprint(body: dict[str, Any]) -> str:
    """A short digest of the certificate's contents.

    Not a signature and not claimed to be one: it detects a certificate that has been
    edited since it was issued, which is all it is for. Signing would need a key
    nobody has agreed to manage yet, and a fake signature is worse than none.
    """
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


def to_markdown(cert: dict[str, Any]) -> str:
    """The certificate as it appears in a pull request body."""
    consent = cert["consent"]
    conf = consent["confidence"]
    change = cert["change"]
    verification = cert["verification"]

    if conf.get("value") is None:
        heard = "confidence not measured"
    else:
        pct = f"{float(conf['value']):.0%}"
        when = (
            "checked before the change"
            if conf["gated_the_change"]
            else "checked afterwards"
        )
        weakest = (
            f", weakest word “{conf['weakest_word']}”"
            if conf.get("weakest_word")
            else ""
        )
        heard = f"heard at **{pct}** ({when}{weakest})"

    lines = [
        "### Consent certificate",
        "",
        f"> “{consent['phrase']}”",
        "",
        f"- **Said** via `{consent['channel']}` at {consent['spoken_at']}Z, "
        f"transcribed by `{consent['transcriber']}`, {heard}.",
        f"- **Authorised** `{change['action']}` with "
        f"`{json.dumps(change['params'], sort_keys=True)}` — dry run "
        f"`{change['dry_run']}` before anything ran.",
    ]
    if change.get("blast_radius"):
        lines.append(f"- **Blast radius** {change['blast_radius']}")
    if verification.get("of"):
        lines.append(
            f"- **Verified** {verification['passed']}/{verification['of']} checks on "
            f"attempt {verification['attempt']}; CloudWatch had to agree before this "
            "was called recovered."
        )
    if cert.get("caused_by") and cert["caused_by"].get("event"):
        c = cert["caused_by"]
        lines.append(f"- **Caused by** `{c['event']}` by `{c['actor']}` at {c['at']}.")
    if cert["proof"].get("recording"):
        lines.append(
            f"- **Recording** [play the words back]({cert['proof']['recording']})"
        )
    lines += ["", f"`{cert['certificate_id']}`"]
    return "\n".join(lines)
