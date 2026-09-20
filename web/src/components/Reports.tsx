import { useEffect, useState } from "react";
import type { Api } from "../api";
import { formatStamp } from "../hooks";
import type { AuditRow, MorningReport } from "../types";

/* ---------- a tiny safe Markdown renderer: text nodes only, never raw HTML ---------- */

function inline(text: string, key = 0): React.ReactNode[] {
  // `code`, **bold**, in that order; everything else is a text node
  const out: React.ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("`")) out.push(<code key={`${key}-${i++}`}>{tok.slice(1, -1)}</code>);
    else out.push(<b key={`${key}-${i++}`}>{tok.slice(2, -2)}</b>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text }: { text: string }) {
  const lines = text.split(/\r?\n/);
  const nodes: React.ReactNode[] = [];
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      const Tag = (`h${h[1].length}` as "h1" | "h2" | "h3");
      nodes.push(<Tag key={k++}>{inline(h[2], k)}</Tag>);
      i++;
      continue;
    }
    if (line.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        const cells = lines[i].slice(1, lines[i].endsWith("|") ? -1 : undefined).split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-+:?$/.test(c))) rows.push(cells);
        i++;
      }
      const [head, ...body] = rows;
      nodes.push(
        <div key={k++} className="scroll-x">
          <table className="t md-table">
            {head ? (
              <thead>
                <tr>
                  {head.map((c, ci) => (
                    <th key={ci}>{inline(c, k)}</th>
                  ))}
                </tr>
              </thead>
            ) : null}
            <tbody>
              {body.map((r, ri) => (
                <tr key={ri}>
                  {r.map((c, ci) => (
                    <td key={ci}>{inline(c, k)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i])) items.push(lines[i++].replace(/^[-*]\s+/, ""));
      nodes.push(
        <ul key={k++}>
          {items.map((it, ii) => (
            <li key={ii}>{inline(it, k)}</li>
          ))}
        </ul>,
      );
      continue;
    }
    const para: string[] = [line];
    i++;
    while (i < lines.length && lines[i].trim() && !/^(#{1,3}\s|\||[-*]\s)/.test(lines[i])) para.push(lines[i++]);
    nodes.push(<p key={k++}>{inline(para.join(" "), k)}</p>);
  }
  return <div className="md">{nodes}</div>;
}

function download(name: string, body: string, type: string) {
  const blob = new Blob([body], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ---------- postmortem ---------- */

export function Postmortem({ id, api, fallback, onToast }: { id: string; api: Api | null; fallback: (() => string | null) | null; onToast: (t: string) => void }) {
  const [md, setMd] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setMd(null);
    setErr(null);
    if (api) {
      api
        .postmortem(id)
        .then(setMd)
        .catch((e) => {
          const local = fallback?.();
          if (local) setMd(local);
          else setErr(e instanceof Error ? e.message : String(e));
        });
    } else {
      const local = fallback?.();
      if (local) setMd(local);
      else setErr("No incident with that id in this replay.");
    }
  }, [id, api, fallback]);
  return (
    <div className="stack letter">
      <div className="row letter-tools">
        <a className="btn ghost" href={`#board/${encodeURIComponent(id)}`}>
          ← Incident
        </a>
        <span className="eyebrow">Postmortem</span>
        <span style={{ marginLeft: "auto" }} />
        <button type="button" className="btn" disabled={!md} onClick={() => md && download(`postmortem-${id.slice(0, 8)}.md`, md, "text/markdown")}>
          Download .md
        </button>
        <button type="button" className="btn" disabled={!md} onClick={() => md && void navigator.clipboard?.writeText(md).then(() => onToast("Postmortem copied"))}>
          Copy
        </button>
      </div>
      {err ? <div className="err">{err}</div> : md ? <Markdown text={md} /> : <p className="dim">Writing the postmortem…</p>}
    </div>
  );
}

/* ---------- audit ---------- */

type AuditFilter = "all" | "approvals" | "contracts" | "executed";

export function Audit({ rows, loading, csvUrl, onReplay, replay }: { rows: AuditRow[] | null; loading: boolean; csvUrl?: string; onReplay?: () => void; replay: boolean }) {
  const [filter, setFilter] = useState<AuditFilter>("all");
  const list = (rows ?? []).filter((r) => (filter === "all" ? true : filter === "approvals" ? r.kind === "approval" : filter === "contracts" ? r.kind === "contract" : !!r.executed));
  const json = () => download("beacon-audit.json", JSON.stringify({ rows: rows ?? [], count: rows?.length ?? 0 }, null, 2), "application/json");
  return (
    <div className="stack">
      <div className="page-h">
        <h1 className="display">
          Every consent, <em>in your words.</em>
        </h1>
        <p className="lede">Approvals and Sleep Contracts, newest first, with the transcript that granted each one and what happened next.</p>
      </div>
      <div className="row">
        <div className="chips" role="group" aria-label="Filter records">
          {(
            [
              ["all", "All"],
              ["approvals", "approvals"],
              ["contracts", "contracts"],
              ["executed", "executed"],
            ] as const
          ).map(([k, label]) => (
            <button key={k} type="button" className={`chip-btn${filter === k ? " on" : ""}`} aria-pressed={filter === k} onClick={() => setFilter(k)}>
              {label}
            </button>
          ))}
        </div>
        <span style={{ marginLeft: "auto" }} />
        {csvUrl && !replay ? (
          <a className="btn" href={csvUrl} target="_blank" rel="noopener noreferrer">
            Export CSV
          </a>
        ) : null}
        <button type="button" className="btn" onClick={json} disabled={!rows?.length}>
          Export JSON
        </button>
      </div>
      {loading && !rows ? (
        <p className="dim">Loading the audit log…</p>
      ) : !rows || rows.length === 0 ? (
        <div className="empty">
          <h3>No consent recorded yet.</h3>
          <p>Every approval and every Sleep Contract lands here with the exact words that granted it.</p>
          {onReplay ? (
            <button type="button" className="btn primary" onClick={onReplay}>
              ▶ View the archived night
            </button>
          ) : null}
        </div>
      ) : (
        <div className="scroll-x">
          <table className="t audit">
            <thead>
              <tr>
                <th>when</th>
                <th>kind</th>
                <th>alarm · action</th>
                <th>the words</th>
                <th>channel</th>
                <th>outcome</th>
              </tr>
            </thead>
            <tbody>
              {list.map((r) => (
                <tr key={`${r.kind}-${r.id}`}>
                  <td className="mono small nowrap">{formatStamp(r.at ?? undefined)}</td>
                  <td>
                    <span className={`pill ${r.kind === "contract" ? "lilac" : "green"}`}>{r.kind}</span>
                  </td>
                  <td>
                    {r.incident_id ? <a href={`#board/${encodeURIComponent(r.incident_id)}`}>{r.alarm_name ?? "incident"}</a> : r.alarm_name}
                    <div className="mono small dim">{r.action}</div>
                  </td>
                  <td className="quote-cell">“{r.quote}”</td>
                  <td className="small dim">
                    {r.channel}
                    {r.source ? ` · ${r.source}` : ""}
                  </td>
                  <td className="small">
                    {r.kind === "approval" ? (
                      r.executed ? (
                        <span className="pill green">executed{r.result ? ` · ${r.result}` : ""}</span>
                      ) : (
                        <span className="pill dim">not executed</span>
                      )
                    ) : (
                      <>
                        <span className="mono">{r.uses} uses</span>
                        <div className="dim">expires {formatStamp(r.expires_at ?? undefined)}</div>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ---------- morning report ---------- */

function shiftNight(night: string, days: number): string {
  const d = new Date(`${night}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export function Report({ report, loading, night, onNight, onReplay, replay }: { report: MorningReport | null; loading: boolean; night: string | null; onNight: (n: string | null) => void; onReplay?: () => void; replay: boolean }) {
  const n = report?.night_of ?? night;
  return (
    <div className="stack">
      <div className="page-h">
        <div className="eyebrow">Morning report</div>
        <h1 className="display letter-subject">{report ? report.subject : loading ? "Writing the morning report…" : "No report yet"}</h1>
        <p className="lede">
          Sent to the SNS topic every day at 07:00 IST; the same text, here, for any night.
          {replay ? " Showing the archived night." : ""}
        </p>
      </div>
      <div className="row">
        <button type="button" className="btn" onClick={() => n && onNight(shiftNight(n, -1))} disabled={!n}>
          ← previous night
        </button>
        <span className="mono">{n ?? "–"}</span>
        <button type="button" className="btn" onClick={() => n && onNight(shiftNight(n, 1))} disabled={!n}>
          next night →
        </button>
        <button type="button" className="btn ghost" onClick={() => onNight(null)} disabled={!night}>
          latest
        </button>
      </div>
      {report ? (
        <div className="letter">
          <div className="kpis rise">
            <div className="tile">
              <div className="n">{report.incidents}</div>
              <div className="l">incidents</div>
            </div>
            <div className="tile">
              <div className="n">{report.resolved}</div>
              <div className="l">resolved</div>
            </div>
            <div className="tile">
              <div className="n">{report.escalated}</div>
              <div className="l">escalated</div>
            </div>
            <div className="tile">
              <div className="n">{report.humans_woken}</div>
              <div className="l">humans woken</div>
            </div>
            <div className="tile moon">
              <div className="n">{report.handled_by_contract}</div>
              <div className="l">under a Sleep Contract</div>
            </div>
            <div className="tile">
              <div className="n">{report.median_minutes_to_recovery == null ? "–" : `${report.median_minutes_to_recovery} min`}</div>
              <div className="l">median recovery</div>
            </div>
          </div>
          <div className="letter-body">
            {report.text.split("\n").map((line, i) =>
              line.startsWith("- ") ? (
                <p key={i} className="letter-item">
                  {line.slice(2)}
                </p>
              ) : (
                <p key={i}>{line}</p>
              ),
            )}
          </div>
          {report.contracts_used.length ? (
            <div className="row">
              <span className="eyebrow">Contracts used</span>
              {report.contracts_used.map((c) => (
                <span key={c.contract_id ?? c.alarm_name ?? ""} className="pill lilac">
                  {c.alarm_name} · {c.uses}
                </span>
              ))}
            </div>
          ) : null}
          {report.incident_ids.length ? (
            <div className="row small">
              <span className="eyebrow">Incidents</span>
              {report.incident_ids.map((id) => (
                <a key={id} className="chip" href={`#postmortem/${encodeURIComponent(id)}`}>
                  {id.slice(0, 8)}… postmortem
                </a>
              ))}
            </div>
          ) : null}
          <div className="faint small">
            Model cost for the night: ₹{report.cost_inr.toFixed(2)} · generated {formatStamp(report.generated_at)}
            {report.incidents === 0 && !night ? " · the night in progress is reported after 07:00 IST; use “next night →” to peek." : ""}
          </div>
        </div>
      ) : !loading && onReplay ? (
        <div className="empty">
          <h3>Nothing to report yet.</h3>
          <p>The archived night has a report of its own.</p>
          <button type="button" className="btn primary" onClick={onReplay}>
            ▶ View the archived night
          </button>
        </div>
      ) : null}
    </div>
  );
}
