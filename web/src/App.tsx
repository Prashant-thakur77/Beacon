import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { makeApi, type Api } from "./api";
import { loadConfig, type Config } from "./config";
import { ArchivedRunCard, IncidentCard, JudgeCard, TallyStrip } from "./components/NightBoard";
import { Analytics } from "./components/Analytics";
import { Audit, Postmortem, Report } from "./components/Reports";
import { auditRows, morningReport, postmortemMd } from "./reports";
import { Landing } from "./components/Landing";
import { Announce, Footer, HelpFab, Toasts, useToasts } from "./components/Chrome";
import { Marquee } from "./components/Marquee";
import { Contracts } from "./components/Contracts";
import { Safety } from "./components/Safety";
import { Talk } from "./components/Talk";
import { TalkDuplex } from "./components/TalkDuplex";
import { chooseBackend } from "./voice/select";
import { useClock, useLocalState, usePoll } from "./hooks";
import { useNavIndicator, useReveal, useScrolled, useCursorMotion } from "./motion";
import { loadReplay, type ReplayBundle } from "./replay";
import { computeAnalytics } from "./analytics";
import type { Analytics as AnalyticsData, AuditRow, Contract, Health, Incident, MorningReport, Safety as SafetyData, Tally } from "./types";
import { chooseStt } from "./voice/stt";

type Route = "home" | "board" | "analytics" | "contracts" | "audit" | "report" | "safety" | "postmortem" | "notfound";
const ROUTES = ["home", "board", "analytics", "contracts", "audit", "report", "safety"] as const;
const KEY_ROUTES: Record<string, Route> = { h: "home", b: "board", a: "analytics", c: "contracts", u: "audit", p: "report", s: "safety" };

/** "Run the night" (local mode): the engineer's lines for the first incident. */
const NIGHT_SCRIPT = ["can you fix it", "approve fix 1", "yes", "grant contract for seven days"];
type Night = null | "first" | "second" | "done";
const NIGHT_LABEL: Record<Exclude<Night, null>, string> = {
  first: "night · incident 1: fix, verify, grant a contract",
  second: "night · incident 2: same fault, handled under the contract",
  done: "night complete · 2 incidents · 1 human woken",
};

/** `#board/<incident_id>` deep-links an incident; unknown hashes get the 404 card. */
function parseHash(): { route: Route; deepId: string | null } {
  const h = window.location.hash.replace("#", "");
  const [head, ...rest] = h.split("/");
  if (head === "board") return { route: "board", deepId: rest[0] ? decodeURIComponent(rest[0]) : null };
  if (head === "postmortem" && rest[0]) return { route: "postmortem", deepId: decodeURIComponent(rest[0]) };
  if ((ROUTES as readonly string[]).includes(head)) return { route: head as Route, deepId: null };
  if (h === "" && new URLSearchParams(window.location.search).get("night") === "1") return { route: "board", deepId: null };
  if (h === "") return { route: "home", deepId: null };
  return { route: "notfound", deepId: null };
}

