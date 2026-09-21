"""Beacon without an AWS account (``make local``, the Build It track).

One FastAPI process hosts the two Lambda handlers (voice-turn, dashboard)
and the remediate Lambda, all against an in-process moto AWS: a VPC with
the demo security groups, the CloudWatch alarm, SNS, SSM golden snapshot,
and the four DynamoDB tables.  Bedrock is replaced by a scripted agent that
calls the real tools; every safety check (allowlist, golden snapshot,
transcript-verified approval, dry run, idempotency, three-part verify,
Sleep Contracts) is the production code.

    make local      ->  http://localhost:8000  (serves web/dist too)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from moto import mock_aws

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("beacon.local")

ROOT = Path(__file__).resolve().parent.parent
REGION = "us-east-1"
STACK = "local"
ALARM = "beacon-demo-infra-errors"
TABLES = {
    "INCIDENTS_TABLE_NAME": (f"beacon-incidents-{STACK}", "incident_id"),
    "APPROVALS_TABLE_NAME": (f"beacon-approvals-{STACK}", "approval_id"),
    "CONTRACTS_TABLE_NAME": (f"beacon-contracts-{STACK}", "contract_id"),
}

TRIAGE_TEXT = """STATUS: High
SUMMARY: The demo web service lost connectivity to its PostgreSQL database; every request that touches the database is failing with connection timeouts.
AFFECTED COMPONENTS: beacon-demo-webapp, beacon-demo-db, payments-service
EVIDENCE:
- 21:41:03 ERROR POST /api/v2/payments 503 5004ms - service=payments-service error='connection_timeout' db_pool=EXHAUSTED
- 21:41:11 ERROR CRITICAL: Database unreachable. Host=beacon-demo-db:5432 consecutive_failures=6
NEXT STEPS:
1. Restore the tcp/5432 ingress rule from the ECS task security group on the RDS security group
2. Confirm the error rate returns to zero
CHANGE CORRELATION: RevokeSecurityGroupIngress on the RDS security group by user/prashant, 25 seconds before the first error.
SPOKEN SUMMARY: Your demo web service in U S east 1 can no longer reach its database. The security group rule that lets the E C S tasks talk to R D S on port 5432 was removed just before the errors began. Restoring that rule should bring the service back.
BEACON_JSON:
```json
{"fingerprint": "rds-sg-ingress-revoked", "suggested_action": "sg.restore_ingress", "action_params": null, "confidence": 0.9, "correlated_change": "RevokeSecurityGroupIngress by user/prashant"}
```
"""


class LocalWorld:
    """The moto-backed AWS this process runs against."""

    def __init__(self) -> None:
        self.mock = mock_aws()
        self.mock.start()
        os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
        os.environ["AWS_DEFAULT_REGION"] = REGION
        os.environ["AWS_REGION"] = REGION

        ddb = boto3.client("dynamodb", region_name=REGION)
        for env_key, (name, key) in TABLES.items():
            ddb.create_table(
                TableName=name,
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
            os.environ[env_key] = name

        ec2 = boto3.client("ec2", region_name=REGION)
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
        self.ecs_sg = ec2.create_security_group(
            GroupName="beacon-demo-ecs", Description="ecs", VpcId=vpc
        )["GroupId"]
        self.rds_sg = ec2.create_security_group(
            GroupName="beacon-demo-rds", Description="rds", VpcId=vpc
        )["GroupId"]
        self.params = {
            "group_id": self.rds_sg,
            "ip_protocol": "tcp",
            "from_port": 5432,
            "to_port": 5432,
            "source_group_id": self.ecs_sg,
        }
        from beacon.remediation import actions_sg

        ec2.authorize_security_group_ingress(
            GroupId=self.rds_sg, IpPermissions=[actions_sg.ip_permission(self.params)]
        )
        golden = actions_sg.snapshot([self.rds_sg, self.ecs_sg], ec2_client=ec2)
        os.environ["GOLDEN_SG_PARAM"] = f"/beacon/{STACK}/golden-sg"
        ssm = boto3.client("ssm", region_name=REGION)
        ssm.put_parameter(
            Name=os.environ["GOLDEN_SG_PARAM"], Type="String", Value=json.dumps(golden)
        )
        # AssemblyAI phase: with a real key in the environment, local mode can run the
        # Voice Agent backend (?voice=assemblyai); the tools still run here against moto.
        if os.environ.get("ASSEMBLYAI_API_KEY"):
            os.environ["ASSEMBLYAI_KEY_PARAM"] = f"/beacon/{STACK}/assemblyai-key"
            ssm.put_parameter(
                Name=os.environ["ASSEMBLYAI_KEY_PARAM"],
                Type="SecureString",
                Value=os.environ["ASSEMBLYAI_API_KEY"],
            )

        cw = boto3.client("cloudwatch", region_name=REGION)
        cw.put_metric_alarm(
            AlarmName=ALARM,
            Namespace="BeaconDemoInfra",
            MetricName="ErrorCount",
            Statistic="Sum",
            Period=60,
            EvaluationPeriods=1,
            Threshold=3,
            ComparisonOperator="GreaterThanOrEqualToThreshold",
            TreatMissingData="notBreaching",
        )
        topic = boto3.client("sns", region_name=REGION).create_topic(
            Name=f"beacon-alerts-{STACK}"
        )["TopicArn"]
        os.environ["SNS_TOPIC_ARN"] = topic
        os.environ["LOG_GROUP_PATTERNS"] = "/ecs/beacon-demo"
        os.environ["INCIDENTS_ENABLED"] = "true"
        os.environ["APPLY_ENABLED"] = "true"
        os.environ["CHANGES_TABLE_NAME"] = ""
        os.environ["STATE_MACHINE_ARN"] = "local"
        os.environ["REMEDIATE_FUNCTION_ARN"] = "local"
        os.environ["VERIFY_WAIT_SECONDS"] = "0"
        os.environ["VERIFY_MAX_ATTEMPTS"] = "3"
        os.environ["PASSCODE"] = os.environ.get("BEACON_LOCAL_PASSCODE", "local")
        os.environ["POLLY_VOICE_ID"] = "Kajal"
        os.environ["STT_LANGUAGE"] = "en-IN"
        os.environ["MIC_ROLE_ARN"] = "arn:aws:iam::123456789012:role/beacon-mic-local"
        self.ec2 = ec2
        self.cw = cw

    def close(self) -> None:
        self.mock.stop()

    def break_db(self) -> None:
        from beacon.remediation import actions_sg

        self.ec2.revoke_security_group_ingress(
            GroupId=self.rds_sg, IpPermissions=[actions_sg.ip_permission(self.params)]
        )
        self.cw.set_alarm_state(
            AlarmName=ALARM, StateValue="ALARM", StateReason="local break"
        )
        # what the demo app's metric filter would have produced over the last minutes
        from datetime import timedelta

        now = datetime.now(tz=UTC)
        self.cw.put_metric_data(
            Namespace="BeaconDemoInfra",
            MetricData=[
                {
                    "MetricName": "ErrorCount",
                    "Value": float(v),
                    "Unit": "Count",
                    "Timestamp": now - timedelta(minutes=m),
                }
                for m, v in ((7, 3), (6, 5), (5, 7), (4, 6), (3, 6))
            ],
        )

    def alarm_ok(self) -> None:
        self.cw.set_alarm_state(
            AlarmName=ALARM, StateValue="OK", StateReason="local recovered"
        )

    def alarm_event(self) -> dict[str, Any]:
        return {
            "detail-type": "CloudWatch Alarm State Change",
            "source": "aws.cloudwatch",
            "time": datetime.now(tz=UTC).isoformat(),
            "detail": {
                "alarmName": ALARM,
                "state": {
                    "value": "ALARM",
                    "reason": "ErrorCount >= 3",
                    "timestamp": datetime.now(tz=UTC).isoformat(),
                },
            },
        }


# ---------------------------------------------------------------------------
# Local stand-ins for the pieces that need real AWS or Bedrock
# ---------------------------------------------------------------------------


def _patch_remote_calls(world: LocalWorld) -> None:
    """Route cross-Lambda calls in-process and replace Bedrock with a script."""
    from beacon import handler as triage_handler
    from beacon import remediate, voice_tools, voice_turn

    def invoke_remediate(payload: dict[str, Any]) -> dict[str, Any]:
        return remediate.handler(payload, None)

    def start_execution(payload: dict[str, Any]) -> str:
        # Step Functions stand-in: run the loop inline. CloudWatch would flip the
        # alarm to OK an evaluation period after the rule returns; here the
        # verify step's own alarm read does it once the post-condition holds.
        from beacon.remediation import actions_sg

        real_verify_alarm = remediate.verify_all

        def verify_all_local(alarm_name: str, **kwargs: Any) -> Any:
            if actions_sg.postcondition(world.params, ec2_client=world.ec2):
                # the app recovers: the metric filter emits zero errors, the alarm clears
                world.cw.put_metric_data(
                    Namespace="BeaconDemoInfra",
                    MetricData=[
                        {
                            "MetricName": "ErrorCount",
                            "Value": 0.0,
                            "Unit": "Count",
                            "Timestamp": datetime.now(tz=UTC),
                        }
                    ],
                )
                world.alarm_ok()
            return real_verify_alarm(alarm_name, **kwargs)

        remediate.verify_all = verify_all_local  # type: ignore[assignment]
        try:
            remediate.handler({**payload, "step": "all"}, None)
        finally:
            remediate.verify_all = real_verify_alarm  # type: ignore[assignment]
        return f"arn:aws:states:{REGION}:123456789012:execution:beacon-remediate-local:{int(time.time())}"

    voice_tools._invoke_remediate = invoke_remediate  # type: ignore[assignment]
    voice_tools._start_execution = start_execution  # type: ignore[assignment]
    triage_handler._start_execution = start_execution  # type: ignore[assignment]

    def synthesize(_text: str) -> dict[str, Any]:
        raise RuntimeError("local mode: browser voice")

    voice_turn._synthesize = synthesize  # type: ignore[assignment]

    def build_agent(*, history: list[dict[str, Any]]) -> Any:
        return ScriptedAgent(history)

    voice_turn._build_agent = build_agent  # type: ignore[assignment]

    # triage: no CloudWatch Logs, no Bedrock; feed the fixture RCA through the real pipeline
    triage_handler.resolve_log_groups = lambda patterns: ["/ecs/beacon-demo"]  # type: ignore[assignment]
    triage_handler.fetch_logs = lambda group, lookback: (
        "ERROR CRITICAL: Database unreachable. Host=beacon-demo-db:5432\n" * 8
    )  # type: ignore[assignment]
    from beacon import triage as triage_module

    def scripted_triage(combined: str, trigger: Any, config: Any) -> str:
        triage_module.last_usage.clear()
        triage_module.last_usage.update({"input_tokens": 14200, "output_tokens": 620})
        return TRIAGE_TEXT

    triage_handler.triage = scripted_triage  # type: ignore[assignment]
    triage_handler.compute_available_tokens = lambda config, sp, tc: 100_000  # type: ignore[assignment]
    import beacon.prefetch as prefetch

    prefetch.run = lambda *a, **k: None  # type: ignore[assignment]


class ScriptedAgent:
    """Deterministic stand-in for the Strands agent: intent by keyword, real tools."""

    def __init__(self, history: list[dict[str, Any]]) -> None:
        self.messages: list[dict[str, Any]] = list(history)

    def _call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        from beacon import voice_tools

        result = voice_tools.dispatch(name, args)
        self.messages.append(
            {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": name, "name": name, "input": args}}
                ],
            }
        )
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": name,
                            "content": [{"json": result}],
                            "status": "success",
                        }
                    }
                ],
            }
        )
        return result

    def __call__(self, text: str) -> str:
        self.messages.append({"role": "user", "content": [{"text": text}]})
        t = text.lower()
        if "brief" in t and "engineer just opened" in t:
            b = self._call("get_incident_brief", {})
            if b.get("handled_by") == "contract":
                reply = f"{b.get('spoken_summary', '')} [E1] The rule is back and the alarm is OK."
            else:
                reply = f"{b.get('spoken_summary', '')} [E1] I can propose a fix if you ask."
        elif "system event" in t and "resolved" in t:
            r = self._call("check_recovery", {})
            tag = f" [{r['evidence'][0]['id']}]" if r.get("evidence") else ""
            reply = f"Recovered. The alarm is back to OK after the fix, the error count is zero, and the rule is present{tag}. Should I handle this myself next time?"
        elif "system event" in t and "escalated" in t:
            r = self._call("check_recovery", {})
            reply = f"Verification did not pass; status is {r.get('status')}. A human is needed."
        elif re.search(
            r"\b(open (?:the |a )?(?:pull request|pr)|pull request kholo)\b", t
        ):
            r = self._call("open_fix_pr", {"confirmation_phrase": text})
            reply = (
                str(r.get("spoken_hint"))
                if r.get("opened")
                else f"I could not open it: {r.get('error')}"
            )
        elif re.search(r"\bundo fix\b", t):
            m = re.search(r"undo fix (\w+)", t)
            fix = {"one": 1, "two": 2}.get(m.group(1), 1) if m else 1
            if m and m.group(1).isdigit():
                fix = int(m.group(1))
            r = self._call("undo_fix", {"fix_id": fix, "confirmation_phrase": text})
            reply = (
                f"Undone on your word: {r['blast_radius']} You are back to awaiting a decision."
                if r.get("undone")
                else f"I could not undo that: {r.get('error')}"
            )
        elif re.search(r"\bapprove fix\b", t):
            m = re.search(r"approve fix (\w+)", t)
            fix = (
                {"one": 1, "two": 2}.get(
                    m.group(1), int(m.group(1)) if m and m.group(1).isdigit() else 1
                )
                if m
                else 1
            )
            r = self._call("approve_fix", {"fix_id": fix, "confirmation_phrase": text})
            reply = (
                "Approved on your word, applying now. I will report when CloudWatch agrees it is recovered."
                if r.get("approved")
                else f"I could not approve that: {r.get('error')}"
            )
        elif (
            "grant" in t
            or t.strip() in ("yes", "haan", "sure", "ok", "okay", "do it", "theek hai")
            or "contract" in t
        ):
            r = self._call("grant_sleep_contract", {"days": 7, "max_uses": 3})
            reply = (
                r["read_back"]
                if r.get("read_back_pending")
                else (
                    "Contract granted for seven days, three uses, scoped to that one rule. Good night."
                    if r.get("granted")
                    else f"Not granted: {r.get('error')}"
                )
            )
        elif "fix" in t or "repair" in t or "restore" in t:
            r = self._call("propose_fix", {})
            if r.get("error"):
                reply = f"I cannot propose a safe fix: {r['error']}"
            else:
                reply = f"I can restore that one ingress rule. Blast radius: {r['blast_radius']} The dry run {'passed' if r['dry_run']['ok'] else 'failed'} under the remediator role [E{len([e for e in self.messages if 'toolResult' in str(e)])}]. To apply it, say exactly: {r['confirmation_phrase']}."
        elif "chang" in t or "why" in t or "cause" in t:
            self._call("get_evidence", {"kind": "changes"})
            self._call("get_evidence", {"kind": "diagnostics"})
            reply = "One write call landed just before the alarm: RevokeSecurityGroupIngress on the R D S security group by user prashant [E1]. That removed the tcp 5432 rule from the E C S tasks, which the golden snapshot says should be there [E2]. That is the cause."
        elif "fixed" in t or "status" in t or "recover" in t:
            r = self._call("check_recovery", {})
            reply = f"Status is {r.get('status')}."
        else:
            b = self._call("get_incident_brief", {})
            reply = (
                f"{b.get('summary', '')} Ask me what changed, or whether I can fix it."
            )
        self.messages.append({"role": "assistant", "content": [{"text": reply}]})
        return _ScriptedResult(reply)


class _ScriptedResult:
    """Looks like a Strands AgentResult to voice_turn (text + token metrics)."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.metrics = type(
            "M", (), {"accumulated_usage": {"inputTokens": 2100, "outputTokens": 140}}
        )()

    def __str__(self) -> str:
        return self._text


