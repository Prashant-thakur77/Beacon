import type { SttHandlers, SttTransport } from "./stt";

type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
};

function ctor(): (new () => Recognition) | null {
  const w = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function webSpeechAvailable(): boolean {
  return ctor() !== null;
}

/** Chrome's built-in recognition: no AWS, no credentials, decent English. The zero-dependency fallback. */
export class WebSpeechTransport implements SttTransport {
  readonly name = "webspeech" as const;
  private rec: Recognition | null = null;
  private finalText = "";

  constructor(private readonly lang: string) {}

  async start(h: SttHandlers): Promise<void> {
    const C = ctor();
    if (!C) {
      h.onError("Web Speech API is not available in this browser (use Chrome) or switch to typed input");
      return;
    }
    this.finalText = "";
    const rec = new C();
    rec.lang = this.lang;
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) this.finalText += r[0].transcript + " ";
        else interim += r[0].transcript;
      }
      h.onPartial((this.finalText + interim).trim());
    };
    rec.onerror = (e) => h.onError(`speech recognition: ${e.error}`);
    rec.onend = () => {
      if (this.finalText.trim()) h.onFinal(this.finalText.trim());
      this.finalText = "";
    };
    rec.start();
    this.rec = rec;
  }

  async stop(): Promise<void> {
    this.rec?.stop();
    this.rec = null;
  }
}
