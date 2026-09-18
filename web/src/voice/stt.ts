/** One interface, three transports: Transcribe streaming (AWS), Web Speech (browser), typed. */
export interface SttHandlers {
  onPartial: (text: string) => void;
  onFinal: (text: string) => void;
  onError: (message: string) => void;
}

export interface SttTransport {
  readonly name: "transcribe" | "webspeech" | "typed";
  start(handlers: SttHandlers): Promise<void>;
  stop(): Promise<void>;
}

export type SttChoice = "transcribe" | "webspeech" | "typed";

export function chooseStt(): SttChoice {
  const q = new URLSearchParams(window.location.search).get("stt");
  if (q === "transcribe" || q === "webspeech" || q === "typed") return q;
  return "transcribe";
}
