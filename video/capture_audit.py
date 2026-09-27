"""Record the audit page playing back the recording behind an approval.

The *Listen* control fetches a short-lived URL for the AssemblyAI session recording through the
voice Lambda (the key never reaches the browser) and plays it inline. This is the film's proof
shot: the words that approved a production change, played back from the audit row.

    ~/.pyenv/versions/3.10.13/bin/python video/capture_audit.py --url <console> --passcode nightshift
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path


async def main(url: str, passcode: str, out: Path, seconds: float) -> None:
    from playwright.async_api import async_playwright

    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=[
            "--autoplay-policy=no-user-gesture-required",
            "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        ])
        ctx = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(out / "_video"),
            record_video_size={"width": 1920, "height": 1080},
        )
        page = await ctx.new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept(passcode)))
        await page.goto(f"{url.rstrip('/')}/#audit")
        await page.wait_for_timeout(5000)
        listen = page.locator("button:has-text('Listen')")
        n = await listen.count()
        print(f"listen controls: {n}")
        if n:
            await listen.first.scroll_into_view_if_needed()
            await page.wait_for_timeout(1200)
            await listen.first.click()
            await page.wait_for_timeout(int(seconds * 1000))
        else:
            await page.wait_for_timeout(int(seconds * 1000))
        video = await page.video.path()
        await ctx.close()
        await browser.close()
        dest = out / "audit.webm"
        Path(video).replace(dest)
        print(f"{dest} ({dest.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--passcode", default="nightshift")
    ap.add_argument("--out", default=str(Path.home() / "beacon-video" / "assets"))
    ap.add_argument("--seconds", type=float, default=16.0)
    a = ap.parse_args()
    asyncio.run(main(a.url, a.passcode, Path(a.out), a.seconds))
