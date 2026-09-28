# Telegram — the recording script

What this is for: the film currently shows the Telegram thread as a still. This
replaces it with **your phone, your thumb, your voice**, which is the difference
between a claim and evidence. Judges can tell.

Target length on screen: **35–50 seconds**. You will record more than that; the cut
uses the best stretch.

---

## Before you press record

Tell me to run this, and wait for the page to land in the chat:

```
make break-demo && alarm → ALARM        (I run it; ~60 s until the page arrives)
```

Then on your phone:

1. Open Telegram → the **@GoodNightShiftbot** chat.
2. **Delete the old messages** in that chat, or scroll so the thread starts clean.
   A thread full of test messages reads as a test.
3. Turn on **Do Not Disturb** — a WhatsApp banner mid-take kills the shot.
4. Screen brightness up. **Portrait**. Start the phone's screen recorder **with
   microphone off** (we want the bot's voice notes, not room noise).
5. Wait for the page to arrive on camera if you can — a notification landing is a
   much better opening than a thread that is already there.

---

## The conversation

Four beats: **what happened → why → what can we do → do it.** That is the shape of
every real 3 AM conversation, and it is the shape of this one.

Send each line as a **voice note** where marked 🎤 — that is the part only this
project does. Wait for Beacon's reply before sending the next one; a reply is text
**and** a voice note you can play on camera.

| # | You send | Beacon answers with | Film note |
|---|---|---|---|
| 0 | *(nothing — the page arrives)* | The incident card: one line of cause, and the buttons **Talk · Fix 1 · Ack** | Let it sit for 2 s. This is the "you have been woken" beat. |
| 1 | 🎤 **"What happened?"** | The brief: the alarm, the service, and the one-line cause | Play the voice note reply out loud for ~3 s, then stop it |
| 2 | 🎤 **"Why? What changed?"** | The CloudTrail entry — who removed the rule, and when | This is the beat that proves it is not guessing |
| 3 | 🎤 **"What are my options?"** | The proposal: the blast radius in one sentence, and *"say approve fix 1"* | **Nothing has changed yet.** Let the words "approve fix one" be readable |
| 4 | 🎤 **"Approve fix one."** | Applied, and verifying | Hold on the screen while it verifies |
| 5 | 🎤 **"Is it fixed?"** | Recovered, with the checks | The last beat. Stop recording ~2 s after. |

### If you want the Hinglish take as well (worth it — it is a differentiator)

Same four beats, spoken naturally:

| # | You say |
|---|---|
| 1 | 🎤 **"Kya hua hai?"** |
| 2 | 🎤 **"Kyun hua, kya change hua?"** |
| 3 | 🎤 **"Kya kar sakte ho?"** |
| 4 | 🎤 **"Approve fix one."** ← *say this one in English* |

The approval phrase stays English on purpose: it is the phrase the consent check
looks for, and the film says so.

### Optional tail, if the take is going well

| # | You send | Beacon answers with |
|---|---|---|
| 6 | 🎤 **"Handle it yourself next time."** | Offers a Sleep Contract and reads back the exact words needed |
| 7 | 🎤 **"Grant contract for seven days."** | Granted, with your own quote stored as the record |
| 8 | 🎤 **"Open the pull request."** | A GitHub link to the PR restoring the rule in the template |

---

## Things that will go wrong, and what they mean

**"Say exactly: approve fix 1" and nothing applies.** Correct behaviour, not a bug.
Agreement is not consent — *"yes"*, *"do it"* and *"go ahead"* deliberately do
nothing. Say the phrase.

**It answers about fix 2, not fix 1.** Proposals are numbered per incident, and this
incident has had an earlier proposal. **Say the number Beacon just said**, not the
one in this script.

**A mumbled approval is refused with a percentage.** Also correct — that is the
word-confidence gate. Say it again, clearly. It is worth keeping one of these in the
take if it happens naturally; it is a better demonstration than a clean run.

**The fix is already applied before you approve anything.** A Sleep Contract from an
earlier run handled it. Tell me and I will revoke it and re-break.

**No reply at all.** Tell me — I will check the webhook and the Lambda log rather
than have you guess.

---

## What I need back

The raw screen recording, however long. Do not trim it — the cut is easier to make
from a long take, and the bits you think are mistakes are often the best material.

If you have a second phone or a friend, a 3-second shot of **you actually speaking
into the phone in a dark room** is worth more than any screen capture. That is the
opening of the film.
