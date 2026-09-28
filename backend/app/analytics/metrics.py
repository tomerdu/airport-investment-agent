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
from dataclasses import dataclass, fields

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

    @property
    def pax_growth(self) -> float | None:
        if not self.passengers_prior or self.passengers is None:
            return None
        return self.passengers / self.passengers_prior - 1.0

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
