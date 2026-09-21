# AssemblyAI Voice Agent hackathon — phase plan (21–30 Sep 2026)

Same product, same backend, a different mouth and ears. Judged on *Application of Technology, Presentation, Business Value, Originality*; five flat winners out of ~80 entries, most of which are receptionists and support bots. Beacon's pitch: **a voice agent whose most important sentence is a tool call that can change production — and the transcript is the safety artifact.**

## API choice

**Voice Agent API** (`wss://agents.assemblyai.com/v1/ws`) as the primary path: Universal-3 Pro STT, managed turn detection and barge-in, LLM routing, TTS, JSON-Schema tool calling on one socket. The Realtime STT API stays as a fallback (`VOICE_BACKEND=assemblyai_stt`: swap only the STT transport, keep Strands + Nova + Polly) if client-side tool semantics do not fit.

Facts verified from the docs on 18 Sep (re-verify on day 1, the spike):

| Item | Verified |
|---|---|
| Endpoint | `wss://agents.assemblyai.com/v1/ws`; auth documented as `Authorization: Bearer <key>` (header) — browsers cannot set WS headers, so the spike must confirm the temporary-token or query-param path. `voice_turn._mint_assemblyai_token` is the single function to adjust. |
| First message | `session.update { session: { system_prompt, greeting, input:{format:{encoding:"audio/pcm"}, turn_detection:{vad_threshold}, transcription_mode, language_codes, voice_focus}, output:{voice, format}, tools:[{type:"function", name, description, parameters}] } }`; re-sendable mid-call. |
| Audio | in: `input.audio { audio: base64 PCM16 24 kHz mono, ~50 ms chunks }`; out: `reply.audio { data }` base64 PCM16 24 kHz mono (the field is `data`, not `audio`; verified by saving a reply and transcribing it). |
| Events | `session.ready`, `transcript.user.delta` (partial, overwrite), `transcript.user` (final), `reply.started`, `reply.audio`, `transcript.agent` (full, trimmed if interrupted), `tool.call { call_id, name, arguments }`, `reply.done { status: completed \| interrupted }`, `session.error`. |
| Tool results | `tool.result { call_id, result: "<json string>", is_error }` — **only when `reply.done` is the latest event** for the turn that carried the call. The transport buffers results until then. |
| Limits | ~1 s end-to-end latency, 30 s reconnect window. |

## Day-1 spike results (21 Sep, against the live socket with a real key)

| Question | Answer, verified |
|---|---|
| Browser auth | `GET https://agents.assemblyai.com/v1/token?expires_in_seconds=600` with `Authorization: Bearer <key>` → `{token, expires_in_seconds}`; connect to `wss://agents.assemblyai.com/v1/ws?token=<token>`. Tokens are single-use: mint one per connection. (`/v2/realtime/token` is the old STT endpoint: 404.) |
| Typed input | The AsyncAPI spec has only six client events (`session.update/resume/end`, `input.audio`, `tool.result`, `reply.create`); there is no text event and no `conversation.message` (a docs summary suggested one; the socket ignores it). Typed lines ride `reply.create {instructions: 'The engineer just typed: "…"'}` — verified: the model then calls the tool with the right argument. |
| Server-injected turn | Same mechanism: `reply.create` with instructions ("System update: the alarm is back to OK…") makes the agent speak to a fact. |
| Tools | Declared inline in `session.update` as `{type:"function", name, description, parameters, execution_mode:"interactive", timeout_seconds}`. `tool.call {call_id, name, arguments}` arrives mid-reply; send `tool.result {call_id, result:<JSON string>}` only when `reply.done` is the latest event. Verified: message → `tool.call` → `reply.done` → `tool.result` → spoken reply in ~3 s. |
| Confidence | `transcript.user` / `transcript.user.delta` carry **no confidence field** (`delta.text` is the full text so far). The consent gate therefore rests on the exact phrase; the "please repeat" path uses a read-back instead of a confidence bar. |
| Interruption | Turn detection is on by default; speaking over the agent yields `reply.done {status:"interrupted"}` and `transcript.agent {interrupted:true}`. There is no `reply.cancel`; the UI button stops playback locally and injects a system message. |
| Extras | `session.ready` carries a `session_id` and a `resume_token`; reconnect with `session.resume { session_id }` within 30 s of the drop (`resume_token` is not the argument — the socket answers `invalid_format` to it), `transcript.agent.delta` has `start_ms/end_ms` per word (sentence highlighting for free), `input.speech.started/stopped` markers, `session.ended` with durations. Voices: `jane` (default in our config), `anna`, `george`, … |
| Model behaviour | The managed LLM invented a tool argument ("hello world" when asked to echo "beacon") — the reason consent is checked against the transcript, never the arguments. |

## The boundary in code (already on branch `assemblyai`)

