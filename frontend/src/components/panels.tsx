/**
 * Analytics panels. Every value here is read from the backend's structured
 * response — none of it is parsed out of the model's prose.
 */
import type {
  AirportProfileResult,
  AirportScores,
  CompareResult,
  LongHaulResult,
  RankResult,
  SourceRecord,
  UnmetDemandResult,
} from '../types';
import {
  Card,
  ClassBadge,
  CLASS_META,
  fmtMin,
  fmtNum,
  fmtPct,
  fmtScore,
  ScoreMeter,
  SUPPRESSION_TEXT,
  Term,
} from './primitives';

/** Standing disclaimer. TDPI must never read as a measured capacity shortage. */
export function TdpiDisclaimer() {
  return (
    <div className="note caution">
      <b>TDPI and ACI are composite proxy indices, not capacity measurements.</b>{' '}
      The datasets used here do not contain gates, holdroom area, checkpoint
      lanes or baggage throughput, and ACI reflects observed delay outcomes
      rather than runway or airspace capacity. Divergence classes are screening
      classifications — a prompt to look closer, not an infrastructure diagnosis,
      and not a statement about whether any investment would be profitable.
    </div>
  );
}

function IndexPair({ scores }: { scores: AirportScores }) {
  return (
    <>
      <div className="legend" style={{ marginBottom: 12 }}>
        <span className="legend-item">
          <span className="swatch" style={{ background: 'var(--tdpi)' }} />
          TDPI — terminal demand pressure
        </span>
        <span className="legend-item">
          <span className="swatch" style={{ background: 'var(--aci)' }} />
          ACI — airside congestion (proxy)
        </span>
      </div>
      <ScoreMeter
        kind="tdpi"
        label="Terminal Demand Pressure"
        score={scores.tdpi.score}
        coverage={scores.tdpi.coverage}
        suppressedReason={scores.tdpi.suppressed_reason}
      />
      <ScoreMeter
        kind="aci"
        label="Airside Congestion"
        score={scores.aci.score}
        coverage={scores.aci.coverage}
        suppressedReason={scores.aci.suppressed_reason}
      />
    </>
  );
}

function Breakdown({ scores }: { scores: AirportScores }) {
  return (
    <>
      {[scores.tdpi, scores.aci].map((idx) => (
        <details className="disclosure" key={idx.index} open={idx.index === 'TDPI'}>
          <summary>
            {idx.label} — full calculation ({idx.components.length} components)
          </summary>
          {idx.suppressed_reason && (
            <div className="note caution" style={{ marginTop: 8 }}>
              {SUPPRESSION_TEXT[idx.suppressed_reason] ?? idx.suppressed_reason}
            </div>
          )}
          <div className="table-scroll">
            <table className="data" style={{ marginTop: 8 }}>
              <thead>
                <tr>
                  <th>Component</th>
                  <th className="num">Raw value</th>
                  <th className="num">
                    <Term k="NORMALISED">Norm.</Term>
                  </th>
                  <th className="num">
                    <Term k="PERCENTILE">Pctl</Term>
                  </th>
                  <th className="num">Weight</th>
                  <th className="num">Contribution</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {idx.components.map((c) => (
                  <tr key={c.id} className={c.available ? undefined : 'suppressed-row'}>
                    <td className="name">
                      <b>{c.id}</b> {c.label}
                      {c.note?.startsWith('PROXY ONLY') && (
                        <span
                          className="proxy-flag"
                          title={c.note}
                        >
                          ⚠ proxy
                        </span>
                      )}
                    </td>
                    <td className="num">{c.raw_display ?? 'not measured'}</td>
                    <td className="num">{fmtScore(c.normalized)}</td>
                    <td className="num">
                      {c.percentile === null ? '—' : Math.round(c.percentile)}
                    </td>
                    <td className="num">{c.weight.toFixed(2)}</td>
                    <td className="num">
                      {c.available ? (
                        <>
                          <span
                            className="mini-bar"
                            style={{
                              width: `${Math.max(2, (c.contribution ?? 0) * 1.6)}px`,
                              background:
                                idx.index === 'TDPI' ? 'var(--tdpi)' : 'var(--aci)',
                            }}
                          />
                          {fmtScore(c.contribution)}
                        </>
                      ) : (
                        <span title="Dropped — weight renormalised across the remaining components">
                          dropped
                        </span>
                      )}
                    </td>
                    <td className="name src">{c.source}</td>
                  </tr>
                ))}
                <tr className="total-row">
                  <td className="name">
                    <b>{idx.index} total</b>
                  </td>
                  <td colSpan={4} className="num">
                    <Term k="COVERAGE">coverage</Term>{' '}
                    {Math.round(idx.coverage * 100)}% · cohort n={idx.cohort_size}
                  </td>
                  <td className="num">
                    <b>{fmtScore(idx.score)}</b>
                  </td>
                  <td />
                </tr>
              </tbody>
            </table>
          </div>
          <div className="note" style={{ marginTop: 8 }}>
            <b>How to read this.</b> Contribution = renormalised weight ×
            normalised value; the contributions sum to the index score.{' '}
            <b>Normalised is not a percentile</b> — it is the raw value
            winsorized to the cohort's 5th–95th percentile then rescaled to
            0–100. The percentile column is the actual rank and is shown for
            interpretation only.
            {idx.components.some((c) => !c.available) &&
              ' Missing components are dropped, never imputed, and the remaining weights are renormalised to sum to 1.0.'}
          </div>
        </details>
      ))}
    </>
  );
}

