import { useEffect, useRef, useState } from "react";
import type { Tally } from "../types";
import { Marquee } from "./Marquee";

/* The product landing page (`#home`). Everything here is the product's own
   words and screenshots; the console routes sit unchanged behind it. */

const FILM = "https://youtu.be/a3SxZHvIkCo";

function minutes(v: number | null | undefined): string {
  if (v == null) return "–";
  return v < 1 ? `${Math.round(v * 60)} s` : `${Math.round(v * 10) / 10} min`;
}

const FEATURES = [
  {
    id: "talk",
    title: "Talk to it",
    text: "Hold the mic. Beacon briefs you on the alarm, the CloudTrail change behind it and the root cause, and every sentence it speaks is pinned to an evidence chip you can open.",
    proof: "src/beacon/voice_tools.py · tests/test_voice_tools.py::test_get_incident_brief_returns_rca_and_evidence_card",
    img: "/landing/night.png",
    pos: "70% 30%",
  },
  {
    id: "approve",
    title: "Approve with your words",
    text: "One allowlisted fix, dry-run under the executor's own role, with its blast radius spelled out. It applies only when the raw transcript contains “approve fix one”.",
    proof: "src/beacon/approvals.py · tests/test_voice_tools.py::test_approve_fix_requires_exact_phrase_in_the_raw_transcript",
    img: "/landing/board.png",
    pos: "50% 55%",
  },
  {
    id: "sleep",
    title: "Sleep through the repeat",
    text: "After the recovery is proven, grant a Sleep Contract in your own voice: one alarm, one action, exact resources, a use count, an expiry. The second night, nobody is woken.",
    proof: "src/beacon/contracts.py · tests/test_contracts.py::test_match_is_scoped_to_alarm_action_and_exact_params",
    img: "/landing/analytics.png",
    pos: "50% 30%",
  },
];

const PILLARS = [
  { title: "Evidence before the model", text: "A golden security-group snapshot taken on a healthy stack and a CloudTrail change ledger come first; the model reasons over what is already known to be true." },
  { title: "Verified means proven", text: "A fix is resolved only when the alarm returns to OK after the change, the error metric is zero, and the post-condition holds. Otherwise it escalates, honestly." },
  { title: "Two roles, one direction", text: "The agent you talk to is read-only. The executor is write-only, scoped by resource tag, and dry-runs under that same role. The templates are asserted by tests." },
  { title: "Runs on your laptop too", text: "`make local` runs the whole product against an in-process moto AWS with a scripted agent: same tools, same safety checks, no account needed." },
];

const QUOTES = [
  { from: "from the demo night", text: "grant contract for seven days", note: "the exact phrase that creates a Sleep Contract, kept as the record" },
  { from: "from the safety tests", text: "fix 1 sg.restore_ingress · dry run PASSED (DryRunOperation, via remediator role)", note: "every action is rehearsed under the role that will execute it" },
  { from: "from the runbook", text: "Resolved under your Sleep Contract; you were not woken.", note: "the line Beacon says on the second night" },
];

const FAQ = [
  { q: "Is it safe?", a: "Only two allowlisted actions exist; params must match the schema exactly; every action is dry-run under the write-only executor role; approval is checked against the engineer's raw transcript, never the model's claim; one approval executes exactly once; and APPLY_ENABLED=false stops every write path." },
  { q: "What can it change?", a: "Restore one security-group ingress rule that exists in the golden snapshot, or force a new deployment of one ECS service. Both are scoped by the beacon:remediable resource tag in IAM." },
  { q: "What if the fix fails?", a: "Verification needs three things: the alarm back to OK after the fix, the error metric at zero, and the post-condition. After the retry window it escalates to a human and says so on the board." },
  { q: "What happens without Bedrock?", a: "Triage falls back to a deterministic summary and the console still shows the alarm, the CloudTrail change and the timeline. On a fresh AWS account under verification hold, that is the state the live URL is in today." },
  { q: "Can I run it without an AWS account?", a: "Yes. `make setup && make local` runs the whole product against moto with a scripted agent, and `?night=1` plays the entire night unattended in the browser." },
  { q: "What did it cost?", a: "Nova 2 Lite list price at a fixed ₹/USD gives an order of magnitude on the Analytics page: a few tenths of a rupee per incident. It is not a bill." },
];

