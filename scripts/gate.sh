#!/usr/bin/env bash
# Full local quality gate: format check, lint, mypy, tests, cfn-lint. Exit non-zero on any failure.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin
$PY/ruff format --check src tests scripts
$PY/ruff check src tests scripts
$PY/mypy src/beacon/
$PY/cfn-lint template.yaml remediation-template.yaml console-template.yaml demo/demo-infra-template.yaml --ignore-checks W1011
$PY/python -m pytest -q
echo "GATE PASSED"
