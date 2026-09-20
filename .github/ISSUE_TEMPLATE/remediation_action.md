---
name: New remediation action
about: Propose a new allowlisted action (read CONTRIBUTING first)
labels: enhancement, safety
---

**Action name** (e.g. `rds.reboot_instance`)

**Exact failure it fixes, and how the alarm identifies it**

**Params schema** (strict; every field validated)

**Data allowlist** — what decides *which* resources may be touched (golden snapshot, tag, env list)?

**Post-condition** — how `remediation/verify.py` proves it worked

**IAM** — the minimal write statement, tag-scoped; note any untaggable resource types
