#!/usr/bin/env bash
# Everything a real phone call needs, in one command.
#
#   TWILIO_ACCOUNT_SID=AC… TWILIO_AUTH_TOKEN=… TWILIO_FROM_NUMBER=+1… \
#     scripts/phone_demo.sh +919015954507 [incident_id]
#
# Opens a Cloudflare tunnel to the local bridge, starts the bridge against the
# deployed voice Lambda, waits for both to answer, then dials. Ctrl-C stops
# everything. The call is recorded to $PHONE_OUT as two channels.
set -euo pipefail
cd "$(dirname "$0")/.."

TO="${1:?usage: phone_demo.sh +91XXXXXXXXXX [incident_id]}"
INCIDENT="${2:-}"
PORT="${PORT:-8080}"
PHONE_OUT="${PHONE_OUT:-$HOME/beacon-video/phone/live}"
PY="${PYTHON:-.venv/bin/python}"
REGION="${REGION:-us-east-1}"
CONSOLE_STACK="${CONSOLE_STACK:-beacon-console}"

for v in TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN TWILIO_FROM_NUMBER; do
    [ -n "${!v:-}" ] || { echo "set $v (see docs/phone.md)" >&2; exit 1; }
done
PASSCODE="${PASSCODE:-$(sed -n 's/^PASSCODE=//p' .beacon.env | head -1)}"
[ -n "$PASSCODE" ] || { echo "no PASSCODE in .beacon.env" >&2; exit 1; }

VOICE_URL=$(aws cloudformation describe-stacks --stack-name "$CONSOLE_STACK" --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`VoiceTurnUrl`].OutputValue' --output text)
[ -n "$VOICE_URL" ] || { echo "no VoiceTurnUrl on $CONSOLE_STACK" >&2; exit 1; }
VOICE_URL="${VOICE_URL%/}"
echo "==> voice Lambda: $VOICE_URL"

if [ -z "$INCIDENT" ]; then
    DASH=$(aws cloudformation describe-stacks --stack-name "$CONSOLE_STACK" --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`DashboardUrl`].OutputValue' --output text)
    INCIDENT=$(curl -s -H "x-beacon-passcode: $PASSCODE" "${DASH%/}/incidents" \
        | "$PY" -c 'import sys,json;print(json.load(sys.stdin)["incidents"][0]["incident_id"])')
    echo "==> newest incident: $INCIDENT"
fi

mkdir -p "$PHONE_OUT"
LOGS=$(mktemp -d)
trap 'kill $(jobs -p) 2>/dev/null || true; echo; echo "logs in $LOGS"' EXIT

cloudflared tunnel --url "http://localhost:$PORT" > "$LOGS/tunnel.log" 2>&1 &
echo -n "==> opening a tunnel "
PUBLIC=""
for _ in $(seq 1 40); do
    PUBLIC=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGS/tunnel.log" | head -1 || true)
    [ -n "$PUBLIC" ] && break
    echo -n "."; sleep 1
done
[ -n "$PUBLIC" ] || { echo " failed — see $LOGS/tunnel.log" >&2; exit 1; }
echo " $PUBLIC"

"$PY" -m beacon.phone serve --base-url "$VOICE_URL" --passcode "$PASSCODE" \
    --public-url "$PUBLIC" --port "$PORT" --incident "$INCIDENT" \
    --recordings "$PHONE_OUT" > "$LOGS/bridge.log" 2>&1 &

echo -n "==> waiting for the bridge "
for _ in $(seq 1 30); do
    curl -sf -m 2 "http://localhost:$PORT/health" >/dev/null 2>&1 && break
    echo -n "."; sleep 1
done
echo " ready"

# Twilio fetches the TwiML over the tunnel, so prove the tunnel serves it first.
curl -sf -m 15 "$PUBLIC/twiml" | head -4 || { echo "the tunnel does not reach /twiml" >&2; exit 1; }

echo "==> dialling $TO"
"$PY" -m beacon.phone dial --to "$TO" --twiml-url "$PUBLIC/twiml"
echo "==> answer it. Ctrl-C here when the call is over; the recording lands in $PHONE_OUT"
tail -f "$LOGS/bridge.log"
