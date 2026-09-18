#!/usr/bin/env bash
# Save the artefacts of the most recent real incident for fixtures/replay:
# triage Lambda log, demo app log tail, newest incident item, change ledger.
# Usage: scripts/capture_run.sh <stack> <region> [since]
set -euo pipefail
STACK="${1:-beacon}"; REGION="${2:-us-east-1}"; SINCE="${3:-30m}"
TS="$(date +%Y%m%dT%H%M%S)"
DIR="tests/fixtures/real"; mkdir -p "$DIR"

aws logs tail "/aws/lambda/beacon-${STACK}" --since "$SINCE" --region "$REGION" --format short > "$DIR/triage-${TS}.log" || true
aws logs tail /ecs/beacon-demo --since "$SINCE" --region "$REGION" --format short | tail -300 > "$DIR/demo-logs-${TS}.log" || true

PY="${PYTHON:-.venv/bin/python}"
aws dynamodb scan --table-name "beacon-incidents-${STACK}" --region "$REGION" --output json 2>/dev/null | \
"$PY" -c '
import json, sys
from boto3.dynamodb.types import TypeDeserializer
d = TypeDeserializer()
rows = json.load(sys.stdin).get("Items", [])
plain = [{k: d.deserialize(v) for k, v in r.items()} for r in rows]
plain.sort(key=lambda r: str(r.get("timestamp", "")), reverse=True)
print(json.dumps(plain[0] if plain else {}, indent=1, default=str))
' > "$DIR/incident-${TS}.json" || echo '{}' > "$DIR/incident-${TS}.json"

aws dynamodb scan --table-name "beacon-changes-${STACK}" --region "$REGION" --output json 2>/dev/null > "$DIR/changes-${TS}.json" || echo '{}' > "$DIR/changes-${TS}.json"

echo "Saved:"; ls -la "$DIR" | grep "$TS"
echo "Incident status: $("$PY" -c "import json; print(json.load(open('$DIR/incident-${TS}.json')).get('status','(none)'))")"