export function ProfilePanel({ data }: { data: AirportProfileResult }) {
  const s = data.scores;
  return (
    <Card
      title={`${data.airport.iata} — ${data.airport.name}`}
      meta={`${data.airport.city ?? ''}${data.airport.state ? `, ${data.airport.state}` : ''} · hub ${
        data.airport.hub_class ?? '—'
      } · ${data.window}`}
    >
      <IndexPair scores={s} />
      <div style={{ margin: '12px 0' }}>
        <ClassBadge cls={s.divergence_class} />
      </div>
      <div className="note">{CLASS_META[s.divergence_class]?.blurb}</div>

      <div className="table-scroll" style={{ marginTop: 14 }}>
        <table className="data">
          <thead>
            <tr>
              <th>Traffic (12 mo)</th>
              <th className="num">Value</th>
              <th>Delay (12 mo)</th>
              <th className="num">Value</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="name">Passengers</td>
              <td className="num">{fmtNum(data.traffic.passengers)}</td>
              <td className="name">Avg taxi-out</td>
              <td className="num">{fmtMin(data.delay.avg_taxi_out_min)}</td>
            </tr>
            <tr>
              <td className="name">Departures</td>
              <td className="num">{fmtNum(data.traffic.departures)}</td>
              <td className="name">NAS delay / flight</td>
              <td className="num">{fmtMin(data.delay.nas_delay_per_flight_min, 2)}</td>
            </tr>
            <tr>
              <td className="name">Load factor</td>
              <td className="num">{fmtPct(data.traffic.load_factor)}</td>
              <td className="name">Departures &gt;15 min</td>
              <td className="num">{fmtPct(data.delay.dep_delayed_over_15min_rate)}</td>
            </tr>
            <tr>
              <td className="name">Seats / departure</td>
              <td className="num">{fmtNum(data.traffic.seats_per_departure, 1)}</td>
              <td className="name">Cancellation rate</td>
              <td className="num">{fmtPct(data.delay.cancellation_rate, 2)}</td>
            </tr>
            <tr>
              <td className="name">Passenger growth YoY</td>
              <td className="num">{fmtPct(data.traffic.passenger_growth_yoy)}</td>
              <td className="name">On-time-reported flights</td>
              <td className="num">{fmtNum(data.delay.otp_flights)}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div style={{ marginTop: 14 }}>
        <Breakdown scores={s} />
      </div>
      <TdpiDisclaimer />
    </Card>
  );
}

