export interface TimelineEvent {
  t: string;
  event: string;
  detail?: Record<string, unknown>;
}

export interface Evidence {
  id: string;
  kind: string;
  title: string;
  payload: unknown;
}

export interface ToolEvent {
  name: string;
  args: Record<string, unknown>;
  summary: string;
  evidence_id?: string | null;
}

export interface Proposal {
  fix_id: number;
  action: string;
  params: Record<string, unknown>;
  blast_radius: string;
  dry_run: { ok: boolean; code?: string; detail?: string; role?: string };
  expires_at: string;
}

export interface Incident {
  incident_id: string;
  alarm_name?: string;
  status: string;
  timestamp: string;
  woken?: boolean;
  handled_by?: string;
  resolved_at?: string;
  executed_at?: string;
  execution_arn?: string;
  rca_json?: {
    status?: string;
    summary?: string;
    spoken_summary?: string;
    change_correlation?: string | null;
    evidence?: string[];
    next_steps?: string[];
    beacon_json?: { suggested_action?: string | null; action_params?: Record<string, unknown> | null; action_source?: string };
  };
  diagnostics?: { missing_rules?: Array<Record<string, unknown>>; text?: string };
  changes?: Array<Record<string, unknown>>;
  timeline?: TimelineEvent[];
  proposals?: Proposal[];
  contract_id?: string;
  contract_readback_pending?: Record<string, unknown> | null;
  turn_count?: number;
}

export interface Contract {
  contract_id: string;
  alarm_name: string;
  action: string;
  scope: Record<string, unknown>;
  uses: number;
  max_uses: number;
  granted_at: string;
  expires_at: string;
  transcript_quote: string;
  granted_by: string;
  incident_id: string;
}

export interface Tally {
  incidents_handled: number;
  resolved: number;
  escalated: number;
  humans_woken: number;
  handled_by_contract: number;
  median_minutes_to_recovery: number | null;
  cost_inr_total?: number;
  cost_inr_per_incident?: number;
  night_incidents_not_woken?: number;
  sleep_protected_hours?: number;
}

export interface MetricSeries {
  alarm_name: string;
  metric: { namespace: string; metric_name: string };
  points: Array<{ t: string; v: number }>;
  executed_at?: string | null;
  resolved_at?: string | null;
}

export interface TurnResponse {
  reply_text: string;
  spoken_text: string;
  cited: string[];
  audio_b64: string | null;
  speech_marks: Array<{ time: number; value: string }>;
  voice: string | null;
  tts_error: string | null;
  tool_events: ToolEvent[];
  evidence: Evidence[];
  incident: Incident;
  turn: number;
}

export interface Safety {
  allowlist: Array<{ id: string; description: string; params: Record<string, string>; iam_actions: string[] }>;
  apply_enabled: Record<string, boolean | null>;
  rules: string[];
}

export interface Message {
  role: "user" | "beacon";
  text: string;
  cited?: string[];
  toolEvents?: ToolEvent[];
  speechMarks?: Array<{ time: number; value: string }>;
  channel?: string;
  at: string;
}
