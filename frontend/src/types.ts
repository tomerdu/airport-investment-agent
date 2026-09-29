/**
 * Types mirroring the FastAPI backend contract.
 *
 * Everything rendered as a number, table or chart comes from these structured
 * fields — never from parsing the model's prose. The prose is narration; the
 * data is the product.
 */

export interface SourceRecord {
  dataset: string;
  source_name: string;
  source_url: string;
  license: string | null;
  coverage: string;
  retrieved_at: string;
  rows: number | null;
  notes: string | null;
}

export interface ComponentResult {
  id: string;
  label: string;
  raw: number | null;
  raw_display: string | null;
  normalized: number | null;
  percentile: number | null;
  weight: number;
  effective_weight: number | null;
  contribution: number | null;
  source: string;
  available: boolean;
  note: string | null;
}

export interface IndexResult {
  index: string;
  label: string;
  score: number | null;
  coverage: number;
  suppressed_reason: string | null;
  components: ComponentResult[];
  cohort_size: number;
  notes: string[];
}

export type DivergenceClass =
  | 'TERMINAL_LED'
  | 'SYSTEMIC'
  | 'AIRSIDE_LED'
  | 'NO_NEAR_TERM_CASE'
  | 'MIXED'
  | 'UNCLASSIFIED_AIRSIDE_UNKNOWN'
  | 'UNCLASSIFIED';

/** How an existing ACI score is distributed across the window.
 *
 * Supplementary evidence only: never a component of ACI or TDPI, and never an
 * input to a ranking or a divergence class. `temporal_pattern` is a separate
 * field from `divergence_class` — its INTERMITTENT value is deliberately not
 * named MIXED so the two can never be confused.
 */
export type TemporalPattern =
  | 'PERSISTENT'
  | 'EPISODIC'
  | 'INTERMITTENT'
  | 'INSUFFICIENT_DATA';

export interface TemporalMonth {
  month: string;
  flights: number;
  aci: number | null;
  evaluated: boolean;
  elevated: boolean;
  reason: string | null;
}

export interface TemporalConcentration {
  /** Points the annual ACI loses when its two worst months are removed.
   *  null when it could not be measured — see `unreliable_reason`. */
  worst_two_month_drop: number | null;
  aci_excluding_worst_two: number | null;
  reliable: boolean;
  /** 'score_at_cohort_ceiling' | 'insufficient_evaluable_months' | null */
  unreliable_reason: string | null;
  /** Control: points lost removing two MIDDLE months. Near zero validates the
   *  measure as behaviour rather than arithmetic. */
  null_baseline_drop: number | null;
}

export interface TemporalDiagnostic {
  months_available: number;
  months_expected: number;
  months_evaluated: number;
  months_unevaluated: number;
  coverage_complete: boolean;
  temporal_pattern: TemporalPattern;
  pattern_label: string;
  description: string;
  concentration: TemporalConcentration;
  monthly_spread: number | null;
  elevated_months: number;
  /** True when NO evaluated month crossed the elevated threshold. Guards
   *  against reading INTERMITTENT as intermittent congestion. */
  no_elevated_months: boolean;
  elevated_share: number | null;
  elevated_threshold: number;
  elevated_season: string | null;
  months: TemporalMonth[];
  uncertainty: string[];
  notes: string[];
}

/** Row-level subset carried on comparison rows. */
export interface TemporalSummary {
  temporal_pattern: TemporalPattern;
  pattern_label: string;
  months_available: number;
  months_expected: number;
  months_evaluated: number;
  coverage_complete: boolean;
  worst_two_month_drop: number | null;
  concentration_reliable: boolean;
  concentration_unreliable_reason: string | null;
  monthly_spread: number | null;
  elevated_months: number;
  no_elevated_months: boolean;
  elevated_season: string | null;
  uncertainty: string[];
}

export interface AirportScores {
  iata: string;
  name: string;
  state: string | null;
  hub_class: string | null;
  window: string;
  cohort: string;
  tdpi: IndexResult;
  aci: IndexResult;
  divergence_class: DivergenceClass;
  divergence_reading: string;
  sources: SourceRecord[];
  limitations: string[];
  /** Optional and additive: absent on older payloads. */
  temporal?: TemporalDiagnostic | null;
}

export interface RankedRow extends AirportScores {
  rank: number | null;
  unscored_reason?: string | null;
  scale: {
    passengers: number | null;
    departures: number | null;
    enplanements_cy: number | null;
    otp_flights: number | null;
    seats_per_departure: number | null;
    hub_class: string | null;
  };
}

export interface RankResult {
  index: string;
  window: string;
  cohort: string;
  cohort_size: number;
  ranked: RankedRow[];
  unscored: RankedRow[];
  skipped: { iata: string; reason: string }[];
  sources: SourceRecord[];
  limitations: string[];
}

export interface CompareAirport {
  iata: string;
  name: string;
  state: string | null;
  hub_class: string | null;
  runway_count: number | null;
  longest_runway_ft: number | null;
  volume: {
    departures: number | null;
    passengers: number | null;
    seats: number | null;
    otp_flights: number | null;
  };
  intensity: {
    load_factor: number | null;
    seats_per_departure: number | null;
    taxi_out_avg_min: number | null;
    nas_delay_per_flight_min: number | null;
    dep_del15_rate: number | null;
    cancel_rate: number | null;
    dep_delay_avg_min: number | null;
  };
  scores: AirportScores | null;
  temporal?: TemporalSummary | null;
}

