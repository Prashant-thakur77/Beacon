import { formatAge, formatStamp } from "../hooks";
import type { Incident, Tally } from "../types";
import { useCountUp } from "../motion";
import { Marquee } from "./Marquee";
import { Timeline } from "./Timeline";

const STATUS: Record<string, { label: string; cls: string; pulse?: boolean }> = {
  awaiting_engineer: { label: "awaiting your word", cls: "amber" },
  remediating: { label: "remediating", cls: "amber", pulse: true },
  auto_remediating: { label: "handled under contract", cls: "lilac", pulse: true },
  resolved: { label: "resolved", cls: "green" },
  escalated: { label: "escalated", cls: "red" },
};

export function StatusPill({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, cls: "dim" };
  return <span className={`pill ${s.cls}${s.pulse ? " pulse" : ""}`}>{s.label}</span>;
}

/** Full local date-time for hover titles; the visible text stays relative. */
function absolute(iso: string | undefined): string | undefined {
  if (!iso) return undefined;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], { dateStyle: "medium", timeStyle: "medium" });
}

function recoveredIn(start: string, end: string): string {
  const s = Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000);
  if (Number.isNaN(s) || s < 0) return "–";
  if (s < 1) return "< 1 s";
  if (s < 90) return `${s} s`;
  return `${Math.round((s / 60) * 10) / 10} min`;
}

function inr(v: number | undefined): string {
  if (v == null) return "–";
  if (v >= 1) return `₹${v.toFixed(2)}`;
  return `₹${v.toFixed(3)}`;
}

export function TallyStrip({ tally }: { tally: Tally | null }) {
  // the numbers count up over 600 ms when they change (serif, tabular digits)
  const resolved = useCountUp(tally?.resolved);
  const median = useCountUp(tally?.median_minutes_to_recovery);
  const woken = useCountUp(tally?.humans_woken);
  const cost = useCountUp(tally?.cost_inr_per_incident);
  const sleep = useCountUp(tally?.sleep_protected_hours);
  return (
    <div className="tally reveal in">
      <div className="tile" style={{ "--i": 0 } as React.CSSProperties}>
        <div className="n">{resolved == null ? "–" : Math.round(resolved)}</div>
        <div className="l">incidents resolved</div>
      </div>
      <div className="tile" style={{ "--i": 1 } as React.CSSProperties}>
        <div className="n">{median == null ? "–" : median < 1 ? `${Math.round(median * 60)} s` : `${Math.round(median * 10) / 10} min`}</div>
        <div className="l">median time to recovery</div>
      </div>
      <div className="tile moon" style={{ "--i": 2 } as React.CSSProperties}>
        <div className="n">{woken == null ? "–" : Math.round(woken)}</div>
        <div className="l">humans woken</div>
      </div>
      <div className="tile" style={{ "--i": 3 } as React.CSSProperties}>
        <div className="n">{cost == null ? "–" : inr(cost)}</div>
        <div className="l">model cost per incident</div>
      </div>
      <div className="tile moon" style={{ "--i": 4 } as React.CSSProperties}>
        <div className="n">{sleep == null ? "–" : `${Math.round(sleep * 10) / 10} h`}</div>
        <div className="l">sleep protected · night IST</div>
      </div>
    </div>
  );
}

