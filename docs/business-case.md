# Business case

Written for the AssemblyAI Voice Agent hackathon's *Business Value* criterion: one specific user, a sized market, a revenue model that matches where the value lands, and the reason this could not have been built two years ago.

## 1. The user, specifically

**A backend engineer on a team of one to five, running production on AWS for users in a different time zone.**

Concretely: a four-person startup in Bengaluru whose customers are in New York; a two-person team in Warsaw serving the US west coast; the single DevOps hire at a 30-person SaaS company anywhere. They share three properties that make them the right first user:

- **No follow-the-sun rotation.** There is no second shift to escalate to. The pager is one person's phone, every night.
- **A small, well-understood failure surface.** Most of their 3 AM pages are a handful of recurring, mechanical faults: a security-group rule dropped by a deploy, a service that needs a redeploy, a stuck task. The fix is known; the cost is being awake to apply it.
- **No budget for an SRE platform.** PagerDuty's per-seat pricing is affordable; the eight-tool observability stack around it is not, and neither is the person to run it.

This is not "every engineering team". Large enterprises have rotations, runbooks and change-control boards; they are a later market, and the self-hosted tier below is how it reaches them.

## 2. What it is worth to them

| What the industry measures | Number | Source |
|---|---|---|
| MTTR, industry average | 53 min (2025), improved from 78 min (2020) — a 12 % improvement against a 3× rise in monitoring spend | [JustAnalytics, 2026](https://justanalytics.app/blog/cost-of-downtime-statistics-2026) |
| Cost of downtime | Gartner's long-standing baseline $5,600/min (≈ $336k/hr); ITIC 2024 puts mid/large enterprise above $300k/hr; Splunk's 2026 report averages ~$15,000/min | [OutageCost](https://outagecost.com/cost-of-it-downtime), [Gatling](https://gatling.io/blog/the-cost-of-downtime) |
| Burnout | 74 % of software and DevOps engineers report burnout; on-call load is the leading indicator, and senior responders leave first | [Worktime, 2026](https://www.worktime.com/blog/statistics/employee-burnout-statistics-trends-in-the-workplace), [Uptime Labs](https://www.uptimelabs.io/learn/reduce-on-call-burnout) |

Beacon attacks both numbers, and the second one is the one nobody prices:

- **Minutes.** On our own demo account, a real alarm goes from firing to a verified recovery in **2.4–5.5 minutes**, most of it the Step Functions verification loop waiting for CloudWatch to agree. Against a 53-minute industry MTTR, the first fault of the night is roughly a 10× improvement — and the engineer never opens a laptop.
- **Nights.** Under a Sleep Contract the same fault, seen again, is fixed and verified with **nobody woken**. That is the line on the morning report: *"handled under your contract; you were not woken."* For a one-person rotation, the repeat page is the thing that ends careers at that company, and it is exactly the page that is most mechanical to fix.

A small team that is paged four times a month, twice for something Beacon already knows how to fix, gets back two nights of sleep a month and about 90 minutes of recovery time. At a loaded engineer cost of $40/hr that is a rounding error; at the cost of losing that engineer it is not.

## 3. Market

| Layer | Size | How it is derived |
|---|---|---|
| **TAM** — incident-management software | **$4.8 B (2026)**, growing to $12.99 B by 2035 at 11.7 % CAGR; the IT/DevOps segment alone is $4.5 B (2025) → $10.8 B (2034) | [MarkWide](https://markwideresearch.com/incidence-management-software-market), [Verified Market Reports](https://www.verifiedmarketreports.com/product/it-devops-incident-management-software-market/) |
| **SAM** — alert-response and auto-remediation for cloud-native teams | **≈ $1.2 B** — the on-call/response slice rather than ITSM ticketing; PagerDuty alone books $493 M of it, with incident.io, Opsgenie, Rootly and FireHydrant sharing the remainder | [PagerDuty revenue](https://en.wikipedia.org/wiki/PagerDuty) |
| **SOM** — small teams on AWS with a one-person rotation | **≈ $125 M/yr** at list price | bottom-up, below |

Bottom-up for the SOM, with the assumptions stated so they can be argued with: AWS reports over a million active customers; assume a fifth run production workloads with a real on-call expectation (200k organisations), and that 60 % of those are teams of ten or fewer (120k). At three responders each and $29/responder/month, that is **$125 M/year** of addressable spend in the initial niche. A realistic three-year capture of 0.5–1 % is **$0.6–1.2 M ARR** — a real small business, not a unicorn slide, and the honest number for a product that starts with one fault class on one cloud.

## 4. Revenue

Three tiers, priced where the value actually lands:

1. **Per responder, $29/month.** Directly comparable to PagerDuty's $21–41 per user, and the tier that gets adopted by a team already paying for paging.
2. **Per verified remediation, $2.** The differentiator: Beacon only charges when a fix was applied *and* verified by CloudWatch — the customer pays for nights not spent awake, not for seats that might page. It also aligns the incentive: we make nothing from noisy alerts.
3. **Self-hosted, $15k/year.** Three CloudFormation stacks in the customer's own account; no incident data, logs or recordings leave their boundary. This is the tier that reaches regulated and enterprise buyers, and it is already how the product is built — there is no multi-tenant service to write.

The wedge is tier 1 + 2 for small teams; the expansion is tier 3 when a larger org wants the Sleep Contract model under its own change-control policy.

## 5. Why this could not have been built two years ago

The product is not "an LLM that fixes servers". It is a **consent mechanism** that happens to be spoken, and every part of it needs capability that arrived with this generation of models:

- **The transcript has to be good enough to be evidence.** Consent is checked against the words Universal-3 Pro actually returned — including a half-asleep Hinglish sentence at 3 AM — not against an argument a model wrote. Older ASR could not carry that responsibility, and DTMF ("press 1 to approve") cannot express *which* fix on *which* resource.
- **Turn-taking has to carry meaning.** Speaking over the read-back withdraws the proposed fix. That requires managed barge-in with a reply lifecycle the application can observe — `reply.done {interrupted}` — not a half-duplex voice loop.
- **Tools have to be callable mid-conversation, with the result feeding the next sentence.** Nine of them, each with a JSON schema, executed server-side while the agent holds the turn.
- **The safety story has to survive the model being wrong.** The model decides *whether* to call a tool; code decides whether the call is allowed, what the parameters are, and what the pull request says. That separation is what makes write access to production defensible, and it is only interesting because the model is now good enough to be trusted with the first half.

Two years ago this would have been an IVR with a fixed menu, or a chatbot that opened a ticket. Neither of those is a thing you would grant `ec2:AuthorizeSecurityGroupIngress`.

## 6. Competition, honestly

| | What they do | What Beacon does differently |
|---|---|---|
| PagerDuty, Opsgenie | Route the alert to a human, fast and reliably | Answers it: proposes one allowlisted fix, applies it on a spoken phrase, verifies it |
| incident.io, Rootly | Coordinate the humans once they are awake — channels, roles, timelines | Aims at the incident that should never have woken anyone |
| AWS Systems Manager runbooks, Auto Remediation | Execute a fix automatically on an alarm | Requires human consent the first time, records the words that gave it, and only then offers a scoped, expiring standing approval |
| "AI SRE" agents | Summarise and suggest | Holds the write credential — behind a dry run, a phrase, a verification loop and an undo |

The honest risk: an incumbent adds voice. The defensible part is not the voice; it is the consent record, the Sleep Contract model and the fact that the pull request — not the alarm clearing — is the end of the incident.

## 7. What it costs to run

Measured on the demo account: about **$0.75/day** of AWS for the always-on demo workload (an RDS t4g.micro and one Fargate task — the *patient*, not the product), and a few tenths of a rupee of model cost per incident. Beacon itself is five Lambdas, three DynamoDB tables and a Step Functions state machine: effectively zero at rest, cents per incident in use. Gross margin at $29/responder is not the interesting constraint; adoption is.
