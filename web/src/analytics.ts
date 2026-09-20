import type { Analytics, AnalyticsIncident, AnalyticsNight, Contract, Incident } from "./types";

/**
 * Client-side mirror of `dashboard_api.build_analytics`, used in replay mode
 * (no API) and as a fallback while a deployed API predates `GET /analytics`.
 * Prices match the server defaults; the server is the source of truth.
 */
const PRICE = { input: 0.06, output: 0.24, embed: 0.02, usdInr: 84 };

function parse(iso: unknown): number | null {
  if (!iso) return null;
  const t = new Date(String(iso)).getTime();
  return Number.isNaN(t) ? null : t;
}

export function nightOf(iso: unknown): string {
  const t = parse(iso);
  if (t == null) return "unknown";
  // IST date, with the day turning over at noon so 23:30 and 03:00 share a night.
  return new Date(t + (5.5 - 12) * 3.6e6).toISOString().slice(0, 10);
}

export function costInr(usage: Incident["usage"]): number {
  if (!usage) return 0;
  const usd = ((usage.input_tokens ?? 0) / 1e6) * PRICE.input + ((usage.output_tokens ?? 0) / 1e6) * PRICE.output + ((usage.embedding_tokens ?? 0) / 1e6) * PRICE.embed;
  return Math.round(usd * PRICE.usdInr * 1e4) / 1e4;
}

export function percentile(values: number[], pct: number): number | null {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  const idx = (s.length - 1) * pct;
  const lo = Math.floor(idx);
  const hi = Math.min(lo + 1, s.length - 1);
  return Math.round((s[lo] + (s[hi] - s[lo]) * (idx - lo)) * 100) / 100;
}

export function computeAnalytics(incidents: Incident[], contracts: Contract[], now: Date): Analytics {
  const nights = new Map<string, AnalyticsNight & { ttr: number[] }>();
  const rows: AnalyticsIncident[] = [];
  const ttrAll: number[] = [];
  const proposal: number[] = [];
  let cumulative = 0;
  const sorted = [...incidents].sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)));
  for (const inc of sorted) {
    const night = nightOf(inc.timestamp);
    let b = nights.get(night);
    if (!b) {
      b = { night, incidents: 0, resolved: 0, escalated: 0, woken: 0, under_contract: 0, cost_inr: 0, median_minutes_to_recovery: null, p90_minutes_to_recovery: null, ttr: [] };
      nights.set(night, b);
    }
    const t0 = parse(inc.timestamp);
    const t1 = parse(inc.resolved_at);
    const ttr = inc.status === "resolved" && t0 != null && t1 != null ? Math.round(((t1 - t0) / 6e4) * 10) / 10 : null;
    const cost = costInr(inc.usage);
    cumulative = Math.round((cumulative + cost) * 1e4) / 1e4;
    const underContract = inc.handled_by === "contract";
    const woken = inc.woken !== false;
    b.incidents += 1;
    b.resolved += inc.status === "resolved" ? 1 : 0;
    b.escalated += inc.status === "escalated" ? 1 : 0;
    b.woken += woken ? 1 : 0;
    b.under_contract += underContract ? 1 : 0;
    b.cost_inr = Math.round((b.cost_inr + cost) * 1e4) / 1e4;
    if (ttr != null) {
      b.ttr.push(ttr);
      ttrAll.push(ttr);
    }
    const fp = (inc.timeline ?? []).find((e) => e.event === "fix_proposed");
    const tf = fp ? parse(fp.t) : null;
    const first = t0 != null && tf != null ? Math.max(0, Math.round(((tf - t0) / 1000) * 10) / 10) : null;
    if (first != null) proposal.push(first);
    rows.push({
      incident_id: inc.incident_id,
      alarm_name: inc.alarm_name,
      timestamp: inc.timestamp,
      night,
      status: inc.status,
      woken,
      under_contract: underContract,
      minutes_to_recovery: ttr,
      seconds_to_first_proposal: first,
      cost_inr: cost,
      cost_inr_cumulative: cumulative,
    });
  }
  const nightRows: AnalyticsNight[] = [...nights.values()]
    .map(({ ttr, ...n }) => ({ ...n, median_minutes_to_recovery: percentile(ttr, 0.5), p90_minutes_to_recovery: percentile(ttr, 0.9) }))
    .sort((a, b) => a.night.localeCompare(b.night));
  const alarms = new Map<string, number>();
  for (const i of incidents) alarms.set(i.alarm_name ?? "unknown", (alarms.get(i.alarm_name ?? "unknown") ?? 0) + 1);
  const resolved = incidents.filter((i) => i.status === "resolved").length;
  const escalated = incidents.filter((i) => i.status === "escalated").length;
  return {
    generated_at: now.toISOString(),
    nights: nightRows,
    incidents: rows,
    recovery: { count: ttrAll.length, p50_minutes: percentile(ttrAll, 0.5), p90_minutes: percentile(ttrAll, 0.9), max_minutes: ttrAll.length ? Math.max(...ttrAll) : null },
    first_proposal: { count: proposal.length, mean_seconds: proposal.length ? Math.round((proposal.reduce((a, b) => a + b, 0) / proposal.length) * 10) / 10 : null },
    outcomes: { resolved, escalated, in_progress: incidents.length - resolved - escalated },
    humans: { woken: incidents.filter((i) => i.woken !== false).length, under_contract: incidents.filter((i) => i.handled_by === "contract").length },
    cost: { total_inr: cumulative, per_incident_inr: incidents.length ? Math.round((cumulative / incidents.length) * 1e4) / 1e4 : 0 },
    top_alarms: [...alarms.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([alarm_name, count]) => ({ alarm_name, count })),
    contracts: contracts.map((c) => {
      const exp = parse(c.expires_at);
      return {
        contract_id: c.contract_id,
        alarm_name: c.alarm_name,
        action: c.action,
        uses: c.uses,
        max_uses: c.max_uses,
        uses_left: Math.max(0, c.max_uses - c.uses),
        expires_at: c.expires_at,
        hours_left: exp == null ? null : Math.round(((exp - now.getTime()) / 3.6e6) * 10) / 10,
      };
    }),
  };
}
