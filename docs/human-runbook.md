# Beacon Night Shift — human runbook

Everything the human (Prashant) runs, in order, with expected output. The agent (Claude Code) cannot call AWS; you do every deploy from this same working tree (`/home/prashant/projects/Firstcommit/beacon`). No `git pull` is needed: the agent commits into this tree.

Plan of record: `docs/PLAN.md`. Open questions the agent could not answer: `docs/QUESTIONS.md`.

---

## §0 — Account prep (all console/CLI, no code). Everything the sandbox could build is committed; start here whenever you sit down.

Do these in this order. Budget: ~45 min of clicking plus the two long-running builds in step 8 (which run unattended).

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

8. First confirm the RDS engine version the template defaults to is still creatable (AWS deprecates minors; the upstream's 16.4 already was):
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
   Expected B: the last lines print `IMAGE_URI=<acct>.dkr.ecr.us-east-1.amazonaws.com/beacon:<tag>` and the value is written to `.beacon.env`. (The tag is the short SHA of the last commit that touched `src/beacon`/Dockerfiles/pyproject; docs commits do not change it.)

   If `make deploy-demo` fails with the ECS task in `STOPPED` and reason `exec format error`, tell the agent: the `CpuArchitecture` parameter did not take. Do not retry blindly.

---

## §1 — Baseline deploy (after §0 step 8 finishes)

```bash
make deploy EMAIL=<your email> LOG_GROUP_PATTERNS=/ecs/beacon-demo ENABLE_ALARM=true ALARM_NAME_PREFIX=beacon-demo TOKEN_BUDGET=6000 REGION=us-east-1
```
`IMAGE_URI` is read from `.beacon.env` (written by `make setup-image`). The target refuses to run if the image tag is `:latest` or does not match the current source tag; if it refuses, run `make setup-image` again (only the code layer is pushed, ~3-5 min).

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
(`check-reduction` greps the triage Lambda log for the "Cordon reduced" line.)

### §1.2 Strands smoke (the Nova tool-use gate)
```bash
make smoke-strands
```
Expected: one tool call printed, then a final answer, both from `us.amazon.nova-2-lite-v1:0`. If it errors twice, tell the agent: it switches the voice engine to the litellm fallback (`VOICE_ENGINE=litellm`).

### §1.3 Before you sleep (checklist)
- [ ] `beacon` and `beacon-demo-infra` stacks are `*_COMPLETE`
- [ ] one real RCA email received, `make fix-demo` run, alarm back to `OK`
- [ ] `tests/fixtures/real/` has the captured run
- [ ] `docs/QUESTIONS.md` answered
- [ ] §2 (remediation + console stacks) done if there was time; otherwise it is the first thing Saturday 08:00

---

## §2 — Remediation + console stacks (Friday late, or first thing Saturday)

Everything below is idempotent. Run from the repo root. `REGION`, `EMAIL`, `LOG_GROUP_PATTERNS`, `IMAGE_URI` are remembered in `.beacon.env` after the first run, so later commands only need what is shown.

```bash
# 1. Build BOTH images at the current source tag (if §0 already built the triage image at this tag, setup-image is a fast no-op push)
make setup-image REGION=us-east-1          # -> IMAGE_URI in .beacon.env
make setup-agent-image REGION=us-east-1    # -> AGENT_IMAGE_URI in .beacon.env   (run in a 2nd terminal, in parallel)

# 2. Remediation stack FIRST (it creates the incidents table the base Lambda writes to)
make deploy-remediation REGION=us-east-1
#    Expected last line: "Done. Next: make snapshot-sg && make tag-remediable && make dry-run"

# 3. Base stack update: new triage image, incident storage on (dashboard link is added in step 7)
make deploy INCIDENTS_ENABLED=true TOKEN_BUDGET=6000 REGION=us-east-1
#    (EMAIL / LOG_GROUP_PATTERNS / ENABLE_ALARM / ALARM_NAME_PREFIX come from .beacon.env; refused if the image tag is stale)

# 4. Golden snapshot of the demo security groups (STACK MUST BE HEALTHY: run make fix-demo first if you broke it)
make fix-demo REGION=us-east-1
make snapshot-sg REGION=us-east-1
#    Expected: "Wrote golden snapshot to /beacon/beacon/golden-sg: N rule(s) across 2 group(s)" and the JSON

# 5. Tag the demo resources for the remediator's IAM condition (no-op if the demo template already tagged them)
make tag-remediable REGION=us-east-1

# 6. THE FRIDAY GATE: dry-run under the remediator role
make dry-run REGION=us-east-1
#    Expected: JSON with "ok": true and "code": "DryRunOperation" (or "InvalidPermission.Duplicate"), then "DRY RUN PASSED".
#    If "code": "UnauthorizedOperation" -> paste the whole output to the agent (IAM statement fix); do not continue to 8.

# 7. Console stack (stub page tonight; CloudFront creation takes 5-10 min, let it run)
make set-passcode PASSCODE=<choose-a-word>       # remembered in .beacon.env
make deploy-console REGION=us-east-1
#    Expected: "Console: https://dXXXX.cloudfront.net" (opens to the placeholder page), Voice API + Dashboard URLs.
#    Then, so SNS emails link to the console:
make deploy REGION=us-east-1                     # DASHBOARD_URL is read from .beacon.env

# 8. Measure CloudTrail -> EventBridge latency (the number goes into the writeup)
make break-demo REGION=us-east-1; date; sleep 60; make fix-demo REGION=us-east-1
watch -n 20 'make changes REGION=us-east-1'      # until RevokeSecurityGroupIngress and AuthorizeSecurityGroupIngress rows appear; note the seconds
#    Write the measured delay into docs/QUESTIONS.md ("CloudTrail->ledger latency: NN s").
#    Since break-demo fired a real incident: confirm the email arrived, then `make incidents` shows it with status awaiting_engineer.

# 9. Health checks of the two Function URLs
curl -s "$(aws cloudformation describe-stacks --stack-name beacon-console --region us-east-1 --query 'Stacks[0].Outputs[?OutputKey==`VoiceTurnUrl`].OutputValue' --output text)health"
curl -s "$(aws cloudformation describe-stacks --stack-name beacon-console --region us-east-1 --query 'Stacks[0].Outputs[?OutputKey==`DashboardUrl`].OutputValue' --output text)health"
#    Expected: {"ok":true,"service":"beacon-voice-turn"} and {"ok":true,"service":"beacon-dashboard"}
```

Before sleeping: `make fix-demo`, confirm the alarm is back to OK, and answer `docs/QUESTIONS.md`.

**Before recording (Sunday):** `make preflight` must be all green. After the real runs: `make capture-run && make build-replay && make console-config` so the archived-run card on the live URL is a genuine incident. `make dashboard` once, for the CloudWatch shot.

## §3 — Saturday 08:00-10:30: prove the loop from the CLI (no UI needed)

Strict priority order. **At 09:45 stop wherever you are, paste the state into chat, pack.** All stacks exist since Friday, so every step is an update.

```bash
# 08:00  images at the current SHA (code layers only, ~5 min each; run in two terminals)
make setup-image REGION=us-east-1
make setup-agent-image REGION=us-east-1

# 08:10  stack updates (order matters: remediation first, then base, then console)
make deploy-remediation REGION=us-east-1
make deploy INCIDENTS_ENABLED=true REGION=us-east-1
make deploy-console REGION=us-east-1            # PASSCODE comes from .beacon.env (make set-passcode PASSCODE=... if not yet)

# 08:25  safety preconditions
make snapshot-sg REGION=us-east-1               # idempotent; stack must be healthy (make fix-demo first if not)
make dry-run REGION=us-east-1                   # must print DRY RUN PASSED
make warm REGION=us-east-1

# 08:30  THE LOOP, cycle 1 (voice-approved, but via CLI)
make demo-reset REGION=us-east-1                # healthy baseline, alarm OK, 200 lines flowing
make break-demo REGION=us-east-1                # revoke tcp/5432 (CloudTrail records it)
watch -n 15 'make incidents REGION=us-east-1'   # a new row with status awaiting_engineer appears ~40-60 s after the REAL alarm (2-3 min)
make propose REGION=us-east-1                   # dry_run.ok=true, role = beacon-remediator, blast_radius "1 ingress rule on 1 security group"
make approve REGION=us-east-1 FIX=1             # transcript "approve fix 1" -> execution_arn
#     open Step Functions console -> beacon-remediate-beacon -> the execution: DryRun -> RequireApproval -> Execute -> Wait30 -> Verify -> Resolve
#     EC2 console -> the RDS security group -> Inbound rules: 5432 from the ECS SG is back
#     CloudWatch -> alarm beacon-demo-infra-errors: back to OK (1-2 evaluation periods after the fix)
make incidents REGION=us-east-1                 # status=resolved
make replay-approval REGION=us-east-1 APPROVAL=<approval_id from the approve output>   # idempotent_replay: true, nothing re-executed

# 09:15  (if time) cycle 2: a Sleep Contract via CLI, then a real re-break handled without a page
INC=$(make -s latest-incident REGION=us-east-1)
PYTHON=.venv/bin/python bash scripts/voice_tool.sh beacon us-east-1 "$INC" grant_sleep_contract '{"days":7,"max_uses":3}' "yes" "$(grep ^PASSCODE= .beacon.env | cut -d= -f2)"        # read-back
PYTHON=.venv/bin/python bash scripts/voice_tool.sh beacon us-east-1 "$INC" grant_sleep_contract '{"days":7,"max_uses":3}' "grant contract for seven days" "$(grep ^PASSCODE= .beacon.env | cut -d= -f2)"   # granted: true
make demo-sleep REGION=us-east-1                # real re-break; wait for the real alarm
watch -n 15 'make incidents REGION=us-east-1'   # new row: status auto_remediating -> resolved, and the email subject says "(not woken)"
```

Local mic smoke before touching CloudFront (Saturday 14:00, per the plan): `make local LOCAL_PORT=8765` (port 8000 is taken on this machine), open `http://localhost:8765/?stt=webspeech`, passcode `local`, hold the mic and speak. This proves the worklet + playback path on the laptop; the Transcribe path itself needs the live URL (it needs the STS creds from `/session`).

If Transcribe answers `BadRequestException ... language` for `en-IN`, redeploy with `make deploy-console STT_LANGUAGE=en-US` (the console reads it from `/config.json`, no rebuild).

If anything fails: paste the verbatim output. Most likely culprits, in order: `UnauthorizedOperation` in `make dry-run` (IAM statement), Step Functions execution `FAILED` at `RequireApproval` (approval table name env), Verify escalating with `ErrorCount` still > 0 (the app needs ~60 s of 200s after the rule returns; a retake with `make demo-reset` fixes it).

**One-line fallback if Step Functions is the problem at 09:45:** the voice Lambda can run the loop inline through the remediate Lambda (`{step: "all"}`) with no template change; tell the agent and it flips `REMEDIATION_MODE=inline`.

---

## Handoff protocol (any failure, any time)

Paste **verbatim** into chat:
```bash
aws cloudformation describe-stack-events --stack-name <stack> --max-items 25 --region us-east-1 \
  --query 'StackEvents[?ResourceStatus==`CREATE_FAILED` || ResourceStatus==`UPDATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' --output table
aws logs tail /aws/lambda/<function> --since 15m --region us-east-1
```
plus the browser console / network tab error for UI problems. The agent never asks you to click around consoles except where this runbook says so.
