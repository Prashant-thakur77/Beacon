"""Telegram: pages with buttons, voice notes with confidence, the same consent."""

from __future__ import annotations

import io
import json
from typing import Any
from urllib.parse import urlparse

import pytest

from beacon import approvals, channels, store, telegram, telegram_bot
from tests.test_voice_tools import APPROVALS, INCIDENTS, env  # noqa: F401 (fixture)


class FakeHttp:
    """Answers urllib for api.telegram.org and api.assemblyai.com; records calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.transcript = {"text": "approve fix one", "confidence": 0.97}

    def __call__(self, req: Any, timeout: float = 0) -> Any:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        host = urlparse(url).netloc
        path = urlparse(url).path
        body = None
        if hasattr(req, "data") and req.data:
            body = req.data
        if host == "api.telegram.org":
            method = path.rsplit("/", 1)[-1]
            if body and req.get_header("Content-type", "").startswith(
                "application/json"
            ):
                self.calls.append((method, json.loads(body)))
            else:
                self.calls.append(
                    (method, {"multipart": True, "bytes": len(body or b"")})
                )
            if method == "getFile":
                out = {"ok": True, "result": {"file_path": "voice/file_1.oga"}}
            elif path.startswith("/file/"):
                return io.BytesIO(b"OggS-opus-bytes")
            else:
                out = {"ok": True, "result": {"message_id": 7}}
            return _Resp(out)
        if host == "api.assemblyai.com":
            if path == "/v2/upload":
                return _Resp({"upload_url": "https://cdn.assemblyai.com/upload/abc"})
            if path == "/v2/transcript" and req.get_method() == "POST":
                self.calls.append(("aai.transcript", json.loads(body or b"{}")))
                return _Resp({"id": "t1", "status": "queued"})
            if path.startswith("/v2/transcript/"):
                words = [
                    {"text": w, "confidence": self.transcript["confidence"]}
                    for w in self.transcript["text"].split()
                ]
                return _Resp(
                    {
                        "id": "t1",
                        "status": "completed",
                        "text": self.transcript["text"],
                        "confidence": self.transcript["confidence"],
                        "words": words,
                        "language_code": "en",
                    }
                )
        raise AssertionError(f"unexpected url {url}")


class _Resp(io.BytesIO):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(json.dumps(payload).encode())
        self.status = 200

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@pytest.fixture()
def tg(env: Any, monkeypatch: pytest.MonkeyPatch, mocker: Any) -> Any:  # noqa: F811
    http = FakeHttp()
    monkeypatch.setattr("urllib.request.urlopen", http)
    for var, val in {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "TELEGRAM_CHAT_ID": "555",
        "TELEGRAM_ALLOWED_IDS": "42, 43",
        "TELEGRAM_WEBHOOK_SECRET": "s3cret",
        "ASSEMBLYAI_API_KEY": "aai-key",
        "DASHBOARD_URL": "https://console.example",
    }.items():
        monkeypatch.setenv(var, val)
    mocker.patch("beacon.telegram.speak", return_value=b"mp3")
    return {"http": http, **env}


def _update(
    text: str | None = None, *, voice: bool = False, user: int = 42
) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "message_id": 1,
        "from": {"id": user, "first_name": "Prashant"},
        "chat": {"id": 555, "type": "private"},
    }
    if voice:
        msg["voice"] = {"file_id": "AwACAgQ", "duration": 2}
    else:
        msg["text"] = text
    return {"update_id": 1, "message": msg}


def _sent(http: FakeHttp, method: str) -> list[dict[str, Any]]:
    return [p for m, p in http.calls if m == method]


def _approvals(incident_id: str) -> list[dict[str, Any]]:
    rows = approvals.list_for_incident(incident_id, table_name=APPROVALS)
    return [r for r in rows if r.get("kind") == "approval"]


def test_page_carries_talk_fix_ack_buttons(tg: Any) -> None:
    sent = channels.send(
        "page",
        "beacon-demo-infra-errors",
        "DB unreachable",
        incident_id=tg["incident_id"],
    )
    assert sent["telegram"] is True
    msg = _sent(tg["http"], "sendMessage")[0]
    buttons = msg["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["Talk", "Fix 1", "Ack"]
    assert buttons[0]["url"] == f"https://console.example/#board/{tg['incident_id']}"
    assert buttons[1]["callback_data"] == f"propose:{tg['incident_id']}"
    assert msg["text"].startswith("🔴 beacon-demo-infra-errors")


def test_webhook_rejects_a_bad_secret_and_strangers(tg: Any) -> None:
    out = telegram_bot.handle_event(
        {"headers": {"x-telegram-bot-api-secret-token": "nope"}, "body": "{}"}
    )
    assert out["statusCode"] == 401
    out = telegram_bot.handle_event(
        {
            "headers": {"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
            "body": json.dumps(_update("fix it", user=99)),
        }
    )
    assert (
        out["statusCode"] == 200 and json.loads(out["body"])["ignored"] == "not allowed"
    )
    assert "allowlist" in _sent(tg["http"], "sendMessage")[-1]["text"]


def test_typed_night_fix_approve_contract(tg: Any) -> None:
    """The whole night typed into the chat: brief → propose → approve → contract."""
    out = telegram_bot.handle_update(_update("kya hua"))
    assert out["tool"] == "get_incident_brief"
    assert "lost its database" in _sent(tg["http"], "sendMessage")[-1]["text"]

    out = telegram_bot.handle_update(_update("isko fix kar do"))
    assert out["tool"] == "propose_fix" and out["ok"]
    text = _sent(tg["http"], "sendMessage")[-1]["text"]
    assert "Reply exactly: approve fix 1" in text and "security group" in text.lower()
    assert _sent(tg["http"], "sendVoice"), "the reply also goes back as a voice note"

    out = telegram_bot.handle_update(_update("yes do it"))
    assert out["tool"] is None  # not a phrase; help, nothing applied
    tg["sfn"].assert_not_called()

    out = telegram_bot.handle_update(_update("approve fix 1"))
    assert out["tool"] == "approve_fix" and out["ok"]
    tg["sfn"].assert_called_once()
    rows = _approvals(tg["incident_id"])
    assert (
        rows[0]["channel"] == "telegram"
        and rows[0]["transcript_quote"] == "approve fix 1"
    )
    assert (
        rows[0]["attestation"]["stt"] == "typed"
        and rows[0]["attestation"]["telegram_user_id"] == 42
    )

    out = telegram_bot.handle_update(_update("handle it next time"))
    assert out["tool"] == "grant_sleep_contract"
    assert (
        "Reply exactly: grant contract for 7 days"
        in _sent(tg["http"], "sendMessage")[-1]["text"]
    )
    out = telegram_bot.handle_update(_update("grant contract for 7 days"))
    assert out["tool"] == "grant_sleep_contract" and out["ok"]
    assert "granted" in _sent(tg["http"], "sendMessage")[-1]["text"].lower()


def test_voice_note_is_transcribed_with_confidence_and_gated(tg: Any) -> None:
    telegram_bot.handle_update(_update("fix it"))
    http: FakeHttp = tg["http"]

    http.transcript = {"text": "approve fix one", "confidence": 0.71}
    out = telegram_bot.handle_update(_update(voice=True))
    assert out["heard"] == "approve fix one" and out["refused"] is True
    text = _sent(http, "sendMessage")[-1]["text"]
    assert "71%" in text and "once more" in text
    tg["sfn"].assert_not_called()
    aai = [p for m, p in http.calls if m == "aai.transcript"][-1]
    assert (
        aai["language_detection"] is True
        and "approve fix one" in aai["keyterms_prompt"]
    )
    assert "api.telegram.org" not in aai["audio_url"], (
        "the bot token never leaves for AssemblyAI"
    )

    http.transcript = {"text": "Approve fix one.", "confidence": 0.96}
    out = telegram_bot.handle_update(_update(voice=True))
    assert out["tool"] == "approve_fix" and out["ok"] and out["refused"] is False
    tg["sfn"].assert_called_once()
    row = _approvals(tg["incident_id"])[0]
    assert row["attestation"]["stt"] == "assemblyai-prerecorded"
    assert row["attestation"]["telegram_file_id"] == "AwACAgQ"
    assert row["attestation"]["confidence"] == pytest.approx(0.96)
    assert "Heard: “Approve fix one.” (96%)" in _sent(http, "sendMessage")[-1]["text"]


def test_fix_button_reads_back_and_never_applies(tg: Any) -> None:
    update = {
        "update_id": 2,
        "callback_query": {
            "id": "cb1",
            "from": {"id": 43},
            "data": f"propose:{tg['incident_id']}",
            "message": {"message_id": 3, "chat": {"id": 555}},
        },
    }
    out = telegram_bot.handle_update(update)
    assert out["tool"] == "propose_fix" and out["ok"]
    assert [m for m, _ in tg["http"].calls if m == "answerCallbackQuery"]
    assert (
        "Reply exactly: approve fix 1" in _sent(tg["http"], "sendMessage")[-1]["text"]
    )
    tg["sfn"].assert_not_called()
    update["callback_query"]["data"] = f"ack:{tg['incident_id']}"
    out = telegram_bot.handle_update(update)
    assert out["acked"] is True
    inc = store.get_incident(tg["incident_id"], table_name=INCIDENTS)
    assert inc["timeline"][-1]["event"] == "acknowledged"


def test_commands_and_route_table(tg: Any) -> None:
    assert telegram_bot.handle_update(_update("/status"))["command"] == "/status"
    assert "beacon-demo-infra-errors" in _sent(tg["http"], "sendMessage")[-1]["text"]
    assert telegram_bot.handle_update(_update("/help"))["command"] == "/help"
    assert telegram.route("undo fix two") == (
        "undo_fix",
        {"fix_id": 2, "confirmation_phrase": "undo fix 2"},
    )
    assert telegram.route("saat din ke liye contract do") == (
        "grant_sleep_contract",
        {"days": 7, "max_uses": 3},
    )
    assert telegram.route("what changed?") == ("get_evidence", {"kind": "changes"})
    assert telegram.route("is it fixed") == ("check_recovery", {})
    assert telegram.route("hello there") is None
