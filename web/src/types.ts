/** Timeline event names the console knows how to label; others render as-is. */
export type KnownEvent =
  | "alarm_received" | "triggered" | "logs_fetched" | "reduced" | "diagnostics_ran" | "diagnostics_skipped" | "changes_checked" | "rca_ready" | "sns_sent"
  | "fix_proposed" | "approved" | "contract_matched" | "contract_matched_apply_disabled" | "remediation_started" | "executing" | "executed" | "execute_failed"
  | "verify_attempt" | "resolved" | "escalated" | "contract_granted" | "contract_exhausted" | "contract_ignored" | "undone" | "undo_failed"
  | "proposal_withdrawn" | "acknowledged" | "pr_opened" | "model_unavailable";

export interface TimelineEvent {
  t: string;
  event: KnownEvent | (string & {});
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
  usage?: { input_tokens?: number; output_tokens?: number; embedding_tokens?: number };
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
  allowlist: Array<{ id: string; description: string; params: Record<string, string>; iam_actions: string[]; inverse?: string | null; undo_of?: string | null }>;
  apply_enabled: Record<string, boolean | null>;
  rules: string[];
  controls?: Array<{ id: string; title: string; rule: string; file: string; test: string }>;
}

export interface Message {
  role: "user" | "beacon";
  text: string;
  cited?: string[];
  toolEvents?: ToolEvent[];
  speechMarks?: Array<{ time: number; value: string }>;
  channel?: string;
  /** The engineer spoke over this reply (AssemblyAI reply.done{interrupted}). */
  interrupted?: boolean;
  /** A console note (proposal withdrawn, reconnect), not something the agent said. */
  note?: boolean;
  at: string;
}

export interface Health {
  ok: boolean;
  service: string;
  version?: string;
  region?: string;
  stack?: string;
}

export interface AnalyticsNight {
  night: string;
  incidents: number;
  resolved: number;
  escalated: number;
  woken: number;
  under_contract: number;
  cost_inr: number;
  median_minutes_to_recovery: number | null;
  p90_minutes_to_recovery: number | null;
}

export interface AnalyticsIncident {
  incident_id: string;
  alarm_name?: string | null;
  timestamp?: string;
  night: string;
  status: string;
  woken: boolean;
  under_contract: boolean;
  minutes_to_recovery: number | null;
  seconds_to_first_proposal: number | null;
  cost_inr: number;
  cost_inr_cumulative: number;
}

export interface AnalyticsContract {
  contract_id: string;
  alarm_name?: string | null;
  action?: string | null;
  uses: number;
  max_uses: number;
  uses_left: number;
  expires_at?: string | null;
  hours_left: number | null;
}

export interface Analytics {
  generated_at: string;
  nights: AnalyticsNight[];
  incidents: AnalyticsIncident[];
  recovery: { count: number; p50_minutes: number | null; p90_minutes: number | null; max_minutes: number | null };
  first_proposal: { count: number; mean_seconds: number | null };
  outcomes: { resolved: number; escalated: number; in_progress: number };
  humans: { woken: number; under_contract: number };
  cost: { total_inr: number; per_incident_inr: number };
  top_alarms: Array<{ alarm_name: string; count: number }>;
  contracts: AnalyticsContract[];
}

export interface AuditRow {
  at?: string | null;
  kind: "approval" | "contract";
  id?: string | null;
  incident_id?: string | null;
  alarm_name?: string | null;
  action?: string | null;
  quote?: string;
  channel?: string | null;
  source?: string | null;
  executed?: boolean;
  executed_at?: string | null;
  result?: string | null;
  contract_id?: string | null;
  status?: string | null;
  uses?: string | null;
  expires_at?: string | null;
  /** Where the words came from: STT confidence, session id, voice-note file id. */
  attestation?: { stt?: string; confidence?: number; session_id?: string; telegram_file_id?: string; language?: string };
}

export interface MorningReport {
  night_of: string;
  generated_at: string;
  incidents: number;
  resolved: number;
  escalated: number;
  humans_woken: number;
  handled_by_contract: number;
  median_minutes_to_recovery: number | null;
  cost_inr: number;
  contracts_used: Array<{ contract_id?: string | null; alarm_name?: string | null; uses?: string | null }>;
  incident_ids: string[];
  subject: string;
  text: string;
}
