"""YouTube chapter timings, computed from the cut rather than typed from memory.

`assemble_aai.py` prints each row's length as it builds it, and the rows are joined
with a crossfade, so a chapter's start is the sum of the rows before it minus one
crossfade per join. Changing any narration line moves every timestamp after it --
which is exactly the kind of thing nobody notices until a viewer clicks a chapter
and lands in the wrong place.

    python video/chapters.py build-aai.log

Reads the assemble log (or stdin) and prints the chapter block for the YouTube
description.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

XF = 0.7  # must match assemble_aai.py

# The row each chapter opens on, and its title. A row missing from here simply
# does not start a chapter.
CHAPTERS: dict[int, str] = {
    1: "03:12 — the page nobody answers",
    3: "Beacon Night Shift — and what unlocks it",
    21: "One socket: browser, phone, Telegram",
    23: "A real call, two channels",
    4: "The page lands on the phone",
    24: "Refused at 75%, refused at 74%",
    6: "The Night Board: what changed",
    7: "One socket, full duplex",
    9: "Interrupt the read-back — the fix is withdrawn",
    10: '"Approve fix two", and the verify loop',
    11: "A Sleep Contract, in your own words",
    12: "The pull request that ends the incident",
    13: "The recording, played back",
    14: "Who it is for",
    15: "Market",
    16: "Revenue",
    17: "Why this needed this generation of models",
    19: "Built solo, in ten days",
}


def hhmm(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def main() -> int:
    text = (
        Path(sys.argv[1]).read_text(encoding="utf-8")
        if len(sys.argv) > 1
        else sys.stdin.read()
    )
    rows = [
        (int(m.group(1)), float(m.group(2)))
        for m in re.finditer(r"^row\s+(\d+)\s+([\d.]+)s", text, re.M)
    ]
    if not rows:
        print("no 'row N  X.Xs' lines found in the log", file=sys.stderr)
        return 1

    start = 0.0
    out: list[str] = []
    for index, (row, length) in enumerate(rows):
        if row in CHAPTERS:
            # YouTube requires the first chapter to start at 0:00.
            out.append(f"{hhmm(0 if not out else start)} {CHAPTERS[row]}")
        start += length - (XF if index < len(rows) - 1 else 0)

    print("\n".join(out))
    print(f"\ntotal {hhmm(start)} ({start:.1f}s)", file=sys.stderr)
    if start > 300:
        print("OVER the 5:00 submission limit", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
