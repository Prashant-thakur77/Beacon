#!/usr/bin/env bash
# Print the newest incident id (empty if none). Usage: scripts/latest_incident.sh <stack> <region>
set -euo pipefail
aws dynamodb scan --table-name "beacon-incidents-$1" --region "$2" --output json | \
"${PYTHON:-.venv/bin/python}" -c 'import json,sys; rows=sorted(json.load(sys.stdin)["Items"], key=lambda r: r["timestamp"]["S"], reverse=True); print(rows[0]["incident_id"]["S"] if rows else "")'
