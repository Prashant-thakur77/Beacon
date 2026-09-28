"""AssemblyAI beyond the socket: session lookup and summarising what was said.

Three AssemblyAI products serve three different jobs in Beacon:

* the **Voice Agent API** is the live conversation (``web/src/voice/assemblyai.ts``),
* **pre-recorded transcription** reads Telegram voice notes, with word confidence
  and PII redaction (``telegram.py``),
* and this module closes the loop: it finds the **session recording** the agent
  kept, and asks the same pre-recorded API to **summarise** it, so the postmortem
  can quote the night in the engineer's own words instead of the model's.

Only urllib and SSM are used, so the triage image can import this without the
Powertools layer.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

from beacon import aws

logger = logging.getLogger(__name__)

# `agents.assemblyai.com` is geo-DNS and sessions are sharded behind it: a browser
# in India creates its session on the EU cluster while a Lambda in us-east-1
# resolves to the US one, and each answers 404 for the other's sessions.
SESSION_HOSTS = tuple(
    h.strip()
    for h in os.environ.get(
        "ASSEMBLYAI_SESSION_HOSTS",
        "agents.eu.assemblyai.com,agents.us.assemblyai.com,agents.assemblyai.com",
    ).split(",")
    if h.strip()
)
API = "https://api.assemblyai.com/v2"
_TIMEOUT = 15
_SUMMARY_WAIT_SECONDS = 90
_cache: dict[str, str] = {}


def key() -> str:
    """The API key: environment first (local runs), then the SSM SecureString."""
    direct = os.environ.get("ASSEMBLYAI_API_KEY", "")
    if direct:
        return direct
    param = os.environ.get("ASSEMBLYAI_KEY_PARAM", "")
    if not param:
        return ""
    if param not in _cache:
        try:
            _cache[param] = str(
                aws.client("ssm").get_parameter(Name=param, WithDecryption=True)[
                    "Parameter"
                ]["Value"]
            )
        except Exception:
            logger.exception("assemblyai key %s unreadable", param)
            return ""
    return _cache[param]


def _json(
    method: str, url: str, body: Any = None, ctype: str = "application/json"
) -> Any:
    data = (
        body
        if isinstance(body, bytes)
        else (json.dumps(body).encode() if body else None)
    )
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"authorization": key(), "content-type": ctype},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def session(session_id: str) -> dict[str, Any] | None:
    """The session record, asking each regional host until one owns it."""
    api_key = key()
    if not api_key:
        return None
    for host in SESSION_HOSTS:
        req = urllib.request.Request(
            f"https://{host}/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
                return dict(json.loads(resp.read().decode()))
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                logger.warning("session %s on %s: %s", session_id, host, exc.code)
        except Exception as exc:
            logger.warning("session %s on %s: %s", session_id, host, exc)
    return None


def audio_url(session_id: str) -> str | None:
    """The short-lived, presigned URL of the session recording."""
    data = session(session_id) or {}
    audio = next(
        (a for a in data.get("artifacts") or [] if a.get("type") == "audio"), None
    )
    return str(audio["url"]) if audio and audio.get("url") else None


def summarise(url: str, *, keyterms: list[str] | None = None) -> dict[str, Any]:
    """Transcribe a recording and summarise it — redacted, because the summary is
    stored on the incident and printed in the postmortem.

    Returns ``{"summary", "text", "transcript_id", "seconds"}``; raises on failure
    so the caller can decide whether a missing summary is worth reporting.
    """
    body: dict[str, Any] = {
        "audio_url": url,
        "summarization": True,
        # `informative` reads like a postmortem; `conversational` needs speaker
        # labels and, on a single mixed channel, invents a cast of speakers.
        "summary_model": "informative",
        "summary_type": "bullets",
        "redact_pii": True,
        "redact_pii_policies": [
            "person_name",
            "phone_number",
            "email_address",
            "credit_card_number",
            "banking_information",
            "us_social_security_number",
        ],
        "redact_pii_sub": "hash",
        "language_code": "en",
    }
    if keyterms:
        body["keyterms_prompt"] = [k for k in keyterms if k][:50]
    job = _json("POST", f"{API}/transcript", body)
    deadline = time.time() + _SUMMARY_WAIT_SECONDS
    while time.time() < deadline:
        got = _json("GET", f"{API}/transcript/{job['id']}")
        if got.get("status") == "completed":
            return {
                "summary": str(got.get("summary") or "").strip(),
                "text": str(got.get("text") or "").strip(),
                "transcript_id": got.get("id"),
                "seconds": got.get("audio_duration"),
            }
        if got.get("status") == "error":
            raise RuntimeError(str(got.get("error")))
        time.sleep(1.5)
    raise TimeoutError("the summary did not finish in time")


def upload(audio: bytes) -> str:
    """Put a recording where AssemblyAI can read it; returns the internal URL.

    Used for the phone bridge's own stereo recording, which no presigned URL
    exists for because Beacon made it rather than a carrier.
    """
    return str(
        _json("POST", f"{API}/upload", audio, "application/octet-stream")["upload_url"]
    )


def transcribe_call(
    url: str, *, keyterms: list[str] | None = None, language: str = "en"
) -> dict[str, Any]:
    """Transcribe a two-channel call recording, keeping the channels apart.

    A phone bridge records the caller on one channel and the agent on the other, so
    ``dual_channel`` gives every utterance a channel without guessing at speakers --
    and the one sentence that authorised a change to production can be attributed to
    the human beyond argument. Diarisation would have to infer that; two channels
    carry it in the file.

    Speaker labels are requested as well, which AssemblyAI returns as channel then
    speaker -- ``1A``, ``2A`` -- so a *second* voice on the caller's channel shows up
    as ``1B``. Consent that arrived while somebody else was in the room is then a
    fact about the recording rather than something nobody thought to check.

    Returns ``{"text", "utterances": [{"channel", "speaker", "text", "confidence",
    "start"}], "transcript_id", "seconds"}``.
    """
    body: dict[str, Any] = {
        "audio_url": url,
        "dual_channel": True,
        # Asked for alongside dual_channel on purpose, and verified to work: the
        # speaker comes back as channel-then-speaker ("1A", "2A"), so a second
        # person on the caller's side of the call appears as "1B". That is the
        # difference between "somebody approved this" and "the person we called did".
        "speaker_labels": True,
        "language_code": language,
        "punctuate": True,
        "format_text": True,
    }
    if keyterms:
        body["keyterms_prompt"] = [k for k in keyterms if k][:50]
    job = _json("POST", f"{API}/transcript", body)
    deadline = time.time() + _SUMMARY_WAIT_SECONDS
    while time.time() < deadline:
        got = _json("GET", f"{API}/transcript/{job['id']}")
        if got.get("status") == "completed":
            return {
                "text": str(got.get("text") or "").strip(),
                "utterances": [
                    {
                        "channel": str(u.get("channel") or ""),
                        "speaker": str(u.get("speaker") or ""),
                        "text": str(u.get("text") or "").strip(),
                        "confidence": u.get("confidence"),
                        "start": u.get("start"),
                    }
                    for u in got.get("utterances") or []
                ],
                "transcript_id": got.get("id"),
                "seconds": got.get("audio_duration"),
            }
        if got.get("status") == "error":
            raise RuntimeError(str(got.get("error")))
        time.sleep(1.5)
    raise TimeoutError("the call transcript did not finish in time")


def audit_session(
    session_id: str, *, keyterms: list[str] | None = None
) -> dict[str, Any]:
    """Re-transcribe a live session's own recording, with word-level confidence.

    The Voice Agent API does not report confidence on a live turn -- there is no
    number on ``transcript.user`` -- so an approval spoken in the browser or down a
    phone is acted on without one, while the same words sent as a Telegram voice
    note are held to 85%. That is a real gap, and this closes it after the fact:
    AssemblyAI transcribes its own recording of the session with the pre-recorded
    model, which does return ``words[].confidence``, and the phrase that authorised
    the change can be checked against the bar it should have cleared.

    It is deliberately a second opinion rather than a gate on the turn: waiting a
    minute for a transcript before applying a fix would be the wrong trade at 3 AM.
    A change whose approval turns out to have been heard poorly is *flagged*, which
    is what the audit is for.

    Returns ``{"text", "words": [{"text", "confidence", "start"}], "transcript_id",
    "seconds"}``.
    """
    url = audio_url(session_id)
    if not url:
        raise LookupError(f"no recording for session {session_id}")
    # A session recording is two channels -- the engineer on 1, the agent on 2 --
    # and a mono transcription downmixes them. The agent's synthesised voice is the
    # louder of the two, so the engineer's words were being buried: a live phone
    # approval whose recording clearly contains "Approve fix 2." at 40 s came back
    # reported as not in the recording at all. Transcribing per channel also keeps
    # the agent's own read-back of the phrase from being scored as the engineer's
    # consent, which a mono transcript cannot tell apart.
    body: dict[str, Any] = {
        "audio_url": url,
        "dual_channel": True,
        "punctuate": True,
        "format_text": True,
        "language_code": "en",
    }
    if keyterms:
        body["keyterms_prompt"] = [k for k in keyterms if k][:50]
    job = _json("POST", f"{API}/transcript", body)
    deadline = time.time() + _SUMMARY_WAIT_SECONDS
    while time.time() < deadline:
        got = _json("GET", f"{API}/transcript/{job['id']}")
        if got.get("status") == "completed":
            return {
                "text": str(got.get("text") or "").strip(),
                "words": [
                    {
                        "text": str(w.get("text") or ""),
                        "confidence": w.get("confidence"),
                        "start": w.get("start"),
                        "channel": str(w.get("channel") or ""),
                    }
                    for w in got.get("words") or []
                ],
                "transcript_id": got.get("id"),
                "seconds": got.get("audio_duration"),
                "session_id": session_id,
            }
        if got.get("status") == "error":
            raise RuntimeError(str(got.get("error")))
        time.sleep(1.5)
    raise TimeoutError("the session transcript did not finish in time")


def summarise_session(
    session_id: str, *, keyterms: list[str] | None = None
) -> dict[str, Any]:
    """Find the recording for a live voice session and summarise it."""
    url = audio_url(session_id)
    if not url:
        raise LookupError(f"no recording for session {session_id}")
    out = summarise(url, keyterms=keyterms)
    out["session_id"] = session_id
    return out
