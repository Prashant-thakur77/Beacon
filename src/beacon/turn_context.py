"""Per-turn context the tools read instead of trusting the model's arguments.

The single most important safety rule in Beacon: consent is decided on the
raw user transcript of the current turn (``TurnContext.transcript``), never
on what the model claims the user said.  The context also collects the
evidence cards a turn produced so the UI can pin sentences to them.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(slots=True)
class TurnContext:
    incident_id: str
    session_id: str
    transcript: str
    channel: str = "typed"
    passcode_ok: bool = False
    # Where the words came from: STT confidence, the voice-note file id, the
    # AssemblyAI session id. Stored on every approval and contract this turn makes.
    attestation: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)

    def add_evidence(self, kind: str, title: str, payload: Any) -> dict[str, Any]:
        card = {
            "id": f"E{len(self.evidence) + 1}",
            "kind": kind,
            "title": title,
            "payload": payload,
        }
        self.evidence.append(card)
        return card


_current: ContextVar[TurnContext | None] = ContextVar("beacon_turn", default=None)


def current() -> TurnContext:
    ctx = _current.get()
    if ctx is None:
        raise RuntimeError("no active turn context")
    return ctx


@contextmanager
def turn_context(ctx: TurnContext) -> Iterator[TurnContext]:
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
