import type { Api } from "../api";
import type { Config } from "../config";
import { trimSlash } from "../config";
import { AssemblyAITransport } from "./assemblyai";
import { AwsCascadeTransport } from "./awsCascade";
import type { SttChoice } from "./stt";
import type { VoiceTransport } from "./transport";

export type VoiceBackend = "aws" | "assemblyai";

export function chooseBackend(config: Config): VoiceBackend {
  const q = new URLSearchParams(window.location.search).get("voice");
  if (q === "aws" || q === "assemblyai") return q;
  return config.voiceBackend === "assemblyai" ? "assemblyai" : "aws";
}

/** Build the voice transport for this session. Both backends run the same six server-side tools. */
export function makeTransport(backend: VoiceBackend, api: Api, config: Config, stt: SttChoice, passcode: () => string): VoiceTransport {
  if (backend === "assemblyai") {
    const voice = trimSlash(config.voiceUrl);
    const headers = () => ({ "content-type": "application/json", "x-beacon-passcode": passcode() });
    return new AssemblyAITransport(
      async () => {
        const resp = await fetch(`${voice}/assemblyai/token`, { method: "POST", headers: headers(), body: "{}" });
        if (!resp.ok) throw new Error(`token: ${resp.status}`);
        return (await resp.json()) as { token: string };
      },
      async (name, args, ctx) => {
        const resp = await fetch(`${voice}/tools/${name}`, {
          method: "POST",
          headers: headers(),
          body: JSON.stringify({ incident_id: ctx.incidentId, session_id: ctx.sessionId, args, transcript: ctx.transcript, confidence: ctx.confidence, channel: "assemblyai" }),
        });
        return (await resp.json()) as { ok: boolean; result: unknown; tool_events: never[]; evidence: never[] };
      },
    );
  }
  return new AwsCascadeTransport(api, stt, config.sttLanguage);
}
