"""Voice-turn Lambda behind a Function URL (``beacon-voice-turn-<stack>``).

Routes (all JSON, CORS open, passcode header on the ones that cost money):
``GET /health`` and the EventBridge keep-warm ping ``{"mode": "warm"}``.
The ``/session`` and ``/turn`` routes arrive with the voice agent.
"""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools.event_handler import CORSConfig, LambdaFunctionUrlResolver

SERVICE = "beacon-voice-turn"

app = LambdaFunctionUrlResolver(
    cors=CORSConfig(allow_origin="*", allow_headers=["x-beacon-passcode"])
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": SERVICE}


def handler(event: dict[str, Any], context: Any) -> Any:
    if isinstance(event, dict) and event.get("mode") == "warm":
        return {"ok": True, "warm": True}
    return app.resolve(event, context)
