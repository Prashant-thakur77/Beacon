# AssemblyAI Voice Agent hackathon — submission text (lablab.ai)

Deadline **Tue 30 Sep 2026, 20:30 IST**. Paste from here; keep the links exactly.

## Title

**Beacon Night Shift — the on-call agent you can interrupt**

## Short description (≤ 200 characters)

A voice agent with write access to AWS: it fixes the 3 AM page on your word, verifies it, answers your Telegram voice note, and ends the night with a pull request. The transcript is the safety artifact.

## Long description

It is 3 AM. Payments are failing. You are alone, half-asleep, phone in hand. Every voice-agent demo is a receptionist; this one holds write access to production, so the question is not whether it can talk but what it is allowed to do on your word.

**What happens.** A CloudWatch alarm fires. Beacon reads the logs, diffs the security groups against a golden snapshot, finds the CloudTrail change that caused it, dry-runs the one allowlisted fix under a locked-down role, and pages you — in the browser and on Telegram. You say *"fix it"* (or *"isko fix kar do"*); Beacon reads back the blast radius. You say *"approve fix one"*; a Step Functions loop applies the fix and refuses to say *recovered* until CloudWatch agrees. You say *"grant contract for seven days"*; the second time it breaks, Beacon fixes it while you sleep. You say *"open the pull request"*; the missing rule is restored in the CloudFormation template with the postmortem, and nothing is merged at 3 AM.

**Where AssemblyAI is.** The browser talks to the **Voice Agent API** — Universal-3 Pro, turn detection, the managed LLM and TTS on one socket — with Beacon's nine tools declared as client-side functions. Every `tool.call` returns to the browser, which runs it on our Lambda with the transcript the API produced: consent is checked against what you said, never against the model's argument. Telegram voice notes go through the **pre-recorded API** with word-level confidence, so a mumbled approval is refused with the number it was heard at. Every approval stores the **session recording**; the audit page plays the words behind the fix.

**Three behaviours the socket makes possible.** Speaking over a read-back withdraws the fix it was reading (barge-in with a safety meaning). If the socket drops after you said *approve*, nothing executes — execution is a Lambda call, never socket state (tested by aborting the socket mid-turn). And the night ends with a pull request produced by code from the parameters that passed the dry run.

**Measured, on the live account.** Turn end to first audio 0.1–0.4 s; *"fix it"* to the proposal in 1.9 s; approval to a verified recovery in 2 m 35 s; Hinglish in and out. Everything in the film is a real run; nothing is scripted.

**Where the model is and is not.** The model decides whether to call a tool, after your words. Code decides whether the call is allowed, what the fix is, what the patch is, and what the reports say. No model writes to AWS or to the repository.

## Business value (the form's own field, and slide 8–9)

**Who.** A backend engineer on a team of one to five running production on AWS for users in another time zone — a four-person startup in Bengaluru serving New York, the single DevOps hire at a 30-person SaaS. No follow-the-sun rotation, no second shift, a small and repetitive failure surface, no budget for an SRE platform.

**What it saves.** Industry MTTR is 53 minutes and has improved 12 % in five years against a tripling of monitoring spend; downtime runs from Gartner's $5,600/min baseline upward; 74 % of DevOps engineers report burnout with on-call load the leading indicator. On our live account a real alarm reaches a verified recovery in 2.4–5.5 minutes without a laptop being opened, and under a Sleep Contract the repeat fault is fixed with nobody woken at all.

**Market.** TAM $4.8 B incident management (→ $12.99 B by 2035, 11.7 % CAGR); SAM ≈ $1.2 B for alert response and auto-remediation, of which PagerDuty alone books $493 M; SOM ≈ $125 M/yr bottom-up — roughly 120k small AWS teams × 3 responders × $29/month.

**Revenue.** $29 per responder per month; **$2 per verified remediation** — charged only when a fix was applied and CloudWatch agreed, so we earn nothing from noisy alerts; $15k/year self-hosted in the customer's own account (three CloudFormation stacks, no data leaves their boundary).

**Why it needed this generation of AI.** Consent is checked against the words Universal-3 Pro actually returned, including half-asleep Hinglish — DTMF cannot express *which* fix on *which* resource, and older ASR could not carry that responsibility. Barge-in has to be observable (`reply.done {interrupted}`) for an interruption to withdraw a pending fix. Nine tools have to be callable mid-turn with results feeding the next sentence. Full reasoning: [docs/business-case.md](business-case.md).

## Links

- Live console (passcode in the form): https://6die6lduac6ipxkeg73nxsuvpu0yzkim.lambda-url.us-east-1.on.aws/
- Repository (MIT, branch `assemblyai`): https://github.com/Prashant-thakur77/Beacon/tree/assemblyai
- The pull request Beacon opened by voice: https://github.com/Prashant-thakur77/beacon-demo-infra/pull/2
- Film: *(YouTube link, Stage 5)*
- Telegram bot: https://t.me/GoodNightShiftbot (allowlisted to the author; the film shows it)

## Tags

Voice Agent API · Universal-3 Pro · pre-recorded transcription · tool calling · barge-in · Hinglish · AWS · Step Functions · Telegram · GitHub · on-call · SRE

## The video (4–5 min, per lablab's structure)

| Time | Content |
|---|---|
| 0:00–0:30 | The problem: 03:12, one-person rotation, the numbers |
| 0:30–2:30 | Live demo: page → Telegram voice note → read-back → barge-in withdraws the fix → *approve fix one* → verified → contract → *open the pull request* → the recording in the audit |
| 2:30–4:00 | Business case: who it is for, market, revenue model, why it needed this generation of AI |
| 4:00–4:45 | Built solo in ten days; what is next; the live URL and passcode |

## Judge's 90 seconds

1. Open the live URL, enter the passcode, press the mic. Say *"what happened"*, then *"fix it"*. Interrupt the read-back: the fix is withdrawn. Say *"fix it"* again, then *"approve fix two"*.
2. Watch the loop verify (Night Board timeline). Say *"open the pull request"* once it is resolved; the PR appears on GitHub within 30 s.
3. Open *Audit*: **▶ Listen** plays the recording behind the approval.

No account, no mic: `make setup && make local`, then `?night=1` plays the whole night unattended.
