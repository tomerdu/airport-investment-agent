"""Raw metric aggregation over the analysis window.

The central correctness rule lives here: **every window metric is computed as
SUM / SUM over the whole window**, never as a mean of monthly means. The
warehouse deliberately stores sums and counts to make that possible.

Concretely: BTV flies ~520 flights a month and LAX ~17,500. Averaging the two
airports' monthly taxi-out averages would weight a BTV month equally with a
LAX month. Within a single airport the same bug appears across months — a
quiet January would count as much as a busy July.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, fields

from etl import config


@dataclass
class AirportMetrics:
    """All raw inputs for one airport over the analysis window."""

    iata: str
    name: str
    city: str | None
    state: str | None
    region: str | None
    hub_class: str | None
    runway_count: int | None
    longest_runway_ft: int | None

    # --- traffic, current window (T-100) ---
    departures: float | None = None
    passengers: float | None = None
    seats: float | None = None
    intl_out_departures: float | None = None
    freight_lbs: float | None = None
    traffic_months: int = 0

    # --- traffic, prior window (for YoY) ---
    departures_prior: float | None = None
    passengers_prior: float | None = None
    seats_prior: float | None = None
    traffic_months_prior: int = 0

    # --- delay (OTP) ---
    flights: int = 0
    cancelled: int = 0
    diverted: int = 0
    dep_del15: int = 0
    dep_del15_n: int = 0
    taxi_out_sum: float = 0.0
    taxi_out_n: int = 0
    nas_delay_sum: float = 0.0
    nas_delay_n: int = 0
    dep_delay_sum: float = 0.0
    dep_delay_n: int = 0
    delay_months: int = 0

    # --- FAA enplanements ---
    enplanements: float | None = None
    enplanements_prior: float | None = None
    enplanement_growth: float | None = None
    enplanements_preliminary: bool = False

    # --- monthly series (TDPI v2 only) ---
    # Window aggregates cannot express month-to-month behaviour, so the v2
    # components that need a shape — sustained growth, peak concentration —
    # read these. 'YYYY-MM' -> value. Months absent from the warehouse are
    # absent here too; they are never filled with zero.
    monthly_passengers: dict[str, float] = field(default_factory=dict)
    monthly_passengers_prior: dict[str, float] = field(default_factory=dict)
    monthly_departures: dict[str, float] = field(default_factory=dict)

    # ---------------------------------------------------------------
    # Derived metrics. All SUM/SUM over the window.
    # ---------------------------------------------------------------

    @property
    def load_factor(self) -> float | None:
        if not self.seats or not self.passengers:
            return None
        return self.passengers / self.seats

    @property
    def seats_per_departure(self) -> float | None:
        if not self.departures or not self.seats:
            return None
        return self.seats / self.departures

    @property
    def seats_per_departure_prior(self) -> float | None:
        if not self.departures_prior or not self.seats_prior:
            return None
        return self.seats_prior / self.departures_prior

    # ---------------------------------------------------------------
    # Year-over-year window comparability (Phase 8.1c).
    #
    # A YoY ratio is only a measurement of demand if both sides cover the
    # SAME CALENDAR MONTHS. Equal month *counts* are not sufficient: GST and
    # KLW each report 11 window months and 11 prior months, but the window
    # includes 2025-12 while the prior side includes 2025-04 instead, so a
    # plain SUM/SUM ratio silently compares December against April.
    #
    # The rule has no tunable threshold. It asks one question — does every
    # window month have a prior-year counterpart? — and the ratio is taken
    # over the matched months only.
    # ---------------------------------------------------------------

    def _yoy_passenger_totals(self) -> tuple[float, float, int, int]:
        """(current_sum, prior_sum, matched_months, unmatched_window_months).

        Summed over window months that have a prior-year counterpart. Months
        present on only one side are excluded from BOTH sums, so the numerator
        and denominator always span the same calendar months.
        """
        cur_sum = prior_sum = 0.0
        matched = unmatched = 0
        for month, cur in self.monthly_passengers.items():
            year, mm = month.split("-")
            prev = self.monthly_passengers_prior.get(f"{int(year) - 1:04d}-{mm}")
            if cur is None:
                continue
            if prev is None:
                unmatched += 1
                continue
            cur_sum += cur
            prior_sum += prev
            matched += 1
        return cur_sum, prior_sum, matched, unmatched

    @property
    def yoy_windows_aligned(self) -> bool:
        """Does every window month have a prior-year counterpart?

        When the monthly series is unavailable this returns True, leaving the
        legacy annual SUM/SUM behaviour in place rather than suppressing a
        component on the basis of data we do not have.
        """
        if not self.monthly_passengers or not self.monthly_passengers_prior:
            return True
        _, _, matched, unmatched = self._yoy_passenger_totals()
        return unmatched == 0 and matched > 0

    @property
    def yoy_months_matched(self) -> int:
        """Window months with a prior-year counterpart. Diagnostic only."""
        if not self.monthly_passengers or not self.monthly_passengers_prior:
            return 0
        return self._yoy_passenger_totals()[2]

    @property
    def pax_growth(self) -> float | None:
        """Year-over-year passenger growth, compared like-for-like.

        Suppressed (None) when the prior window does not cover every month of
        the current window — the component is then dropped and the remaining
        weights renormalised by `_compose`, never imputed. See
        `yoy_windows_aligned`.
        """
        if self.passengers is None:
            return None
        if not self.monthly_passengers or not self.monthly_passengers_prior:
            # No monthly series (some test fixtures): legacy annual ratio.
            if not self.passengers_prior:
                return None
            return self.passengers / self.passengers_prior - 1.0
        cur_sum, prior_sum, matched, unmatched = self._yoy_passenger_totals()
        if unmatched or matched == 0 or prior_sum <= 0:
            return None
        return cur_sum / prior_sum - 1.0

    @property
    def departure_growth(self) -> float | None:
        if not self.departures_prior or self.departures is None:
            return None
        return self.departures / self.departures_prior - 1.0

    @property
    def gauge_growth(self) -> float | None:
        cur, prior = self.seats_per_departure, self.seats_per_departure_prior
        if cur is None or not prior:
            return None
        return cur / prior - 1.0

    @property
    def pax_per_runway(self) -> float | None:
        """PROXY for relative throughput against physical scale.

        Not a measurement of terminal capacity or congestion — see
        `definitions.T4_PROXY_NOTE`.
        """
        if not self.runway_count or self.passengers is None:
            return None
        return self.passengers / self.runway_count

    @property
    def intl_departure_share(self) -> float | None:
        if not self.departures or self.intl_out_departures is None:
            return None
        return self.intl_out_departures / self.departures

    # --- delay metrics: every one is a ratio of window sums ---

    @property
    def taxi_out_avg(self) -> float | None:
        return self.taxi_out_sum / self.taxi_out_n if self.taxi_out_n else None

    @property
    def nas_delay_per_flight(self) -> float | None:
        return self.nas_delay_sum / self.nas_delay_n if self.nas_delay_n else None

    @property
    def dep_delay_avg(self) -> float | None:
        return self.dep_delay_sum / self.dep_delay_n if self.dep_delay_n else None

    @property
    def dep_del15_rate(self) -> float | None:
        return self.dep_del15 / self.dep_del15_n if self.dep_del15_n else None

    @property
    def cancel_rate(self) -> float | None:
        return self.cancelled / self.flights if self.flights else None

    # ---------------------------------------------------------------
    # TDPI v2 candidate metrics (experimental, opt-in)
    # ---------------------------------------------------------------
    # Every one returns None rather than a filled value when its eligibility
    # rule is not met. Thresholds are named constants in `definitions` so the
    # rules are inspectable rather than buried in arithmetic.

    @property
    def pax_per_departure(self) -> float | None:
        """Actual passengers carried per departure.

        Replaces load factor × gauge: those measure *seat* supply, this
        measures realised passengers, which is what a terminal handles.
        """
        if not self.departures or self.passengers is None:
            return None
        return self.passengers / self.departures

    @property
    def pax_per_departure_prior(self) -> float | None:
        if not self.departures_prior or self.passengers_prior is None:
            return None
        return self.passengers_prior / self.departures_prior

    @property
    def pax_per_departure_growth(self) -> float | None:
        """YoY change in passengers per departure.

        Rising = each movement delivers more people into the terminal, whether
        through bigger aircraft or fuller ones. Unlike gauge it cannot rise on
        empty seats.
        """
        cur, prior = self.pax_per_departure, self.pax_per_departure_prior
        if cur is None or not prior:
            return None
        return cur / prior - 1.0

    def _yoy_month_pairs(self) -> list[tuple[str, float]]:
        """(month, YoY growth) for each window month with a usable prior-year
        counterpart. Months without a pair are omitted, never imputed."""
        out: list[tuple[str, float]] = []
        for month, cur in sorted(self.monthly_passengers.items()):
            year, mm = month.split("-")
            prior_key = f"{int(year) - 1:04d}-{mm}"
            prev = self.monthly_passengers_prior.get(prior_key)
            if prev and prev > 0 and cur is not None:
                out.append((month, cur / prev - 1.0))
        return out

    @property
    def yoy_months_evaluable(self) -> int:
        return len(self._yoy_month_pairs())

    @property
    def sustained_growth(self) -> float | None:
        """Share of evaluable months whose YoY passenger growth is positive.

        Separates durable expansion from a single outlier month: an airport
        that grew 20% on one chartered summer can score the same annual growth
        as one that grew 2% every month, but not the same sustained share.

        Requires `MIN_YOY_MONTHS_FOR_SUSTAINED` evaluable month-pairs.
        """
        from .definitions import MIN_YOY_MONTHS_FOR_SUSTAINED

        pairs = self._yoy_month_pairs()
        if len(pairs) < MIN_YOY_MONTHS_FOR_SUSTAINED:
            return None
        return sum(1 for _, g in pairs if g > 0) / len(pairs)

    @property
    def peak_concentration(self) -> float | None:
        """Busiest month ÷ mean month, over window passengers.

        Terminals are sized for peak rather than mean throughput, so this is
        conceptually relevant — but it rises with *seasonality* as well as with
        pressure. A summer-only resort airport scores high while being empty
        for nine months. Evaluated rather than assumed; see the Phase 8.1
        report.
        """
        from .definitions import MIN_MONTHS_FOR_PEAK

        vals = [v for v in self.monthly_passengers.values() if v is not None and v > 0]
        if len(vals) < MIN_MONTHS_FOR_PEAK:
            return None
        mean = sum(vals) / len(vals)
        return max(vals) / mean if mean > 0 else None

    @property
    def absolute_pax_growth(self) -> float | None:
        """Passenger count delta versus the prior window.

        Size-biased by construction: a 2% gain at a large hub outweighs a 40%
        gain at a small one. Kept as a candidate so the bias can be measured
        rather than argued about.
        """
        if self.passengers is None or self.passengers_prior is None:
            return None
        return self.passengers - self.passengers_prior


    @property
    def tdpi_v2_eligible(self) -> bool:
        """Enough months in both windows for the shape-based components."""
        from .definitions import MIN_MONTHS_FOR_PEAK, MIN_YOY_MONTHS_FOR_SUSTAINED

        return (
            len(self.monthly_passengers) >= MIN_MONTHS_FOR_PEAK
            and self.yoy_months_evaluable >= MIN_YOY_MONTHS_FOR_SUSTAINED
        )

    @property
    def has_traffic(self) -> bool:
        return bool(self.departures)

    @property
    def aci_eligible(self) -> bool:
        return self.flights >= config.MIN_OTP_FLIGHTS_FOR_ACI


_TRAFFIC_SQL = """
SELECT iata,
       SUM(departures)          AS departures,
       SUM(passengers)          AS passengers,
       SUM(seats)               AS seats,
       SUM(intl_out_departures) AS intl_out_departures,
       SUM(freight_lbs)         AS freight_lbs,
       COUNT(DISTINCT month)    AS months
