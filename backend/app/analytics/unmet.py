"""Unmet Demand Evidence Index (UDEI).

Unmet demand is counterfactual: passengers who did not book and flights
airlines did not schedule leave no record in any dataset. There is therefore
no honest way to produce a number, and this module deliberately cannot —
`UnmetDemandEvidence` has no magnitude field to populate.

What it returns instead is a table of observable indicators, each with its
value, its trigger threshold, whether it fired, and its source; plus a
qualitative band. An indicator that cannot be evaluated is reported as
**unavailable with a reason**, never assumed.
"""

from __future__ import annotations

from .definitions import (
    GLOBAL_LIMITATIONS,
    UDEI_CAVEAT,
    UDEI_COHORT_PERCENTILE,
    UDEI_UPGAUGE_THRESHOLD,
    SRC_OTP,
    SRC_T100,
)
from .metrics import AirportMetrics
from .models import Indicator, UnmetDemandEvidence
from .normalize import percentile
from .scoring import Cohort, compute_aci


def _fmt_pct(v: float | None) -> str | None:
    return None if v is None else f"{v * 100:.1f}%"


def unmet_demand_evidence(
    m: AirportMetrics,
    cohort: Cohort,
    *,
    window: str,
    sources: list[dict] | None = None,
) -> UnmetDemandEvidence:
    indicators: list[Indicator] = []

    # --- U1: high fill ------------------------------------------------------
    lf_stats = cohort.stat("load_factor")
    lf_threshold = lf_stats.value_at_percentile(UDEI_COHORT_PERCENTILE)
    lf = m.load_factor
    indicators.append(
        Indicator(
            id="U1",
            label="High load factor",
            value=lf,
            value_display=_fmt_pct(lf),
            threshold_display=(
                f">= cohort P{UDEI_COHORT_PERCENTILE:.0f} ({_fmt_pct(lf_threshold)})"
                if lf_threshold is not None else "unavailable"
            ),
            triggered=(lf is not None and lf_threshold is not None and lf >= lf_threshold),
            available=lf is not None and lf_threshold is not None,
            direction="Little slack in existing seats",
            source=SRC_T100,
            unavailable_reason=None if lf is not None else "no traffic data in window",
        )
    )

    # --- U2: frequency suppression -----------------------------------------
    pg, dg = m.pax_growth, m.departure_growth
    avail = pg is not None and dg is not None
    indicators.append(
        Indicator(
            id="U2",
            label="Frequency suppression",
            value=dg,
            value_display=(
                f"passengers {_fmt_pct(pg)} / departures {_fmt_pct(dg)}"
                if avail else None
            ),
            threshold_display="passenger growth > 0 AND departure growth <= 0",
            triggered=(avail and pg > 0 and dg <= 0),
            available=avail,
            direction="Demand absorbed without adding flights",
            source=SRC_T100,
            unavailable_reason=None if avail else "prior-year traffic unavailable",
        )
    )

    # --- U3: upgauging ------------------------------------------------------
    gg = m.gauge_growth
    indicators.append(
        Indicator(
            id="U3",
            label="Upgauging (seats per departure rising)",
            value=gg,
            value_display=_fmt_pct(gg),
            threshold_display=f">= +{UDEI_UPGAUGE_THRESHOLD * 100:.0f}% YoY",
            triggered=(gg is not None and gg >= UDEI_UPGAUGE_THRESHOLD),
            available=gg is not None,
            direction=(
                "Airlines adding seats they cannot add as flights — a classic "
                "slot/gate-constrained signature"
            ),
            source=SRC_T100,
            unavailable_reason=None if gg is not None else "prior-year gauge unavailable",
        )
    )

    # --- U4: throughput ceiling --------------------------------------------
    aci = compute_aci(m, cohort)
    aci_values = cohort.aci_score_distribution()
    aci_threshold = (
        percentile(aci_values, UDEI_COHORT_PERCENTILE) if aci_values else None
    )
    indicators.append(
        Indicator(
            id="U4",
            label="Airside throughput ceiling",
            value=aci.score,
            value_display=(f"ACI {aci.score:.1f}" if aci.score is not None else None),
            threshold_display=(
                f">= cohort P{UDEI_COHORT_PERCENTILE:.0f} (ACI {aci_threshold:.1f})"
                if aci_threshold is not None else "unavailable"
            ),
            triggered=(
                aci.score is not None
                and aci_threshold is not None
                and aci.score >= aci_threshold
            ),
            available=aci.score is not None and aci_threshold is not None,
            direction="Airport is hitting operational limits",
            source=SRC_OTP,
            unavailable_reason=(
                None if aci.score is not None
                else f"ACI suppressed: {aci.suppressed_reason}"
            ),
        )
    )

    # --- U5: fare premium ---------------------------------------------------
    # Verified to exist (BTS Consumer Airfare Report, Socrata tfrh-tu9e) but
    # NOT ingested in the Phase 2 data foundation. Reported as unavailable
    # rather than assumed — an indicator without data is not a zero.
    indicators.append(
        Indicator(
            id="U5",
            label="Fare premium vs distance-matched markets",
            value=None,
            value_display=None,
            threshold_display="route fares above distance-matched cohort median",
            triggered=None,
            available=False,
            direction="Supply-constrained market pricing",
            source="BTS Consumer Airfare Report (Socrata tfrh-tu9e)",
            unavailable_reason=(
                "Not ingested in the current warehouse. The source is verified "
                "and accessible; it was out of scope for the data foundation."
            ),
        )
    )

    available = [i for i in indicators if i.available]
    triggered = [i for i in available if i.triggered]
    n_avail, n_trig = len(available), len(triggered)

    # Band uses the approved absolute counts (0-1 Weak, 2-3 Moderate, 4+
    # Strong). With U5 unavailable the attainable maximum is 4, so "Strong"
    # requires all four remaining indicators to fire — a deliberately high
    # bar. If fewer than two indicators can be evaluated at all, no band is
    # claimed.
    if n_avail < 2:
        band = "Indeterminate"
    elif n_trig >= 4:
        band = "Strong"
    elif n_trig >= 2:
        band = "Moderate"
    else:
        band = "Weak"

    limitations = list(GLOBAL_LIMITATIONS)
    limitations.insert(
        0,
        "Unmet demand is counterfactual and cannot be measured from public "
        "data. No numeric estimate of unmet passengers or flights is produced.",
    )
    if n_avail < len(indicators):
        missing = ", ".join(i.id for i in indicators if not i.available)
        limitations.insert(
            1,
            f"Indicator(s) {missing} could not be evaluated and are excluded "
            f"from the band; the attainable maximum is {n_avail} of "
            f"{len(indicators)}.",
        )

    return UnmetDemandEvidence(
        iata=m.iata,
        name=m.name,
        window=window,
        indicators=indicators,
        triggered_count=n_trig,
        available_count=n_avail,
        total_count=len(indicators),
        evidence_band=band,  # type: ignore[arg-type]
        caveat=UDEI_CAVEAT,
        sources=sources or [],
        limitations=limitations,
    )
