# Measured, not asserted

Generated 2026-09-28 20:45 UTC from 5 real call(s) by `make bench`. Every number below is read out of a report written while the call was happening — none of it is typed in by hand.

## Time to first audio

Turn end to the first byte of the agent's reply, as the caller hears it. Two things sit inside this number that a bare STT benchmark would not have: the agent's own turn detection deciding the caller has stopped, and — on a turn that calls a tool — the round trip to AWS.

| | fastest | median | slowest | turns |
|---|---|---|---|---|
| Every turn | 712 ms | **3450 ms** | 4067 ms | 15 |
| … answered without a tool | 777 ms | **2604 ms** | 2609 ms | 3 |
| … answered after a tool | 712 ms | **3606 ms** | 4067 ms | 9 |

Median **3450 ms** over 15 turns — above AssemblyAI's own 1.5 s guidance for a voice agent, and worth being plain about why rather than quoting a friendlier number.

Two deliberate choices are inside it. Turn detection on the phone waits `min_silence` **700 ms** before deciding the caller has finished, because a half-asleep engineer pauses mid-sentence and being cut off at 3 AM is worse than waiting; that 700 ms is part of every number above. And a turn that calls a tool carries the AWS round trip, because a fix is dry-run under a locked-down role *before* the agent is allowed to read its blast radius back. We would rather the engineer wait than be read a blast radius nothing checked.

The honest read: the transcription is not the slow part, and the parts that are slow are ours to fix — the dry run could start speculatively while the agent speaks, which is the obvious next move and is not built yet.

The split covers the 4 of 5 calls where replies pair one-to-one with turns. On the rest a turn ended without an answer — “Wait.” then “Stop.” is two turns and one reply — so pairing them would be guesswork.

Tool dispatch itself is 44–249 ms (median 60 ms) against the local stack; on the deployed account the same call carries Lambda and the dry run with it.

## The calls

| Call | length | turns | tools called |
|---|---|---|---|
| `approve` | 68 s | 4 | `get_evidence`, `propose_fix`, `approve_fix`, `check_recovery` |
| `wrong_phrase` | 44 s | 2 | `propose_fix` |
| `barge_in` | 52 s | 4 | `propose_fix`, `cancel_proposal`, `approve_fix` |
| `hinglish` | 76 s | 4 | `propose_fix`, `approve_fix`, `check_recovery` |
| `keypad` | 45 s | 2 | `propose_fix` |

## Who authorised what

Every phrase that unlocked a change, checked against the call's own two-channel recording.

| Call | phrase | caller / agent |
|---|---|---|
| `approve` | “Approve fix one” | 1x / 0x |
| `barge_in` | “Approve fix 2” | 1x / 0x |
| `hinglish` | “अप्रूव फिक्स थ्री” | 1x / 0x |

The agent reads the phrase back, so it is in the recording twice — once from each party. Only the caller's channel counts: a phrase found on the agent's channel alone is not consent. The Hinglish call is the interesting row, because the caller's channel came back in Devanagari and it is still the same consent.

*How clearly* the words were heard is a separate question, and the Voice Agent API reports no confidence on a live turn. `POST /sessions/<id>/attest` answers it afterwards by re-transcribing the session's own recording per channel with the pre-recorded model, which does return per-word confidence, and scoring the phrase by its **weakest** word. On the deployed account, a phone approval scored **95%** with `fix` the weakest word, found at 40.1 s on the caller's channel.

## What produced these numbers

| | |
|---|---|
| Commit | `37ea0a3` |
| Transcription | AssemblyAI Voice Agent API, `wss://agents.assemblyai.com/v1/ws` |
| Audio | G.711 µ-law (`audio/pcmu`) in and out, 8 kHz, forwarded untouched |
| Turn detection | `vad_threshold` 0.65, `min_silence` 700 ms, `max_silence` 2200 ms |
| Languages | `en`, `hi` (code-switching; the Hinglish call is in the set) |
| Tools | 9, declared as JSON-Schema functions on the same socket |
| Python | 3.12.13 on Linux |
| Reports | `phone-test-report.json` |

## Reproduce it

```
make local                 # one shell: the whole product, no AWS account
make phone-test            # another: five real calls at telephone quality
make bench                 # regenerate this file from the report
```

`make phone-test` needs `ASSEMBLYAI_API_KEY` and AWS credentials — Polly speaks the caller's lines, everything else is local. The calls are real calls: they go through the Voice Agent API and the real tools, so the numbers move a little run to run.
