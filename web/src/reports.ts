import type { AuditRow, Contract, Incident, MorningReport } from "./types";

/* Browser mirrors of `beacon.reports` for replay mode (the bundle has no
   approvals table, so approvals are derived from the timeline). Simpler
   than the server, same sections and shapes. */

const IST = 5.5 * 3.6e6;

function dt(v: unknown): Date | null {
  if (!v) return null;
  const d = new Date(String(v));
  return Number.isNaN(d.getTime()) ? null : d;
}

function hhmm(v: unknown): string {
  const d = dt(v);
  if (!d) return "–";
  const ist = new Date(d.getTime() + IST);
  return `${String(ist.getUTCHours()).padStart(2, "0")}:${String(ist.getUTCMinutes()).padStart(2, "0")}`;
}

function span(a: unknown, b: unknown): string {
  const x = dt(a);
  const y = dt(b);
  if (!x || !y) return "–";
  const s = Math.max(0, Math.round((y.getTime() - x.getTime()) / 1000));
  return s < 90 ? `${s} s` : `${Math.round((s / 60) * 10) / 10} min`;
}

export function nightOf(ts: unknown): string {
  const d = dt(ts);
  if (!d) return "unknown";
  return new Date(d.getTime() + IST - 12 * 3.6e6).toISOString().slice(0, 10);
}

function detailOf(ev: { detail?: Record<string, unknown> }): Record<string, unknown> {
  return (ev.detail ?? {}) as Record<string, unknown>;
}

export function postmortemMd(incident: Incident, contracts: Contract[]): string {
  const rca = incident.rca_json ?? {};
  const alarm = incident.alarm_name ?? "incident";
  const timeline = incident.timeline ?? [];
  const out: string[] = [];
  out.push(`# Postmortem: ${alarm}`, "", `Incident \`${incident.incident_id}\` · ${hhmm(incident.timestamp)} · status **${incident.status}**`, "");
  out.push("## Summary", "", rca.summary ?? "No root-cause summary was recorded.", "");
  out.push("## Timeline", "", "| Time (IST) | Event | Detail |", "|---|---|---|");
  for (const e of timeline) {
    const d = detailOf(e);
    let detail = "";
    if (e.event === "verify_attempt") detail = ((d.checks as Array<{ name: string; ok: boolean }>) ?? []).map((c) => `${c.name}=${c.ok ? "ok" : "no"}`).join(", ");
    else if (e.event === "fix_proposed") detail = `fix ${String(d.fix_id)} ${String(d.action)} · dry run ${(d.dry_run as { ok?: boolean })?.ok ? "passed" : "failed"}`;
    else if (e.event === "approved" || e.event === "contract_granted") detail = `“${String(d.transcript_quote ?? "")}”`;
    else if (e.event === "contract_matched") detail = `contract ${String(d.contract_id ?? "").slice(0, 8)}…`;
    else if (e.event === "resolved") detail = `handled by ${String(d.handled_by ?? "voice")}`;
    else if (e.event === "escalated") detail = String(d.reason ?? "");
    out.push(`| ${hhmm(e.t)} | ${e.event.replace(/_/g, " ")} | ${detail.replace(/\|/g, "/")} |`);
  }
  out.push("", "## Root cause", "", rca.summary ?? "–", "");
  if (rca.evidence?.length) out.push(...rca.evidence.map((x) => `- ${x}`), "");
  out.push("## What changed", "", rca.change_correlation ?? "No correlated CloudTrail change.", "");
  const fix = timeline.find((e) => e.event === "fix_proposed");
  const fd = fix ? detailOf(fix) : null;
  out.push("## The fix", "", fd ? `\`${String(fd.action)}\` · ${String(fd.blast_radius ?? "")} · dry run **${(fd.dry_run as { ok?: boolean })?.ok ? "PASSED" : "FAILED"}**` : incident.handled_by === "contract" ? "Applied under a Sleep Contract without a new proposal." : "No fix was proposed.", "");
  const verify = timeline.filter((e) => e.event === "verify_attempt");
  out.push("## Verification", "", verify.length ? `${verify.length} attempt(s); resolved ${incident.resolved_at ? `after ${span(incident.timestamp, incident.resolved_at)}` : "–"}.` : "No verification recorded.", "");
  const used = contracts.find((c) => c.contract_id === incident.contract_id);
  const granted = contracts.filter((c) => c.incident_id === incident.incident_id);
  out.push("## Sleep Contract", "");
  if (used) out.push(`Handled under contract \`${used.contract_id.slice(0, 8)}…\` (“${used.transcript_quote}”), ${used.uses}/${used.max_uses} uses, expires ${used.expires_at}.`);
  else if (granted.length) out.push(...granted.map((c) => `Granted here: “${c.transcript_quote}” · ${c.max_uses} uses · expires ${c.expires_at}.`));
  else out.push("None.");
  const u = incident.usage ?? {};
  const usd = ((u.input_tokens ?? 0) / 1e6) * 0.06 + ((u.output_tokens ?? 0) / 1e6) * 0.24 + ((u.embedding_tokens ?? 0) / 1e6) * 0.02;
  out.push("", "## Cost", "", `${(u.input_tokens ?? 0).toLocaleString()} input · ${(u.output_tokens ?? 0).toLocaleString()} output · ${(u.embedding_tokens ?? 0).toLocaleString()} embedding tokens ≈ ₹${(usd * 84).toFixed(3)} (list price, order of magnitude).`, "");
  return out.join("\n");
}

