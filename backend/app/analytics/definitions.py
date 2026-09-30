"""Declarative metric, weight and threshold definitions.

Kept separate from the scoring code so the methodology is inspectable and
testable as data: weights, directions, thresholds and proxy warnings all live
here rather than being scattered through the arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# Source labels attached to every component, so citations are generated.
SRC_T100 = "BTS T-100 Segment Summary by Origin Airport (Socrata r495-tyji)"
SRC_OTP = "BTS On-Time Performance (Reporting Carrier)"
SRC_FAA = "FAA Passenger Boarding (Enplanement) Data"
SRC_OA = "OurAirports runway inventory"
SRC_SEG = "BTS T-100 Segment (All Carriers)"


@dataclass(frozen=True)
class MetricDef:
    id: str
    label: str
    attr: str                    # attribute on AirportMetrics
    weight: float
    source: str
    unit: str
    fmt: Callable[[float], str]
    higher_is_more_pressure: bool = True
    note: str | None = None


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _pct_direct(v: float) -> str:
    return f"{v:.1f}%"


def _num(v: float) -> str:
    return f"{v:,.0f}"


def _min(v: float) -> str:
    return f"{v:.1f} min"


def _one(v: float) -> str:
    return f"{v:.1f}"


# ---------------------------------------------------------------------------
# TDPI — Terminal Demand Pressure Index
# ---------------------------------------------------------------------------
# What it claims: passenger-handling load is high and rising relative to peers.
# What it does NOT claim: that a terminal capacity deficit has been measured.

T4_PROXY_NOTE = (
    "PROXY ONLY. Passengers per runway is a rough proxy for relative airport "
    "throughput against physical scale. It is NOT a measurement of terminal "
    "capacity, gate availability, holdroom crowding, checkpoint queuing or "
    "baggage-system load — none of which are published in any public dataset. "
    "Runway count ignores runway geometry, spacing and weather-dependence, and "
    "correlates only loosely with terminal size."
)

TDPI_METRICS: list[MetricDef] = [
    MetricDef(
        id="T1", label="Load factor", attr="load_factor",
        weight=0.20, source=SRC_T100, unit="%", fmt=_pct,
    ),
    MetricDef(
        id="T2", label="Passenger growth (YoY)", attr="pax_growth",
        weight=0.30, source=SRC_T100, unit="%", fmt=_pct,
        note=(
            "Compared like-for-like: the ratio spans only calendar months "
            "present in both the current and prior windows. If the prior year "
            "does not cover every month of the current window, this component "
            "is dropped rather than reported, because the ratio would measure "
            "reporting coverage instead of demand."
        ),
    ),
    MetricDef(
        id="T3", label="Gauge (seats per departure)", attr="seats_per_departure",
        weight=0.15, source=SRC_T100, unit="seats", fmt=_one,
        note=(
            "Rising gauge with flat departures indicates airlines adding seats "
            "they cannot add as flights — the most terminal-specific signal "
            "available to us."
        ),
    ),
    MetricDef(
        id="T4", label="Throughput per runway (proxy)", attr="pax_per_runway",
        weight=0.20, source=f"{SRC_T100} + {SRC_OA}", unit="passengers/runway",
        fmt=_num, note=T4_PROXY_NOTE,
    ),
    MetricDef(
        id="T5", label="Enplanement growth (FAA CY25 vs CY24)", attr="enplanement_growth",
        weight=0.15, source=SRC_FAA, unit="%", fmt=_pct,
        note="FAA CY2025 is preliminary and will be restated.",
    ),
]

# ---------------------------------------------------------------------------
# TDPI v2 — EXPERIMENTAL candidate composite (opt-in, not the default)
# ---------------------------------------------------------------------------
# Motivation for each change from v1:
#
#   * v1 T1 (load factor) and T3 (gauge) both describe SEAT supply. Neither
#     says how many people actually walked through the terminal. v2 replaces
#     both with the YoY change in passengers per departure.
#   * v1 T2 (passenger growth) and T5 (FAA enplanement growth) measure the
#     same underlying quantity from two sources, so scoring both double-counts
#     growth. v2 scores the BTS figure and keeps FAA as a cross-source
#     validation signal reported alongside, not summed in.
#   * v1 T4 (throughput per runway) is a weak proxy for terminal capacity and
#     is dropped from the composite. It is still computed and reported as
#     context.
#
# The weights below are HYPOTHESES that were evaluated and NOT adopted; v1
# remains the production formulation. Retained because the tests pin them.

# Eligibility: shape metrics need enough months to have a shape at all.
MIN_YOY_MONTHS_FOR_SUSTAINED = 8   # of 12 possible month-pairs
MIN_MONTHS_FOR_PEAK = 10           # of 12 window months

# ---------------------------------------------------------------------------
# YEAR-OVER-YEAR WINDOW COMPARABILITY (Phase 8.1c) — ACTIVE in v1's T2.
#
# THE RULE: a YoY passenger-growth ratio is computed only when every month of
# the current window has a prior-year counterpart, and it is summed over the
# matched months alone. Otherwise T2 is dropped (never imputed) and the
# remaining weights are renormalised by the existing machinery.
#
# There is no month-count threshold, and deliberately so. Three formulations
# were considered:
#
#   (a) An absolute floor on prior months (">= 10 of 12"). REJECTED — it
#       discards WYS (Yellowstone), a seasonal airport reporting 6 window and
#       6 prior months that align exactly. Its +22% is a valid like-for-like
#       figure; a floor throws away a correct number and would have moved WYS
#       118 rank places for no analytical reason.
#
#   (b) Equal month COUNTS (prior >= window - 1). REJECTED — counts do not
#       imply the same months. GST and KLW each report 11 and 11, and both
#       pass (b), yet their window includes 2025-12 while their prior side
#       includes 2025-04 instead: the ratio compares December against April.
#
#   (c) MATCHING CALENDAR MONTHS. ADOPTED. It is parameter-free, it preserves
#       WYS, and it is the only one of the three that states the property a
#       growth ratio actually needs: both sides span the same months.
#
# Why alignment alone is not sufficient, and why the rule requires COMPLETE
# alignment rather than merely computing over whatever overlaps: GUF reports
# 12 window months against a prior year containing 2 months totalling SEVEN
# passengers (5 in 2024-06, 2 in 2024-08). Restricting the ratio to those two
# matched months still yields +167,929%. The prior year does not cover the
# airport's operation, so no ratio against it measures demand.
#
# Scope: this governs T2 (BTS T-100 passengers). It does NOT govern T5, which
# is FAA annual enplanements — a different source with its own coverage
# characteristics and no monthly series to align.
#
# Measured effect on the current warehouse: 5 of 399 airports change. Pinned by
# backend/tests/test_yoy_comparability.py.
# ---------------------------------------------------------------------------

SEASONALITY_WARN_RATIO = 2.0       # peak/mean above this is strongly seasonal


def _ratio(v: float) -> str:
    return f"{v:.2f}x"


TDPI_V2_METRICS: list[MetricDef] = [
    MetricDef(
        id="V1", label="Passenger growth (YoY)", attr="pax_growth",
        weight=0.30, source=SRC_T100, unit="%", fmt=_pct,
        note="BTS T-100 only. FAA enplanement growth is reported as a "
             "cross-source check rather than scored again.",
    ),
    MetricDef(
        id="V2", label="Sustained growth (share of months positive)",
        attr="sustained_growth", weight=0.25, source=SRC_T100, unit="%", fmt=_pct,
        note=f"Share of evaluable month-pairs with positive YoY passenger "
             f"growth. Requires at least {MIN_YOY_MONTHS_FOR_SUSTAINED} of 12 "
             f"pairs. Distinguishes durable expansion from one outlier month.",
    ),
    MetricDef(
        id="V3", label="Peak demand concentration", attr="peak_concentration",
        weight=0.20, source=SRC_T100, unit="ratio", fmt=_ratio,
        note="Busiest month / mean month. Terminals are sized for peak, but "
             "this rises with SEASONALITY as well as pressure — a summer-only "
             "airport scores high while empty for nine months. Under "
             "evaluation; see the Phase 8.1 report.",
    ),
    MetricDef(
        id="V4", label="Absolute passenger growth", attr="absolute_pax_growth",
        weight=0.15, source=SRC_T100, unit="passengers", fmt=_num,
        note="Size-biased by construction and correlated with V1, which is the "
             "same quantity in percentage form. Included so the redundancy can "
             "be measured rather than argued.",
    ),
    MetricDef(
        id="V5", label="Change in passengers per departure",
        attr="pax_per_departure_growth", weight=0.10, source=SRC_T100,
        unit="%", fmt=_pct,
        note="Replaces v1's load factor and gauge. Measures realised "
             "passengers per movement, so it cannot rise on empty seats.",
    ),
]

# Reported beside a v2 score but NOT summed into it.
TDPI_V2_CONTEXT_ATTRS = {
    "enplanement_growth": "FAA enplanement growth (cross-source check)",
    "pax_per_runway": "Throughput per runway (context only; dropped from v2)",
    "peak_concentration": "Peak/mean month ratio (seasonality indicator)",
}

TDPI_V2_NOTES = [
    "TDPI v2 is EXPERIMENTAL and opt-in. TDPI v1 remains the production "
    "default; nothing in the agent or frontend uses v2 unless asked.",
    "Like v1, v2 is a composite proxy for passenger demand pressure. It does "
    "not measure terminal capacity and is not an investment recommendation.",
    "FAA enplanement growth is reported as a cross-source validation signal, "
    "not scored, to avoid double-counting growth already captured by V1.",
]

# ---------------------------------------------------------------------------
# TDPI v2c — EXPERIMENTAL correction candidate (opt-in, not the default)
# ---------------------------------------------------------------------------
# Proposed in the Phase 8.1 report after v2 was rejected: drop V3 (peak
# concentration, which measured seasonality) and V4 (absolute growth, which
# duplicated V1), and restore a level anchor.
#
# ⚠ C3 IS THE OPEN QUESTION. Passengers per departure is average aircraft
# occupancy — gauge × load factor. It says how many people arrive per
# MOVEMENT, not how many pass through the terminal. An airport running 100
# flights of 200 passengers imposes the same terminal load as one running 200
# flights of 100, yet C3 scores the first twice as high. Phase 8.1b tests
# whether C3 earns its place or is a gauge proxy wearing a level-anchor label.

TDPI_V2C_METRICS: list[MetricDef] = [
    MetricDef(
        id="C1", label="Passenger growth (YoY)", attr="pax_growth",
        weight=0.30, source=SRC_T100, unit="%", fmt=_pct,
    ),
    MetricDef(
        id="C2", label="Sustained growth (share of months positive)",
        attr="sustained_growth", weight=0.25, source=SRC_T100, unit="%", fmt=_pct,
    ),
    MetricDef(
        id="C3", label="Passengers per departure (level)",
        attr="pax_per_departure", weight=0.30, source=SRC_T100,
        unit="passengers", fmt=_num,
        note="Average aircraft occupancy (gauge x load factor). This is a "
             "per-MOVEMENT measure, not a terminal-throughput measure, and it "
             "is NOT a measurement of terminal capacity. Under evaluation in "
             "Phase 8.1b.",
    ),
    MetricDef(
        id="C4", label="Change in passengers per departure",
        attr="pax_per_departure_growth", weight=0.15, source=SRC_T100,
        unit="%", fmt=_pct,
    ),
]

TDPI_V2C_NOTES = [
    "TDPI v2c is EXPERIMENTAL and opt-in. TDPI v1 remains the production "
    "default; no agent, API or frontend path uses v2c.",
    "A composite proxy for passenger demand pressure. It does not measure "
    "terminal capacity and is not an investment recommendation.",
    "C3 is average aircraft occupancy, a per-movement quantity. It must not be "
    "read as terminal throughput or as evidence of terminal constraint.",
]

# ---------------------------------------------------------------------------
# ACI — Airside Congestion Index
# ---------------------------------------------------------------------------
# A composite proxy built from observed delay OUTCOMES. The inputs are
# measured, but the index does not measure runway or airspace capacity and
# does not establish that any particular constraint is binding.

ACI_METRICS: list[MetricDef] = [
    MetricDef(
        id="A1", label="Average taxi-out time", attr="taxi_out_avg",
        weight=0.30, source=SRC_OTP, unit="minutes", fmt=_min,
        note="Physical surface queuing; the most airport-specific delay metric.",
    ),
    MetricDef(
        id="A2", label="NAS delay per flight", attr="nas_delay_per_flight",
        weight=0.30, source=SRC_OTP, unit="minutes", fmt=_min,
        note="FAA's own attribution of delay to the National Airspace System.",
    ),
    MetricDef(
        id="A3", label="Departures delayed >15 min", attr="dep_del15_rate",
        weight=0.25, source=SRC_OTP, unit="%", fmt=_pct,
        note="Contaminated by airline schedule padding and upstream late aircraft.",
    ),
    MetricDef(
        id="A4", label="Cancellation rate", attr="cancel_rate",
        weight=0.15, source=SRC_OTP, unit="%", fmt=_pct,
        note="Heavily weather-driven and lumpy; deliberately down-weighted.",
    ),
]

# ---------------------------------------------------------------------------
# Suppression and classification thresholds
# ---------------------------------------------------------------------------

MIN_COVERAGE = 0.60          # below this, no score is produced at all
MIN_OTP_FLIGHTS_FOR_ACI = 1000
WINSOR_LOW_PCTL = 5.0
WINSOR_HIGH_PCTL = 95.0

DIVERGENCE_HI = 60.0
DIVERGENCE_LO = 40.0

# Screening classifications, not investment recommendations or infrastructure
# diagnoses. Each describes only where the two proxy indices sit relative to
# the thresholds above; none asserts a cause or predicts what any investment
# would achieve.
DIVERGENCE_READINGS = {
    "TERMINAL_LED": (
        "Demand pressure is elevated while airside congestion is not. This is "
        "the profile most consistent with a terminal-side question and is a "
        "prompt to investigate — not evidence that terminal capacity is short, "
        "and not a recommendation."
    ),
    "SYSTEMIC": (
        "Both the demand-pressure and airside-congestion proxies are elevated "
        "relative to peers."
    ),
    "AIRSIDE_LED": (
        "The airside-congestion proxy is elevated while demand pressure is not. "
        "Delay and queuing are high relative to peers; this system does not "
        "identify the cause."
    ),
    "NO_NEAR_TERM_CASE": (
        "Neither demand pressure nor airside congestion is elevated versus peers."
    ),
    "MIXED": (
        "At least one of the two indices sits in the intermediate band "
        f"({DIVERGENCE_LO:.0f}–{DIVERGENCE_HI:.0f}), so the pair does not match "
        "one of the four corner profiles. The label combines a demand-side and "
        "an airside signal without distinguishing them, and it does NOT mean "
        "both scores are mid-range — one of them may well be high or low. Read "
        "the two scores directly rather than the label."
    ),
    "UNCLASSIFIED_AIRSIDE_UNKNOWN": (
        "Airside congestion could not be computed (insufficient flight volume "
        "or no on-time data). Absence of a measurement is NOT evidence of "
        "absence of congestion, so no class is assigned."
    ),
    "UNCLASSIFIED": (
        "Terminal demand pressure could not be computed with sufficient "
        "coverage; no class is assigned."
    ),
}

# ---------------------------------------------------------------------------
# Long-haul (approved decision D3/D4)
# ---------------------------------------------------------------------------

LONG_HAUL_THRESHOLDS_SM = [1500, 2000, 2500, 3000, 6000]
LONG_HAUL_DEFAULT_SM = 3000

LONG_HAUL_DEFINITION = (
    "Long-haul = segment great-circle distance >= 3,000 statute miles. "
    "Bands: short-haul <1,500 sm; medium-haul 1,500-2,999 sm; "
    "long-haul >=3,000 sm; ultra-long-haul >=6,000 sm."
)
LONG_HAUL_UNIT = (
    "Share of departures performed (T-100 DEPARTURES_PERFORMED), not seats "
    "and not passengers."
)

# T-100 AIRCRAFT_CONFIG codes (BTS Accounting & Reporting Directive No. 125,
# "T-100 System ADP Specifications"):
#
#   1  Passenger
#   2  All-cargo
#   3  Combi — passengers AND cargo on the main deck, moveable bulkhead
#   4  Amphibious — aircraft equipped for water landings
#
# Confirmed against the warehouse: config 3 averages 85 seats over ~1,393 sm
# and carries 3.57M passengers nationwide (real airliners carrying freight
# alongside passengers); config 4 averages 8.2 seats over ~55 sm and originates
# at LKE (Seattle Lake Union), KEH (Kenmore Air Harbor) and WFB (Waterfall) —
# all seaplane bases.
#
# Passenger and all-cargo are therefore NOT exhaustive. Reporting only those
# two leaves a residual that silently fails to reconcile with the total: at ANC
# that residual is 888 departures of combi service to Alaskan communities
# (AKN, GAL, DLG), which is genuinely both passenger and freight.
SCOPE_ALL = "all_carriers"
SCOPE_PASSENGER = "passenger"
SCOPE_CARGO = "cargo"
SCOPE_COMBI = "combi"
SCOPE_AMPHIBIOUS = "amphibious"

SCOPES = {
    SCOPE_ALL: ("All carriers (every configuration)", None),
    SCOPE_PASSENGER: ("Passenger-configured aircraft", "1"),
    SCOPE_CARGO: ("Freighter (all-cargo) aircraft", "2"),
    SCOPE_COMBI: ("Combi (passengers + cargo, main deck)", "3"),
    SCOPE_AMPHIBIOUS: ("Amphibious (water-landing) aircraft", "4"),
}

# Scopes that partition the total exactly once each.
EXCLUSIVE_SCOPES = [SCOPE_PASSENGER, SCOPE_CARGO, SCOPE_COMBI, SCOPE_AMPHIBIOUS]

# ---------------------------------------------------------------------------
# UDEI — Unmet Demand Evidence Index
# ---------------------------------------------------------------------------

UDEI_CAVEAT = (
    "These are convergent indicators consistent with constrained supply. They "
    "are NOT a measurement of unmet demand, which cannot be quantified from the "
    "datasets this system uses: passengers who did not book and flights "
    "airlines did not schedule leave no trace in the BTS and FAA sources behind "
    "these tools. No numeric estimate of unmet passengers or unmet flights is "
    "produced here, because this system has no basis for one."
)

UDEI_UPGAUGE_THRESHOLD = 0.02        # +2% YoY seats/departure
UDEI_COHORT_PERCENTILE = 75.0        # "high" = at or above cohort P75

# ---------------------------------------------------------------------------
# UDEI transparency text (Phase 8.3b).
#
# The band and its thresholds are UNCHANGED. What changed is that the band's
# construction and its known comparability limit are now stated wherever the
# band is shown, instead of being derivable only by reading the code.
# ---------------------------------------------------------------------------

UDEI_BAND_DEFINITION = (
    "The band counts how many indicators fired, on absolute counts: 0-1 Weak, "
    "2-3 Moderate, 4 or more Strong. Fewer than two evaluable indicators yields "
    "Indeterminate. It is a count of convergent signals, not a score, and it is "
    "not weighted."
)

UDEI_BAND_COMPARABILITY_NOTE = (
    "Because the band uses ABSOLUTE trigger counts while the number of "
    "evaluable indicators varies by airport, bands are not fully comparable "
    "across airports. U4 requires a computable ACI (suppressed below 1,000 "
    "on-time-reported flights) and U5 is unavailable everywhere, so an airport "
    "with three evaluable indicators cannot reach Strong however strong its "
    "evidence, while one with four can. Read the triggered count against the "
    "attainable maximum, not the band alone."
)

# U2 reads frequency growth, U3 reads gauge growth, U1 reads the level of load
# factor. Passenger growth decomposes exactly into those three terms, so they
# are related views of one quantity rather than independent confirmations.
UDEI_ARITHMETIC_NOTE = (
    "Passenger growth decomposes exactly: (1 + passenger growth) = "
    "(1 + departure growth) x (1 + seats-per-departure growth) x "
    "(1 + load-factor growth). U2 reads the first term, U3 the second, and U1 "
    "the level of the third. They are therefore RELATED VIEWS of one "
    "decomposition, not independent confirmations of each other: if passengers "
    "rise while departures do not, then gauge or load factor must have risen, "
    "as a matter of arithmetic rather than evidence."
)

# Absence of evidence is not evidence of absence. A Weak band means the
# observable indicators did not converge; it does not mean demand is being met.
UDEI_WEAK_IS_NOT_ABSENCE = (
    "A Weak band means the observable indicators did not converge. It is NOT "
    "proof that unmet demand does not exist: the indicators are proxies, U5 is "
    "missing everywhere, and the quantity itself is unobservable in this data. "
    "Absence of evidence is not evidence of absence."
)

# One-line evidence phrase per indicator, for the model view. The full
# `direction` wording stays on the indicator for the panel. Kept here rather
# than on the payload so the frontend response carries no model-view scaffolding.
UDEI_BRIEF_DIRECTIONS = {
    "U1": "consistent with little slack in seats offered",
    "U2": "consistent with passengers absorbed without adding flights",
    "U3": "consistent with capacity added as larger aircraft, not more flights",
    "U4": "consistent with high observed delay and queuing versus peers",
    "U5": "would be consistent with supply-constrained pricing; not evaluated",
}

# One-line forms of the limitations, for the model view. The full paragraphs
# stay in the frontend payload, which is where the evidence table is read.
UDEI_BRIEF_LIMITS = {
    "band": (
        "Absolute trigger counts (0-1 Weak, 2-3 Moderate, 4+ Strong), so bands "
        "are not comparable across airports with different indicator "
        "availability. Compare triggered against max_attainable."
    ),
    "u1_threshold": (
        "U1's bar is the cohort-wide P75; load factor varies by hub class, so "
        "large hubs clear it ~67% of the time against ~25% cohort-wide."
    ),
    "u2_u3_dependence": (
        "Passenger growth = departures x gauge x load factor, so U1/U2/U3 are "
        "related terms of one decomposition, not independent confirmations."
    ),
    "u3_threshold": "U3's +2% bar is a judgement constant, not derived.",
    "u4_threshold": "U4's bar is the cohort P75, an upper-quartile flag.",
    "causation": (
        "A fired indicator is consistent with constrained service; it cannot "
        "establish a cause. Fleet strategy, network changes and weather produce "
        "the same patterns."
    ),
    "quantification": (
        "No magnitude of unmet passengers or flights exists in this data and "
        "none is produced. Never state or imply one."
    ),
    "weak_is_not_absence": UDEI_WEAK_IS_NOT_ABSENCE,
}

UDEI_U1_COMPARABILITY_NOTE = (
    "U1's threshold is the percentile of the WHOLE cohort, which is dominated "
    "by small airports with lower load factors. Load factor varies "
    "systematically by hub class, so the same bar is easier for a large hub to "
    "clear: measured on the current window, large hubs trigger U1 about 67% of "
    "the time against roughly 25% cohort-wide. A trigger therefore means 'high "
    "relative to all US primary airports', not 'high for an airport of this "
    "size'."
)

# ---------------------------------------------------------------------------
# Global limitations, surfaced on every result
# ---------------------------------------------------------------------------

GLOBAL_LIMITATIONS = [
    "TDPI and ACI are composite proxy indices computed from observed aviation "
    "data and scored against a peer cohort. Neither measures infrastructure "
    "capacity.",
    "Terminal capacity is never measured. TDPI is demand *pressure*; the "
    "datasets used here do not contain gates, holdroom area, checkpoint lanes "
    "or baggage throughput.",
    "ACI reflects observed delay outcomes. It does not identify a cause and "
    "does not establish that any airside constraint is binding.",
    "Divergence classes are screening classifications, not investment "
    "recommendations or infrastructure diagnoses.",
    "Profitability is not modelled. No construction cost, financing, concession "
    "revenue or PFC/AIP structure is available, so no score should be read as "
    "evidence that a terminal expansion would be financially profitable.",
    "On-Time Performance is domestic and reporting-carrier only, and excludes "
    "all-cargo carriers; ACI therefore under-observes international and freight "
    "operations.",
    "All growth figures are trailing, not forecast. The FAA Terminal Area "
    "Forecast bulk download was out of service when this data was assembled.",
    "Scores are cohort-relative percentile positions within the stated peer "
    "group, not absolute capacity statements.",
    "Weights are reasoned judgement, reported with every score, and not derived "
    "from observed investment outcomes.",
]
