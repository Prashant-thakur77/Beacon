# Demo video script (3:00) and staging

Hard gate from the judges: *AWS must be visible in the video.* Every segment below names the console shot that proves it. Record at 1080p with system audio (Polly) and a headset mic; captions on every spoken line so it works muted; the real on-screen clock throughout, no edited timestamps.

## Before recording (10 min)

```bash
make warm                   # voice, remediate, dashboard Lambdas
make demo-reset             # fix-demo + alarm OK + wait for 200s in the app log
make apply-on               # in case a rehearsal left it off
```
Open and arrange tabs: the console (CloudFront URL, passcode entered), CloudWatch → Alarms, EC2 → the RDS security group → Inbound rules, Step Functions → `beacon-remediate-beacon`, DynamoDB → `beacon-approvals-beacon` and `beacon-contracts-beacon`, CloudTrail → Event history, IAM → `beacon-remediator-beacon` policy JSON, CloudWatch → Logs Insights, a terminal at the repo root.

Retakes: `make demo-reset` between takes. If waiting for the real alarm blows the budget, `make demo-alarm` forces the transition (say so in the caption; the recorded take should use the real alarm).

## Shots

| Time | On screen | Voiceover | AWS visible |
|---|---|---|---|
| 0:00–0:12 | Phone on a bedside table lights up: the SNS email *"Beacon - Alarm: beacon-demo-infra-errors"*. Title card **Beacon Night Shift**. | "Three AM. Payments are failing. You are alone, half-asleep, no laptop. You need four answers: is it real, what changed, what do I do, can I go back to sleep. Beacon answers all four — on AWS." | SNS email |
| 0:12–0:30 | Architecture overlay (README diagram, animated): ECS/RDS → CloudWatch → Lambda (Nova 2 Lite, Nova Embeddings) + CloudTrail ledger → DynamoDB → console (Transcribe, Strands, Polly) → Step Functions. | "CloudWatch detects. Bedrock Nova reasons. CloudTrail says what changed. A Strands agent talks to you from the browser. And Step Functions carries out the fix — and proves it worked." | architecture with named services |
| 0:30–0:50 | Terminal: `make break-demo`. Split: `aws logs tail /ecs/beacon-demo --follow` going red (503, `db_pool=EXHAUSTED`, CRITICAL). CloudWatch alarm tile flips to **In alarm** (clock visible, time-cut). Lambda log line: `Cordon reduced /ecs/beacon-demo to top NN%` and `us.amazon.nova-2-lite-v1:0`. | "I cut the database security-group rule on a real Fargate app. Errors climb, the alarm fires, and Beacon triages on Bedrock: Cordon with Nova embeddings keeps the anomalous lines, Nova 2 Lite writes the root cause." | CloudWatch Logs, Alarm, Lambda, Bedrock model id |
| 0:50–1:30 | Night Board: incident card slides in, timeline fills — *CloudTrail changes checked: RevokeSecurityGroupIngress by user/prashant*. Talk opens; Beacon speaks (sentences highlight, E1 E2 chips pulse). Mic: **"what changed?"** → live Transcribe partials → reply cites [E2] [E3]. **"can you fix it?"** → Fix card: `sg.restore_ingress`, blast radius *1 ingress rule on 1 security group*, **dry run PASSED · via the remediator role**, amber *awaiting your word*. | "Beacon doesn't just name the symptom. It correlates the alarm with the CloudTrail change that preceded it, and proves the cause against a golden snapshot of the security group. It proposes one allowlisted fix, dry-runs it under the same locked-down role that will execute it, reads me the blast radius — and waits for my word." | CloudFront URL bar, Transcribe transcript, CloudTrail card, EC2 DryRun |
| 1:30–1:50 | Mic: **"approve fix one."** Tool chip `approve_fix` lights; timeline: *Approved by voice · "approve fix one"*. Cut: Step Functions graph DryRun → RequireApproval → Execute → Wait30 → Verify turning green. EC2 inbound rules refreshed: 5432 is back. Alarm tile **OK**. Fix card: *✓ Verified recovery 3/3 checks*. Beacon: "Recovered. The alarm is back to OK." | "The approval is checked against my actual transcript, not the model's claim. Then a Step Functions loop applies the fix — and refuses to say 'recovered' until CloudWatch agrees: the alarm cleared *after* the fix, the error count is zero, and the rule is really there." | Step Functions graph, EC2 console, Alarm OK |
| 1:50–2:15 | Beacon: "Should I handle this myself next time?" Mic: **"yes."** Beacon reads back the scope. Mic: **"grant contract for seven days."** Contracts tab: card with alarm, action, exact resource ids, *7d left · 3 of 3 uses*, the quote. Terminal: `make demo-sleep`. Night Board: second incident → *☾ handled while you slept*. Step Functions execution with `source: contract`. Email: *"Beacon (not woken) - …"*. | "Then the part nobody ships. I grant a Sleep Contract by voice — scoped to this alarm, this fix, this security group — after Beacon reads the terms back. When the same thing breaks again, Beacon fixes it under that contract, verifies it, and emails me in the morning instead of paging me." | DynamoDB contract item, Step Functions, SNS email |
| 2:15–2:42 | Safety montage, ~4 s each: `registry.py` (two actions); IAM policy JSON with `aws:ResourceTag/beacon:remediable` and the separate `security-group-rule` statement; DynamoDB approval item with `transcript_quote`; `make replay-approval` output `idempotent_replay: true`; the `/safety` page with the per-function kill switch; `tests/test_template_safety.py` green. | "Control is the point. Two allowlisted actions. A write-only role scoped by tag. Dry run first, under that role. One approval executes exactly once. Every approval carries my own words. Contracts are scoped to resources and expire on their own. One switch stops every write path. And if verification fails, a human is paged." | IAM, DynamoDB, terminal, console |
| 2:42–3:00 | Tally: **2 incidents · 2.2 min median recovery · 0 humans woken** on the second. Cards: repo URL, live URL, services list. Three "what I learned" bullets. | "Built solo in a weekend on AWS. Beacon: fewer pages, faster recovery, and your sleep back. Link in the description." | services list, live URL |

## Exact phrases (say them exactly)

- "what changed?"
- "can you fix it?"
- "approve fix one"
- "yes" → (read-back) → "grant contract for seven days"

## If something misbehaves during the take

| Symptom | Do |
|---|---|
| Transcribe partials do not appear | switch the STT selector to *Browser speech*; if that fails, type the phrase (caption: "typed") — Polly still speaks |
| Verify keeps failing after the rule is back | the app needs ~60 s of 200s; wait one more attempt; if it escalates, `make demo-reset` and retake |
| Alarm will not re-enter ALARM on the second cycle | `make demo-alarm` (caption it) |
| Cold-start pause on the first turn | `make warm` before the take; keep-warm runs every 4 min anyway |
