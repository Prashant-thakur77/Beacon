import { useCallback, useEffect, useRef, useState } from "react";

const MAX_BACKOFF_MS = 60_000;

/**
 * Poll `fn` every `ms` while `enabled`; keeps the last good value across errors.
 * Consecutive failures back off (x2, capped at 60 s) so a dead API is not
 * hammered, and polling pauses while the tab is hidden and resumes at once
 * when it is shown again.
 */
export function usePoll<T>(fn: (() => Promise<T>) | null, ms: number, deps: unknown[] = [], enabled = true) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const timer = useRef<number | null>(null);
  const alive = useRef(true);
  const failures = useRef(0);
  const inFlight = useRef(false);

  const tick = useCallback(async () => {
    if (!fn || inFlight.current) return;
    inFlight.current = true;
    try {
      const value = await fn();
      if (alive.current) {
        setData(value);
        setError(null);
        failures.current = 0;
      }
    } catch (e) {
      failures.current += 1;
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      inFlight.current = false;
      if (alive.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    alive.current = true;
    if (!enabled || !fn) {
      setLoading(false);
      return;
    }
    const schedule = () => {
      if (!alive.current) return;
      const delay = Math.min(ms * 2 ** failures.current, MAX_BACKOFF_MS);
      timer.current = window.setTimeout(run, delay);
    };
    const run = async () => {
      if (document.visibilityState === "hidden") return; // resumed by visibilitychange
      await tick();
      schedule();
    };
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      if (timer.current) window.clearTimeout(timer.current);
      void run();
    };
    document.addEventListener("visibilitychange", onVisible);
    void run();
    return () => {
      alive.current = false;
      document.removeEventListener("visibilitychange", onVisible);
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [tick, ms, enabled, fn]);

  return { data, error, loading, refresh: tick, setData };
}

export function useClock(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

export function useLocalState<T>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = sessionStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });
  const set = (v: T) => {
    setValue(v);
    try {
      sessionStorage.setItem(key, JSON.stringify(v));
    } catch {
      /* storage may be unavailable */
    }
  };
  return [value, set];
}

export function formatAge(iso: string, now: Date): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const s = Math.max(0, Math.round((now.getTime() - then) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function formatTime(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatStamp(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
