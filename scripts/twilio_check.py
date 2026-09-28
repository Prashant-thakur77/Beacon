"""Is this Twilio account ready to call the engineer? Ask Twilio, not the docs.

Four things have to be true before `scripts/phone_demo.sh` can dial an Indian
mobile, and three of them are silent until a call fails. This checks all four in one
request each and prints what to click for any that are missing.

    TWILIO_ACCOUNT_SID=AC… TWILIO_AUTH_TOKEN=… \
        .venv/bin/python scripts/twilio_check.py +919015954507
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.twilio.com/2010-04-01"
VOICE = "https://voice.twilio.com/v1"


def get(url: str, sid: str, token: str) -> dict:
    req = urllib.request.Request(url)
    req.add_header(
        "Authorization",
        "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode(),
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        return dict(json.loads(resp.read().decode()))


def main() -> int:
    to = sys.argv[1] if len(sys.argv) > 1 else "+919015954507"
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not (sid and token):
        print("set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN")
        return 1
    if not sid.startswith("AC") or len(sid) != 34:
        print(f"  ✗ the Account SID looks wrong: {sid!r}")
        print(
            "    it is 34 characters: 'AC' then 32 hex.\n"
            "    Use the copy icon beside it in the console."
        )
        return 1

    problems: list[str] = []
    print()

    try:
        account = get(f"{API}/Accounts/{sid}.json", sid, token)
    except urllib.error.HTTPError as exc:
        print(f"  ✗ Twilio refused the credentials ({exc.code}) — check the auth token")
        return 1
    trial = account.get("type") == "Trial"
    print(f"  ok  account {account.get('friendly_name')} · {account.get('type')}")

    numbers = get(f"{API}/Accounts/{sid}/IncomingPhoneNumbers.json", sid, token)
    voice_numbers = [
        n
        for n in numbers.get("incoming_phone_numbers") or []
        if (n.get("capabilities") or {}).get("voice")
    ]
    if voice_numbers:
        for n in voice_numbers:
            print(f"  ok  number {n['phone_number']} can do voice")
        print(f"      → use TWILIO_FROM_NUMBER={voice_numbers[0]['phone_number']}")
    else:
        problems.append(
            "No voice-capable number. Console → Phone Numbers → Manage → Buy a number\n"
            "      → Country: United States, tick Voice. Instant. Do NOT buy\n"
            "      an Indian one: those need a regulatory bundle and days of review."
            "      number: those need a regulatory bundle and days of review."
        )

    verified = get(f"{API}/Accounts/{sid}/OutgoingCallerIds.json", sid, token)
    ids = {c.get("phone_number") for c in verified.get("outgoing_caller_ids") or []}
    if to in ids or not trial:
        print(
            f"  ok  {to} may be called"
            + (" (verified)" if to in ids else " (paid account)")
        )
    else:
        problems.append(
            f"{to} is not a Verified Caller ID, and a trial account can only call\n"
            "      verified numbers. Console → Phone Numbers → Manage →\n"
            "      Verified Caller IDs → Add a new Caller ID.\n"
            "      → Add a new Caller ID. Twilio reads you a code; type it in."
        )

    india = get(f"{VOICE}/DialingPermissions/Countries/IN", sid, token)
    if india.get("low_risk_numbers_enabled"):
        print("  ok  outbound calls to India are enabled")
    else:
        problems.append(
            "Calls to India are switched off — they are off by default on every new\n"
            "      account, as anti-fraud. Console → Voice → Settings → Geographic\n"
            "      Permissions → find India → tick it → Save. Until then every call\n"
            "      fails with error 13227."
        )

    if trial:
        print(
            "  !   trial account: Twilio plays 'You have a trial account…' before it\n"
            "      connects. Any payment removes it — otherwise the filmed recording\n"
            "      opens in Twilio's voice rather than Beacon's."
        )

    print()
    for problem in problems:
        print(f"  ✗ {problem}\n")
    print(f"  {'READY TO DIAL' if not problems else f'{len(problems)} left'}\n")
    return len(problems)


if __name__ == "__main__":
    sys.exit(main())
