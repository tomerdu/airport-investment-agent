/**
 * Shared display primitives.
 *
 * All formatting lives here so a null is rendered as an explicit "not
 * measured" rather than silently becoming 0 or an empty cell.
 */
import { GLOSSARY, tip } from '../glossary';
import type { DivergenceClass } from '../types';

/** A term with a dotted underline and an explanatory tooltip. */
export function Term({
  k,
  children,
}: {
  k: string;
  children?: React.ReactNode;
}) {
  const g = GLOSSARY[k];
  if (!g) return <>{children}</>;
  return (
    <abbr className="term" title={tip(k)}>
      {children ?? k}
    </abbr>
  );
}

export function fmtNum(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return v.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtScore(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return v.toFixed(1);
}

export function fmtMin(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${v.toFixed(digits)} min`;
}

export function Card({
  title,
  meta,
  accent,
  children,
}: {
  title: string;
  meta?: string;
  accent?: 'tdpi' | 'aci' | 'cargo';
  children: React.ReactNode;
}) {
  return (
    <section className="card">
      <header className="card-head">
        {accent && (
          <span
            className="swatch"
            style={{ background: `var(--${accent})` }}
            aria-hidden="true"
          />
        )}
        <h3>{title}</h3>
        {meta && <span className="meta">{meta}</span>}
      </header>
      <div className="card-body">{children}</div>
    </section>
  );
}

/** Human-readable reason a score is absent. Never rendered as a low score. */
export const SUPPRESSION_TEXT: Record<string, string> = {
  insufficient_flight_volume:
    'Not scored — fewer than 1,000 on-time-reported flights in the window. Delay rates on small samples are noise. This is not evidence that congestion is absent.',
  insufficient_coverage:
    'Not scored — too few components had data (below the 60% coverage floor) to produce a defensible score.',
  no_data: 'Not scored — no input data available for this airport in the window.',
  degenerate_cohort: 'Not scored — the peer cohort had no usable spread on these metrics.',
};

export const CLASS_META: Record<
  DivergenceClass,
  { label: string; color: string; blurb: string }
> = {
  TERMINAL_LED: {
    label: 'Terminal-led',
    color: 'var(--tdpi)',
    blurb:
      'Demand pressure elevated, airside-congestion proxy not. A screening classification and a prompt to investigate — not proof that terminal capacity is short, and not a recommendation.',
  },
  SYSTEMIC: {
    label: 'Systemic',
    color: 'var(--critical)',
    blurb:
      'Both proxy indices are elevated relative to peers. A screening classification only; no cause is identified.',
  },
  AIRSIDE_LED: {
    label: 'Airside-led',
    color: 'var(--aci)',
    blurb:
      'The airside-congestion proxy is elevated while demand pressure is not. Delay and queuing are high relative to peers; this system does not identify the cause.',
  },
  NO_NEAR_TERM_CASE: {
    label: 'No near-term case',
    color: 'var(--text-muted)',
    blurb: 'Neither demand pressure nor airside congestion is elevated versus peers.',
  },
  MIXED: {
    label: 'Mixed',
    color: 'var(--warning)',
    // Must not claim BOTH scores are mid-range: MIXED only requires that ONE of
    // them is. BOS is MIXED with TDPI 58.6 and ACI 79.5 — the old wording
    // contradicted the two figures shown directly above it.
    blurb:
      'At least one of the two indices sits in the intermediate band (40–60), so the pair does not match one of the four corner profiles. The label combines a demand-side and an airside signal without distinguishing them — it does not mean both scores are mid-range, and one of them may be high or low. Read the two scores above rather than the label.',
  },
  UNCLASSIFIED_AIRSIDE_UNKNOWN: {
    label: 'Airside unmeasured',
    color: 'var(--text-muted)',
    blurb:
      'The airside-congestion proxy could not be computed. Absence of a measurement is NOT evidence that congestion is absent, so no class is assigned.',
  },
  UNCLASSIFIED: {
    label: 'Unclassified',
    color: 'var(--text-muted)',
    blurb: 'Terminal demand pressure could not be computed with sufficient coverage.',
  },
};

export function ClassBadge({ cls }: { cls: DivergenceClass }) {
  const meta = CLASS_META[cls] ?? CLASS_META.UNCLASSIFIED;
  return (
    <span className="class-badge" title={tip(cls) || meta.blurb}>
      <span className="dot" style={{ background: meta.color }} aria-hidden="true" />
      {meta.label}
    </span>
  );
}

/**
 * A 0-100 index meter. Two series (TDPI, ACI) always appear together with a
 * legend and a direct value label, so identity never rests on colour alone.
 */
export function ScoreMeter({
  kind,
  label,
  score,
  coverage,
  suppressedReason,
}: {
  kind: 'tdpi' | 'aci';
  label: string;
  score: number | null;
  coverage: number;
  suppressedReason: string | null;
}) {
  const suppressed = score === null;
  return (
    <div className="meter-row">
      <div className="meter-label">
        <span className="meter-name">
          <span className="swatch" style={{ background: `var(--${kind})` }} aria-hidden="true" />
          <Term k={kind.toUpperCase()}>{label}</Term>
        </span>
        {suppressed ? (
          <span className="meter-value suppressed">not scored</span>
        ) : (
          <span className="meter-value">{fmtScore(score)}</span>
        )}
      </div>
      <div
        className={`meter-track${suppressed ? ' empty' : ''}`}
        role="img"
        aria-label={
          suppressed
            ? `${label}: not scored`
            : `${label}: ${fmtScore(score)} out of 100`
        }
      >
        {!suppressed && (
          <div
            className={`meter-fill ${kind}`}
            style={{ width: `${Math.max(1.5, Math.min(100, score))}%` }}
          />
        )}
      </div>
      <div className="meter-ticks">
        <span>0</span>
        <span>
          {suppressed
            ? SUPPRESSION_TEXT[suppressedReason ?? 'no_data']?.split('—')[0].trim()
            : `coverage ${Math.round(coverage * 100)}%`}
        </span>
        <span>100</span>
      </div>
    </div>
  );
}
