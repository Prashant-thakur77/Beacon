# Feature plan — beyond the submission

Ordered by value to an on-call engineer, then by what can be proven without a model call
(the account's Bedrock access is still held). Items marked **tonight** are being built in the
20 Sep sprint; the rest are the roadmap.

| # | Feature | What it does | Why it matters | Status |
|---|---|---|---|---|
| 1 | **Postmortem generator** | `GET /incidents/{id}/postmortem` renders a deterministic Markdown postmortem from the incident record: timeline, root cause, the change that caused it, the fix and its blast radius, the three verification checks with timestamps, the approval quote, the contract if one was granted. "Download postmortem" on the incident card. | The document every team writes by hand the next morning, produced from the same facts the loop used; no model needed, so it works on the live site today. | **shipped 20 Sep** — `#postmortem/<id>`, Download .md |
| 2 | **Audit log** | `GET /audit`: every approval and contract with who/what/when, the exact transcript quote, channel, dry-run result, execution result, replayed-or-not; `#audit` route with filters and JSON/CSV export. | The safety story as a record, not a claim; what a security reviewer asks for first. | **shipped 20 Sep** — `#audit`, CSV/JSON export |
| 3 | **Morning report** | A scheduled Lambda run (07:00 IST) composes "last night": incidents, what Beacon did, contracts used, humans woken, minutes to recovery, cost; sent through SNS and served at `GET /report/latest` → `#report`. | Closes the loop on "can I go back to sleep": the engineer reads one email instead of a dashboard. | **shipped 20 Sep** — 07:00 IST schedule live on the account, `#report` |
| 4 | Webhook notifications | `WEBHOOK_URL` (Slack-compatible) alongside SNS for pages, "not woken" and escalations. | Teams live in Slack; email is the fallback. | **shipped 20 Sep** — `WEBHOOK_URL` (Slack-compatible) and `PAGERDUTY_ROUTING_KEY` on all three stacks; pages trigger, resolutions resolve, every message deep-links to `#board/<id>` |
| 5 | Acknowledge + escalation policy | "I'm on it" on the board; if nobody acknowledges within N minutes, escalate to a second topic/phone. | Real on-call rotations need a hand-off, not a single page. | next |
| 6 | Third allowlisted action: `ecs.scale_out` | Bounded desired-count increase on a tagged service, post-condition = running count ≥ desired, same IAM (`ecs:UpdateService`). | Proves the registry pattern generalises beyond the demo fault. | next |
| 6b | **Undo by phrase** | `undo fix <n>`: the fix's inverse action (`sg.revoke_ingress`) runs through the same dry run, approval record and single execute; only fixes Beacon executed can be undone; the incident returns to awaiting a human. | The thing that makes people comfortable approving. | **shipped 20 Sep** |
| 7 | Contract policies | Org caps (max days/uses), pause-all switch, expiry emails, renewal by voice. | Standing approvals need governance to be trusted at scale. | next |
| 8 | Hand-off notes | A note on an incident, spoken or typed, that appears in the morning report and the postmortem. | The 3 AM engineer's context for the 9 AM one. | next |
| 9 | Multi-account / multi-region | Stack-set the remediator per account; the console lists accounts. | Where real estates live. | later |
| 10 | AgentCore Memory for contract history | Beacon remembers past nights in conversation ("this broke twice last week"). | Mentor-gated; see PLAN-v2 item 10. | later |
