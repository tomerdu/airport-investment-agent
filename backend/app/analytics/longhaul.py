"""Long-haul breakdown from per-segment T-100 data.

Two design choices carry the honesty of this answer:

1. **No scalar field exists.** `LongHaulScopeResult` returns a band table.
   A headline share is present but always accompanied by the full sensitivity
   across thresholds, because the ANC answer moves ~18 points between 1,500
   and 3,000 sm — a single unqualified percentage would be a coin flip
   presented as a fact.

2. **The period is explicit and self-describing.** `period`, `period_months`
   and `is_full_window` are always populated, so a single-month result can
   never be mistaken for an annual one.
"""

from __future__ import annotations

import sqlite3

from etl import config

from .definitions import (
    EXCLUSIVE_SCOPES,
    GLOBAL_LIMITATIONS,
    LONG_HAUL_DEFAULT_SM,
    LONG_HAUL_DEFINITION,
    LONG_HAUL_THRESHOLDS_SM,
    LONG_HAUL_UNIT,
    SCOPE_ALL,
    SCOPES,
)
from .models import LongHaulBand, LongHaulResult, LongHaulScopeResult


def _scope_clause(scope: str) -> tuple[str, tuple]:
    label, cfg = SCOPES[scope]
    if cfg is None:
        return "", ()
    return " AND aircraft_config = ?", (cfg,)


def compute_scope(
    conn: sqlite3.Connection,
    iata: str,
    scope: str,
    month_start: str,
    month_end: str,
    thresholds: list[int],
    headline: int,
) -> LongHaulScopeResult:
    clause, params = _scope_clause(scope)
    base = (iata, month_start, month_end, *params)

    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(departures_performed), 0) dep,
               COALESCE(SUM(passengers), 0) pax
        FROM segments
        WHERE origin = ? AND month BETWEEN ? AND ?{clause}
        """,
        base,
    ).fetchone()
    total_dep = float(row["dep"] or 0.0)
    total_pax = float(row["pax"] or 0.0)

    bands: list[LongHaulBand] = []
    for th in thresholds:
        r = conn.execute(
            f"""
            SELECT COALESCE(SUM(departures_performed), 0) dep
            FROM segments
            WHERE origin = ? AND month BETWEEN ? AND ?{clause}
              AND distance_sm >= ?
            """,
            (*base, th),
        ).fetchone()
        dep = float(r["dep"] or 0.0)
        bands.append(
            LongHaulBand(
                threshold_sm=th,
                departures=dep,
                share_pct=(100.0 * dep / total_dep) if total_dep else 0.0,
            )
        )

    headline_share = next(
        (b.share_pct for b in bands if b.threshold_sm == headline), None
    ) if total_dep else None

    return LongHaulScopeResult(
        scope=scope,
        scope_label=SCOPES[scope][0],
        total_departures=total_dep,
        total_passengers=total_pax,
        bands=bands,
        headline_threshold_sm=headline,
        headline_share_pct=headline_share,
    )


def long_haul_breakdown(
    conn: sqlite3.Connection,
    iata: str,
    *,
    month_start: str = config.WINDOW_START,
    month_end: str = config.WINDOW_END,
    thresholds: list[int] | None = None,
    headline_threshold_sm: int = LONG_HAUL_DEFAULT_SM,
    top_n_destinations: int = 10,
    sources: list[dict] | None = None,
) -> LongHaulResult:
    thresholds = sorted(thresholds or LONG_HAUL_THRESHOLDS_SM)
    iata = iata.upper()

    apt = conn.execute(
        "SELECT iata, name FROM airports WHERE iata = ?", (iata,)
    ).fetchone()
    name = apt["name"] if apt else iata

    months = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT month FROM segments WHERE origin = ? "
            "AND month BETWEEN ? AND ? ORDER BY month",
            (iata, month_start, month_end),
        )
    ]
    n_months = len(months)
    full_window = (
        month_start == config.WINDOW_START
        and month_end == config.WINDOW_END
        and n_months == 12
    )
    if n_months == 0:
        period = f"{month_start}..{month_end} (no data)"
    elif n_months == 1:
        period = f"{months[0]} (single month)"
    else:
        period = f"{months[0]}..{months[-1]} ({n_months} months)"

    scopes = [
        compute_scope(conn, iata, s, month_start, month_end, thresholds, headline_threshold_sm)
        for s in ([SCOPE_ALL] + EXCLUSIVE_SCOPES)
    ]

    # The exclusive scopes must account for every departure. Reporting only
    # passenger and all-cargo silently drops combi and amphibious service, so
    # the reconciliation is computed and returned rather than assumed.
    by_scope = {s.scope: s for s in scopes}
    total = by_scope[SCOPE_ALL].total_departures
    parts = sum(by_scope[s].total_departures for s in EXCLUSIVE_SCOPES)
    reconciliation = {
        "total_departures": total,
        "sum_of_exclusive_scopes": parts,
        "residual": total - parts,
        "reconciles": abs(total - parts) < 0.5,
        "breakdown": {
            s: {
                "departures": by_scope[s].total_departures,
                "share_pct": (100.0 * by_scope[s].total_departures / total) if total else 0.0,
                "label": by_scope[s].scope_label,
            }
            for s in EXCLUSIVE_SCOPES
        },
        "note": (
            "Passenger and all-cargo are not exhaustive: T-100 AIRCRAFT_CONFIG "
            "also codes combi aircraft (3 — passengers and freight on the same "
            "main deck) and amphibious aircraft (4). These four scopes together "
            "account for every departure."
        ),
    }

    # Only surface scopes that actually operated here, so a panel is not padded
    # with four empty columns at a typical airport.
    scopes = [s for s in scopes if s.scope == SCOPE_ALL or s.total_departures > 0]

    dests = [
        {
            "dest": r["dest"],
            "departures": float(r["dep"]),
            "distance_sm": float(r["dist"]) if r["dist"] is not None else None,
            "passengers": float(r["pax"] or 0.0),
            "is_long_haul": bool(r["dist"] and r["dist"] >= headline_threshold_sm),
        }
        for r in conn.execute(
            """
            SELECT dest,
                   SUM(departures_performed) dep,
                   MAX(distance_sm) dist,
                   SUM(passengers) pax
            FROM segments
            WHERE origin = ? AND month BETWEEN ? AND ?
            GROUP BY dest
            ORDER BY dep DESC, dest ASC
            LIMIT ?
            """,
            (iata, month_start, month_end, top_n_destinations),
        )
    ]

    limitations = list(GLOBAL_LIMITATIONS)
    limitations.insert(
        0,
        "The reported share depends materially on the distance threshold "
        "chosen; the sensitivity table is part of the answer, not an appendix.",
    )
    limitations.insert(
        1,
        "Unit is departures performed. Seat-share and passenger-share would "
        "give different answers, especially where long sectors are freighters "
        "carrying no passengers.",
    )
    if not full_window:
        limitations.insert(
            2,
            f"This result covers {period} and must not be presented as a "
            f"12-month figure.",
        )

    return LongHaulResult(
        iata=iata,
        name=name,
        period=period,
        period_months=n_months,
        is_full_window=full_window,
        definition=LONG_HAUL_DEFINITION,
        unit=LONG_HAUL_UNIT,
        scopes=scopes,
        reconciliation=reconciliation,
        top_destinations=dests,
        sources=sources or [],
        limitations=limitations,
    )
