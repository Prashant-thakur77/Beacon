# AssemblyAI Voice Agent hackathon — submission text (lablab.ai)

Deadline **Tue 30 Sep 2026, 20:30 IST**. Paste from here; keep the links exactly.

## Title

**Beacon Night Shift — the on-call agent you can interrupt**

## Short description (≤ 200 characters)

A voice agent with write access to AWS. Answer the phone at 3 AM, say "approve fix one", and it applies the fix and proves the recovery. Two-channel recording; the transcript is the safety artifact.

## Long description

It is 3 AM. Payments are failing. You are alone, half-asleep, phone in hand. Every voice-agent demo is a receptionist; this one holds write access to production, so the question is not whether it can talk but what it is allowed to do on your word.

**What happens.** A CloudWatch alarm fires. Beacon reads the logs, diffs the security groups against a golden snapshot, finds the CloudTrail change that caused it, dry-runs the one allowlisted fix under a locked-down role, and pages you — in the browser and on Telegram. You say *"fix it"* (or *"isko fix kar do"*); Beacon reads back the blast radius. You say *"approve fix one"*; a Step Functions loop applies the fix and refuses to say *recovered* until CloudWatch agrees. You say *"grant contract for seven days"*; the second time it breaks, Beacon fixes it while you sleep. You say *"open the pull request"*; the missing rule is restored in the CloudFormation template with the postmortem, and nothing is merged at 3 AM.

**Answer the phone.** A laptop at 3 AM needs finding, unlocking, a network and a browser. A phone rings in the dark and you answer it — so Beacon answers one. The call is bridged into the *same* Voice Agent socket. The API takes **G.711 µ-law natively** (`audio/pcmu`) and a phone line is µ-law already, so the audio is forwarded **untouched in both directions** — no resampling, no transcoding, and none of the quality a trip through 24 kHz and back would cost. Turn detection is loosened for a noisy line; the same nine tools, the same consent rules. Two rules the phone adds. **A keypress is never consent** — DTMF can acknowledge, re-brief or hang up, and pressing anything else makes the agent say that a change needs the spoken phrase, because an approval has to be words somebody can be held to. And **only the caller's audio ever reaches the transcriber**, so the transcript the consent check reads cannot contain the agent reading a phrase back to itself.

**The transcript is the authorisation.** Beacon is not a voice interface bolted onto a tool. The words are the artifact a change to production is justified by, afterwards, to somebody who was not in the room — and every property of that artifact comes from AssemblyAI: what unlocked the change, how clearly it was heard, who said it, and a recording to play back. Take AssemblyAI out and you do not lose the voice interface; you lose the audit, and the audit is the reason this can hold a credential at all. Every applied change ships a **consent certificate** carrying exactly those fields, printed into the pull request ahead of the diff, because that is where somebody reads it six weeks later.

**A live turn carries no confidence, so we went and got one.** `transcript.user` has text and no number — which meant a browser or phone approval was applied without one, while the same words in a Telegram voice note had to clear 85 %. AssemblyAI now re-transcribes **its own recording of the session** afterwards, and each phrase that unlocked a change is scored by its **weakest word**: "approve fix one" heard as "approve fix" plus a guess is not 90 % correct, it is wrong in the one place that decides which fix runs. The certificate says plainly whether that number *gated* the change or only *reviewed* it. One standard, whichever channel carried the words.

