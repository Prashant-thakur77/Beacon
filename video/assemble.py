"""Assemble the film from renders, the night recording, photos and narration.

Usage: assemble.py full|short out.mp4
Each scene -> a 1080p30 clip exactly as long as its narration (+ tail). Then concat,
add the music bed (ducked under speech), fade in/out.
"""
import json, os, subprocess, sys
from pathlib import Path

ROOT = Path.home() / "beacon-video"
FONT = str(ROOT / "scenes/fonts")
W, H, FPS = 1920, 1080, 30
mode, out = sys.argv[1], sys.argv[2]
TMP = ROOT / "build"; TMP.mkdir(exist_ok=True)

def dur(p, stream="v"):
    sel = ["-select_streams", stream] if str(p).endswith(".mp4") else []
    return float(subprocess.check_output(["ffprobe", "-v", "error", *sel, "-show_entries", "stream=duration", "-of", "csv=p=0", str(p)]).decode().strip().splitlines()[0])

def run(args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)

vo = lambda n: ROOT / "vo" / f"{n:02d}.wav"
night = ROOT / "assets/night.webm"

# photo Ken Burns with keyword overlays (scene 2)
def photo_clip(n, secs):
    segs = [("servers.jpg", "alone", 0.0), ("night.jpg", "half-asleep · no laptop", secs / 2)]
    parts = []
    for i, (img, word, _) in enumerate(segs):
        d = secs / 2 + 0.3
        p = TMP / f"p{n:02d}_{i}.mp4"
        # slow zoom via zoompan on a 1920x1080-cropped source
        z = "zoompan=z='min(zoom+0.0006,1.18)':d=%d:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=%dx%d:fps=%d" % (int(d * FPS), W, H, FPS)
        txt = f"drawtext=fontfile={FONT}/../../scenes/fonts/geist-regular.ttf:text='{word}':fontcolor=white:fontsize=84:x=120:y=h-220:alpha='if(lt(t,0.8),t/0.8,1)'"
        run(["-loop", "1", "-i", str(ROOT / "assets/photos" / img), "-t", str(d), "-vf",
             f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},{z},eq=brightness=-0.12:saturation=0.8,{txt},format=yuv420p",
             "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", str(p)])
        parts.append(p)
    return parts

def render_clip(n, src, secs):
    p = TMP / f"c{n:02d}.mp4"
    run(["-i", str(src), "-t", str(secs), "-vf", f"scale={W}:{H},fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", str(p)])
    return p

def night_clip(n, start, secs, hold=0.0):
    """Segment of the night recording; freeze the last frame for `hold` seconds."""
    p = TMP / f"c{n:02d}.mp4"
    vf = f"scale={W}:{H},fps={FPS},format=yuv420p"
    if hold > 0:
        vf += f",tpad=stop_mode=clone:stop_duration={hold}"
    run(["-ss", str(start), "-i", str(night), "-t", str(secs + hold), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", str(p)])
    return p

def with_audio(n, video, secs, extra_tail=0.6):
    """Pad the narration to the clip length and mux."""
    p = TMP / f"a{n:02d}.mp4"
    run(["-i", str(video), "-i", str(vo(n)), "-filter_complex", f"[1:a]adelay=400|400,apad=whole_dur={secs}[a]", "-map", "0:v", "-map", "[a]", "-t", str(secs), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(p)])
    return p

# scene table: n -> builder(secs) where secs = narration + tail
R = ROOT / "renders"
def vlen(n, tail): return round(dur(vo(n)) + 0.4 + tail, 2)

scenes = {
    1: lambda s: render_clip(1, R / "s01.webm", s),
    2: lambda s: render_clip(2, R / "s02.webm", s),
    3: lambda s: render_clip(3, R / "s03.webm", s),
    4: lambda s: render_clip(4, R / "s04.webm", s),
    5: lambda s: render_clip(5, R / "s05.webm", s),
    6: lambda s: render_clip(6, R / "s06.webm", s),
    7: lambda s: render_clip(7, R / "s07.webm", s),
    8: lambda s: render_clip(8, R / "s08.webm", s),
    9: lambda s: render_clip(9, ROOT / "assets/board.webm", s),
    10: lambda s: night_clip(10, 2.0, s, hold=max(0, s - 18)),
    11: lambda s: night_clip(11, 21.0, s, hold=max(0, s - 18)),
    12: lambda s: night_clip(12, 40.0, min(s, 10.6), hold=max(0.0, s - 10.6)),
    13: lambda s: night_clip(13, 50.6, min(s, 11.6), hold=max(0.0, s - 11.6)),
    14: lambda s: night_clip(14, 62.0, s, hold=max(0, s - 20)),
    15: lambda s: render_clip(15, R / "s15.webm", s),
    16: lambda s: render_clip(16, R / "s16.webm", s),
    17: lambda s: render_clip(17, ROOT / "assets/supply/real-aws.mp4" if (ROOT / "assets/supply/real-aws.mp4").exists() else R / "s17.webm", s),
    18: lambda s: render_clip(18, R / "s18.webm", s),
    19: lambda s: render_clip(19, R / "s19.webm", s),
    20: lambda s: render_clip(20, R / "s20.webm", s),
    21: lambda s: render_clip(21, R / "s21.webm", s),
}
tails = {1: 1.2, 6: 1.4, 14: 1.0, 21: 2.4}
short_set = {1, 2, 3, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 20, 21}
order = [n for n in range(1, 22) if mode == "full" or n in short_set]

clips = []
for n in order:
    secs = vlen(n, tails.get(n, 0.7))
    v = scenes[n](secs)
    clips.append(with_audio(n, v, secs))
    print("scene", n, secs, flush=True)

# --- smooth transitions: xfade (video) + acrossfade (audio) chained over every cut
XF = 0.7
n = len(clips)
inputs = []
for c in clips: inputs += ["-i", str(c)]
lens = [dur(c) for c in clips]
fc = []
vprev, aprev, offset = "[0:v]", "[0:a]", 0.0
for i in range(1, n):
    offset += lens[i-1] - XF
    vout = f"[v{i}]" if i < n-1 else "[vx]"
    aout = f"[a{i}]" if i < n-1 else "[ax]"
    fc.append(f"{vprev}[{i}:v]xfade=transition=fade:duration={XF}:offset={offset:.3f}{vout}")
    fc.append(f"{aprev}[{i}:a]acrossfade=d={XF}:c1=tri:c2=tri{aout}")
    vprev, aprev = vout, aout
cat = TMP / "cat.mp4"
run([*inputs, "-filter_complex", ";".join(fc), "-map", "[vx]", "-map", "[ax]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(cat)])
total = dur(cat)
# music: Kevin MacLeod "Immersed" (CC BY 4.0), loudness-normalised, ducked under narration
music = ROOT / "assets/music-Immersed.mp3"
run(["-i", str(cat), "-stream_loop", "-1", "-i", str(music), "-filter_complex",
     f"[1:a]atrim=0:{total},loudnorm=I=-23:LRA=9:TP=-2,afade=t=in:d=2.5,afade=t=out:st={total-5}:d=5[m];"
     f"[0:a]asplit=2[v1][v2];[m][v2]sidechaincompress=threshold=0.015:ratio=5:attack=60:release=1200:makeup=1[md];"
     f"[v1][md]amix=inputs=2:weights=1 0.55:normalize=0,alimiter=limit=0.95[a];"
     f"[0:v]fade=t=in:d=1,fade=t=out:st={total-1.2}:d=1.2[v]",
     "-map", "[v]", "-map", "[a]", "-shortest", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
print("done", out, round(total, 1), "s")