export function auditRows(incidents: Incident[], contracts: Contract[]): AuditRow[] {
  const rows: AuditRow[] = [];
  for (const i of incidents) {
    for (const e of i.timeline ?? []) {
      if (e.event !== "approved") continue;
      const d = detailOf(e);
      const executed = (i.timeline ?? []).find((x) => x.event === "executed");
      rows.push({
        at: e.t,
        kind: "approval",
        id: String(d.approval_id ?? `${i.incident_id}:${e.t}`),
        incident_id: i.incident_id,
        alarm_name: i.alarm_name,
        action: String(d.action ?? i.rca_json?.beacon_json?.suggested_action ?? ""),
        quote: String(d.transcript_quote ?? ""),
        channel: String(d.channel ?? ""),
        source: "transcript",
        executed: !!executed,
        executed_at: executed?.t ?? null,
        result: executed ? "ok" : null,
      });
    }
  }
  for (const c of contracts) {
    rows.push({
      at: c.granted_at,
      kind: "contract",
      id: c.contract_id,
      incident_id: c.incident_id,
      alarm_name: c.alarm_name,
      action: c.action,
      quote: c.transcript_quote,
      channel: "voice",
      source: c.granted_by,
      status: "active",
      uses: `${c.uses}/${c.max_uses}`,
      expires_at: c.expires_at,
    });
  }
  return rows.sort((a, b) => String(b.at ?? "").localeCompare(String(a.at ?? "")));
}

export function morningReport(incidents: Incident[], contracts: Contract[], night?: string): MorningReport {
  const nights = [...new Set(incidents.map((i) => nightOf(i.timestamp)))].sort();
  const n = night ?? nights[nights.length - 1] ?? nightOf(new Date().toISOString());
  const rows = incidents.filter((i) => nightOf(i.timestamp) === n).sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)));
  const resolved = rows.filter((i) => i.status === "resolved");
  const escalated = rows.filter((i) => i.status === "escalated");
  const woken = rows.filter((i) => i.woken !== false).length;
  const byContract = rows.filter((i) => i.handled_by === "contract");
  const mins = resolved.map((i) => [dt(i.timestamp), dt(i.resolved_at)]).filter((p): p is [Date, Date] => !!p[0] && !!p[1]).map(([a, b]) => (b.getTime() - a.getTime()) / 6e4).sort((a, b) => a - b);
  const median = mins.length ? Math.round((mins.length % 2 ? mins[(mins.length - 1) / 2] : (mins[mins.length / 2 - 1] + mins[mins.length / 2]) / 2) * 10) / 10 : null;
  const cost = Math.round(rows.reduce((a, i) => a + (((i.usage?.input_tokens ?? 0) / 1e6) * 0.06 + ((i.usage?.output_tokens ?? 0) / 1e6) * 0.24 + ((i.usage?.embedding_tokens ?? 0) / 1e6) * 0.02) * 84, 0) * 100) / 100;
  const usedIds = new Set(byContract.map((i) => i.contract_id));
  const lines = [`Good morning. Night of ${n}.`];
  if (!rows.length) lines.push("A quiet night: no incidents, nobody woken.");
  else {
    lines.push(`${rows.length} incident(s): ${resolved.length} resolved, ${escalated.length} escalated. ${woken} human woken, ${byContract.length} handled under a Sleep Contract.`);
    if (median != null) lines.push(`Median time to recovery: ${median} min.`);
    for (const i of rows) {
      const how = i.handled_by === "contract" ? "handled under your contract, you were not woken" : i.status === "resolved" ? "resolved with your approval" : i.status === "escalated" ? "escalated to a human" : i.status;
      lines.push(`- ${hhmm(i.timestamp)} ${i.alarm_name}: ${how} (${i.resolved_at ? span(i.timestamp, i.resolved_at) : "open"}).`);
    }
    lines.push(`Model cost for the night: ₹${cost.toFixed(2)}.`);
  }
  return {
    night_of: n,
    generated_at: new Date().toISOString(),
    incidents: rows.length,
    resolved: resolved.length,
    escalated: escalated.length,
    humans_woken: woken,
    handled_by_contract: byContract.length,
    median_minutes_to_recovery: median,
    cost_inr: cost,
    contracts_used: contracts.filter((c) => usedIds.has(c.contract_id)).map((c) => ({ contract_id: c.contract_id, alarm_name: c.alarm_name, uses: `${c.uses}/${c.max_uses}` })),
    incident_ids: rows.map((i) => i.incident_id),
    subject: `Beacon morning report · ${n} · ${rows.length ? `${rows.length} incident(s), ${woken} woken` : "quiet night"}`,
    text: lines.join("\n"),
  };
}