**Who said the sentence that changed production.** The agent reads the approval phrase back, so the phrase is in every recording twice — once from each party. A single-channel recording cannot say which occurrence authorised the change. So a call is recorded with the caller on one channel and the agent on the other, and AssemblyAI's **`dual_channel`** transcription labels every utterance by channel without inferring speakers. Each consent tool that ran is then checked against the caller's channel; a change whose phrase appears only on the agent's channel fails. In a real run: `ok approve_fix from the caller's channel (1x; agent 2x)` — the agent's two are its read-backs, and they are not consent.

**And whether the caller was alone.** A two-channel recording proves the approval came from the caller's side, not from the agent reading the phrase back. It does not say whether somebody else was in the room, and "a colleague told them to say it" is a different failure. AssemblyAI answers that too: `dual_channel` and `speaker_labels` can be requested **together**, and the speaker comes back as channel-then-speaker — `1A`, `2A` — so a second person on the caller's side arrives as `1B`. A second voice near the approval is flagged, not treated as a forgery: somebody did say the phrase, and it is the room a reviewer should be told about. A browser session cannot be checked this way at all — one channel carries both parties — so the certificate says nothing rather than guessing.

**Where AssemblyAI is.** The browser talks to the **Voice Agent API** — Universal-3 Pro, turn detection, the managed LLM and TTS on one socket — with Beacon's nine tools declared as client-side functions. Every `tool.call` returns to the browser, which runs it on our Lambda with the transcript the API produced: consent is checked against what you said, never against the model's argument. Telegram voice notes go through the **pre-recorded API** with word-level confidence, so a mumbled approval is refused with the number it was heard at. Every approval stores the **session recording**; the audit page plays the words behind the fix.

**The socket, used the way it is meant to be used.** Nine tools declared as **JSON-Schema functions** on the session, called mid-sentence so the answer to *"what changed"* is spoken while the evidence is still arriving. **Turn detection tuned per channel** — the browser keeps the defaults; the phone waits `min_silence` 700 ms with `vad_threshold` 0.65, because a half-asleep engineer pauses mid-sentence and being cut off at 3 AM is worse than waiting. **Keyterms rebuilt per incident**, priming the exact phrase that will unlock *this* proposal in both spoken and digit form, because "approve fix eight" misheard as "approve fix ate" is the one error that matters. **`language_codes: ["en", "hi"]`** so the same session takes English and Hinglish without being told which is coming. And **`session.resume`** within the 30-second window after a drop, because a socket dying mid-incident should not cost the engineer the conversation — while execution never lives in socket state, so a drop after *approve* applies nothing.

**Latency, measured rather than asserted.** [`docs/assets/benchmark.md`](assets/benchmark.md) is generated by `make bench` from the reports the regression suite wrote while the calls were happening — median **3.45 s** to first audio over 15 real turns at telephone quality, and it is *above* AssemblyAI's own 1.5 s guidance. Two deliberate choices are inside that number and both are ours: the 700 ms of turn-detection patience above, and the AWS round trip on a turn that calls a tool, because a fix is dry-run under a locked-down role before the agent is allowed to read its blast radius back. Transcription is not the slow part. The obvious next move — starting the dry run speculatively while the agent is still speaking — is not built yet, and the file says so.

**Four AssemblyAI products, four jobs.** The Voice Agent API is the live conversation. Pre-recorded transcription reads Telegram voice notes — with word-level confidence for the 85 % consent gate, and PII redaction, so what gets *written* to the approval row is redacted while consent is checked on the words as heard. Summarization closes the loop: the audit can summarise the session recording behind an approval, and that summary is printed in the postmortem as *the night in the engineer's words*. Dual-channel transcription makes the phone call's approval attributable to the human who gave it.

**The gate refusing, on camera.** The strongest thirty seconds of the film were not planned. Asked to approve, the engineer's voice note was transcribed at 75% and refused; said again it came back at 74% and was refused again; they typed the phrase instead. Nothing reached production until the words were certain. That is the safety argument demonstrating itself on a live AWS account, and it is the reason the confidence number matters more here than the model does.

**Tests that speak — including down a phone line.** `make phone-test` places five scripted calls on a fresh incident each, through the real Voice Agent API and the real tools, with the caller's lines degraded to telephone quality first. The caller says the fix number the agent actually proposed (a script hard-coding "one" is a refused approval, which looks exactly like a real bug), every call is checked against its own two-channel recording, and the keypad scenario never speaks the phrase at all — so anything applied in it was applied by a keypress. `make voice-test` runs six spoken scenarios through the Voice Agent API against the real tools — the exact phrase applies a fix, *"yes, do it"* does not, interrupting the read-back withdraws the proposal, Hinglish reaches the same tool, a contract needs the read-back and then the phrase, and an applied fix can be undone by phrase. Writing it found three real bugs.

**Three behaviours the socket makes possible.** Speaking over a read-back withdraws the fix it was reading (barge-in with a safety meaning). If the socket drops after you said *approve*, nothing executes — execution is a Lambda call, never socket state (tested by aborting the socket mid-turn). And the night ends with a pull request produced by code from the parameters that passed the dry run.

**Measured, on the live account.** Turn end to first audio 0.1–0.4 s; *"fix it"* to the proposal in 1.9 s; approval to a verified recovery in 2 m 35 s; Hinglish in and out. Everything in the film is a real run; nothing is scripted.

**Where the model is and is not.** The model decides whether to call a tool, after your words. Code decides whether the call is allowed, what the fix is, what the patch is, and what the reports say. No model writes to AWS or to the repository.

## Business value (the form's own field, and slide 8–9)

**Who.** A backend engineer on a team of one to five running production on AWS for users in another time zone — a four-person startup in Bengaluru serving New York, the single DevOps hire at a 30-person SaaS. No follow-the-sun rotation, no second shift, a small and repetitive failure surface, no budget for an SRE platform.

**What it saves.** Industry MTTR is 53 minutes and has improved 12 % in five years against a tripling of monitoring spend; downtime runs from Gartner's $5,600/min baseline upward; 74 % of DevOps engineers report burnout with on-call load the leading indicator. On our live account a real alarm reaches a verified recovery in 2.4–5.5 minutes without a laptop being opened, and under a Sleep Contract the repeat fault is fixed with nobody woken at all.

**Market.** TAM $4.8 B incident management (→ $12.99 B by 2035, 11.7 % CAGR); SAM ≈ $1.2 B for alert response and auto-remediation, of which PagerDuty alone books $493 M; SOM ≈ $125 M/yr bottom-up — roughly 120k small AWS teams × 3 responders × $29/month.

**Revenue.** $29 per responder per month; **$2 per verified remediation** — charged only when a fix was applied and CloudWatch agreed, so we earn nothing from noisy alerts; $15k/year self-hosted in the customer's own account (three CloudFormation stacks, no data leaves their boundary).

**Who we are against.** Everything that reaches a 3 AM AWS alarm today either **asks without acting** or **acts without asking**. PagerDuty routes the page; incident.io opens a channel; both are correct and unhelpful, because the minutes that matter still contain *"engineer finds laptop"*. AWS SSM runbooks and EventBridge auto-remediation do act — and never ask, so they will cheerfully restore a rule somebody removed on purpose, because a runbook sees a condition and never a cause. Voice agents pointed at on-call transcribe and summarise; the transcript is a record nobody is bound by. Beacon is the only one that does both, and the asking is what makes the acting defensible. Concretely, on this incident: a runbook restores the missing rule including when its removal was deliberate, where Beacon reads CloudTrail first, names the actor, and when nothing explains the alarm offers a restart *as a restart* and never as a root cause. Full table: [README § What else is in this space](https://github.com/Prashant-thakur77/Beacon/tree/assemblyai#what-else-is-in-this-space-and-where-it-stops).

**Why it needed this generation of AI.** Consent is checked against the words Universal-3 Pro actually returned, including half-asleep Hinglish — DTMF cannot express *which* fix on *which* resource, and older ASR could not carry that responsibility. Barge-in has to be observable (`reply.done {interrupted}`) for an interruption to withdraw a pending fix. Nine tools have to be callable mid-turn with results feeding the next sentence. Full reasoning: [docs/business-case.md](business-case.md).


## YouTube metadata (for the upload)

**Title**
`Beacon Night Shift — the on-call agent you can interrupt (AssemblyAI Voice Agent API)`

**Description**
```
A voice agent with write access to AWS. At 3 AM it pages you on Telegram, you answer with a voice
note, and the fix only happens on words you actually said.

