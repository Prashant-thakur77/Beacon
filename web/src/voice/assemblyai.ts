/**
 * AssemblyAI Voice Agent API transport.
 *
 * Protocol (wss://agents.assemblyai.com/v1/ws):
 *   client → session.update { system_prompt, greeting, input{format, turn_detection, language_codes},
 *                             output{voice, format}, tools[{type:"function", name, description, parameters}] }
 *   client → input.audio { audio: base64 PCM16 24 kHz mono, ~50 ms chunks }
 *   server → session.ready | transcript.user.delta | transcript.user | reply.started | reply.audio { data }
 *          | transcript.agent | tool.call { call_id, name, arguments } | reply.done { status } | session.error
 *   client → tool.result { call_id, result: JSON string }   — only after reply.done for that turn
 *
 * Every tool.call is forwarded to POST /tools/<name> on the voice Lambda with the latest final user
 * transcript and its confidence, so approve_fix / grant_sleep_contract are still decided server-side
 * against what the engineer actually said.
 */
import type { VoiceSession, VoiceTransport, VoiceTransportHandlers } from "./transport";
import tools from "../tools.json";

const WS_URL = "wss://agents.assemblyai.com/v1/ws";
const OUT_RATE = 24000;

interface ToolCallMsg {
  type: "tool.call";
  call_id: string;
  name: string;
  arguments: Record<string, unknown>;
}

function b64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

function fromB64(s: string): Int16Array {
  const bin = atob(s);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Int16Array(bytes.buffer);
}

