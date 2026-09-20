"""Procedural ambient bed for the film: slow minor pad + sparse arpeggio + sub, with a simple reverb.
Usage: music.py out.wav seconds"""
import sys, numpy as np, soundfile as sf
out, secs = sys.argv[1], float(sys.argv[2])
sr = 44100; t = np.arange(int(secs * sr)) / sr
rng = np.random.default_rng(7)

def note(f, start, dur, amp=0.2, attack=1.5, release=2.5, detune=0.004):
    n = np.zeros_like(t)
    i0, i1 = int(start*sr), min(int((start+dur)*sr), len(t))
    if i1 <= i0: return n
    tt = t[i0:i1] - start
    env = np.minimum(1, tt/attack) * np.minimum(1, (dur - tt)/release)
    env = np.clip(env, 0, 1)
    sig = (np.sin(2*np.pi*f*tt) + 0.5*np.sin(2*np.pi*f*(1+detune)*tt) + 0.25*np.sin(2*np.pi*f*2*tt + 0.3)) / 1.75
    n[i0:i1] = sig * env * amp
    return n

# D minor: chords cycle every 12 s  (Dm, Bb, F, C) as pads, two octaves low
chords = [[146.83, 174.61, 220.0], [116.54, 146.83, 174.61], [174.61, 220.0, 261.63], [130.81, 164.81, 196.0]]
mix = np.zeros_like(t)
bar = 12.0
k = 0
while k * bar < secs:
    ch = chords[k % 4]
    for f in ch:
        mix += note(f/2, k*bar, bar+2, amp=0.09, attack=3, release=4)
        mix += note(f, k*bar, bar+2, amp=0.05, attack=3, release=4)
    mix += note(ch[0]/4, k*bar, bar+1, amp=0.12, attack=2, release=3, detune=0.0)  # sub
    # sparse arpeggio: a few soft plucks per bar
    for j in range(6):
        s = k*bar + j*2 + rng.uniform(0, 0.8)
        f = ch[rng.integers(0, 3)] * (2 if rng.random() < 0.6 else 4)
        mix += note(f, s, 1.8, amp=0.035, attack=0.02, release=1.6, detune=0.002)
    k += 1

# gentle "reverb": feedback delays
def delay(x, ms, g):
    d = int(sr*ms/1000); y = x.copy()
    for i in range(1, 6):
        if d*i < len(x): y[d*i:] += x[:-d*i] * (g**i)
    return y
mix = 0.6*mix + 0.25*delay(mix, 311, 0.55) + 0.15*delay(mix, 487, 0.45)
# slow stereo drift
lfo = 0.5 + 0.5*np.sin(2*np.pi*0.05*t)
left = mix * (0.85 + 0.15*lfo); right = mix * (1.0 - 0.15*lfo)
st = np.stack([left, right], 1)
st = np.tanh(st * 1.4) * 0.8 / max(1e-6, np.abs(st).max()) * 0.9
# fade in/out
fade = int(4*sr); st[:fade] *= np.linspace(0,1,fade)[:,None]; st[-fade:] *= np.linspace(1,0,fade)[:,None]
sf.write(out, st.astype(np.float32), sr)
print(out, secs)
