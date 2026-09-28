# The phone channel

Beacon answers a telephone call with the same agent the console talks to. The caller
hears the AssemblyAI Voice Agent API; the tools run on the same Lambda, under the
same consent rules, and the call is recorded in a form that proves who authorised
what.

This is the channel that matters at 3 AM. A laptop needs finding, unlocking, a
network and a browser. A phone rings in the dark and you answer it.

```
  carrier  ──── 8 kHz mu-law ────►  bridge  ──── 24 kHz PCM ────►  AssemblyAI
 (a phone)  ◄─── over a socket ───  (this)   ◄─── Voice Agent ───   nine tools
                                      │
                                      └── POST /tools/<name> ──►  voice Lambda
                                                                  (consent, IAM,
                                                                   DynamoDB, SFN)
```

## Why not Amazon Connect

Connect is still in the tree and still wired into triage (`beacon/caller.py`,
called from `handler.py`), and it keeps the job it is good at: **placing the
outbound call that wakes the human.**

It is not the media path, for a specific reason. Connect will stream a caller's
audio *out* to Kinesis Video Streams, but it will not accept arbitrary audio *in* —
a contact flow can play a Polly prompt or an S3 file and nothing else. A voice agent
needs both directions on the same call, so with Connect alone the caller could be
heard and never answered. Full duplex needs a SIP media leg (Chime SDK Voice
Connector) or a carrier that streams media over a WebSocket in both directions.

There is a second, duller reason: outbound calling to Indian mobile numbers needs an
AWS service-limit approval, and this account is still inside the new-account
verification hold that also blocks Bedrock and CloudFront. `aws connect
list-instances` returns an empty list.

## Why a process and not a Lambda

A call is stateful, full duplex and minutes long, with two sockets open at once.
Lambda bills wall-clock time and cannot hold both. So the bridge is a small
always-on service and the Lambda stays the only place a tool is allowed to run —
which is also the security property: **a new channel must not become a new way
around the rules.** The bridge holds no IAM role, touches no table, and decides
nothing. It posts a tool name and the transcript AssemblyAI produced.

## Two rules the phone adds

**A keypress is never consent.** DTMF can acknowledge (`1`), ask for the brief
again (`2`), repeat the last sentence (`0`) or hang up (`9`). It can never approve,
undo, grant a contract or open a pull request. Pressing any other key makes the
agent say so. The reason is the artifact: an approval has to be words somebody can
be held to, and `5` is not a sentence.

**The caller's channel is the only consent channel.** Only the caller's audio is
ever sent to `input.audio`, so the transcript the Lambda checks cannot contain the
agent reading a phrase back to itself. See below for the artifact that proves it
after the fact.

## Two channels, so the approval is attributable

The agent reads the approval phrase back — *"say exactly: approve fix one"* — so the
phrase is in the recording twice, once from each party. A single-channel recording
cannot say which occurrence authorised the change.

So the bridge records the caller on the left channel and the agent on the right, and
afterwards AssemblyAI transcribes it with `dual_channel`, which labels every
utterance by channel without guessing at speakers. `beacon/phone/attest.py` then
checks each consent tool that ran against the caller's channel:

```
who authorised what (channel 1 = caller, 2 = agent):
  ok   approve_fix: caller said it 1x, agent 2x — 'Approve fix one'
```

A change whose phrase appears **only** on the agent's channel fails the check. That
is the failure this exists to catch: the agent talking itself into a change.

## Running it

### A call with no carrier

Five scripted calls, real speech at telephone quality, real AssemblyAI, real tools:

```bash
make local                                  # in another shell
make phone-test                             # every scenario, on a fresh incident each
make phone-call SCENARIO=barge_in           # just one
```

Each run leaves a two-channel WAV and a JSON report in `~/beacon-video/phone/`.
Only the carrier is simulated: the caller's lines are rendered by Polly at 8 kHz and
converted to mu-law, so the agent hears exactly what a phone line would have
carried.

| scenario | what it has to prove |
|---|---|
| `approve` | cause, proposal, the exact phrase, applied, verified |
| `wrong_phrase` | "yes, do it" changes nothing |
| `barge_in` | talking over the read-back withdraws the fix, and the phrase then fails |
| `hinglish` | the same tools from Hinglish, which is how the phone gets used here |
| `keypad` | `1` acknowledges; `5` is told that a keypress cannot approve |

### A real number

```bash
export TWILIO_ACCOUNT_SID=… TWILIO_AUTH_TOKEN=… TWILIO_FROM_NUMBER=+1…
python -m beacon.phone serve \
    --base-url "$VOICE_URL" --passcode "$PASSCODE" \
    --public-url https://beacon.example --recordings ~/calls
```

Then either direction works:

* **inbound** — point the number's voice webhook at `https://beacon.example/twiml`
  and call Beacon back. No extra code.
* **outbound** — `python -m beacon.phone dial --to +91… --twiml-url
  https://beacon.example/twiml`, which is what `beacon/caller.py` does through
  Connect when an instance exists.

Twilio requires `wss://`, so the process runs behind TLS: a Fargate task behind an
ALB in production, or this process plus a tunnel during a demo.

## The audio path

`beacon/phone/codec.py` is the whole conversion, in the standard library
(`audioop` did it in one line until Python 3.13 removed it):

* **G.711 mu-law ↔ PCM16**, by lookup table — 8 bits logarithmic, so the round trip
  stays inside 2 % of full scale.
* **8 kHz → 24 kHz** by linear interpolation. The ratio is exactly 3.
* **24 kHz → 8 kHz** through a 19-tap Hamming-windowed sinc cutting at 3.4 kHz,
  then decimation. Averaging three samples instead would be cheaper and lose 2.5 dB
  at the top of the phone band; worse, without a low-pass anything above 4 kHz in
  the agent's voice folds back as a whistle on the line. Measured rejection is 28 dB
  at 5 kHz and 55 dB at 9 kHz, at about 47× real time in Python.

## What the simulated leg models, and what it does not

It streams **continuously**, fifty frames a second, silence included, because a
carrier does — and an agent whose input stops between sentences cannot tell a pause
from a hang-up. It models the carrier's **jitter buffer**: the API generates a
sentence faster than it takes to say it, so the audio is treated as playing at
speaking speed, and the caller waits for it to finish. Barge-in drops the buffer,
which is what Twilio's `clear` does.

It does not model packet loss, jitter, echo or a GSM codec on top of G.711. A real
call is worse than this, which is why the phone brief tells the agent to say the
approval phrase twice and slowly the first time.
