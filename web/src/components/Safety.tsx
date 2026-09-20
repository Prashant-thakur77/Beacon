import type { Safety as SafetyData } from "../types";
import { Marquee } from "./Marquee";

const IAM_SG = `{
  "Sid": "RestoreIngressOnTaggedGroups",
  "Effect": "Allow",
  "Action": "ec2:AuthorizeSecurityGroupIngress",
  "Resource": "arn:aws:ec2:<region>:<account>:security-group/*",
  "Condition": { "StringEquals": { "aws:ResourceTag/beacon:remediable": "true" } }
},
{
  "Sid": "RestoreIngressRuleResource",
  "Effect": "Allow",
  "Action": "ec2:AuthorizeSecurityGroupIngress",
  "Resource": "arn:aws:ec2:<region>:<account>:security-group-rule/*"
}`;

export function Safety({ safety }: { safety: SafetyData | null }) {
  const flags = safety?.apply_enabled ?? {};
  return (
    <div className="stack">
      <Marquee />
      <div className="panel">
        <div className="panel-h">
          <h2>Kill switch</h2>
          <span className="meta">APPLY_ENABLED per function</span>
        </div>
        <div className="panel-b row">
          {(["triage", "voice", "remediate"] as const).map((k) => {
            const v = flags[k];
            return (
              <span key={k} className={`pill ${v === true ? "green" : v === false ? "red" : "dim"}`}>
                {k}: {v === true ? "writes allowed" : v === false ? "writes blocked" : "unknown"}
              </span>
            );
          })}
          <span className="faint small">
            <code className="mono">make apply-off</code> flips all three; nothing can execute until <code className="mono">make apply-on</code>.
          </span>
        </div>
      </div>

      <div className="panel">
        <div className="panel-h">
          <h2>Allowlist</h2>
          <span className="meta">registry.py — nothing else can run</span>
        </div>
        <div className="panel-b scroll-x">
          <table className="t">
            <thead>
              <tr>
                <th>action</th>
                <th>what it does</th>
                <th>params (exact schema)</th>
                <th>IAM write action</th>
              </tr>
            </thead>
            <tbody>
              {(safety?.allowlist ?? []).map((a) => (
                <tr key={a.id}>
                  <td className="mono">{a.id}</td>
                  <td>{a.description}</td>
                  <td className="mono small">{Object.entries(a.params).map(([k, t]) => `${k}: ${t}`).join(", ")}</td>
                  <td className="mono small">{a.iam_actions.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <div className="panel-h">
          <h2>Two roles, one direction</h2>
          <span className="meta">proven by tests/test_template_safety.py</span>
        </div>
        <div className="panel-b stack">
          <p className="dim" style={{ margin: 0 }}>
            The agent you talk to runs under a <b>read-only</b> role. The only thing it can write is a Step Functions execution and an approval record. The
            executor runs under a <b>write-only</b> role scoped by resource tag, and it dry-runs every action first — under that same role, because EC2 only
            tells the truth about permissions to the caller that will execute.
          </p>
          <pre className="ev" style={{ margin: 0 }}>
            <code className="mono small">{IAM_SG}</code>
          </pre>
          <p className="faint small" style={{ margin: 0 }}>
            The second statement exists because <code className="mono">AuthorizeSecurityGroupIngress</code> is authorised against the rule being created as well as
            the group, and a rule that does not exist yet cannot carry a tag.
          </p>
        </div>
      </div>

      <div className="panel">
        <div className="panel-h">
          <h2>Rules</h2>
        </div>
        <div className="panel-b">
          <ol className="rules">{(safety?.rules ?? []).map((r, i) => <li key={i}>{r}</li>)}</ol>
        </div>
      </div>
    </div>
  );
}
