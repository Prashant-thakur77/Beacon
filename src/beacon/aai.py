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