- **Browser** `web/src/voice/transport.ts`: `VoiceTransport { start(session, handlers), sendAudio, sendText, interrupt, stop }` with handlers for user transcript (partial/final + confidence), agent text (with `[E#]` citations), agent audio, done/interrupted, tool results, state. `web/src/voice/assemblyai.ts` implements it; the First Commit cascade gets wrapped as `AwsCascadeTransport` on day 2.
- **Tool definitions** `web/src/tools.json`, generated from `voice_tools.TOOL_SCHEMAS` by `make export-tools`; a test fails if it drifts.
- **Server** `POST /tools/<name>` on the voice Lambda: runs one tool with a `TurnContext` built from the **browser-supplied final transcript and its confidence**. Consent tools (`approve_fix`, `grant_sleep_contract`) are refused below 0.85 confidence with "please repeat the exact phrase". `POST /assemblyai/token` mints a short-lived token from an SSM SecureString (`ASSEMBLYAI_KEY_PARAM`), passcode-gated.
- **Everything else is unchanged:** triage, ledger, registry, Step Functions loop, contracts, dashboard, DynamoDB, Bedrock inside the tools. Both entries share one backend and one CloudFront URL (`?voice=assemblyai` / `?voice=aws`).

## Already built on this branch (beyond the scaffold)

- `web/src/voice/select.ts`: `chooseBackend` (`?voice=` / `config.voiceBackend`) and `makeTransport` for both backends.
- `web/src/voice/awsCascade.ts`: the First Commit path behind `VoiceTransport` (for the side-by-side comparison).
- `web/src/voice/pcmPlayer.ts`: PCM16 ring playback with `flush()` for barge-in.
- `web/src/components/TalkDuplex.tsx`: full-duplex Talk — continuous mic, interrupt button, *interrupted* marker on the cut-off reply, "heard: … (97%)" line, per-incident keyterms sent in `session.update`, tool results routed through `/tools/<name>`.
- `console-template.yaml` `AssemblyAIKeyParam` → `ASSEMBLYAI_KEY_PARAM`; `make set-assemblyai-key ASSEMBLYAI_API_KEY=…` then `make deploy-console VOICE_BACKEND=assemblyai`.

Day 1's spike is now one command: `ASSEMBLYAI_API_KEY=… .venv/bin/python scripts/assemblyai_probe.py` — it tries the three auth variants (header, minted query token, query key), sends `session.update` with an echo tool, prints one line per distinct event, and ends with the list of event names `assemblyai.ts` expects but did not see. Fix whatever differs in `assemblyai.ts` / `_mint_assemblyai_token` and move on.

## Stage 1 results (21 Sep evening, live socket, real key)

Everything below ran unattended by `scripts/dev/assemblyai_loop.py` (typed lines in a real Chromium against the console) and `scripts/dev/assemblyai_audio.py` (Polly speech pushed through `input.audio`, the same tools, no browser), first against `make local`, then against the deployed account.

| Check | Result |
|---|---|
| Whole night by voice on **real AWS** (console URL, passcode) | "what happened" → brief; "fix it" → `propose_fix` in **1.9 s**; "approve fix 1" → `approve_fix`, Step Functions `SUCCEEDED`, verified on attempt 5 (2 m 35 s), incident `resolved`, alarm back to OK; contract read-back; "grant contract for seven days" → granted. No Bedrock involved: triage ran in the deterministic degraded mode (`model_unavailable` in the timeline), the voice path is AssemblyAI end to end. |
| **Barge-in withdraws the fix** | Interrupting the read-back after `propose_fix` calls `cancel_proposal`; `approve fix 1` afterwards is refused with *"fix 1 was withdrawn because you interrupted the read-back; nothing was applied"*; "fix it" proposes fix 2; approval of fix 2 executes. The interrupted bubble is marked and a console note explains the withdrawal. |
| **Hinglish by audio** | Spoken "kya hua hai" / "isko fix kar do" transcribe as Devanagari (`क्या हुआ है?`, `इसको फिक्स कर दो।`); the agent understands them, calls `propose_fix`, and answers in Hinglish ("U S east 1 mein web service R D S se connect nahi kar paa rahi hai. Security group ka rule missing hai"). Spoken "approve fix five" is heard as *"Approve fix five."* and the server's phrase check accepts it. Voice `jane` pronounces the Hinglish well enough that pre-recorded STT re-transcribes it as Hindi words. |
| **Latency** (turn end → first agent audio) | 0.1–0.4 s when no tool is needed; a tool turn starts speaking in ~0.2 s, calls the tool 1–2 s in, tool round trip 40–200 ms (Lambda), and the read-back reply begins ~2 s after `tool.result`. The console shows these live (latency strip in the Talk panel). |
| **Agent audio field** | `reply.audio` carries the PCM in `data`, not `audio`; the transport read the wrong field until this run (silent agent). Fixed; the loop driver now fails loudly if no audio bytes arrive. |
| Client pings | The `websockets` library's keepalive pings went unanswered mid-reply and closed the socket (`1011 keepalive ping timeout`); browsers do not ping, so the audio harness runs with `ping_interval=None`. |
| Prompt adherence | With trigger phrases in the tool descriptions (`propose_fix`, `grant_sleep_contract`) the managed LLM calls tools without asking permission in every run of the loop; one earlier run asked the engineer to say the phrase instead of issuing the read-back first. |