export function RankPanel({ data }: { data: RankResult }) {
  return (
    <Card
      title={`Ranking by ${data.index}`}
      meta={`${data.window} · cohort n=${data.cohort_size}`}
      accent={data.index === 'TDPI' ? 'tdpi' : 'aci'}
    >
      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr>
              <th className="num">#</th>
              <th>Airport</th>
              <th className="num">TDPI</th>
              <th className="num">ACI</th>
              <th>Class</th>
              <th className="num">Passengers</th>
            </tr>
          </thead>
          <tbody>
            {data.ranked.map((r) => (
              <tr key={r.iata}>
                <td className="num">{r.rank}</td>
                <td className="name">
                  <b>{r.iata}</b>{' '}
                  <span style={{ color: 'var(--text-muted)' }}>
                    {r.name?.slice(0, 26)}
                  </span>
                </td>
                <td className="num">
                  <span
                    className="mini-bar"
                    style={{
                      width: `${Math.max(2, (r.tdpi.score ?? 0) * 0.5)}px`,
                      background: 'var(--tdpi)',
                    }}
                  />
                  {fmtScore(r.tdpi.score)}
                </td>
                <td className="num">
                  {r.aci.score === null ? (
                    <span
                      style={{ color: 'var(--text-muted)' }}
                      title={SUPPRESSION_TEXT[r.aci.suppressed_reason ?? 'no_data']}
                    >
                      not scored
                    </span>
                  ) : (
                    <>
                      <span
                        className="mini-bar"
                        style={{
                          width: `${Math.max(2, r.aci.score * 0.5)}px`,
                          background: 'var(--aci)',
                        }}
                      />
                      {fmtScore(r.aci.score)}
                    </>
                  )}
                </td>
                <td className="name">
                  <ClassBadge cls={r.divergence_class} />
                </td>
                <td className="num">{fmtNum(r.scale?.passengers)}</td>
              </tr>
            ))}
            {data.unscored.map((r) => (
              <tr key={r.iata} className="suppressed-row">
                <td className="num">—</td>
                <td className="name">
                  <b>{r.iata}</b> {r.name?.slice(0, 26)}
                </td>
                <td colSpan={3} className="name">
                  {SUPPRESSION_TEXT[r.unscored_reason ?? 'no_data'] ?? r.unscored_reason}
                </td>
                <td className="num">{fmtNum(r.scale?.passengers)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.skipped.length > 0 && (
        <div className="note" style={{ marginTop: 10 }}>
          <b>Excluded:</b>{' '}
          {data.skipped.map((s) => `${s.iata} (${s.reason})`).join('; ')}
        </div>
      )}
      <div className="note" style={{ marginTop: 10 }}>
        Absolute passenger volume is shown beside each relative score: TDPI is
        cohort-relative and growth-weighted, so a very small airport can rank
        highly on a modest absolute change.
      </div>
      <TdpiDisclaimer />
    </Card>
  );
}

const COMPARE_ROWS: {
  section: 'volume' | 'intensity';
  key: string;
  label: string;
  fmt: (v: number | null) => string;
}[] = [
  { section: 'volume', key: 'departures', label: 'Departures', fmt: (v) => fmtNum(v) },
  { section: 'volume', key: 'passengers', label: 'Passengers', fmt: (v) => fmtNum(v) },
  { section: 'volume', key: 'otp_flights', label: 'On-time-reported flights', fmt: (v) => fmtNum(v) },
  { section: 'intensity', key: 'taxi_out_avg_min', label: 'Avg taxi-out', fmt: (v) => fmtMin(v) },
  { section: 'intensity', key: 'nas_delay_per_flight_min', label: 'NAS delay / flight', fmt: (v) => fmtMin(v, 2) },
  { section: 'intensity', key: 'dep_del15_rate', label: 'Departures >15 min', fmt: (v) => fmtPct(v) },
  { section: 'intensity', key: 'cancel_rate', label: 'Cancellation rate', fmt: (v) => fmtPct(v, 2) },
  { section: 'intensity', key: 'load_factor', label: 'Load factor', fmt: (v) => fmtPct(v) },
];

export function ComparePanel({ data }: { data: CompareResult }) {
  const codes = data.airports.map((a) => a.iata);
  return (
    <Card title={`Comparison — ${codes.join(' vs ')}`} meta={data.window}>
      <div className="note" style={{ marginTop: 0, marginBottom: 12 }}>
        <b>Volume and per-flight intensity are different questions.</b> A larger
        airport is not automatically more congested — read the two blocks
        separately.
      </div>

      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr>
              <th>Metric</th>
              {data.airports.map((a) => (
                <th key={a.iata} className="num">
                  {a.iata}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="name" colSpan={codes.length + 1}>
                <b style={{ fontSize: 11, letterSpacing: '0.05em' }}>VOLUME (SCALE)</b>
              </td>
            </tr>
            {COMPARE_ROWS.filter((r) => r.section === 'volume').map((row) => (
              <tr key={row.key}>
                <td className="name">{row.label}</td>
                {data.airports.map((a) => (
                  <td key={a.iata} className="num">
                    {row.fmt((a.volume as Record<string, number | null>)[row.key])}
                  </td>
                ))}
              </tr>
            ))}
            <tr>
              <td className="name" colSpan={codes.length + 1}>
                <b style={{ fontSize: 11, letterSpacing: '0.05em' }}>
                  PER-FLIGHT INTENSITY
                </b>
              </td>
            </tr>
            {COMPARE_ROWS.filter((r) => r.section === 'intensity').map((row) => (
              <tr key={row.key}>
                <td className="name">{row.label}</td>
                {data.airports.map((a) => (
                  <td key={a.iata} className="num">
                    {row.fmt((a.intensity as Record<string, number | null>)[row.key])}
                  </td>
                ))}
              </tr>
            ))}
            <tr>
              <td className="name" colSpan={codes.length + 1}>
                <b style={{ fontSize: 11, letterSpacing: '0.05em' }}>
                  PHYSICAL &amp; SCORES
                </b>
              </td>
            </tr>
            <tr>
              <td className="name">Runways / longest</td>
              {data.airports.map((a) => (
                <td key={a.iata} className="num">
                  {a.runway_count ?? '—'} / {fmtNum(a.longest_runway_ft)} ft
                </td>
              ))}
            </tr>
            <tr>
              <td className="name">TDPI</td>
              {data.airports.map((a) => (
                <td key={a.iata} className="num">
                  {fmtScore(a.scores?.tdpi.score ?? null)}
                </td>
              ))}
            </tr>
            <tr>
              <td className="name">ACI</td>
              {data.airports.map((a) => (
                <td key={a.iata} className="num">
                  {a.scores?.aci.score === null || a.scores?.aci.score === undefined
                    ? 'not scored'
                    : fmtScore(a.scores.aci.score)}
                </td>
              ))}
            </tr>
            <tr>
              <td className="name">Class</td>
              {data.airports.map((a) => (
                <td key={a.iata} className="num">
                  {a.scores && <ClassBadge cls={a.scores.divergence_class} />}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      <TdpiDisclaimer />
    </Card>
  );
}

export function LongHaulPanel({ data }: { data: LongHaulResult }) {
  const thresholds = data.scopes[0]?.bands.map((b) => b.threshold_sm) ?? [];
  const scopeColor: Record<string, string> = {
    all_carriers: 'var(--text-secondary)',
    passenger: 'var(--tdpi)',
    cargo: 'var(--cargo)',
    combi: 'var(--aci)',
    amphibious: 'var(--warning)',
  };
  const shortLabel: Record<string, string> = {
    all_carriers: 'All',
    passenger: 'Passenger',
    cargo: 'Freighter',
    combi: 'Combi',
    amphibious: 'Amphib.',
  };
  const rec = data.reconciliation;

  return (
    <Card title={`${data.iata} — long-haul distribution`} meta={data.period}>
      {!data.is_full_window && (
        <div className="banner warn">
          <b>Partial period.</b> This covers {data.period} and must not be read as
          a 12-month figure.
        </div>
      )}

      <div className="legend" style={{ marginBottom: 12 }}>
        {data.scopes.map((s) => (
          <span className="legend-item" key={s.scope}>
            <span className="swatch" style={{ background: scopeColor[s.scope] }} />
            {s.scope_label}
          </span>
        ))}
      </div>

      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr>
              <th>Threshold</th>
              {data.scopes.map((s) => (
                <th key={s.scope} className="num" title={s.scope_label}>
                  {s.scope === 'combi' ? (
                    <Term k="COMBI">{shortLabel[s.scope]}</Term>
                  ) : (
                    (shortLabel[s.scope] ?? s.scope_label)
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {thresholds.map((t, i) => (
              <tr
                key={t}
                style={
                  t === data.scopes[0]?.headline_threshold_sm
                    ? { background: 'var(--tdpi-soft)' }
                    : undefined
                }
              >
                <td className="name">
                  ≥ {fmtNum(t)} sm
                  {t === data.scopes[0]?.headline_threshold_sm && (
                    <b style={{ color: 'var(--text-secondary)' }}> (default)</b>
                  )}
                </td>
                {data.scopes.map((s) => (
                  <td key={s.scope} className="num">
                    <span
                      className="mini-bar"
                      style={{
                        width: `${Math.max(2, (s.bands[i]?.share_pct ?? 0) * 0.6)}px`,
                        background: scopeColor[s.scope],
                      }}
                    />
                    {s.bands[i]?.share_pct.toFixed(1)}%
                  </td>
                ))}
              </tr>
            ))}
            <tr>
              <td className="name">
                <b>Total departures</b>
              </td>
              {data.scopes.map((s) => (
                <td key={s.scope} className="num">
                  <b>{fmtNum(s.total_departures)}</b>
                </td>
              ))}
            </tr>
            <tr>
              <td className="name">Passengers carried</td>
              {data.scopes.map((s) => (
                <td key={s.scope} className="num">
                  {fmtNum(s.total_passengers)}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>

      <div className="note strong" style={{ marginTop: 12 }}>
        <b>Passenger aviation and cargo aviation are different businesses here.</b>{' '}
        The all-carrier figure blends them. For a passenger-terminal thesis, read
        the passenger column; for freight, the freighter column.
      </div>

      {rec && (
        <details className="disclosure" style={{ marginTop: 10 }} open={!rec.reconciles}>
          <summary>
            Configuration reconciliation —{' '}
            {rec.reconciles
              ? 'all departures accounted for'
              : `residual ${fmtNum(rec.residual)}`}
          </summary>
          <div className="table-scroll" style={{ marginTop: 6 }}>
            <table className="data">
              <thead>
                <tr>
                  <th>Aircraft configuration</th>
                  <th className="num">Departures</th>
                  <th className="num">Share</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(rec.breakdown).map(([key, v]) => (
                  <tr key={key} className={v.departures ? undefined : 'suppressed-row'}>
                    <td className="name">
                      <span
                        className="swatch"
                        style={{
                          background: scopeColor[key] ?? 'var(--text-muted)',
                          marginRight: 6,
                          display: 'inline-block',
                          verticalAlign: 'middle',
                        }}
                      />
                      {v.label}
                    </td>
                    <td className="num">{fmtNum(v.departures)}</td>
                    <td className="num">{v.share_pct.toFixed(2)}%</td>
                  </tr>
                ))}
                <tr className="total-row">
                  <td className="name">
                    <b>Total</b>
                  </td>
                  <td className="num">
                    <b>{fmtNum(rec.total_departures)}</b>
                  </td>
                  <td className="num">
                    <b>100.00%</b>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div className="note" style={{ marginTop: 8 }}>
            {rec.note}
          </div>
        </details>
      )}
      <div className="note" style={{ marginTop: 8 }}>
        <b>Definition:</b> {data.definition}
        <br />
        <b>Unit:</b> {data.unit}
      </div>
    </Card>
  );
}

export function UnmetDemandPanel({ data }: { data: UnmetDemandResult }) {
  const bandColor: Record<string, string> = {
    Strong: 'var(--critical)',
    Moderate: 'var(--warning)',
    Weak: 'var(--text-muted)',
    Indeterminate: 'var(--text-muted)',
  };
  return (
    <Card
      title={`${data.iata} — unmet demand evidence`}
      meta={data.window}
    >
      <div className="note strong" style={{ marginTop: 0, marginBottom: 14 }}>
        <b>
          <Term k="UDEI">Unmet demand</Term> cannot be quantified from the data
          this system uses.
        </b>{' '}
        Passengers who did not book and flights airlines did not schedule leave
        no trace in the BTS and FAA sources behind these tools. This panel
        organises evidence consistent with constrained supply — it does not
        quantify unmet demand, and no numeric estimate is produced.
      </div>

      <div style={{ marginBottom: 12 }}>
        <span
          className="band"
          style={{
            borderColor: bandColor[data.evidence_band],
            color: bandColor[data.evidence_band],
          }}
        >
          Evidence: {data.evidence_band}
        </span>
        <span style={{ marginLeft: 10, fontSize: 12, color: 'var(--text-secondary)' }}>
          {data.triggered_count} of {data.available_count} evaluable indicators
          triggered ({data.total_count} defined)
        </span>
      </div>

      {data.indicators.map((i) => (
        <div
          className={`indicator ${!i.available ? 'na' : i.triggered ? '' : 'off'}`}
          key={i.id}
        >
          <span className="icon" aria-hidden="true">
            {!i.available ? '○' : i.triggered ? '●' : '·'}
          </span>
          <span>
            <span className="lbl">
              {i.id} — {i.label}
            </span>
            <br />
            <span className="thr">
              {i.available
                ? `Trigger: ${i.threshold_display} · ${i.triggered ? 'TRIGGERED' : 'not triggered'}`
                : `Unavailable — ${i.unavailable_reason}`}
            </span>
          </span>
          <span className="val">{i.value_display ?? 'n/a'}</span>
        </div>
      ))}
    </Card>
  );
}

export function SourcesPanel({
  sources,
  limitations,
  assumptions,
}: {
  sources: SourceRecord[];
  limitations: string[];
  assumptions: string[];
}) {
  if (!sources.length && !limitations.length && !assumptions.length) return null;
  return (
    <Card title="Sources, freshness & caveats">
      {assumptions.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div
            style={{
              fontSize: 11,
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              color: 'var(--text-muted)',
              marginBottom: 5,
            }}
          >
            Assumptions in this conversation
          </div>
          <ul className="limitation-list">
            {assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </div>
      )}

      {sources.length > 0 && (
        <details className="disclosure" open>
          <summary>Data sources ({sources.length})</summary>
          <div style={{ marginTop: 6 }}>
            {sources.map((s) => (
              <div className="source-row" key={s.dataset}>
                <span className="nm">
                  {s.source_name}
                  <br />
                  <a href={s.source_url} target="_blank" rel="noreferrer noopener">
                    {s.source_url.slice(0, 58)}
                    {s.source_url.length > 58 ? '…' : ''}
                  </a>
                </span>
                <span className="cov">
                  {s.coverage}
                  <br />
                  <span style={{ fontSize: 10 }}>
                    retrieved {s.retrieved_at.slice(0, 10)}
                  </span>
                </span>
              </div>
            ))}
          </div>
        </details>
      )}

      {limitations.length > 0 && (
        <details className="disclosure" style={{ marginTop: 8 }}>
          <summary>Limitations ({limitations.length})</summary>
          <ul className="limitation-list" style={{ marginTop: 6 }}>
            {limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}
