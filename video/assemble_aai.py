"""Assemble the AssemblyAI cut: scenes + live capture, cut to the narration.

Rows 1–3 and 14–20 are motion graphics (`renders-aai/sNN.webm`); rows 6–13 are segments of the
real browser recordings, chosen by the timestamps `capture_aai.py` logged. Each clip is exactly
as long as its narration, then everything is chained with xfade and the ducked music bed.

    ~/.pyenv/versions/3.10.13/bin/python video/assemble_aai.py ~/beacon-video/Beacon-AssemblyAI.mp4
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path.home() / "beacon-video"
W, H, FPS = 1920, 1080, 30
TMP = ROOT / "build-aai"
TMP.mkdir(exist_ok=True)
out = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "Beacon-AssemblyAI.mp4")

R = ROOT / "renders-aai"
A = ROOT / "assets"
NIGHT = A / "aai-night-full.webm"      # the whole night: brief → barge-in → approve → PR
CONTRACT = A / "aai-contract2.webm"    # the two-phrase Sleep Contract grant
AUDIT = A / "audit.webm"               # the recording played back in the audit


def run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def dur(path: Path, stream: str = "v") -> float:
    """Seconds. Playwright's webm has no stream duration, so fall back to the container
    and then to decoding, which is slow but always right."""
    for args in (["-select_streams", stream, "-show_entries", "stream=duration"],
                 ["-show_entries", "format=duration"]):
        txt = subprocess.check_output(
            ["ffprobe", "-v", "error", *args, "-of", "csv=p=0", str(path)]
        ).decode().strip().splitlines()
        if txt and txt[0] not in ("", "N/A"):
            return float(txt[0])
    txt = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", stream, "-count_packets",
         "-show_entries", "stream=nb_read_packets,avg_frame_rate", "-of", "csv=p=0", str(path)]
    ).decode().strip().split(",")
    num, den = (txt[0].split("/") + ["1"])[:2]
    fps = float(num) / float(den or 1)
    return int(txt[1]) / fps if fps else 0.0


def vo(n: int) -> Path:
    return ROOT / "vo-aai" / f"{n:02d}.wav"


def scene_clip(n: int, secs: float) -> Path:
    p = TMP / f"c{n:02d}.mp4"
    run(["-i", str(R / f"s{n:02d}.webm"), "-t", str(secs),
         "-vf", f"scale={W}:{H},fps={FPS},format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", str(p)])
    return p


def shot(n: int, src: Path, start: float, secs: float, zoom: float = 1.0) -> Path:
    """A segment of a browser recording, optionally punched in, padded by freezing the last frame."""
    p = TMP / f"c{n:02d}.mp4"
    avail = max(0.5, dur(src) - start)
    take = min(secs, avail)
    hold = max(0.0, secs - take)
    vf = f"scale={W}:{H},fps={FPS}"
    if zoom > 1.0:
        cw, ch = int(W / zoom) // 2 * 2, int(H / zoom) // 2 * 2
        vf += f",crop={cw}:{ch}:(iw-{cw})/2:(ih-{ch})/3,scale={W}:{H}"
    if hold > 0:
        vf += f",tpad=stop_mode=clone:stop_duration={hold}"
    vf += ",format=yuv420p"
    run(["-ss", str(start), "-i", str(src), "-t", str(take + hold), "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", str(p)])
    return p


def with_audio(n: int, video: Path, secs: float) -> Path:
    p = TMP / f"a{n:02d}.mp4"
    run(["-i", str(video), "-i", str(vo(n)), "-filter_complex",
         f"[1:a]adelay=400|400,apad=whole_dur={secs}[a]",
         "-map", "0:v", "-map", "[a]", "-t", str(secs),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(p)])
    return p


# row -> builder. Capture offsets come from the milestone logs next to each recording.
BUILD = {
    1:  lambda s: scene_clip(1, s),
    2:  lambda s: scene_clip(2, s),
    3:  lambda s: scene_clip(3, s),
    4:  lambda s: scene_clip(4, s),
    5:  lambda s: scene_clip(5, s),
    6:  lambda s: shot(6, NIGHT, 30.0, s),            # the board and the brief
    7:  lambda s: shot(7, NIGHT, 12.0, s),            # connect, latency strip
    8:  lambda s: shot(8, NIGHT, 45.0, s),            # "fix it" → propose_fix → dry run
    9:  lambda s: shot(9, NIGHT, 58.5, s),            # read-back cut → withdrawn → refused
    10: lambda s: shot(10, NIGHT, 108.0, s),          # "approve fix two" → verify
    11: lambda s: shot(11, CONTRACT, 144.0, s),       # read-back → phrase → granted
    12: lambda s: shot(12, NIGHT, 328.0, s),          # "open the pull request" → PR
    13: lambda s: shot(13, AUDIT, 6.0, s),            # ▶ Listen plays the recording
    14: lambda s: scene_clip(14, s),
    15: lambda s: scene_clip(15, s),
    16: lambda s: scene_clip(16, s),
    17: lambda s: scene_clip(17, s),
    18: lambda s: scene_clip(18, s),
    19: lambda s: scene_clip(19, s),
    20: lambda s: scene_clip(20, s),
    21: lambda s: scene_clip(21, s),          # the 3D voice path
    22: lambda s: scene_clip(22, s),          # India / New York
    23: lambda s: scene_clip(23, s),          # the phone call, two channels
}

# The order the film is cut in, which is not the order the rows were written. The
# mechanism is explained before the demo shows it, and the phone opens the demo
# because it is where the engineer actually is at 3 AM.
ORDER = [1, 2, 22, 3, 21, 23, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13,
         14, 15, 16, 17, 18, 19, 20]
# the live-capture rows hold after the narration so the UI is readable
TAILS = {1: 1.4, 3: 1.4, 20: 2.2, 21: 1.8, 22: 1.4, 23: 2.0,
         6: 2.4, 7: 2.4, 8: 2.6, 9: 3.0, 10: 2.6, 11: 2.6, 12: 2.8, 13: 2.6}


def main() -> None:
    clips: list[Path] = []
    for n in ORDER:
        secs = round(dur(vo(n), "a") + 0.4 + TAILS.get(n, 0.8), 2)
        clips.append(with_audio(n, BUILD[n](secs), secs))
        print(f"row {n:2d}  {secs:5.1f}s", flush=True)

    XF = 0.7
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]
    lens = [dur(c) for c in clips]
    fc, vprev, aprev, offset = [], "[0:v]", "[0:a]", 0.0
    n = len(clips)
    for i in range(1, n):
        offset += lens[i - 1] - XF
        vout = f"[v{i}]" if i < n - 1 else "[vx]"
        aout = f"[a{i}]" if i < n - 1 else "[ax]"
        fc.append(f"{vprev}[{i}:v]xfade=transition=fade:duration={XF}:offset={offset:.3f}{vout}")
        fc.append(f"{aprev}[{i}:a]acrossfade=d={XF}:c1=tri:c2=tri{aout}")
        vprev, aprev = vout, aout
    cat = TMP / "cat.mp4"
    run([*inputs, "-filter_complex", ";".join(fc), "-map", "[vx]", "-map", "[ax]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", str(cat)])

    total = dur(cat)
    music = A / "music-Immersed.mp3"
    run(["-i", str(cat), "-stream_loop", "-1", "-i", str(music), "-filter_complex",
         f"[1:a]atrim=0:{total},loudnorm=I=-23:LRA=9:TP=-2,afade=t=in:d=2.5,"
         f"afade=t=out:st={total - 5}:d=5[m];"
         f"[0:a]asplit=2[v1][v2];"
         f"[m][v2]sidechaincompress=threshold=0.015:ratio=5:attack=60:release=1200:makeup=1[md];"
         f"[v1][md]amix=inputs=2:weights=1 0.5:normalize=0,alimiter=limit=0.95[a];"
         f"[0:v]fade=t=in:d=1,fade=t=out:st={total - 1.2}:d=1.2[v]",
         "-map", "[v]", "-map", "[a]", "-shortest",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])
    print(f"done  {out}  {round(total, 1)} s")


if __name__ == "__main__":
    main()