Everything in the demo section is a real run on a deployed AWS account — including the moment where
speaking over the read-back withdraws the proposed fix, and the approval phrase stops working.

Built on AssemblyAI:
• Voice Agent API (Universal-3 Pro, turn detection, barge-in, TTS) with nine tools on one socket
• Pre-recorded transcription with word-level confidence for Telegram voice notes
• Session recordings played back in the audit, behind every approval

And on AWS: Lambda, Step Functions, DynamoDB, EventBridge, CloudTrail, CloudWatch, ECS, RDS, SNS.

Live console (judge passcode in the submission):
https://6die6lduac6ipxkeg73nxsuvpu0yzkim.lambda-url.us-east-1.on.aws/
Code (MIT): https://github.com/Prashant-thakur77/Beacon/tree/assemblyai
A pull request Beacon opened by voice: https://github.com/Prashant-thakur77/beacon-demo-infra/pull/2

Chapters
0:00 03:12 — the page nobody answers
0:22 The page lands on the phone
0:40 The Night Board: what changed
0:49 One socket, full duplex
1:00 Propose a fix, dry run first
1:10 Interrupt the read-back — the fix is withdrawn
1:23 "Approve fix two", and the verify loop
1:39 A Sleep Contract, in your own words
1:50 The pull request that ends the incident
2:02 The recording, played back
2:13 Who it is for
2:22 Market
2:33 Revenue
2:45 Why this needed this generation of models
3:17 Built solo, in ten days