function Faq() {
  const [sel, setSel] = useState(0);
  const list = useRef<HTMLUListElement>(null);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const next = e.key === "ArrowDown" ? Math.min(FAQ.length - 1, sel + 1) : Math.max(0, sel - 1);
    setSel(next);
    list.current?.querySelectorAll<HTMLElement>("button")[next]?.focus();
  };
  return (
    <div className="faq">
      <div className="faq-q">
        <h3>Questions</h3>
        <ul ref={list} role="tablist" aria-orientation="vertical" onKeyDown={onKey}>
          {FAQ.map((f, i) => (
            <li key={f.q}>
              <button type="button" role="tab" aria-selected={i === sel} tabIndex={i === sel ? 0 : -1} className={i === sel ? "on" : ""} onClick={() => setSel(i)}>
                {f.q}
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="faq-a" role="tabpanel">
        <h3>Answer</h3>
        <div className="faq-ask">{FAQ[sel].q}</div>
        <div className="faq-bubble" key={sel}>
          <p>{FAQ[sel].a}</p>
        </div>
        <span className="faq-mark" aria-hidden="true">
          ✓
        </span>
      </div>
    </div>
  );
}

/** Sticky list on the left; the card on the right swaps as the matching step scrolls into view. */
function Features() {
  const [active, setActive] = useState(0);
  const steps = useRef<Array<HTMLDivElement | null>>([]);
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) setActive(Number((e.target as HTMLElement).dataset.i));
        }
      },
      { rootMargin: "-40% 0px -40% 0px", threshold: 0 },
    );
    steps.current.forEach((el) => el && io.observe(el));
    return () => io.disconnect();
  }, []);
  const f = FEATURES[active];
  return (
    <div className="features">
      <div className="features-list">
        <ul role="tablist" aria-orientation="vertical">
          {FEATURES.map((it, i) => (
            <li key={it.id}>
              <button
                type="button"
                role="tab"
                aria-selected={i === active}
                className={i === active ? "on" : ""}
                onClick={() => {
                  setActive(i);
                  steps.current[i]?.scrollIntoView({ behavior: "smooth", block: "center" });
                }}
              >
                {it.title}
              </button>
            </li>
          ))}
        </ul>
        <div className="feature-card" key={f.id}>
          <div className="shot" style={{ backgroundImage: `url(${f.img})`, backgroundPosition: f.pos }} role="img" aria-label={`${f.title} in the console`} />
          <div className="feature-proof mono small">{f.proof}</div>
        </div>
      </div>
      <div className="features-steps">
        {FEATURES.map((it, i) => (
          <div key={it.id} className="feature-step" data-i={i} ref={(el) => (steps.current[i] = el)}>
            <h3 className="display">{it.title}</h3>
            <p className="lede">{it.text}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

export function Landing({ tally, local, onRunNight }: { tally: Tally | null; local: boolean; onRunNight?: () => void }) {
  const live = !!tally && tally.resolved > 0;
  const stats = live
    ? [
        { n: String(tally.resolved), l: "incidents resolved" },
        { n: minutes(tally.median_minutes_to_recovery), l: "median time to recovery" },
        { n: String(tally.humans_woken), l: "humans woken" },
      ]
    : [
        { n: "2", l: "incidents resolved" },
        { n: "2.2 min", l: "median time to recovery" },
        { n: "1", l: "human woken" },
      ];
  return (
    <main key="home" className="landing route-in">
      {/* 1 · hero */}
      <section className="hero">
        <div className="eyebrow">Beacon Night Shift</div>
        <h1 className="display hero-h">
          The 3 AM page, <em>fixed with your voice.</em>
        </h1>
        <p className="lede hero-sub">An on-call agent for AWS that finds the cause, proposes one safe fix, applies it on your word, proves the recovery, and does not wake you the second time.</p>
        <div className="row hero-cta">
          <a className="btn primary" href="#board">
            Open the Night Board
          </a>
          {local && onRunNight ? (
            <button type="button" className="btn" onClick={onRunNight}>
              ▶ Run the night
            </button>
          ) : (
            <a className="btn" href={FILM} target="_blank" rel="noopener noreferrer">
              ▶ Watch the film
            </a>
          )}
        </div>
        <div className="hero-shot reveal">
          <div className="shot-card">
            <img src="/landing/board.png" alt="The Night Board: tally, incident cards and the Talk panel" loading="eager" />
          </div>
          <div className="ribbon-tag" aria-hidden="true">
            <span className="wave">
              <i />
              <i />
              <i />
              <i />
              <i />
            </span>
            approve fix one
          </div>
        </div>
        <div className="faint small">Built on AWS · First Commit 2026</div>
      </section>

      {/* 2 · ink band */}
      <div className="band">
        <Marquee label="Built on" />
      </div>

      {/* 3 · stats on deep green */}
      <section className="green-band">
        <h2 className="display">
          <em>Nobody</em> woken the second time.
        </h2>
        <p className="lede">{live ? "Live numbers from this deployment's tally." : "From the scripted night in local mode."}</p>
        <div className="stats reveal">
          {stats.map((s) => (
            <div key={s.l} className="stat">
              <div className="n">{s.n}</div>
              <div className="l">{s.l}</div>
            </div>
          ))}
        </div>
        <div className="versus reveal">
          <div className="vs-card small-card">
            <div className="l">Human on call</div>
            <div className="n">12 min</div>
            <div className="l">to wake, read, fix, watch</div>
          </div>
          <div className="vs-card big-card" style={{ backgroundImage: "url(/landing/night.png)" }}>
            <div className="vs-inner">
              <div className="l">Beacon</div>
              <div className="n">30 s</div>
              <div className="l">to a proven recovery, on your word</div>
            </div>
          </div>
        </div>
      </section>

      {/* 4 · feature trio */}
      <section className="section">
        <div className="eyebrow center">How it works</div>
        <h2 className="display center">
          Three things, <em>in your own voice.</em>
        </h2>
        <Features />
      </section>

      {/* 5 · pillars */}
      <section className="section">
        <h2 className="display center">
          Built around <em>how on-call works.</em>
        </h2>
        <div className="pillars">
          {PILLARS.map((p, i) => (
            <div key={p.title} className="pillar reveal" style={{ "--i": i } as React.CSSProperties}>
              <h3>{p.title}</h3>
              <p>{p.text}</p>
            </div>
          ))}
        </div>
      </section>

      {/* 6 · quotes */}
      <section className="section">
        <h2 className="display center">
          From the first <em>nights.</em>
        </h2>
        <div className="quotes">
          {QUOTES.map((q, i) => (
            <figure key={q.from} className="quote-card reveal" style={{ "--i": i } as React.CSSProperties}>
              <blockquote>“{q.text}”</blockquote>
              <figcaption>
                <span className="eyebrow">{q.from}</span>
                <span className="dim small">{q.note}</span>
              </figcaption>
            </figure>
          ))}
        </div>
      </section>

      {/* 7 · FAQ */}
      <section className="section">
        <div className="eyebrow center">FAQs</div>
        <h2 className="display center">
          Good <em>questions.</em>
        </h2>
        <Faq />
      </section>

      {/* 8 · final CTA */}
      <section className="section cta">
        <h2 className="display center">
          Ready for the <em>next night?</em>
        </h2>
        <div className="row center">
          <a className="btn primary" href="#board">
            Open the Night Board
          </a>
          <a className="btn" href={FILM} target="_blank" rel="noopener noreferrer">
            ▶ Watch the film
          </a>
        </div>
      </section>
    </main>
  );
}
