"""Record the AssemblyAI night in a real browser, with a real voice going into the microphone.

Chromium accepts a WAV file as the capture device (`--use-file-for-fake-audio-capture`), so the
console hears actual speech: AssemblyAI transcribes it, the tools run, the agent answers, and the
page is recorded exactly as a judge would see it. Nothing is typed.

The engineer's lines are synthesised with Polly (Kajal, en-IN) and laid out on one timeline with
silences between them — including one line deliberately placed *on top of* the read-back, so the
barge-in in the film is a real interruption rather than an edit.

Usage:
    source ~/.beacon-aws/env.sh
    # the track needs boto3 (project venv); the capture needs Playwright (pyenv 3.10)
    .venv/bin/python video/capture_aai.py --url <console> --build-audio-only
    ~/.pyenv/versions/3.10.13/bin/python video/capture_aai.py --url <console> --passcode nightshift

Outputs `<out>/aai-night.webm` (1920×1080) and `<out>/aai-night.json` (DOM and socket milestones
with timestamps, so the cut points in assembly are derived rather than eyeballed).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
import wave
from pathlib import Path

RATE = 48000  # what Chromium expects from the fake capture file

# (text, seconds of silence before this line). Gaps are tuned to the live account: propose_fix
# lands ~7 s after the words, the read-back runs ~15 s, the verify loop about two and a half
# minutes. The third line is timed to land *during* the read-back.
PLANS = {
    "default": [
        ("what happened", 6),
        ("fix it", 26),
        ("no, wait, stop", 13),
        ("okay, fix it again", 16),
        ("approve fix two", 30),
        ("yes", 34),
        ("grant contract for seven days", 26),
        ("open the pull request", 150),
    ],
    "hinglish": [
        ("kya hua hai", 6),
        ("isko fix kar do", 26),
        ("approve fix one", 32),
        ("haan", 34),
        ("saat din ke liye contract do", 26),
    ],
    # the contract beat on its own: an en-US voice, because Polly's en-IN accent routes
    # "grant contract for seven days" to Hindi when language_codes includes hi
    "contract": [
        ("what happened", 6),
        ("fix it", 24),
        ("approve fix one", 30),
        ("yes", 34),
        ("grant contract for seven days", 26),
    ],
    # the grant is two phrases: the first returns the read-back, the second grants it
    "contract2": [
        ("fix it", 8),
        ("approve fix one", 30),
        ("yes", 34),
        ("grant contract for seven days", 26),
        ("grant contract for seven days", 34),
    ],
    "short": [
        ("what happened", 6),
        ("fix it", 26),
        ("no, wait, stop", 13),
    ],
}


def say(polly, text: str, voice: str = "Kajal") -> bytes:
    """One line as 16-bit PCM at RATE (Polly gives 16 kHz; ffmpeg resamples)."""
    pcm = polly.synthesize_speech(
        Text=text, OutputFormat="pcm", SampleRate="16000", VoiceId=voice, Engine="neural"
    )["AudioStream"].read()
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", "16000",
         "-ac", "1", "-i", "pipe:0", "-ar", str(RATE), "-ac", "1", "-f", "s16le", "pipe:1"],
        input=pcm, capture_output=True, check=True,
    ).stdout


def build_audio(plan: list[tuple[str, int]], path: Path, voice: str = "Kajal") -> list[tuple[float, str]]:
    """Write the microphone track; return (start seconds, text) for the log."""
    import boto3  # only needed when the track is (re)built

    polly = boto3.client("polly")
    track = bytearray()
    marks: list[tuple[float, str]] = []
    for text, gap in plan:
        track += b"\x00\x00" * int(RATE * gap)
        marks.append((len(track) / 2 / RATE, text))
        track += say(polly, text, voice)
    track += b"\x00\x00" * (RATE * 25)  # tail: let the last reply finish
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(track))
    return marks


async def capture(url: str, passcode: str, wav: Path, out: Path,
                  marks: list[tuple[float, str]], extra_seconds: float) -> None:
    from playwright.async_api import async_playwright  # only needed for the capture half

    events: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-audio-capture={wav}%noloop",
            "--autoplay-policy=no-user-gesture-required",
            "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        ])
        ctx = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            permissions=["microphone"],
            record_video_dir=str(out / "_video"),
            record_video_size={"width": 1920, "height": 1080},
        )
        page = await ctx.new_page()
        t0 = time.time()

        def log(kind: str, detail: str = "") -> None:
            events.append({"t": round(time.time() - t0, 2), "kind": kind, "detail": detail[:200]})
            print(f"{time.time() - t0:6.1f}  {kind:14} {detail[:90]}", flush=True)

        def on_ws(ws):
            def rx(frame):
                try:
                    msg = json.loads(frame)
                except Exception:
                    return
                kind = msg.get("type")
                if kind == "transcript.user":
                    log("heard", str(msg.get("text", "")))
                elif kind == "transcript.agent":
                    log("agent", str(msg.get("text", "")))
                elif kind == "tool.call":
                    log("tool", str(msg.get("name", "")))
                elif kind == "reply.done":
                    log("reply.done", str(msg.get("status", "")))
                elif kind == "session.error":
                    log("ERROR", json.dumps(msg)[:150])
            ws.on("framereceived", rx)

        page.on("websocket", on_ws)
        await page.goto(f"{url.rstrip('/')}/#board")
        await page.wait_for_timeout(4000)
        try:
            await page.fill("#passcode", passcode, timeout=8000)
            await page.click("text=Unlock voice")
            log("unlocked")
        except Exception:
            log("no-passcode-box")
        await page.wait_for_timeout(1200)
        await page.click("[aria-label='Connect']")
        log("connect")

        deadline = time.time() + (marks[-1][0] if marks else 60) + extra_seconds
        seen: set[str] = set()
        while time.time() < deadline:
            await asyncio.sleep(1.0)
            for sel, name in (
                (".bubble.beacon.note", "console-note"),
                (".bubble.interrupted", "interrupted"),
                (".latency", "latency-strip"),
                (".pill.green", "resolved-pill"),
            ):
                if name not in seen and await page.locator(sel).count():
                    seen.add(name)
                    log(name)

        video = await page.video.path()
        await ctx.close()
        await browser.close()
        dest = out / "aai-night.webm"
        Path(video).replace(dest)
        (out / "aai-night.json").write_text(json.dumps(
            {"marks": [{"t": t, "said": s} for t, s in marks], "events": events}, indent=1))
        print(f"\n{dest}  ({dest.stat().st_size // 1048576} MB)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--passcode", default="nightshift")
    ap.add_argument("--out", default=str(Path.home() / "beacon-video" / "assets"))
    ap.add_argument("--plan", default="default", choices=sorted(PLANS))
    ap.add_argument("--build-audio-only", action="store_true")
    ap.add_argument("--rebuild-audio", action="store_true")
    ap.add_argument("--voice", default="Kajal", help="Polly voice for the engineer's lines")
    ap.add_argument("--tail", type=float, default=45.0, help="seconds to keep recording after the last line")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    wav = out / f"mic-{a.plan}-{a.voice.lower()}.wav"
    marks_file = out / f"mic-{a.plan}-{a.voice.lower()}.json"
    if wav.exists() and marks_file.exists() and not a.rebuild_audio:
        marks = [(m["t"], m["said"]) for m in json.loads(marks_file.read_text())]
    else:
        marks = build_audio(PLANS[a.plan], wav, a.voice)
        marks_file.write_text(json.dumps([{"t": t, "said": s} for t, s in marks], indent=1))
    print(f"microphone track: {wav} ({wav.stat().st_size // 1024} KB)")
    for t, s in marks:
        print(f"  {t:6.1f}s  {s}")
    if a.build_audio_only:
        return
    asyncio.run(capture(a.url, a.passcode, wav, out, marks, a.tail))


if __name__ == "__main__":
    main()
