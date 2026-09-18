#!/usr/bin/env bash
# Invoke one voice tool through the voice-turn Lambda in tool_only mode.
# The transcript is what the "engineer said"; approve_fix checks it server-side.
#
# Usage: scripts/voice_tool.sh <stack> <region> <incident_id> <tool> '<args-json>' '<transcript>' <passcode>
set -euo pipefail
STACK="$1"; REGION="$2"; INCIDENT="$3"; TOOL="$4"; ARGS="${5:-{\}}"; TRANSCRIPT="${6:-}"; PASSCODE="${7:-}"
PY="${PYTHON:-.venv/bin/python}"

PAYLOAD="$("$PY" -c '
import json, sys
print(json.dumps({"mode": "tool_only", "tool": sys.argv[1], "args": json.loads(sys.argv[2] or "{}"),
                  "incident_id": sys.argv[3], "transcript": sys.argv[4], "channel": "cli", "passcode": sys.argv[5]}))
' "$TOOL" "$ARGS" "$INCIDENT" "$TRANSCRIPT" "$PASSCODE")"

OUT="$(mktemp)"
aws lambda invoke --function-name "beacon-voice-turn-${STACK}" --region "$REGION" \
    --cli-binary-format raw-in-base64-out --payload "$PAYLOAD" "$OUT" > /dev/null
"$PY" -c '
import json, sys
d = json.load(open(sys.argv[1]))
print(json.dumps(d, indent=1, default=str))
sys.exit(0 if d.get("ok") else 1)
' "$OUT"
