## What

## Why

## Safety checklist
- [ ] No new write action without a registry entry, data allowlist, post-condition, and tag-scoped IAM statement
- [ ] Consent still comes from `TurnContext`, never from tool arguments
- [ ] `bash scripts/gate.sh` green (ruff, mypy --strict, cfn-lint, pytest, web tsc)
- [ ] Template changes have a test in `tests/test_template_safety.py` or `tests/test_template_ops.py`
- [ ] `docs/LEARNINGS.md` entry if anything surprised you
