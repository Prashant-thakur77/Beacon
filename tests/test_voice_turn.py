from __future__ import annotations

import base64
import json
from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon import store, voice_turn
from beacon.events import TriggerInfo, TriggerType
from tests.test_function_urls import url_event

INCIDENTS = "beacon-incidents-test"
APPROVALS = "beacon-approvals-test"
CONTRACTS = "beacon-contracts-test"
RCA = {
    "status": "High",
    "summary": "The demo web service lost its database.",
    "spoken_summary": "Your demo service lost its database.",
    "evidence": ["ERROR db unreachable"],
    "next_steps": [],
    "affected_components": [],
    "change_correlation": None,
    "beacon_json": {},
}


class FakeAgent:
    """Stands in for a Strands Agent: runs a scripted tool call, then replies."""

    def __init__(self, script: list[tuple[str, dict[str, Any]]], reply: str) -> None:
        self.script = script
        self.reply = reply
        self.messages: list[dict[str, Any]] = []
        self.calls: list[str] = []

    def __call__(self, text: str) -> Any:
        from beacon import voice_tools

        self.messages.append({"role": "user", "content": [{"text": text}]})
        self.calls.append(text)
        for name, args in self.script:
            result = voice_tools.dispatch(name, args)
            self.messages.append(
                {
                    "role": "assistant",
                    "content": [
                        {"toolUse": {"toolUseId": "t1", "name": name, "input": args}}
                    ],
                }
            )
            self.messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "toolResult": {
                                "toolUseId": "t1",
                                "content": [{"json": result}],
                                "status": "success",
                            }
                        }
                    ],
                }
            )
        self.messages.append({"role": "assistant", "content": [{"text": self.reply}]})
        return self.reply


