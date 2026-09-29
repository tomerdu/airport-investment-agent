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

from typing import Any

from .definitions import (
    GLOBAL_LIMITATIONS,
    UDEI_ARITHMETIC_NOTE,
    UDEI_BAND_COMPARABILITY_NOTE,
    UDEI_BAND_DEFINITION,
    UDEI_CAVEAT,
    UDEI_COHORT_PERCENTILE,
    UDEI_U1_COMPARABILITY_NOTE,
    UDEI_UPGAUGE_THRESHOLD,
    UDEI_WEAK_IS_NOT_ABSENCE,
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
    cohort_context: dict[str, Any] | None = None,
) -> UnmetDemandEvidence:
    """Observable indicators consistent with constrained supply.

    `cohort_context` is optional calibration data — how often each band occurs
    across the cohort — supplied by the engine so a reader can tell whether
    "Moderate" is common or rare. It is context for interpreting the BAND, and
    is never evidence about this airport.
    """
    indicators: list[Indicator] = []

    # --- U1: high fill ------------------------------------------------------
    lf_stats = cohort.stat("load_factor")
    lf_threshold = lf_stats.value_at_percentile(UDEI_COHORT_PERCENTILE)
    lf = m.load_factor

    # Optional, clearly labelled, and reproducible from the cohort alone: where
    # this airport's load factor sits among peers of the SAME hub class. Added
    # because U1's threshold is cohort-wide and load factor varies by class, so
    # "above cohort P75" and "high for an airport this size" are different
    # statements. It changes no trigger and no band.
    class_peers = sorted(
        p.load_factor for p in cohort.members
        if p.hub_class == m.hub_class and p.load_factor is not None
    )
    u1_class_note: str | None = None
    if lf is not None and m.hub_class and len(class_peers) >= 5:
        rank = sum(1 for x in class_peers if x > lf) + 1
        u1_class_note = (
            f"Within hub class {m.hub_class} this load factor ranks {rank} of "
            f"{len(class_peers)} (median {class_peers[len(class_peers) // 2] * 100:.1f}%). "
            f"Reproduce by ranking load_factor among cohort members with "
            f"hub_class == '{m.hub_class}'. Context only: the U1 trigger is "
            f"unchanged and remains the cohort-wide "
            f"P{UDEI_COHORT_PERCENTILE:.0f} comparison."
        )
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
            direction=(
                "Consistent with little residual slack in the seats actually "
                "offered, measured as a seat-weighted 12-month average."
            ),
            source=SRC_T100,
            unavailable_reason=None if lf is not None else "no traffic data in window",
            cannot_establish=(
                "Cannot establish that demand exceeded supply. Airlines manage "
                "toward high load factors, so a full aircraft is as consistent "
                "with a well-matched schedule as with an underserved market. An "
                "annual average also cannot show whether peaks are full while "
                "off-peak periods are empty, and unmet demand is a peak "
                "phenomenon."
            ),
            threshold_note=(
                UDEI_U1_COMPARABILITY_NOTE
                + (f" {u1_class_note}" if u1_class_note else "")
            ),
            shares_arithmetic_with=("U2", "U3"),
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
            direction=(
                "Consistent with additional passengers being absorbed without "
                "adding flights."
            ),
            source=SRC_T100,
            unavailable_reason=None if avail else "prior-year traffic unavailable",
            cannot_establish=(
                "Cannot establish WHY frequency did not rise. 'Could not add "
                "flights' and 'chose not to' produce the same pattern, and so do "
                "fleet availability, crew supply, hub restructuring and route "
                "profitability. The trigger is also a near-binary condition on "
                "two year-over-year ratios, so a departure change of +0.1% "
                "against -0.1% flips it."
            ),
            shares_arithmetic_with=("U1", "U3"),
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
            # Reworded in Phase 8.3b. The previous text called this "a classic
            # slot/gate-constrained signature", which asserts a cause this
            # measurement cannot support.
            direction=(
                "Consistent with capacity being added as larger aircraft rather "
                "than as additional flights."
            ),
            source=SRC_T100,
            unavailable_reason=None if gg is not None else "prior-year gauge unavailable",
            cannot_establish=(
                "Cannot establish a slot, gate or runway constraint. The US "
                "narrowbody fleet has been trending larger for years "
                "independently of any individual airport, so fleet renewal "
                "produces the same pattern; so does adding longer-haul routes. "
                "Cohort-wide the link between gauge growth and departure growth "
                "is weak (Spearman -0.10), which is not what a strong "
                "substitution effect would look like."
            ),
            threshold_note=(
                f"The +{UDEI_UPGAUGE_THRESHOLD * 100:.0f}% bar is a judgement "
                f"constant, not a value derived from the data."
            ),
            shares_arithmetic_with=("U1", "U2"),
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
            # Reworded in Phase 8.3b: "hitting operational limits" asserts a
            # limit that observed delay outcomes cannot establish.
            direction=(
                "Consistent with the airport operating with high observed delay "
                "and queuing relative to peers."
            ),
            source=SRC_OTP,
            unavailable_reason=(
                None if aci.score is not None
                else f"ACI suppressed: {aci.suppressed_reason}"
            ),
            cannot_establish=(
                "Cannot establish a runway, gate or airspace capacity limit. ACI "
                "measures delay OUTCOMES, not capacity, and it does not "
                "attribute cause: weather and delay propagating from other "
                "airports raise it identically. The Phase 8.2 temporal review "
                "also found that a high annual ACI is often concentrated in part "
                "of the year rather than sustained."
            ),
            threshold_note=(
                f"Like U1, the bar is the cohort "
                f"P{UDEI_COHORT_PERCENTILE:.0f}, so it identifies the cohort's "
                f"upper quartile by construction rather than an absolute "
                f"operational limit."
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
            direction=(
                "Would be consistent with pricing in a supply-constrained "
                "market. Not evaluated — see the unavailable reason."
            ),
            source="BTS Consumer Airfare Report (Socrata tfrh-tu9e)",
            unavailable_reason=(
                "Not ingested in the current warehouse. The source is verified "
                "and accessible; it was out of scope for the data foundation."
            ),
            cannot_establish=(
                "Establishes nothing at present: it is unavailable for every "
                "airport, so no band in this system includes it. Of the five it "
                "is the only one that would observe a market's own pricing "
                "response rather than an operational outcome, so its absence "
                "matters."
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

    # The best band this airport could reach on its own data coverage. Same
    # thresholds as above, applied to the attainable maximum rather than to the
    # actual count — so the ceiling is disclosed, not the band changed.
    if n_avail < 2:
        max_band = "Indeterminate"
    elif n_avail >= 4:
        max_band = "Strong"
    else:
        max_band = "Moderate"

    unavailable = [i for i in indicators if not i.available]

    limitations = list(GLOBAL_LIMITATIONS)
    limitations.insert(
        0,
        "Unmet demand is counterfactual and cannot be measured from public "
        "data. No numeric estimate of unmet passengers or flights is produced.",
    )
    if unavailable:
        missing = ", ".join(i.id for i in unavailable)
        limitations.insert(
            1,
            f"Indicator(s) {missing} could not be evaluated and are excluded "
            f"from the band; the attainable maximum is {n_avail} of "
            f"{len(indicators)}, so the best band reachable here is "
            f"'{max_band}'.",
        )
    limitations.insert(2, UDEI_BAND_COMPARABILITY_NOTE)
    limitations.insert(3, UDEI_ARITHMETIC_NOTE)
    if band in ("Weak", "Indeterminate"):
        limitations.insert(4, UDEI_WEAK_IS_NOT_ABSENCE)

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
        unavailable_count=len(unavailable),
        unavailable_reasons=[
            {"id": i.id, "label": i.label,
             "reason": i.unavailable_reason or "no reason recorded"}
            for i in unavailable
        ],
        max_attainable_triggered=n_avail,
        max_attainable_band=max_band,
        band_definition=UDEI_BAND_DEFINITION,
        band_comparability_note=UDEI_BAND_COMPARABILITY_NOTE,
        indicator_relationships=[UDEI_ARITHMETIC_NOTE],
        cohort_context=cohort_context,
        weak_is_not_absence=(
            UDEI_WEAK_IS_NOT_ABSENCE if band in ("Weak", "Indeterminate") else ""
        ),
    )
