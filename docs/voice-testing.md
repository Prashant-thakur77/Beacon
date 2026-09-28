# Testing a voice agent

Unit tests prove the tools are safe. They cannot prove the part that actually matters at 3 AM:
that **saying** something makes the right tool run, that agreement is not consent, and that
interrupting a read-back really withdraws a fix. Those properties live in speech, turn-taking and
transcription — none of which a mocked test can reach.

So Beacon has a second suite that speaks.

```bash
make local                                   # the whole product against in-process moto
make voice-test                              # six spoken scenarios; ONLY=barge_in runs one
```

Each scenario renders the engineer's lines with Polly, streams them into the **AssemblyAI Voice
Agent API** as microphone audio, lets the managed model decide which tools to call, runs every
`tool.call` against the local Beacon exactly as the browser would, and then asserts on three
things: **what was heard**, **which tools ran**, and **the state the incident ended in**. The exit
code is the number of failures, so it can gate a release.

| Scenario | The property it defends |
|---|---|
| `approve` | the exact phrase applies the fix and the verify loop starts |
| `wrong_phrase` | *"yes, do it"* and *"go ahead please"* change **nothing** — agreement is not consent |
| `barge_in` | speaking over the read-back withdraws the proposal; nothing executes afterwards |
| `hinglish` | *"isko fix kar do"* reaches the same tool as *"fix it"* |
| `contract` | a Sleep Contract needs the read-back **and** then the exact phrase |
| `undo` | an applied fix can be reversed by phrase, and the incident returns to awaiting a human |

The latest run is committed as [`docs/assets/voice-test-report.json`](assets/voice-test-report.json).

## What it caught

Writing the suite was not ceremony — it found real things:

- **The fix number is not always one.** `make local` reuses an open incident inside its dedup
  window, so proposals accumulate and the phrase becomes *"approve fix nine"*. The suite now speaks
  the number the agent actually proposed, which is what a real engineer reads off the card.
- **`/local/break` was not idempotent** — breaking an already-broken demo raised
  `InvalidPermission.NotFound`. Fixed, because every scenario starts by breaking things.
- **A cold start gets clipped.** Speech that begins the instant the queue opens loses its first
  word — *"Contract for 7 days"* instead of *"Grant contract for 7 days"* — and the server then
  correctly refuses the grant. Utterances now carry a short lead-in of silence, except the
  barge-in, which deliberately starts cold on top of the agent.
- **Assertions must be scoped to the scenario**, not the incident: a reused incident still carries
  the previous scenario's `executed` events.

## What it is not

It is not a load test, and it is not deterministic in the way `pytest` is: real speech, a managed
model and turn detection all vary. In practice **five or six of the six pass on any given run**,
and the one that fails is usually a line the ASR misheard — `"approve fix four"` as `"Approved."`,
`"fix it"` as `"Click set."`. The harness already says each line up to three times when the words
it needs do not come back; beyond that, read the `heard` list before suspecting the agent, because
almost every flake is audio delivery rather than logic.

A useful thing it taught us about our own product: a **Sleep Contract granted by one scenario
silently fixed the next scenario's incident** — no phrase, nobody woken, exactly as designed. The
suite now revokes contracts between scenarios, and that failure was the feature proving itself.

The same machinery films the demo: [`video/capture_aai.py`](../video/capture_aai.py) feeds the
identical kind of audio into a real browser, which is why the barge-in in the film is an actual
interruption rather than an edit.

## The phone suite

`make phone-test` is the same idea down a telephone line. Five scripted calls, each
on a fresh incident, each speaking Polly-rendered lines that have been degraded to
8 kHz G.711 µ-law before the agent hears a word — so what is being tested is speech
at the quality a phone actually delivers, not studio audio.

```bash
make local && make phone-test           # all five
.venv/bin/python scripts/phone_test.py --only barge_in
```

| Scenario | The property it defends |
|---|---|
| `approve` | the exact phrase applies the fix and the verify loop starts |
| `wrong_phrase` | *"yes, do it"* changes nothing — agreement is not consent |
| `barge_in` | talking over the read-back withdraws the proposal; the phrase then fails |
| `hinglish` | *"isko fix kar do"* reaches the same tool, transcribed in Devanagari |
| `keypad` | `1` acknowledges, `5` is refused in words, and **nothing is applied** |

Three things this suite does that the browser suite does not:

**It says the number that was actually proposed.** Proposals are numbered per
incident, so a script hard-coding *"approve fix one"* is a refused approval the
moment an incident has had an earlier proposal — and a refused approval looks
exactly like a real bug. The caller's approval line is rendered at the moment it is
due, from the `fix_id` the agent returned.

**It checks the recording, not just the outcome.** Each call is recorded with the
caller on one channel and the agent on the other; AssemblyAI transcribes it with
`dual_channel`, and every consent tool that ran is checked against the caller's
channel. The report prints it:

```
ok approve_fix from the caller's channel (1x; agent 2x)
```

The agent's two occurrences are its read-backs. They are in the recording, and they
are not consent.

**The keypad scenario never speaks the phrase.** The caller presses `1`, says "fix
it", presses `5`, and then asks whether that did it. Anything applied in that call
was applied by a keypress, which is the failure being hunted. The agent's answer, in
a real run: *"A key press cannot approve a change."*
