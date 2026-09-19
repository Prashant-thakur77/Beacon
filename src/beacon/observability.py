"""Powertools Metrics (CloudWatch EMF) and Tracer (X-Ray), wrapped so the rest
of the code never imports Powertools directly and works outside Lambda.

* ``metrics_scope()``: collect metrics for one invocation, flush as one EMF blob.
* ``metric(name, value, unit)``: record inside a scope (no-op outside one).
* ``span(name)``: an X-Ray subsegment around a function (no-op when tracing is
  disabled, i.e. locally and in tests).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any, TypeVar

from aws_lambda_powertools import Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

_metrics: Metrics | None = None
_tracer: Tracer | None = None
_in_scope = False


def _get_metrics() -> Metrics:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        _metrics = Metrics(
            namespace=os.environ.get("POWERTOOLS_METRICS_NAMESPACE", "Beacon"),
            service=os.environ.get("POWERTOOLS_SERVICE_NAME", "beacon"),
        )
    return _metrics


def _get_tracer() -> Tracer:
    global _tracer  # noqa: PLW0603
    if _tracer is None:
        _tracer = Tracer(service=os.environ.get("POWERTOOLS_SERVICE_NAME", "beacon"))
    return _tracer


def reset() -> None:
    """Forget cached providers (tests change env between cases)."""
    global _metrics, _tracer, _in_scope  # noqa: PLW0603
    _metrics = None
    _tracer = None
    _in_scope = False


@contextmanager
def metrics_scope(**dimensions: str) -> Iterator[None]:
    """Collect metrics for one invocation and flush them as a single EMF line."""
    global _in_scope  # noqa: PLW0603
    m = _get_metrics()
    for key, value in dimensions.items():
        if value:
            m.add_dimension(name=key, value=str(value))
    _in_scope = True
    try:
        yield
    finally:
        _in_scope = False
        try:
            m.flush_metrics(raise_on_empty_metrics=False)
        except Exception:  # never let telemetry break the request
            logger.exception("metrics flush failed")


def metric(name: str, value: float, *, unit: str = "Count") -> None:
    """Record one metric value inside the current scope."""
    if not _in_scope:
        return
    try:
        _get_metrics().add_metric(name=name, unit=MetricUnit[unit], value=float(value))
    except Exception:
        logger.exception("metric %s failed", name)


def metadata(key: str, value: str) -> None:
    """Attach a non-dimension field to the EMF line (searchable in Logs Insights)."""
    if not _in_scope or not value:
        return
    try:
        _get_metrics().add_metadata(key=key, value=value)
    except Exception:
        logger.exception("metadata %s failed", key)


def span(name: str) -> Callable[[F], F]:
    """X-Ray subsegment around a function; transparent when tracing is off."""

    def decorate(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = _get_tracer()
            if tracer.disabled:
                return fn(*args, **kwargs)
            with tracer.provider.in_subsegment(name=f"## {name}"):
                return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorate
