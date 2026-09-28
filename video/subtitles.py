"""Caption the film with the same API the product runs on.

A judge may watch this muted, on a phone, or in a second language. A four-minute
film with no captions loses marks for reasons that have nothing to do with the work.

The captions are produced by AssemblyAI's pre-recorded transcription — the same API
that reads the engineer's voice notes and re-scores an approval against its own
recording. Using it on our own film is not a gimmick: it is the shortest honest
demonstration that the transcription is good enough to build consent on.

    ~/.pyenv/.../python video/subtitles.py ~/beacon-video/Beacon-AssemblyAI.mp4

Writes `<film>.srt` beside the film. Upload it with the video; do not burn it in,
because a caption a viewer cannot switch off is worse than none.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

# Two short lines read better than one long one at the bottom of a phone screen.
MAX_CHARS = 42
MAX_LINES = 2
MAX_SECONDS = 6.0
# Beacon's own vocabulary, so the captions do not invent spellings for the words the
# film is about.
KEYTERMS = [
    "Beacon",
    "AssemblyAI",
    "CloudWatch",
    "CloudTrail",
    "Step Functions",
    "security group",
    "approve fix one",
    "Sleep Contract",
    "Hinglish",
    "pull request",
    "barge-in",
    "on-call",
    "dry run",
    "blast radius",
]


def audio_of(film: pathlib.Path) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(film),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                tmp.name,
            ],
            check=True,
        )
        return pathlib.Path(tmp.name).read_bytes()


def stamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def wrap(text: str) -> str:
    lines: list[str] = []
    for word in text.split():
        if lines and len(lines[-1]) + 1 + len(word) <= MAX_CHARS:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    return "\n".join(lines[:MAX_LINES])


def cues(words: list[dict]) -> list[tuple[float, float, str]]:
    """Group words into caption-sized cues, breaking at sentence ends."""
    out: list[tuple[float, float, str]] = []
    buf: list[dict] = []

    def flush() -> None:
        if not buf:
            return
        text = wrap(" ".join(str(w.get("text") or "") for w in buf))
        out.append((buf[0]["start"] / 1000, buf[-1]["end"] / 1000, text))
        buf.clear()

    for word in words:
        buf.append(word)
        joined = " ".join(str(w.get("text") or "") for w in buf)
        ends_sentence = joined.rstrip().endswith((".", "?", "!"))
        too_long = len(joined) > MAX_CHARS * MAX_LINES
        too_slow = (word["end"] - buf[0]["start"]) / 1000 > MAX_SECONDS
        if ends_sentence or too_long or too_slow:
            flush()
    flush()
    return out


def main() -> int:
    from beacon import aai

    film = pathlib.Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else pathlib.Path.home() / "beacon-video/Beacon-AssemblyAI.mp4"
    )
    if not film.exists():
        print(f"no film at {film}")
        return 1

    print(f"transcribing {film.name} with AssemblyAI …")
    url = aai.upload(audio_of(film))
    body = {
        "audio_url": url,
        "punctuate": True,
        "format_text": True,
        "language_code": "en",
        "keyterms_prompt": KEYTERMS,
    }
    job = aai._json("POST", f"{aai.API}/transcript", body)  # noqa: SLF001
    import time

    for _ in range(200):
        got = aai._json("GET", f"{aai.API}/transcript/{job['id']}")  # noqa: SLF001
        if got.get("status") == "completed":
            break
        if got.get("status") == "error":
            print(f"transcription failed: {got.get('error')}")
            return 1
        time.sleep(2)
    else:
        print("transcription did not finish in time")
        return 1

    words = [
        w
        for w in got.get("words") or []
        if isinstance(w.get("start"), int) and isinstance(w.get("end"), int)
    ]
    if not words:
        print("no word timings came back")
        return 1

    srt = film.with_suffix(".srt")
    with srt.open("w", encoding="utf-8") as fh:
        for i, (start, end, text) in enumerate(cues(words), 1):
            fh.write(f"{i}\n{stamp(start)} --> {stamp(end)}\n{text}\n\n")
    n = sum(1 for _ in srt.open(encoding="utf-8") if _.strip().isdigit())
    print(f"{srt}  ({n} cues, {len(words)} words, transcript {got.get('id')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
