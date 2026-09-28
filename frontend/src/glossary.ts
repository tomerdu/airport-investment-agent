/**
 * Plain-language definitions surfaced as tooltips and inline help.
 *
 * Wording here is deliberately calibrated: each entry says what the measure
 * IS and what it is NOT, so a reviewer cannot read a proxy as a measurement.
 */

export interface GlossaryEntry {
  term: string;
  short: string;
  isNot: string;
}

export const GLOSSARY: Record<string, GlossaryEntry> = {
  TDPI: {
    term: 'Terminal Demand Pressure Index',
    short:
      'A 0–100 composite proxy built from load factor, passenger growth, aircraft gauge, throughput per runway and enplanement growth, scored against a peer cohort.',
    isNot:
      'Not a measurement of terminal capacity, gates, holdroom space or checkpoint queuing — the datasets used here do not contain those. A high score means an airport is worth a closer look, not that it needs a terminal.',
  },
  ACI: {
    term: 'Airside Congestion Index',
    short:
      'A 0–100 composite proxy built from observed delay outcomes: average taxi-out time, NAS delay per flight, the share of departures delayed over 15 minutes, and cancellation rate.',
    isNot:
      'Does not measure runway or airspace capacity, and does not establish that any airside constraint is binding. High ACI means delay and queuing are elevated relative to peers; the cause is not identified.',
  },
  UDEI: {
    term: 'Unmet Demand Evidence',
    short:
      'A checklist of observable indicators consistent with constrained supply: high load factor, frequency suppression, upgauging, an airside throughput ceiling and fare premium.',
    isNot:
      'Does not quantify unmet demand. It cannot be quantified from the datasets this system uses — passengers who did not book and flights airlines did not schedule leave no trace in the BTS and FAA sources behind these tools.',
  },
  TERMINAL_LED: {
    term: 'Terminal-led',
    short:
      'Demand pressure is elevated (TDPI ≥ 60) while the airside-congestion proxy is not (ACI < 40).',
    isNot:
      'A screening classification and a prompt to investigate — not evidence that terminal capacity is short, not an infrastructure diagnosis, and not an investment recommendation.',
  },
  SYSTEMIC: {
    term: 'Systemic',
    short: 'Both proxy indices are elevated (TDPI ≥ 60 and ACI ≥ 60).',
    isNot:
      'A screening classification only. It does not identify a cause or predict what any investment would achieve.',
  },
  AIRSIDE_LED: {
    term: 'Airside-led',
    short:
      'The airside-congestion proxy is elevated (ACI ≥ 60) while demand pressure is not (TDPI < 40).',
    isNot:
      'Delay and queuing are high relative to peers. This system does not identify the cause or establish that an airside constraint is binding.',
  },
  NO_NEAR_TERM_CASE: {
    term: 'No near-term case',
    short: 'Neither index is elevated versus the peer cohort (both < 40).',
    isNot:
      'Says nothing about the airport in absolute terms — only that it does not stand out against its peers in this window.',
  },
  MIXED: {
    term: 'Mixed',
    short: 'One or both indices fall in the middle band (40–60).',
    isNot:
      'Not an "average risk" rating. Read the two scores directly rather than relying on the label.',
  },
  UNCLASSIFIED_AIRSIDE_UNKNOWN: {
    term: 'Airside unmeasured',
    short:
      'Airside congestion could not be scored — usually fewer than 1,000 on-time-reported flights in the window.',
    isNot:
      'NOT a low congestion score. Absence of a measurement is not evidence that congestion is absent, so no class is assigned.',
  },
  UNCLASSIFIED: {
    term: 'Unclassified',
    short:
      'Terminal demand pressure could not be computed with enough component coverage (below the 60% floor).',
    isNot: 'Unknown, not low.',
  },
  NORMALISED: {
    term: 'Normalised value',
    short:
      'The raw metric winsorized to the cohort’s 5th–95th percentile, then linearly rescaled onto 0–100.',
    isNot:
      'NOT a percentile rank. A normalised 94 means "94% of the way from the cohort’s 5th to its 95th percentile", not "higher than 94% of peers". The separate percentile column is the rank.',
  },
  PERCENTILE: {
    term: 'Percentile',
    short:
      'The share of cohort airports at or below this value. Shown for interpretation only.',
    isNot: 'Not used in the score arithmetic — the normalised value is.',
  },
  COVERAGE: {
    term: 'Coverage',
    short:
      'The share of the index’s declared weight that had data. Missing components are dropped and the remaining weights renormalised to sum to 1.0.',
    isNot:
      'Below 60% the score is suppressed entirely rather than reported on partial evidence.',
  },
  COMBI: {
    term: 'Combi aircraft',
    short:
      'T-100 AIRCRAFT_CONFIG 3 — passengers and freight carried on the same main deck behind a moveable bulkhead.',
    isNot:
      'Neither exclusively passenger nor all-cargo, so it appears in neither of those columns. This is why those two do not sum to the total.',
  },
};

/** Tooltip text: what it is, then what it is not. */
export function tip(key: string): string {
  const g = GLOSSARY[key];
  if (!g) return '';
  return `${g.term}\n\n${g.short}\n\n⚠ ${g.isNot}`;
}
