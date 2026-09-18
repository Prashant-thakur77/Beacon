"""Voice-turn Lambda behind a Function URL (``beacon-voice-turn-<stack>``).

Routes (JSON, CORS open):

* ``GET /health``, plus the EventBridge keep-warm ping ``{"mode": "warm"}``.
* ``POST /session`` (passcode) -> 15-minute STS credentials for the browser
  mic, scoped to Transcribe streaming only (``BeaconMicRole``).
* ``POST /turn`` (passcode) -> one agent turn: the Strands agent on Nova 2
  Lite calls the six tools, the reply is spoken by Polly, and every tool
  event / evidence card is returned for the UI.
* direct invoke ``{"mode": "tool_only", ...}`` -> run one tool (CLI targets,
  and the AssemblyAI phase's tool route).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from functools import cache
from importlib.resources import files
from typing import Any, cast

import boto3
from aws_lambda_powertools.event_handler import (
    CORSConfig,
    LambdaFunctionUrlResolver,
    Response,
)

from beacon import store, voice_tools
from beacon.turn_context import TurnContext, turn_context

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

SERVICE = "beacon-voice-turn"
_CITATION_RE = re.compile(r"\s*\[(E\d+)\]")
_MAX_HISTORY = 20

app = LambdaFunctionUrlResolver(
    cors=CORSConfig(allow_origin="*", allow_headers=["x-beacon-passcode"])
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _incidents_table() -> str:
    return _env("INCIDENTS_TABLE_NAME")


@cache
def _system_prompt() -> str:
    return (
        files("beacon").joinpath("prompts/voice_system.txt").read_text(encoding="utf-8")
    )


def _json(status: int, body: dict[str, Any]) -> Response[str]:
    return Response(
        status_code=status,
        content_type="application/json",
        body=json.dumps(body, default=str),
    )


def _passcode_ok(headers: dict[str, str]) -> bool:
    expected = _env("PASSCODE")
    if not expected:
        return True
    given = headers.get("x-beacon-passcode") or headers.get("X-Beacon-Passcode") or ""
    return given == expected


def strip_citations(text: str) -> tuple[str, list[str]]:
    """Remove ``[E#]`` tags for speech; return the ids in order of appearance."""
    ids = _CITATION_RE.findall(text)
    clean = _CITATION_RE.sub("", text)
    clean = re.sub(r"\s+([.,;!?])", r"\1", clean)
    return re.sub(r"[ \t]{2,}", " ", clean).strip(), ids


# ---------------------------------------------------------------------------
# Speech
# ---------------------------------------------------------------------------


def _synthesize(text: str) -> dict[str, Any]:
    """Polly mp3 + sentence speech marks; falls back through voices/engines."""
    polly: Any = boto3.client("polly")
    attempts = [
        (_env("POLLY_VOICE_ID", "Kajal"), "neural"),
        ("Joanna", "neural"),
        ("Joanna", "standard"),
    ]
    last_error: Exception | None = None
    for voice, engine in attempts:
        try:
            audio = polly.synthesize_speech(
                Text=text, VoiceId=voice, Engine=engine, OutputFormat="mp3"
            )["AudioStream"].read()
            marks_raw = (
                polly.synthesize_speech(
                    Text=text,
                    VoiceId=voice,
                    Engine=engine,
                    OutputFormat="json",
                    SpeechMarkTypes=["sentence"],
                )["AudioStream"]
                .read()
                .decode("utf-8")
            )
            marks = [
                json.loads(line) for line in marks_raw.splitlines() if line.strip()
            ]
            return {
                "audio_b64": base64.b64encode(audio).decode("ascii"),
                "speech_marks": [
                    {"time": m.get("time", 0), "value": m.get("value", "")}
                    for m in marks
                ],
                "voice": voice,
            }
        except Exception as exc:  # try the next voice
            last_error = exc
            logger.warning("polly %s/%s failed: %s", voice, engine, exc)
    raise RuntimeError(f"polly failed: {last_error}")


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


def _build_agent(*, history: list[dict[str, Any]]) -> Any:
    """A Strands Agent on Nova 2 Lite with the six tools (or the litellm loop)."""
    if _env("VOICE_ENGINE", "strands") == "litellm":
        from beacon.voice_loop import LiteLLMAgent

        return LiteLLMAgent(system_prompt=_system_prompt(), history=history)

    from strands import Agent, tool
    from strands.models import BedrockModel

    model = BedrockModel(
        model_id=_env("NOVA_MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
        region_name=_env("BEDROCK_REGION", _env("AWS_REGION", "us-east-1")),
        temperature=0.3,
        max_tokens=500,
        streaming=False,
    )
    tools: list[Any] = [tool(fn) for fn in voice_tools.TOOL_FUNCTIONS.values()]
    return Agent(
        model=model,
        tools=tools,
        system_prompt=_system_prompt(),
        messages=cast("Any", history),
        callback_handler=None,
    )


def _extract_reply(agent: Any, result: Any, history_len: int) -> str:
    """Final assistant text from the run (falls back to str(result))."""
    for message in reversed(agent.messages[history_len:]):
        if message.get("role") != "assistant":
            continue
        texts = [
            b.get("text", "")
            for b in message.get("content", [])
            if isinstance(b, dict) and "text" in b
        ]
        if texts:
            return " ".join(t.strip() for t in texts if t.strip())
    return str(result).strip()


def _turn_prompt(body: dict[str, Any]) -> str:
    mode = body.get("mode", "chat")
    if mode == "brief":
        return (
            "The engineer just opened the incident. Brief them: call "
            "get_incident_brief, then in two or three sentences say what is wrong, "
            "the likely cause, and that you can propose a fix if they ask."
        )
    if mode == "event":
        event = str(body.get("event", ""))
        if event == "resolved":
            return (
                "System event: the remediation loop has verified recovery (resolved). "
                "Call check_recovery, then tell the engineer the alarm is back to OK "
                "in one sentence, and offer a Sleep Contract in one sentence."
            )
        if event == "escalated":
            return (
                "System event: verification failed (escalated). Call check_recovery "
                "and tell the engineer honestly what did not pass and that a human "
                "is needed."
            )
        return f"System event: {event}. Tell the engineer briefly."
    return str(body.get("text", "")).strip()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": SERVICE}


@app.post("/session")
def session() -> Response[str]:
    if not _passcode_ok(dict(app.current_event.headers)):
        return _json(401, {"error": "passcode required"})
    role_arn = _env("MIC_ROLE_ARN")
    if not role_arn:
        return _json(500, {"error": "MIC_ROLE_ARN not configured"})
    creds = boto3.client("sts").assume_role(
        RoleArn=role_arn, RoleSessionName="beacon-mic", DurationSeconds=900
    )["Credentials"]
    return _json(
        200,
        {
            "credentials": {
                "accessKeyId": creds["AccessKeyId"],
                "secretAccessKey": creds["SecretAccessKey"],
                "sessionToken": creds["SessionToken"],
                "expiration": creds["Expiration"],
            },
            "region": _env("AWS_REGION", _env("AWS_DEFAULT_REGION", "us-east-1")),
            "sttLanguage": _env("STT_LANGUAGE", "en-IN"),
        },
    )


@app.post("/turn")
def turn() -> Response[str]:
    headers = dict(app.current_event.headers)
    if not _passcode_ok(headers):
        return _json(401, {"error": "passcode required"})
    body = app.current_event.json_body or {}
    incident_id = str(body.get("incident_id", ""))
    session_id = str(body.get("session_id", "default"))
    channel = str(body.get("channel", "typed"))
    text = _turn_prompt(body)
    if not incident_id or not text:
        return _json(400, {"error": "incident_id and text (or mode) are required"})

    incident = store.get_incident(incident_id, table_name=_incidents_table())
    if not incident:
        return _json(404, {"error": f"incident {incident_id} not found"})

    history = [m for m in (incident.get("conversation") or []) if isinstance(m, dict)][
        -_MAX_HISTORY:
    ]
    turns_so_far = int(incident.get("turn_count") or 0)
    cap = int(_env("SESSION_CAP_TURNS", "30"))
    if turns_so_far >= cap:
        return _json(
            429, {"error": f"session cap of {cap} turns reached for this incident"}
        )

    ctx = TurnContext(
        incident_id=incident_id,
        session_id=session_id,
        transcript=str(body.get("text", "")).strip(),
        channel=channel,
        passcode_ok=True,
    )
    with turn_context(ctx):
        agent = _build_agent(history=history)
        history_len = len(agent.messages)
        try:
            result = agent(text)
        except Exception as exc:
            logger.exception("agent turn failed")
            return _json(
                502,
                {"error": f"the agent failed: {exc}", "tool_events": ctx.tool_events},
            )
        reply_text = _extract_reply(agent, result, history_len)

    spoken, cited = strip_citations(reply_text)
    tts: dict[str, Any] = {
        "audio_b64": None,
        "speech_marks": [],
        "voice": None,
        "tts_error": None,
    }
    try:
        tts.update(_synthesize(spoken))
    except Exception as exc:
        tts["tts_error"] = str(exc)

    new_messages = [m for m in agent.messages[history_len:] if isinstance(m, dict)]
    conversation = (history + new_messages)[-_MAX_HISTORY:]
    # Tools may have moved the status during this turn (approve -> remediating,
    # or resolved when the loop runs inline), so re-read before persisting.
    fresh = store.get_incident(incident_id, table_name=_incidents_table())
    store.update_status(
        incident_id,
        str(fresh.get("status") or incident.get("status") or "awaiting_engineer"),
        table_name=_incidents_table(),
        extra={
            "conversation": conversation,
            "turn_count": turns_so_far + 1,
            "last_channel": channel,
        },
    )
    fresh = store.get_incident(incident_id, table_name=_incidents_table())
    fresh.pop("conversation", None)
    fresh.pop("rca", None)
    return _json(
        200,
        {
            "reply_text": reply_text,
            "spoken_text": spoken,
            "cited": cited,
            "audio_b64": tts["audio_b64"],
            "speech_marks": tts["speech_marks"],
            "voice": tts["voice"],
            "tts_error": tts["tts_error"],
            "tool_events": ctx.tool_events,
            "evidence": ctx.evidence,
            "incident": fresh,
            "turn": turns_so_far + 1,
        },
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _tool_only(event: dict[str, Any]) -> dict[str, Any]:
    expected = _env("PASSCODE")
    if expected and event.get("passcode") != expected:
        return {"ok": False, "error": "passcode required"}
    ctx = TurnContext(
        incident_id=str(event.get("incident_id", "")),
        session_id=str(event.get("session_id", "cli")),
        transcript=str(event.get("transcript", "")),
        channel=str(event.get("channel", "cli")),
        passcode_ok=True,
    )
    with turn_context(ctx):
        result = voice_tools.dispatch(
            str(event.get("tool", "")), dict(event.get("args") or {})
        )
    return {
        "ok": "error" not in result,
        "result": result,
        "tool_events": ctx.tool_events,
        "evidence": ctx.evidence,
    }


def handler(event: dict[str, Any], context: Any) -> Any:
    if isinstance(event, dict) and event.get("mode") == "warm":
        return {"ok": True, "warm": True}
    if isinstance(event, dict) and event.get("mode") == "tool_only":
        return _tool_only(event)
    return app.resolve(event, context)
