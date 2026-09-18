"""Parser for the RCA text contract produced by ``prompts/triage.txt``.

The triage model returns labelled sections (``STATUS:``, ``SUMMARY:``,
``EVIDENCE:`` ...) followed by an optional fenced ``BEACON_JSON:`` tail.
Everything downstream (handler, notifier, voice agent, dashboard) reads
the structured :class:`RcaJson` instead of scanning the raw text.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

_SECTION_NAMES = (
    "STATUS",
    "SUMMARY",
    "AFFECTED COMPONENTS",
    "EVIDENCE",
    "NEXT STEPS",
    "CHANGE CORRELATION",
    "SPOKEN SUMMARY",
    "BEACON_JSON",
)

# Tolerates markdown emphasis around the label: ``**STATUS:** High``.
_HEADER_RE = re.compile(
    r"^\s*\**\s*(" + "|".join(_SECTION_NAMES) + r")\s*:?\s*\**\s*:?\s*(.*)$",
    re.IGNORECASE,
)
_LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
_FENCE_RE = re.compile(r"^\s*```")


@dataclass(slots=True)
class RcaJson:
    """Structured view of one triage response."""

    status: str = "Unknown"
    summary: str = ""
    affected_components: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    change_correlation: str | None = None
    spoken_summary: str = ""
    beacon_json: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str | None:
        value = self.beacon_json.get("fingerprint")
        return str(value) if value else None

    @property
    def suggested_action(self) -> str | None:
        value = self.beacon_json.get("suggested_action")
        return str(value) if value else None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in text.splitlines():
        match = _HEADER_RE.match(raw_line)
        if match:
            current = match.group(1).upper()
            sections.setdefault(current, [])
            rest = match.group(2).strip()
            if rest:
                sections[current].append(rest)
            continue
        if current is not None:
            sections[current].append(raw_line.rstrip())
    return sections


def _joined(lines: list[str]) -> str:
    return " ".join(line.strip() for line in lines if line.strip())


def _list_items(lines: list[str]) -> list[str]:
    items = [_LIST_PREFIX_RE.sub("", line).strip() for line in lines]
    return [item for item in items if item]


def _parse_json_tail(lines: list[str]) -> dict[str, Any]:
    body = "\n".join(line for line in lines if not _FENCE_RE.match(line)).strip()
    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse(text: str) -> RcaJson:
    """Parse triage output into an :class:`RcaJson`.

    Unstructured text (no recognised headers) is kept whole as the summary
    with ``status`` ``"Unknown"`` so callers never lose the model's words.
    """
    sections = _split_sections(text)
    if not sections:
        return RcaJson(summary=text.strip())

    status = _joined(sections.get("STATUS", [])) or "Unknown"
    components = [
        part.strip()
        for part in _joined(sections.get("AFFECTED COMPONENTS", [])).split(",")
        if part.strip()
    ]
    change = _joined(sections.get("CHANGE CORRELATION", [])) or None

    return RcaJson(
        status=status,
        summary=_joined(sections.get("SUMMARY", [])),
        affected_components=components,
        evidence=_list_items(sections.get("EVIDENCE", [])),
        next_steps=_list_items(sections.get("NEXT STEPS", [])),
        change_correlation=change,
        spoken_summary=_joined(sections.get("SPOKEN SUMMARY", [])),
        beacon_json=_parse_json_tail(sections.get("BEACON_JSON", [])),
    )


def is_healthy(text: str) -> bool:
    """Return True when the triage ``STATUS`` is ``Healthy``."""
    return parse(text).status.strip().lower() == "healthy"
