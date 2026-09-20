"""Record the local night at 1080p (zoomed) and log DOM milestones with timestamps for cutting."""
import asyncio, json, shutil, sys, time
from playwright.async_api import async_playwright
url, out, secs = sys.argv[1], sys.argv[2], float(sys.argv[3])
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        ctx = await b.new_context(viewport={"width": 1920, "height": 1080}, record_video_dir="/tmp/pw-night", record_video_size={"width": 1920, "height": 1080})
        page = await ctx.new_page()
        await page.goto(url)
        await page.add_style_tag(content="html{zoom:1.4}")
        t0 = time.time(); last = None; log = []
        while time.time() - t0 < secs:
            st = await page.evaluate("""(() => ({pill: document.querySelector('.pill.lilac')?.textContent||'', state: document.querySelector('.state')?.textContent||'', msgs: document.querySelectorAll('.bubble').length, cards: document.querySelectorAll('.card').length, contracts: document.querySelector('nav a[href="#contracts"]')?.textContent||''}))()""")
            key = json.dumps(st)
            if key != last:
                log.append({"t": round(time.time()-t0, 1), **st}); last = key
            await asyncio.sleep(0.5)
        path = await page.video.path(); await ctx.close(); await b.close(); shutil.move(path, out)
        json.dump(log, open(out + ".json", "w"), indent=1); print(out, len(log), "milestones")
asyncio.run(main())
