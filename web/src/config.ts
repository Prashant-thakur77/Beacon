/** Runtime config served at /config.json (CachingDisabled on CloudFront), so URLs never need a rebuild. */
export interface Config {
  voiceUrl: string;
  dashboardUrl: string;
  region: string;
  sttLanguage: string;
  voiceBackend: "aws" | "assemblyai";
  archivedIncidentId: string;
  replay: boolean;
  /** `make local`: the whole product against moto in one process. */
  local: boolean;
  localPasscode: string;
}

const DEFAULTS: Config = {
  voiceUrl: "",
  dashboardUrl: "",
  region: "us-east-1",
  sttLanguage: "en-IN",
  voiceBackend: "aws",
  archivedIncidentId: "",
  replay: false,
  local: false,
  localPasscode: "",
};

let cached: Config | null = null;

export async function loadConfig(): Promise<Config> {
  if (cached) return cached;
  const params = new URLSearchParams(window.location.search);
  let fetched: Partial<Config> = {};
  try {
    const resp = await fetch("/config.json", { cache: "no-store" });
    if (resp.ok) fetched = await resp.json();
  } catch {
    /* no config: local dev or replay */
  }
  const env = import.meta.env as Record<string, string | undefined>;
  const lang = params.get("lang");
  cached = {
    ...DEFAULTS,
    ...fetched,
    ...(lang ? { sttLanguage: lang } : {}),
    voiceUrl: fetched.voiceUrl || env.VITE_VOICE_URL || "",
    dashboardUrl: fetched.dashboardUrl || env.VITE_DASHBOARD_URL || "",
    replay: params.get("replay") === "1" || env.VITE_REPLAY === "1",
  };
  return cached;
}

export function trimSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}