FROM airport_month
WHERE month BETWEEN ? AND ?
GROUP BY iata
"""

# Sums and counts only — the ratios are formed afterwards, once, over the
# whole window.
_DELAY_SQL = """
SELECT iata,
       SUM(flights)        AS flights,
       SUM(cancelled)      AS cancelled,
       SUM(diverted)       AS diverted,
       SUM(dep_del15)      AS dep_del15,
       SUM(dep_del15_n)    AS dep_del15_n,
       SUM(taxi_out_sum)   AS taxi_out_sum,
       SUM(taxi_out_n)     AS taxi_out_n,
       SUM(nas_delay_sum)  AS nas_delay_sum,
       SUM(nas_delay_n)    AS nas_delay_n,
       SUM(dep_delay_sum)  AS dep_delay_sum,
       SUM(dep_delay_n)    AS dep_delay_n,
       COUNT(DISTINCT month) AS months
FROM airport_delay_month
WHERE month BETWEEN ? AND ?
GROUP BY iata
"""


# Per-month rows for the TDPI v2 shape metrics. Kept separate from the
# aggregate query so v1 is untouched.
_MONTHLY_SQL = """
SELECT iata, month, passengers, departures
FROM airport_month
WHERE month BETWEEN ? AND ?
ORDER BY iata, month
"""


def load_metrics(
    conn: sqlite3.Connection,
    *,
    window_start: str = config.WINDOW_START,
    window_end: str = config.WINDOW_END,
    prior_start: str = config.PRIOR_WINDOW_START,
    prior_end: str = config.PRIOR_WINDOW_END,
    universe_only: bool = True,
) -> dict[str, AirportMetrics]:
    """Build the full metric table for every airport in one pass."""
    where = "WHERE in_universe = 1" if universe_only else ""
    out: dict[str, AirportMetrics] = {}
    for r in conn.execute(
        f"""SELECT iata, name, city, state, region, hub_class,
                   runway_count, longest_runway_ft
            FROM airports {where}"""
    ):
        out[r["iata"]] = AirportMetrics(
            iata=r["iata"], name=r["name"], city=r["city"], state=r["state"],
            region=r["region"], hub_class=r["hub_class"],
            runway_count=r["runway_count"], longest_runway_ft=r["longest_runway_ft"],
        )

    for r in conn.execute(_TRAFFIC_SQL, (window_start, window_end)):
        m = out.get(r["iata"])
        if m is None:
            continue
        m.departures = r["departures"]
        m.passengers = r["passengers"]
        m.seats = r["seats"]
        m.intl_out_departures = r["intl_out_departures"]
        m.freight_lbs = r["freight_lbs"]
        m.traffic_months = r["months"]

    for r in conn.execute(_TRAFFIC_SQL, (prior_start, prior_end)):
        m = out.get(r["iata"])
        if m is None:
            continue
        m.departures_prior = r["departures"]
        m.passengers_prior = r["passengers"]
        m.seats_prior = r["seats"]
        m.traffic_months_prior = r["months"]

    # Monthly series spanning BOTH windows, for the TDPI v2 shape metrics.
    # One pass; months missing from the warehouse simply do not appear.
    for r in conn.execute(_MONTHLY_SQL, (prior_start, window_end)):
        m = out.get(r["iata"])
        if m is None or r["passengers"] is None:
            continue
        if window_start <= r["month"] <= window_end:
            m.monthly_passengers[r["month"]] = r["passengers"]
            if r["departures"] is not None:
                m.monthly_departures[r["month"]] = r["departures"]
        elif prior_start <= r["month"] <= prior_end:
            m.monthly_passengers_prior[r["month"]] = r["passengers"]

    for r in conn.execute(_DELAY_SQL, (window_start, window_end)):
        m = out.get(r["iata"])
        if m is None:
            continue
        m.flights = r["flights"] or 0
        m.cancelled = r["cancelled"] or 0
        m.diverted = r["diverted"] or 0
        m.dep_del15 = r["dep_del15"] or 0
        m.dep_del15_n = r["dep_del15_n"] or 0
        m.taxi_out_sum = r["taxi_out_sum"] or 0.0
        m.taxi_out_n = r["taxi_out_n"] or 0
        m.nas_delay_sum = r["nas_delay_sum"] or 0.0
        m.nas_delay_n = r["nas_delay_n"] or 0
        m.dep_delay_sum = r["dep_delay_sum"] or 0.0
        m.dep_delay_n = r["dep_delay_n"] or 0
        m.delay_months = r["months"] or 0

    # FAA enplanements: latest calendar year present, joined via FAA Locid.
    latest_cy = conn.execute("SELECT MAX(cy) FROM enplanements").fetchone()[0]
    if latest_cy is not None:
        for r in conn.execute(
            """
            SELECT a.iata, e.enplanements, e.prior_year, e.pct_change, e.preliminary
            FROM airports a
            JOIN enplanements e ON e.faa_locid = a.faa_locid
            WHERE e.cy = ?
            """,
            (latest_cy,),
        ):
            m = out.get(r["iata"])
            if m is None:
                continue
            m.enplanements = r["enplanements"]
            m.enplanements_prior = r["prior_year"]
            # FAA publishes the change as a fraction (-0.0139 = -1.39%).
            # Recompute where both years are present rather than trusting the
            # published column, and fall back to it otherwise.
            if r["prior_year"]:
                m.enplanement_growth = r["enplanements"] / r["prior_year"] - 1.0
            else:
                m.enplanement_growth = r["pct_change"]
            m.enplanements_preliminary = bool(r["preliminary"])

    return out


METRIC_ATTRS = [
    f.name for f in fields(AirportMetrics)
] + [
    "load_factor", "seats_per_departure", "pax_growth", "departure_growth",
    "gauge_growth", "pax_per_runway", "intl_departure_share",
    "taxi_out_avg", "nas_delay_per_flight", "dep_delay_avg",
    "dep_del15_rate", "cancel_rate",
]
