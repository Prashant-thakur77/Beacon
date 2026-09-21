"""Telegram inbound: one update → one tool → one reply (see ``telegram.py``)."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from beacon import aws, channels, contracts, store, telegram, voice_tools
from beacon.telegram import HELP, MIN_CONSENT_CONFIDENCE, call, reply_text_and_voice
from beacon.turn_context import TurnContext, turn_context

logger = logging.getLogger(__name__)

_CONSENT_TOOLS = ("approve_fix", "grant_sleep_contract", "undo_fix")
_MAX_TEXT = 500


def _incidents_table() -> str:
    return os.environ.get("INCIDENTS_TABLE_NAME", "")


def _approvals_table() -> str:
    return os.environ.get("APPROVALS_TABLE_NAME", "")


def _focus_key(chat_id: Any) -> str:
    return f"telegram-focus:{chat_id}"


def _all_incidents() -> list[dict[str, Any]]:
    client = aws.client("dynamodb")
    kwargs: dict[str, Any] = {"TableName": _incidents_table()}
    rows: list[dict[str, Any]] = []
    while True:
        resp = client.scan(**kwargs)
        rows.extend(store._deserialize_item(raw) for raw in resp.get("Items", []))
        if not resp.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    rows.sort(key=lambda r: str(r.get("timestamp", "")), reverse=True)
    return rows


def focus_incident(chat_id: Any) -> dict[str, Any] | None:
    """The incident this chat is talking about: ``/use <id>`` if set, else the
    newest one that still needs a human, else the newest of the night."""
    client = aws.client("dynamodb")
    try:
        got = client.get_item(
            TableName=_approvals_table(),
            Key={"approval_id": {"S": _focus_key(chat_id)}},
        ).get("Item")
    except Exception:
        got = None
    if got and got.get("incident_id", {}).get("S"):
        inc = store.get_incident(got["incident_id"]["S"], table_name=_incidents_table())
        if inc:
            return inc
    rows = _all_incidents()
    for row in rows:
        if row.get("status") in ("awaiting_engineer", "remediating", "escalated"):
            return row
    return rows[0] if rows else None


def set_focus(chat_id: Any, incident_id: str) -> None:
    aws.client("dynamodb").put_item(
        TableName=_approvals_table(),
        Item={
            "approval_id": {"S": _focus_key(chat_id)},
            "kind": {"S": "telegram-focus"},
            "incident_id": {"S": incident_id},
            "ttl": {"N": str(int(time.time()) + 7 * 86400)},
        },
    )


def run_tool(
    name: str,
    args: dict[str, Any],
    *,
    incident: dict[str, Any],
    transcript: str,
    attestation: dict[str, Any],
) -> dict[str, Any]:
    """Execute one tool for this chat with the consent gate the browser has."""
    conf = attestation.get("confidence")
    if (
        name in _CONSENT_TOOLS
        and isinstance(conf, int | float)
        and conf < MIN_CONSENT_CONFIDENCE
    ):
        return {
            "refused": True,
            "error": (
                f"I heard “{transcript}” at {int(conf * 100)}% — send it once more, "
                "or type it."
            ),
        }
    ctx = TurnContext(
        incident_id=str(incident["incident_id"]),
        session_id=f"telegram-{attestation.get('telegram_user_id', 'user')}",
        transcript=transcript,
        channel="telegram",
        passcode_ok=True,
        attestation=attestation,
    )
    with turn_context(ctx):
        result = voice_tools.dispatch(name, args)
    result["_events"] = ctx.tool_events
    return result


def wording(name: str, result: dict[str, Any]) -> str:
    """What to say back for a tool result, written for a phone screen."""
    if result.get("refused") or (
        "error" in result and not result.get("read_back_pending")
    ):
        return str(result.get("error"))
    if name == "get_incident_brief":
        return str(
            result.get("spoken_summary") or result.get("summary") or "No summary yet."
        )
    if name == "get_evidence":
        data = result.get("data")
        if result.get("kind") == "changes":
            rows = data if isinstance(data, list) else []
            if not rows:
                return "No write API calls in the hour before the alarm."
            top = rows[0]
            ids = ", ".join(str(i) for i in top.get("resource_ids", [])) or "-"
            return (
                f"{top.get('event_name')} by {top.get('actor_short')} at "
                f"{str(top.get('event_time', ''))[11:19]} UTC on {ids}. "
                f"{len(rows)} change(s) in the window."
            )
        return json.dumps(data)[:600] if data else "Nothing notable."
    if name == "propose_fix":
        blast = result.get("blast_radius_spoken") or result.get("blast_radius") or ""
        return f"{blast}\n\nReply exactly: {result.get('confirmation_phrase')}"
    if name == "cancel_proposal":
        return str(result.get("spoken_hint"))
    if name == "approve_fix":
        if result.get("approved"):
            return (
                "Approved. Applying and verifying now; I will message you when "
                "the alarm clears."
            )
        return str(result.get("error") or "Not approved.")
    if name == "undo_fix":
        return str(result.get("spoken_hint") or result.get("error") or "Undo done.")
    if name == "check_recovery":
        status = str(result.get("status"))
        last = result.get("last_verify") or {}
        if status == "resolved":
            checks = last.get("checks") or []
            passed = sum(1 for c in checks if c.get("ok"))
            return (
                f"Resolved: verified on attempt {last.get('attempt')}, "
                f"{passed}/{len(checks)} checks passed."
            )
        if status == "remediating":
            return (
                f"Still verifying (attempt {last.get('attempt') or 0} so far); "
                "I will message you when it clears."
            )
        if status == "escalated":
            return "Escalated: the fix did not verify. It needs a human."
        return f"Status: {status.replace('_', ' ')}. Nothing has been applied yet."
    if name == "grant_sleep_contract":
        if result.get("granted"):
            return str(result.get("spoken") or "Contract granted. Sleep well.")
        if result.get("read_back_pending"):
            days = int(result.get("days", 7))
            read_back = result.get("read_back_spoken") or result.get("read_back")
            return f"{read_back}\n\nReply exactly: grant contract for {days} days"
        return str(result.get("error") or "Not granted.")
    return json.dumps(result)[:500]


def reply(chat_id: Any, text: str, *, voice: bool = True) -> None:
    reply_text_and_voice(chat_id, text, voice=voice)


def _status_text() -> str:
    rows = _all_incidents()[:5]
    if not rows:
        return "Quiet night: no incidents."
    lines = []
    for r in rows:
        lines.append(
            f"• {r.get('alarm_name')} — {r.get('status')} "
            f"({str(r.get('timestamp', ''))[11:16]} UTC)"
        )
    return "\n".join(lines)


def _contracts_text() -> str:
    rows = [
        c
        for c in contracts._scan(
            os.environ.get("CONTRACTS_TABLE_NAME", ""), aws.client("dynamodb")
        )
        if c.get("status") == "active"
    ]
    if not rows:
        return "No active Sleep Contracts."
    return "\n".join(
        f"• {c.get('alarm_name')}: {c.get('action')} until "
        f"{str(c.get('expires_at', ''))[:10]}, "
        f"{c.get('uses', 0)}/{c.get('max_uses')} uses"
        for c in rows
    )


def handle_update(update: dict[str, Any]) -> dict[str, Any]:
    """One Telegram update → one reply. Returns what happened (for tests/logs)."""
    cb = update.get("callback_query")
    msg = update.get("message") or (cb or {}).get("message") or {}
    user = (cb or update.get("message") or {}).get("from") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    user_id = user.get("id")
    if chat_id is None or user_id is None:
        return {"ignored": "no chat"}
    if not telegram.allowed(user_id):
        call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "This bot only answers its on-call allowlist.",
            },
        )
        return {"ignored": "not allowed", "user_id": user_id}

    if cb:
        call("answerCallbackQuery", {"callback_query_id": cb.get("id")})
        data = str(cb.get("data", ""))
        kind, _, incident_id = data.partition(":")
        incident = (
            store.get_incident(incident_id, table_name=_incidents_table())
            if incident_id
            else None
        )
        if not incident:
            reply(chat_id, "That incident is gone from the board.", voice=False)
            return {"callback": data, "missing": True}
        set_focus(chat_id, incident_id)
        if kind == "propose":
            result = run_tool(
                "propose_fix",
                {},
                incident=incident,
                transcript="Fix 1 button",
                attestation={"telegram_user_id": user_id, "via": "button"},
            )
            reply(chat_id, wording("propose_fix", result))
            return {
                "callback": data,
                "tool": "propose_fix",
                "ok": "error" not in result,
            }
        if kind == "ack":
            store.append_timeline(
                incident_id,
                "acknowledged",
                table_name=_incidents_table(),
                detail={"channel": "telegram", "user_id": str(user_id)},
            )
            reply(
                chat_id,
                "Acknowledged. It stays on the board; I will not page you again "
                "for it unless it escalates.",
                voice=False,
            )
            return {"callback": data, "acked": True}
        return {"callback": data, "ignored": "unknown"}

    text = str(msg.get("text") or "").strip()
    voice_note = msg.get("voice") or msg.get("audio")
    attestation: dict[str, Any] = {
        "telegram_user_id": user_id,
        "telegram_message_id": msg.get("message_id"),
    }

    if text.startswith("/"):
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/start", "/help"):
            reply(chat_id, HELP, voice=False)
        elif cmd == "/status":
            reply(chat_id, _status_text(), voice=False)
        elif cmd == "/contracts":
            reply(chat_id, _contracts_text(), voice=False)
        elif cmd == "/report":
            link = channels.deep_link(None).replace("#board", "#report")
            reply(
                chat_id,
                f"Morning report: {link}" if link else "No console URL configured.",
                voice=False,
            )
        elif cmd == "/use" and arg.strip():
            inc = store.get_incident(arg.strip(), table_name=_incidents_table())
            if inc:
                set_focus(chat_id, str(inc["incident_id"]))
                reply(
                    chat_id,
                    f"Talking about {inc.get('alarm_name')} ({inc.get('status')}).",
                    voice=False,
                )
            else:
                reply(chat_id, "No incident with that id.", voice=False)
        else:
            reply(chat_id, HELP, voice=False)
        return {"command": cmd}

    if voice_note:
        file_id = str(voice_note.get("file_id", ""))
        audio = telegram.download(file_id)
        if audio is None:
            reply(
                chat_id,
                "I could not fetch that voice note; try again or type it.",
                voice=False,
            )
            return {"voice": file_id, "error": "download"}
        incident_for_terms = focus_incident(chat_id) or {}
        terms = [
            str(incident_for_terms.get("alarm_name") or ""),
            "approve fix one",
            "approve fix two",
            "undo fix one",
            "grant contract for seven days",
            "Beacon",
        ]
        try:
            heard = telegram.transcribe(audio, keyterms=terms)
        except Exception as exc:
            logger.warning("transcription failed: %s", exc)
            reply(
                chat_id,
                "I could not transcribe that; try again or type it.",
                voice=False,
            )
            return {"voice": file_id, "error": "stt"}
        text = heard["text"]
        attestation.update(
            {
                "stt": "assemblyai-prerecorded",
                "confidence": heard["confidence"],
                "language": heard.get("language"),
                "telegram_file_id": file_id,
                "assemblyai_transcript_id": heard.get("transcript_id"),
            }
        )
        if not text.strip():
            reply(
                chat_id, "I heard silence. Say it once more, or type it.", voice=False
            )
            return {"voice": file_id, "heard": ""}
    else:
        attestation["stt"] = "typed"

    if len(text) > _MAX_TEXT:
        reply(chat_id, "That is long for a phone; keep it to one request.", voice=False)
        return {"error": "too long"}

    incident = focus_incident(chat_id)
    if incident is None:
        reply(chat_id, "Quiet night: there is no incident to talk about.", voice=False)
        return {"heard": text, "no_incident": True}
    routed = telegram.route(text)
    if routed is None:
        reply(
            chat_id,
            (f"I heard: “{text}”\n\n" if voice_note else "") + HELP,
            voice=False,
        )
        return {"heard": text, "tool": None}
    name, args = routed
    result = run_tool(
        name, args, incident=incident, transcript=text, attestation=attestation
    )
    prefix = (
        f"Heard: “{text}”"
        + (
            f" ({int(attestation['confidence'] * 100)}%)"
            if isinstance(attestation.get("confidence"), int | float)
            else ""
        )
        + "\n\n"
        if voice_note
        else ""
    )
    reply(chat_id, prefix + wording(name, result))
    return {
        "heard": text,
        "tool": name,
        "ok": "error" not in result,
        "refused": bool(result.get("refused")),
    }


def handle_event(event: dict[str, Any]) -> dict[str, Any]:
    """Function-URL entry: verify the webhook secret, then handle the update."""
    headers = {str(k): str(v) for k, v in (event.get("headers") or {}).items()}
    if not telegram.webhook_secret_ok(headers):
        return {"statusCode": 401, "body": json.dumps({"error": "bad secret"})}
    try:
        update = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": json.dumps({"error": "bad json"})}
    try:
        out = handle_update(update)
    except Exception:
        logger.exception("telegram update failed")
        out = {"error": "internal"}
    # Always 200: Telegram retries anything else, and a retry would re-run a tool.
    return {"statusCode": 200, "body": json.dumps(out)}
