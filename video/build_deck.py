"""Render the twelve pitch slides from the film's own scene engine, then bind them into a PDF.

Deck and film are the same design system: image slides (`slide.html`) carrying a real screenshot and the architecture drawing. Needs the
scene server running:  cd ~/beacon-video/scenes && python3 -m http.server 8877

    ~/.pyenv/versions/3.10.13/bin/python video/build_deck.py [out.pdf]
"""

import asyncio
import sys
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path.home() / "beacon-video"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "Beacon-deck.pdf"
SLIDES_DIR = ROOT / "deck"
BASE = "http://localhost:8877"

# (filename, url) — scenes render themselves; slide.html takes the image ones.
def img_slide(**kw: str) -> str:
    return f"{BASE}/slide.html?" + urlencode({**kw, "theme": "light"})


SLIDES: list[tuple[str, str]] = [
    ("01-title", f"{BASE}/scenes.html?theme=light&scene=title"),
    ("02-problem", f"{BASE}/scenes.html?theme=light&scene=problem-stats"),
    ("03-who", f"{BASE}/scenes.html?theme=light&scene=who"),
    ("04-demo", img_slide(
        k="Live, not a mock-up",
        h="Speak over the read-back, and <em>the fix is withdrawn</em>.",
        img="demo-shot.jpg",
        c="A real run on the deployed account: the read-back cut mid-sentence, <b>INTERRUPTED</b>, "
          "<i>propose_fix · dry run PASSED</i>, and the console note — <b>“Fix 1 withdrawn — you spoke "
          "over the read-back, so nothing was applied.”</b> The approval phrase stops working until it is proposed again.")),
    ("05-arch", img_slide(
        k="Where AssemblyAI sits",
        h="Nine tools on one socket. <em>Consent is decided in code.</em>",
        img="arch.png", fit="contain",
        c="The Voice Agent API is the mouth and the ears; every <b>tool.call</b> returns to the browser, "
          "which runs it on our Lambda with the transcript the API produced.")),
    ("06-phone", f"{BASE}/scenes.html?theme=light&scene=call"),
    ("07-why", f"{BASE}/scenes.html?theme=light&scene=whyai"),
    ("08-voicetest", f"{BASE}/scenes.html?theme=light&scene=voicetest"),
    ("09-market", f"{BASE}/scenes.html?theme=light&scene=market"),
    ("10-revenue", f"{BASE}/scenes.html?theme=light&scene=revenue"),
    ("11-competitors", f"{BASE}/scenes.html?theme=light&scene=competitors"),
    ("12-next", f"{BASE}/scenes.html?theme=light&scene=next"),
]


async def shoot() -> list[Path]:
    from playwright.async_api import async_playwright

    SLIDES_DIR.mkdir(exist_ok=True)
    paths: list[Path] = []
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                          "--enable-unsafe-swiftshader"])
        ctx = await b.new_context(viewport={"width": 1920, "height": 1080})
        page = await ctx.new_page()
        for name, url in SLIDES:
            await page.goto(url)
            await page.evaluate("document.fonts.ready")
            # let the staggered entrances finish so nothing is mid-fade
            await page.wait_for_timeout(7000)
            out = SLIDES_DIR / f"{name}.png"
            await page.screenshot(path=str(out))
            paths.append(out)
            print(f"  {name}", flush=True)
        await b.close()
    return paths


def to_pdf(pages: list[Path]) -> None:
    from PIL import Image

    imgs = [Image.open(p).convert("RGB") for p in pages]
    imgs[0].save(OUT, save_all=True, append_images=imgs[1:], resolution=150.0)
    print(f"{OUT}  ({OUT.stat().st_size // 1024} KB, {len(imgs)} slides)")


if __name__ == "__main__":
    to_pdf(asyncio.run(shoot()))
