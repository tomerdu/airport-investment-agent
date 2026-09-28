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
        "Indices fall in the middle band; no clean classification. Read both "
        "scores directly rather than the label."
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
