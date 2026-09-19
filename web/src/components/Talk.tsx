import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Api } from "../api";
import { formatTime } from "../hooks";
import type { Evidence, Incident, Message, MetricSeries, Proposal, TurnResponse } from "../types";
import { RecoverySparkline } from "./Sparkline";
import { Player, speakFallback } from "../voice/player";
import type { SttChoice, SttTransport } from "../voice/stt";
import { TranscribeTransport } from "../voice/transcribe";
import { WebSpeechTransport, webSpeechAvailable } from "../voice/webspeech";

type State = "idle" | "listening" | "thinking" | "speaking" | "error";

const SENTENCE_RE = /[^.!?]+[.!?]+["')\]]*|[^.!?]+$/g;

function splitSentences(text: string): string[] {
  return (text.match(SENTENCE_RE) ?? [text]).map((s) => s.trim()).filter(Boolean);
}

function Sentence({ text, hot, onChip }: { text: string; hot: boolean; onChip: (id: string) => void }) {
  const parts = text.split(/(\[E\d+\])/g);
  return (
    <span className={`sentence${hot ? " hot" : ""}`}>
      {parts.map((p, i) => {
        const m = p.match(/^\[(E\d+)\]$/);
        if (m)
          return (
            <button key={i} className={`chip${hot ? " hot" : ""}`} onClick={() => onChip(m[1])} title={`evidence ${m[1]}`}>
              {m[1]}
            </button>
          );
        return <span key={i}>{p}</span>;
      })}{" "}
    </span>
  );
}

function EvidenceCard({ ev, hot }: { ev: Evidence; hot: boolean }) {
  const body = typeof ev.payload === "string" ? ev.payload : JSON.stringify(ev.payload, null, 1);
  return (
    <div className={`ev${hot ? " hot" : ""}`} id={`ev-${ev.id}`}>
      <div className="eh">
        <span className="id">{ev.id}</span>
        <span>{ev.title}</span>
        <span className="kind">{ev.kind}</span>
      </div>
      <pre>{body.length > 1600 ? body.slice(0, 1600) + "\n…" : body}</pre>
    </div>
  );
}

function FixCard({ incident, proposal, series }: { incident: Incident; proposal: Proposal | null; series: MetricSeries | null }) {
  const status = incident.status;
  if (status === "resolved") {
    const verifies = (incident.timeline ?? []).filter((e) => e.event === "verify_attempt");
    const last = (verifies[verifies.length - 1]?.detail ?? {}) as { checks?: Array<{ name: string; ok: boolean }> };
    const okCount = (last.checks ?? []).filter((c) => c.ok).length;
    return (
      <div className={`fix ${incident.handled_by === "contract" ? "lilac" : "green"}`}>
        <div className="head">
          {incident.handled_by === "contract" ? "☾ Resolved under Sleep Contract" : "✓ Verified recovery"}
          <span className="pill green">{okCount}/3 checks</span>
        </div>
        <div className="small dim">alarm OK after the fix · error count zero · rule present</div>
        <RecoverySparkline series={series} />
      </div>
    );
  }
  if (status === "escalated") {
    return (
      <div className="fix red">
        <div className="head">✗ Escalated to a human</div>
        <div className="small dim">Verification did not pass. Check the timeline for which condition failed.</div>
      </div>
    );
  }
  if (status === "remediating" || status === "auto_remediating") {
    return (
      <div className="fix">
        <div className="head">
          Executing <span className="pill amber pulse">Step Functions loop</span>
        </div>
        <div className="small dim">dry run → require approval → execute → wait 30 s → verify (up to 6×)</div>
        <RecoverySparkline series={series} />
      </div>
    );
  }
  if (!proposal) return null;
  return (
    <div className={`fix${proposal.dry_run.ok ? "" : " red"}`}>
      <div className="head">
        Fix {proposal.fix_id}: <code className="mono">{proposal.action}</code>
        <span className={`pill ${proposal.dry_run.ok ? "green" : "red"}`}>dry run {proposal.dry_run.ok ? "PASSED" : "FAILED"}</span>
      </div>
      <dl className="kv">
        <dt>blast radius</dt>
        <dd>{proposal.blast_radius}</dd>
        <dt>dry run</dt>
        <dd>
          {proposal.dry_run.code} {proposal.dry_run.role ? "· via the remediator role" : ""}
        </dd>
        <dt>params</dt>
        <dd>{JSON.stringify(proposal.params)}</dd>
      </dl>
      {proposal.dry_run.ok ? (
        <div style={{ marginTop: 10 }}>
          awaiting your word: say <span className="phrase">approve fix {proposal.fix_id}</span>
        </div>
      ) : null}
    </div>
  );
}

export function Talk({
  api,
  incident,
  stt,
  replayTurns,
  onIncident,
  unlocked,
  sttLanguage = "en-IN",
  script,
  onScriptDone,
}: {
  api: Api | null;
  incident: Incident;
  stt: SttChoice;
  replayTurns?: TurnResponse[];
  onIncident: (i: Incident) => void;
  unlocked: boolean;
  sttLanguage?: string;
  /** Lines to type on the engineer's behalf, one per idle gap ("Run the night"). */
  script?: string[];
  onScriptDone?: () => void;
}) {
  const [state, setState] = useState<State>("idle");
  const [partial, setPartial] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [hotSentence, setHotSentence] = useState<{ msg: number; idx: number } | null>(null);
  const [hotEvidence, setHotEvidence] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [latency, setLatency] = useState<{ stt?: number; agent?: number; tts?: number }>({});
  const [activeStt, setActiveStt] = useState<SttChoice>(stt);
  const player = useMemo(() => new Player(), []);
  const transport = useRef<SttTransport | null>(null);
  const sessionId = useMemo(() => `s-${Math.random().toString(36).slice(2, 10)}`, []);
  const briefedFor = useRef<string | null>(null);
  const eventFor = useRef<string | null>(null);
  const replayIdx = useRef(0);
  const listenStart = useRef(0);

  const proposal = incident.proposals?.length ? incident.proposals[incident.proposals.length - 1] : null;
  const [series, setSeries] = useState<MetricSeries | null>(null);

  // the alarm's metric, refreshed while the fix is being applied/verified and once after
  useEffect(() => {
    if (replayTurns) {
      // replay: derive a plausible series from the timeline (alarm at start, zero after the fix)
      const tl = incident.timeline ?? [];
      const exec = incident.executed_at ?? tl.find((e) => e.event === "executed")?.t ?? null;
      const t0 = new Date(tl[0]?.t ?? incident.timestamp).getTime() - 4 * 60_000;
      const pts = Array.from({ length: 10 }, (_, i) => {
        const t = new Date(t0 + i * 60_000);
        const before = exec ? t.getTime() < new Date(exec).getTime() : i < 5;
        return { t: t.toISOString(), v: before ? [0, 4, 6, 7, 5, 6][Math.min(i, 5)] : i - 5 === 0 ? 2 : 0 };
      });
      setSeries({ alarm_name: incident.alarm_name ?? "", metric: { namespace: "BeaconDemoInfra", metric_name: "ErrorCount" }, points: pts, executed_at: exec });
      return;
    }
    if (!api) return;
    let alive = true;
    const pull = () => api.metric(incident.incident_id).then((m) => alive && setSeries(m)).catch(() => undefined);
    void pull();
    const active = incident.status === "remediating" || incident.status === "auto_remediating";
    const id = active ? window.setInterval(pull, 10000) : null;
    return () => {
      alive = false;
      if (id) window.clearInterval(id);
    };
  }, [api, incident.incident_id, incident.status, replayTurns]);

  const speak = useCallback(
    (msgIndex: number, resp: TurnResponse) => {
      setState("speaking");
      const done = () => {
        setState("idle");
        setHotSentence(null);
        setHotEvidence(null);
      };
      const sentences = splitSentences(resp.reply_text);
      const onSentence = (i: number) => {
        setHotSentence({ msg: msgIndex, idx: i });
        const m = sentences[i]?.match(/\[(E\d+)\]/);
        setHotEvidence(m ? m[1] : null);
      };
      if (resp.audio_b64) void player.play(resp.audio_b64, resp.speech_marks, onSentence, done);
      else {
        onSentence(0);
        speakFallback(resp.spoken_text || resp.reply_text, done);
      }
    },
    [player],
  );

  const runTurn = useCallback(
    async (body: { text?: string; channel?: string; mode?: "chat" | "brief" | "event"; event?: string }) => {
      setError(null);
      setState("thinking");
      const t0 = performance.now();
      let resp: TurnResponse;
      try {
        if (replayTurns) {
          resp = replayTurns[Math.min(replayIdx.current, replayTurns.length - 1)];
          replayIdx.current += 1;
          await new Promise((r) => setTimeout(r, 400));
        } else if (api) {
          resp = await api.turn({ incident_id: incident.incident_id, session_id: sessionId, lang: sttLanguage, ...body });
        } else throw new Error("no API configured");
      } catch (e) {
        setState("error");
        setError(e instanceof Error ? e.message : String(e));
        return;
      }
      const agentMs = Math.round(performance.now() - t0);
      setLatency((l) => ({ ...l, agent: agentMs, tts: resp.audio_b64 ? 0 : undefined }));
      setEvidence((prev) => [...prev, ...resp.evidence.filter((e) => !prev.some((p) => p.id === e.id))]);
      setMessages((prev) => {
        const next: Message[] = [
          ...prev,
          { role: "beacon", text: resp.reply_text, cited: resp.cited, toolEvents: resp.tool_events, speechMarks: resp.speech_marks, at: new Date().toISOString() },
        ];
        speak(next.length - 1, resp);
        return next;
      });
      if (resp.incident) onIncident(resp.incident);
    },
    [api, incident.incident_id, onIncident, replayTurns, sessionId, speak],
  );

  const send = useCallback(
    (text: string, channel: string) => {
      const clean = text.trim();
      if (!clean) return;
      setMessages((prev) => [...prev, { role: "user", text: clean, channel, at: new Date().toISOString() }]);
      setPartial("");
      void runTurn({ text: clean, channel });
    },
    [runTurn],
  );

  // auto-brief once per incident (once the voice is unlocked), and react to resolved/escalated
  useEffect(() => {
    if (!unlocked && !replayTurns) return;
    if (briefedFor.current !== incident.incident_id) {
      briefedFor.current = incident.incident_id;
      eventFor.current = null;
      setMessages([]);
      setEvidence([]);
      replayIdx.current = 0;
      void runTurn({ mode: "brief" });
    }
  }, [incident.incident_id, runTurn, unlocked, replayTurns]);

  useEffect(() => {
    if ((incident.status === "resolved" || incident.status === "escalated") && eventFor.current !== `${incident.incident_id}:${incident.status}`) {
      eventFor.current = `${incident.incident_id}:${incident.status}`;
      if (messages.length > 0) void runTurn({ mode: "event", event: incident.status });
    }
  }, [incident.status, incident.incident_id, messages.length, runTurn]);

  // "Run the night": type the next scripted line once Beacon has finished speaking.
  const scriptIdx = useRef(0);
  useEffect(() => {
    scriptIdx.current = 0;
  }, [script, incident.incident_id]);
  useEffect(() => {
    if (!script || state !== "idle" || messages.length === 0) return;
    if (scriptIdx.current >= script.length) {
      onScriptDone?.();
      return;
    }
    const id = window.setTimeout(() => {
      const line = script[scriptIdx.current];
      scriptIdx.current += 1;
      send(line, "typed");
    }, 1800);
    return () => window.clearTimeout(id);
  }, [script, state, messages.length, send, onScriptDone]);

  const startListening = useCallback(async () => {
    if (state === "listening" || replayTurns) return;
    player.stop();
    setError(null);
    setPartial("");
    listenStart.current = performance.now();
    let t: SttTransport;
    if (activeStt === "transcribe" && api) t = new TranscribeTransport(() => api.session(), sttLanguage);
    else if (activeStt === "webspeech" || (activeStt === "transcribe" && !api)) t = new WebSpeechTransport(sttLanguage);
    else return;
    transport.current = t;
    setState("listening");
    try {
      await t.start({
        onPartial: (text) => setPartial(text),
        onFinal: (text) => {
          setLatency((l) => ({ ...l, stt: Math.round(performance.now() - listenStart.current) }));
          send(text, t.name);
        },
        onError: (message) => {
          setState("error");
          setError(message);
          if (t.name === "transcribe" && webSpeechAvailable()) {
            setActiveStt("webspeech");
            setError(`${message} — switched to browser speech recognition; press the mic again`);
          }
        },
      });
    } catch (e) {
      setState("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [activeStt, api, player, replayTurns, send, state]);

  const stopListening = useCallback(async () => {
    const t = transport.current;
    transport.current = null;
    if (t) await t.stop();
    setState((s) => (s === "listening" ? "thinking" : s));
    // Transcribe delivers the final result slightly after stop; if nothing came, fall back to the partial
    window.setTimeout(() => {
      setPartial((p) => {
        if (p && t?.name === "transcribe") send(p, "transcribe");
        return "";
      });
      setState((s) => (s === "thinking" && messages.length === 0 ? "idle" : s));
    }, 900);
  }, [messages.length, send]);

  const jumpToEvidence = (id: string) => {
    setHotEvidence(id);
    document.getElementById(`ev-${id}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  const stateLabel: Record<State, string> = {
    idle: "Ready",
    listening: "Listening…",
    thinking: "Thinking…",
    speaking: "Beacon is speaking",
    error: "Error",
  };

  return (
    <div className="talk">
      <div className="panel">
        <div className="panel-h">
          <h2>Talk to Beacon</h2>
          <span className="meta">{incident.alarm_name}</span>
        </div>
        <div className="panel-b stack">
          <div className="mic-wrap">
            <button
              className={`mic${state === "listening" ? " live" : ""}${state === "speaking" ? " speaking" : ""}`}
              onPointerDown={startListening}
              onPointerUp={stopListening}
              onPointerLeave={() => state === "listening" && void stopListening()}
              disabled={!!replayTurns || activeStt === "typed"}
              aria-label="Hold to talk"
              title="Hold to talk"
            >
              {state === "speaking" ? "◉" : "🎙"}
            </button>
            <div className="stack" style={{ gap: 4 }}>
              <div className={`state ${state}`} role="status">{stateLabel[state]}</div>
              <div className="partial">{partial || (state === "listening" ? "…" : unlocked || replayTurns ? "hold the mic and speak, or type below" : "enter the passcode on the left to talk")}</div>
              <div className="row small">
                <label className="faint small" htmlFor="stt-select">mic</label>
                <select className="input" style={{ width: "auto", padding: "4px 8px" }} value={activeStt} onChange={(e) => setActiveStt(e.target.value as SttChoice)} id="stt-select">
                  <option value="transcribe">Transcribe</option>
                  <option value="webspeech">Browser</option>
                  <option value="typed">Typed</option>
                </select>
              </div>
            </div>
          </div>
          {error ? <div className="err">{error}</div> : null}

          <div className="bubbles" aria-live="polite" aria-label="Conversation with Beacon">
            {messages.map((m, mi) =>
              m.role === "user" ? (
                <div key={mi} className="bubble user">
                  <div className="who">
                    you<span className="when">{m.channel} · {formatTime(m.at)}</span>
                  </div>
                  <div>“{m.text}”</div>
                </div>
              ) : (
                <div key={mi} className="bubble beacon">
                  <div className="who">
                    Beacon<span className="when">{formatTime(m.at)}</span>
                  </div>
                  <div>
                    {splitSentences(m.text).map((s, si) => (
                      <Sentence key={si} text={s} hot={hotSentence?.msg === mi && hotSentence.idx === si} onChip={jumpToEvidence} />
                    ))}
                    {m.cited?.length === 0 && evidence.length > 0 ? <span className="unverified">unverified</span> : null}
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
              send(typed, "typed");
              setTyped("");
            }}
          >
            <input
              id="typed-input"
              className="input"
              placeholder={proposal && incident.status === "awaiting_engineer" ? `type: approve fix ${proposal.fix_id}` : "type instead of speaking…"}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              disabled={!!replayTurns || state === "thinking"}
            />
            <button className="btn primary" type="submit" disabled={!!replayTurns || state === "thinking" || !typed.trim()}>
              Send
            </button>
          </form>
          <div className="latency">
            {latency.stt != null ? <span>stt {latency.stt} ms</span> : null}
            {latency.agent != null ? <span>agent {latency.agent} ms</span> : null}
            <span>{replayTurns ? "replay" : "Nova 2 Lite · Strands · Polly"}</span>
          </div>
        </div>
      </div>

      <FixCard incident={incident} proposal={proposal} series={series} />

      {incident.contract_readback_pending ? (
        <div className="fix lilac">
          <div className="head">☾ Sleep Contract read-back pending</div>
          <div className="small">
            say <span className="phrase">grant contract for {String((incident.contract_readback_pending as { days?: number }).days ?? 7)} days</span> to grant it
          </div>
        </div>
      ) : null}

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