function resampleTo24k(pcm: Int16Array, from: number): Int16Array {
  if (from === OUT_RATE) return pcm;
  const ratio = from / OUT_RATE;
  const out = new Int16Array(Math.floor(pcm.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const idx = i * ratio;
    const lo = Math.floor(idx);
    const hi = Math.min(lo + 1, pcm.length - 1);
    const frac = idx - lo;
    out[i] = pcm[lo] * (1 - frac) + pcm[hi] * frac;
  }
  return out;
}

export class AssemblyAITransport implements VoiceTransport {
  readonly name = "assemblyai" as const;
  private ws: WebSocket | null = null;
  private h: VoiceTransportHandlers | null = null;
  private session: VoiceSession | null = null;
  private aaiSessionId: string | null = null;
  private resumeToken: string | null = null;
  private turnDetection: { vad_threshold: number; min_silence?: number; max_silence?: number } = { vad_threshold: 0.5 };
  private resumes = 0;
  private resuming = false;
  private stopping = false;
  private lastFinal: { text: string; confidence?: number } = { text: "" };
  private pendingResults: Array<{ call_id: string; result: string }> = [];
  private replyOpen = false;

  constructor(
    private readonly getToken: () => Promise<{ token: string }>,
    private readonly runTool: (
      name: string,
      args: Record<string, unknown>,
      ctx: { incidentId: string; sessionId: string; transcript: string; confidence?: number },
    ) => Promise<{ ok: boolean; result: unknown; tool_events: never[]; evidence: never[] }>,
  ) {}

  async start(session: VoiceSession, handlers: VoiceTransportHandlers): Promise<void> {
    this.session = session;
    this.h = handlers;
    handlers.onState("connecting");
    const greeting = session.greeting ?? "";
    const { token } = await this.getToken();
    // Browsers cannot set headers on WebSocket; the short-lived token rides the URL.
    const ws = new WebSocket(`${WS_URL}?token=${encodeURIComponent(token)}`);
    this.ws = ws;
    ws.onopen = () => {
      ws.send(this.sessionUpdate(session, greeting));
      handlers.onState("listening");
    };
    ws.onmessage = (e) => void this.onMessage(JSON.parse(String(e.data)));
    ws.onerror = () => handlers.onError("AssemblyAI socket error");
    ws.onclose = () => void this.onClose(ws);
  }

  setTurnDetection(opts: { vad_threshold: number; min_silence?: number; max_silence?: number }): void {
    this.turnDetection = opts;
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    // input.turn_detection is mutable after session.ready (the spec lists it under the
    // fields session.update may change mid-session).
    this.ws.send(JSON.stringify({ type: "session.update", session: { input: { turn_detection: opts } } }));
  }

  private sessionUpdate(session: VoiceSession, greeting: string): string {
    return JSON.stringify({
      type: "session.update",
      session: {
        system_prompt: session.systemPrompt,
        greeting,
        input: {
          format: { encoding: "audio/pcm" },
          turn_detection: this.turnDetection,
          transcription_mode: "balanced",
          language_codes: session.languageCodes ?? ["en", "hi"],
          ...(session.keyterms?.length ? { keyterms: session.keyterms } : {}),
        },
        output: { voice: session.voice ?? "jane", format: { encoding: "audio/pcm" } },
        // Client-side function tools: the browser gets tool.call and answers with tool.result
        // (after reply.done); "interactive" lets the agent keep the turn while we run it.
        tools: tools.map((t) => ({ type: "function", name: t.name, description: t.description, parameters: t.parameters, execution_mode: "interactive", timeout_seconds: 60 })),
      },
    });
  }

  /**
   * A dropped socket (Wi-Fi blip, laptop lid) resumes the same session by its
   * session_id inside the API's 30 s reconnect window. Nothing
   * executes across the gap: a tool only runs when its tool.call reaches the
   * browser and the browser calls the Lambda.
   */
  private async onClose(closed: WebSocket): Promise<void> {
    const h = this.h;
    if (!h || this.ws !== closed) return; // stop() or an older socket
    if (this.stopping || !this.aaiSessionId || this.resumes >= 3) {
      h.onState("idle");
      return;
    }
    this.resumes += 1;
    h.onState("connecting");
    try {
      const { token } = await this.getToken();
      const ws = new WebSocket(`${WS_URL}?token=${encodeURIComponent(token)}`);
      this.ws = ws;
      // The spec: session.resume { session_id } within 30 s of the drop (the
      // resume_token in session.ready is informational here).
      this.resuming = true;
      ws.onopen = () => ws.send(JSON.stringify({ type: "session.resume", session_id: this.aaiSessionId }));
      ws.onmessage = (e) => void this.onMessage(JSON.parse(String(e.data)));
      ws.onerror = () => h.onError("AssemblyAI socket error");
      ws.onclose = () => void this.onClose(ws);
      h.onResumed?.(this.resumes);
    } catch (e) {
      h.onError(e instanceof Error ? e.message : String(e));
      h.onState("error");
    }
  }

  private async onMessage(msg: Record<string, unknown>): Promise<void> {
    const h = this.h!;
    switch (msg.type) {
      case "session.ready":
        // The provider's id; approvals store it so the audit can play the recording back.
        this.aaiSessionId = typeof msg.session_id === "string" ? msg.session_id : null;
        this.resumeToken = typeof msg.resume_token === "string" ? msg.resume_token : this.resumeToken;
        this.resuming = false;
        h.onState("listening");
        return;
      case "session.resumed":
        this.resuming = false;
        h.onState("listening");
        return;
      case "input.speech.started":
        h.onState("listening");
        return;
      case "input.speech.stopped":
        return;
      case "session.ended":
        h.onState("idle");
        return;
      case "transcript.user.delta":
        h.onUserTranscript({ text: String(msg.text ?? ""), final: false });
        return;
      case "transcript.user":
        this.lastFinal = { text: String(msg.text ?? ""), confidence: typeof msg.confidence === "number" ? msg.confidence : undefined };
        h.onUserTranscript({ text: this.lastFinal.text, final: true, confidence: this.lastFinal.confidence });
        h.onState("thinking");
        return;
      case "reply.started":
        this.replyOpen = true;
        h.onState("speaking");
        return;
      case "reply.audio":
        // The payload field is `data` (verified 21 Sep against the live API); `audio` is
        // kept as a fallback in case the field is renamed to match input.audio.
        h.onAgentAudio(fromB64(String(msg.data ?? msg.audio ?? "")), OUT_RATE);
        return;
      case "transcript.agent": {
        const text = String(msg.text ?? "");
        h.onAgentText({ text, cited: [...text.matchAll(/\[(E\d+)\]/g)].map((m) => m[1]) });
        return;
      }
      case "tool.call": {
        const call = msg as unknown as ToolCallMsg;
        const s = this.session!;
        h.onToolStart?.(call.name);
        const out = await this.runTool(call.name, call.arguments ?? {}, {
          incidentId: s.incidentId,
          sessionId: this.aaiSessionId ?? s.sessionId,
          transcript: this.lastFinal.text,
          confidence: this.lastFinal.confidence,
        });
        h.onToolResult(call.name, call.arguments ?? {}, out.result, out.tool_events, out.evidence);
        // Send only when reply.done is the latest event for the turn that carried the call.
        this.pendingResults.push({ call_id: call.call_id, result: JSON.stringify(out.result) });
        if (!this.replyOpen) this.flushResults();
        return;
      }
      case "reply.done": {
        this.replyOpen = false;
        const status = msg.status === "interrupted" ? "interrupted" : "completed";
        h.onAgentDone(status);
        h.onState("listening");
        this.flushResults();
        return;
      }
      case "session.error":
        if (this.resuming) {
          // The 30 s grace window is gone (or the credential was refused): start a
          // fresh session with the same config; the brief is in the system prompt.
          this.resuming = false;
          this.aaiSessionId = null;
          h.onError(`resume refused (${String(msg.code ?? "")}); starting a fresh session`);
          if (this.session) void this.start(this.session, h);
          return;
        }
        h.onError(`${String(msg.code ?? "")} ${String(msg.message ?? "")}`.trim());
        h.onState("error");
        return;
      default:
        return;
    }
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<{ ok: boolean; result: unknown }> {
    const s = this.session;
    if (!s) return { ok: false, result: { error: "no session" } };
    const out = await this.runTool(name, args, {
      incidentId: s.incidentId,
      sessionId: this.aaiSessionId ?? s.sessionId,
      transcript: this.lastFinal.text,
      confidence: this.lastFinal.confidence,
    });
    this.h?.onToolResult(name, args, out.result, out.tool_events, out.evidence);
    return { ok: out.ok, result: out.result };
  }

  private flushResults(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    for (const r of this.pendingResults) this.ws.send(JSON.stringify({ type: "tool.result", ...r }));
    this.pendingResults = [];
  }

  sendAudio(pcm16: Int16Array, sampleRate: number): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const out = resampleTo24k(pcm16, sampleRate);
    this.ws.send(JSON.stringify({ type: "input.audio", audio: b64(new Uint8Array(out.buffer)) }));
  }

  async sendText(text: string): Promise<void> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    // The protocol has no text-input event (only audio). reply.create's one-shot
    // `instructions` is the documented way to make the agent respond to something
    // other than speech, so the typed line rides there. The typed text is also the
    // transcript the server-side consent check sees for consent tools.
    this.lastFinal = { text, confidence: 1 };
    this.ws.send(
      JSON.stringify({
        type: "reply.create",
        instructions: `The engineer just typed: "${text}". Respond to exactly that as if they had spoken it, using your tools as the rules say.`,
      }),
    );
  }

  /** Make the agent speak to a system-side fact (e.g. "CloudWatch says the alarm is back to OK"). */
  async inject(content: string): Promise<void> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ type: "reply.create", instructions: `System update, not the engineer speaking: ${content} Tell the engineer in one sentence.` }));
  }

  interrupt(): void {
    // Interruption is turn-detection driven: speaking over the agent yields reply.done{interrupted}.
    // The button version stops local playback at once; the agent's current reply is simply not played.
    this.h?.onAgentDone("interrupted");
  }

  async stop(): Promise<void> {
    this.stopping = true;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "session.end" }));
    this.ws?.close();
    this.ws = null;
  }
}
