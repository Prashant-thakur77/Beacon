import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { makeApi, type Api } from "./api";
import { loadConfig, type Config } from "./config";
import { ArchivedRunCard, IncidentCard, JudgeCard, TallyStrip } from "./components/NightBoard";
import { Analytics } from "./components/Analytics";
import { Announce, Footer, HelpFab, Toasts, useToasts } from "./components/Chrome";
import { Marquee } from "./components/Marquee";
import { Contracts } from "./components/Contracts";
import { Safety } from "./components/Safety";
import { Talk } from "./components/Talk";
import { useClock, useLocalState, usePoll } from "./hooks";
import { useNavIndicator, useReveal, useScrolled } from "./motion";
import { loadReplay, type ReplayBundle } from "./replay";
import { computeAnalytics } from "./analytics";
import type { Analytics as AnalyticsData, Contract, Health, Incident, Safety as SafetyData, Tally } from "./types";
import { chooseStt } from "./voice/stt";

type Route = "board" | "analytics" | "contracts" | "safety";

/** "Run the night" (local mode): the engineer's lines for the first incident. */
const NIGHT_SCRIPT = ["can you fix it", "approve fix 1", "yes", "grant contract for seven days"];
type Night = null | "first" | "second" | "done";
const NIGHT_LABEL: Record<Exclude<Night, null>, string> = {
  first: "night · incident 1: fix, verify, grant a contract",
  second: "night · incident 2: same fault, handled under the contract",
  done: "night complete · 2 incidents · 1 human woken",
};

