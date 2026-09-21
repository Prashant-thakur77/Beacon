/**
 * One voice interface, two backends.
 *
 * - `aws`: the First Commit cascade (Transcribe streaming → /turn on the voice Lambda → Polly).
 *   The agent runs server-side; the browser only moves audio and text.
 * - `assemblyai`: the Voice Agent API. STT, turn-taking, barge-in, the LLM and TTS run on
 *   AssemblyAI's WebSocket; every tool call comes back to the browser, which forwards it to
 *   POST /tools/<name> on the same voice Lambda with the user's transcript and confidence.
 *
 * Both share TOOL_SCHEMAS (exported by scripts/export_tools.py) and the same six Python tools,
 * so the safety model does not change when the mouth and ears do.
 */
import type { Evidence, ToolEvent } from "../types";

export interface TranscriptEvent {
  text: string;
  final: boolean;
  confidence?: number;
}

export interface AgentReplyEvent {
  text: string;
  cited: string[];
  interrupted?: boolean;
}

export interface VoiceTransportHandlers {
  onUserTranscript: (e: TranscriptEvent) => void;
  onAgentText: (e: AgentReplyEvent) => void;
  onAgentAudio: (pcm16: Int16Array, sampleRate: number) => void;
  onAgentDone: (status: "completed" | "interrupted") => void;
  onToolResult: (name: string, args: Record<string, unknown>, result: unknown, events: ToolEvent[], evidence: Evidence[]) => void;
  /** A tool call has arrived and is about to run (for the latency overlay). */
  onToolStart?: (name: string) => void;
  onState: (state: "connecting" | "listening" | "thinking" | "speaking" | "idle" | "error") => void;
  onError: (message: string) => void;
}

export interface VoiceSession {
  incidentId: string;
  sessionId: string;
  systemPrompt: string;
  greeting?: string;
  keyterms?: string[];
  languageCodes?: string[];
  /** AssemblyAI voice id (english: alba, eve, george, jane, jean, mary, michael, anna, charles, paul, vera). */
  voice?: string;
}

export interface VoiceTransport {
  readonly name: "aws" | "assemblyai";
  start(session: VoiceSession, handlers: VoiceTransportHandlers): Promise<void>;
  /** Push microphone audio (PCM16 mono at `sampleRate`). Full-duplex backends accept it continuously. */
  sendAudio(pcm16: Int16Array, sampleRate: number): void;
  /** Send typed text as if spoken. */
  sendText(text: string): Promise<void>;
  /** Make the agent speak to a system-side fact (the loop resolved or escalated); not the engineer speaking. */
  inject(content: string): Promise<void>;
  /**
   * Run a tool from the console itself, outside any agent turn (barge-in withdraws a
   * proposal through cancel_proposal). Optional: the cascade runs its tools server-side.
   */
  callTool?(name: string, args: Record<string, unknown>): Promise<{ ok: boolean; result: unknown }>;
  /** Interrupt the agent (barge-in). */
  interrupt(): void;
  stop(): Promise<void>;
}
