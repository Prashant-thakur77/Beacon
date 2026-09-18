/**
 * The First Commit voice path behind the VoiceTransport interface:
 * push-to-talk STT (Transcribe / Web Speech) → POST /turn (Strands on Nova 2 Lite) → Polly mp3.
 * Half-duplex by design: `sendAudio` is a no-op (the STT transports own the mic), and
 * `interrupt()` stops Polly playback.
 */
import type { Api } from "../api";
import type { TurnResponse } from "../types";
import { Player, speakFallback } from "./player";
import type { SttChoice } from "./stt";
import { TranscribeTransport } from "./transcribe";
import type { VoiceSession, VoiceTransport, VoiceTransportHandlers } from "./transport";
import { WebSpeechTransport } from "./webspeech";

export class AwsCascadeTransport implements VoiceTransport {
  readonly name = "aws" as const;
  private h: VoiceTransportHandlers | null = null;
  private session: VoiceSession | null = null;
  private stt: TranscribeTransport | WebSpeechTransport | null = null;
  private player = new Player();
  private listening = false;

  constructor(
    private readonly api: Api,
    private readonly sttChoice: SttChoice,
    private readonly sttLanguage: string,
  ) {}

  async start(session: VoiceSession, handlers: VoiceTransportHandlers): Promise<void> {
    this.session = session;
    this.h = handlers;
    handlers.onState("idle");
  }

  /** Push-to-talk: call on pointer down. */
  async beginListening(): Promise<void> {
    if (this.listening || !this.h) return;
    const h = this.h;
    this.player.stop();
    this.stt =
      this.sttChoice === "webspeech"
        ? new WebSpeechTransport(this.sttLanguage)
        : new TranscribeTransport(() => this.api.session(), this.sttLanguage);
    this.listening = true;
    h.onState("listening");
    await this.stt.start({
      onPartial: (text) => h.onUserTranscript({ text, final: false }),
      onFinal: (text) => {
        h.onUserTranscript({ text, final: true });
        void this.sendText(text, this.stt?.name ?? "transcribe");
      },
      onError: (message) => {
        h.onError(message);
        h.onState("error");
      },
    });
  }

  /** Push-to-talk: call on pointer up. */
  async endListening(): Promise<void> {
    const stt = this.stt;
    this.stt = null;
    this.listening = false;
    if (stt) await stt.stop();
  }

  sendAudio(): void {
    /* the STT transport owns the microphone in this backend */
  }

  async sendText(text: string, channel = "typed"): Promise<void> {
    if (!this.h || !this.session) return;
    const h = this.h;
    h.onState("thinking");
    let resp: TurnResponse;
    try {
      resp = await this.api.turn({
        incident_id: this.session.incidentId,
        session_id: this.session.sessionId,
        text,
        channel,
        lang: this.sttLanguage,
      });
    } catch (e) {
      h.onError(e instanceof Error ? e.message : String(e));
      h.onState("error");
      return;
    }
    for (const ev of resp.tool_events) h.onToolResult(ev.name, ev.args, ev.summary, [ev], resp.evidence);
    h.onAgentText({ text: resp.reply_text, cited: resp.cited });
    h.onState("speaking");
    const done = () => {
      h.onAgentDone("completed");
      h.onState("idle");
    };
    if (resp.audio_b64) await this.player.play(resp.audio_b64, resp.speech_marks, () => undefined, done);
    else speakFallback(resp.spoken_text || resp.reply_text, done);
  }

  interrupt(): void {
    this.player.stop();
    this.h?.onAgentDone("interrupted");
    this.h?.onState("idle");
  }

  async stop(): Promise<void> {
    await this.endListening();
    this.player.stop();
  }
}
