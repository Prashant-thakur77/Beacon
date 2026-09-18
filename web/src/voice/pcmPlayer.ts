/** Plays streamed PCM16 chunks through a ring of AudioBufferSourceNodes; `flush()` is the barge-in. */
export class PcmPlayer {
  private ctx: AudioContext | null = null;
  private nextAt = 0;
  private sources: AudioBufferSourceNode[] = [];
  private onDrain: (() => void) | null = null;

  async ensure(): Promise<AudioContext> {
    if (!this.ctx) this.ctx = new AudioContext();
    if (this.ctx.state === "suspended") await this.ctx.resume();
    return this.ctx;
  }

  push(pcm16: Int16Array, sampleRate: number): void {
    if (!this.ctx) return;
    const ctx = this.ctx;
    const buffer = ctx.createBuffer(1, pcm16.length, sampleRate);
    const ch = buffer.getChannelData(0);
    for (let i = 0; i < pcm16.length; i++) ch[i] = pcm16[i] / 0x8000;
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime + 0.02, this.nextAt);
    src.start(startAt);
    this.nextAt = startAt + buffer.duration;
    this.sources.push(src);
    src.onended = () => {
      this.sources = this.sources.filter((s) => s !== src);
      if (this.sources.length === 0 && this.onDrain) {
        const cb = this.onDrain;
        this.onDrain = null;
        cb();
      }
    };
  }

  /** Resolve when everything queued has played (or immediately if idle). */
  drained(cb: () => void): void {
    if (this.sources.length === 0) cb();
    else this.onDrain = cb;
  }

  /** Barge-in: drop everything not yet heard. */
  flush(): void {
    for (const s of this.sources) {
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
    }
    this.sources = [];
    this.nextAt = 0;
    this.onDrain = null;
  }

  async close(): Promise<void> {
    this.flush();
    await this.ctx?.close().catch(() => undefined);
    this.ctx = null;
  }
}