# ---------------------------------------------------------------------------
# FastAPI adapter around the two Function URL handlers
# ---------------------------------------------------------------------------


def _url_event(request: Request, body: bytes, prefix: str) -> dict[str, Any]:
    path = request.url.path[len(prefix) :] or "/"
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": request.url.query,
        "queryStringParameters": dict(request.query_params) or None,
        "headers": {k.lower(): v for k, v in request.headers.items()},
        "requestContext": {
            "http": {"method": request.method, "path": path, "sourceIp": "127.0.0.1"},
            "requestId": "local",
            "stage": "$default",
        },
        "body": body.decode() if body else None,
        "isBase64Encoded": False,
    }


def _to_response(result: Any) -> Response:
    if isinstance(result, dict) and "statusCode" in result:
        headers = {
            k: v
            for k, v in (result.get("headers") or {}).items()
            if k.lower() != "content-length"
        }
        return Response(
            content=result.get("body") or "",
            status_code=int(result["statusCode"]),
            headers=headers,
            media_type="application/json",
        )
    return Response(content=json.dumps(result), media_type="application/json")


def create_app() -> FastAPI:
    from contextlib import asynccontextmanager

    from beacon import dashboard_api, voice_turn
    from beacon import handler as triage_handler

    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        world = LocalWorld()
        _patch_remote_calls(world)
        world.break_db()
        triage_handler.handler(world.alarm_event(), None)
        state["world"] = world
        try:
            yield
        finally:
            world.close()
            state.clear()

    app = FastAPI(title="Beacon local", lifespan=lifespan)

    def world_of() -> LocalWorld:
        return state["world"]

    @app.get("/config.json")
    def config(request: Request) -> dict[str, Any]:
        base = str(request.base_url).rstrip("/")
        return {
            "voiceUrl": f"{base}/voice",
            "dashboardUrl": f"{base}/dash",
            "region": REGION,
            "sttLanguage": "en-IN",
            "voiceBackend": "assemblyai"
            if os.environ.get("ASSEMBLYAI_API_KEY")
            else "aws",
            "archivedIncidentId": "",
            "local": True,
            # Local only: lets the console's "Run the night" button unlock itself.
            "localPasscode": os.environ["PASSCODE"],
        }

    @app.api_route("/dash/{rest:path}", methods=["GET", "POST", "DELETE", "OPTIONS"])
    async def dash(request: Request, rest: str) -> Response:
        # One process writes and reads here; skip the per-container cache so
        # the board reflects a break/fix on the very next poll.
        dashboard_api._cache.clear()
        return _to_response(
            dashboard_api.handler(
                _url_event(request, await request.body(), "/dash"), None
            )
        )

    @app.api_route("/voice/{rest:path}", methods=["GET", "POST", "OPTIONS"])
    async def voice(request: Request, rest: str) -> Response:
        return _to_response(
            voice_turn.handler(
                _url_event(request, await request.body(), "/voice"), None
            )
        )

    @app.post("/local/break")
    def local_break(request: Request) -> dict[str, Any]:
        if request.headers.get("x-beacon-passcode") != os.environ["PASSCODE"]:
            return {"ok": False, "error": "passcode required"}
        world = world_of()
        world.break_db()
        result = triage_handler.handler(world.alarm_event(), None)
        return {"ok": True, **{k: v for k, v in result.items() if k != "statusCode"}}

    @app.post("/local/fix")
    def local_fix(request: Request) -> dict[str, Any]:
        if request.headers.get("x-beacon-passcode") != os.environ["PASSCODE"]:
            return {"ok": False, "error": "passcode required"}
        from beacon.remediation import actions_sg

        world = world_of()
        actions_sg.execute(world.params, ec2_client=world.ec2)
        world.alarm_ok()
        return {"ok": True}

    dist = ROOT / "web" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="console")
    return app


app = None  # created by main() or tests

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        create_app(), host="127.0.0.1", port=int(os.environ.get("PORT", "8000"))
    )
