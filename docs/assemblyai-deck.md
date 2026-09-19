# Beacon Night Shift — AssemblyAI Voice Agent deck (10 slides)

*Source for the `.pptx` / PDF. One idea per slide; the speaker line is what is said, not what is written. Screenshots referenced by name live in `docs/assets/` once the AssemblyAI runs exist (Fri 25 Sep).*

| # | Title | On the slide | Speaker line (≤ 20 s) |
|---|---|---|---|
| 1 | **The on-call agent you can interrupt** | Cover image (`cover.png`: Night Board mid-approval), one line: *"A voice agent whose most important sentence is a tool call that can change production."* | Every voice-agent demo is a receptionist. This one holds write access to AWS, and the transcript is the safety artifact. |
| 2 | **3 AM, four questions** | *is it real · what changed · what do I do · can I go back to sleep* — with the tools that answer each (most stop at one). | The fourth question is the product. Nothing changes unless something changes. |
| 3 | **One night, ninety seconds** | Timeline strip: alarm → RCA on Bedrock → "can you fix it" → dry run + blast radius → "approve fix one" → Step Functions verify ×3 → "grant contract for seven days" → second alarm handled, nobody woken. | The whole loop, then the same fault again with zero humans woken. That second row is the pitch. |
| 4 | **Where AssemblyAI sits** | Architecture: browser ⇄ `wss://agents.assemblyai.com` (Universal-3 Pro STT, turn detection, tool calling, TTS) ⇄ `POST /tools/<name>` on the voice Lambda ⇄ the unchanged backend (registry, Step Functions, DynamoDB, Bedrock inside the tools). | The Voice Agent API is the mouth and ears; every tool runs on our side with the raw transcript in hand. Same six tools as the AWS cascade, selectable with `?voice=`. |
| 5 | **Consent from the transcript, gated on confidence** | Code strip: `TurnContext(transcript, confidence)`; `approve_fix` ignores its model-written argument; `< 0.85 → "please repeat the exact phrase"`. Screenshot: *heard: "approve fix one" (97%)*. | The model can call the tool. Only what the engineer actually said — and how clearly — can make it succeed. |
| 6 | **Barge-in with a safety meaning** | Screenshot: the read-back cut mid-sentence with an *interrupted* marker; the proposal withdrawn (`cancel_proposal`). | Interrupting Beacon during a read-back does not just stop the audio, it withdraws the fix. Turn-taking as a control. |
| 7 | **Hinglish, because 3 AM IST is someone's peak** | `"haan, saat din ke liye contract do"` → contract granted; keyterms per incident (`sg-…`, alarm name, the phrases) sent in `session.update`. | Universal-3 Pro handles code-switching; the grant phrase works in both languages, and the exact phrase is still required. |
| 8 | **Drop-safety** | Sequence: socket killed mid-approval → nothing executed; execution is a DynamoDB approval record + a Step Functions run, never socket state. Test name on the slide. | If the call drops, the worst case is that nothing happens. That is the right worst case. |
| 9 | **Measured** | Latency overlay: cascade (Transcribe → Nova → Polly) vs Voice Agent, per turn, real numbers from Thu 24; tally: incidents resolved, median recovery, humans woken. | One socket beats three services by about N hundred milliseconds per turn; the number on the right is the one that matters. |
| 10 | **Business value and what is next** | Who pays: solo engineers and small teams on-call for other time zones; what it saves: the repeat page. Roadmap: AgentCore Memory for contract history, Nova 2 Sonic, phone channel. Repo · live URL · passcode. | Try it: the live URL with the judge passcode, or `make local` and `?night=1` with no AWS account at all. |

## Production notes for the deck

- 16:9, dark background matching the console (`#0b0b0d`, Geist), one accent (lilac for contracts, amber for "needs you"), no bullet walls.
- Slides 5–8 each carry one screenshot from a real AssemblyAI run and the name of the test that proves the behaviour.
- Export: `.pptx` for the form, PDF as backup; the cover image doubles as the submission thumbnail.