Music: "Immersed" by Kevin MacLeod (incompetech.com), CC BY 4.0.
Narration: Chatterbox TTS. No editor was used — the film is assembled by ffmpeg from
video/script-assemblyai.md; the pipeline is in the repo.
```

**Captions** `Beacon-AssemblyAI.srt` in the release — upload it with the video. They were produced by **AssemblyAI's pre-recorded transcription**, the same API that reads the engineer's voice notes and re-scores an approval against its own recording. Captioning our own film with it is the shortest honest demonstration that the transcription is good enough to build consent on.

**Thumbnail** `docs/assets/thumbnail-aai.jpg` · **Visibility** public · **Category** Science & Technology

## Links

- Live console (passcode in the form): https://6die6lduac6ipxkeg73nxsuvpu0yzkim.lambda-url.us-east-1.on.aws/
- Repository (MIT, branch `assemblyai`): https://github.com/Prashant-thakur77/Beacon/tree/assemblyai
- The pull request Beacon opened by voice: https://github.com/Prashant-thakur77/beacon-demo-infra/pull/2
- Film (4:46): *(YouTube link — upload `Beacon-AssemblyAI.mp4` from the release)* · direct download: https://github.com/Prashant-thakur77/Beacon/releases/download/v0.6.0/Beacon-AssemblyAI.mp4
- Deck (11 slides): https://github.com/Prashant-thakur77/Beacon/releases/download/v0.6.0/Beacon-deck.pdf
- Release with every artefact: https://github.com/Prashant-thakur77/Beacon/releases/tag/v0.6.0
- Telegram bot: https://t.me/GoodNightShiftbot (allowlisted to the author; the film shows it)

## Tags

Voice Agent API · Universal-3 Pro · pre-recorded transcription · dual-channel · tool calling · barge-in · telephony · G.711 · Hinglish · AWS · Step Functions · Telegram · GitHub · on-call · SRE

## The video (4–5 min, per lablab's structure)

| Time | Content |
|---|---|
| 0:00–0:30 | The problem: 03:12, one-person rotation, the numbers |
| 0:30–1:05 | Where the words go: three channels on one socket, and the phone call — two channels, the approval attributable to the human |
| 1:05–1:50 | A real phone, filmed: the page lands, a voice note is heard at **98%**, and the CloudTrail entry that removed the rule |
| 1:50–2:10 | **The approval refused at 75%, refused again at 74%, and typed instead** — the confidence gate, unscripted, on a live account |
| 2:10–3:05 | The same night on a laptop: read-back → barge-in withdraws the fix → *approve fix one* → verified → contract → *open the pull request* → the recording in the audit |
| 3:05–4:10 | Business case: who it is for, market, revenue model, why it needed this generation of AI |
| 4:10–4:46 | Built solo in ten days; what is next; the live URL and passcode |

## Judge's 90 seconds

1. Open the live URL, enter the passcode, press the mic. Say *"what happened"*, then *"fix it"*. Interrupt the read-back: the fix is withdrawn. Say *"fix it"* again, then *"approve fix two"*.
2. Watch the loop verify (Night Board timeline). Say *"open the pull request"* once it is resolved; the PR appears on GitHub within 30 s.
3. Open *Audit*: **▶ Listen** plays the AssemblyAI recording behind the approval, and **◎ How clearly was it heard?** re-transcribes that recording *per channel* and scores the phrase by its weakest word — on a live phone approval, **95 %**, weakest word `fix`, at 40.1 s **on the caller's channel**. The agent reads the phrase back too, and its own voice is not consent.
4. For the phone leg with no phone: `make local` in one shell, then `make phone-test` — five scripted calls at telephone quality through the real Voice Agent API, each leaving a two-channel WAV and a verdict on which channel the approval came from.

No account, no mic: `make setup && make local`, then `?night=1` plays the whole night unattended.
