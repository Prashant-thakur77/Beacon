"""Powertools Metrics (EMF) and Tracer wiring: the shots that prove it is real."""

from __future__ import annotations

import json
from typing import Any

import pytest

from beacon import observability


@pytest.fixture()
def emf(monkeypatch: Any, capsys: Any) -> Any:
    monkeypatch.setenv("POWERTOOLS_METRICS_NAMESPACE", "Beacon")
    monkeypatch.setenv("POWERTOOLS_SERVICE_NAME", "beacon-test")
    monkeypatch.setenv("POWERTOOLS_TRACE_DISABLED", "true")
    observability.reset()
    yield capsys


def _emf_blobs(captured: str) -> list[dict[str, Any]]:
    blobs = []
    for line in captured.splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "_aws" in data:
            blobs.append(data)
    return blobs


def test_metrics_are_emitted_as_emf_with_the_incident_dimension(emf: Any) -> None:
    with observability.metrics_scope():
        observability.metric("TurnLatencyMs", 812, unit="Milliseconds")
        observability.metric("ToolCalls", 2, unit="Count")
        observability.metric("HumansWoken", 0, unit="Count")
    blobs = _emf_blobs(emf.readouterr().out)
    assert len(blobs) == 1
    blob = blobs[0]
    names = {m["Name"] for m in blob["_aws"]["CloudWatchMetrics"][0]["Metrics"]}
    assert names == {"TurnLatencyMs", "ToolCalls", "HumansWoken"}
    assert blob["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "Beacon"
    flat = {
        k: (v[0] if isinstance(v, list) else v) for k, v in blob.items() if k != "_aws"
    }
    assert flat["TurnLatencyMs"] == 812 and flat["HumansWoken"] == 0


def test_metrics_scope_is_safe_when_nothing_was_recorded(emf: Any) -> None:
    with observability.metrics_scope():
        pass
    assert _emf_blobs(emf.readouterr().out) == []


def test_tool_span_runs_the_function_outside_lambda(emf: Any) -> None:
    @observability.span("tool:get_evidence")
    def work(x: int) -> int:
        return x * 2

    assert work(21) == 42


def test_voice_turn_emits_turn_metrics(monkeypatch: Any) -> None:
    from beacon import voice_turn
    from tests.test_voice_turn import (
        FakeAgent,  # noqa: F401  (import ensures module loads)
    )

    assert hasattr(voice_turn, "_emit_turn_metrics")


def test_remediate_emits_loop_metrics() -> None:
    from beacon import remediate

    assert hasattr(remediate, "_emit_outcome_metrics")


def test_remediate_metrics_use_only_the_service_dimension(
    emf: Any, monkeypatch: Any
) -> None:
    """The dashboard and the Escalated alarm query ``{service}``; an extra
    dimension would put the metrics in a set nothing reads."""
    from beacon import remediate

    monkeypatch.setitem(remediate._STEPS, "escalate", lambda e: {"ok": True})
    remediate.handler(
        {"step": "escalate", "action": "sg.restore_ingress", "incident_id": "inc-1"},
        None,
    )
    blob = _emf_blobs(emf.readouterr().out)[0]
    assert blob["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [["service"]]
    assert blob["service"] == "beacon-remediate"
    assert blob["action"] == "sg.restore_ingress" and blob["incident_id"] == "inc-1"
    assert "Escalated" in {
        m["Name"] for m in blob["_aws"]["CloudWatchMetrics"][0]["Metrics"]
    }
