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
#
# The film opens at night and turns to the console's own palette at the title, so
# rows 1, 2 and 22 stay dark and everything from row 3 carries "@light" — the
# product being judged is a cream-and-ink console, and a film in a different palette
# is a film of something else.
PLAN = {
    1: ("phone", 1.6),
    2: ("problem-stats", 1.4),
    22: ("timezones", 1.4),
    3: ("title@light", 1.6),
    21: ("archvoice@light", 2.0),
    23: ("call@light", 2.0),
    14: ("who@light", 1.2),
    15: ("market@light", 1.4),
    16: ("revenue@light", 1.4),
    17: ("whyai@light", 1.2),
    18: ("safety@light", 1.0),
    19: ("next@light", 1.2),
    20: ("close@light", 2.2),
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
