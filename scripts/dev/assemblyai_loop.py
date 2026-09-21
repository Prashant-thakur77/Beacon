"""Drive the AssemblyAI backend in a real browser against ``make local``.

Waits for each reply (and any tool call) to finish before the next line.
Usage: ASSEMBLYAI_API_KEY=... make local  # then
  python scripts/dev/assemblyai_loop.py "can you fix it" "approve fix 1"
"""

import asyncio
import json
import os
import re
import sys
import time

from playwright.async_api import async_playwright

BASE = os.environ.get("BEACON_LOCAL_URL", "http://localhost:8000")
PASSCODE = os.environ.get("BEACON_PASSCODE", "local")
LINES = sys.argv[1:] or [
    "can you fix it",
    "approve fix 1",
    "yes",
    "grant contract for seven days",
]


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
            ]
        )
        ctx = await b.new_context(
            viewport={"width": 1440, "height": 1000}, permissions=["microphone"]
        )
        pg = await ctx.new_page()
        t0 = time.time()
        state = {"busy": False, "pending_tool": False, "log": []}

        def onws(ws):
            def rx(f):
                try:
                    d = json.loads(f)
                except Exception:
                    return
                t = d.get("type")
                if t == "reply.audio":
                    state["audio"] = state.get("audio", 0) + len(d.get("data") or "")
                if t == "reply.started":
                    state["busy"] = True
                    state["log"].append((time.time() - t0, "reply.start", ""))
                if t == "tool.call":
                    state["pending_tool"] = True
                    state["last_tool"] = d.get("name")
                    state["log"].append((time.time() - t0, "tool.call", d.get("name")))
                if t == "reply.done":
                    state["log"].append(
                        (time.time() - t0, "reply.done", d.get("status"))
                    )
                if t == "reply.done":
                    state["busy"] = False
                if t == "transcript.agent":
                    state["log"].append(
                        (time.time() - t0, "agent", d.get("text", "")[:140])
                    )
                if t == "session.error":
                    state["log"].append((time.time() - t0, "ERROR", d.get("message")))

            def tx(f):
                if isinstance(f, str) and '"tool.result"' in f:
                    state["pending_tool"] = False
                    m = re.search(r'fix_id\\?":\s*(\d+)', f)
                    if m and state.get("last_tool") == "propose_fix":
                        state["fix"] = m.group(1)

            ws.on("framereceived", rx)
            ws.on("framesent", tx)

        pg.on("websocket", onws)
        await pg.goto(f"{BASE}/?voice=assemblyai#board")
        await pg.wait_for_timeout(3000)
        await pg.fill("#passcode", PASSCODE)
        await pg.click("text=Unlock voice")
        await pg.wait_for_timeout(1200)
        await pg.click("[aria-label='Connect']")

        async def settle(max_s=40):
            t = time.time()
            await asyncio.sleep(3)
            while (state["busy"] or state["pending_tool"]) and time.time() - t < max_s:
                await asyncio.sleep(0.3)
            await asyncio.sleep(1.0)

        await settle()
        for i, line in enumerate(LINES):
            line = line.replace("{fix}", state.get("fix", "1"))
            if line == "!interrupt":
                # Barge in on the reply being spoken (the read-back after a tool call).
                t = time.time()
                # wait for the proposal (tool.result sent), then for the read-back reply
                state.pop("fix", None)
                while "fix" not in state and time.time() - t < 60:
                    await asyncio.sleep(0.2)
                while not state["busy"] and time.time() - t < 60:
                    await asyncio.sleep(0.2)
                await asyncio.sleep(2.5)
                label = await pg.text_content(".state")
                state["log"].append((time.time() - t0, "you", f"[interrupt] ({label})"))
                btn = "document.querySelector('button[aria-label=Interrupt]')"
                info = await pg.evaluate(
                    "(() => ({ state: document.querySelector('.state')?.className,"
                    f" disabled: {btn}?.disabled }}))()"
                )
                state["log"].append((time.time() - t0, "dom", json.dumps(info)))
                await pg.evaluate(f"{btn}.click()")
                await settle(45)
                continue
            state["log"].append((time.time() - t0, "you", line))
            await pg.fill("#typed-input-duplex", line)
            await pg.press("#typed-input-duplex", "Enter")
            if LINES[i + 1 : i + 2] == ["!interrupt"]:
                continue  # the next step barges in on this reply
            await settle(75)
            # a tool reply may chain: wait until quiet again
            await settle(45)
        await pg.screenshot(
            path="/home/prashant/beacon-shots/aai-loop.png", full_page=True
        )
        for t, k, v in state["log"]:
            print(f"{t:6.1f} {k:9} {v}")
        d = await pg.evaluate(
            "fetch('/config.json').then(r=>r.json()).then(c=>fetch(c.dashboardUrl.replace(/\\/$/,'')+'/incidents',"
            f"{{headers:{{'x-beacon-passcode':'{PASSCODE}'}}}})).then(r=>r.json()).then(d=>d.incidents[0])"
        )
        c = await pg.evaluate(
            "fetch('/config.json').then(r=>r.json()).then(c=>fetch(c.dashboardUrl.replace(/\\/$/,'')+'/contracts',"
            f"{{headers:{{'x-beacon-passcode':'{PASSCODE}'}}}})).then(r=>r.json()).then(d=>d.contracts.length)"
        )
        print(
            "incident status:",
            d["status"],
            "| contracts:",
            c,
            "| agent audio b64 bytes:",
            state.get("audio", 0),
        )
        await b.close()


asyncio.run(main())
