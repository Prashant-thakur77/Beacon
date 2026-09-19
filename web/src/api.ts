import { trimSlash, type Config } from "./config";
import type { Contract, Incident, MetricSeries, Safety, Tally, TurnResponse } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Reads are short; a voice turn can legitimately take most of the Lambda's 45 s. */
const READ_TIMEOUT_MS = 10_000;
const TURN_TIMEOUT_MS = 50_000;

async function request<T>(url: string, init: RequestInit = {}, passcode?: string, timeoutMs = READ_TIMEOUT_MS): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json", ...(init.headers as Record<string, string>) };
  if (passcode) headers["x-beacon-passcode"] = passcode;
  const ctl = new AbortController();
  const timer = window.setTimeout(() => ctl.abort(), timeoutMs);
  let resp: Response;
  try {
    resp = await fetch(url, { ...init, headers, signal: ctl.signal });
  } catch (e) {
    if (ctl.signal.aborted) throw new ApiError(0, `timed out after ${Math.round(timeoutMs / 1000)}s`);
    throw new ApiError(0, e instanceof Error ? e.message : String(e));
  } finally {
    window.clearTimeout(timer);
  }
  const text = await resp.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { error: text };
  }
  if (!resp.ok) {
    const msg = (body as { error?: string } | null)?.error || `${resp.status} ${resp.statusText}`;
    throw new ApiError(resp.status, msg);
  }
  return body as T;
}

export function makeApi(config: Config, passcode: () => string) {
  const dash = trimSlash(config.dashboardUrl);
  const voice = trimSlash(config.voiceUrl);
  const local = dash.replace(/\/dash$/, "/local");
  return {
    /** `make local` only: cut the demo's database rule again and run triage. */
    localBreak: () => request<{ ok: boolean }>(`${local}/break`, { method: "POST", body: "{}" }, passcode()),
    incidents: () => request<{ incidents: Incident[] }>(`${dash}/incidents`),
    incident: (id: string) => request<{ incident: Incident }>(`${dash}/incidents/${id}`),
    execution: (id: string) => request<{ execution_arn: string; status: string | null; events: Array<{ t: string; type: string; state: string | null }> }>(`${dash}/incidents/${id}/execution`),
    tally: () => request<Tally>(`${dash}/tally`),
    metric: (id: string) => request<MetricSeries>(`${dash}/incidents/${id}/metric`),
    contracts: () => request<{ contracts: Contract[] }>(`${dash}/contracts`),
    revoke: (id: string) => request<{ ok: boolean }>(`${dash}/contracts/${id}`, { method: "DELETE" }, passcode()),
    safety: () => request<Safety>(`${dash}/safety`),
    session: () =>
      request<{ credentials: { accessKeyId: string; secretAccessKey: string; sessionToken: string; expiration: string }; region: string; sttLanguage: string }>(
        `${voice}/session`,
        { method: "POST", body: "{}" },
        passcode(),
      ),
    turn: (body: { incident_id: string; session_id: string; text?: string; channel?: string; mode?: "chat" | "brief" | "event"; event?: string; lang?: string }) =>
      request<TurnResponse>(`${voice}/turn`, { method: "POST", body: JSON.stringify(body) }, passcode(), TURN_TIMEOUT_MS),
  };
}

export type Api = ReturnType<typeof makeApi>;
