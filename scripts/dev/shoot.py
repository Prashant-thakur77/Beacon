"""Headless Chromium via CDP: open a URL and screenshot it at given seconds.

Usage:
    .venv/bin/python scripts/dev/shoot.py \
        "http://localhost:8000/?night=1" ~/shots/night 30 60 95

``WIN=400,900`` sets the window size. Needs ``chromium`` on PATH and the
``websockets`` package. Plain ``--screenshot`` fires on load and
``--virtual-time-budget`` never ends on a page that polls, hence CDP.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from typing import Any

import websockets

PORT = 9333
PROBE = (
    "(document.querySelector('.pill.lilac')?.textContent ?? '') + ' | ' + "
    "(document.querySelector('.state')?.textContent ?? '')"
)


def _launch(win: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            "chromium",
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--window-size={win}",
            f"--remote-debugging-port={PORT}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _page_ws() -> str:
    for _ in range(50):
        try:
            targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
            return str(
                [t for t in targets if t["type"] == "page"][0]["webSocketDebuggerUrl"]
            )
        except Exception:  # noqa: BLE001 - the browser is still starting
            time.sleep(0.2)
    raise SystemExit("chromium did not expose a page target")


async def _run(url: str, prefix: str, secs: list[int]) -> None:
    async with websockets.connect(_page_ws(), max_size=50_000_000) as ws:
        seq = 0

        async def call(method: str, **params: Any) -> dict[str, Any]:
            nonlocal seq
            seq += 1
            await ws.send(json.dumps({"id": seq, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == seq:
                    return dict(msg.get("result", {}))

        await call("Page.enable")
        await call("Runtime.enable")
        await call("Page.navigate", url=url)
        t0 = time.time()
        for s in secs:
            await asyncio.sleep(max(0, s - (time.time() - t0)))
            shot = await call("Page.captureScreenshot", format="png")
            with open(f"{prefix}-{s}s.png", "wb") as fh:
                fh.write(base64.b64decode(shot["data"]))
            probe = await call("Runtime.evaluate", expression=PROBE, returnByValue=True)
            print(s, "s:", probe.get("result", {}).get("value"))


def main() -> None:
    url, prefix, *rest = sys.argv[1:]
    proc = _launch(os.environ.get("WIN", "1440,1000"))
    try:
        asyncio.run(_run(url, prefix, [int(x) for x in rest]))
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
