import { formatAge, formatStamp } from "../hooks";
import type { Incident, Tally } from "../types";
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

function inr(v: number | undefined): string {
  if (v == null) return "–";
  if (v >= 1) return `₹${v.toFixed(2)}`;
  return `₹${v.toFixed(3)}`;
}

export function TallyStrip({ tally }: { tally: Tally | null }) {
  const median = tally?.median_minutes_to_recovery;
  return (
    <div className="tally">
      <div className="tile">
        <div className="n">{tally ? tally.resolved : "–"}</div>
        <div className="l">incidents resolved</div>
      </div>
      <div className="tile">
        <div className="n">{median == null ? "–" : `${median} min`}</div>
        <div className="l">median time to recovery</div>
      </div>
      <div className="tile moon">
        <div className="n">{tally ? tally.humans_woken : "–"}</div>
        <div className="l">humans woken</div>
      </div>
      <div className="tile">
        <div className="n">{tally ? inr(tally.cost_inr_per_incident) : "–"}</div>
        <div className="l">model cost per incident</div>
      </div>
      <div className="tile moon">
        <div className="n">{tally?.sleep_protected_hours != null ? `${tally.sleep_protected_hours} h` : "–"}</div>
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
}: {
  incident: Incident;
  now: Date;
  selected: boolean;
  open: boolean;
  onSelect: () => void;
  onToggle: () => void;
}) {
  const rca = incident.rca_json ?? {};
  const sev = rca.status ?? "?";
  const sevCls = sev === "Critical" || sev === "High" ? "red" : sev === "Medium" ? "amber" : "dim";
  return (
    <div
      className={`card${selected ? " selected" : ""}${open ? " open" : ""}`}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onSelect()}
    >
      <div className="title">
        <span className={`pill ${sevCls}`}>{sev}</span>
        <span className="alarm">{incident.alarm_name ?? "incident"}</span>
        <StatusPill status={incident.status} />
        {incident.handled_by === "contract" || incident.woken === false ? <span className="moon">☾ handled while you slept</span> : null}
        <span className="age" title={incident.timestamp}>
          {formatAge(incident.timestamp, now)}
        </span>
      </div>
      <div className="summary">{rca.summary ?? ""}</div>
      <div className="row" style={{ marginTop: 8 }}>
        <button
          className="btn ghost small"
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
        >
          {open ? "Hide timeline" : "Timeline"}
        </button>
        <span className="faint small mono">{formatStamp(incident.timestamp)}</span>
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

/** For a judge opening the URL cold, weeks later: what this is and how to see it work in 90 seconds. */
export function JudgeCard({ hasPasscode }: { hasPasscode: boolean }) {
  return (
    <div className="panel">
      <div className="panel-h">
        <h2>How to judge this in 90 seconds</h2>
      </div>
      <div className="panel-b stack small">
        <div>
          <b>What it is.</b> An on-call agent for AWS. An alarm fires → Beacon finds the root cause on Bedrock and the CloudTrail change behind it → you talk to it
          in this browser → it proposes one allowlisted fix, dry-runs it, and applies it only when you say <span className="mono">approve fix one</span> → a Step
          Functions loop proves the recovery → you can grant a <span style={{ color: "var(--lilac)" }}>Sleep Contract</span> so the repeat never wakes you.
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
      </div>
    </div>
  );
}
