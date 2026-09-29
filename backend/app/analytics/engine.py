"""Analytics entry point: ranking, comparison, profiles and source citation.

One `AnalyticsEngine` holds the warehouse connection and a cached metric table,
so a ranking over 400 airports is a single pass rather than 400 queries.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from etl import config

from .definitions import GLOBAL_LIMITATIONS
from .longhaul import long_haul_breakdown
from .metrics import AirportMetrics, load_metrics
from .models import AirportScores, LongHaulResult, UnmetDemandEvidence
from .persistence import MonthlyDelay, build_profile, load_monthly_delay
from .scoring import Cohort, build_cohort, score_airport
from .unmet import unmet_demand_evidence

WINDOW_LABEL = f"{config.WINDOW_START}..{config.WINDOW_END}"


def _temporal_summary(t: dict[str, Any] | None) -> dict[str, Any] | None:
    """The comparison-table subset of a temporal diagnostic.

    Drops the monthly series and the notes; keeps every figure a reader needs to
    judge whether two ACI scores mean the same thing.
    """
    if t is None:
        return None
    return {
        "temporal_pattern": t["temporal_pattern"],
        "pattern_label": t["pattern_label"],
        "months_available": t["months_available"],
        "months_expected": t["months_expected"],
        "months_evaluated": t["months_evaluated"],
        "coverage_complete": t["coverage_complete"],
        "worst_two_month_drop": t["concentration"]["worst_two_month_drop"],
        "concentration_reliable": t["concentration"]["reliable"],
        "concentration_unreliable_reason": t["concentration"]["unreliable_reason"],
        "monthly_spread": t["monthly_spread"],
        "elevated_months": t["elevated_months"],
        "no_elevated_months": t["no_elevated_months"],
        "elevated_season": t["elevated_season"],
        "uncertainty": t["uncertainty"],
    }


class AnalyticsEngine:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or config.WAREHOUSE_PATH
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"warehouse not found at {self.db_path}; run `python -m etl.build_warehouse`"
            )
        # check_same_thread=False: FastAPI runs sync endpoints on a threadpool,
        # so the connection is created on one thread and used on others. The
        # connection is strictly read-only (mode=ro) and every query is
        # serialised by `self._lock`, so this is safe — without it, any tool
        # that queries the warehouse live (long_haul_breakdown) raises
        # "SQLite objects created in a thread can only be used in that same
        # thread" once served through the API.
        self.conn = sqlite3.connect(
            f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self.window = WINDOW_LABEL
        self.metrics: dict[str, AirportMetrics] = load_metrics(self.conn)
        self._cohorts: dict[str, Cohort] = {}
        self._sources: list[dict[str, Any]] | None = None
        # Monthly OTP rows for the ACI temporal diagnostic; loaded on demand.
        self._monthly_delay: dict[str, list[MonthlyDelay]] | None = None

    # -- infrastructure ----------------------------------------------------

    def close(self) -> None:
        self.conn.close()

    def sources(self) -> list[dict[str, Any]]:
        """Provenance for every dataset, read from the warehouse registry.

        Citations are generated from recorded data rather than hand-written
        strings, so they cannot drift from what was actually loaded.
        """
        if self._sources is None:
            self._sources = [
                {
                    "dataset": r["dataset"],
                    "source_name": r["source_name"],
                    "source_url": r["source_url"],
                    "license": r["license"],
                    "coverage": f"{r['coverage_start']}..{r['coverage_end']}",
                    "retrieved_at": r["retrieved_at"],
                    "rows": r["row_count"],
                    "notes": r["notes"],
                }
                for r in self.conn.execute(
                    "SELECT * FROM source_registry ORDER BY dataset"
                )
            ]
        return self._sources

    def cohort(
        self, *, hub_class: str | None = None, region: str | None = None
    ) -> Cohort:
        key = f"hub={hub_class}|region={region}"
        if key not in self._cohorts:
            self._cohorts[key] = build_cohort(
                self.metrics, hub_class=hub_class, region=region
            )
        return self._cohorts[key]

    # -- lookups -----------------------------------------------------------

    def get_metrics(self, iata: str) -> AirportMetrics | None:
        return self.metrics.get(iata.upper())

    def resolve_region(self, region: str) -> list[str]:
        return sorted(
            m.iata for m in self.metrics.values()
            if m.region == region and m.has_traffic
        )

    def resolve_states(self, states: list[str]) -> list[str]:
        wanted = {s.upper() for s in states}
        return sorted(
            m.iata for m in self.metrics.values()
            if m.state in wanted and m.has_traffic
        )

    # -- primary operations -------------------------------------------------

    def monthly_delay(self) -> dict[str, list[MonthlyDelay]]:
        """Monthly OTP rows, loaded once on first use.

        Deliberately lazy: ranking never needs it, so startup and the ranking
        path stay exactly as fast as before the diagnostic existed.
        """
        if self._monthly_delay is None:
            self._monthly_delay = load_monthly_delay(
                self.conn, config.WINDOW_START, config.WINDOW_END
            )
        return self._monthly_delay

    def temporal_diagnostic(
        self, iata: str, *, hub_class_cohort: bool = False
    ) -> dict[str, Any] | None:
        """Supplementary ACI temporal diagnostic, or None when unavailable.

        Explains how an existing ACI score is distributed across the window. It
        is not a score, not a component of one, and is never consulted by
        ranking or classification.
        """
        m = self.get_metrics(iata)
        if m is None:
            return None
        rows = self.monthly_delay().get(m.iata)
        if not rows:
            return None
        cohort = self.cohort(hub_class=m.hub_class if hub_class_cohort else None)
        profile = build_profile(m, rows, cohort)
        if profile.annual_aci is None:
            return None          # no ACI to explain
        return profile.to_dict()

    def profile(
        self, iata: str, *, hub_class_cohort: bool = False
    ) -> AirportScores | None:
        m = self.get_metrics(iata)
        if m is None:
            return None
        cohort = self.cohort(hub_class=m.hub_class if hub_class_cohort else None)
        scores = score_airport(
            m, cohort, window=self.window, sources=self.sources()
        )
        # Attached after scoring, never during it: the diagnostic explains the
        # ACI figure and cannot influence it.
        return replace(
            scores,
            temporal=self.temporal_diagnostic(
                iata, hub_class_cohort=hub_class_cohort
            ),
        )

    def _scale(self, m: AirportMetrics) -> dict[str, Any]:
        """Absolute size, reported beside every score.

        TDPI is cohort-relative and growth-weighted, so a very small airport
        can score highly on a few thousand extra passengers. Surfacing absolute
        scale next to the score lets a reader see that immediately instead of
        mistaking a 20-seat-gauge field for a major opportunity. This is
        transparency, not a filter — nothing is excluded on size.
        """
        return {
            "passengers": m.passengers,
            "departures": m.departures,
            "enplanements_cy": m.enplanements,
            "otp_flights": m.flights,
            "seats_per_departure": m.seats_per_departure,
            "hub_class": m.hub_class,
        }

    def rank(
        self,
        iatas: list[str],
        *,
        index: str = "TDPI",
        hub_class_cohort: bool = False,
        min_passengers: float | None = None,
    ) -> dict[str, Any]:
        """Rank a set of airports. Suppressed scores sort last, never as zero."""
        cohort = self.cohort()
        scored: list[AirportScores] = []
        skipped: list[dict[str, str]] = []
        for code in iatas:
            m = self.get_metrics(code)
            if m is None:
                skipped.append({"iata": code, "reason": "not in airport universe"})
                continue
            if not m.has_traffic:
                skipped.append({"iata": code, "reason": "no traffic data in window"})
                continue
            if min_passengers is not None and (m.passengers or 0) < min_passengers:
                skipped.append(
                    {
                        "iata": code,
                        "reason": (
                            f"below requested minimum scale "
                            f"({(m.passengers or 0):,.0f} < {min_passengers:,.0f} passengers)"
                        ),
                    }
                )
                continue
            local = self.cohort(hub_class=m.hub_class) if hub_class_cohort else cohort
            scored.append(
                score_airport(m, local, window=self.window, sources=self.sources())
            )

        key = (lambda s: s.tdpi.score) if index.upper() == "TDPI" else (lambda s: s.aci.score)
        # Sort: scored airports descending, then unscored (alphabetically) —
        # a suppressed score must never be treated as a low score.
        ranked = sorted(
            [s for s in scored if key(s) is not None],
            key=lambda s: (-(key(s) or 0.0), s.iata),
        )
        unscored = sorted(
            [s for s in scored if key(s) is None], key=lambda s: s.iata
        )

        return {
            "index": index.upper(),
            "window": self.window,
            "cohort": cohort.name,
            "cohort_size": cohort.size,
            "ranked": [
                {
                    "rank": i + 1,
                    "scale": self._scale(self.metrics[s.iata]),
                    **s.to_dict(),
                }
                for i, s in enumerate(ranked)
            ],
            "unscored": [
                {
                    "rank": None,
                    "scale": self._scale(self.metrics[s.iata]),
                    **s.to_dict(),
                    "unscored_reason": (
                        s.tdpi.suppressed_reason if index.upper() == "TDPI"
                        else s.aci.suppressed_reason
                    ),
                }
                for s in unscored
            ],
            "skipped": skipped,
            "sources": self.sources(),
            "limitations": GLOBAL_LIMITATIONS + [
                "TDPI is cohort-relative and growth-weighted, so a very small "
                "airport can rank highly on a modest absolute change. Absolute "
                "scale is reported beside every score; read them together.",
            ],
        }

    def compare(self, iatas: list[str], *, hub_class_cohort: bool = False) -> dict[str, Any]:
        """Side-by-side comparison that separates volume from per-flight intensity.

        The split matters: LAX has ~4x SNA's movements but comparable per-flight
        delay. Reporting one 'congestion' number would answer a different
        question than the one asked.
        """
        rows: list[dict[str, Any]] = []
        for code in iatas:
            m = self.get_metrics(code)
            if m is None:
                continue
            s = self.profile(code, hub_class_cohort=hub_class_cohort)
            rows.append(
                {
                    "iata": m.iata,
                    "name": m.name,
                    "state": m.state,
                    "hub_class": m.hub_class,
                    "runway_count": m.runway_count,
                    "longest_runway_ft": m.longest_runway_ft,
                    "volume": {
                        "departures": m.departures,
                        "passengers": m.passengers,
                        "seats": m.seats,
                        "otp_flights": m.flights,
                    },
                    "intensity": {
                        "load_factor": m.load_factor,
                        "seats_per_departure": m.seats_per_departure,
                        "taxi_out_avg_min": m.taxi_out_avg,
                        "nas_delay_per_flight_min": m.nas_delay_per_flight,
                        "dep_del15_rate": m.dep_del15_rate,
                        "cancel_rate": m.cancel_rate,
                        "dep_delay_avg_min": m.dep_delay_avg,
                    },
                    "scores": s.to_dict() if s else None,
                    # Temporal distribution of the ACI figure above. Surfaced at
                    # the row level so a comparison table can show it without
                    # reaching into `scores`; the full monthly series stays in
                    # `scores.temporal`.
                    "temporal": _temporal_summary(s.temporal if s else None),
                }
            )

        return {
            "window": self.window,
            "airports": rows,
            "note": (
                "Volume and per-flight intensity are reported separately. A "
                "larger airport is not automatically 'more congested': high "
                "movement counts and high per-flight delay are different "
                "findings with different investment implications."
            ),
            "sources": self.sources(),
            "limitations": GLOBAL_LIMITATIONS,
        }

    def long_haul(
        self,
        iata: str,
        *,
        month_start: str = config.WINDOW_START,
        month_end: str = config.WINDOW_END,
        **kwargs: Any,
    ) -> LongHaulResult:
        # The only operation that queries the warehouse live rather than
        # reading the in-memory metric table, so it is the one that needs the
        # cross-thread lock.
        with self._lock:
            return long_haul_breakdown(
                self.conn, iata, month_start=month_start, month_end=month_end,
                sources=self.sources(), **kwargs,
            )

    def unmet_demand(self, iata: str) -> UnmetDemandEvidence | None:
        m = self.get_metrics(iata)
        if m is None:
            return None
        return unmet_demand_evidence(
            m, self.cohort(), window=self.window, sources=self.sources()
        )
