# Voice-approved remediation on AWS: Strands, Step Functions, EC2 DryRun, and why the transcript is a safety artifact

*Draft for AWS Builder Center. ~900 words. Screenshots to add: the Night Board with a fix awaiting approval; the Step Functions graph mid-loop; the IAM policy with both security-group statements; the Contracts card with the quoted grant.*

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

## What I would tell you to steal

- Put consent in a context variable the tools read, not in tool arguments the model writes.
- Verify against the alarm that paged, with a timestamp comparison, or you are verifying nothing.
- Tag-scoped IAM for agents is great; remember the resources that cannot be tagged.
- Run the whole thing against moto before you deploy. `make local` found two status races my 200 unit tests had not.

The code, the plan, and the safety table with a test per control are in the repo. Build for the fourth question.
