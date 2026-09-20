import { useEffect, useRef, useState } from "react";
import { formatStamp } from "../hooks";
import type { Analytics as AnalyticsData } from "../types";

/* ------------------------------------------------------------------------
   Operations analytics: one page, computed from what the board already shows.
   Charts are inline SVG in the house style (hairlines, thin marks, colour
   only for state). Every chart has a designed empty state and a skeleton.
   ------------------------------------------------------------------------ */

const H = 150;
const PAD = { l: 6, r: 6, t: 18, b: 22 };

/** The measured width of the chart column, so SVG text and dots are never stretched. */
function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => setW(Math.max(200, Math.round(el.getBoundingClientRect().width)));
    read();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function nightLabel(night: string): string {
  const d = new Date(`${night}T12:00:00Z`);
  return Number.isNaN(d.getTime()) ? night : d.toLocaleDateString([], { day: "2-digit", month: "short", timeZone: "UTC" });
}

function minutes(v: number | null | undefined): string {
  if (v == null) return "–";
  if (v < 1) return `${Math.round(v * 60)} s`;
  return `${Math.round(v * 10) / 10} min`;
}

function seconds(v: number | null | undefined): string {
  if (v == null) return "–";
  return v < 90 ? `${Math.round(v)} s` : `${Math.round((v / 60) * 10) / 10} min`;
}

export function inr(v: number | null | undefined): string {
  if (v == null) return "–";
  if (v === 0) return "₹0";
  return v >= 1 ? `₹${v.toFixed(2)}` : `₹${v.toFixed(3)}`;
}

function hoursLeft(hours: number): string {
  const total = Math.round(hours);
  return total >= 48 ? `${Math.floor(total / 24)}d ${total % 24}h left` : `${total}h left`;
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * p;
}

/* ---------- frame, empty and loading states ---------- */

