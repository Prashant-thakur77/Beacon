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
model and turn detection all vary. Treat a single failure as a signal to look, not as a broken
build — and read the `heard` list first, because most flakes are audio delivery, not logic.

The same machinery films the demo: [`video/capture_aai.py`](../video/capture_aai.py) feeds the
identical kind of audio into a real browser, which is why the barge-in in the film is an actual
interruption rather than an edit.
