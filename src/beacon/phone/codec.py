"""G.711 mu-law and the 8 kHz <-> 24 kHz resampling a phone leg needs.

The telephone network carries 8 kHz mu-law; the AssemblyAI Voice Agent API speaks
16-bit linear PCM at 24 kHz in both directions. Everything between the two lives
here, in the standard library, so it is unit-testable without a carrier, a socket
or a sound card.

The ratio is exactly 3, which keeps both conversions honest: upsampling is linear
interpolation between neighbours, downsampling averages each group of three (a
three-tap box filter -- crude as anti-aliasing goes, but everything above 4 kHz is
already gone by the time audio reaches a phone, and it costs nothing per frame).

``audioop`` did this in one line until Python 3.13 removed it, so the tables are
built here instead.
"""

from __future__ import annotations

import array
import math

PHONE_RATE = 8000
AGENT_RATE = 24000
RATIO = AGENT_RATE // PHONE_RATE
# Twilio sends and expects 20 ms frames: 160 mu-law bytes, 480 PCM samples at 24 kHz.
FRAME_MS = 20
PHONE_FRAME_BYTES = PHONE_RATE * FRAME_MS // 1000

_BIAS = 0x84
_CLIP = 32635


def _encode_one(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample
    sample = min(sample, _CLIP) + _BIAS
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not sample & mask:
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def _decode_one(byte: int) -> int:
    byte = ~byte & 0xFF
    magnitude = (((byte & 0x0F) << 3) + _BIAS) << ((byte & 0x70) >> 4)
    return _BIAS - magnitude if byte & 0x80 else magnitude - _BIAS


# 64 K of lookup for the encoder, 256 entries for the decoder: built once, and the
# hot path becomes a list index per sample.
_ENCODE = bytes(_encode_one(s if s < 0x8000 else s - 0x10000) for s in range(0x10000))
_DECODE = [_decode_one(b) for b in range(256)]


def ulaw_to_pcm(payload: bytes) -> array.array[int]:
    """8 kHz mu-law bytes -> 8 kHz signed 16-bit samples."""
    return array.array("h", [_DECODE[b] for b in payload])


def pcm_to_ulaw(pcm: array.array[int]) -> bytes:
    """Signed 16-bit samples -> mu-law bytes, one per sample."""
    return bytes(_ENCODE[s & 0xFFFF] for s in pcm)


def upsample(pcm: array.array[int], *, ratio: int = RATIO) -> array.array[int]:
    """Linear interpolation by a whole-number ratio (8 kHz -> 24 kHz)."""
    out = array.array("h", bytes(2 * len(pcm) * ratio))
    if not pcm:
        return out
    at = 0
    for i, sample in enumerate(pcm):
        nxt = pcm[i + 1] if i + 1 < len(pcm) else sample
        step = (nxt - sample) / ratio
        for k in range(ratio):
            out[at] = int(sample + step * k)
            at += 1
    return out


# Decimating 24 kHz to 8 kHz has to remove everything above 4 kHz first or it folds
# back as a whistle on the caller's line. A 19-tap Hamming-windowed sinc cutting at
# 3.4 kHz -- the top of the telephone band -- costs 19 multiplies per output sample,
# which at 8000 samples a second is nothing, and keeps the agent's voice clear
# instead of muffled (a plain three-sample average loses 2.5 dB at 3.4 kHz).
def _lowpass(taps: int, cutoff_hz: float, rate: int) -> list[float]:
    middle = (taps - 1) / 2
    ratio = 2 * cutoff_hz / rate
    kernel = []
    for n in range(taps):
        x = n - middle
        sinc = ratio if x == 0 else math.sin(math.pi * ratio * x) / (math.pi * x)
        kernel.append(sinc * (0.54 - 0.46 * math.cos(2 * math.pi * n / (taps - 1))))
    total = sum(kernel)
    return [k / total for k in kernel]


_ANTIALIAS = _lowpass(19, 3400.0, AGENT_RATE)
_HALF = len(_ANTIALIAS) // 2


def downsample(pcm: array.array[int], *, ratio: int = RATIO) -> array.array[int]:
    """Low-pass, then keep every *ratio*-th sample (24 kHz -> 8 kHz).

    Edges are handled by holding the first and last sample, which is what a
    continuous stream would have supplied anyway; frames are short enough that the
    19-tap window spans less than a millisecond.
    """
    n = len(pcm)
    out = array.array("h", bytes(2 * ((n + ratio - 1) // ratio)))
    if n == 0:
        return out
    for i in range(len(out)):
        centre = i * ratio
        acc = 0.0
        for k, weight in enumerate(_ANTIALIAS):
            j = centre + k - _HALF
            acc += weight * pcm[0 if j < 0 else n - 1 if j >= n else j]
        out[i] = max(-32768, min(32767, int(acc)))
    return out


def phone_to_agent(payload: bytes) -> bytes:
    """One mu-law telephony frame -> little-endian PCM16 bytes at 24 kHz."""
    return upsample(ulaw_to_pcm(payload)).tobytes()


def agent_to_phone(pcm24: bytes) -> bytes:
    """PCM16 bytes at 24 kHz -> mu-law bytes at 8 kHz."""
    samples = array.array("h")
    samples.frombytes(pcm24[: len(pcm24) - len(pcm24) % 2])
    return pcm_to_ulaw(downsample(samples))


def silence_ulaw(ms: int) -> bytes:
    """mu-law silence, for the lead-in before a caller's first word."""
    return _ENCODE[0].to_bytes(1, "little") * (PHONE_RATE * ms // 1000)


def frames(payload: bytes, *, size: int = PHONE_FRAME_BYTES) -> list[bytes]:
    """Split a mu-law stream into whole frames, padding the last one with silence."""
    out = [payload[i : i + size] for i in range(0, len(payload), size)]
    if out and len(out[-1]) < size:
        out[-1] = out[-1] + silence_ulaw(FRAME_MS)[: size - len(out[-1])]
    return out
