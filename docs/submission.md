# Submission writeup — Beacon Night Shift

*Paste into the First Commit form. Replace the bracketed links.*

**Live URL:** http://beacon-console-283146810291-us-east-1.s3-website-us-east-1.amazonaws.com (passcode for judges: `nightshift`) · **Repo:** https://github.com/Prashant-thakur77/Beacon · **Video:** [YouTube URL — also attached to the v0.2.0 release] · **Blog:** https://builder.aws.com/post/3Jb5v7ouXDReILJ7WMuHrlB1leL_p/why-transcript-is-the-safety-artifactvoice-approved-aws-remediation-with-strands-and-step-functions

> Honest note for judges (20 Sep): the AWS account is eight days old and still under AWS's new-account verification, which blocks Bedrock model access and CloudFront creation regardless of code. Everything else is deployed and exercised on the account: the demo workload, the real alarm firing, the triage Lambda running to the model call, the remediator role dry run passing, the Function URLs and the console. The `make local` mode (`?night=1`) shows the complete loop with the model scripted; the film uses it for the demo section and shows the real account's CLI output for the deployment.

## The problem

Every on-call engineer knows the 3 AM page: alone, half-asleep, phone in hand, no laptop. Four questions, in order — *is it real, what changed, what do I do, can I go back to sleep* — and the tools answer at most the first one. For solo engineers and small teams (in India often on-call for US traffic, so 3 AM IST is their customers' peak) the worst part is that the most common page is a repeat of something already fixed once. Nothing learns from the fix.

## What Beacon does

Beacon turns the page into a ninety-second conversation in the browser that ends with the alarm back to OK — and then removes the repeat page entirely.

1. **Triage on Bedrock.** A CloudWatch alarm invokes the triage Lambda. Cordon reduces the logs to their anomalous sections with Nova 2 Multimodal Embeddings; Nova 2 Lite writes the root cause. Two deterministic sources are fed in ahead of the logs: a **security-group drift check** against a golden snapshot (exact resource ids, no guessing), and a **CloudTrail change ledger** fed by EventBridge (what changed, by whom, how long before the first error).
2. **Talk to it.** The Night Board (S3 + CloudFront) shows the incident; you press the mic. Amazon Transcribe streams your speech with 15-minute STS credentials scoped to one action. A Strands agent on Nova 2 Lite answers with six tools, and every sentence it speaks is pinned to an evidence card (`[E2]`). Polly speaks the reply; sentence speech marks light the transcript in sync.
3. **Fix it, on your word.** "Can you fix it?" proposes the one allowlisted fix, dry-run under the write-only remediator role, with the blast radius read aloud. "Approve fix one" is checked against your *raw transcript*, not the model's claim. A Step Functions loop then dry-runs again, requires the approval record, executes exactly once, and refuses to say *recovered* until three checks pass: the alarm is OK *after* the fix, the error metric is zero, and the rule is present. Anything else pages a human.
4. **Sleep Contracts.** After a verified fix, Beacon asks whether to handle the same alarm itself next time. You say yes; it reads back the scope (alarm, action, exact resources, days, uses); you say *"grant contract for seven days."* That standing approval — with your own words as the record — lets Beacon run the same loop the next time, and send a *"you were not woken"* email in the morning. Contracts are resource-scoped, counted, and expire.

The tally on the board is the outcome: incidents resolved, median minutes to recovery, humans woken.

## How it is built

- **Five Lambdas** from two container images (a torch image for triage, a slim one for the rest), **three CloudFormation stacks**, one `make` target each; images tagged with the git SHA and deploys refuse a stale tag.
- **AWS open source:** Strands Agents SDK runs the voice agent; Powertools for AWS Lambda handles the Function URL routing and tracing.
- **The safety model is code and IAM, not prompt text**, and `tests/test_template_safety.py` parses the real templates to prove the two-role split (read-only agent, write-only executor scoped by `aws:ResourceTag`, plus the untaggable `security-group-rule/*` statement).
- **244 tests** (moto for AWS, a fake agent for the model) and a `make local` mode that runs the entire product against in-process moto with no AWS account — the Build It path — which found two real bugs the unit tests had not. `http://localhost:8000/?night=1` plays a whole night unattended: fix, approve, verify, grant a contract, then the same fault handled with nobody woken.
- **Production grade, not a demo shell:** fail-closed passcode, bounded AWS clients, idempotent execute even on failure, named JSON log groups, Lambda error and escalation alarms to SNS, reserved concurrency, point-in-time recovery, CloudFront security headers with a CSP, origin-scoped CORS, the demo database password in Secrets Manager, pinned image dependencies. Each claim has a test in `tests/test_template_ops.py`; the table is in the README.
- Full architecture, hour plan and risk register: `docs/PLAN.md`.

## Where AWS fits (and where it is visible in the video)

| Service | Role | Video |
|---|---|---|
| Bedrock: Nova 2 Lite | RCA + the voice agent's reasoning | 0:30 model id in the Lambda log; every reply |
| Bedrock: Nova 2 Multimodal Embeddings | log reduction via Cordon | 0:30 "reduced to top NN%" |
| Strands Agents SDK (OSS) | tool-calling agent | 0:50 tool chips |
| Powertools for AWS Lambda (OSS) | Function URL resolver, tracing | 2:15 |
| Step Functions | the verified loop | 1:30 graph |
| CloudTrail + EventBridge | change ledger | 0:50 change card |
| Transcribe streaming | browser STT | 0:50 live transcript |
| Polly | Beacon's voice + speech marks | throughout |
| Lambda, DynamoDB, SNS, S3 + CloudFront, CloudWatch, IAM, EC2/ECS/RDS | the rest of the system and the patient | 0:30, 1:30, 1:50, 2:15 |

## Safety and control

Allowlist of two actions · golden-snapshot data allowlist · dry run under the executing role · consent from the transcript with an exact phrase · read-back before a standing grant · approval as a record, used once · two IAM roles in one direction · three-part verification or escalate · resource-scoped expiring contracts · one kill switch across all write paths · passcode, turn cap and redaction on the public URL. Details and the test for each: `docs/safety.md`.

## What I learned

- Nova 2 Lite tool use through Strands works well at temperature 0.3 with tight tool descriptions; the model over-calls tools if the descriptions overlap, so each tool got one sentence saying when *not* to call it.
- `cloudtrail:LookupEvents` lags minutes; an EventBridge rule on *AWS API Call via CloudTrail* into DynamoDB is the right design, and it needs a trail to exist.
- EC2 `DryRun=True` is the cheapest safety primitive on AWS — and it only tells the truth when the caller is the role that will execute.
- `aws:ResourceTag` scopes "the agent can change security groups" down to "these two resources", but `AuthorizeSecurityGroupIngress` is also authorised against the not-yet-tagged `security-group-rule`, so the policy needs two statements.
- Verification is a loop, not a boolean: alarms take an evaluation period to clear, and "OK" only counts if it happened after the fix.
- A standing approval deserves a stronger gate than a one-shot approval: read the scope back, then require the phrase.
- Lambda container images on `:latest` do not redeploy; git-SHA tags with a deploy guard do. Copy source last so rebuilds push kilobytes.
- Transcribe streaming from a browser works with 15-minute STS credentials scoped to one action — no identity pool needed — and Vite needs Node polyfills for the SDK's event stream.
- Running the whole product against moto (`make local`) found two status-race bugs that 200 unit tests had not.
- Every EMF dimension you add creates a metric set nothing else reads: the remediate Lambda's `{service, action}` metrics were invisible to a dashboard querying `{service}`. One dimension, metadata for the rest.
- CORS belongs in exactly one place: a Function URL CORS config plus a Powertools `CORSConfig` produce duplicated headers that browsers reject.

## Roadmap

AssemblyAI Voice Agent API as a second voice backend behind the same six tools (barge-in that cancels a pending fix, confidence-gated approval, Hinglish); Bedrock AgentCore Memory for contract history; Nova 2 Sonic full-duplex; the inherited Amazon Connect phone channel.
