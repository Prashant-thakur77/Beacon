import { trimSlash, type Config } from "./config";
import type { Contract, Incident, Safety, Tally, TurnResponse } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(url: string, init: RequestInit = {}, passcode?: string): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json", ...(init.headers as Record<string, string>) };
  if (passcode) headers["x-beacon-passcode"] = passcode;
  const resp = await fetch(url, { ...init, headers });
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
  return {
    incidents: () => request<{ incidents: Incident[] }>(`${dash}/incidents`),
    incident: (id: string) => request<{ incident: Incident }>(`${dash}/incidents/${id}`),
    execution: (id: string) => request<{ execution_arn: string; status: string | null; events: Array<{ t: string; type: string; state: string | null }> }>(`${dash}/incidents/${id}/execution`),
    tally: () => request<Tally>(`${dash}/tally`),
    contracts: () => request<{ contracts: Contract[] }>(`${dash}/contracts`),
    revoke: (id: string) => request<{ ok: boolean }>(`${dash}/contracts/${id}`, { method: "DELETE" }, passcode()),
    safety: () => request<Safety>(`${dash}/safety`),
    session: () =>
      request<{ credentials: { accessKeyId: string; secretAccessKey: string; sessionToken: string; expiration: string }; region: string; sttLanguage: string }>(
        `${voice}/session`,
        { method: "POST", body: "{}" },
        passcode(),
      ),
    turn: (body: { incident_id: string; session_id: string; text?: string; channel?: string; mode?: "chat" | "brief" | "event"; event?: string }) =>
      request<TurnResponse>(`${voice}/turn`, { method: "POST", body: JSON.stringify(body) }, passcode()),
  };
}

export type Api = ReturnType<typeof makeApi>;