| **Drop-safety** (automated, `scripts/dev/assemblyai_audio.py … "approve fix {fix}" "!drop"`) | The approval is spoken, the socket is aborted before `tool.call` arrives: nothing executes, no approval row exists, the incident stays `awaiting_engineer`. Execution is a Lambda call the browser makes after `tool.call`, so a dead socket cannot approve anything. |
| `session.resume` | Documented as `session.resume { session_id }` within 30 s with a fresh temp token. Every combination tried (fresh token, `resume_token` as the credential, API-key header) was refused with `unauthorized: Resume credential is invalid for this session` or `session_not_found`. The transport tries once and then starts a fresh session with the same config (the brief is in the system prompt, so nothing is lost but the greeting); the console shows a note. |

Backend switch: the Talk panel has an *AssemblyAI · full duplex / AWS cascade · push to talk* toggle, remembered per browser, for the side-by-side.

## Day by day

| Day | Work | Done when |
|---|---|---|
| **Mon 21** | Spike: connect with a temp token from the browser, `session.update` with one echo tool, confirm the token path, the exact event names, whether a server-injected context message mid-session is supported (for the "alarm is back to OK" turn), and measure a turn. Add `AssemblyAIKeyParam` + `ssm:GetParameter (WithDecryption)` to `console-template.yaml`. | Decision (Voice Agent vs Realtime STT) written here; `make deploy-console VOICE_BACKEND=assemblyai` works. |
| **Tue 22** | Wire `AssemblyAITransport` into `Talk.tsx` behind `config.voiceBackend`; mic → 24 kHz PCM via the worklet; playback ring buffer with flush on `interrupted`; `AwsCascadeTransport` wrapper for parity. Per-incident **keyterms** from `rca_json` + diagnostics (`sg-…`, alarm name, `approve fix one`, `grant contract for seven days`) sent in `session.update`. | Full loop (brief → evidence → propose → approve → resolved → contract) on the AssemblyAI path against the live AWS backend. |
| **Wed 23** | Behaviours: **barge-in during the read-back** → `interrupt()` + a `cancel_proposal` tool + UI marker; **confidence gate** on approvals (already server-side) with the "repeat the phrase" line; **Hinglish** affirmatives for the read-back and the code-switched grant phrase (`saat din ke liye contract do`); **agent-initiated turn** when the watcher sees `resolved` (context injection if supported, else a Polly one-liner + chime); `grant_sleep_contract(until: "till Monday")` natural-duration parsing (still behind the read-back); **drop-safety** test: close the socket mid-approval, assert nothing executed. | Each behaviour has a test and a rehearsed script line. |
| **Thu 24** | Latency overlay (measured per turn: cascade vs Voice Agent); replay mode for the AssemblyAI path; three end-to-end runs; merge `assemblyai` → `main` behind the flag; redeploy. | `?voice=assemblyai` and `?voice=aws` both work on the same CloudFront URL. |
| **Fri 25** | Record raw footage: interruption, Hinglish approval, mumbled approval refused then clear approval executes, contract "till Monday", drop-safety, agent-initiated recovery line, latency overlay. Slide deck (10 slides) and cover image. | Raw clips + deck v1 + cover. |
| **Sat 26** | Edit the 3-minute video; descriptions and tags; deck polish. | Video uploaded (unlisted). |
| **Sun 27** | Submit v1 early; ask a mentor in Discord for feedback. | Submission confirmed. |
| **Mon 28 – Tue 29** | Fixes, re-cut, final submit by Tue 29 evening (deadline Wed 30 20:30 IST). | Final. |

## What is new for this hackathon (the originality story)

1. **Consent from the ASR transcript, gated on confidence.** The model's tool argument is ignored; the server checks what Universal-3 Pro actually heard and refuses a mumbled approval.
2. **Barge-in cancels a pending fix.** Interrupting the read-back withdraws the proposal — turn-taking with a safety meaning.
3. **Sleep Contracts granted by voice**, in English or Hinglish, with the quote stored on every approval they later produce.
4. **Agent-initiated turn**: Beacon speaks first when CloudWatch says the alarm cleared.
5. **Drop-safety**: kill the socket mid-approval and nothing executes, because execution is a DynamoDB record plus a Step Functions run, not a socket state.

## Submission checklist

Title *Beacon Night Shift: the on-call agent you can interrupt* · short + long description (from `docs/submission.md` plus this page) · tags (Voice Agent API, Universal-3 Pro, tool calling; DevOps, incident response, SRE) · cover image `docs/assets/cover.png` (1280×720 from the Night Board) · video (YouTube) · slides (`.pptx` + PDF) · repo (`main`, tag `v0.3.0`) · platform: Web · application URL: the CloudFront URL with `?voice=assemblyai` · judge passcode in the description.

## Repo strategy

`v0.2.0` = First Commit submission (immutable). Branch `assemblyai` from it (this branch). Merge to `main` on 24 Sep behind `VOICE_BACKEND` / `config.voiceBackend`; `?voice=aws` reproduces the First Commit experience exactly; images re-tagged with the new SHA and the deploy guard enforced. If the demo stack is torn down for cost, replay mode keeps both URLs meaningful.
