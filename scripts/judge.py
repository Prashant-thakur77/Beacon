"""The whole night in one command, in a terminal, with no microphone and no AWS.

lablab's guidance is that a demo a judge cannot reach scores as if it does not work,
and that the value has to be legible in about thirty seconds. The live console covers
the first; this covers the second, for anyone who would rather read than click.

It drives the real tools against `make local` using typed transcripts — the same
route the browser and the phone use, with the same consent rules — and prints the
night as it happens: the cause, the change that caused it, the proposal, an approval
that is *refused* because agreement is not consent, the one that works, the
verification, and the consent certificate that justifies the change afterwards.

    make local          # in another shell
    make judge
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

BASE = "http://localhost:8000"
PASSCODE = "local"

DIM, BOLD, GREEN, AMBER, RED, OFF = (
    "\033[2m",
    "\033[1m",
    "\033[32m",
    "\033[33m",
    "\033[31m",
    "\033[0m",
)


def _req(method: str, path: str, body: Any = None) -> Any:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"content-type": "application/json", "x-beacon-passcode": PASSCODE},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:  # noqa: S310
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        return {"error": f"{exc.code}: {exc.read().decode()[:200]}"}


def tool(name: str, incident: str, said: str, **args: Any) -> dict[str, Any]:
    """Run one tool the way the browser does: with the words that asked for it."""
    out = _req(
        "POST",
        f"/voice/tools/{name}",
        {
            "incident_id": incident,
            "session_id": "judge",
            "transcript": said,
            "channel": "typed",
            "args": args,
        },
    )
    return dict(out.get("result") or out)


def say(who: str, words: str) -> None:
    colour = BOLD if who == "you" else DIM
    print(f"  {colour}{who:>6}{OFF}  {words}")


def beat(title: str) -> None:
    print(f"\n{BOLD}── {title}{OFF}")


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base.rstrip("/")

    if "error" in _req("GET", "/config.json"):
        print(f"{RED}Start `make local` in another shell first.{OFF}")
        return 1

    beat("03:12 — something breaks")
    _req("POST", "/local/break", {})
    incident: dict[str, Any] = {}
    for _ in range(30):
        time.sleep(1)
        found = _req("GET", "/dash/incidents").get("incidents") or []
        if found and found[0].get("status") in (
            "awaiting_engineer",
            "awaiting_approval",
        ):
            incident = found[0]
            break
    if not incident:
        print(f"{RED}no incident appeared{OFF}")
        return 1
    inc = str(incident["incident_id"])
    rca = incident.get("rca_json") or {}
    print(f"  {DIM}incident {inc[:8]} · alarm {incident.get('alarm_name')}{OFF}")
    say("beacon", str(rca.get("spoken_summary") or rca.get("summary") or "")[:200])

    beat("What changed?")
    say("you", "why? what changed?")
    changes = tool("get_evidence", inc, "why? what changed?", kind="changes")
    # moto keeps no CloudTrail, so locally the ledger on the incident is the record.
    found = changes.get("data") or incident.get("changes") or []
    top = dict(found[0]) if found else {}
    if top:
        say(
            "beacon",
            f"{top.get('event_name')} by {top.get('actor_short')} at "
            f"{top.get('event_time')} on {', '.join(top.get('resource_ids') or [])}",
        )
    else:
        say("beacon", "no CloudTrail change in the window")

    beat("What are my options?")
    say("you", "what are my options?")
    proposal = tool("propose_fix", inc, "what are my options?")
    fix_id = proposal.get("fix_id", 1)
    say(
        "beacon",
        str(proposal.get("blast_radius_spoken") or proposal.get("blast_radius")),
    )
    dry = proposal.get("dry_run") or {}
    print(f"  {DIM}dry run: {dry.get('code')} — nothing has been applied{OFF}")
    say("beacon", f"say exactly: approve fix {fix_id}")

    beat("Agreement is not consent")
    say("you", "yes, do it")
    refused = tool(
        "approve_fix",
        inc,
        "yes, do it",
        fix_id=fix_id,
        confirmation_phrase="yes, do it",
    )
    ok = bool(refused.get("approved"))
    print(
        f"  {RED if ok else GREEN}{'APPLIED — this is a bug' if ok else 'REFUSED'}{OFF}"
        f"  {DIM}{refused.get('error') or 'the phrase was not said'}{OFF}"
    )

    beat("The exact phrase")
    phrase = f"approve fix {fix_id}"
    say("you", phrase)
    applied = tool(
        "approve_fix", inc, phrase, fix_id=fix_id, confirmation_phrase=phrase
    )
    if not applied.get("approved"):
        print(f"  {RED}not approved: {applied.get('error')}{OFF}")
        return 1
    print(f"  {GREEN}APPLIED{OFF}  {DIM}{applied.get('instruction', '')}{OFF}")

    beat("Recovered means proven")
    for _ in range(20):
        time.sleep(2)
        state = tool("check_recovery", inc, "is it fixed?")
        if state.get("status") in ("resolved", "escalated"):
            break
    checks = (state.get("last_verify") or {}).get("checks") or []
    for c in checks:
        mark = f"{GREEN}ok{OFF}" if c.get("ok") else f"{AMBER}no{OFF}"
        print(f"  {mark}  {c.get('name')}: {DIM}{c.get('detail')}{OFF}")
    print(f"  {BOLD}{state.get('status')}{OFF}")

    beat("Why this was allowed — the consent certificate")
    from beacon import certificate

    fresh = _req("GET", f"/dash/incidents/{inc}").get("incident") or {}
    rows = [
        r
        for r in (_req("GET", "/dash/audit").get("rows") or [])
        if r.get("incident_id") == inc and r.get("quote")
    ]
    if rows:
        row = rows[0]
        cert = certificate.build(
            fresh,
            {
                "approval_id": row.get("approval_id"),
                "fix_id": fix_id,
                "transcript_quote": row.get("quote"),
                "channel": row.get("channel"),
                "granted_at": row.get("at"),
                "action": row.get("action"),
                "params": row.get("params") or {},
                "attestation": row.get("attestation") or {},
            },
        )
        print()
        for line in certificate.to_markdown(cert).splitlines():
            print(f"  {line}")
    else:
        print(f"  {DIM}no approval row found{OFF}")

    print(
        f"\n{DIM}Everything above ran against the real tools with the real consent "
        f"rules. The only thing simulated is AWS.{OFF}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
