# Learnings log

Dated, specific, in the order they happened. The "Learning" criterion and the last fifteen seconds of the video read from here. Add an entry whenever something broke, surprised you, or a mentor said something worth keeping.

## Fri 18 Sep

- **20:30 — `cordon` pulls the full CUDA build of torch.** A plain `pip install -e .` tried to download ~2 GB of `cuda-bindings`. The Dockerfile already knew this (CPU torch first, then `cordon --no-deps`); the dev install and CI now do the same.
- **21:10 — The upstream RDS engine version (16.4) was already deprecated for new instances.** cfn-lint caught it; the demo template now takes `PostgresVersion` as a parameter and the runbook checks availability with `describe-db-engine-versions` before deploying.
- **21:40 — Lambda container images on `:latest` never redeploy.** CloudFormation sees the same URI string and does nothing. Images are tagged with the short SHA of the last commit that touched `src/beacon`, the Dockerfiles or `pyproject.toml`, and every deploy target refuses a stale tag. Docs-only commits do not invalidate a built image.
- **22:05 — `cloudtrail:LookupEvents` lags minutes.** The "what changed" card would have been empty on a live take. Replaced with an EventBridge rule on `AWS API Call via CloudTrail` writing to DynamoDB; the card re-reads the ledger on every poll. Needs a trail to exist in the region.
- **22:30 — EC2 `DryRun=True` only tells the truth to the caller that will execute.** A read-only agent role always gets `UnauthorizedOperation`. So the proposal-time dry run runs inside the remediator Lambda, and the UI shows which role answered.
- **22:45 — `AuthorizeSecurityGroupIngress` is authorised against two resources.** The security group (taggable) and the rule being created (which cannot carry a tag yet). A tag-scoped policy needs a second statement on `security-group-rule/*` or every call fails naming an ARN you never wrote. `tests/test_template_safety.py` now fails if either statement disappears.
- **23:20 — Hindi words collide with number-word normalisation.** "do" (give) was being rewritten to "2", which broke the Hinglish grant phrase `saat din ke liye contract do`. The normaliser only maps English number words now.
- **23:50 — Consent belongs in a context variable, not a tool argument.** `approve_fix(fix_id, confirmation_phrase)` looked right and was wrong: the model writes the argument. The tool now reads the raw transcript of the current turn from a `contextvars` `TurnContext` and ignores the argument for the decision.

## Sat 19 Sep

- **00:15 — Running the whole product against moto found two bugs 200 unit tests had not.** When the remediation loop finishes within a single voice turn (inline mode), both `voice_turn` and the triage contract branch overwrote the incident's `resolved` status with the stale one they had read earlier. Both now re-read before persisting. `make local` stays in the gate.
- **00:40 — A CloudWatch metric filter emits nothing when nothing matches.** `verify_metric` was judging the latest datapoint in five minutes, which after a real fix is still the last error minute. It now judges only the most recent two periods: silence there means recovery. (The demo filter has `DefaultValue: 0`, which would have hidden the bug on the real stack.)
- **00:55 — IAM's "Invalid principal" race.** A trust policy naming a role created seconds earlier can fail on first deploy. The mic role now trusts the account root with an `aws:PrincipalArn` condition on the voice role: equivalent, and never races.
- **01:10 — Powertools EMF emits metric values as arrays.** `{"TurnLatencyMs": [812.0]}` is valid EMF; the first test asserted a scalar and was wrong.

- **02:10 — `!Ref` on a DynamoDB table is its name, not its ARN.** One such line inside an IAM `Resource` list would have failed the remediation stack on first create with a message about ARN format; cfn-lint did not flag it. A template test now asserts every IAM resource is an ARN or `*`.
- **02:20 — A second allowlisted action needs its own data allowlist.** The golden snapshot protects security-group restores; for `ecs.force_redeploy` the equivalent is `REMEDIABLE_ECS_SERVICES` (filled from the demo stack outputs). A model proposal that names any other service is dropped before it can be proposed, not just refused at dry-run.

## Sat 19 Sep — workshop (fill in at 11:00–14:00)

- Nova 2 Lite tool use through Strands: recommended temperature / quirks →
- Transcribe streaming `en-IN` / `hi-IN` support and quality →
- CloudTrail → EventBridge delivery latency (measured: ___ s) →
- AgentCore Memory in a day from a Lambda-hosted Strands agent: yes/no, link →
- Mentor quote worth the video →

## Sun 20 Sep — recording

-