function Frame({
  title,
  meta,
  children,
  legend,
}: {
  title: string;
  meta?: string;
  legend?: Array<{ name: string; cls: string; dashed?: boolean }>;
  children: (w: number) => React.ReactNode;
}) {
  const [ref, w] = useWidth();
  return (
    <section className="chart">
      <header className="chart-h">
        <h3>{title}</h3>
        {meta ? <span className="meta">{meta}</span> : null}
      </header>
      <div ref={ref} className="chart-body">
        {children(w)}
      </div>
      {legend && legend.length > 1 ? (
        <div className="legend">
          {legend.map((l) => (
            <span key={l.name} className={`legend-item ${l.cls}${l.dashed ? " dashed" : ""}`}>
              {l.name}
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function Empty({ text = "No nights recorded yet", w }: { text?: string; w: number }) {
  const W = w;
  return (
    <div className="chart-empty" role="img" aria-label={text}>
      <svg viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
        <line x1={PAD.l} x2={W - PAD.r} y1={H - PAD.b} y2={H - PAD.b} stroke="var(--line-strong)" strokeDasharray="2 6" />
        {[0, 1, 2, 3, 4, 5, 6].map((i) => (
          <rect key={i} x={PAD.l + 30 + i * 80} y={H - PAD.b - 4} width={38} height={4} rx={2} fill="var(--line)" />
        ))}
      </svg>
      <div className="chart-empty-text">
        <span>{text}</span>
        <span className="faint">Run a night and this fills in.</span>
      </div>
    </div>
  );
}

function Skeleton({ w }: { w: number }) {
  const W = w;
  return (
    <div className="chart-skel" aria-busy="true" aria-label="loading">
      <svg viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
        <line x1={PAD.l} x2={W - PAD.r} y1={H - PAD.b} y2={H - PAD.b} stroke="var(--line)" />
        {[46, 88, 62, 104, 70, 96, 54].map((h, i) => (
          <rect key={i} className="shimmer" x={PAD.l + 30 + i * 80} y={H - PAD.b - h} width={38} height={h} rx={2} />
        ))}
      </svg>
    </div>
  );
}

/* ---------- primitives ---------- */

function xScale(n: number, W: number) {
  const inner = W - PAD.l - PAD.r;
  const slot = inner / Math.max(1, n);
  const bar = Math.min(44, Math.max(10, slot * 0.56));
  return { slot, bar, x: (i: number) => PAD.l + slot * i + (slot - bar) / 2, cx: (i: number) => PAD.l + slot * i + slot / 2 };
}

function XLabels({ labels, W }: { labels: string[]; W: number }) {
  const { cx } = xScale(labels.length, W);
  const every = labels.length > 10 ? Math.ceil(labels.length / 8) : 1;
  return (
    <g className="axis">
      {labels.map((l, i) =>
        i % every === 0 || i === labels.length - 1 ? (
          <text key={l + i} x={cx(i)} y={H - 6} textAnchor="middle">
            {l}
          </text>
        ) : null,
      )}
    </g>
  );
}

function Baseline({ max, unit, W }: { max: number; unit?: (v: number) => string; W: number }) {
  return (
    <g>
      <line x1={PAD.l} x2={W - PAD.r} y1={H - PAD.b} y2={H - PAD.b} stroke="var(--line-strong)" />
      <line x1={PAD.l} x2={W - PAD.r} y1={PAD.t} y2={PAD.t} stroke="var(--line)" strokeDasharray="2 4" />
      <text className="axis" x={W - PAD.r} y={PAD.t - 5} textAnchor="end">
        {unit ? unit(max) : max}
      </text>
    </g>
  );
}

/** Vertical bars, one series. */
function Bars({
  labels,
  values,
  cls = "neutral",
  unit,
  titles,
  W,
}: {
  labels: string[];
  values: number[];
  cls?: string;
  unit?: (v: number) => string;
  titles?: string[];
  W: number;
}) {
  const max = niceMax(Math.max(0, ...values));
  const { slot, bar, x, cx } = xScale(values.length, W);
  const plotH = H - PAD.t - PAD.b;
  const y = (v: number) => H - PAD.b - (v / max) * plotH;
  const showValues = values.length <= 12;
  return (
    <svg className="plot" viewBox={`0 0 ${W} ${H}`} role="img">
      <Baseline max={max} unit={unit} W={W} />
      {values.map((v, i) => (
        <g key={i} className={`mark ${cls}`}>
          <title>{titles?.[i] ?? `${labels[i]}: ${unit ? unit(v) : v}`}</title>
          <rect className="hit" x={PAD.l + slot * i} y={PAD.t} width={slot} height={plotH} />
          <rect className="bar" x={x(i)} y={y(v)} width={bar} height={Math.max(0, H - PAD.b - y(v))} rx={2} />
          {showValues && v > 0 ? (
            <text className="val" x={cx(i)} y={y(v) - 4} textAnchor="middle">
              {unit ? unit(v) : v}
            </text>
          ) : null}
        </g>
      ))}
      <XLabels labels={labels} W={W} />
    </svg>
  );
}

/** Stacked bars: parts in fixed order, 2px surface gap between segments. */
function Stacked({ labels, series, W }: { labels: string[]; series: Array<{ name: string; cls: string; values: number[] }>; W: number }) {
  const totals = labels.map((_, i) => series.reduce((a, s) => a + (s.values[i] ?? 0), 0));
  const max = niceMax(Math.max(0, ...totals));
  const { slot, bar, x, cx } = xScale(labels.length, W);
  const plotH = H - PAD.t - PAD.b;
  const h = (v: number) => (v / max) * plotH;
  return (
    <svg className="plot" viewBox={`0 0 ${W} ${H}`} role="img">
      <Baseline max={max} W={W} />
      {labels.map((l, i) => {
        let top = H - PAD.b;
        return (
          <g key={l + i}>
            <title>{`${l}: ${series.map((s) => `${s.values[i] ?? 0} ${s.name}`).join(" · ")}`}</title>
            <rect className="hit" x={PAD.l + slot * i} y={PAD.t} width={slot} height={plotH} />
            {series.map((s) => {
              const v = s.values[i] ?? 0;
              if (!v) return null;
              const hh = h(v);
              top -= hh;
              const el = <rect key={s.name} className={`bar ${s.cls}`} x={x(i)} y={top + 1} width={bar} height={Math.max(0, hh - 2)} rx={2} />;
              return el;
            })}
            {totals[i] > 0 && labels.length <= 12 ? (
              <text className="val" x={cx(i)} y={top - 4} textAnchor="middle">
                {totals[i]}
              </text>
            ) : null}
          </g>
        );
      })}
      <XLabels labels={labels} W={W} />
    </svg>
  );
}

/** Lines over the same x categories; nulls break the line. */
function Lines({
  labels,
  series,
  unit,
  area,
  W,
}: {
  labels: string[];
  series: Array<{ name: string; cls: string; values: Array<number | null>; dashed?: boolean }>;
  unit?: (v: number) => string;
  area?: boolean;
  W: number;
}) {
  const all = series.flatMap((s) => s.values).filter((v): v is number => v != null);
  const max = niceMax(Math.max(0, ...all));
  const { cx } = xScale(labels.length, W);
  const plotH = H - PAD.t - PAD.b;
  const y = (v: number) => H - PAD.b - (v / max) * plotH;
  const path = (vals: Array<number | null>) => {
    let d = "";
    let pen = false;
    vals.forEach((v, i) => {
      if (v == null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${cx(i).toFixed(1)} ${y(v).toFixed(1)} `;
      pen = true;
    });
    return d.trim();
  };
  const single = labels.length === 1;
  return (
    <svg className="plot" viewBox={`0 0 ${W} ${H}`} role="img">
      <Baseline max={max} unit={unit} W={W} />
      {series.map((s) => {
        const d = path(s.values);
        const last = [...s.values].reverse().findIndex((v) => v != null);
        const lastIdx = last < 0 ? -1 : s.values.length - 1 - last;
        return (
          <g key={s.name} className={`mark ${s.cls}`}>
            {area && d ? <path className="area" d={`${d} L${cx(lastIdx)} ${H - PAD.b} L${cx(0)} ${H - PAD.b} Z`} /> : null}
            {!single && d ? <path className="line" d={d} strokeDasharray={s.dashed ? "4 4" : undefined} /> : null}
            {s.values.map((v, i) =>
              v == null ? null : (
                <g key={i}>
                  <title>{`${labels[i]} · ${s.name}: ${unit ? unit(v) : v}`}</title>
                  <circle className="dot" cx={cx(i)} cy={y(v)} r={single || i === lastIdx ? 4 : 3} />
                  {i === lastIdx ? (
                    <text className="val" x={cx(i)} y={y(v) - 8} textAnchor={i === labels.length - 1 && labels.length > 1 ? "end" : "middle"}>
                      {unit ? unit(v) : v}
                    </text>
                  ) : null}
                </g>
              ),
            )}
          </g>
        );
      })}
      <XLabels labels={labels} W={W} />
    </svg>
  );
}

/** Horizontal bars with the label on the left and the value on the right. */
function HBars({ items, unit }: { items: Array<{ label: string; value: number; cls?: string; sub?: string }>; unit?: (v: number) => string }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="hbars">
      {items.map((it) => (
        <li key={it.label} title={`${it.label}: ${unit ? unit(it.value) : it.value}`}>
          <span className="hb-label">
            <span className="mono">{it.label}</span>
            {it.sub ? <span className="faint"> {it.sub}</span> : null}
          </span>
          <span className={`hb-track ${it.cls ?? "neutral"}`}>
            <span className="hb-fill" style={{ width: `${(it.value / max) * 100}%` }} />
          </span>
          <span className="hb-val mono">{unit ? unit(it.value) : it.value}</span>
        </li>
      ))}
    </ul>
  );
}

function Meter({ used, max }: { used: number; max: number }) {
  const n = Math.max(1, Math.min(12, max));
  return (
    <span className="meter" aria-label={`${max - used} of ${max} uses left`}>
      {Array.from({ length: n }, (_, i) => (
        <span key={i} className={i < used ? "used" : "left"} />
      ))}
    </span>
  );
}

/* ---------- the page ---------- */

export function Analytics({ data, loading, source, now }: { data: AnalyticsData | null; loading: boolean; source: "api" | "computed" | "replay"; now: Date }) {
  const nights = data?.nights ?? [];
  const labels = nights.map((n) => nightLabel(n.night));
  const has = nights.length > 0;
  const body = (chart: (w: number) => React.ReactNode, empty?: string) => (w: number) => (loading && !data ? <Skeleton w={w} /> : has ? chart(w) : <Empty text={empty} w={w} />);

  const incidents = data?.incidents ?? [];
  const costLabels = incidents.map((i, idx) => (i.timestamp ? formatStamp(i.timestamp) : `#${idx + 1}`));
  const contracts = data?.contracts ?? [];
  const outcomes = data?.outcomes ?? { resolved: 0, escalated: 0, in_progress: 0 };
  const humans = data?.humans ?? { woken: 0, under_contract: 0 };
  const totalIncidents = incidents.length;

  return (
    <div className="stack analytics">
      <div className="panel-h">
        <h2>Operations analytics</h2>
        <span className="meta">
          {data ? `${totalIncidents} incident${totalIncidents === 1 ? "" : "s"} · ${nights.length} night${nights.length === 1 ? "" : "s"}` : loading ? "loading…" : "no data"}
          {data ? (source === "replay" ? " · replay" : source === "computed" ? " · computed in the browser" : "") : ""}
        </span>
      </div>

      <div className="kpis">
        <div className="tile">
          <div className="n">{minutes(data?.recovery.p50_minutes)}</div>
          <div className="l">median time to recovery</div>
        </div>
        <div className="tile">
          <div className="n">{minutes(data?.recovery.p90_minutes)}</div>
          <div className="l">p90 time to recovery</div>
        </div>
        <div className="tile">
          <div className="n">{seconds(data?.first_proposal.mean_seconds)}</div>
          <div className="l">alarm → first proposal (mean)</div>
        </div>
        <div className="tile">
          <div className="n">{data ? inr(data.cost.per_incident_inr) : "–"}</div>
          <div className="l">model cost per incident</div>
        </div>
        <div className="tile">
          <div className="n">{data ? inr(data.cost.total_inr) : "–"}</div>
          <div className="l">model cost, all nights</div>
        </div>
        <div className="tile moon">
          <div className="n">
            {data ? humans.under_contract : "–"}
            <span className="of"> / {data ? totalIncidents : "–"}</span>
          </div>
          <div className="l">handled under a Sleep Contract</div>
        </div>
      </div>

      <div className="charts">
        <Frame title="Incidents per night" meta={has ? `${nights.reduce((a, n) => a + n.incidents, 0)} total` : undefined}>
          {body((w) => (
            <Bars
              W={w}
              labels={labels}
              values={nights.map((n) => n.incidents)}
              titles={nights.map((n) => `${nightLabel(n.night)}: ${n.incidents} incident${n.incidents === 1 ? "" : "s"}, ${n.resolved} resolved, ${n.escalated} escalated`)}
            />
          ))}
        </Frame>

        <Frame
          title="Time to recovery per night"
          meta={has ? `${data?.recovery.count ?? 0} resolved` : undefined}
          legend={[
            { name: "median", cls: "text" },
            { name: "p90", cls: "dim", dashed: true },
          ]}
        >
          {body(
            (w) => (
              <Lines
                W={w}
                labels={labels}
                unit={minutes}
                series={[
                  { name: "p90", cls: "dim", values: nights.map((n) => n.p90_minutes_to_recovery), dashed: true },
                  { name: "median", cls: "text", values: nights.map((n) => n.median_minutes_to_recovery) },
                ]}
              />
            ),
            "No recoveries verified yet",
          )}
        </Frame>

        <Frame
          title="Humans woken vs handled under contract"
          meta={has ? `${humans.woken} woken · ${humans.under_contract} slept through` : undefined}
          legend={[
            { name: "human woken", cls: "amber" },
            { name: "handled under contract", cls: "violet" },
          ]}
        >
          {body((w) => (
            <Stacked
              W={w}
              labels={labels}
              series={[
                { name: "woken", cls: "amber", values: nights.map((n) => n.woken) },
                { name: "under contract", cls: "violet", values: nights.map((n) => n.under_contract) },
              ]}
            />
          ))}
        </Frame>

        <Frame title="Verification outcomes" meta={has ? `${outcomes.resolved + outcomes.escalated} verified` : undefined}>
          {body(
            () => (
              <HBars
                items={[
                  { label: "resolved", value: outcomes.resolved, cls: "green", sub: "alarm OK · metric zero · post-condition" },
                  { label: "escalated", value: outcomes.escalated, cls: "red", sub: "a human was needed" },
                  { label: "in progress", value: outcomes.in_progress, cls: "amber" },
                ]}
              />
            ),
            "No verifications yet",
          )}
        </Frame>

        <Frame title="Model cost per incident" meta={has ? `${inr(data?.cost.per_incident_inr)} average` : undefined}>
          {body(
            (w) => (
              <Bars
                W={w}
                labels={costLabels}
                values={incidents.map((i) => i.cost_inr)}
                cls="violet"
                unit={inr}
                titles={incidents.map((i) => `${i.alarm_name ?? "incident"} · ${i.timestamp ? formatStamp(i.timestamp) : ""}: ${inr(i.cost_inr)}`)}
              />
            ),
            "No model calls billed yet",
          )}
        </Frame>

        <Frame title="Cumulative model cost" meta={has ? `${inr(data?.cost.total_inr)} so far` : undefined}>
          {body(
            (w) => (
              <Lines W={w} labels={costLabels} unit={inr} area series={[{ name: "cumulative ₹", cls: "violet", values: incidents.map((i) => i.cost_inr_cumulative) }]} />
            ),
            "No model calls billed yet",
          )}
        </Frame>

        <Frame title="Top alarms" meta={has ? `${data?.top_alarms.length ?? 0} distinct` : undefined}>
          {body(
            () => (
              <HBars items={(data?.top_alarms ?? []).map((a) => ({ label: a.alarm_name, value: a.count }))} />
            ),
            "No alarms received yet",
          )}
        </Frame>

        <Frame title="Alarm → first proposal" meta={has ? `${data?.first_proposal.count ?? 0} with a dry-run fix` : undefined}>
          {body(
            (w) => (
              <Lines W={w} labels={costLabels} unit={seconds} series={[{ name: "seconds", cls: "text", values: incidents.map((i) => i.seconds_to_first_proposal) }]} />
            ),
            "No fix proposed yet",
          )}
        </Frame>

        <Frame title="Sleep Contracts in force" meta={contracts.length ? `${contracts.reduce((a, c) => a + c.uses_left, 0)} uses remaining` : undefined}>
          {(w) =>
            loading && !data ? (
              <Skeleton w={w} />
            ) : contracts.length === 0 ? (
              <Empty text="No contracts granted yet" w={w} />
            ) : (
              <ul className="contract-rows">
                {contracts.map((c) => {
                  const expired = c.hours_left != null && c.hours_left <= 0;
                  const soon = c.hours_left != null && c.hours_left > 0 && c.hours_left < 24;
                  return (
                    <li key={c.contract_id} title={c.expires_at ? `expires ${new Date(c.expires_at).toLocaleString()}` : undefined}>
                      <div className="cr-main">
                        <span className="alarm">{c.alarm_name ?? "alarm"}</span>
                        <span className="mono faint">{c.action}</span>
                      </div>
                      <div className="cr-meter">
                        <Meter used={c.uses} max={c.max_uses} />
                        <span className="mono small dim">
                          {c.uses_left} of {c.max_uses} left
                        </span>
                      </div>
                      <span className={`pill ${expired ? "red" : soon ? "amber" : "lilac"}`}>
                        {c.hours_left == null ? "no expiry" : expired ? "expired" : hoursLeft(c.hours_left)}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )
          }
        </Frame>
      </div>
      <div className="faint small">
        Nights turn over at noon IST, so a 23:30 page and its 03:00 repeat count as one night. Cost is Nova 2 Lite list price at a fixed ₹/USD, an order of magnitude, not a bill.
        {data?.generated_at
          ? ` Computed ${formatStamp(data.generated_at)} (${Math.max(0, Math.round((now.getTime() - new Date(data.generated_at).getTime()) / 1000))} s ago).`
          : ""}
      </div>
    </div>
  );
}
