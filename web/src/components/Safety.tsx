import { useEffect, useRef, useState } from "react";
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

const FALLBACK_FILES: Record<number, [string, string]> = {
  0: ["src/beacon/remediation/registry.py", "tests/test_registry.py::test_registry_has_exactly_the_two_allowlisted_actions"],
  1: ["src/beacon/remediation/actions_sg.py", "tests/test_remediate.py::test_dryrun_step_rejects_rule_outside_golden_snapshot"],
  2: ["src/beacon/remediate.py", "tests/test_voice_tools.py::test_propose_fix_dry_runs_under_remediator_and_stores_proposal"],
  3: ["src/beacon/voice_tools.py", "tests/test_voice_tools.py::test_approve_fix_requires_exact_phrase_in_the_raw_transcript"],
  4: ["src/beacon/approvals.py", "tests/test_remediate_steps.py::test_execute_restores_rule_once_and_is_idempotent_on_retry"],
  5: ["src/beacon/remediation/verify.py", "tests/test_remediate_steps.py::test_verify_counts_attempts_and_needs_all_three_checks"],
  6: ["src/beacon/contracts.py", "tests/test_contracts.py::test_match_is_scoped_to_alarm_action_and_exact_params"],
  7: ["src/beacon/remediate.py", "tests/test_remediate_steps.py::test_execute_honours_the_kill_switch"],
};
const TWO_ROLES = {
  id: "two-roles",
  title: "Two roles, one direction",
  rule: "The agent you talk to runs under a read-only role; only the executor, under a write-only role scoped by resource tag, can change anything.",
  file: "console-template.yaml",
  test: "tests/test_template_safety.py::test_voice_role_has_no_write_actions",
};

function controlsOf(safety: SafetyData | null): NonNullable<SafetyData["controls"]> {
  if (safety?.controls?.length) return safety.controls;
  const rules = safety?.rules ?? [];
  return [
    ...rules.map((rule, i) => ({ id: `rule-${i}`, title: rule.split(/[;:.]/)[0], rule, file: FALLBACK_FILES[i]?.[0] ?? "", test: FALLBACK_FILES[i]?.[1] ?? "" })),
    TWO_ROLES,
  ];
}

/** Their FAQ pattern: questions on deep green, the answer as a chat bubble on beige. */
function ControlsProof({ safety }: { safety: SafetyData | null }) {
  const controls = controlsOf(safety);
  const [sel, setSel] = useState(0);
  const list = useRef<HTMLUListElement>(null);
  useEffect(() => {
    if (sel >= controls.length) setSel(0);
  }, [controls.length, sel]);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const next = e.key === "ArrowDown" ? Math.min(controls.length - 1, sel + 1) : Math.max(0, sel - 1);
    setSel(next);
    list.current?.querySelectorAll<HTMLElement>("button")[next]?.focus();
  };
  const c = controls[sel];
  const [testFile, testName] = (c?.test ?? "").split("::");
  return (
    <div className="faq">
      <div className="faq-q">
        <h3>Controls</h3>
        <ul ref={list} role="tablist" aria-orientation="vertical" onKeyDown={onKey}>
          {controls.map((ctl, i) => (
            <li key={ctl.id}>
              <button type="button" role="tab" aria-selected={i === sel} tabIndex={i === sel ? 0 : -1} className={i === sel ? "on" : ""} onClick={() => setSel(i)}>
                {ctl.title}
              </button>
            </li>
          ))}
          {controls.length === 0 ? <li className="dim small">Loading the safety model…</li> : null}
        </ul>
      </div>
      <div className="faq-a" role="tabpanel">
        <h3>Proof</h3>
        {c ? (
          <>
            <div className="faq-ask">{c.title}</div>
            <div className="faq-bubble">
              <p>{c.rule}</p>
              {c.id === "two-roles" ? (
                <pre className="mono small">{IAM_SG}</pre>
              ) : null}
              <dl className="kv">
                <dt>enforced in</dt>
                <dd>{c.file || "—"}</dd>
                <dt>proven by</dt>
                <dd>
                  {testFile ? (
                    <>
                      {testFile}
                      <br />
                      <b>{testName}</b>
                    </>
                  ) : (
                    "—"
                  )}
                </dd>
              </dl>
            </div>
            <span className="faq-mark" aria-hidden="true">
              ✓
            </span>
          </>
        ) : null}
      </div>
    </div>
  );
}

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
                  <td className="mono">
                    {a.id}
                    {a.undo_of ? <div className="small dim">undo of {a.undo_of}</div> : a.inverse ? <div className="small dim">undo: {a.inverse}</div> : null}
                  </td>
                  <td>{a.description}</td>
                  <td className="mono small">{Object.entries(a.params).map(([k, t]) => `${k}: ${t}`).join(", ")}</td>
                  <td className="mono small">{a.iam_actions.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <ControlsProof safety={safety} />
    </div>
  );
}
