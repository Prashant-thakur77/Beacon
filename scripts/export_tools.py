"""Write ``web/src/tools.json`` from ``voice_tools.TOOL_SCHEMAS``.

The browser-hosted AssemblyAI agent needs the same tool definitions the
Strands agent gets, so both come from one source (``make export-tools``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from beacon.voice_tools import TOOL_SCHEMAS

OUT = Path(__file__).resolve().parent.parent / "web" / "src" / "tools.json"


def main() -> int:
    OUT.write_text(json.dumps(TOOL_SCHEMAS, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(TOOL_SCHEMAS)} tools)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