export interface CompareResult {
  window: string;
  airports: CompareAirport[];
  note: string;
  sources: SourceRecord[];
  limitations: string[];
}

export interface LongHaulBand {
  threshold_sm: number;
  departures: number;
  share_pct: number;
}

export type LongHaulScopeId =
  | 'all_carriers'
  | 'passenger'
  | 'cargo'
  | 'combi'
  | 'amphibious';

export interface LongHaulReconciliation {
  total_departures: number;
  sum_of_exclusive_scopes: number;
  residual: number;
  reconciles: boolean;
  breakdown: Record<
    string,
    { departures: number; share_pct: number; label: string }
  >;
  note: string;
}

export interface LongHaulScope {
  scope: LongHaulScopeId;
  scope_label: string;
  total_departures: number;
  total_passengers: number;
  bands: LongHaulBand[];
  headline_threshold_sm: number;
  headline_share_pct: number | null;
}

export interface LongHaulResult {
  iata: string;
  name: string;
  period: string;
  period_months: number;
  is_full_window: boolean;
  definition: string;
  unit: string;
  scopes: LongHaulScope[];
  reconciliation: LongHaulReconciliation;
  top_destinations: {
    dest: string;
    departures: number;
    distance_sm: number | null;
    passengers: number;
    is_long_haul: boolean;
  }[];
  sources: SourceRecord[];
  limitations: string[];
}

export interface Indicator {
  id: string;
  label: string;
  value: number | null;
  value_display: string | null;
  threshold_display: string;
  triggered: boolean | null;
  available: boolean;
  /** What a trigger is CONSISTENT WITH — never a cause. */
  direction: string;
  source: string;
  unavailable_reason: string | null;
  /** What this indicator cannot establish even when it fires. */
  cannot_establish?: string;
  /** How the threshold is built and where it is not comparable. */
  threshold_note?: string;
  /** Indicators measured on the same underlying quantities as this one. */
  shares_arithmetic_with?: string[];
}

export type EvidenceBand = 'Weak' | 'Moderate' | 'Strong' | 'Indeterminate';

/** Cohort frequencies, for calibrating what a band means.
 *  Never evidence about a particular airport. */
export interface UdeiCohortContext {
  cohort_size: number;
  band_counts: Record<string, number>;
  indicator_available_counts?: Record<string, number>;
  indicator_triggered_counts?: Record<string, number>;
  airports_by_attainable_maximum: Record<string, number>;
  note: string;
}

export interface UnmetDemandResult {
  iata: string;
  name: string;
  window: string;
  indicators: Indicator[];
  triggered_count: number;
  available_count: number;
  total_count: number;
  evidence_band: EvidenceBand;
  caveat: string;
  sources: SourceRecord[];
  limitations: string[];
  /** Phase 8.3b transparency fields. Optional and additive. */
  unavailable_count?: number;
  unavailable_reasons?: { id: string; label: string; reason: string }[];
  /** Highest trigger count reachable on this airport's data coverage. */
  max_attainable_triggered?: number;
  max_attainable_band?: string;
  band_definition?: string;
  band_comparability_note?: string;
  indicator_relationships?: string[];
  cohort_context?: UdeiCohortContext | null;
  /** Set only on a Weak or Indeterminate band: absence of evidence is not
   *  evidence of absence. */
  weak_is_not_absence?: string;
  /** One-line forms of the limitations, used by the compact model view. The
   *  panel renders the full paragraphs above instead. */
  brief_limits?: Record<string, string>;
}

export interface AirportProfileResult {
  window: string;
  airport: {
    iata: string;
    name: string;
    city: string | null;
    state: string | null;
    hub_class: string | null;
    runway_count: number | null;
    longest_runway_ft: number | null;
  };
  traffic: Record<string, number | null>;
  delay: Record<string, number | null>;
  scores: AirportScores;
  sources: SourceRecord[];
  limitations: string[];
}

export interface ToolCall {
  name: string;
  input: Record<string, unknown>;
  ok: boolean;
  error: string | null;
  duration_ms: number;
  result: unknown;
}

export interface AgentReply {
  answer: string;
  session_id: string;
  window: string;
  tool_calls: ToolCall[];
  sources: SourceRecord[];
  limitations: string[];
  assumptions: string[];
  focus_airports: string[];
  scores: AirportScores[];
  audit: {
    passed: boolean;
    numerals_checked: number;
    unmatched: string[];
    summary: string;
  };
  usage: { input_tokens?: number; output_tokens?: number };
  stop_reason: string | null;
  degraded: boolean;
}

export interface HealthResponse {
  status: string;
  window: string;
  airports: number;
  cohort_size: number;
  aci_eligible: number;
  llm_configured: boolean;
  model: string;
  active_sessions: number;
}

export interface ChatTurn {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  reply?: AgentReply;
  error?: string;
}
