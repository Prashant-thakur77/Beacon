# Beacon Night Shift — film script (full version, ~7 min; the 3-min cut is marked ★)

Style, learned from the reference (RiskWise): a cinematic problem section over real footage with
keyword overlays and quiet corporate music, then the product, then the demo, then the architecture,
then a close. Ours is darker: it happens at night. Palette = the console (near-black, white, one lilac
accent, amber for "needs you"). Music: a slow ambient bed, ducked under narration, swells at the turn.

Scene ids match `scenes/*.html` (motion graphics rendered in Chromium) or capture files.
SUPPLY = a shot only the human can record (AWS console, logged in). Everything else is generated.

| # | Scene | On screen | Narration (Chatterbox, one file per line) | ★ |
|---|---|---|---|---|
| 1 | cold open | Black. A phone screen lights up in the dark: "03:12 · CloudWatch ALARM · payments-errors". (scene: `phone`) | It is three in the morning. Payments are failing. And the only person awake is you. | ★ |
| 2 | problem, real | Real photos, slow push-in, keyword overlays: *alone*, *half-asleep*, *no laptop*. (Wikimedia Commons: night city, server room, engineer at desk at night) | Every on-call engineer knows this page. Alone, half-asleep, phone in hand. And you need four answers, in order. | ★ |
| 3 | four questions | Four lines type on, one by one: is it real · what changed · what do I do · can I go back to sleep. (scene: `questions`) | Is it real. What changed. What do I do. And can I go back to sleep. Today's tools answer, at most, the first one. | ★ |
| 4 | the repeat | A calendar of nights; the same alarm name lights up on five of them. (scene: `repeat`) | And the worst part: the most common page is a repeat. Something that was already fixed once. Nothing learns from the fix. | ★ |
| 5 | India angle | Map: US traffic peak vs 3 AM IST, a clock pair. (scene: `timezones`) | For solo engineers and small teams in India, on call for customers on the other side of the world, three A M is not an edge case. It is every night. | |
| 6 | title | Logo dot pulses; "Beacon Night Shift"; sub-line: the on-call agent that fixes the 3 AM page with your voice, and does not wake you the second time. (scene: `title`) | This is Beacon Night Shift. An on-call agent for AWS that answers all four questions, and then removes the repeat page entirely. | ★ |
| 7 | how it starts | Architecture, part one, animated packets: CloudWatch alarm → Lambda → Bedrock (Nova 2 Lite, Nova Embeddings) → DynamoDB → Night Board. (scene: `arch1`) | An alarm fires. A Lambda function reads the logs. Cordon, with Nova two embeddings, keeps only the anomalous sections. Nova two Lite writes the root cause. | ★ |
| 8 | evidence first | Two cards slide in: golden-snapshot drift check (exact security-group ids), CloudTrail change ledger (who changed what, how long before the errors). (scene: `evidence`) | But the model never guesses resource ids. Two deterministic sources run first. A drift check against a golden snapshot of the security groups. And a CloudTrail change ledger: what changed, by whom, how long before the first error. | ★ |
| 9 | demo: the board | CAPTURE `board`: the Night Board with the live incident, tally strip, incident card "awaiting your word". | Here is the Night Board. One incident, high severity, and it is waiting for a human. | ★ |
| 10 | demo: brief | CAPTURE `brief`: Beacon's first reply, sentence highlight, E1 chip, evidence card. | You open it, and Beacon briefs you. Every sentence it speaks is pinned to an evidence card. Click E one, and you see exactly what it saw. | ★ |
| 11 | demo: can you fix it | CAPTURE `propose`: "can you fix it" → proposal card, blast radius, dry run PASSED under the remediator role. | Can you fix it? Beacon proposes exactly one allowlisted action. It reads the blast radius aloud, and dry-runs it under the role that would execute. Nothing has changed yet. | ★ |
| 12 | demo: approve | CAPTURE `approve`: "approve fix 1" → approve_fix chip → Step Functions loop → verify 3/3 → resolved, sparkline drops to zero. | Approve fix one. Those words are checked against your transcript, not the model's claim. A Step Functions loop applies the fix, then refuses to say recovered until three checks pass: the alarm is OK after the fix, the error metric is zero, and the rule is present. | ★ |
| 13 | demo: contract | CAPTURE `contract`: "yes" → read-back; "grant contract for seven days" → contract granted; Contracts tab shows the quote. | Then Beacon asks: should I handle this myself next time? You say yes. It reads back the exact scope. And only the phrase, grant contract for seven days, creates a Sleep Contract. Your own words are the record. | ★ |
| 14 | demo: the second night | CAPTURE `second`: second incident appears, "handled while you slept", tally: 2 resolved, 1 human woken. | The same fault, again. This time Beacon runs the same loop under your contract, verifies it, and sends a morning email. Two incidents. One human woken. | ★ |
| 15 | safety | Scene `safety`: nine controls appear as a list with the file that enforces each. | None of this is a prompt. Two IAM roles, one direction: the agent you talk to has zero write actions; the remediator holds exactly two, scoped by tag. Consent comes from your transcript. Executes exactly once. One switch stops every write path. And a test parses the real CloudFormation to prove it. | ★ |
| 16 | architecture, full | Scene `arch2`: the full AWS picture, service names lit as narrated. | Built on AWS end to end. Bedrock for reasoning. Strands Agents and Powertools, both AWS open source, for the agent and the functions. Step Functions for the loop. Transcribe and Polly for the voice. DynamoDB, EventBridge, SNS, CloudFront, CloudWatch. And a real Fargate application as the patient. | ★ |
| 17 | SUPPLY: real AWS | SUPPLY: screen recordings of your account: CloudWatch alarm in ALARM, Step Functions graph, the Lambda list, DynamoDB tables, CloudFront console URL. | And it is deployed. This is the account, this is the alarm, and this is the loop running. | ★ |
| 18 | production notes | Scene `prod`: the production-notes table scrolls: log groups, alarms, CSP, Secrets Manager, pinned images. | It is built to be run, not just shown: named log groups, error alarms, a content security policy, secrets in Secrets Manager, and every claim has a test. | |
| 19 | build it | CAPTURE `local`: terminal `make local`, then `?night=1` playing. | And if you have no AWS account at all: make local runs the entire product against moto on your laptop, and plays the whole night for you. | |
| 20 | learning | Scene `learn`: five lessons, one line each. | What I learned this weekend: a dry run only tells the truth for the caller that will execute. Tag-scoped IAM misses the resources that cannot be tagged. Verification is a loop, not a boolean. And the transcript is a safety artifact. | ★ |
| 21 | close | Scene `close`: logo, repo URL, live URL, "Build for the fourth question." | Beacon Night Shift. Build for the fourth question. | ★ |

## Music cue
Ambient bed from 0:00; low at −22 dB under narration; swell at scene 6 (title) and 14 (second night);
fade out over the close.

## Human shot list (scene 17), record at 1920×1080, 10–15 s each, no cursor waving:
1. CloudWatch → Alarms → `beacon-demo-infra-errors` in ALARM (after `make break-demo`), the graph visible.
2. Step Functions → `beacon-remediate-beacon` → an execution → the graph view with green states.
3. Lambda → Functions list filtered by `beacon-`.
4. DynamoDB → Tables list (`beacon-incidents-beacon`, approvals, contracts, changes).
5. CloudFront → the distribution → the console URL open in another tab with the Night Board.
6. Bedrock → Model access page (the moment access is granted).
Drop them in `~/beacon-video/assets/supply/01-alarm.mp4` … `06-bedrock.mp4`.