function useRoute(): [Route, (r: Route) => void] {
  const read = (): Route => {
    const h = window.location.hash.replace("#", "");
    return h === "contracts" || h === "safety" || h === "analytics" ? h : "board";
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
  const scrolled = useScrolled();
  const navRef = useRef<HTMLElement>(null);
  const mainRef = useRef<HTMLDivElement>(null);
  const stt = useMemo(() => chooseStt(), []);
  const { toasts, push: toast, dismiss } = useToasts();
  // mobile menu: the nav card itself expands downwards
  const [menuOpen, setMenuOpen] = useState(false);
  const topbarRef = useRef<HTMLElement>(null);
  useEffect(() => setMenuOpen(false), [route]);
  useEffect(() => {
    if (!menuOpen) return;
    const bar = topbarRef.current;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMenuOpen(false);
        bar?.querySelector<HTMLElement>(".burger")?.focus();
      }
      if (e.key === "Tab" && bar) {
        const items = Array.from(bar.querySelectorAll<HTMLElement>("a, button, select")).filter((el) => el.offsetParent !== null);
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    bar?.querySelector<HTMLElement>(".nav a")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);
  // route change: back to the top; the main content rises in (CSS)
  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [route]);
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
  // `make local` only: an API that is always unlocked, for the scripted night.
  const localApi: Api | null = useMemo(() => (config?.local && !replay ? makeApi(config, () => config.localPasscode) : null), [config, replay]);
  const [night, setNight] = useState<Night>(null);

  const incidentsQ = usePoll<{ incidents: Incident[] }>(api ? api.incidents : null, 3000, [api], !!api);
  const tallyQ = usePoll<Tally>(api ? api.tally : null, 10000, [api], !!api);
  const contractsQ = usePoll<{ contracts: Contract[] }>(api ? api.contracts : null, 10000, [api], !!api);
  const safetyQ = usePoll<SafetyData>(api ? api.safety : null, 30000, [api], !!api && route === "safety");
  const analyticsQ = usePoll<AnalyticsData>(api ? api.analytics : null, 15000, [api], !!api && route === "analytics");
  const healthQ = usePoll<Health>(api ? api.health : null, 60000, [api], !!api);

  const incidents: Incident[] = replay ? replay.incidents : incidentsQ.data?.incidents ?? [];
  const tally: Tally | null = replay ? replay.tally : tallyQ.data;
  const contracts: Contract[] = replay ? replay.contracts : contractsQ.data?.contracts ?? [];
  const safety: SafetyData | null = replay ? replay.safety : safetyQ.data;
  useNavIndicator(navRef, `${route}:${contracts.length}`);
  useReveal(mainRef, [route, incidents.length, contracts.length, !!safety, !!replay]);
  // Analytics: the API's aggregation when it has it; otherwise the same maths in the browser
  // (replay bundles, and a deployed API that predates GET /analytics).
  const analytics = useMemo<{ data: AnalyticsData | null; loading: boolean; source: "api" | "computed" | "replay" }>(() => {
    if (replay) return { data: computeAnalytics(replay.incidents, replay.contracts, now), loading: false, source: "replay" };
    if (analyticsQ.data && !analyticsQ.error) return { data: analyticsQ.data, loading: false, source: "api" };
    if (api && (analyticsQ.error || incidentsQ.data)) return { data: computeAnalytics(incidents, contracts, now), loading: false, source: "computed" };
    // nothing has answered yet (neither /analytics nor the incidents poll): skeletons, not empty states
    return { data: null, loading: !!api && !incidentsQ.error, source: "computed" };
  }, [replay, analyticsQ.data, analyticsQ.error, api, incidentsQ.data, incidentsQ.error, incidents, contracts, now]);

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
      toast("Contract revoked", "ok");
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    }
  };

  const connected = !!api && !incidentsQ.error;

  const runNight = async () => {
    if (!localApi || !config) return;
    if (!passcode) setPasscode(config.localPasscode);
    setPinned(false);
    setOpenId(null);
    const newest = incidents[0];
    if (!newest || newest.status === "resolved" || newest.status === "escalated") {
      await localApi.localBreak().catch(() => undefined);
      await incidentsQ.refresh();
    }
    setNight("first");
  };
  // `?night=1` starts the scripted night as soon as the board has loaded (local mode only).
  const autoNight = useRef(new URLSearchParams(window.location.search).get("night") === "1");
  useEffect(() => {
    if (autoNight.current && localApi && incidentsQ.data && night === null) {
      autoNight.current = false;
      void runNight();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [localApi, incidentsQ.data, night]);
  const onNightScriptDone = useCallback(() => {
    if (night !== "first" || !localApi) return;
    setNight("second");
    void localApi
      .localBreak()
      .then(() => Promise.all([incidentsQ.refresh(), contractsQ.refresh(), tallyQ.refresh()]))
      .then(() => {
        setNight("done");
        toast("Night complete · 2 incidents · 1 human woken", "ok");
      })
      .catch(() => setNight("done"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [night, localApi]);

  return (
    <div className={`shell${scrolled ? " scrolled" : ""}`} ref={mainRef}>
      {replay ? (
        <Announce text={`Replay of ${new Date(replay.recorded_at).toLocaleDateString([], { day: "2-digit", month: "long", year: "numeric" })}`} href="#board" />
      ) : config?.local ? (
        <Announce
          text="Local mode · moto · Press Run the night"
          onClick={() => {
            if (route !== "board") window.location.hash = "board";
            window.setTimeout(() => {
              const el = document.querySelector<HTMLElement>(".judge");
              el?.setAttribute("open", "");
              el?.scrollIntoView({ behavior: "smooth", block: "start" });
            }, 60);
          }}
        />
      ) : config ? (
        <Announce text={`Live on AWS · ${config.region}${healthQ.data?.version ? ` · v${healthQ.data.version}` : ""} · Judge passcode in the submission`} href="#safety" />
      ) : null}
      <header className={`topbar${menuOpen ? " open" : ""}`} ref={topbarRef}>
        <a className="brand" href="#board" aria-label="Beacon Night Shift, Night Board">
          <span className="dot" />
          Beacon <span className="sub">Night Shift</span>
        </a>
        <button type="button" className={`burger${menuOpen ? " x" : ""}`} aria-label={menuOpen ? "Close menu" : "Open menu"} aria-expanded={menuOpen} aria-controls="site-nav" onClick={() => setMenuOpen((o) => !o)}>
          <span />
          <span />
          <span />
        </button>
        <nav className="nav" id="site-nav" aria-label="Sections" ref={navRef}>
          <a href="#board" className={route === "board" ? "active" : ""} aria-current={route === "board" ? "page" : undefined}>
            Night Board
          </a>
          <a href="#analytics" className={route === "analytics" ? "active" : ""} aria-current={route === "analytics" ? "page" : undefined}>
            Analytics
          </a>
          <a href="#contracts" className={route === "contracts" ? "active" : ""} aria-current={route === "contracts" ? "page" : undefined}>
            Contracts{contracts.length ? <span className="count">{contracts.length}</span> : null}
          </a>
          <a href="#safety" className={route === "safety" ? "active" : ""} aria-current={route === "safety" ? "page" : undefined}>
            Safety
          </a>
        </nav>
        <div className="status" role="status" aria-live="polite">
          {replay ? <span className="pill lilac">REPLAY of {new Date(replay.recorded_at).toLocaleString()}</span> : null}
          {!replay && api ? <span className={`pill ${connected ? "green" : "red"}${connected ? "" : " pulse"}`}>{connected ? "live" : "reconnecting…"}</span> : null}
          {!replay && !api && config ? <span className="pill dim">no API configured</span> : null}
          {night ? <span className="pill lilac">{NIGHT_LABEL[night]}</span> : null}
          <span className="status-meta mono" title={healthQ.data?.stack ? `stack ${healthQ.data.stack}` : undefined}>
            {config?.local ? "local · moto" : config?.region ?? ""}
            {healthQ.data?.version ? <span className="ver"> · v{healthQ.data.version}</span> : null}
          </span>
          <span className="clock">{wall.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
        </div>
      </header>

      {route === "board" ? (
        <main key="board" className={`main route-in${current ? " two-col" : ""}`}>
          <section className="stack">
            <TallyStrip tally={tally} />
            {!replay && !passcode ? (
              <form
                className="unlock"
                onSubmit={(e) => {
                  e.preventDefault();
                  const v = (new FormData(e.currentTarget).get("passcode") as string) ?? "";
                  setPasscode(v.trim());
                  if (v.trim()) toast("Passcode saved for this tab", "ok");
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
            <JudgeCard
              hasPasscode={!!passcode}
              open={new URLSearchParams(window.location.search).get("judge") === "1" || incidents.length === 0}
              onRunNight={localApi && night !== "first" && night !== "second" ? runNight : undefined}
            />
            <div className="feed">
              {incidents.length === 0 ? (
                <div className="panel">
                  <ArchivedRunCard onReplay={startReplay} />
                </div>
              ) : (
                incidents.map((i, idx) => (
                  <IncidentCard
                    key={i.incident_id}
                    index={idx}
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
                script={night === "first" ? NIGHT_SCRIPT : undefined}
                onScriptDone={onNightScriptDone}
              />
            </section>
          ) : null}
        </main>
      ) : route === "analytics" ? (
        <main key="analytics" className="main route-in">
          <Analytics data={analytics.data} loading={analytics.loading} source={analytics.source} now={now} />
        </main>
      ) : route === "contracts" ? (
        <main key="contracts" className="main route-in">
          <div className="page-h">
            <h1 className="display">
              Standing approvals, <em>in your own words.</em>
            </h1>
          </div>
          <Contracts contracts={contracts} now={now} onRevoke={revoke} canRevoke={!!api && !!passcode} />
        </main>
      ) : (
        <main key="safety" className="main route-in">
          <div className="page-h">
            <h1 className="display">
              Two roles, <em>one direction.</em>
            </h1>
            <p className="lede">What Beacon is allowed to do, proven by tests, and the switch that stops every write path.</p>
          </div>
          <Safety safety={safety} />
        </main>
      )}
      {route === "board" || route === "safety" ? (
        <div className="band">
          <Marquee label="Built on" />
        </div>
      ) : null}
      <Footer version={healthQ.data?.version} />
      <Toasts items={toasts} onDismiss={dismiss} />
      <HelpFab onBoard={route === "board"} hasPasscode={!!passcode} />
    </div>
  );
}
