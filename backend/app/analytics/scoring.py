"""TDPI, ACI and divergence classification.

Deterministic throughout: no randomness, no model calls, no hidden state. The
same warehouse and cohort always produce bit-identical scores.

Three suppression rules, applied in order, and each one reports *why* rather
than returning a misleading number:

1. ACI requires >= 1,000 OTP flights in the window. Delay rates on a few
   hundred observations are noise.
2. Any index with coverage < 0.60 of its declared weight is suppressed.
3. A suppressed ACI blocks divergence classification entirely — absence of a
   congestion measurement is not evidence of absence of congestion.
"""

from __future__ import annotations

from .definitions import (
    ACI_METRICS,
    DIVERGENCE_HI,
    DIVERGENCE_LO,
    DIVERGENCE_READINGS,
    GLOBAL_LIMITATIONS,
    MIN_COVERAGE,
    MIN_OTP_FLIGHTS_FOR_ACI,
    TDPI_METRICS,
    MetricDef,
)
from .metrics import AirportMetrics
from .models import AirportScores, ComponentResult, IndexResult
from .normalize import CohortStats, build_cohort_stats


class Cohort:
    """A peer group plus its per-metric winsorization bounds."""

    def __init__(self, name: str, members: list[AirportMetrics]) -> None:
        self.name = name
        self.members = members
        self.size = len(members)
        attrs = [m.attr for m in TDPI_METRICS + ACI_METRICS] + [
            "load_factor", "taxi_out_avg", "nas_delay_per_flight",
            "dep_del15_rate", "cancel_rate", "gauge_growth",
        ]
        # ACI metrics are only meaningful for airports that clear the volume
        # gate; including sub-threshold airports would drag the bounds toward
        # noisy small-sample values.
        aci_members = [m for m in members if m.flights >= MIN_OTP_FLIGHTS_FOR_ACI]
        self.stats: dict[str, CohortStats] = build_cohort_stats(members, attrs)
        aci_attrs = {m.attr for m in ACI_METRICS} | {
            "taxi_out_avg", "nas_delay_per_flight", "dep_del15_rate", "cancel_rate",
        }
        for attr in aci_attrs:
            self.stats[attr] = build_cohort_stats(aci_members, [attr])[attr]
        self.aci_eligible_size = len(aci_members)
        self._aci_scores: list[float] | None = None

    def stat(self, attr: str) -> CohortStats:
        return self.stats[attr]

    def aci_score_distribution(self) -> list[float]:
        """Ascending ACI scores across the cohort, computed once.

        UDEI's U4 indicator needs the cohort's ACI distribution; without this
        cache every UDEI call would re-score all ~400 peers.
        """
        if self._aci_scores is None:
            scores = [compute_aci(peer, self).score for peer in self.members]
            self._aci_scores = sorted(s for s in scores if s is not None)
        return self._aci_scores


def build_cohort(
    metrics: dict[str, AirportMetrics],
    *,
    name: str = "US primary commercial service airports",
    hub_class: str | None = None,
    region: str | None = None,
    require_traffic: bool = True,
) -> Cohort:
    """Select the peer group scores are normalised against."""
    members = list(metrics.values())
    if require_traffic:
        members = [m for m in members if m.has_traffic]
    if hub_class:
        members = [m for m in members if m.hub_class == hub_class]
        name = f"{name} (hub class {hub_class})"
    if region:
        members = [m for m in members if m.region == region]
        name = f"{name} (region {region})"
    members.sort(key=lambda m: m.iata)  # determinism
    return Cohort(name, members)


def _component(
    defn: MetricDef, m: AirportMetrics, cohort: Cohort
) -> ComponentResult:
    raw = getattr(m, defn.attr, None)
    stats = cohort.stat(defn.attr)
    normalized = stats.normalize(raw)
    if normalized is not None and not defn.higher_is_more_pressure:
        normalized = 100.0 - normalized
    return ComponentResult(
        id=defn.id,
        label=defn.label,
        raw=raw,
        raw_display=defn.fmt(raw) if raw is not None else None,
        normalized=normalized,
        percentile=stats.percentile_of(raw),
        weight=defn.weight,
        effective_weight=None,          # filled once we know what is present
        contribution=None,
        source=defn.source,
        available=normalized is not None,
        note=defn.note,
    )


