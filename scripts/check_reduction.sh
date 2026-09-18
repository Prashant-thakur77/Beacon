#!/usr/bin/env bash
# Did Cordon (Nova Embeddings) actually reduce the logs on the last triage run?
# Greps the triage Lambda log for the reduction line. Exit 1 if absent, so the
# "reduced to top NN%" claim is never made in the video without evidence.
# Usage: scripts/check_reduction.sh <stack> <region> [since]
set -euo pipefail
STACK="${1:-beacon}"; REGION="${2:-us-east-1}"; SINCE="${3:-30m}"
LINES="$(aws logs tail "/aws/lambda/beacon-${STACK}" --since "$SINCE" --region "$REGION" --format short 2>/dev/null | grep -i "Cordon reduced" || true)"
if [ -z "$LINES" ]; then
    echo "NO REDUCTION LINE in /aws/lambda/beacon-${STACK} (last ${SINCE})."
    echo "Either the logs fit the TOKEN_BUDGET (lower it: make deploy TOKEN_BUDGET=3000) or no triage ran yet."
    exit 1
fi
echo "$LINES" | tail -5
echo "REDUCTION CONFIRMED"
