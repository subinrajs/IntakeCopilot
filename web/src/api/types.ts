/** Response shapes of the IntakeCopilot API (backend/intake/cases.py, eval/runner.py). */

export type Role = "intake" | "radiologist" | "admin";
export type Priority = "P1" | "P2" | "P3" | "P4";
export type Confidence = "high" | "medium" | "low";
export type CaseStatus =
  | "uploaded"
  | "processing"
  | "manual_entry"
  | "ready_for_review"
  | "in_review"
  | "approved"
  | "rejected"
  | "exported";

export interface User {
  id: string;
  display_name: string;
  role: Role;
}

export interface RedFlagHit {
  level: "P1" | "P2";
  phrase: string;
  context: string;
}

export interface QueueItem {
  id: string;
  status: CaseStatus;
  patient_name: string | null;
  modality: string | null;
  body_part: string | null;
  priority: Priority | null;
  model_priority: Priority | null;
  raised_by_rules: boolean;
  red_flags: RedFlagHit[];
  pinned: boolean;
  protocol_id: string | null;
  contrast_result: "clear" | "needs_labs" | "needs_review" | null;
  uploaded_at: string;
  status_changed_at: string;
  waiting_seconds: number;
  target_seconds: number | null;
  sla_breached: boolean;
  locked_by: string | null;
  original_filename: string | null;
}

export interface Evidence {
  quote: string;
  page: number;
}

export interface Extracted<T> {
  value: T | null;
  confidence: Confidence;
  evidence: Evidence | null;
}

export interface EvidenceCheck {
  found: boolean;
  score: number;
  page: number;
  boxes: [number, number, number, number][];
}

export type Extraction = Record<string, Extracted<unknown> | Extracted<unknown>[]>;

export interface CaseFields {
  patient_name: string | null;
  dob: string | null;
  health_card_last4: string | null;
  referrer_name: string | null;
  referrer_billing_number: string | null;
  modality: "MRI" | "CT" | null;
  body_part: string | null;
  laterality: "left" | "right" | "bilateral" | "none" | null;
  contrast_requested: boolean | null;
  clinical_indication: string | null;
  relevant_history: string[];
  allergies: string[];
  egfr: number | null;
  egfr_date: string | null;
  medications_of_note: string[];
  physician_marked_urgent: boolean | null;
}

export interface StepSummary<O> {
  id: number;
  valid: boolean;
  error: string | null;
  prompt_version: string;
  model: string | null;
  output: O;
  latency_ms: number | null;
  cost_usd: number | null;
  created_at: string;
}

export interface TriageOutput {
  model: {
    priority: Priority;
    red_flags: string[];
    rationale: string;
    evidence: Evidence[];
    confidence: Confidence;
  } | null;
  rules: { rules_version: number; floor: "P1" | "P2" | null; hits: RedFlagHit[] };
  final_priority: Priority | null;
  raised_by_rules: boolean;
  evidence_checks: { quote: string; found: boolean }[];
}

export interface Candidate {
  id: string;
  name: string;
  contrast: string;
  score: number;
}

export interface ProtocolOutput {
  candidates: Candidate[];
  choice: {
    protocol_id: string;
    contrast: string;
    rationale: string;
    confidence: Confidence;
  } | null;
  show_all_candidates?: boolean;
}

export interface FiredRule {
  id: string;
  result: "needs_labs" | "needs_review";
  message: string;
}

export interface ContrastOutput {
  rules_version: number;
  result: "clear" | "needs_labs" | "needs_review";
  effective_contrast: string;
  fired: FiredRule[];
  protocol_id: string | null;
}

export interface ProtocolRow {
  id: string;
  modality: "MRI" | "CT";
  body_part: string;
  name: string;
  contrast: "none" | "iv" | "optional";
  indications: string[] | string;
  slot_minutes?: number;
  version?: number;
}

export interface Decision {
  id: number;
  kind: "priority" | "protocol" | "contrast" | "case";
  ai_value: string | null;
  final_value: string | null;
  action: "accept" | "override" | "reject" | "acknowledge";
  reason: string | null;
  decided_by: string;
  decided_at: string;
}

export interface CaseDetail {
  id: string;
  status: CaseStatus;
  uploaded_by: string;
  uploaded_at: string;
  status_changed_at: string;
  original_filename: string | null;
  content_type: string;
  locked_by: string | null;
  pages: { page_no: number; width: number; height: number; text_source: string }[];
  fields: CaseFields;
  extraction: { extraction: Extraction | null; evidence: Record<string, EvidenceCheck> } | null;
  extraction_error: string | null;
  corrections: { field: string; ai_value: unknown; value: unknown; by: string; at: string }[];
  triage: StepSummary<TriageOutput> | null;
  protocol: StepSummary<ProtocolOutput> | null;
  protocol_candidates: Record<string, ProtocolRow>;
  contrast: StepSummary<ContrastOutput> | null;
  decisions: Decision[];
  history: { at: string; actor: string; action: string; detail: Record<string, unknown> | null }[];
}

export interface Rate {
  value: number | null;
  n: number;
  total: number;
  ci95: [number, number];
}

export interface MetricGroup {
  cases: number;
  field_accuracy: Rate;
  evidence_validity: Rate;
  priority_agreement: Rate;
  model_priority_agreement: Rate;
  under_triage: Rate;
  model_under_triage: Rate;
  over_triage: Rate;
  severe_under_triage: number;
  retrieval_recall_at_5: Rate;
  protocol_top1: Rate;
  contrast_flag_accuracy: Rate;
  manual_entry: number;
  cost_usd_per_case: number | null;
  cost_usd_total: number;
  latency_ms_p50: number | null;
  latency_ms_max: number | null;
}

export type Confusion = Record<Priority, Record<Priority | "none", number>>;

export interface EvalMetrics {
  all: MetricGroup;
  clean: MetricGroup;
  hard: MetricGroup;
  confusion: Confusion;
  model_confusion: Confusion;
  field_errors: Record<string, number>;
  nondeterminism?: { repeat_run_id: string; cases: { case_key: string; differs: string[] }[] };
  simulation: boolean;
  baseline_run_id: string | null;
  release_gate: { passed: boolean; checks: { check: string; ok: boolean; value: unknown }[] };
}

export interface PerCase {
  case_key: string;
  requisition_id: string;
  difficulty: "clean" | "hard";
  status: string;
  gold_priority: Priority;
  priority: Priority | null;
  model_priority: Priority | null;
  raised_by_rules: boolean;
  gold_protocol_id: string;
  protocol_id: string | null;
  recall_at_5: boolean;
  gold_contrast_flags: string[];
  contrast_flags: string[];
  contrast_ok: boolean;
  fields_correct: number;
  fields_total: number;
  field_errors: string[];
  cost_usd: number;
  latency_ms: number | null;
  error?: string;
}

export interface EvalRun {
  id: string;
  gold_version: string;
  prompt_versions: Record<string, string>;
  models: Record<string, string>;
  label: string | null;
  started_by: string;
  started_at: string;
  finished_at: string | null;
  status: "running" | "done" | "failed";
  error: string | null;
  metrics: EvalMetrics | null;
  per_case?: PerCase[];
}
