// Downsamples the mic to 16 kHz mono Int16 PCM frames (~64 ms) for Transcribe streaming.
class PcmWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.inRate = sampleRate;
    this.outRate = 16000;
    this.ratio = this.inRate / this.outRate;
    this.buffer = [];
    this.pos = 0;
    this.frame = 1024; // output samples per message (64 ms at 16 kHz)
    this.out = new Int16Array(this.frame);
    this.outLen = 0;
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const ch = input[0];
    for (let i = 0; i < ch.length; i++) this.buffer.push(ch[i]);
    // linear resample
    while (this.pos + this.ratio < this.buffer.length) {
      const idx = Math.floor(this.pos);
      const frac = this.pos - idx;
      const s = this.buffer[idx] * (1 - frac) + this.buffer[idx + 1] * frac;
      const clamped = Math.max(-1, Math.min(1, s));
      this.out[this.outLen++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
      this.pos += this.ratio;
      if (this.outLen === this.frame) {
        this.port.postMessage(this.out.buffer.slice(0));
        this.outLen = 0;
      }
    }
    const consumed = Math.floor(this.pos);
    this.buffer = this.buffer.slice(consumed);
    this.pos -= consumed;
    return true;
  }
}
registerProcessor("pcm-worklet", PcmWorklet);
