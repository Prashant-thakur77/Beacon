# Telegram: the page where the engineer actually is

At 03:12 the engineer has a phone, not a laptop. Beacon's page lands in their Telegram
chat with three buttons, and they answer it the way people talk at night: a voice
note, in English or Hinglish. The same eight tools, the same consent rule, the same
audit row — plus a confidence number, because pre-recorded transcription has one.

```
CloudWatch alarm ──► triage ──► channels.send("page") ──► Telegram: 🔴 alarm · cause
                                                              [Talk] [Fix 1] [Ack]
engineer voice note ──► voice Lambda /telegram/webhook ──► getFile ──► AssemblyAI
   "approve fix one"                                       pre-recorded STT (words + confidence)
                                                          ──► route("approve fix one")
                                                          ──► approve_fix(1, "approve fix 1")
                                                              consent checked against the transcript
                                                          ──► Step Functions loop (dry run → execute → verify)
Beacon ──► text + Polly voice note: "Approved. Applying and verifying now…"
loop verifies ──► channels.send("resolved") ──► Telegram: 🟢 resolved · [Open]
```

## What the engineer sees

| Message | From | What it does |
|---|---|---|
| 🔴 `beacon-demo-infra-errors` — *DB unreachable: the ECS→RDS rule is missing* · **Talk · Fix 1 · Ack** | Beacon (page) | *Talk* opens the console on the incident (`#board/<id>`). *Fix 1* runs `propose_fix` and sends the read-back; it never applies. *Ack* records `acknowledged` and stops further pages for this incident. |
| voice note *"isko fix kar do"* | engineer | Transcribed (language detected: Hindi), routed to `propose_fix`; reply = blast radius + *Reply exactly: approve fix 1*, as text and as a voice note. |
| voice note *"approve fix one"* | engineer | Transcribed with word confidence. ≥ 85 %: `approve_fix` runs, the approval row stores the quote, the file id, the confidence and the transcript id. < 85 %: *"I heard 'approve fix one' at 71 % — send it once more, or type it."* Nothing runs. |
| 🟢 resolved · verified on attempt 5 | Beacon (loop) | Posted by the remediation loop through the same `channels.send`. |
| *"handle it next time"* → read-back → *"grant contract for 7 days"* | engineer | Same two-step Sleep Contract as the console: the read-back names the alarm, the action, the days and the uses; only the exact phrase grants it. |
| `/status` `/contracts` `/report` `/use <id>` `/help` | engineer | Tonight's incidents, active contracts, the morning-report link, pick an incident, the phrase list. |

The router is a table of phrases (`telegram.route`), not a model: *fix it / isko fix kar do* → `propose_fix`; *approve fix N* → `approve_fix`; *undo fix N* → `undo_fix`; *what changed* → `get_evidence(changes)`; *is it fixed* → `check_recovery`; *what happened / kya hua* → `get_incident_brief`; *N din ke liye contract* / *grant contract for N days* → `grant_sleep_contract`. Anything else gets the phrase list back — an unrecognised sentence can never apply a fix.

## Why the confidence gate lives here

The Voice Agent API's realtime transcripts carry no confidence field, so the browser path relies on the exact phrase alone. The pre-recorded API returns `words[].confidence`; Beacon takes the **minimum word confidence** of the utterance as the gate (a clear sentence with one mumbled word is still refused), stores it on the approval, and quotes it back when refusing.

## Security

- Telegram calls the voice Lambda's public URL at `POST /telegram/webhook`; the request must carry the `X-Telegram-Bot-Api-Secret-Token` set at `setWebhook` (`TELEGRAM_WEBHOOK_SECRET`). Wrong secret → 401 before the body is read.
- Only user ids in `TELEGRAM_ALLOWED_IDS` are answered; everyone else gets a one-line refusal.
- The bot token lives in SSM (`/beacon/<stack>/telegram-token`, SecureString). Voice notes are **downloaded by the Lambda and uploaded** to AssemblyAI — Telegram file URLs embed the bot token, so they are never handed to a third party.
- The passcode is never typed into Telegram; the allowlist is the identity. Every approval from Telegram is `channel: telegram` with the user id in `attestation`.
- The webhook always answers 200 once the secret matches: Telegram retries anything else, and a retry would re-run a tool.

## Set-up (one engineer, one bot)

1. Talk to `@BotFather` → `/newbot` → copy the token. Send the new bot any message, then open `https://api.telegram.org/bot<token>/getUpdates` and read your numeric `from.id` (that is both your chat id and your user id).
2. ```
   make set-telegram-token TELEGRAM_BOT_TOKEN=<token> TELEGRAM_CHAT_ID=<id> TELEGRAM_ALLOWED_IDS=<id>
   make deploy … && make deploy-remediation … && make deploy-console …     # same flags as before
   make set-telegram-webhook
   ```
   `set-telegram-token` stores the token in SSM and saves `TELEGRAM_*` in `.beacon.env` (webhook secret generated for you); the three deploys pass them to the Lambdas; `set-telegram-webhook` registers the URL and the bot's command menu.
3. `make break-demo` → the page arrives in Telegram within a minute of the alarm. Reply with a voice note.

Local: `make local` with `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ALLOWED_IDS` and `ASSEMBLYAI_API_KEY` in the environment pages your real chat from the moto world; the webhook needs a public URL, so inbound is exercised by `tests/test_telegram.py` (fake Bot API + fake AssemblyAI) and, once deployed, by your phone.

## What is and is not AssemblyAI here

- **In:** AssemblyAI pre-recorded transcription (`/v2/upload` + `/v2/transcript`, language detection, `keyterms_prompt` with the alarm name and the consent phrases) — chosen over the realtime API because a voice note is a file and because this API returns per-word confidence.
- **Out:** Polly (`Kajal`, mp3 sent with `sendVoice`). AssemblyAI's TTS is part of the realtime Voice Agent session and has no asynchronous endpoint; the writeup says so.