function useRoute(): [Route, string | null] {
  const [state, setState] = useState(parseHash);
  useEffect(() => {
    const on = () => setState(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [state.route, state.deepId];
}

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [replay, setReplay] = useState<ReplayBundle | null>(null);
  const [passcode, setPasscode] = useLocalState("beacon.passcode", "");
  // Side-by-side for the judges: the live AssemblyAI backend or the AWS cascade, per browser.
  const [voicePick, setVoicePick] = useLocalState("beacon.voiceBackend", "");
  const [route, deepId] = useRoute();
  const [filter, setFilter] = useState<"all" | "needs" | "progress" | "resolved" | "contract">("all");
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
  // keyboard: g then h/b/a/c/s routes, "/" focuses the message box, "?" opens help, Esc closes
  useEffect(() => {
    let pendingG = 0;
    const typing = (el: Element | null) => !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || (el as HTMLElement).isContentEditable);
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Escape") {
        (document.activeElement as HTMLElement | null)?.blur?.();
        return;
      }
      if (typing(document.activeElement)) return;
      if (e.key === "g") {
        pendingG = Date.now();
        return;
      }
      if (pendingG && Date.now() - pendingG < 1500 && KEY_ROUTES[e.key]) {
        pendingG = 0;
        window.location.hash = KEY_ROUTES[e.key];
        return;
      }
      pendingG = 0;
      if (e.key === "/") {
        const box = document.querySelector<HTMLElement>(".composer input, .composer textarea");
        if (box) {
          e.preventDefault();
          box.focus();
        }
      } else if (e.key === "?") {
        e.preventDefault();
        document.querySelector<HTMLElement>(".fab")?.click();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  // route change: back to the top; the main content rises in (CSS)
  useEffect(() => {
    window.scrollTo({ top: 0 });
    const titles: Record<Route, string> = {
      home: "Beacon Night Shift — the 3 AM page, fixed with your voice",
      board: "Night Board · Beacon Night Shift",
      analytics: "Analytics · Beacon Night Shift",
      contracts: "Sleep Contracts · Beacon Night Shift",
      safety: "Safety · Beacon Night Shift",
      audit: "Audit · Beacon Night Shift",
      report: "Morning report · Beacon Night Shift",
      postmortem: "Postmortem · Beacon Night Shift",
      notfound: "Nothing here · Beacon Night Shift",
    };
    document.title = titles[route];
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
  const auditQ = usePoll<{ rows: AuditRow[]; count: number }>(api ? api.audit : null, 15000, [api], !!api && route === "audit");
  const [reportNight, setReportNight] = useState<string | null>(null);
  const reportQ = usePoll<MorningReport>(api ? () => api.report(reportNight ?? undefined) : null, 60000, [api, reportNight], !!api && route === "report");

  const incidents: Incident[] = replay ? replay.incidents : incidentsQ.data?.incidents ?? [];
  const tally: Tally | null = replay ? replay.tally : tallyQ.data;
  const contracts: Contract[] = replay ? replay.contracts : contractsQ.data?.contracts ?? [];
  const safety: SafetyData | null = replay ? replay.safety : safetyQ.data;
  // Replay prefers what the server rendered at export time; browser mirrors cover older bundles and a pre-feature API.
  const audit: AuditRow[] | null = replay ? replay.audit ?? auditRows(replay.incidents, replay.contracts) : auditQ.data?.rows ?? (auditQ.error ? auditRows(incidents, contracts) : null);
  const report: MorningReport | null = replay
    ? replay.report && (!reportNight || reportNight === replay.report.night_of)
      ? replay.report
      : morningReport(replay.incidents, replay.contracts, reportNight ?? undefined)
    : reportQ.data ?? (reportQ.error ? morningReport(incidents, contracts, reportNight ?? undefined) : null);
  const postmortemFallback = useCallback(() => {
    if (!deepId) return null;
    const served = replay?.postmortems?.[deepId];
    if (served) return served;
    const inc = incidents.find((i) => i.incident_id === deepId);
    return inc ? postmortemMd(inc, contracts) : null;
  }, [deepId, incidents, contracts, replay]);
  useNavIndicator(navRef, `${route}:${contracts.length}`);
  useReveal(mainRef, [route, incidents.length, contracts.length, !!safety, !!replay]);
  useCursorMotion();
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

  useEffect(() => {
    if (!deepId || route !== "board") return;
    setPinned(true);
    setSelectedId(deepId);
    setOpenId(deepId);
    window.setTimeout(() => {
      const phone = window.matchMedia?.("(max-width: 760px)").matches;
      document.querySelector<HTMLElement>(phone ? ".talk" : ".card.selected")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 150);
  }, [deepId, route]);
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
      toast("Showing the archived night (replay)", "info");
    } catch {
      toast("No archived night in this build", "info");
    }
  };
  const exitReplay = () => {
    setReplay(null);
    setPinned(false);
    setSelectedId(null);
  };
  const freshDeployment = !replay && !!api && !incidentsQ.error && !!incidentsQ.data && incidents.length === 0;

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
      ) : freshDeployment ? (
        <Announce text="No incidents tonight · view the archived night" onClick={() => void startReplay()} />
      ) : config ? (
        <Announce text={`Live on AWS · ${config.region}${healthQ.data?.version ? ` · v${healthQ.data.version}` : ""} · Judge passcode in the submission`} href="#safety" />
      ) : null}
      <header className={`topbar${menuOpen ? " open" : ""}`} ref={topbarRef}>
        <a className="brand" href="#home" aria-label="Beacon Night Shift, home">
          <span className="dot" />
          Beacon <span className="sub">Night Shift</span>
        </a>
        <button type="button" className={`burger${menuOpen ? " x" : ""}`} aria-label={menuOpen ? "Close menu" : "Open menu"} aria-expanded={menuOpen} aria-controls="site-nav" onClick={() => setMenuOpen((o) => !o)}>
          <span />
          <span />
          <span />
        </button>
        <nav className="nav" id="site-nav" aria-label="Sections" ref={navRef}>
          <a href="#home" className={route === "home" ? "active" : ""} aria-current={route === "home" ? "page" : undefined}>
            Home
          </a>
          <a href="#board" className={route === "board" ? "active" : ""} aria-current={route === "board" ? "page" : undefined}>
            Night Board
          </a>
          <a href="#analytics" className={route === "analytics" ? "active" : ""} aria-current={route === "analytics" ? "page" : undefined}>
            Analytics
          </a>
          <a href="#contracts" className={route === "contracts" ? "active" : ""} aria-current={route === "contracts" ? "page" : undefined}>
            Contracts{contracts.length ? <span className="count">{contracts.length}</span> : null}
          </a>
          <a href="#audit" className={route === "audit" ? "active" : ""} aria-current={route === "audit" ? "page" : undefined}>
            Audit
          </a>
          <a href="#report" className={route === "report" ? "active" : ""} aria-current={route === "report" ? "page" : undefined}>
            Report
          </a>
          <a href="#safety" className={route === "safety" ? "active" : ""} aria-current={route === "safety" ? "page" : undefined}>
            Safety
          </a>
        </nav>
        <div className="status" role="status" aria-live="polite">
          {replay ? <span className="pill lilac">REPLAY of {new Date(replay.recorded_at).toLocaleString()}</span> : null}
          {replay && config?.dashboardUrl && !config.replay ? (
            <button type="button" className="btn ghost small" onClick={exitReplay}>
              back to live
            </button>
          ) : null}
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

      {route === "home" ? (
        <Landing tally={tally} replay={!!replay} local={!!config?.local} onRunNight={localApi ? () => (window.location.href = `${window.location.pathname}?night=1#board`) : undefined} />
      ) : route === "board" ? (
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
            {incidents.length > 1 ? (
              <div className="chips" role="group" aria-label="Filter incidents">
                {(
                  [
                    ["all", "All"],
                    ["needs", "needs you"],
                    ["progress", "in progress"],
                    ["resolved", "resolved"],
                    ["contract", "handled by contract"],
                  ] as const
                ).map(([k, label]) => (
                  <button key={k} type="button" className={`chip-btn${filter === k ? " on" : ""}`} aria-pressed={filter === k} onClick={() => setFilter(k)}>
                    {label}
                  </button>
                ))}
              </div>
            ) : null}
            {incidents.length > 0 ? (
              <div className="row small feed-tools">
                <button type="button" className="btn ghost small" onClick={() => setOpenId(openId === "*" ? null : "*")} aria-pressed={openId === "*"}>
                  {openId === "*" ? "Collapse timelines" : "Expand all timelines"}
                </button>
              </div>
            ) : null}
            <div className="feed">
              {incidents.length === 0 ? (
                <div className="panel">
                  <ArchivedRunCard onReplay={startReplay} />
                </div>
              ) : (
                incidents
                  .filter((i) =>
                    filter === "all"
                      ? true
                      : filter === "needs"
                        ? i.status === "awaiting_engineer"
                        : filter === "progress"
                          ? ["remediating", "auto_remediating", "triaging"].includes(i.status)
                          : filter === "resolved"
                            ? i.status === "resolved"
                            : i.handled_by === "contract" || i.woken === false,
                  )
                  .map((i, idx) => (
                  <IncidentCard
                    key={i.incident_id}
                    index={idx}
                    incident={i.incident_id === current?.incident_id ? current : i}
                    now={now}
                    selected={i.incident_id === selectedId}
                    open={openId === "*" || openId === i.incident_id}
                    onSelect={() => {
                      setPinned(true);
                      setSelectedId(i.incident_id);
                    }}
                    onToggle={() => setOpenId(openId === i.incident_id ? null : i.incident_id)}
                    onPostmortem={() => (window.location.hash = `postmortem/${encodeURIComponent(i.incident_id)}`)}
                    onCopyLink={() => {
                      const url = `${window.location.origin}${window.location.pathname}#board/${encodeURIComponent(i.incident_id)}`;
                      void navigator.clipboard?.writeText(url).then(() => toast("Link copied", "ok")).catch(() => toast(url, "info"));
                    }}
                  />
                  ))
              )}
            </div>
          </section>
          {current ? (
            <section>
              {api && config && !replay && chooseBackend(config) === "assemblyai" ? (
                <div className="backend-switch" role="tablist" aria-label="Voice backend">
                  <button role="tab" aria-selected={voicePick !== "aws"} className={`seg${voicePick !== "aws" ? " on" : ""}`} onClick={() => setVoicePick("assemblyai")}>
                    AssemblyAI · full duplex
                  </button>
                  <button role="tab" aria-selected={voicePick === "aws"} className={`seg${voicePick === "aws" ? " on" : ""}`} onClick={() => setVoicePick("aws")}>
                    AWS cascade · push to talk
                  </button>
                </div>
              ) : null}
              {api && config && !replay && chooseBackend(config) === "assemblyai" && voicePick !== "aws" ? (
                <TalkDuplex
                  key={`duplex-${current.incident_id}`}
                  api={api}
                  config={config}
                  backend="assemblyai"
                  incident={current}
                  stt={stt}
                  passcode={() => passcode}
                  onIncident={onIncident}
                  series={null}
                />
              ) : (
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
              )}
            </section>
          ) : null}
        </main>
      ) : route === "analytics" ? (
        <main key="analytics" className="main route-in">
          <Analytics data={analytics.data} loading={analytics.loading} source={analytics.source} now={now} onReplay={!replay ? startReplay : undefined} />
        </main>
      ) : route === "contracts" ? (
        <main key="contracts" className="main route-in">
          <div className="page-h">
            <h1 className="display">
              Standing approvals, <em>in your own words.</em>
            </h1>
          </div>
          <Contracts contracts={contracts} now={now} onRevoke={revoke} canRevoke={!!api && !!passcode} onReplay={!replay ? startReplay : undefined} />
        </main>
      ) : route === "audit" ? (
        <main key="audit" className="main route-in">
          <Audit rows={audit} loading={!!api && auditQ.loading && !audit} csvUrl={api?.auditCsvUrl} onReplay={!replay ? startReplay : undefined} replay={!!replay} />
        </main>
      ) : route === "report" ? (
        <main key="report" className="main route-in">
          <Report report={report} loading={!!api && !report && !reportQ.error} night={reportNight} onNight={setReportNight} onReplay={!replay && (!report || report.incidents === 0) ? startReplay : undefined} replay={!!replay} />
        </main>
      ) : route === "postmortem" && deepId ? (
        <main key="postmortem" className="main route-in">
          <Postmortem id={deepId} api={api} fallback={postmortemFallback} onToast={(t) => toast(t, "ok")} />
        </main>
      ) : route === "notfound" ? (
        <main key="notfound" className="main route-in">
          <div className="empty notfound">
            <h3>Nothing here.</h3>
            <p>
              There is no page at <code className="mono">#{window.location.hash.replace("#", "")}</code>. The night is elsewhere:
            </p>
            <div className="row">
              <a className="btn primary" href="#board">
                Night Board
              </a>
              <a className="btn" href="#analytics">
                Analytics
              </a>
              <a className="btn" href="#contracts">
                Contracts
              </a>
              <a className="btn" href="#safety">
                Safety
              </a>
              <a className="btn ghost" href="#home">
                Home
              </a>
            </div>
          </div>
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
