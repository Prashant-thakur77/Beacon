import { formatTime } from "../hooks";
import type { TimelineEvent } from "../types";

const LABELS: Record<string, string> = {
  alarm_received: "Alarm received",
  triggered: "Triggered",
  logs_fetched: "Logs fetched",
  reduced: "Cordon reduced the logs (Nova Embeddings)",
  diagnostics_ran: "Security group drift checked",
  diagnostics_skipped: "Diagnostics skipped",
  changes_checked: "CloudTrail changes checked",
  rca_ready: "Root cause ready (Nova 2 Lite)",
  sns_sent: "Engineer paged",
  fix_proposed: "Fix proposed and dry-run",
  approved: "Approved by voice",
  contract_matched: "Sleep Contract matched",
  contract_matched_apply_disabled: "Contract matched, but apply is disabled",
  remediation_started: "Remediation loop started",
  executing: "Executing",
  executed: "Executed",
  execute_failed: "Execute failed",
  verify_attempt: "Verify attempt",
  resolved: "Resolved",
  escalated: "Escalated to a human",
  contract_granted: "Sleep Contract granted",
  contract_exhausted: "Contract exhausted",
  contract_ignored: "Contract ignored",
};

function tone(ev: TimelineEvent): string {
  const d = (ev.detail ?? {}) as Record<string, unknown>;
  if (ev.event === "resolved" || ev.event === "executed" || ev.event === "approved") return "ok";
  if (ev.event === "verify_attempt") return d.ok ? "ok" : "warn";
  if (ev.event === "escalated" || ev.event === "execute_failed") return "bad";
  if (ev.event.startsWith("contract")) return "moon";
  if (ev.event === "sns_sent" || ev.event === "fix_proposed" || ev.event === "alarm_received") return "warn";
  return "";
}

function Detail({ ev }: { ev: TimelineEvent }) {
  const d = (ev.detail ?? {}) as Record<string, unknown>;
  switch (ev.event) {
    case "logs_fetched":
      return <div className="d">{String(d.lines ?? "?")} lines from {String(d.groups ?? "?")} log group(s)</div>;
    case "reduced":
      return (
        <div className="d">
          kept the top {Math.round(Number(d.percentile ?? 0) * 100)}% most anomalous sections · <code>{String(d.model ?? "")}</code>
        </div>
      );
    case "diagnostics_ran":
      return (
        <div className="d">
          {Number(d.missing_rules ?? 0)} golden rule(s) missing{d.suggested_action ? <> · fix: <code>{String(d.suggested_action)}</code></> : null}
        </div>
      );
    case "changes_checked":
      return (
        <div className="d">
          {Number(d.count ?? 0)} write call(s) before the alarm{d.top ? <> · most destructive: <code>{String(d.top)}</code></> : null}
          {d.source ? <span className="faint"> ({String(d.source)})</span> : null}
        </div>
      );
    case "rca_ready":
      return (
        <div className="d">
          severity {String(d.status ?? "?")} · <code>{String(d.model ?? "")}</code>
        </div>
      );
    case "fix_proposed": {
      const dry = (d.dry_run ?? {}) as Record<string, unknown>;
      return (
        <div className="d">
          fix {String(d.fix_id)} <code>{String(d.action)}</code> · {String(d.blast_radius ?? "")} · dry run{" "}
          <b className={dry.ok ? "ok" : "bad"} style={{ color: dry.ok ? "var(--green)" : "var(--red)" }}>
            {dry.ok ? "PASSED" : "FAILED"}
          </b>{" "}
          <span className="faint">({String(dry.code ?? "")}{dry.role ? ", via remediator role" : ""})</span>
        </div>
      );
    }
    case "approved":
      return (
        <div className="d">
          via {String(d.channel)} · <span className="quote" style={{ display: "inline", borderLeft: "none", paddingLeft: 0 }}>“{String(d.transcript_quote ?? "")}”</span>
        </div>
      );
    case "verify_attempt": {
      const checks = (d.checks ?? []) as Array<{ name: string; ok: boolean; detail?: string }>;
      return (
        <div className="checks">
          <span className="dim">attempt {String(d.attempt)}</span>
          {checks.map((c) => (
            <span key={c.name} className={c.ok ? "ok" : "bad"}>
              {c.ok ? "✓" : "✗"} {c.name.replace(/_/g, " ")} <span className="faint">— {c.detail}</span>
            </span>
          ))}
        </div>
      );
    }
    case "contract_matched":
      return <div className="d">contract {String(d.contract_id ?? "").slice(0, 8)}… · {String(d.uses_left)} use(s) left after this</div>;
    case "contract_granted":
      return (
        <div className="d">
          {String(d.days)} days, {String(d.max_uses)} uses · “{String(d.transcript_quote ?? "")}”
        </div>
      );
    case "escalated":
      return <div className="d">{String(d.reason ?? "")}</div>;
    case "resolved":
      return <div className="d">handled by {String(d.handled_by ?? "voice")}{d.attempts ? ` after ${String(d.attempts)} verify attempt(s)` : ""}</div>;
    default:
      return null;
  }
}

export function Timeline({ events }: { events: TimelineEvent[] }) {
  if (!events?.length) return <p className="dim small">No timeline yet.</p>;
  return (
    <ul className="timeline">
      {events.map((ev, i) => (
        <li key={`${ev.t}-${i}`} className={tone(ev)}>
          <div className="t">{formatTime(ev.t)}</div>
          <div className="e">{LABELS[ev.event] ?? ev.event}</div>
          <Detail ev={ev} />
        </li>
      ))}
    </ul>
  );
}
