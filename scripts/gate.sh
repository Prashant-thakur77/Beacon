#!/usr/bin/env bash
# Full local quality gate: format check, lint, mypy, tests, cfn-lint. Exit non-zero on any failure.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin
$PY/ruff format --check src tests scripts
$PY/ruff check src tests scripts
$PY/mypy src/beacon/
$PY/cfn-lint template.yaml remediation-template.yaml console-template.yaml demo/demo-infra-template.yaml --ignore-checks W1011
$PY/python -m pytest -q -p no:warnings
# The console is part of the product: a type error there is a red gate too.
if [ -d web/node_modules ]; then (cd web && npx tsc --noEmit -p .); fi
echo "GATE PASSED"
