import type { Contract, Incident, Safety, Tally, TurnResponse } from "./types";

export interface ReplayBundle {
  recorded_at: string;
  incidents: Incident[];
  turns: Record<string, TurnResponse[]>;
  contracts: Contract[];
  tally: Tally;
  safety: Safety;
}

export async function loadReplay(): Promise<ReplayBundle> {
  const resp = await fetch("/replay/incident-001.json", { cache: "no-store" });
  if (!resp.ok) throw new Error("replay bundle missing");
  return (await resp.json()) as ReplayBundle;
}
