# The demo film, reproducibly

Everything in the film is generated from this folder; no editor was used.

| Step | Tool | Output |
|---|---|---|
| Narration | `narrate.py` — Chatterbox TTS (open source, runs on a laptop GPU) reads every line of `script.md` | `vo/NN.wav` |
| Motion graphics | `scenes/scenes.html` (three.js architecture, typographic scenes) and `scenes/terminal.html`, rendered in headless Chromium by `render_scene.py` / `render_all.py` | `renders/sNN.webm` |
| Product demo | `capture_night.py` records the console playing `?night=1` in `make local` and logs DOM milestones for the cut points | `assets/night.webm` |
| Real-account proof | the `aws` CLI output in `scenes/real-cli.txt`, typed on screen | scene 17 |
| Music | Kevin MacLeod, *Immersed* (incompetech.com, CC BY 4.0); `music.py` is the procedural fallback | bed, ducked under speech |
| Photos | Wikimedia Commons, CC0 / CC BY 4.0 (credits in the video description) | scene 2 |
| Assembly | `assemble.py full|short out.mp4` — per-scene clips cut to narration length, `xfade` transitions, sidechain-ducked music, loudness-normalised | `Beacon-Night-Shift-*.mp4` |

Run order: `narrate.py` → `render_all.py` → `capture_night.py` → `assemble.py`. The scenes need `python3 -m http.server 8877` inside `scenes/` (ES-module imports do not load from `file://`) and the Geist fonts vendored under `scenes/fonts/` (see the `@font-face` CSS from Google Fonts).
