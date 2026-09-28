"""The telephony codec: a phone call is 8 kHz mu-law and the agent is 24 kHz PCM."""

from __future__ import annotations

import array
import math

from beacon.phone import codec


def tone(
    hz: float, ms: int, rate: int = codec.PHONE_RATE, amp: int = 12000
) -> array.array[int]:
    n = rate * ms // 1000
    return array.array(
        "h", [int(amp * math.sin(2 * math.pi * hz * i / rate)) for i in range(n)]
    )


def rms(samples: array.array[int]) -> float:
    return math.sqrt(sum(s * s for s in samples) / max(len(samples), 1))


def test_mu_law_round_trip_keeps_the_waveform_within_its_quantisation_step() -> None:
    original = tone(440, 100)
    back = codec.ulaw_to_pcm(codec.pcm_to_ulaw(original))
    assert len(back) == len(original)
    # mu-law is 8 bits logarithmic: a couple of percent of full scale, not more.
    assert max(abs(a - b) for a, b in zip(original, back, strict=True)) < 0.03 * 32768


def test_silence_stays_silent_through_both_conversions() -> None:
    quiet = codec.silence_ulaw(20)
    assert len(quiet) == codec.PHONE_FRAME_BYTES
    assert max(abs(s) for s in codec.ulaw_to_pcm(quiet)) <= 8


def test_a_phone_frame_becomes_exactly_one_agent_frame() -> None:
    frame = codec.pcm_to_ulaw(tone(1000, codec.FRAME_MS))
    assert len(frame) == codec.PHONE_FRAME_BYTES
    # 20 ms at 24 kHz, 2 bytes a sample
    assert len(codec.phone_to_agent(frame)) == 480 * 2
    back = codec.agent_to_phone(codec.phone_to_agent(frame))
    assert len(back) == codec.PHONE_FRAME_BYTES


def test_speech_band_survives_the_trip_to_the_agent_and_back() -> None:
    for hz in (300, 1000, 2000):
        original = tone(hz, 500)
        there_and_back = codec.downsample(codec.upsample(original))
        loss_db = 20 * math.log10(rms(there_and_back) / rms(original))
        assert loss_db > -3.0, f"{hz} Hz lost {loss_db:.1f} dB"


def test_decimation_rejects_what_would_otherwise_fold_into_the_phone_band() -> None:
    """Without a low-pass, a 5 kHz tone in the agent's voice reappears as a whistle."""
    above_the_band = tone(5000, 500, rate=codec.AGENT_RATE)
    folded = max(rms(codec.downsample(above_the_band)), 1e-9)
    rejected_db = 20 * math.log10(rms(above_the_band) / folded)
    assert rejected_db > 20


def test_frames_are_whole_and_the_last_one_is_padded_not_truncated() -> None:
    payload = codec.pcm_to_ulaw(tone(800, 55))  # 2.75 frames
    split = codec.frames(payload)
    assert len(split) == 3
    assert {len(f) for f in split} == {codec.PHONE_FRAME_BYTES}
    assert b"".join(split)[: len(payload)] == payload


def test_the_codec_is_fast_enough_for_a_live_call() -> None:
    """A call is real time: two seconds of audio must convert in far less than two."""
    import time

    two_seconds = tone(700, 2000, rate=codec.AGENT_RATE)
    start = time.perf_counter()
    codec.downsample(two_seconds)
    assert time.perf_counter() - start < 0.5
