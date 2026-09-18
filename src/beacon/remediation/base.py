from __future__ import annotations

from dataclasses import dataclass


class ParamError(ValueError):
    """Raised when an action id or its parameters are not allowlisted."""


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Outcome of a dry-run or execute call.

    ``code`` is the AWS error code (or a short success label) so the UI and
    the audit trail can show exactly what the API said.
    """

    ok: bool
    code: str
    detail: str = ""
