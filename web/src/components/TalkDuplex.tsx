/**
 * Full-duplex Talk for the AssemblyAI Voice Agent backend: the mic streams continuously,
 * the agent can be interrupted mid-sentence, and every tool call still runs on the same
 * server-side tools (consent checked against the transcript AssemblyAI heard).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Api } from "../api";
import type { Config } from "../config";
import { formatTime } from "../hooks";
import type { Evidence, Incident, Message, MetricSeries, ToolEvent } from "../types";
import { PcmPlayer } from "../voice/pcmPlayer";
import { makeTransport, type VoiceBackend } from "../voice/select";
import type { SttChoice } from "../voice/stt";
import type { VoiceTransport } from "../voice/transport";
import { EvidenceCard, FixCard, Sentence, splitSentences } from "./Talk";

type State = "connecting" | "listening" | "thinking" | "speaking" | "idle" | "error";

const SYSTEM_PROMPT_HINT =
  "You are Beacon, the on-call agent for an AWS incident. Use your tools; never guess. Call get_incident_brief first. " +
  "For a fix, call propose_fix, read back the blast radius and the exact phrase 'approve fix <n>'. Only call approve_fix after the engineer says it. " +
  "After a verified fix, offer a Sleep Contract; call grant_sleep_contract, read the read-back aloud, and wait for 'grant contract for <n> days'. " +
  "Cite evidence ids like [E2] at the end of sentences that rely on them. Short sentences. Answer Hinglish with Hinglish.";

export function TalkDuplex({
  api,
  config,
  backend,
  incident,
  stt,
  passcode,
  onIncident,
  series,
}: {
  api: Api;
  config: Config;
  backend: VoiceBackend;
  incident: Incident;
  stt: SttChoice;
  passcode: () => string;
  onIncident: (i: Incident) => void;
  series: MetricSeries | null;
}) {
  const [state, setState] = useState<State>("idle");
  const [partial, setPartial] = useState("");
  const [heard, setHeard] = useState<{ text: string; confidence?: number } | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [hotEvidence, setHotEvidence] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const player = useMemo(() => new PcmPlayer(), []);
  const transport = useRef<VoiceTransport | null>(null);
  const micCtx = useRef<AudioContext | null>(null);
  const micStream = useRef<MediaStream | null>(null);
  const sessionId = useMemo(() => `d-${Math.random().toString(36).slice(2, 10)}`, []);
  const pendingTools = useRef<ToolEvent[]>([]);
  const pendingEvidence = useRef<Evidence[]>([]);

  const proposal = incident.proposals?.length ? incident.proposals[incident.proposals.length - 1] : null;

  const connect = useCallback(async () => {
    setError(null);
    await player.ensure();
    const t = makeTransport(backend, api, config, stt, passcode);
    transport.current = t;
    await t.start(
      {
        incidentId: incident.incident_id,
        sessionId,
        systemPrompt: SYSTEM_PROMPT_HINT,
        greeting: "Beacon here. Give me one second to read the incident.",
        keyterms: [
          incident.alarm_name ?? "",
          ...(incident.diagnostics?.missing_rules ?? []).flatMap((r) => [String(r.group_id ?? ""), String(r.source_group_id ?? "")]),
          "approve fix one",
          "approve fix two",
          "grant contract for seven days",
          "Beacon",
        ].filter(Boolean),
        languageCodes: config.sttLanguage.startsWith("hi") ? ["hi", "en"] : ["en", "hi"],
      },
      {
        onUserTranscript: (e) => {
          if (e.final) {
            setHeard({ text: e.text, confidence: e.confidence });
            setPartial("");
            setMessages((prev) => [...prev, { role: "user", text: e.text, channel: backend, at: new Date().toISOString() }]);
          } else setPartial(e.text);
        },
        onAgentText: (e) => {
          const tools = pendingTools.current;
          pendingTools.current = [];
          setMessages((prev) => [...prev, { role: "beacon", text: e.text, cited: e.cited, toolEvents: tools, at: new Date().toISOString() }]);
          const first = e.cited[0];
          if (first) setHotEvidence(first);
        },
        onAgentAudio: (pcm, rate) => player.push(pcm, rate),
        onAgentDone: (status) => {
          if (status === "interrupted") {
            player.flush();
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last?.role === "beacon") return [...prev.slice(0, -1), { ...last, text: `${last.text} —` , interrupted: true } as Message];
              return prev;
            });
          }
          setHotEvidence(null);
        },
        onToolResult: (name, args, result, events, cards) => {
          const summary = events[0]?.summary ?? (typeof result === "object" && result && "error" in (result as object) ? String((result as { error: string }).error) : "ok");
          pendingTools.current.push({ name, args, summary, evidence_id: cards[0]?.id ?? null });
          const fresh = cards.filter((c) => !pendingEvidence.current.some((p) => p.id === c.id));
          pendingEvidence.current.push(...fresh);
          setEvidence((prev) => [...prev, ...fresh.filter((c) => !prev.some((p) => p.id === c.id))]);
          const inc = (result as { incident?: Incident } | null)?.incident;
          if (inc) onIncident(inc);
          void api.incident(incident.incident_id).then((r) => onIncident(r.incident)).catch(() => undefined);
        },
        onState: (s) => setState(s),
        onError: (m) => setError(m),
      },
    );
    // continuous mic → transport
    micStream.current = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    micCtx.current = new AudioContext();
    await micCtx.current.audioWorklet.addModule("/pcm-worklet.js");
    const source = micCtx.current.createMediaStreamSource(micStream.current);
    const node = new AudioWorkletNode(micCtx.current, "pcm-worklet");
    node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => transport.current?.sendAudio(new Int16Array(e.data), 16000);
    const silent = micCtx.current.createGain();
    silent.gain.value = 0;
    source.connect(node).connect(silent).connect(micCtx.current.destination);
    setLive(true);
  }, [api, backend, config, incident.alarm_name, incident.diagnostics, incident.incident_id, onIncident, passcode, player, sessionId, stt]);

  const disconnect = useCallback(async () => {
    setLive(false);
    micStream.current?.getTracks().forEach((t) => t.stop());
    await micCtx.current?.close().catch(() => undefined);
    micCtx.current = null;
    await transport.current?.stop();
    transport.current = null;
    await player.close();
    setState("idle");
  }, [player]);

  useEffect(() => () => void disconnect(), [disconnect]);

  const jump = (id: string) => {
    setHotEvidence(id);
    document.getElementById(`ev-${id}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  const stateLabel: Record<State, string> = {
    connecting: "Connecting…",
    listening: "Listening",
    thinking: "Thinking…",
    speaking: "Beacon is speaking — talk to interrupt",
    idle: live ? "Ready" : "Not connected",
    error: "Error",
  };

  return (
    <div className="talk">
      <div className="panel">
        <div className="panel-h">
          <h2>Talk to Beacon</h2>
          <span className="meta">{backend === "assemblyai" ? "AssemblyAI Voice Agent · Universal-3 Pro" : "AWS cascade"}</span>
        </div>
        <div className="panel-b stack">
          <div className="mic-wrap">
            <button
              className={`mic${state === "listening" ? " live" : ""}${state === "speaking" ? " speaking" : ""}`}
              onClick={() => (live ? void disconnect() : void connect())}
              aria-label={live ? "Hang up" : "Connect"}
              title={live ? "Hang up" : "Connect"}
            >
              {live ? "●" : "🎙"}
            </button>
            <div className="stack" style={{ gap: 4 }}>
              <div className={`state ${state}`}>{stateLabel[state]}</div>
              <div className="partial">{partial || (heard ? `heard: “${heard.text}”${heard.confidence != null ? ` (${Math.round(heard.confidence * 100)}%)` : ""}` : "full-duplex: just talk")}</div>
              {state === "speaking" ? (
                <button className="btn ghost small" onClick={() => transport.current?.interrupt()}>
                  Interrupt
                </button>
              ) : null}
            </div>
          </div>
          {error ? <div className="err">{error}</div> : null}
          <div className="bubbles">
            {messages.map((m, mi) =>
              m.role === "user" ? (
                <div key={mi} className="bubble user">
                  <div className="who">you · {m.channel} · {formatTime(m.at)}</div>
                  <div>“{m.text}”</div>
                </div>
              ) : (
                <div key={mi} className={`bubble beacon${(m as Message & { interrupted?: boolean }).interrupted ? " interrupted" : ""}`}>
                  <div className="who">
                    Beacon · {formatTime(m.at)}
                    {(m as Message & { interrupted?: boolean }).interrupted ? <span className="pill amber" style={{ marginLeft: 8 }}>interrupted</span> : null}
                  </div>
                  <div>
                    {splitSentences(m.text).map((s, si) => (
                      <Sentence key={si} text={s} hot={false} onChip={jump} />
                    ))}
                  </div>
                  {m.toolEvents?.length ? (
                    <div className="tools">
                      {m.toolEvents.map((t, ti) => (
                        <span key={ti} className="tool" title={JSON.stringify(t.args)}>
                          <b>{t.name}</b> {t.summary}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              ),
            )}
          </div>
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              const text = typed.trim();
              if (!text) return;
              setMessages((prev) => [...prev, { role: "user", text, channel: "typed", at: new Date().toISOString() }]);
              void transport.current?.sendText(text);
              setTyped("");
            }}
          >
            <input id="typed-input-duplex" className="input" placeholder="type instead of speaking…" value={typed} onChange={(e) => setTyped(e.target.value)} disabled={!live} />
            <button className="btn primary" type="submit" disabled={!live || !typed.trim()}>
              Send
            </button>
          </form>
        </div>
      </div>

      <FixCard incident={incident} proposal={proposal} series={series} />

      <div className="panel">
        <div className="panel-h">
          <h2>Evidence</h2>
          <span className="meta">{evidence.length} card(s)</span>
        </div>
        <div className="panel-b dock">
          {evidence.length === 0 ? <p className="dim small" style={{ margin: 0 }}>Every sentence Beacon speaks is pinned to a card here.</p> : null}
          {evidence.map((ev) => (
            <EvidenceCard key={ev.id} ev={ev} hot={hotEvidence === ev.id} />
          ))}
        </div>
      </div>
    </div>
  );
}