export function IncidentCard({
  incident,
  now,
  selected,
  open,
  onSelect,
  onToggle,
  onCopyLink,
  onPostmortem,
  index = 0,
}: {
  incident: Incident;
  now: Date;
  selected: boolean;
  open: boolean;
  onSelect: () => void;
  onToggle: () => void;
  onCopyLink?: () => void;
  onPostmortem?: () => void;
  index?: number;
}) {
  const rca = incident.rca_json ?? {};
  const sev = rca.status ?? "?";
  const sevCls = sev === "Critical" || sev === "High" ? "red" : sev === "Medium" ? "amber" : "dim";
  return (
    <div
      className={`card rise${selected ? " selected" : ""}${open ? " open" : ""}`}
      style={{ "--i": Math.min(index, 8) } as React.CSSProperties}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onSelect()}
    >
      <div className="title">
        <span className={`pill ${sevCls}`} title={`severity ${sev}`}>
          {sev}
        </span>
        <span className="alarm" title={incident.alarm_name ?? undefined}>
          {incident.alarm_name ?? "incident"}
        </span>
        <span className="state-col">
          <StatusPill status={incident.status} />
          {incident.handled_by === "contract" || incident.woken === false ? <span className="moon">☾ handled while you slept</span> : null}
          <time className="age" dateTime={incident.timestamp} title={absolute(incident.timestamp)}>
            {formatAge(incident.timestamp, now)}
          </time>
        </span>
      </div>
      <div className="summary">{rca.summary ?? ""}</div>
      <div className="foot">
        <button
          className="btn ghost small"
          aria-expanded={open}
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
        >
          {open ? "Hide timeline" : `Timeline${incident.timeline?.length ? ` · ${incident.timeline.length}` : ""}`}
        </button>
        <span className="stamp" title={absolute(incident.timestamp)}>
          {formatStamp(incident.timestamp)}
        </span>
        {(() => {
          const pr = [...(incident.timeline ?? [])].reverse().find((e) => e.event === "pr_opened");
          const d = (pr?.detail ?? {}) as { url?: string; number?: number };
          return pr && d.url ? (
            <a className="btn ghost small" href={d.url} target="_blank" rel="noreferrer" title="The pull request Beacon opened after this incident" onClick={(e) => e.stopPropagation()}>
              PR #{String(d.number)}
            </a>
          ) : null;
        })()}
        {onPostmortem && (incident.status === "resolved" || incident.status === "escalated") ? (
          <button
            className="btn ghost small"
            title="Open the postmortem"
            onClick={(e) => {
              e.stopPropagation();
              onPostmortem();
            }}
          >
            Postmortem
          </button>
        ) : null}
        {onCopyLink ? (
          <button
            className="btn ghost small copy"
            title="Copy a link to this incident"
            onClick={(e) => {
              e.stopPropagation();
              onCopyLink();
            }}
          >
            Copy link
          </button>
        ) : null}
        {incident.resolved_at ? (
          <span className="stamp" title={`resolved ${absolute(incident.resolved_at)}`}>
            recovered in {recoveredIn(incident.timestamp, incident.resolved_at)}
          </span>
        ) : null}
      </div>
      {open ? <Timeline events={incident.timeline ?? []} /> : null}
    </div>
  );
}

export function ArchivedRunCard({ onReplay }: { onReplay: () => void }) {
  return (
    <div className="empty">
      <h3>All quiet.</h3>
      <p>Waiting for the next page. Nothing is broken right now.</p>
      <button className="btn" onClick={onReplay}>
        ▶ Replay an archived run
      </button>
      <p className="faint small" style={{ marginTop: 10 }}>
        A real incident from an earlier night, recorded end to end: alarm, root cause, voice approval, verified fix, Sleep Contract.
      </p>
    </div>
  );
}

/** The judge card's content, shared with the "?" popover on every route. */
export function JudgeBody({ hasPasscode }: { hasPasscode: boolean }) {
  return (
    <>
        <div>
          <b>What it is.</b> An on-call agent for AWS. An alarm fires → Beacon finds the root cause on Bedrock and the CloudTrail change behind it → you talk to it
          in this browser → it proposes one allowlisted fix, dry-runs it, and applies it only when you say <span className="mono">approve fix one</span> → a Step
          Functions loop proves the recovery → you can grant a <span className="moon">Sleep Contract</span> so the repeat never wakes you.
        </div>
        <ol className="rules">
          <li>
            Press <b>Replay an archived run</b> to watch a real incident end to end (no passcode needed). Click any <span className="chip">E2</span> chip: every
            sentence Beacon speaks is pinned to evidence.
          </li>
          <li>
            Open <b>Contracts</b> to see a standing approval with the engineer’s own words, and <b>Safety</b> for the allowlist, the two-role IAM split and the kill switch.
          </li>
          <li>
            {hasPasscode ? "You are unlocked: when an incident is live you can talk to Beacon with the mic or the text box." : "With the judge passcode from the submission, you can talk to Beacon on a live incident."}
          </li>
        </ol>
        <div className="faint">
          Reads are public and redacted (no account ids). Writes need the passcode and are limited to the two allowlisted actions on tagged demo resources.
        </div>
    </>
  );
}

/** For a judge opening the URL cold, weeks later: what this is and how to see it work in 90 seconds. */
export function JudgeCard({ hasPasscode, open, onRunNight }: { hasPasscode: boolean; open: boolean; onRunNight?: () => void }) {
  return (
    <details className="judge" open={open}>
      <summary>How to judge this in 90 seconds</summary>
      <div className="stack small" style={{ paddingTop: 12 }}>
        {onRunNight ? (
          <div className="row" style={{ alignItems: "center" }}>
            <button className="btn primary" type="button" onClick={onRunNight}>
              ▶ Run the night
            </button>
            <span className="faint">
              Local mode: Beacon types the engineer’s lines for you — fix, approve, grant a Sleep Contract — then the same fault fires again and nobody is woken.
            </span>
          </div>
        ) : null}
        <JudgeBody hasPasscode={hasPasscode} />
        <Marquee />
      </div>
    </details>
  );
}
