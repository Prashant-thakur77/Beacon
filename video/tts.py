"""Chatterbox TTS: text -> wav (GPU). Usage: tts.py out.wav "text" [exaggeration cfg]"""
import sys, torch, torchaudio as ta
from chatterbox.tts import ChatterboxTTS
out, text = sys.argv[1], sys.argv[2]
exag = float(sys.argv[3]) if len(sys.argv) > 3 else 0.45
cfg = float(sys.argv[4]) if len(sys.argv) > 4 else 0.4
model = ChatterboxTTS.from_pretrained(device="cuda" if torch.cuda.is_available() else "cpu")
wav = model.generate(text, exaggeration=exag, cfg_weight=cfg)
ta.save(out, wav, model.sr)
print(out, wav.shape[-1] / model.sr, "s")
