"""Render every narration line from script.md with Chatterbox -> vo/NN.wav (+ NN.txt)."""
import re, sys, torch, torchaudio as ta
from pathlib import Path
from chatterbox.tts import ChatterboxTTS
root = Path.home() / "beacon-video"
rows = [l for l in (root / "script.md").read_text().splitlines() if re.match(r"^\| \d+ \|", l)]
model = ChatterboxTTS.from_pretrained(device="cuda")
only = set(sys.argv[1:])
for l in rows:
    cells = [c.strip() for c in l.strip().strip("|").split("|")]
    n, text = cells[0], cells[3]
    if only and n not in only: continue
    out = root / "vo" / f"{int(n):02d}.wav"
    (root / "vo" / f"{int(n):02d}.txt").write_text(text)
    wav = model.generate(text, exaggeration=0.4, cfg_weight=0.45, temperature=0.7)
    ta.save(str(out), wav, model.sr)
    print(f"{n}: {wav.shape[-1]/model.sr:.1f}s  {text[:50]}", flush=True)
