# Beacon Night Shift — human runbook

Everything the human (Prashant) runs, in order, with expected output. The agent (Claude Code) cannot call AWS; you do every deploy from this same working tree (`/home/prashant/projects/Firstcommit/beacon`). No `git pull` is needed: the agent commits into this tree.

Plan of record: `docs/PLAN.md`. Open questions the agent could not answer: `docs/QUESTIONS.md`.

---

## §0 — Tonight, right now (Fri 18 Sep, ~22:00 IST). Account prep, all console/CLI, no code needed

Do these in this order. Steps 1-7 need nothing from the agent.

1. **Install AWS CLI v2** (not on this machine yet):
   ```bash
   cd /tmp && curl -sS "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip && unzip -q awscliv2.zip && sudo ./aws/install && aws --version
   ```
   Expected: `aws-cli/2.x.x ...`

2. **Configure credentials** for the hackathon account (an IAM user/role with admin for the weekend is fine; region **us-east-1**, every Nova model we use is there):
   ```bash
   aws configure            # access key, secret, region us-east-1, output json
   aws sts get-caller-identity
   ```
   Expected: JSON with your `Account` id. Paste the account id into `docs/QUESTIONS.md` (the agent needs it only for docs; templates use `${AWS::AccountId}`).

3. **Bedrock model access** (console → Amazon Bedrock → Model access → Modify): enable **Amazon Nova 2 Lite** and **Amazon Nova 2 Multimodal Embeddings**. While there, note whether **Nova 2 Sonic** and **Nova Micro** are listed (for the Saturday workshop questions). Expected: "Access granted" on both.

4. **CloudTrail trail** (console → CloudTrail → Create trail): name `beacon-trail`, management events (read + write), this region only is fine, new S3 bucket. Then:
   ```bash
   aws cloudtrail get-trail-status --name beacon-trail --query IsLogging
   ```
   Expected: `true`. (The change-ledger Lambda only receives EventBridge `AWS API Call via CloudTrail` events if a trail exists.)

5. **Billing alarm**: console → Billing → Budgets or CloudWatch billing alarm, threshold **$20**, to your email. Expected: subscription confirmation email received.

6. **GitHub**: create an empty **public** repo `beacon` under your account. Do not push yet (the agent is committing locally; you push on Sunday, or earlier if you want a remote backup: `git remote add origin <url> && git push -u origin main --tags`).

7. **Tell the agent**: your account id, `uname -m` (this machine is x86_64), and whether model access is granted. Put them in `docs/QUESTIONS.md` under "Answers".

8. **Wait for the agent's "A0 committed" message** (Makefile + demo template fixes: git-SHA image tags, x86/ARM handling, tag guard). First confirm the RDS engine version the template defaults to is still creatable (AWS deprecates minors; the upstream's 16.4 already was):
   ```bash
   aws rds describe-db-engine-versions --engine postgres --engine-version 16.10 --region us-east-1 --query 'DBEngineVersions[0].EngineVersion' --output text
   ```
   Expected: `16.10`. If it prints `None`, list what exists (`aws rds describe-db-engine-versions --engine postgres --query 'DBEngineVersions[].EngineVersion' --output text`) and pass one: `make deploy-demo PG_VERSION=16.9`.

   Then run, in two terminals in parallel:
   ```bash
   # terminal A — demo workload (VPC + RDS + Fargate), ~10-12 min, mostly RDS
   make deploy-demo REGION=us-east-1
   # terminal B — triage image (torch, multi-GB first push, 20-40 min on home broadband)
   make setup-image REGION=us-east-1
   ```
   Expected A: `Demo infrastructure deployed.` and `aws logs tail /ecs/beacon-demo --since 2m --region us-east-1` shows `200` lines within ~3 min of the service starting.
   Expected B: the last lines print `IMAGE_URI=<acct>.dkr.ecr.us-east-1.amazonaws.com/beacon:<git-sha>` and the value is written to `.beacon.env`.

   If `make deploy-demo` fails with the ECS task in `STOPPED` and reason `exec format error`, tell the agent: the `CpuArchitecture` parameter did not take. Do not retry blindly.

---

## §1 — Baseline deploy (tonight, after §0 step 8 finishes)

```bash
make deploy EMAIL=<your email> LOG_GROUP_PATTERNS=/ecs/beacon-demo ENABLE_ALARM=true ALARM_NAME_PREFIX=beacon-demo TOKEN_BUDGET=6000 REGION=us-east-1
```
`IMAGE_URI` is read from `.beacon.env` (written by `make setup-image`). The target refuses to run if the image tag is `:latest` or does not match the current git HEAD; if it refuses, run `make setup-image` again (only the code layer is pushed, ~3-5 min).

Expected: `Done. Check your email to confirm the SNS subscription.` → click **Confirm subscription** in the email from AWS Notifications. Then:
```bash
aws cloudformation describe-stacks --stack-name beacon --query 'Stacks[0].StackStatus'   # CREATE_COMPLETE / UPDATE_COMPLETE
```

### §1.1 First real incident (proves the inherited pipeline on your account)
```bash
make break-demo REGION=us-east-1
# wait 2-3 min; watch the alarm
watch -n 15 'aws cloudwatch describe-alarms --alarm-names beacon-demo-infra-errors --query "MetricAlarms[0].StateValue" --output text'
```
Expected: `OK` → `ALARM`. Within ~60 s of ALARM you get an email `Beacon - Alarm: beacon-demo-infra-errors` with `STATUS: Critical|High` (not Healthy).

Then:
```bash
make check-reduction REGION=us-east-1     # prints the "[/ecs/beacon-demo] (reduced to top NN%)" line from the Lambda log — if EMPTY, tell the agent (TOKEN_BUDGET goes to 3000)
make capture-run REGION=us-east-1         # saves the Lambda log, 300 demo log lines and the RCA email text into tests/fixtures/real/
make fix-demo REGION=us-east-1
```
(`check-reduction` and `capture-run` arrive with the agent's A1 commit; if they do not exist yet, run `aws logs tail /aws/lambda/beacon-beacon --since 10m --region us-east-1 | grep -i "reduced to top"` by hand.)

### §1.2 Strands smoke (after the agent's A1 commit, ~22:45-23:00)
```bash
make smoke-strands
```
Expected: one tool call printed, then a final answer, both from `us.amazon.nova-2-lite-v1:0`. If it errors twice, tell the agent: it switches the voice engine to the litellm fallback (`VOICE_ENGINE=litellm`).

### §1.3 Before you sleep (checklist)
- [ ] `beacon` and `beacon-demo-infra` stacks are `*_COMPLETE`
- [ ] one real RCA email received, `make fix-demo` run, alarm back to `OK`
- [ ] `tests/fixtures/real/` has the captured run
- [ ] `docs/QUESTIONS.md` answered
- [ ] §2 (Friday 23:00 deploys of the remediation + console stubs) done if the agent's commit landed; otherwise it is the first thing Saturday 08:00

---

## §2 — Friday late / Saturday 08:00: remediation + console stacks

Written by the agent when the templates are committed (see `docs/PLAN.md` §6, rows H3 and H4). Check back here.

---

## Handoff protocol (any failure, any time)

Paste **verbatim** into chat:
```bash
aws cloudformation describe-stack-events --stack-name <stack> --max-items 25 --region us-east-1 \
  --query 'StackEvents[?ResourceStatus==`CREATE_FAILED` || ResourceStatus==`UPDATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' --output table
aws logs tail /aws/lambda/<function> --since 15m --region us-east-1
```
plus the browser console / network tab error for UI problems. The agent never asks you to click around consoles except where this runbook says so.
