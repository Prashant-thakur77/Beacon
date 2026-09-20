"""Render a scenes.html scene to PNG frames at 30 fps with Playwright (deterministic timing via CDP virtual time
is unreliable with rAF; we record video instead). Usage: render_scene.py <scene> <seconds> <out.webm> [snapshot.png]"""
import asyncio, shutil, sys, time
from pathlib import Path
from playwright.async_api import async_playwright
scene, secs, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
snap = sys.argv[4] if len(sys.argv) > 4 else None
url = f"http://localhost:8877/scenes.html?scene={scene}" if not scene.startswith("terminal:") else "http://localhost:8877/terminal.html?" + scene.split(":",1)[1]
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        ctx = await b.new_context(viewport={"width": 1920, "height": 1080}, record_video_dir="/tmp/pw-scenes", record_video_size={"width": 1920, "height": 1080})
        page = await ctx.new_page()
        await page.goto(url); await page.wait_for_timeout(300)
        await page.evaluate("document.fonts.ready")
        t0 = time.time()
        if snap:
            await page.wait_for_timeout(int(min(secs, 6) * 1000)); await page.screenshot(path=snap)
        while time.time() - t0 < secs: await asyncio.sleep(0.5)
        path = await page.video.path(); await ctx.close(); await b.close(); shutil.move(path, out); print(out)
asyncio.run(main())
