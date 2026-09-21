import { useEffect, useRef, useState } from "react";

/* Tiny motion helpers. Everything here degrades to an instant state change
   under prefers-reduced-motion (the CSS collapses animations; the hooks
   short-circuit). No animation library: CSS does the easing. */

export function reducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** A number that counts up (or down) to `target` over `ms` with requestAnimationFrame. */
export function useCountUp(target: number | null | undefined, ms = 600): number | null {
  const [value, setValue] = useState<number | null>(target ?? null);
  const from = useRef<number | null>(target ?? null);
  useEffect(() => {
    if (target == null) {
      setValue(null);
      from.current = null;
      return;
    }
    const start = from.current ?? 0;
    if (start === target || reducedMotion()) {
      setValue(target);
      from.current = target;
      return;
    }
    const t0 = performance.now();
    let raf = 0;
    const step = (t: number) => {
      const p = Math.min(1, (t - t0) / ms);
      const eased = 1 - (1 - p) * (1 - p) * (1 - p); // ease-out cubic
      setValue(start + (target - start) * eased);
      if (p < 1) raf = requestAnimationFrame(step);
      else from.current = target;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return value;
}

/** True once the window has scrolled past `px`; drives the floating nav's shadow. */
export function useScrolled(px = 8): boolean {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > px);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, [px]);
  return scrolled;
}

/**
 * Reveal-on-scroll: every `.reveal` descendant of `root` gets `.in` when it
 * enters the viewport (once). Without IntersectionObserver, or with reduced
 * motion, everything is shown at once so nothing can stay hidden.
 */
export function useReveal(root: React.RefObject<HTMLElement>, deps: unknown[] = []): void {
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const items = Array.from(el.querySelectorAll<HTMLElement>(".reveal:not(.in)"));
    if (!items.length) return;
    if (typeof IntersectionObserver === "undefined" || reducedMotion()) {
      items.forEach((i) => i.classList.add("in"));
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add("in");
            io.unobserve(e.target);
          }
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.05 },
    );
    items.forEach((i) => io.observe(i));
    // anything already on screen at mount reveals on the next frame
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

/**
 * The sliding indicator under the active nav link: measures the active
 * anchor and writes --ind-x / --ind-w on the nav, which CSS transitions.
 */
export function useNavIndicator(nav: React.RefObject<HTMLElement>, active: string): void {
  useEffect(() => {
    const el = nav.current;
    if (!el) return;
    const measure = () => {
      const a = el.querySelector<HTMLElement>("a.active");
      if (!a) return;
      el.style.setProperty("--ind-x", `${a.offsetLeft}px`);
      el.style.setProperty("--ind-w", `${a.offsetWidth}px`);
      el.classList.add("measured");
    };
    measure();
    const t = window.setTimeout(measure, 350); // after web fonts settle
    window.addEventListener("resize", measure);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("resize", measure);
    };
  }, [nav, active]);
}

/**
 * Pointer motion suited to the editorial theme: a soft deep-green spotlight that
 * trails the cursor across the cream page, a small ink ring that grows over
 * anything clickable, cards that tilt a few degrees toward the pointer, and
 * primary buttons that lean toward it when it is close. Fine pointers only;
 * nothing runs on touch devices or under prefers-reduced-motion, and the native
 * cursor is never hidden.
 */
export function useCursorMotion(): void {
  useEffect(() => {
    if (reducedMotion() || !window.matchMedia("(pointer: fine)").matches) return;
    const glow = document.createElement("div");
    glow.className = "cursor-glow";
    const ring = document.createElement("div");
    ring.className = "cursor-ring";
    document.body.append(glow, ring);

    let x = window.innerWidth / 2, y = window.innerHeight / 2; // target
    let gx = x, gy = y, rx = x, ry = y; // eased positions
    let seen = false, raf = 0, hot = false;
    const tiltSel = ".card, .tile, .pillar, .quote, .kpi, .chart, .letter, .versus";
    const magnetSel = ".btn.primary";
    let magnet: HTMLElement | null = null;

    const onMove = (e: PointerEvent) => {
      x = e.clientX; y = e.clientY;
      if (!seen) { seen = true; gx = rx = x; gy = ry = y; glow.style.opacity = "1"; ring.style.opacity = "1"; }
      const t = e.target as HTMLElement | null;
      const clickable = !!t?.closest("a, button, [role=button], [role=tab], input, select, textarea, summary, label");
      if (clickable !== hot) { hot = clickable; ring.classList.toggle("hot", hot); }
      // tilt: the nearest card leans toward the pointer
      const card = t?.closest(tiltSel) as HTMLElement | null;
      if (card) {
        const r = card.getBoundingClientRect();
        const px = (x - r.left) / r.width - 0.5, py = (y - r.top) / r.height - 0.5;
        card.style.setProperty("--tx", `${(-py * 3).toFixed(2)}deg`);
        card.style.setProperty("--ty", `${(px * 3).toFixed(2)}deg`);
        card.classList.add("tilt");
      }
      // magnet: primary buttons lean toward a close pointer
      const btn = t?.closest(magnetSel) as HTMLElement | null;
      if (btn !== magnet) { magnet?.style.removeProperty("transform"); magnet = btn; }
      if (btn) {
        const r = btn.getBoundingClientRect();
        const dx = x - (r.left + r.width / 2), dy = y - (r.top + r.height / 2);
        btn.style.transform = `translate(${(dx * 0.12).toFixed(1)}px, ${(dy * 0.18).toFixed(1)}px)`;
      }
    };
    const onOut = (e: PointerEvent) => {
      const card = (e.target as HTMLElement | null)?.closest(tiltSel) as HTMLElement | null;
      if (card && !card.contains(e.relatedTarget as Node | null)) { card.classList.remove("tilt"); card.style.removeProperty("--tx"); card.style.removeProperty("--ty"); }
    };
    const onDown = () => ring.classList.add("down");
    const onUp = () => ring.classList.remove("down");
    const onLeave = () => { glow.style.opacity = "0"; ring.style.opacity = "0"; seen = false; };
    const tick = () => {
      gx += (x - gx) * 0.08; gy += (y - gy) * 0.08; // the spotlight lags
      rx += (x - rx) * 0.35; ry += (y - ry) * 0.35; // the ring keeps up
      glow.style.transform = `translate3d(${gx.toFixed(1)}px, ${gy.toFixed(1)}px, 0)`;
      ring.style.transform = `translate3d(${rx.toFixed(1)}px, ${ry.toFixed(1)}px, 0)`;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    document.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("pointerout", onOut, { passive: true });
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("pointerup", onUp);
    document.documentElement.addEventListener("mouseleave", onLeave);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerout", onOut);
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("pointerup", onUp);
      document.documentElement.removeEventListener("mouseleave", onLeave);
      glow.remove(); ring.remove();
    };
  }, []);
}
