# Contributing

## Setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python torch --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python --no-deps cordon
uv pip install --python .venv/bin/python -e ".[agent,dev]"
cd web && npm ci && cd ..
```

`cordon` pulls CUDA torch by default; the CPU wheel first and `--no-deps`
keeps the environment small. `make local` runs the whole product with no AWS
account.

## The gate

Every commit goes through `bash scripts/commit.sh "message"`, which runs
`scripts/gate.sh` (ruff format and lint, mypy `--strict`, cfn-lint on all four
templates, pytest, the console's `tsc`) and refuses to commit on red. CI runs
the same gate.

## Rules that keep the safety model honest

- Tests first. A new control gets a test in `tests/test_template_safety.py`
  (IAM) or `tests/test_template_ops.py` (operations) that parses the real
  CloudFormation; a new tool gets a test under `turn_context` that proves what
  the model *cannot* do with it.
- Consent is read from `TurnContext`, never from tool arguments.
- A new remediation action needs: a `registry` entry with a strict schema, a
  data allowlist (golden snapshot or `REMEDIABLE_ECS_SERVICES`), a
  post-condition in `remediation/verify.py`, and a tag-scoped IAM statement.
- Image dependencies are pinned in `requirements/`; bump them deliberately
  and run the gate (a test checks the pins match the environment).
- Docs live next to the code they describe; `docs/LEARNINGS.md` gets a dated
  entry for anything that surprised you.
