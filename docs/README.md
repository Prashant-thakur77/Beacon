# Documentation index

Beacon was built for two hackathons. The **AssemblyAI Voice Agent** entry is the
current one and lives on branch `assemblyai`; the **First Commit** entry is finished
and its documents are kept as a record. A number in a record document is the number
that was true when it was written — where a count matters, the live one is in the
repository and in the [README](../README.md), and `tests/test_claims.py` checks the
README against the code.

## The AssemblyAI entry — start here

| Read this when | File |
|---|---|
| You want the whole picture in five minutes | [`../README.md`](../README.md) |
| You would rather read the demo than click it | `make local`, then `make judge` |
| **Where AssemblyAI sits**, every API used, and what each one is load-bearing for | [`assemblyai.md`](assemblyai.md) |
| The phone channel: why it exists, why Amazon Connect cannot carry the media, and what a Twilio trial will not do | [`phone.md`](phone.md) |
| Testing a voice agent by speaking to it — in the browser and down a phone line | [`voice-testing.md`](voice-testing.md) |
| Telegram pages and voice notes | [`telegram.md`](telegram.md) |
| The pull request that ends the incident | [`fix-at-source.md`](fix-at-source.md) |
| Whether it is safe to give it write access | [`safety.md`](safety.md) — every control and the test that proves it |
| The diagrams (system, one incident, the verify loop, trust boundaries) | [`architecture.md`](architecture.md) |
| Deploying it to an account | [`human-runbook.md`](human-runbook.md) (§0–§3 with expected outputs), `make help` |
| Something surprised us and you want to know why the code is the way it is | [`LEARNINGS.md`](LEARNINGS.md) |
| The business case — who it is for, market, pricing | [`business-case.md`](business-case.md) |
| The submission text, ready to paste | [`assemblyai-submission.md`](assemblyai-submission.md) |
| The pitch deck, slide by slide | [`assemblyai-deck.md`](assemblyai-deck.md) |
| The five-stage plan this entry followed | [`assemblyai-roadmap.md`](assemblyai-roadmap.md) |
| The plan for the last three days | [`win-plan.md`](win-plan.md) |

## Kept as a record

These describe the First Commit entry or earlier planning. They are **not
maintained**, and their counts are historical.

| What it is | File |
|---|---|
| The First Commit submission and demo script | [`submission.md`](submission.md), [`demo-script.md`](demo-script.md) |
| The story behind the design, published on AWS Builder Center | [`blog.md`](blog.md) |
| Where that build stood, and what was proven | [`STATUS.md`](STATUS.md), [`TASKS.md`](TASKS.md), [`FEATURES.md`](FEATURES.md) |
| Open decisions at the time | [`QUESTIONS.md`](QUESTIONS.md) |
| The original plans | [`PLAN.md`](PLAN.md), [`PLAN-v2.md`](PLAN-v2.md) |
