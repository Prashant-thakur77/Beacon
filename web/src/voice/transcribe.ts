import { StartStreamTranscriptionCommand, TranscribeStreamingClient, type AudioStream } from "@aws-sdk/client-transcribe-streaming";
import type { SttHandlers, SttTransport } from "./stt";

interface Creds {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken: string;
  expiration: string;
}

/** Amazon Transcribe streaming over WebSocket from the browser, with 15-minute STS creds vended by /session. */
export class TranscribeTransport implements SttTransport {
  readonly name = "transcribe" as const;
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private queue: Uint8Array[] = [];
  private waiting: ((v: Uint8Array | null) => void) | null = null;
  private stopped = false;
  private client: TranscribeStreamingClient | null = null;

  constructor(
    private readonly getSession: () => Promise<{ credentials: Creds; region: string; sttLanguage: string }>,
  ) {}

  async start(h: SttHandlers): Promise<void> {
    this.stopped = false;
    this.queue = [];
    const session = await this.getSession();
    this.client = new TranscribeStreamingClient({
      region: session.region,
      credentials: {
        accessKeyId: session.credentials.accessKeyId,
        secretAccessKey: session.credentials.secretAccessKey,
        sessionToken: session.credentials.sessionToken,
      },
    });

    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule("/pcm-worklet.js");
    const source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "pcm-worklet");
    this.node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => this.push(new Uint8Array(e.data));
    source.connect(this.node);
    // keep the graph alive without echoing the mic
    const silent = this.ctx.createGain();
    silent.gain.value = 0;
    this.node.connect(silent).connect(this.ctx.destination);

    const self = this;
    const audioStream = (async function* (): AsyncGenerator<AudioStream> {
      while (true) {
        const chunk = await self.next();
        if (chunk === null) return;
        yield { AudioEvent: { AudioChunk: chunk } };
      }
    })();

    const command = new StartStreamTranscriptionCommand({
      LanguageCode: session.sttLanguage as "en-IN",
      MediaEncoding: "pcm",
      MediaSampleRateHertz: 16000,
      EnablePartialResultsStabilization: true,
      PartialResultsStability: "high",
      AudioStream: audioStream,
    });

    void (async () => {
      try {
        const resp = await this.client!.send(command);
        for await (const ev of resp.TranscriptResultStream ?? []) {
          const results = ev.TranscriptEvent?.Transcript?.Results ?? [];
          for (const r of results) {
            const text = r.Alternatives?.[0]?.Transcript ?? "";
            if (!text) continue;
            if (r.IsPartial) h.onPartial(text);
            else h.onFinal(text);
          }
        }
      } catch (e) {
        if (!this.stopped) h.onError(e instanceof Error ? e.message : String(e));
      }
    })();
  }

  private push(chunk: Uint8Array) {
    if (this.stopped) return;
    if (this.waiting) {
      const w = this.waiting;
      this.waiting = null;
      w(chunk);
    } else {
      this.queue.push(chunk);
      if (this.queue.length > 200) this.queue.shift();
    }
  }

  private next(): Promise<Uint8Array | null> {
    if (this.stopped) return Promise.resolve(null);
    const q = this.queue.shift();
    if (q) return Promise.resolve(q);
    return new Promise((resolve) => (this.waiting = resolve));
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.waiting) {
      const w = this.waiting;
      this.waiting = null;
      w(null);
    }
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    await this.ctx?.close().catch(() => undefined);
    this.node = null;
    this.stream = null;
    this.ctx = null;
    this.client?.destroy();
    this.client = null;
  }
}
