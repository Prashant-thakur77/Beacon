import { useEffect, useRef, useState } from "react";
import { JudgeBody } from "./NightBoard";

/* Page chrome: announcement strip, footer, toasts, the help popover.
   Nothing here touches the Talk panel or the incident feed. */

const REPO = "https://github.com/Prashant-thakur77/Beacon";
const FOOTER: Array<{ label: string; links: Array<{ text: string; href: string; external?: boolean }> }> = [
  {
    label: "Product",
    links: [
      { text: "Night Board", href: "#board" },
      { text: "Analytics", href: "#analytics" },
      { text: "Contracts", href: "#contracts" },
      { text: "Safety", href: "#safety" },
    ],
  },
  {
    label: "Run it",
    links: [
      { text: "README · Try it", href: `${REPO}#try-it`, external: true },
      { text: "Human runbook", href: `${REPO}/blob/main/docs/human-runbook.md`, external: true },
      { text: "Deploy workflow", href: `${REPO}/blob/main/.github/workflows/deploy.yaml`, external: true },
    ],
  },
  {
    label: "Trust",
    links: [
      { text: "Safety model", href: `${REPO}/blob/main/docs/safety.md`, external: true },
      { text: "Production notes", href: `${REPO}#production-notes`, external: true },
      { text: "SECURITY.md", href: `${REPO}/blob/main/SECURITY.md`, external: true },
    ],
  },
  {
    label: "Project",
    links: [
      { text: "GitHub repo", href: REPO, external: true },
      { text: "Release · demo film", href: `${REPO}/releases/tag/v0.2.0`, external: true },
      { text: "Blog: the transcript is the safety artifact", href: "https://builder.aws.com/post/3Jb5v7ouXDReILJ7WMuHrlB1leL_p/why-transcript-is-the-safety-artifactvoice-approved-aws-remediation-with-strands-and-step-functions", external: true },
    ],
  },
];

export function Footer({ version }: { version?: string }) {
  return (
    <footer className="footer">
      <div className="footer-cols">
        {FOOTER.map((col) => (
          <div key={col.label} className="footer-col">
            <div className="footer-label">{col.label}</div>
            {col.links.map((l) => (
              <a key={l.text} href={l.href} target={l.external ? "_blank" : undefined} rel={l.external ? "noopener noreferrer" : undefined}>
                {l.text}
              </a>
            ))}
          </div>
        ))}
      </div>
      <div className="wordmark" aria-hidden="true">
        Beacon
      </div>
      <div className="footer-line">
        <span>© Beacon Night Shift · built on AWS for First Commit 2026</span>
        <span className="kbd-hint" title="Keyboard shortcuts">
          <kbd>g</kbd> <kbd>b</kbd> board · <kbd>g</kbd> <kbd>a</kbd> analytics · <kbd>g</kbd> <kbd>c</kbd> contracts · <kbd>g</kbd> <kbd>s</kbd> safety · <kbd>g</kbd> <kbd>h</kbd> home · <kbd>/</kbd> talk · <kbd>?</kbd> help · <kbd>esc</kbd> close
        </span>
        <span className="mono">{version ? `v${version}` : ""}</span>
      </div>
    </footer>
  );
}

/** The thin deep-green strip above the nav. */
export function Announce({ text, href, onClick }: { text: string; href?: string; onClick?: () => void }) {
  return (
    <div className="announce">
      <a
        href={href ?? "#"}
        onClick={(e) => {
          if (onClick) {
            e.preventDefault();
            onClick();
          }
        }}
      >
        {text} <span aria-hidden="true">›</span>
      </a>
    </div>
  );
}

export interface ToastItem {
  id: number;
  text: string;
  tone?: "ok" | "info";
}

export function Toasts({ items, onDismiss }: { items: ToastItem[]; onDismiss: (id: number) => void }) {
  if (!items.length) return null;
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((t) => (
        <button key={t.id} type="button" className={`toast ${t.tone ?? "info"}`} onClick={() => onDismiss(t.id)} title="Dismiss">
          <span className="toast-dot" />
          {t.text}
        </button>
      ))}
    </div>
  );
}

let toastSeq = 1;
export function useToasts(ms = 4000): { toasts: ToastItem[]; push: (text: string, tone?: ToastItem["tone"]) => void; dismiss: (id: number) => void } {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const dismiss = (id: number) => setToasts((t) => t.filter((x) => x.id !== id));
  const push = (text: string, tone?: ToastItem["tone"]) => {
    const id = toastSeq++;
    setToasts((t) => [...t.slice(-2), { id, text, tone }]);
    window.setTimeout(() => dismiss(id), ms);
  };
  return { toasts, push, dismiss };
}

/** Round lavender "?" bottom-left; opens the judge content as a popover on every route. */
export function HelpFab({ onBoard, hasPasscode }: { onBoard: boolean; hasPasscode: boolean }) {
  const [open, setOpen] = useState(false);
  const pop = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    pop.current?.querySelector<HTMLElement>("button, a")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);
  const onClick = () => {
    if (onBoard) {
      const el = document.querySelector<HTMLElement>(".judge");
      if (el) {
        el.setAttribute("open", "");
        el.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
    }
    setOpen((o) => !o);
  };
  return (
    <>
      <button type="button" className={`fab${open ? " on" : ""}`} onClick={onClick} aria-label="How to judge this" aria-expanded={open} title="How to judge this in 90 seconds">
        ?
      </button>
      {open ? (
        <div className="popover" ref={pop} role="dialog" aria-label="How to judge this in 90 seconds">
          <div className="popover-h">
            <span className="eyebrow">How to judge this in 90 seconds</span>
            <button type="button" className="btn ghost" onClick={() => setOpen(false)} aria-label="Close">
              ✕
            </button>
          </div>
          <div className="stack small">
            <JudgeBody hasPasscode={hasPasscode} />
          </div>
        </div>
      ) : null}
    </>
  );
}
