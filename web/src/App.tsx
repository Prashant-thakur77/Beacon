import { useCallback, useEffect, useMemo, useState } from "react";
import { makeApi, type Api } from "./api";
import { loadConfig, type Config } from "./config";
import { ArchivedRunCard, IncidentCard, JudgeCard, TallyStrip } from "./components/NightBoard";
import { Contracts } from "./components/Contracts";
import { Safety } from "./components/Safety";
import { Talk } from "./components/Talk";
import { useClock, useLocalState, usePoll } from "./hooks";
import { loadReplay, type ReplayBundle } from "./replay";
import type { Contract, Incident, Safety as SafetyData, Tally } from "./types";
import { chooseStt } from "./voice/stt";

type Route = "board" | "contracts" | "safety";

function useRoute(): [Route, (r: Route) => void] {
  const read = (): Route => {
    const h = window.location.hash.replace("#", "");
    return h === "contracts" || h === "safety" ? h : "board";
  };
  const [route, setRoute] = useState<Route>(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [route, (r) => (window.location.hash = r)];
}

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [replay, setReplay] = useState<ReplayBundle | null>(null);
  const [passcode, setPasscode] = useLocalState("beacon.passcode", "");
  const [route] = useRoute();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const wall = useClock();
  const stt = useMemo(() => chooseStt(), []);
  // In replay, ages are relative to the recording (the last event), not to today.
  const now = useMemo(() => {
    if (!replay) return wall;
    const last = replay.incidents.flatMap((i) => (i.timeline ?? []).map((e) => new Date(e.t).getTime())).filter((n) => !Number.isNaN(n));
    return new Date(last.length ? Math.max(...last) + 90_000 : wall.getTime());
  }, [replay, wall]);

  useEffect(() => {
    void loadConfig().then(async (c) => {
      setConfig(c);
      if (c.replay || !c.dashboardUrl) {
        try {
          setReplay(await loadReplay());
        } catch {
          /* no bundle: empty board */
        }
      }
    });
  }, []);

  const api: Api | null = useMemo(() => (config && config.dashboardUrl && !replay ? makeApi(config, () => passcode) : null), [config, passcode, replay]);

  const incidentsQ = usePoll<{ incidents: Incident[] }>(api ? api.incidents : null, 3000, [api], !!api);
  const tallyQ = usePoll<Tally>(api ? api.tally : null, 10000, [api], !!api);
  const contractsQ = usePoll<{ contracts: Contract[] }>(api ? api.contracts : null, 10000, [api], !!api);
  const safetyQ = usePoll<SafetyData>(api ? api.safety : null, 30000, [api], !!api && route === "safety");

  const incidents: Incident[] = replay ? replay.incidents : incidentsQ.data?.incidents ?? [];
  const tally: Tally | null = replay ? replay.tally : tallyQ.data;
  const contracts: Contract[] = replay ? replay.contracts : contractsQ.data?.contracts ?? [];
  const safety: SafetyData | null = replay ? replay.safety : safetyQ.data;

  // follow the newest incident automatically until the user picks one
  const [pinned, setPinned] = useState(false);
  useEffect(() => {
    if (!pinned && incidents.length && incidents[0].incident_id !== selectedId) setSelectedId(incidents[0].incident_id);
  }, [incidents, pinned, selectedId]);

  const selected = incidents.find((i) => i.incident_id === selectedId) ?? null;
  const [live, setLive] = useState<Incident | null>(null);
  const detailQ = usePoll<{ incident: Incident }>(
    api && selectedId ? () => api.incident(selectedId) : null,
    2000,
    [api, selectedId],
    !!api && !!selectedId && !["resolved", "escalated"].includes(live?.status ?? selected?.status ?? ""),
  );
  useEffect(() => {
    if (detailQ.data?.incident) setLive(detailQ.data.incident);
  }, [detailQ.data]);
  useEffect(() => setLive(null), [selectedId]);
  const current = (live && live.incident_id === selectedId ? live : selected) ?? null;

  const onIncident = useCallback((i: Incident) => setLive(i), []);

  const startReplay = async () => {
    try {
      const bundle = await loadReplay();
      setReplay(bundle);
      setPinned(false);
      setSelectedId(null);
    } catch {
      /* nothing recorded yet */
    }
  };

  const revoke = async (id: string) => {
    if (!api) return;
    try {
      await api.revoke(id);
      await contractsQ.refresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    }
  };

  const connected = !!api && !incidentsQ.error;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="dot" />
          Beacon <span className="sub">Night Shift</span>
        </div>
        {replay ? <span className="pill lilac">REPLAY of {new Date(replay.recorded_at).toLocaleString()}</span> : null}
        {!replay && api ? <span className={`pill ${connected ? "green" : "red"}`}>{connected ? "live" : "reconnecting…"}</span> : null}
        <nav className="nav">
          <a href="#board" className={route === "board" ? "active" : ""}>
            Night Board
          </a>
          <a href="#contracts" className={route === "contracts" ? "active" : ""}>
            Contracts {contracts.length ? `(${contracts.length})` : ""}
          </a>
          <a href="#safety" className={route === "safety" ? "active" : ""}>
            Safety
          </a>
        </nav>
        <span className="clock">{wall.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
      </header>

      {route === "board" ? (
        <main className={`main${current ? " two-col" : ""}`}>
          <section className="stack">
            <TallyStrip tally={tally} />
            {!replay && !passcode ? (
              <form
                className="unlock"
                onSubmit={(e) => {
                  e.preventDefault();
                  const v = (new FormData(e.currentTarget).get("passcode") as string) ?? "";
                  setPasscode(v.trim());
                }}
              >
                <div className="row">
                  <input id="passcode" name="passcode" className="input" style={{ maxWidth: 240 }} type="password" placeholder="passcode" autoComplete="off" />
                  <button className="btn primary" type="submit">
                    Unlock voice
                  </button>
                  <span className="faint small">reads are public · talking and approving need the passcode</span>
                </div>
              </form>
            ) : null}
            {incidentsQ.error && !replay ? <div className="err">Dashboard API: {incidentsQ.error}</div> : null}
            <JudgeCard hasPasscode={!!passcode} open={new URLSearchParams(window.location.search).get("judge") === "1" || incidents.length === 0} />
            <div className="feed">
              {incidents.length === 0 ? (
                <div className="panel">
                  <ArchivedRunCard onReplay={startReplay} />
                </div>
              ) : (
                incidents.map((i) => (
                  <IncidentCard
                    key={i.incident_id}
                    incident={i.incident_id === current?.incident_id ? current : i}
                    now={now}
                    selected={i.incident_id === selectedId}
                    open={openId === i.incident_id}
                    onSelect={() => {
                      setPinned(true);
                      setSelectedId(i.incident_id);
                    }}
                    onToggle={() => setOpenId(openId === i.incident_id ? null : i.incident_id)}
                  />
                ))
              )}
            </div>
          </section>
          {current ? (
            <section>
              <Talk
                key={current.incident_id}
                api={api}
                incident={current}
                stt={stt}
                replayTurns={replay ? replay.turns[current.incident_id] ?? [] : undefined}
                onIncident={onIncident}
                unlocked={!!passcode}
                sttLanguage={config?.sttLanguage}
              />
            </section>
          ) : null}
        </main>
      ) : route === "contracts" ? (
        <main className="main">
          <Contracts contracts={contracts} now={now} onRevoke={revoke} canRevoke={!!api && !!passcode} />
        </main>
      ) : (
        <main className="main">
          <Safety safety={safety} />
        </main>
      )}
    </div>
  );
}
