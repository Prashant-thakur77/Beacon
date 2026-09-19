"""One place to build boto3 clients with bounded timeouts and retries.

Lambda budgets are short (45 s for a voice turn); a hung Polly or STS call
must fail fast enough to leave room for the fallback, so every client made
here carries a connect/read timeout and the standard retry mode.
"""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

DEFAULT_CONFIG = Config(
    connect_timeout=3,
    read_timeout=15,
    retries={"max_attempts": 2, "mode": "standard"},
)


def client(service: str, *, region_name: str | None = None, **overrides: Any) -> Any:
    """``boto3.client`` with the bounded config (overrides win)."""
    cfg = DEFAULT_CONFIG.merge(Config(**overrides)) if overrides else DEFAULT_CONFIG
    # boto3-stubs types ``client`` per service literal; the name is dynamic here.
    make: Any = boto3.client
    if region_name:
        return make(service, region_name=region_name, config=cfg)
    return make(service, config=cfg)
