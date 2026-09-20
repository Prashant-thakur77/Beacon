# Voice-approved remediation on AWS: Strands, Step Functions, EC2 DryRun, and why the transcript is a safety artifact

*For AWS Builder Center. Repo: https://github.com/Prashant-thakur77/Beacon · Demo film: https://github.com/Prashant-thakur77/Beacon/releases/tag/v0.2.0 · Live console: https://6die6lduac6ipxkeg73nxsuvpu0yzkim.lambda-url.us-east-1.on.aws/*

![The Night Board mid-incident](https://raw.githubusercontent.com/Prashant-thakur77/Beacon/main/docs/assets/night-board.png)

Last weekend I built Beacon Night Shift for the First Commit hackathon: an on-call agent you talk to in the browser at 3 AM, which proposes a fix, applies it when you say the word, proves the recovery, and — if you let it — handles the same outage next time without waking you. This post is about the four decisions that made it safe enough to give write access to.

## 1. "AI explains the outage" is not enough

Every AI ops demo ends at the root cause. But the on-call engineer's fourth question is *can I go back to sleep*, and the answer is only yes if something changed. So Beacon closes the loop: CloudWatch alarm → Nova 2 Lite root cause on Bedrock → one allowlisted fix → your spoken approval → a Step Functions loop that executes and verifies → a Sleep Contract so the repeat does not page you.

The trick is that closing the loop with an LLM in it is only acceptable if the LLM cannot be the thing that decides. Every control below is code or IAM.

## 2. Make the fix proposal evidence-bound

I did not want the model guessing security-group ids. Before Nova sees a single log line, two deterministic sources run: a **drift check** comparing the live security groups to a golden snapshot taken on a healthy stack (SSM parameter, written by `make snapshot-sg`), and a **change ledger** of CloudTrail write calls. The drift check yields exact `action_params`; the model's own suggestion is overridden with them.

For the ledger I first reached for `cloudtrail:LookupEvents` and learned it lags several minutes — the "what changed" card would have been empty on a live take. The right design is an EventBridge rule on `AWS API Call via CloudTrail` writing to DynamoDB (a trail must exist in the region). Destructive verbs rank first, and calls made by Beacon's own remediator role are tagged so the agent never blames itself.

## 3. The model cannot approve itself

The agent is a Strands `Agent` on `BedrockModel("us.amazon.nova-2-lite-v1:0")` with six plain-Python tools. `approve_fix(fix_id, confirmation_phrase)` looks like it takes the engineer's words as an argument — but the tool ignores that argument for the decision. It reads a `contextvars` `TurnContext` holding the raw transcript of the current turn (from Transcribe, or the typed box) and requires the literal `approve fix <n>`. The model can call the tool; only the human can make it succeed.

A standing approval (the Sleep Contract) gets a stronger gate: the first call returns a read-back — alarm, action, exact resources, days, uses — that the agent must speak; the grant only happens on a later turn whose transcript contains `grant contract for <n> days` (or the Hinglish `saat din ke liye contract do`). "Yes" alone never grants anything. That quote is stored on the contract and on every approval it later produces.

## 4. Step Functions as the honesty layer

The loop is a Standard state machine: `DryRun → RequireApproval → Execute → Wait 30s → Verify` (up to six times) → `Resolve` or `Escalate`. Two details matter.

`RequireApproval` reads a DynamoDB record, not a prompt. No record with a matching action and params hash, no execution. `Execute` consumes the record with a conditional write and stores its result, so a retry returns `idempotent_replay: true` instead of touching AWS twice.

`Verify` refuses to be a boolean. It needs the alarm `OK` **with `StateUpdatedTimestamp` after the execute time** (a forced `set-alarm-state` or a pre-existing OK cannot pass), the alarm's own metric at zero for the latest period, and the action's post-condition (the rule is present). Alarms take an evaluation period to clear, so the first attempt usually fails honestly; the timeline shows every attempt.

## 5. Least privilege for an agent, and the trap

Two roles, one direction. The agent you talk to has zero EC2/ECS write actions; it may invoke the remediate Lambda and start the state machine. The remediator role holds only `ec2:AuthorizeSecurityGroupIngress` and `ecs:UpdateService`, scoped by `aws:ResourceTag/beacon:remediable = "true"`.

Except that does not work as written. `AuthorizeSecurityGroupIngress` is authorised against *two* resources: the security group (taggable) and the rule being created (which does not exist yet, so it cannot carry a tag). The policy needs a second statement on `security-group-rule/*` with no tag condition, or every call fails with `UnauthorizedOperation` naming an ARN you never wrote. A test parses the CloudFormation and fails if either statement disappears.

The same trap applies to dry runs. EC2 `DryRun=True` is the cheapest safety primitive on AWS — it returns `DryRunOperation` if you *could* do it — but only truthfully for the caller that would execute. So the proposal-time dry run does not run in the agent's Lambda; it invokes the remediator Lambda synchronously and reports its role in the UI.

## 6. Hardening it in an afternoon: three traps I did not expect

Once the loop worked I spent one pass making it something I would run for real, and three things bit that were not in any tutorial.

**EMF dimensions are metric sets, not labels.** My remediate Lambda emitted metrics with dimensions `{service, action}`; my dashboard and my new "Escalated" alarm queried `{service}`. In CloudWatch those are different metrics, so the dashboard was empty. Keep one dimension set per service and put the rest (`action`, `incident_id`) in EMF metadata, which Logs Insights can still search.

**CORS in two places is CORS in zero places.** A Lambda Function URL with a CORS config *and* a Powertools resolver with `CORSConfig` both add `Access-Control-Allow-Origin`; the duplicated header fails the browser's check. The URL config is the right home (it answers preflights without invoking the function), scoped to the CloudFront origin.

**A per-container read cache needs an escape hatch for single-process test modes.** A two-second cache on the incident list is invisible in production, where one Lambda writes and another reads. In `make local`, where both live in one process, it made the board lag one poll behind a break and leaked state across test apps. Clear it on the local path; keep it on the real one.

The rest was boring and that is the point: fail-closed passcode, bounded boto3 timeouts, named log groups with retention, error alarms, reserved concurrency, PITR, a CSP, and the demo database password generated by RDS into Secrets Manager instead of a template default. Each one is a test against the CloudFormation, so it cannot quietly regress.

## 7. What a brand-new AWS account taught me on submission day

My hackathon account was eight days old. Deploying to it on the last day surfaced three things that no amount of local testing could: **new accounts sit under a verification hold** that makes CloudFront refuse to create distributions and Bedrock return `NOT_AUTHORIZED` even when the model catalog lists the models (the fix is a support case, not code); the **unreserved Lambda concurrency minimum of 10** rejects any `ReservedConcurrentExecutions` on a fresh account (now a template parameter, off by default); and since 2025 a public **Function URL needs `lambda:InvokeFunction` with `InvokedViaFunctionUrl` as well as `InvokeFunctionUrl`** — the console returned `Forbidden` until both permissions were there. The console template gained a `UseCloudFront=false` mode (S3 website hosting) so there is a live URL while the hold clears; the triage pipeline ran end to end on the real alarm and stopped exactly at the model call, which is the honest state of things at submission time.

## What I would tell you to steal

- Put consent in a context variable the tools read, not in tool arguments the model writes.
- Verify against the alarm that paged, with a timestamp comparison, or you are verifying nothing.
- Tag-scoped IAM for agents is great; remember the resources that cannot be tagged.
- Run the whole thing against moto before you deploy. `make local` found two status races my 200 unit tests had not.

The code, the plan, and the safety table with a test per control are in the repo. Build for the fourth question.
