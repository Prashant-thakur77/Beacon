"""Read-only dashboard API behind a Function URL (``beacon-dashboard-<stack>``).

Public reads for the console.  Every response is passed through ``redact``
so the account id and actor ARNs never leave the account.  ``GET /health``
now; incidents, contracts, tally and safety routes arrive with the console.
"""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools.event_handler import CORSConfig, LambdaFunctionUrlResolver

SERVICE = "beacon-dashboard"

app = LambdaFunctionUrlResolver(cors=CORSConfig(allow_origin="*"))


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": SERVICE}


def handler(event: dict[str, Any], context: Any) -> Any:
    if isinstance(event, dict) and event.get("mode") == "warm":
        return {"ok": True, "warm": True}
    return app.resolve(event, context)
