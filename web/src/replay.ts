import type { AuditRow, Contract, Incident, MorningReport, Safety, Tally, TurnResponse } from "./types";

export interface ReplayBundle {
  recorded_at: string;
  incidents: Incident[];
  turns: Record<string, TurnResponse[]>;
  contracts: Contract[];
  tally: Tally;
  safety: Safety;
  /** Server-rendered at export time (scripts/export_night.py); older bundles lack these. */
  audit?: AuditRow[];
  report?: MorningReport;
  postmortems?: Record<string, string>;
}

export async function loadReplay(): Promise<ReplayBundle> {
  const resp = await fetch("/replay/incident-001.json", { cache: "no-store" });
  if (!resp.ok) throw new Error("replay bundle missing");
  return (await resp.json()) as ReplayBundle;
}