def _compose(
    index: str,
    label: str,
    defs: list[MetricDef],
    m: AirportMetrics,
    cohort: Cohort,
    *,
    forced_suppression: str | None = None,
    notes: list[str] | None = None,
) -> IndexResult:
    comps = [_component(d, m, cohort) for d in defs]
    declared = sum(d.weight for d in defs)
    present = sum(c.weight for c in comps if c.available)
    coverage = present / declared if declared else 0.0

    # Renormalise the surviving weights so they sum to 1.0. A missing
    # component is dropped, never imputed.
    resolved: list[ComponentResult] = []
    score: float | None = 0.0
    for c in comps:
        if c.available and present > 0:
            eff = c.weight / present
            contrib = eff * (c.normalized or 0.0)
            resolved.append(
                ComponentResult(
                    **{**c.to_dict(), "effective_weight": eff, "contribution": contrib}
                )
            )
            score = (score or 0.0) + contrib
        else:
            resolved.append(c)

    reason: str | None = None
    if forced_suppression:
        reason, score = forced_suppression, None
    elif present == 0:
        reason, score = "no_data", None
    elif coverage < MIN_COVERAGE:
        reason, score = "insufficient_coverage", None

    return IndexResult(
        index=index,
        label=label,
        score=score,
        coverage=coverage,
        suppressed_reason=reason,  # type: ignore[arg-type]
        components=resolved,
        cohort_size=cohort.size,
        notes=list(notes or []),
    )


def compute_tdpi(m: AirportMetrics, cohort: Cohort) -> IndexResult:
    return _compose(
        "TDPI",
        "Terminal Demand Pressure Index",
        TDPI_METRICS,
        m,
        cohort,
        notes=[
            "TDPI measures demand pressure on the passenger-handling side. It "
            "is NOT a measurement of terminal capacity, and a high score is "
            "not evidence that an airport requires terminal expansion.",
        ],
    )


def compute_aci(m: AirportMetrics, cohort: Cohort) -> IndexResult:
    forced = None
    notes = [
        "ACI is a composite proxy index built from observed delay outcomes "
        "(taxi-out, NAS-attributed delay, delay rate, cancellations). It does "
        "not measure runway or airspace capacity and does not establish that "
        "any airside constraint is binding.",
    ]
    if m.flights < MIN_OTP_FLIGHTS_FOR_ACI:
        forced = "insufficient_flight_volume"
        notes.append(
            f"Suppressed: {m.flights:,} on-time-reported flights in the window, "
            f"below the {MIN_OTP_FLIGHTS_FOR_ACI:,} minimum. Delay rates on "
            f"small samples are noise, and a confident score on noise is worse "
            f"than no score."
        )
    return _compose(
        "ACI", "Airside Congestion Index", ACI_METRICS, m, cohort,
        forced_suppression=forced, notes=notes,
    )


def classify(tdpi: IndexResult, aci: IndexResult) -> str:
    """Divergence quadrant. Requires both indices; never guesses."""
    if tdpi.score is None:
        return "UNCLASSIFIED"
    if aci.score is None:
        return "UNCLASSIFIED_AIRSIDE_UNKNOWN"
    t, a = tdpi.score, aci.score
    if t >= DIVERGENCE_HI and a < DIVERGENCE_LO:
        return "TERMINAL_LED"
    if t >= DIVERGENCE_HI and a >= DIVERGENCE_HI:
        return "SYSTEMIC"
    if t < DIVERGENCE_LO and a >= DIVERGENCE_HI:
        return "AIRSIDE_LED"
    if t < DIVERGENCE_LO and a < DIVERGENCE_LO:
        return "NO_NEAR_TERM_CASE"
    return "MIXED"


def score_airport(
    m: AirportMetrics,
    cohort: Cohort,
    *,
    window: str,
    sources: list[dict] | None = None,
) -> AirportScores:
    tdpi = compute_tdpi(m, cohort)
    aci = compute_aci(m, cohort)
    cls = classify(tdpi, aci)

    limitations = list(GLOBAL_LIMITATIONS)
    if m.enplanements_preliminary:
        limitations.append(
            "FAA enplanement figures for the latest calendar year are "
            "preliminary and will be restated."
        )
    if m.traffic_months < 12:
        limitations.append(
            f"Only {m.traffic_months} of 12 months of traffic data are present "
            f"for {m.iata}; window metrics cover a partial year."
        )
    if aci.suppressed_reason == "insufficient_flight_volume":
        limitations.append(
            "Airside congestion is unmeasured for this airport. That is not "
            "evidence that congestion is absent."
        )

    return AirportScores(
        iata=m.iata,
        name=m.name,
        state=m.state,
        hub_class=m.hub_class,
        window=window,
        cohort=cohort.name,
        tdpi=tdpi,
        aci=aci,
        divergence_class=cls,  # type: ignore[arg-type]
        divergence_reading=DIVERGENCE_READINGS[cls],
        sources=sources or [],
        limitations=limitations,
    )
