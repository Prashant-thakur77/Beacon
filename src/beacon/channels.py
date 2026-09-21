"""Where a page actually lands: Slack-compatible webhooks, PagerDuty, Telegram.

SNS email stays the baseline. When ``WEBHOOK_URL`` is set, every event is also
posted as a Slack-style message with a deep link into the incident; when
``PAGERDUTY_ROUTING_KEY`` is set, a page opens a PagerDuty incident keyed by
the Beacon incident id, and a resolution closes it; when ``TELEGRAM_CHAT_ID`` is
set (with the bot token), the on-call chat gets the page with *Talk / Fix 1 / Ack*
buttons and can answer with a voice note (see ``telegram.py``). All are fire-and-forget:
a slow or failing channel never breaks triage or the loop.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_PD_URL = "https://events.pagerduty.com/v2/enqueue"
_TIMEOUT = 5

# event kind -> (PagerDuty action, severity)
_PD = {
    "page": ("trigger", "critical"),
    "escalated": ("trigger", "critical"),
    "resolved": ("resolve", "info"),
    "contract": (None, "info"),
    "undone": ("trigger", "warning"),
    "morning_report": (None, "info"),
}


def deep_link(incident_id: str | None) -> str:
    """The console URL that opens straight onto the incident."""
    base = os.environ.get("DASHBOARD_URL", "").rstrip("/")
    if not base:
        return ""
    return f"{base}/#board/{incident_id}" if incident_id else f"{base}/#board"


def _post(
    url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
) -> bool:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"content-type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            return bool(200 <= int(resp.status) < 300)
    except Exception:  # never let a chat/paging outage break the loop
        logger.exception("channel post to %s failed", url.split("/")[2])
        return False


def slack_payload(
    kind: str, title: str, text: str, *, incident_id: str | None, link: str
) -> dict[str, Any]:
    icon = {
        "page": "🔴",
        "escalated": "🆘",
        "resolved": "🟢",
        "contract": "🌙",
        "undone": "↩️",
        "morning_report": "☀️",
    }.get(kind, "•")
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{icon} {title}"[:150]},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}},
    ]
    if link:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open in Beacon"},
                        "url": link,
                        "style": "primary" if kind in ("page", "escalated") else None,
                    }
                ],
            }
        )
        # Slack rejects a null style; drop it when not set
        btn = blocks[-1]["elements"][0]
        if btn["style"] is None:
            del btn["style"]
    if incident_id:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"incident `{incident_id}`"}],
            }
        )
    return {"text": f"{title} — {text[:200]}", "blocks": blocks}


def pagerduty_payload(
    kind: str,
    title: str,
    text: str,
    *,
    incident_id: str | None,
    link: str,
    routing_key: str,
) -> dict[str, Any] | None:
    action, severity = _PD.get(kind, (None, "info"))
    if action is None:
        return None
    payload: dict[str, Any] = {
        "routing_key": routing_key,
        "event_action": action,
        "dedup_key": f"beacon:{incident_id or title}",
    }
    if action == "trigger":
        payload["payload"] = {
            "summary": title[:1024],
            "source": "beacon-night-shift",
            "severity": severity,
            "custom_details": {"detail": text[:4000], "incident_id": incident_id},
        }
        if link:
            payload["links"] = [{"href": link, "text": "Open in Beacon"}]
    return payload


def send(
    kind: str,
    title: str,
    text: str,
    *,
    incident_id: str | None = None,
) -> dict[str, bool]:
    """Fan one event out to every configured channel; returns what was attempted."""
    link = deep_link(incident_id)
    sent: dict[str, bool] = {}
    webhook = os.environ.get("WEBHOOK_URL", "")
    if webhook:
        sent["webhook"] = _post(
            webhook,
            slack_payload(kind, title, text, incident_id=incident_id, link=link),
        )
    key = os.environ.get("PAGERDUTY_ROUTING_KEY", "")
    if key:
        payload = pagerduty_payload(
            kind, title, text, incident_id=incident_id, link=link, routing_key=key
        )
        if payload is not None:
            sent["pagerduty"] = _post(_PD_URL, payload)
    if os.environ.get("TELEGRAM_CHAT_ID", ""):
        from beacon import telegram  # stdlib + boto3 only; safe in every image

        try:
            sent["telegram"] = telegram.send_event(
                kind, title, text, incident_id=incident_id, link=link
            )
        except Exception:  # same rule: a chat outage never breaks the loop
            logger.exception("telegram send failed")
            sent["telegram"] = False
    return sent
