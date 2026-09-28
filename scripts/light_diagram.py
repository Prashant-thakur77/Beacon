"""Recolour the architecture drawing into the console's own palette.

The product is a cream-and-ink console, and the deck and the film match it from the
title onward. The drawing is authored once, in the dark palette the film opens with;
this derives the light one from it so the two can never drift apart by hand.

    .venv/bin/python scripts/light_diagram.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "docs/assets/architecture-voice.svg"
OUT = ROOT / "docs/assets/architecture-voice-light.svg"

# night -> the console. Ordered longest-first so no key is a prefix of another.
PALETTE = {
    "#0b0b0d": "#FFFFEB",  # page
    "#0e0e11": "#FBFBE8",  # plane fill
    "#1c1c21": "#E6E6D2",  # plane stroke
    "#141417": "#FFFDF2",  # box fill
    "#26262b": "#DCDCC6",  # box stroke
    "#f5f5f4": "#1A1A1A",  # headings
    "#a1a1aa": "#55554E",  # body and mono
    "#55555c": "#8A8A78",  # kickers and plain arrows
    "#b4a7ff": "#6B4FD8",  # lilac
    "#6ee7a0": "#03796A",  # green
    "#f5b942": "#B26B00",  # amber
    "#ff6b6b": "#C02626",  # red
    "#3a3a44": "#C8C8B4",
}


def main() -> int:
    svg = SRC.read_text()
    for night, day in PALETTE.items():
        svg = svg.replace(night, day).replace(night.upper(), day)
    OUT.write_text(svg)
    print(f"{OUT.relative_to(ROOT)}  ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
