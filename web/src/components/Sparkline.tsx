import type { MetricSeries } from "../types";

/** The alarm's metric, drawn to scale, with the execute moment marked. Reads with the sound off. */
export function RecoverySparkline({ series }: { series: MetricSeries | null }) {
  if (!series || series.points.length < 2) return null;
  const W = 600;
  const H = 56;
  const pad = 4;
  const pts = series.points;
  const t0 = new Date(pts[0].t).getTime();
  const t1 = new Date(pts[pts.length - 1].t).getTime();
  const span = Math.max(1, t1 - t0);
  const max = Math.max(1, ...pts.map((p) => p.v));
  const x = (iso: string) => pad + ((new Date(iso).getTime() - t0) / span) * (W - 2 * pad);
  const y = (v: number) => H - pad - (v / max) * (H - 2 * pad);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`).join(" ");
  const area = `${path} L${x(pts[pts.length - 1].t).toFixed(1)} ${H - pad} L${x(pts[0].t).toFixed(1)} ${H - pad} Z`;
  // clamp the execute marker into the drawn range (the fix can land after the last datapoint)
  const exec = series.executed_at ? Math.min(W - pad, Math.max(pad, x(series.executed_at))) : null;
  const last = pts[pts.length - 1];
  return (
    <div>
      <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`${series.metric.metric_name} over time`}>
        <line x1={pad} x2={W - pad} y1={H - pad} y2={H - pad} stroke="var(--line)" strokeWidth="1" />
        <path d={area} fill="var(--red-soft)" />
        <path d={path} fill="none" stroke="var(--red)" strokeWidth="1.6" />
        {exec != null && exec >= pad && exec <= W - pad ? (
          <g>
            <line x1={exec} x2={exec} y1={pad} y2={H - pad} stroke="var(--amber)" strokeWidth="1.5" strokeDasharray="3 3" />
          </g>
        ) : null}
        <circle cx={x(last.t)} cy={y(last.v)} r="3" fill={last.v === 0 ? "var(--green)" : "var(--red)"} />
      </svg>
      <div className="spark-legend">
        <span>{series.metric.namespace}/{series.metric.metric_name}</span>
        <span>peak {max}</span>
        {exec != null ? <span style={{ color: "var(--amber)" }}>┆ fix applied</span> : null}
        <span style={{ color: last.v === 0 ? "var(--green)" : "var(--red)" }}>latest {last.v}</span>
      </div>
    </div>
  );
}
