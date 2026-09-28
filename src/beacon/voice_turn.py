"""Voice-turn Lambda behind a Function URL (``beacon-voice-turn-<stack>``).

Routes (JSON; CORS is configured on the Function URL):

* ``GET /health``, plus the EventBridge keep-warm ping ``{"mode": "warm"}``.
* ``POST /session`` (passcode) -> 15-minute STS credentials for the browser
  mic, scoped to Transcribe streaming only (``BeaconMicRole``).
* ``POST /turn`` (passcode) -> one agent turn: the Strands agent on Nova 2
  Lite calls the seven tools, the reply is spoken by Polly, and every tool
  event / evidence card is returned for the UI.
* direct invoke ``{"mode": "tool_only", ...}`` -> run one tool (CLI targets,
  and the AssemblyAI phase's tool route).
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from functools import cache
from importlib.resources import files
from typing import Any, cast

from aws_lambda_powertools.event_handler import (
    LambdaFunctionUrlResolver,
    Response,
)

from beacon import aai, aws, observability, store, voice_brief, voice_tools
from beacon.phone import attest
from beacon.turn_context import TurnContext, turn_context

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

SERVICE = "beacon-voice-turn"
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MAX_TEXT = 2000
_CITATION_RE = re.compile(r"\s*\[(E\d+)\]")
_MAX_HISTORY = 20
_CONSENT_TOOLS = ("approve_fix", "grant_sleep_contract", "undo_fix", "open_fix_pr")
# The same four, as the timeline names them once they have run.
_CONSENT_EVENTS = ("approved", "contract_granted", "undone", "pr_opened")
_MIN_CONSENT_CONFIDENCE = 0.85
# Phrases the re-transcription must not mishear; they are what unlocks a change.
_CONSENT_KEYTERMS = (
    "approve fix one",
    "approve fix two",
    "grant contract for seven days",
    "open the pull request",
    "undo fix one",
)

# CORS lives on the Function URL (console-template.yaml), scoped to the console
# origin; setting it here too would duplicate the headers in every response.
app = LambdaFunctionUrlResolver()


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
    """Constant-time compare; an unset passcode fails closed (the URL is public)."""
    expected = _env("PASSCODE")
    given = headers.get("x-beacon-passcode") or headers.get("X-Beacon-Passcode") or ""
    if not expected or not given:
        return False
    return hmac.compare_digest(given.encode(), expected.encode())


def strip_citations(text: str) -> tuple[str, list[str]]:
    """Remove ``[E#]`` tags for speech; return the ids in order of appearance."""
    ids = _CITATION_RE.findall(text)
    clean = _CITATION_RE.sub("", text)
    clean = re.sub(r"\s+([.,;!?])", r"\1", clean)
    return re.sub(r"[ \t]{2,}", " ", clean).strip(), ids


# ---------------------------------------------------------------------------
# Speech
# ---------------------------------------------------------------------------


@observability.span("polly.synthesize")
def _synthesize(text: str) -> dict[str, Any]:
    """Polly mp3 + sentence speech marks; falls back through voices/engines."""
    polly: Any = aws.client("polly", read_timeout=10)
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
    """A Strands Agent on Nova 2 Lite with the seven tools (or the litellm loop)."""
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


@observability.span("bedrock.agent_turn")
def _run_agent(agent: Any, text: str) -> Any:
    return agent(text)


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


def _record_turn_usage(incident_id: str, result: Any) -> None:
    """Strands AgentResult.metrics.accumulated_usage -> incident usage counters."""
    metrics = getattr(result, "metrics", None)
    usage = getattr(metrics, "accumulated_usage", None) or {}
    try:
        counts = {
            "input_tokens": int(usage.get("inputTokens", 0) or 0),
            "output_tokens": int(usage.get("outputTokens", 0) or 0),
        }
    except (AttributeError, TypeError, ValueError):
        return
    if not any(counts.values()):
        return
    try:
        store.add_usage(incident_id, counts, table_name=_incidents_table())
    except Exception:
        logger.exception("usage write failed")


def _turn_prompt(body: dict[str, Any]) -> str:
    mode = body.get("mode", "chat")
    hint = ""
    if str(body.get("lang", "")).lower().startswith("hi"):
        hint = (
            " Answer in Hinglish (Hindi in Latin script with English technical terms)."
        )
    if mode == "brief":
        return (
            "The engineer just opened the incident. Brief them: call "
            "get_incident_brief, then in two or three sentences say what is wrong, "
            "the likely cause, and that you can propose a fix if they ask." + hint
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
        return f"System event: {event}. Tell the engineer briefly." + hint
    return str(body.get("text", "")).strip() + hint


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    from beacon import __version__

    return {"ok": True, "service": SERVICE, "version": __version__}


@app.post("/session")
def session() -> Response[str]:
    if not _passcode_ok(dict(app.current_event.headers)):
        return _json(401, {"error": "passcode required"})
    role_arn = _env("MIC_ROLE_ARN")
    if not role_arn:
        return _json(500, {"error": "MIC_ROLE_ARN not configured"})
    creds = aws.client("sts").assume_role(
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


def _emit_turn_metrics(
    *,
    started: float,
    agent_ms: float,
    tts_ms: float,
    tool_events: list[dict[str, Any]],
    channel: str,
) -> None:
    approvals = sum(
        1
        for t in tool_events
        if t.get("name") == "approve_fix" and "approved via" in str(t.get("summary"))
    )
    grants = sum(
        1
        for t in tool_events
        if t.get("name") == "grant_sleep_contract"
        and "granted:" in str(t.get("summary"))
    )
    observability.metric(
        "TurnLatencyMs", (time.perf_counter() - started) * 1000, unit="Milliseconds"
    )
    observability.metric("AgentLatencyMs", agent_ms, unit="Milliseconds")
    observability.metric("TtsLatencyMs", tts_ms, unit="Milliseconds")
    observability.metric("ToolCalls", len(tool_events), unit="Count")
    observability.metric("Approvals", approvals, unit="Count")
    observability.metric("ContractsGranted", grants, unit="Count")
    observability.metric(f"Turns{channel.capitalize()}", 1, unit="Count")


@app.post("/turn")
def turn() -> Response[str]:
    with observability.metrics_scope(service=SERVICE):
        return _turn()


def _turn() -> Response[str]:
    started = time.perf_counter()
    headers = dict(app.current_event.headers)
    if not _passcode_ok(headers):
        return _json(401, {"error": "passcode required"})
    body = app.current_event.json_body or {}
    incident_id = str(body.get("incident_id", ""))
    session_id = str(body.get("session_id", "default"))[:64]
    channel = str(body.get("channel", "typed"))[:32]
    raw_text = str(body.get("text", ""))
    if len(raw_text) > _MAX_TEXT:
        return _json(413, {"error": f"text is limited to {_MAX_TEXT} characters"})
    if not _ID_RE.match(incident_id):
        return _json(400, {"error": "incident_id is required (letters, digits, . _ -)"})
    text = _turn_prompt(body)
    if not text:
        return _json(400, {"error": "text (or mode) is required"})

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
        agent_started = time.perf_counter()
        try:
            result = _run_agent(agent, text)
        except Exception as exc:
            logger.exception("agent turn failed")
            return _json(
                502,
                {"error": f"the agent failed: {exc}", "tool_events": ctx.tool_events},
            )
        agent_ms = (time.perf_counter() - agent_started) * 1000
        reply_text = _extract_reply(agent, result, history_len)
    _record_turn_usage(incident_id, result)

    spoken, cited = strip_citations(reply_text)
    tts_started = time.perf_counter()
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

    _emit_turn_metrics(
        started=started,
        agent_ms=agent_ms,
        tts_ms=(time.perf_counter() - tts_started) * 1000,
        tool_events=ctx.tool_events,
        channel=channel,
    )

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
# AssemblyAI phase: the agent lives in the browser session; tools run here
# ---------------------------------------------------------------------------


def _run_tool(
    name: str,
    args: dict[str, Any],
    *,
    incident_id: str,
    session_id: str,
    transcript: str,
    channel: str,
    confidence: float | None,
) -> dict[str, Any]:
    """Execute one tool with a TurnContext built from the browser's transcript.

    For consent tools the STT confidence must clear a bar: a mumbled
    "approve fix one" is refused with a request to repeat the phrase.
    """
    if (
        name in _CONSENT_TOOLS
        and confidence is not None
        and confidence < _MIN_CONSENT_CONFIDENCE
    ):
        return {
            "ok": False,
            "result": {
                "approved": False,
                "granted": False,
                "error": (
                    f"I heard that at {int(confidence * 100)}% confidence; "
                    "please repeat the exact phrase clearly"
                ),
            },
            "tool_events": [],
            "evidence": [],
        }
    ctx = TurnContext(
        incident_id=incident_id,
        session_id=session_id,
        transcript=transcript,
        channel=channel,
        passcode_ok=True,
        attestation={
            "stt": _transcriber(channel, session_id),
            "session_id": session_id,
            **({"confidence": confidence} if confidence is not None else {}),
        },
    )
    with turn_context(ctx):
        result = voice_tools.dispatch(name, args)
    return {
        "ok": "error" not in result,
        "result": result,
        "tool_events": ctx.tool_events,
        "evidence": ctx.evidence,
    }


def _transcriber(channel: str, session_id: str) -> str:
    """What to name as having produced these words -- claiming no more than it can show.

    "assemblyai-voice-agent" is a claim that the Voice Agent heard the phrase, and
    the consent certificate prints it as such. It only holds when the turn came in
    as audio on a live session, because that is the only case where a recording
    exists to re-check. A typed line in the same session arrives on the "typed"
    channel, and a turn whose session id is the console's local fallback rather
    than an AssemblyAI ``sess_`` id has no recording to appeal to either.
    """
    if channel != "assemblyai":
        return channel
    return "assemblyai-voice-agent" if session_id.startswith("sess_") else "assemblyai"


@app.post("/tools/<name>")
def tools_route(name: str) -> Response[str]:
    if not _passcode_ok(dict(app.current_event.headers)):
        return _json(401, {"error": "passcode required"})
    if name not in voice_tools.TOOL_FUNCTIONS:
        return _json(404, {"error": f"unknown tool {name}"})
    body = app.current_event.json_body or {}
    incident_id = str(body.get("incident_id", ""))
    if not _ID_RE.match(incident_id):
        return _json(400, {"error": "incident_id is required"})
    transcript = str(body.get("transcript", "")).strip()
    if len(transcript) > _MAX_TEXT:
        return _json(413, {"error": f"transcript is limited to {_MAX_TEXT} characters"})
    if not store.get_incident(incident_id, table_name=_incidents_table()):
        return _json(404, {"error": f"incident {incident_id} not found"})
    raw_conf = body.get("confidence")
    out = _run_tool(
        name,
        dict(body.get("args") or {}),
        incident_id=incident_id,
        session_id=str(body.get("session_id", "assemblyai"))[:64],
        transcript=transcript,
        channel=str(body.get("channel", "assemblyai"))[:32],
        confidence=float(raw_conf) if isinstance(raw_conf, int | float) else None,
    )
    fresh = store.get_incident(incident_id, table_name=_incidents_table())
    fresh.pop("conversation", None)
    fresh.pop("rca", None)
    out["incident"] = fresh
    return _json(200, out)


@app.get("/brief/<incident_id>")
def voice_brief_route(incident_id: str) -> Response[str]:
    """The session brief for one incident, for whichever channel is asking.

    The browser, the phone bridge and the spoken regression suite all open an
    AssemblyAI session against the same incident, and they have to open it with the
    same rules. Building the brief here is what stops a channel added later from
    quietly getting a different agent -- and the phone gets an extra paragraph,
    because there is no screen on a telephone.
    """
    if not _passcode_ok(dict(app.current_event.headers)):
        return _json(401, {"error": "passcode required"})
    if not _ID_RE.match(incident_id):
        return _json(400, {"error": "incident_id is required"})
    incident = store.get_incident(incident_id, table_name=_incidents_table())
    if not incident:
        return _json(404, {"error": f"incident {incident_id} not found"})
    params = app.current_event.query_string_parameters or {}
    channel = str(params.get("channel") or "browser")[:16]
    language = str(params.get("language") or "en")[:8]
    return _json(
        200, voice_brief.session_brief(incident, channel=channel, language=language)
    )


def _mint_assemblyai_token(api_key: str) -> dict[str, Any]:
    """Exchange the long-lived key for a short-lived browser token."""

    # Voice Agent API tokens are single-use and ride the socket URL (?token=).
    req = urllib.request.Request(
        "https://agents.assemblyai.com/v1/token?expires_in_seconds=600",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        data = json.loads(resp.read().decode())
    return {
        "token": data.get("token"),
        "expires_in_seconds": int(data.get("expires_in_seconds", 600)),
    }


@app.post("/assemblyai/token")
def assemblyai_token() -> Response[str]:
    if not _passcode_ok(dict(app.current_event.headers)):
        return _json(401, {"error": "passcode required"})
    param = _env("ASSEMBLYAI_KEY_PARAM")
    if not param:
        return _json(503, {"error": "AssemblyAI is not configured on this deployment"})
    try:
        api_key = aws.client("ssm").get_parameter(Name=param, WithDecryption=True)[
            "Parameter"
        ]["Value"]
        minted = _mint_assemblyai_token(api_key)
    except Exception as exc:
        logger.exception("assemblyai token mint failed")
        return _json(502, {"error": f"could not mint an AssemblyAI token: {exc}"})
    return _json(200, minted)


_SESSION_RE = re.compile(r"^sess_[0-9a-f]{32}$")


@app.get("/recordings/<session_id>")
def recording(session_id: str) -> Response[str]:
    """A short-lived link to the AssemblyAI session recording behind an approval.

    The API key never leaves the Lambda: the browser gets the presigned audio URL
    the sessions API returns, valid for minutes, and plays it inline.
    """
    if not _passcode_ok(dict(app.current_event.headers)):
        return _json(401, {"error": "passcode required"})
    if not _SESSION_RE.match(session_id):
        return _json(400, {"error": "not an AssemblyAI session id"})
    if not aai.key():
        return _json(503, {"error": "AssemblyAI is not configured on this deployment"})
    data = aai.session(session_id)
    if data is None:
        return _json(404, {"error": "session not found in any region"})
    audio = next(
        (a for a in data.get("artifacts") or [] if a.get("type") == "audio"), None
    )
    return _json(
        200,
        {
            "session_id": session_id,
            "status": data.get("status"),
            "duration_seconds": data.get("duration_seconds"),
            "ended_at": data.get("ended_at"),
            "audio_url": audio.get("url") if audio else None,
        },
    )


@app.post("/sessions/<session_id>/summary")
def session_summary(session_id: str) -> Response[str]:
    """Summarise what was actually said in a voice session, and keep it.

    AssemblyAI transcribes its own recording and summarises it (redacted). The
    result is cached on the incident, so the postmortem and the morning report can
    quote the night in the engineer's words rather than the model's.
    """
    if not _passcode_ok(dict(app.current_event.headers)):
        return _json(401, {"error": "passcode required"})
    if not _SESSION_RE.match(session_id):
        return _json(400, {"error": "not an AssemblyAI session id"})
    body = app.current_event.json_body or {}
    incident_id = str(body.get("incident_id", ""))
    if not _ID_RE.match(incident_id):
        return _json(400, {"error": "incident_id is required"})
    incident = store.get_incident(incident_id, table_name=_incidents_table())
    if not incident:
        return _json(404, {"error": f"incident {incident_id} not found"})
    cached = (incident.get("voice_summary") or {}).get(session_id)
    if cached and not body.get("refresh"):
        return _json(200, {"cached": True, **cached})
    try:
        out = aai.summarise_session(
            session_id,
            keyterms=[
                str(incident.get("alarm_name") or ""),
                "approve fix one",
                "grant contract for seven days",
                "Beacon",
            ],
        )
    except Exception as exc:
        logger.warning("session summary failed: %s", exc)
        return _json(502, {"error": f"could not summarise the session: {exc}"})
    summaries = dict(incident.get("voice_summary") or {})
    summaries[session_id] = {
        "summary": out["summary"],
        "seconds": out.get("seconds"),
        "transcript_id": out.get("transcript_id"),
    }
    store.update_status(
        incident_id,
        str(incident.get("status") or "awaiting_engineer"),
        table_name=_incidents_table(),
        extra={"voice_summary": summaries},
    )
    return _json(200, {"cached": False, **summaries[session_id]})


def consent_phrases(incident: dict[str, Any]) -> list[dict[str, Any]]:
    """The spoken phrases that authorised a change on this incident, in order.

    The four tools that change something each leave a timeline entry, but they do
    not all name the phrase the same way: three write ``transcript_quote`` and
    open_fix_pr writes ``quote``. Reading one key and listing three events is how
    this route came to answer "no consent phrase was recorded" for every live
    approval it exists to re-check -- on an incident whose audit row showed the
    phrase plainly.
    """
    phrases: list[dict[str, Any]] = []
    for entry in incident.get("timeline") or []:
        if entry.get("event") not in _CONSENT_EVENTS:
            continue
        detail = entry.get("detail") or {}
        quote = str(detail.get("transcript_quote") or detail.get("quote") or "").strip()
        if quote:
            phrases.append(
                {
                    "quote": quote,
                    "event": str(entry.get("event")),
                    "channel": str(detail.get("channel") or ""),
                }
            )
    return phrases


@app.post("/sessions/<session_id>/attest")
def session_attest(session_id: str) -> Response[str]:
    """How clearly the words that authorised this session's changes were heard.

    The Voice Agent API reports no confidence on a live turn, so an approval spoken
    in the browser or down a phone is acted on without one -- while the same words
    sent as a Telegram voice note must clear 85%. That gap is real, and this closes
    it after the fact: AssemblyAI re-transcribes its own recording of the session
    with the pre-recorded model, which does return per-word confidence, and each
    phrase that unlocked a change is scored by its **weakest** word.

    Nothing is undone by a poor score; by the time a transcript exists the fix has
    been applied and verified. It is recorded on the incident and flagged in the
    audit, which is what an audit is for. Waiting a minute for a transcript before
    touching production would be the wrong trade at 3 AM.
    """
    if not _passcode_ok(dict(app.current_event.headers)):
        return _json(401, {"error": "passcode required"})
    if not _SESSION_RE.match(session_id):
        return _json(400, {"error": "not an AssemblyAI session id"})
    body = app.current_event.json_body or {}
    incident_id = str(body.get("incident_id", ""))
    if not _ID_RE.match(incident_id):
        return _json(400, {"error": "incident_id is required"})
    incident = store.get_incident(incident_id, table_name=_incidents_table())
    if not incident:
        return _json(404, {"error": f"incident {incident_id} not found"})

    phrases = consent_phrases(incident)
    # Words typed into the console during a voice session are not in its recording,
    # and hunting for them there would report a missing phrase as a mishearing.
    typed = [p for p in phrases if p["channel"] == "typed"]
    spoken = [p for p in phrases if p["channel"] != "typed"]
    if not spoken:
        return _json(
            200,
            {
                "ok": True,
                "checks": [],
                "problems": [],
                "typed": typed,
                "note": (
                    "every phrase on this incident was typed, not spoken; "
                    "there is nothing in the recording to score"
                    if typed
                    else "no consent phrase was recorded for this incident"
                ),
            },
        )
    try:
        heard = aai.audit_session(
            session_id,
            keyterms=[str(incident.get("alarm_name") or ""), *_CONSENT_KEYTERMS],
        )
    except Exception as exc:
        logger.warning("session attestation failed: %s", exc)
        return _json(502, {"error": f"could not re-transcribe the session: {exc}"})

    verdict = attest.verify_confidence(heard["words"], spoken)
    verdict["typed"] = typed
    verdict["transcript_id"] = heard.get("transcript_id")
    verdict["session_id"] = session_id
    attestations = dict(incident.get("voice_attestation") or {})
    attestations[session_id] = verdict
    store.update_status(
        incident_id,
        str(incident.get("status") or "awaiting_engineer"),
        table_name=_incidents_table(),
        extra={"voice_attestation": attestations},
    )
    return _json(200, verdict)


@app.post("/telegram/webhook")
def telegram_webhook() -> Response[str]:
    """Telegram's webhook (bot messages, voice notes, button taps).

    Authenticated by the webhook secret, not the passcode; the allowlist of
    Telegram user ids is checked inside. Always answers 200 once the secret
    matches, because Telegram retries anything else and a retry would re-run
    a tool.
    """
    from beacon import telegram_bot

    event = app.current_event
    out = telegram_bot.handle_event(
        {"headers": dict(event.headers), "body": event.body or "{}"}
    )
    return Response(
        status_code=int(out["statusCode"]),
        content_type="application/json",
        body=str(out["body"]),
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
