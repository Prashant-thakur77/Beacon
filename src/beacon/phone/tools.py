"""Running a tool from the phone leg: the same HTTP route the browser uses.

The bridge never touches DynamoDB, never assumes an IAM role and never decides
whether an action is allowed. It posts to ``/tools/<name>`` on the voice Lambda
with the transcript AssemblyAI produced, and the Lambda's consent rules answer.
That is the whole point: a new channel must not be a new way around the rules.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)
_TIMEOUT = 90


def _post(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"content-type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            return dict(json.loads(resp.read().decode() or "{}"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        logger.warning("%s -> %s %s", url, exc.code, detail)
        return {"ok": False, "result": {"error": f"{exc.code}: {detail}"}}


def http_runner(
    base_url: str,
    *,
    passcode: str,
    incident_id: str,
    session_id: str,
    channel: str = "phone",
) -> Any:
    """A ``ToolRunner`` bound to one incident and one call."""
    base = base_url.rstrip("/")
    headers = {"x-beacon-passcode": passcode}

    async def run(
        name: str, args: dict[str, Any], transcript: str, confidence: float | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "incident_id": incident_id,
            "session_id": session_id,
            "transcript": transcript,
            "channel": channel,
            "args": args,
        }
        if confidence is not None:
            body["confidence"] = confidence
        return await asyncio.to_thread(_post, f"{base}/tools/{name}", body, headers)

    return run


def fetch_brief(
    base_url: str, *, passcode: str, incident_id: str, channel: str = "phone"
) -> dict[str, Any]:
    """The session brief, from the server that owns it."""
    base = base_url.rstrip("/")
    req = urllib.request.Request(
        f"{base}/brief/{incident_id}?channel={channel}",
        headers={"x-beacon-passcode": passcode},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return dict(json.loads(resp.read().decode()))


TOKEN_URL = "https://agents.assemblyai.com/v1/token?expires_in_seconds=600"


def mint_token(base_url: str, *, passcode: str) -> str:
    """A short-lived AssemblyAI token, minted by the Lambda that holds the key.

    A local run has the key in the environment instead of in Parameter Store, so it
    mints its own rather than needing a deployment to exist. Deployed, the key never
    leaves the Lambda.
    """
    out = _post(
        f"{base_url.rstrip('/')}/assemblyai/token", {}, {"x-beacon-passcode": passcode}
    )
    token = str(out.get("token") or "")
    if token:
        return token
    import os

    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "")
    if not api_key:
        raise RuntimeError(f"no AssemblyAI token: {out}")
    req = urllib.request.Request(
        TOKEN_URL, headers={"Authorization": f"Bearer {api_key}"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        return str(json.loads(resp.read().decode())["token"])
