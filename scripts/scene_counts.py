"""Write the numbers the film and deck quote, from the repository itself.

These were typed into `scenes.html` by hand and corrected four times in one day —
121 commits, then 141, then 156, then 173 — which is exactly the kind of small,
confident, wrong detail a judge notices. Generating them costs nothing and removes
the whole class of mistake.

    .venv/bin/python scripts/scene_counts.py
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> int:
    commits = (
        subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=ROOT)
        .decode()
        .strip()
    )
    collected = (
        subprocess.run(
            [".venv/bin/python", "-m", "pytest", "-q", "--collect-only"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .splitlines()
    )
    tests = next(
        (
            part
            for line in reversed(collected)
            for part in line.split()
            if part.isdigit()
        ),
        "0",
    )
    counts = {"commits": int(commits), "tests": int(tests)}
    for out in (
        ROOT / "video/scenes/counts.json",
        pathlib.Path.home() / "beacon-video/scenes/counts.json",
    ):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(counts))
    print(f"{counts['commits']} commits, {counts['tests']} tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
