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
  "You are Beacon, the on-call agent for an AWS incident, speaking to a tired engineer at 3 AM. " +
  "The incident brief is already in this conversation; do not call get_incident_brief unless asked to re-brief. " +
  "When in doubt, call the tool: a wasted call is fine, a missed one is not. propose_fix and check_recovery change nothing, so never ask permission before calling them. " +
  "Examples: user: 'can you fix it' -> call propose_fix immediately, then say its blast_radius_spoken and the exact phrase 'approve fix <n>'. " +
  "user: 'fix it' / 'restore it' / 'repair it' -> same, call propose_fix immediately. " +
  "user: 'approve fix 1' -> call approve_fix with fix_id 1 and confirmation_phrase 'approve fix 1'. user: 'yes' or 'do it' after a proposal -> do NOT call approve_fix; say: say exactly 'approve fix <n>'. " +
  "user: 'what changed' / 'why' -> call get_evidence with kind 'changes'. user: 'undo fix 1' -> call undo_fix. user: 'is it fixed' -> call check_recovery. " +
  "After a tool reports a verified fix, offer a Sleep Contract in one sentence. When the engineer agrees (yes, sure, haan) call grant_sleep_contract with days 7 and max_uses 3 without asking anything; it returns read_back_spoken: speak that word for word and then wait. " +
  "When the engineer then says 'grant contract for <n> days', call grant_sleep_contract again with days <n> and max_uses 3, and the tool grants it. Never ask the engineer how many days; the read-back and the phrase decide that. " +
  "Voice style: at most two short sentences per turn. Never read resource ids or hashes aloud; say 'the R D S security group' or 'the E C S service'. Spell acronyms as letters (R D S, E C S, U S east 1). " +
  "Cite evidence ids like [E2] at the end of a sentence that relies on them. " +
  "If the engineer speaks Hindi (the transcript may be in Devanagari) or Hinglish, answer in Hinglish written in Roman script, keeping technical words in English (example: 'Security group ka rule wapas laga dunga, bolo approve fix one'). Otherwise answer in English.";

/** The brief as system context, so the first real turn needs no tool round trip. */
function briefContext(incident: Incident): string {
  const rca = incident.rca_json ?? {};
  const change = (incident.changes ?? [])[0];
  const missing = (incident.diagnostics?.missing_rules ?? [])[0];
  return [
    `Incident ${incident.incident_id} on alarm ${incident.alarm_name ?? "unknown"}, status ${incident.status}, severity ${rca.status ?? "unknown"}.`,
    rca.spoken_summary || rca.summary ? `Summary: ${rca.spoken_summary || rca.summary}` : "",
    change ? `Change ledger: ${String(change.event_name)} by ${String(change.actor_short ?? "unknown")} at ${String(change.event_time ?? "?")} on ${((change.resource_ids as string[] | undefined) ?? []).join(", ")}.` : "",
    missing ? `Drift: rule ${String(missing.ip_protocol ?? "tcp")}/${String(missing.from_port ?? "?")} from ${String(missing.source_group_id ?? "?")} is missing on ${String(missing.group_id ?? "?")}; the allowlisted fix is sg.restore_ingress.` : "",
  ]
    .filter(Boolean)
    .join(" ");
}

