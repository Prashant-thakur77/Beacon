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
