import { formatStamp } from "../hooks";
import type { Contract } from "../types";

function countdown(iso: string, now: Date): string {
  const ms = new Date(iso).getTime() - now.getTime();
  if (Number.isNaN(ms) || ms <= 0) return "expired";
  const h = Math.floor(ms / 3.6e6);
  const d = Math.floor(h / 24);
  return d >= 1 ? `${d}d ${h % 24}h left` : `${h}h ${Math.floor((ms % 3.6e6) / 6e4)}m left`;
}

export function Contracts({
  contracts,
  now,
  onRevoke,
  canRevoke,
}: {
  contracts: Contract[];
  now: Date;
  onRevoke: (id: string) => void;
  canRevoke: boolean;
}) {
  return (
    <div className="stack">
      <div className="banner">
        ☾ A Sleep Contract is a standing approval you grant by voice: one alarm, one allowlisted action, exact resources, a use count, an expiry — and your own words as the record.
      </div>
      {contracts.length === 0 ? (
        <div className="empty">
          <h3>No contracts yet.</h3>
          <p>After Beacon verifies a fix, it asks whether to handle the same alarm itself next time. Saying yes, then the grant phrase, creates one.</p>
        </div>
      ) : (
        <div className="grid2">
          {contracts.map((c) => (
            <div key={c.contract_id} className="contract">
              <div className="row">
                <span className="pill lilac">{countdown(c.expires_at, now)}</span>
                <span className="pill dim">
                  {c.max_uses - c.uses} of {c.max_uses} uses left
                </span>
              </div>
              <div style={{ marginTop: 8, fontWeight: 800 }}>{c.alarm_name}</div>
              <div className="mono small dim">{c.action}</div>
              <dl className="kv">
                {Object.entries(c.scope ?? {}).map(([k, v]) => (
                  <>
                    <dt key={`${k}-t`}>{k}</dt>
                    <dd key={`${k}-d`}>{String(v)}</dd>
                  </>
                ))}
              </dl>
              <div className="quote">“{c.transcript_quote}”</div>
              <div className="faint small">
                granted via {c.granted_by} · {formatStamp(c.granted_at)} · expires {formatStamp(c.expires_at)}
              </div>
              <div className="row" style={{ marginTop: 10 }}>
                <button className="btn danger" onClick={() => onRevoke(c.contract_id)} disabled={!canRevoke} title={canRevoke ? "Revoke this contract" : "Enter the passcode to revoke"}>
                  Revoke
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
