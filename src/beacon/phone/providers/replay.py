"""A telephone call with no telephone: scripted audio in, the agent's audio out.

This is the adapter that makes the phone path testable. The caller's lines are
rendered ahead of time (Polly at 8 kHz, in ``beacon.phone.__main__``) and converted
to mu-law -- so they are degraded to exactly what a phone line carries -- then fed
in at real-time pace, one line per turn. Everything downstream is the production
path: the same socket, the same session brief, the same tools against the same
Lambda, the same consent check. Only the carrier is simulated.

The leg streams *continuously*, fifty frames a second, silence included. That is not
a detail: a carrier sends frames whether or not anybody is speaking, and an agent
whose input stops between sentences cannot tell a pause from a hang-up. Getting this
wrong the first time produced a call where the agent greeted an engineer who, as far
as the API could tell, was never on the line.

Turn taking waits for a reply to *begin* and then to finish, not merely for the line
to be quiet. Quiet is the state immediately after the caller stops talking, so a leg
that only waits for quiet sends its next line straight into the agent's pending reply
and cancels it -- which is how the first version of this produced a call where the
caller said four things and the agent answered none of them.

A line can be marked ``interrupt`` to speak *over* the agent, which is how barge-in
is proved without a human holding a phone.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from beacon.phone import codec

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

# How long the agent must have been talking before an interrupting line cuts in, so
# the interruption is plainly one and not a collision at the start of a turn.
_INTO_THE_REPLY = 0.45
# The agent counts as "still talking" while audio arrived this recently.
_TALKING_WINDOW = 0.25


@dataclass
class Line:
    """One thing the caller does: say something, or press a key.

    ``render`` defers the audio to the moment the line is due, which is how a line
    can name something the agent has not said yet. The fix number is the case that
    forces it: proposals are numbered per incident, so a script that always says
    "approve fix one" is wrong the moment an incident has had an earlier proposal --
    and it fails as a refused approval, which looks exactly like a real bug.
    """

    ulaw: bytes = b""
    label: str = ""
    interrupt: bool = False
    dtmf: str = ""
    render: Callable[[], bytes] | None = None


class ReplayLeg:
    """A ``Leg`` whose caller is a script.

    Turn taking is driven by the agent's own audio: the next line goes out once the
    agent has been quiet for ``quiet_ms``, which is what a person waiting for a
    sentence to finish would do. Patience is bounded -- after ``patience_s`` of
    silence the caller speaks anyway, rather than holding a dead line forever.
    """

    def __init__(
        self,
        lines: list[Line],
        *,
        quiet_ms: int = 900,
        lead_ms: int = 700,
        patience_s: float = 12.0,
        tail_s: float = 1.2,
        max_seconds: float = 180.0,
        caller: str = "+91 replay",
    ) -> None:
        self.lines = lines
        self.quiet_ms = quiet_ms
        self.lead_ms = lead_ms
        self.patience_s = patience_s
        self.tail_s = tail_s
        self.max_seconds = max_seconds
        self.caller = caller
        self.cleared = 0
        self.spoken: list[str] = []
        # When each line went out, and what the agent was doing at that moment. An
        # utterance the API never turned into a turn is invisible from our side
        # otherwise: it is in the recording and in no event we logged.
        self.timeline: list[dict[str, Any]] = []
        # When the audio handed to us will have finished playing. A carrier holds a
        # jitter buffer and plays at speaking speed; without modelling that, the
        # caller's next line lands while the agent is still mid-sentence.
        self._play_until = 0.0
        self._reply_started = 0.0
        self._ever_played = False
        # Replies the agent has finished, as the protocol reports them. Counting
        # them is exact where waiting for a gap in the audio is a guess -- and the
        # guess is wrong precisely on the long read-back that carries the phrase.
        self._replies_done = 0
        self._done_after: int | None = None
        # When the caller last finished a line, and therefore what the agent owes a
        # reply to. ``None`` means nothing is outstanding.
        self._spoke_at: float | None = None
        self._started = time.monotonic()
        self._hung_up = False

    # -- Leg ---------------------------------------------------------------

    async def inbound(self) -> AsyncIterator[bytes]:
        silence = codec.silence_ulaw(codec.FRAME_MS)
        # A cold start clips the first word: turn detection wants a moment of line
        # noise before speech, exactly as a real call has.
        queued = list(codec.frames(codec.silence_ulaw(self.lead_ms)))
        pending = list(self.lines)
        waiting_since = time.monotonic()
        finished_at = 0.0

        while not self._hung_up and not self._expired():
            if queued:
                yield queued.pop(0)
            elif pending and self._ready(pending[0], waiting_since):
                line = pending.pop(0)
                waiting_since = time.monotonic()
                self.timeline.append(
                    {
                        "line": line.label or line.dtmf,
                        "at": round(time.monotonic() - self._started, 2),
                        "agent_talking": self._talking(),
                        "agent_quiet_for": round(self._quiet_for(), 2),
                        "replies_done": self._replies_done,
                    }
                )
                if line.dtmf:
                    self.spoken.append(f"[{line.dtmf}]")
                    yield b"DTMF:" + line.dtmf.encode()
                    self._spoke_at = time.monotonic()
                    self._done_after = self._replies_done
                    # Whatever the agent was saying belongs to the last turn. Without
                    # this, audio arriving back to back across two turns counts as one
                    # burst, "has the agent replied to *this*?" stays false, and the
                    # leg waits for reply.done instead — which is where the dropped
                    # utterances were coming from.
                    self._reply_started = 0.0
                else:
                    audio = line.ulaw
                    if line.render is not None:
                        # Rendering takes a moment; keep the line alive meanwhile,
                        # because a carrier never stops sending frames.
                        task = asyncio.create_task(asyncio.to_thread(line.render))
                        while not task.done():
                            yield silence
                            await asyncio.sleep(codec.FRAME_MS / 1000)
                        audio = task.result()
                    self.spoken.append(line.label or "(audio)")
                    queued = list(codec.frames(audio))
                    # When the line *starts*, not when it finishes streaming. Turn
                    # detection often fires before the last frame is sent, so the
                    # agent's reply begins mid-line; dating the line from its end
                    # made "has the agent replied to this?" permanently false, and
                    # the leg fell back to waiting for reply.done — which arrives
                    # seconds after the audio stops. Lines spoken into that gap are
                    # audible in the recording and never become a turn.
                    self._spoke_at = time.monotonic()
                    self._done_after = self._replies_done
                    # Whatever the agent was saying belongs to the last turn. Without
                    # this, audio arriving back to back across two turns counts as one
                    # burst, "has the agent replied to *this*?" stays false, and the
                    # leg waits for reply.done instead — which is where the dropped
                    # utterances were coming from.
                    self._reply_started = 0.0
                finished_at = 0.0
            else:
                # The line stays open between lines: a carrier keeps sending frames
                # whether or not anyone is speaking.
                yield silence
                if not pending:
                    finished_at = finished_at or time.monotonic()
                    if self._settled(finished_at):
                        return
            await asyncio.sleep(codec.FRAME_MS / 1000)

    async def play(self, ulaw: bytes) -> None:
        now = time.monotonic()
        if self._play_until < now - _TALKING_WINDOW:
            self._reply_started = now
        self._play_until = max(now, self._play_until) + len(ulaw) / codec.PHONE_RATE
        self._ever_played = True

    async def clear(self) -> None:
        """Barge-in: a carrier drops what it has buffered, so the agent stops now."""
        self.cleared += 1
        self._play_until = time.monotonic()

    def reply_done(self, interrupted: bool) -> None:
        """The agent finished a turn -- said by the API rather than guessed.

        An *interrupted* reply does not count. It was cut off, so the caller has not
        been answered, and treating it as an answer sends the next line into the
        reply the agent is still trying to give. That is the same mistake as
        inferring the end of a turn from a gap in the audio, arrived at differently.
        """
        if not interrupted:
            self._replies_done += 1

    async def hangup(self) -> None:
        self._hung_up = True

    # -- turn taking -------------------------------------------------------

    def _expired(self) -> bool:
        return time.monotonic() - self._started > self.max_seconds

    def _quiet_for(self) -> float:
        """Seconds since the agent stopped being audible; negative while it talks."""
        return time.monotonic() - self._play_until if self._ever_played else 0.0

    def _talking(self) -> bool:
        return self._ever_played and self._quiet_for() < _TALKING_WINDOW

    def _answered(self) -> bool:
        """Has the agent finished a reply to what the caller last said?

        Without this the next line goes out into a reply that has not begun yet, the
        API treats it as an interruption, and the answer the caller was waiting for
        is cancelled. ``reply.done`` is the exact signal; the audio-gap test below is
        the fallback for a turn that produced no spoken reply at all.
        """
        if self._spoke_at is None:
            return False
        # Deliberately *not* reply.done. That event can arrive before the agent has
        # said anything — on a turn that called a tool it fires while the spoken
        # answer is still coming — so treating it as "answered" let the caller speak
        # at the exact moment the agent began. Measured on a failing run: the two
        # lines the API never turned into turns were spoken 6.7 s and 7.8 s after
        # the previous audio, which was the *greeting*; the reply to the line before
        # them had not started yet. The audio is the honest signal, and
        # `_patience_gone()` is the escape hatch for a turn that produces none.
        return self._reply_started > self._spoke_at and not self._talking()

    def _patience_gone(self) -> bool:
        since = self._spoke_at if self._spoke_at is not None else self._started
        return time.monotonic() - since > self.patience_s

    def _ready(self, line: Line, waiting_since: float) -> bool:
        """Is it this line's moment?"""
        if line.dtmf:
            # People press keys while the prompt is still talking.
            return True
        if line.interrupt:
            return self._talking() and (
                time.monotonic() - self._reply_started > _INTO_THE_REPLY
            )
        if self._spoke_at is None:
            # Nothing said yet: wait out the greeting, or the patience bound if the
            # agent never speaks at all.
            if not self._ever_played:
                return time.monotonic() - waiting_since > self.patience_s
            return self._quiet_for() * 1000 >= self.quiet_ms
        if not self._answered():
            return self._patience_gone()
        return not self._talking() and self._quiet_for() * 1000 >= self.quiet_ms

    def _settled(self, finished_at: float) -> bool:
        """Nothing left to say: hang up once the agent's last answer has landed."""
        if self._talking():
            return False
        if self._spoke_at is not None and not self._answered():
            return self._patience_gone()
        waited = time.monotonic() - finished_at
        if not self._ever_played:
            return waited > self.patience_s
        return waited > self.tail_s and self._quiet_for() > self.tail_s