@pytest.fixture()
def env(monkeypatch: Any, mocker: Any) -> Any:
    with mock_aws():
        for var, val in {
            "AWS_DEFAULT_REGION": "us-east-1",
            "INCIDENTS_TABLE_NAME": INCIDENTS,
            "APPROVALS_TABLE_NAME": APPROVALS,
            "CONTRACTS_TABLE_NAME": CONTRACTS,
            "PASSCODE": "nightshift",
            "APPLY_ENABLED": "true",
            "MIC_ROLE_ARN": "arn:aws:iam::123456789012:role/beacon-mic-test",
            "POLLY_VOICE_ID": "Kajal",
            "STT_LANGUAGE": "en-IN",
            "SESSION_CAP_TURNS": "3",
        }.items():
            monkeypatch.setenv(var, val)
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        for name, key in (
            (INCIDENTS, "incident_id"),
            (APPROVALS, "approval_id"),
            (CONTRACTS, "contract_id"),
        ):
            ddb.create_table(
                TableName=name,
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
        incident_id = store.put_incident(
            "STATUS: High\nSUMMARY: x",
            TriggerInfo(trigger_type=TriggerType.ALARM, alarm_name="a"),
            table_name=INCIDENTS,
            rca_json=RCA,
        )
        polly = mocker.patch(
            "beacon.voice_turn._synthesize",
            return_value={
                "audio_b64": base64.b64encode(b"mp3").decode(),
                "speech_marks": [{"time": 0, "value": "Hello."}],
                "voice": "Kajal",
            },
        )
        yield {"incident_id": incident_id, "polly": polly, "ddb": ddb}


def _post(
    path: str, body: dict[str, Any], passcode: str | None = "nightshift"
) -> dict[str, Any]:
    headers = {"x-beacon-passcode": passcode} if passcode else {}
    return voice_turn.handler(url_event("POST", path, body, headers), None)


def test_session_vends_scoped_sts_credentials_with_passcode(env: Any) -> None:
    resp = _post("/session", {})
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert set(body["credentials"]) == {
        "accessKeyId",
        "secretAccessKey",
        "sessionToken",
        "expiration",
    }
    assert body["region"] == "us-east-1" and body["sttLanguage"] == "en-IN"


def test_session_and_turn_refuse_without_passcode(env: Any) -> None:
    assert _post("/session", {}, passcode=None)["statusCode"] == 401
    assert _post("/session", {}, passcode="wrong")["statusCode"] == 401
    assert (
        _post(
            "/turn", {"incident_id": env["incident_id"], "text": "hi"}, passcode=None
        )["statusCode"]
        == 401
    )


def test_turn_runs_agent_returns_reply_audio_tools_and_evidence(
    env: Any, mocker: Any
) -> None:
    fake = FakeAgent(
        [("get_incident_brief", {})],
        "Your database is down [E1]. Say 'approve fix 1' when ready.",
    )
    mocker.patch("beacon.voice_turn._build_agent", return_value=fake)
    resp = _post(
        "/turn",
        {
            "incident_id": env["incident_id"],
            "session_id": "s1",
            "text": "what happened",
            "channel": "transcribe",
        },
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["reply_text"].startswith("Your database is down")
    assert body["audio_b64"] and body["speech_marks"][0]["value"] == "Hello."
    assert body["tool_events"][0]["name"] == "get_incident_brief"
    assert body["evidence"][0]["id"] == "E1" and body["evidence"][0]["kind"] == "rca"
    assert body["cited"] == ["E1"]
    assert body["incident"]["incident_id"] == env["incident_id"]
    assert fake.calls == ["what happened"]
    # TTS gets the reply with citation tags stripped
    spoken = env["polly"].call_args.args[0]
    assert "[E1]" not in spoken and "Your database is down" in spoken


def test_turn_persists_conversation_and_replays_history(env: Any, mocker: Any) -> None:
    fake = FakeAgent([], "First reply.")
    built = mocker.patch("beacon.voice_turn._build_agent", return_value=fake)
    _post(
        "/turn", {"incident_id": env["incident_id"], "session_id": "s1", "text": "one"}
    )
    incident = store.get_incident(env["incident_id"], table_name=INCIDENTS)
    conv = incident["conversation"]
    assert [m["role"] for m in conv] == ["user", "assistant"]
    assert conv[0]["content"][0]["text"] == "one"

    _post(
        "/turn", {"incident_id": env["incident_id"], "session_id": "s1", "text": "two"}
    )
    history = built.call_args.kwargs["history"]
    assert len(history) == 2 and history[0]["content"][0]["text"] == "one"


def test_turn_brief_mode_uses_the_briefing_prompt(env: Any, mocker: Any) -> None:
    fake = FakeAgent([("get_incident_brief", {})], "Briefing.")
    mocker.patch("beacon.voice_turn._build_agent", return_value=fake)
    resp = _post(
        "/turn",
        {"incident_id": env["incident_id"], "session_id": "s1", "mode": "brief"},
    )
    assert resp["statusCode"] == 200
    assert "brief" in fake.calls[0].lower()


def test_turn_event_mode_injects_resolved_event(env: Any, mocker: Any) -> None:
    fake = FakeAgent(
        [("check_recovery", {})], "Recovered. Should I handle this next time?"
    )
    mocker.patch("beacon.voice_turn._build_agent", return_value=fake)
    resp = _post(
        "/turn",
        {
            "incident_id": env["incident_id"],
            "session_id": "s1",
            "mode": "event",
            "event": "resolved",
        },
    )
    assert resp["statusCode"] == 200
    assert "resolved" in fake.calls[0].lower()


def test_turn_session_cap_is_enforced(env: Any, mocker: Any) -> None:
    mocker.patch("beacon.voice_turn._build_agent", return_value=FakeAgent([], "ok"))
    for _ in range(3):
        assert (
            _post(
                "/turn",
                {"incident_id": env["incident_id"], "session_id": "s1", "text": "x"},
            )["statusCode"]
            == 200
        )
    capped = _post(
        "/turn", {"incident_id": env["incident_id"], "session_id": "s1", "text": "x"}
    )
    assert capped["statusCode"] == 429


def test_turn_survives_polly_failure_in_text_only_mode(env: Any, mocker: Any) -> None:
    mocker.patch(
        "beacon.voice_turn._build_agent", return_value=FakeAgent([], "Text only.")
    )
    env["polly"].side_effect = RuntimeError("polly down")
    resp = _post(
        "/turn", {"incident_id": env["incident_id"], "session_id": "s1", "text": "hi"}
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert (
        body["reply_text"] == "Text only."
        and body["audio_b64"] is None
        and body["tts_error"]
    )


def test_turn_unknown_incident_is_404(env: Any, mocker: Any) -> None:
    mocker.patch("beacon.voice_turn._build_agent", return_value=FakeAgent([], "x"))
    assert (
        _post("/turn", {"incident_id": "nope", "session_id": "s1", "text": "hi"})[
            "statusCode"
        ]
        == 404
    )


def test_tool_only_invoke_runs_one_tool_with_transcript_context(env: Any) -> None:
    out = voice_turn.handler(
        {
            "mode": "tool_only",
            "tool": "get_incident_brief",
            "args": {},
            "incident_id": env["incident_id"],
            "transcript": "brief",
            "channel": "cli",
            "passcode": "nightshift",
        },
        None,
    )
    assert out["ok"] is True and out["result"]["status"] == "High"
    denied = voice_turn.handler(
        {
            "mode": "tool_only",
            "tool": "get_incident_brief",
            "args": {},
            "incident_id": env["incident_id"],
            "passcode": "bad",
        },
        None,
    )
    assert denied["ok"] is False


def test_strip_citations_and_collect_ids() -> None:
    text, ids = voice_turn.strip_citations("The rule is gone [E1][E3]. Fix ready [E2].")
    assert text == "The rule is gone. Fix ready."
    assert ids == ["E1", "E3", "E2"]


# ------------------------------------------------ AssemblyAI phase routes


def test_tools_route_runs_one_tool_with_the_browser_transcript(env: Any) -> None:
    resp = _post(
        "/tools/get_incident_brief",
        {
            "incident_id": env["incident_id"],
            "args": {},
            "transcript": "brief me",
            "channel": "assemblyai",
            "confidence": 0.97,
        },
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["ok"] is True and body["result"]["status"] == "High"
    assert body["evidence"][0]["id"] == "E1"


def test_tools_route_approve_uses_the_transcript_not_the_args(
    env: Any, mocker: Any
) -> None:
    mocker.patch(
        "beacon.voice_tools._invoke_remediate",
        return_value={
            "ok": True,
            "code": "DryRunOperation",
            "blast_radius": "1 rule",
            "role": "r",
        },
    )
    mocker.patch("beacon.voice_tools._start_execution", return_value="arn:aws:states:x")
    store.update_status(
        env["incident_id"],
        "awaiting_engineer",
        table_name=INCIDENTS,
        extra={
            "rca_json": {
                **RCA,
                "beacon_json": {
                    "suggested_action": "sg.restore_ingress",
                    "action_params": {
                        "group_id": "sg-1",
                        "ip_protocol": "tcp",
                        "from_port": 5432,
                        "to_port": 5432,
                        "source_group_id": "sg-2",
                    },
                },
            }
        },
    )
    _post(
        "/tools/propose_fix",
        {
            "incident_id": env["incident_id"],
            "args": {},
            "transcript": "fix it",
            "channel": "assemblyai",
        },
    )
    refused = json.loads(
        _post(
            "/tools/approve_fix",
            {
                "incident_id": env["incident_id"],
                "args": {"fix_id": 1, "confirmation_phrase": "approve fix 1"},
                "transcript": "yeah go ahead",
                "channel": "assemblyai",
            },
        )["body"]
    )
    assert refused["result"]["approved"] is False
    ok = json.loads(
        _post(
            "/tools/approve_fix",
            {
                "incident_id": env["incident_id"],
                "args": {"fix_id": 1, "confirmation_phrase": "approve fix 1"},
                "transcript": "okay approve fix one",
                "channel": "assemblyai",
            },
        )["body"]
    )
    assert ok["result"]["approved"] is True


def test_tools_route_rejects_low_confidence_approvals(env: Any) -> None:
    resp = _post(
        "/tools/approve_fix",
        {
            "incident_id": env["incident_id"],
            "args": {"fix_id": 1, "confirmation_phrase": "approve fix 1"},
            "transcript": "approve fix one",
            "channel": "assemblyai",
            "confidence": 0.6,
        },
    )
    body = json.loads(resp["body"])
    assert body["ok"] is False and "repeat" in body["result"]["error"]


def test_tools_route_requires_passcode_and_known_tool(env: Any) -> None:
    assert (
        _post(
            "/tools/get_incident_brief",
            {"incident_id": env["incident_id"]},
            passcode=None,
        )["statusCode"]
        == 401
    )
    assert (
        _post("/tools/launch_nukes", {"incident_id": env["incident_id"]})["statusCode"]
        == 404
    )


def test_assemblyai_token_route_mints_a_temporary_token(
    env: Any, mocker: Any, monkeypatch: Any
) -> None:
    monkeypatch.setenv("ASSEMBLYAI_KEY_PARAM", "/beacon/test/assemblyai-key")
    boto3.client("ssm", region_name="us-east-1").put_parameter(
        Name="/beacon/test/assemblyai-key", Type="SecureString", Value="aai-secret"
    )
    minted = mocker.patch(
        "beacon.voice_turn._mint_assemblyai_token",
        return_value={"token": "tmp-123", "expires_in_seconds": 600},
    )
    resp = _post("/assemblyai/token", {})
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["token"] == "tmp-123"
    assert minted.call_args.args[0] == "aai-secret"
    assert _post("/assemblyai/token", {}, passcode=None)["statusCode"] == 401


def test_assemblyai_token_route_is_503_when_not_configured(
    env: Any, monkeypatch: Any
) -> None:
    monkeypatch.delenv("ASSEMBLYAI_KEY_PARAM", raising=False)
    assert _post("/assemblyai/token", {})["statusCode"] == 503
