"""Render every motion-graphics scene of the AssemblyAI cut to the length of its narration.

The demo rows (4–13) are live capture, not scenes, so they are skipped here. Each clip runs for
its narration plus a little breathing room, which is what `assemble.py` then cuts to.

    cd ~/beacon-video && (cd scenes && python3 -m http.server 8877 &)
    ~/.pyenv/versions/3.10.13/bin/python tools/render_aai.py [row ...]
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path.home() / "beacon-video"
PY = "/home/prashant/.pyenv/versions/3.10.13/bin/python"

# row -> (scene, extra seconds after the narration ends)
PLAN = {
    1: ("phone", 1.6),
    4: ("telegram", 1.2),
    5: ("telegram", 1.2),
    2: ("problem-stats", 1.4),
    3: ("title", 1.6),
    14: ("who", 1.2),
    15: ("market", 1.4),
    16: ("revenue", 1.4),
    17: ("whyai", 1.2),
    18: ("safety", 1.0),
    19: ("next", 1.2),
    20: ("close", 2.2),
}


def dur(path: Path) -> float:
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    ).decode().strip())


def main() -> None:
    only = {int(a) for a in sys.argv[1:]}
    (ROOT / "renders-aai").mkdir(exist_ok=True)
    for row, (scene, extra) in PLAN.items():
        if only and row not in only:
            continue
        vo = ROOT / "vo-aai" / f"{row:02d}.wav"
        secs = round(dur(vo) + extra + 0.6, 1) if vo.exists() else 6.0
        out = ROOT / "renders-aai" / f"s{row:02d}.webm"
        subprocess.run([PY, str(ROOT / "tools" / "render_scene.py"), scene, str(secs), str(out)],
                       check=True, capture_output=True)
        print(f"{row:2d}  {scene:14} {secs:5.1f}s  {out.name}", flush=True)


if __name__ == "__main__":
    main()
