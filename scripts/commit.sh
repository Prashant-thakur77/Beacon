#!/usr/bin/env bash
# Gate-then-commit. Refuses to commit if the gate fails. Usage: scripts/commit.sh "message"
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/gate.sh > /tmp/beacon-gate.log 2>&1 || { tail -30 /tmp/beacon-gate.log; echo "GATE FAILED - not committing"; exit 1; }
tail -2 /tmp/beacon-gate.log
git add -A
git -c user.email=prashant101007@gmail.com -c user.name=Prashant commit -q -m "$1"
git log --oneline | head -1