function greetingFor(incident: Incident): string {
  const rca = incident.rca_json ?? {};
  const s = String(rca.spoken_summary || rca.summary || "").split(/(?<=\.)\s/)[0];
  if (incident.handled_by === "contract" && incident.status === "resolved") return "This one was handled under your Sleep Contract; you were not woken. Ask me anything about it.";
  if (incident.status === "resolved") return "This incident is resolved. Ask me what happened, or whether to handle it myself next time.";
  return s ? `${s.replace(/\s+sg-[0-9a-f]+/g, " the security group")} Say fix it, and I will propose the fix.` : "Beacon here. Ask me what is going on.";
}

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
  /** fix_id proposed inside the reply now being spoken; interrupting that reply withdraws it. */
  const proposalInReply = useRef<number | null>(null);
  const replySpoke = useRef(false);
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
        systemPrompt: `${SYSTEM_PROMPT_HINT}\n\n${briefContext(incident)}`,
        greeting: greetingFor(incident),
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
          replySpoke.current = true;
          const tools = pendingTools.current;
          pendingTools.current = [];
          setMessages((prev) => [...prev, { role: "beacon", text: e.text, cited: e.cited, toolEvents: tools, at: new Date().toISOString() }]);
          const first = e.cited[0];
          if (first) setHotEvidence(first);
        },
        onAgentAudio: (pcm, rate) => player.push(pcm, rate),
        onAgentDone: (status) => {
          // A tool call closes one reply and the read-back is the next one, so the proposal
          // stays armed until a reply that actually spoke completes.
          const spoke = replySpoke.current;
          replySpoke.current = false;
          const proposal = proposalInReply.current;
          if (status === "completed" && spoke) proposalInReply.current = null;
          if (status === "interrupted") {
            proposalInReply.current = null;
            player.flush();
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last?.role === "beacon") return [...prev.slice(0, -1), { ...last, text: `${last.text} —`, interrupted: true }];
              return prev;
            });
            // Barge-in that means something: speaking over a read-back withdraws the fix it
            // was reading. The engineer has to ask again; "approve fix n" no longer works.
            if (proposal !== null && t.callTool) {
              void t
                .callTool("cancel_proposal", { fix_id: proposal, reason: "engineer interrupted the read-back" })
                .then(({ result }) => {
                  const withdrawn = Boolean((result as { withdrawn?: boolean } | null)?.withdrawn);
                  if (!withdrawn) return;
                  setMessages((prev) => [...prev, { role: "beacon", note: true, text: `Fix ${proposal} withdrawn — you spoke over the read-back, so nothing was applied. Say "fix it" to propose it again.`, at: new Date().toISOString() }]);
                  void t.inject(`The engineer interrupted the read-back, so fix ${proposal} was withdrawn and cannot be approved. Nothing was applied. Ask in one short sentence what they want; call propose_fix again only if they ask for the fix.`);
                })
                .catch(() => undefined);
            }
          }
          setHotEvidence(null);
        },
        onToolResult: (name, args, result, events, cards) => {
          const fixId = (result as { fix_id?: number } | null)?.fix_id;
          if (name === "propose_fix" && typeof fixId === "number") proposalInReply.current = fixId;
          else if (name === "approve_fix" || name === "cancel_proposal") proposalInReply.current = null;
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

  // Esc interrupts Beacon mid-sentence (same as speaking over it, or the Interrupt button).
  useEffect(() => {
    if (!live) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || (e.target as HTMLElement | null)?.tagName === "INPUT") return;
      transport.current?.interrupt();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [live]);

  // Agent-initiated turn: when the loop resolves or escalates while we are live, Beacon speaks first.
  const announcedFor = useRef<string | null>(null);
  useEffect(() => {
    const key = `${incident.incident_id}:${incident.status}`;
    if (!live || !transport.current) return;
    if (announcedFor.current === null) {
      announcedFor.current = key; // do not announce the state we connected in
      return;
    }
    if (announcedFor.current === key) return;
    announcedFor.current = key;
    if (incident.status === "resolved") {
      const verifies = (incident.timeline ?? []).filter((e) => e.event === "verify_attempt");
      const last = (verifies[verifies.length - 1]?.detail ?? {}) as { attempt?: number };
      void transport.current.inject(
        `CloudWatch has verified the recovery: the alarm is back to OK after the fix, the error metric is zero and the rule is present (verify attempt ${last.attempt ?? 1}). ` +
          "Tell the engineer, then offer a Sleep Contract in one sentence.",
      );
    } else if (incident.status === "escalated") {
      void transport.current.inject("Verification did not pass and the loop escalated to a human. Say so plainly and ask what they want to do.");
    }
  }, [incident.status, incident.incident_id, incident.timeline, live]);

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
          <span className="meta">{backend === "assemblyai" ? "AssemblyAI · Universal-3 Pro" : "AWS cascade"}</span>
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
              {live ? (
                <button
                  className="btn ghost small"
                  aria-label="Interrupt"
                  title="Stop Beacon mid-sentence (Esc). Interrupting a read-back withdraws the fix it was reading."
                  disabled={state !== "speaking" && state !== "thinking"}
                  onClick={() => transport.current?.interrupt()}
                >
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
                  <div className="who">
                    you<span className="when">{m.channel} · {formatTime(m.at)}</span>
                  </div>
                  <div>“{m.text}”</div>
                </div>
              ) : (
                <div key={mi} className={`bubble beacon${m.interrupted ? " interrupted" : ""}${m.note ? " note" : ""}`}>
                  <div className="who">
                    Beacon<span className="when">{formatTime(m.at)}</span>
                    {m.interrupted ? <span className="pill amber">interrupted</span> : null}
                    {m.note ? <span className="pill">console</span> : null}
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
