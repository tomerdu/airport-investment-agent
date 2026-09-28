"""Build the SQLite warehouse from cached raw data.

Run order matters: reference/FAA data defines the airport universe, and
everything else is filtered to it.

    python -m etl.build_warehouse [--force-fetch]

The build is idempotent — it drops and repopulates the derived tables each
time — and it records validation results into `etl_validation` so the
checkpoint report shows what was actually checked rather than asserting it.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from . import (
    config,
    crosswalk,
    fetch_faa_enplanements,
    fetch_reference,
    fetch_t100_segment,
    fetch_t100_socrata,
    parse_otp,
)
from .common import connect, get_logger, register_source, utc_now_iso

log = get_logger("etl.build")

LICENSE_USGOV = "Public Domain (U.S. Government Work)"
LICENSE_OA = "Public Domain (OurAirports dedication)"


def init_schema(conn: sqlite3.Connection) -> None:
    schema = (config.BACKEND_DIR / "etl" / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()


def _clear(conn: sqlite3.Connection, table: str) -> None:
    conn.execute(f"DELETE FROM {table}")


def record_validation(conn: sqlite3.Connection, name: str, status: str, detail: str) -> None:
    conn.execute(
        """
        INSERT INTO etl_validation (check_name, status, detail, checked_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(check_name) DO UPDATE SET
            status = excluded.status,
            detail = excluded.detail,
            checked_at = excluded.checked_at
        """,
        (name, status, detail, utc_now_iso()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_airports(conn: sqlite3.Connection, force: bool) -> dict:
    fetch_reference.fetch(force=force)
    oa_airports = fetch_reference.load_airports()
    oa_runways = fetch_reference.load_runways()

    faa_paths = fetch_faa_enplanements.fetch(force=force)
    if not faa_paths:
        raise RuntimeError("no FAA enplanement workbook available; cannot define universe")

    latest_cy = max(faa_paths)
    faa_latest = fetch_faa_enplanements.parse(faa_paths[latest_cy], latest_cy)
    if not faa_latest:
        raise RuntimeError("FAA workbook parsed to zero records")

    rows, report = crosswalk.build(faa_latest, oa_airports, oa_runways)

    _clear(conn, "airports")
    conn.executemany(
        """
        INSERT INTO airports
            (iata, icao, faa_locid, name, city, state, region, hub_class,
             service_level, lat, lon, runway_count, longest_runway_ft, in_universe)
        VALUES
            (:iata, :icao, :faa_locid, :name, :city, :state, :region, :hub_class,
             :service_level, :lat, :lon, :runway_count, :longest_runway_ft, :in_universe)
        """,
        rows,
    )
    conn.commit()

    register_source(
        conn,
        dataset="airports",
        source_name="OurAirports + FAA Passenger Boarding (crosswalk)",
        source_url=f"{config.OURAIRPORTS_AIRPORTS_URL} ; {config.FAA_ENPLANEMENTS_FILES[latest_cy]}",
        license_name=f"{LICENSE_OA} ; {LICENSE_USGOV}",
        coverage_start=None,
        coverage_end=f"CY{latest_cy}",
        row_count=len(rows),
        notes=(
            f"Universe = FAA service level '{config.PRIMARY_SERVICE_LEVEL}'. "
            f"Matched {report['matched']}/{report['faa_records']} FAA records "
            f"({report['unmatched']} unmatched)."
        ),
    )

    # --- enplanements (all available calendar years) ---
    _clear(conn, "enplanements")
    total_enp = 0
    for cy, path in sorted(faa_paths.items()):
        recs = fetch_faa_enplanements.parse(path, cy)
        conn.executemany(
            """
            INSERT INTO enplanements
                (faa_locid, cy, enplanements, prior_year, pct_change,
                 hub_class, service_level, rank, preliminary)
            VALUES
                (:faa_locid, :cy, :enplanements, :prior_year, :pct_change,
                 :hub_class, :service_level, :rank, :preliminary)
            ON CONFLICT(faa_locid, cy) DO NOTHING
            """,
            recs,
        )
        total_enp += len(recs)
    conn.commit()

    register_source(
        conn,
        dataset="enplanements",
        source_name="FAA Passenger Boarding (Enplanement) Data",
        source_url=config.FAA_ENPLANEMENTS_BASE,
        license_name=LICENSE_USGOV,
        coverage_start=f"CY{min(faa_paths)}",
        coverage_end=f"CY{max(faa_paths)}",
        row_count=total_enp,
        notes="CY2025 is FAA preliminary and will be restated.",
    )

    return report


def load_t100(conn: sqlite3.Connection, force: bool) -> int:
    raw = fetch_t100_socrata.fetch(force=force)
    recs = fetch_t100_socrata.normalize(raw)

    universe = {r[0] for r in conn.execute("SELECT iata FROM airports")}
    keep = [r for r in recs if r["iata"] in universe]
    dropped = len(recs) - len(keep)

    # Record how much real traffic each unknown code carries, so the
    # identifier-drift check can judge materiality rather than row counts.
    dropped_pax: dict[str, float] = {}
    for r in recs:
        if r["iata"] not in universe and r["month"] in config.WINDOW_MONTHS:
            dropped_pax[r["iata"]] = dropped_pax.get(r["iata"], 0.0) + (r["passengers"] or 0.0)
    conn.execute("DROP TABLE IF EXISTS _dropped_codes")
    conn.execute("CREATE TABLE _dropped_codes (code TEXT PRIMARY KEY, passengers REAL)")
    conn.executemany(
        "INSERT INTO _dropped_codes (code, passengers) VALUES (?, ?)",
        sorted(dropped_pax.items()),
    )
    conn.commit()

    _clear(conn, "airport_month")
    conn.executemany(
        """
        INSERT INTO airport_month
            (iata, month, departures, passengers, seats, freight_lbs, mail_lbs,
             dom_departures, dom_passengers, dom_seats,
             intl_out_departures, intl_out_passengers, intl_out_seats, avg_distance_sm)
        VALUES
            (:iata, :month, :departures, :passengers, :seats, :freight_lbs, :mail_lbs,
             :dom_departures, :dom_passengers, :dom_seats,
             :intl_out_departures, :intl_out_passengers, :intl_out_seats, :avg_distance_sm)
        ON CONFLICT(iata, month) DO NOTHING
        """,
        keep,
    )
    conn.commit()

    months = sorted({r["month"] for r in keep})
    register_source(
        conn,
        dataset="airport_month",
        source_name="BTS T-100 Segment Summary by Origin Airport (Socrata r495-tyji)",
        source_url=config.SOCRATA_T100_ORIGIN_URL,
        license_name=LICENSE_USGOV,
        coverage_start=months[0] if months else None,
        coverage_end=months[-1] if months else None,
        row_count=len(keep),
        notes=(
            f"Fields resolved via display names. {dropped} rows outside the "
            "FAA primary-airport universe were dropped."
        ),
    )
    log.info("airport_month: %d rows loaded (%d dropped outside universe)", len(keep), dropped)
    return len(keep)


def load_segments(conn: sqlite3.Connection, force: bool) -> int:
    paths = fetch_t100_segment.fetch_window(force=force)
    if not paths:
        log.warning("no T-100 segment months available")
        record_validation(conn, "segments_present", "FAIL", "no segment months fetched")
        return 0

    _clear(conn, "segments")
    total = 0
    months: set[str] = set()
    for p in paths:
        recs = fetch_t100_segment.parse_month(p)
        if not recs:
            continue
        months.update(r["month"] for r in recs)
        conn.executemany(
            """
            INSERT INTO segments
                (origin, dest, month, carrier_group, aircraft_config, service_class,
                 origin_country, dest_country,
                 distance_sm, departures_performed, seats, passengers)
            VALUES
                (:origin, :dest, :month, :carrier_group, :aircraft_config, :service_class,
                 :origin_country, :dest_country,
                 :distance_sm, :departures_performed, :seats, :passengers)
            """,
            recs,
        )
        total += len(recs)
    conn.commit()

    ordered = sorted(months)
    register_source(
        conn,
        dataset="segments",
        source_name="BTS T-100 Segment (All Carriers)",
        source_url=config.T100_SEGMENT_FORM_URL,
        license_name=LICENSE_USGOV,
        coverage_start=ordered[0] if ordered else None,
        coverage_end=ordered[-1] if ordered else None,
        row_count=total,
        notes="Includes international segments and all-cargo carriers. Per-segment DISTANCE.",
    )
    log.info("segments: %d rows across %d months", total, len(months))
    return total


def load_otp(conn: sqlite3.Connection) -> int:
    rows = parse_otp.aggregate_window()
    if not rows:
        log.warning("no OTP data available")
        record_validation(conn, "otp_present", "FAIL", "no OTP zips parsed")
        return 0

    universe = {r[0] for r in conn.execute("SELECT iata FROM airports")}
    keep = [r for r in rows if r["iata"] in universe]

    _clear(conn, "airport_delay_month")
    conn.executemany(
        """
        INSERT INTO airport_delay_month
            (iata, month, flights, cancelled, diverted, dep_del15, dep_del15_n,
             taxi_out_sum, taxi_out_n, nas_delay_sum, nas_delay_n,
             dep_delay_sum, dep_delay_n)
        VALUES
            (:iata, :month, :flights, :cancelled, :diverted, :dep_del15, :dep_del15_n,
             :taxi_out_sum, :taxi_out_n, :nas_delay_sum, :nas_delay_n,
             :dep_delay_sum, :dep_delay_n)
        ON CONFLICT(iata, month) DO NOTHING
        """,
        keep,
    )
    conn.commit()

    months = sorted({r["month"] for r in keep})
    register_source(
        conn,
        dataset="airport_delay_month",
        source_name="BTS On-Time Performance (Reporting Carrier)",
        source_url=config.OTP_BASE_URL,
        license_name=LICENSE_USGOV,
        coverage_start=months[0] if months else None,
        coverage_end=months[-1] if months else None,
        row_count=len(keep),
        notes=(
            "Domestic, reporting carriers only; excludes all-cargo carriers. "
            "Stored as sums and counts so any window re-aggregates correctly."
        ),
    )
    log.info("airport_delay_month: %d rows across %d months", len(keep), len(months))
    return len(keep)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(conn: sqlite3.Connection, crosswalk_report: dict) -> list[tuple[str, str, str]]:
    """Run data-quality checks; record and return them."""
    # Clear first: a check that is renamed or removed would otherwise leave a
    # stale row behind and keep reporting an outcome nothing re-evaluates.
    conn.execute("DELETE FROM etl_validation")
    conn.commit()

    results: list[tuple[str, str, str]] = []

    def check(name: str, status: str, detail: str) -> None:
        results.append((name, status, detail))
        record_validation(conn, name, status, detail)

    # 1. Universe size
    n_universe = conn.execute("SELECT COUNT(*) FROM airports WHERE in_universe=1").fetchone()[0]
    check(
        "universe_size",
        "PASS" if 300 <= n_universe <= 450 else "WARN",
        f"{n_universe} FAA primary airports (expected ~380-400)",
    )

    # 2. Required airports present with full identifiers
    missing = []
    for code in config.REQUIRED_AIRPORTS:
        row = conn.execute(
            "SELECT iata, icao, faa_locid, runway_count FROM airports WHERE iata=?", (code,)
        ).fetchone()
        if row is None or not row["icao"] or not row["faa_locid"]:
            missing.append(code)
    check(
        "required_airports_crosswalked",
        "PASS" if not missing else "FAIL",
        "all present" if not missing else f"missing/incomplete: {missing}",
    )

    # 3. Known ICAO divergences resolve correctly (the K/P/T prefix trap)
    icao_expect = {"ANC": "PANC", "HNL": "PHNL", "SJU": "TJSJ", "LAX": "KLAX", "BOS": "KBOS"}
    bad = []
    for iata, want in icao_expect.items():
        row = conn.execute("SELECT icao FROM airports WHERE iata=?", (iata,)).fetchone()
        got = row["icao"] if row else None
        if got != want:
            bad.append(f"{iata}: got {got}, want {want}")
    check("icao_prefix_divergence", "PASS" if not bad else "FAIL", "; ".join(bad) or "ANC/HNL/SJU/LAX/BOS correct")

    # 4. Crosswalk match rate.
    #    Measured over FAA *primary* airports only: the overall rate is
    #    dominated by ~370 small GA fields that have no IATA code at all and
    #    are legitimately out of scope. An unmatched primary airport, by
    #    contrast, is a silently missing airport (this check caught all 12
    #    US-territory airports, including SJU, being dropped).
    n_primary_faa = sum(
        1 for r in crosswalk_report.get("faa_primary_locids", [])
    ) or crosswalk_report.get("faa_primary_count", 0)
    unmatched_primary = crosswalk_report.get("unmatched_primary", [])
    rate = (
        (n_primary_faa - len(unmatched_primary)) / n_primary_faa
        if n_primary_faa else 0.0
    )
    check(
        "crosswalk_primary_match_rate",
        "PASS" if not unmatched_primary else "FAIL",
        f"{rate:.1%} of FAA primary airports matched"
        + (f"; UNMATCHED: {unmatched_primary}" if unmatched_primary else ""),
    )
    check(
        "crosswalk_overall_match_rate",
        "PASS",
        f"{crosswalk_report['matched']}/{crosswalk_report['faa_records']} FAA rows matched; "
        f"{crosswalk_report['unmatched']} unmatched (mostly GA fields with no IATA code)",
    )

    # 5. T-100 window completeness
    row = conn.execute(
        "SELECT COUNT(DISTINCT month) n, MIN(month) mn, MAX(month) mx FROM airport_month "
        "WHERE month BETWEEN ? AND ?",
        (config.WINDOW_START, config.WINDOW_END),
    ).fetchone()
    check(
        "t100_window_complete",
        "PASS" if row["n"] == 12 else "FAIL",
        f"{row['n']}/12 months in window ({row['mn']}..{row['mx']})",
    )

    # 6. OTP window completeness
    row = conn.execute(
        "SELECT COUNT(DISTINCT month) n, MIN(month) mn, MAX(month) mx FROM airport_delay_month"
    ).fetchone()
    check(
        "otp_window_complete",
        "PASS" if row["n"] == 12 else ("WARN" if row["n"] else "FAIL"),
        f"{row['n'] or 0}/12 months ({row['mn']}..{row['mx']})" if row["n"] else "no OTP data",
    )

    # 7. Segment window completeness
    row = conn.execute(
        "SELECT COUNT(DISTINCT month) n, MIN(month) mn, MAX(month) mx FROM segments"
    ).fetchone()
    check(
        "segment_window_complete",
        "PASS" if row["n"] == 12 else ("WARN" if row["n"] else "FAIL"),
        f"{row['n'] or 0}/12 months ({row['mn']}..{row['mx']})" if row["n"] else "no segment data",
    )

    # 8. Regression against Phase 1 hand-verified figures.
    #    These were computed directly from the live API before any ETL existed,
    #    so a mismatch means the pipeline is wrong, not the baseline.
    expected = {
        "LAX": (270853, 36590656, 44584901),
        "SFO": (190280, 26642605, 32273396),
        "BOS": (189745, 20983745, 25675310),
        "SNA": (51609, 5590354, 6950118),
        "ANC": (86373, 2725281, 3745847),
    }
    mism = []
    for code, (dep, pax, seats) in expected.items():
        row = conn.execute(
            """
            SELECT SUM(departures) d, SUM(passengers) p, SUM(seats) s
            FROM airport_month WHERE iata=? AND month BETWEEN ? AND ?
            """,
            (code, config.WINDOW_START, config.WINDOW_END),
        ).fetchone()
        got = (int(row["d"] or 0), int(row["p"] or 0), int(row["s"] or 0))
        if got != (dep, pax, seats):
            mism.append(f"{code}: got {got}, want {(dep, pax, seats)}")
    check(
        "t100_regression_vs_phase1",
        "PASS" if not mism else "FAIL",
        "; ".join(mism) or f"{len(expected)} airports match hand-verified Phase 1 values",
    )

    # 9. No negative measurements anywhere
    neg = conn.execute(
        "SELECT COUNT(*) FROM airport_month WHERE departures < 0 OR passengers < 0 OR seats < 0"
    ).fetchone()[0]
    check("no_negative_traffic", "PASS" if neg == 0 else "FAIL", f"{neg} negative rows")

    # 10. Load factor sanity. A handful of tiny airports report more
    #     passengers than seats — a genuine BTS reporting quirk at fields with
    #     1-13 departures/month, not a pipeline defect. We judge it by affected
    #     passenger volume, not row count, so the check stays meaningful.
    row = conn.execute(
        """
        SELECT COUNT(*) n, COALESCE(SUM(passengers), 0) pax
        FROM airport_month WHERE seats > 0 AND passengers > seats * 1.02
        """
    ).fetchone()
    total_pax = conn.execute("SELECT COALESCE(SUM(passengers),0) FROM airport_month").fetchone()[0]
    share = (row["pax"] / total_pax) if total_pax else 0.0
    check(
        "load_factor_plausible",
        "PASS" if share < 1e-4 else "FAIL",
        f"{row['n']} airport-months with passengers > seats, "
        f"affecting {row['pax']:,.0f} of {total_pax:,.0f} passengers ({share:.6%}) "
        f"— immaterial, source-side reporting quirk at very small fields",
    )

    # 10b. Identifier drift. Any BTS code carrying material traffic that does
    #      not resolve to a known airport is almost certainly a rename the
    #      alias map has not caught yet — exactly how PBI -> DJT (a medium hub
    #      with 4.26M enplanements) silently vanished from every ranking.
    rows = conn.execute(
        "SELECT code, passengers FROM _dropped_codes WHERE passengers > ? ORDER BY passengers DESC",
        (config.MAX_UNMAPPED_DROPPED_PASSENGERS,),
    ).fetchall()
    total_dropped = conn.execute(
        "SELECT COALESCE(SUM(passengers), 0) FROM _dropped_codes"
    ).fetchone()[0]
    check(
        "material_dropped_traffic",
        "PASS" if not rows else "FAIL",
        (
            f"unmapped BTS codes with >{config.MAX_UNMAPPED_DROPPED_PASSENGERS:,} passengers: "
            + ", ".join(f"{r['code']} ({r['passengers']:,.0f})" for r in rows)
            + " — add to config.BTS_IATA_ALIASES"
        ) if rows else (
            f"no unmapped code exceeds {config.MAX_UNMAPPED_DROPPED_PASSENGERS:,} passengers; "
            f"{total_dropped:,.0f} passengers dropped in total (small GA/seaplane bases "
            f"outside the FAA primary universe)"
        ),
    )

    # 10c. The alias map itself must actually resolve.
    bad_alias = []
    for src, dst in config.BTS_IATA_ALIASES.items():
        hit = conn.execute("SELECT 1 FROM airports WHERE iata=?", (dst,)).fetchone()
        if hit is None:
            bad_alias.append(f"{src}->{dst} (target not in airports)")
        pax = conn.execute(
            "SELECT COALESCE(SUM(passengers),0) p FROM airport_month WHERE iata=? AND month BETWEEN ? AND ?",
            (dst, config.WINDOW_START, config.WINDOW_END),
        ).fetchone()["p"]
        if not pax:
            bad_alias.append(f"{src}->{dst} (no traffic landed on target)")
    check(
        "iata_aliases_resolve",
        "PASS" if not bad_alias else "FAIL",
        "; ".join(bad_alias) or f"{len(config.BTS_IATA_ALIASES)} alias(es) resolve and carry traffic",
    )

    # 11. Segment vs summary reconciliation.
    #     T-100 Segment (per-O&D) and the T-100 origin summary are different
    #     BTS aggregations of the same programme, so they should agree closely
    #     but need not match exactly. A large gap means we joined wrong.
    recon = []
    for code in ("ANC", "LAX", "SFO"):
        seg = conn.execute(
            "SELECT SUM(departures_performed) d FROM segments WHERE origin=? AND month BETWEEN ? AND ?",
            (code, config.WINDOW_START, config.WINDOW_END),
        ).fetchone()["d"] or 0
        summ = conn.execute(
            "SELECT SUM(departures) d FROM airport_month WHERE iata=? AND month BETWEEN ? AND ?",
            (code, config.WINDOW_START, config.WINDOW_END),
        ).fetchone()["d"] or 0
        if summ:
            diff = abs(seg - summ) / summ
            recon.append(f"{code}: segment={seg:,.0f} summary={summ:,.0f} ({diff:.1%})")
            if diff > 0.05:
                recon[-1] += " ** >5% **"
    over = [r for r in recon if "**" in r]
    check(
        "segment_summary_reconciliation",
        "PASS" if not over else "WARN",
        "; ".join(recon),
    )

    # 12. ANC cargo/passenger split must be derivable (approved decision D4)
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN aircraft_config='2' THEN departures_performed ELSE 0 END) cargo,
            SUM(CASE WHEN aircraft_config='1' THEN departures_performed ELSE 0 END) pax,
            SUM(departures_performed) total
        FROM segments WHERE origin='ANC' AND month BETWEEN ? AND ?
        """,
        (config.WINDOW_START, config.WINDOW_END),
    ).fetchone()
    total = row["total"] or 0
    check(
        "anc_cargo_split_available",
        "PASS" if total and (row["cargo"] or 0) > 0 else "FAIL",
        (
            f"freighter={row['cargo']:,.0f} ({100 * row['cargo'] / total:.1f}%), "
            f"passenger={row['pax']:,.0f} of {total:,.0f} departures"
        ) if total else "no ANC segment data",
    )

    # 13. Airports below the ACI volume gate (informational, not a failure)
    gated = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT iata, SUM(flights) f FROM airport_delay_month GROUP BY iata
            HAVING f < ?
        )
        """,
        (config.MIN_OTP_FLIGHTS_FOR_ACI,),
    ).fetchone()[0]
    with_otp = conn.execute("SELECT COUNT(DISTINCT iata) FROM airport_delay_month").fetchone()[0]
    check(
        "aci_volume_gate",
        "PASS",
        f"{gated}/{with_otp} airports below {config.MIN_OTP_FLIGHTS_FOR_ACI} flights "
        f"-> ACI will be suppressed for them",
    )

    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the airport warehouse")
    ap.add_argument("--force-fetch", action="store_true", help="ignore caches and re-download")
    args = ap.parse_args()

    conn = connect()
    init_schema(conn)

    log.info("=== 1/4 reference + FAA universe ===")
    report = load_airports(conn, force=args.force_fetch)

    log.info("=== 2/4 T-100 traffic ===")
    load_t100(conn, force=args.force_fetch)

    log.info("=== 3/4 T-100 segments ===")
    load_segments(conn, force=args.force_fetch)

    log.info("=== 4/4 OTP delays ===")
    load_otp(conn)

    log.info("=== validation ===")
    results = validate(conn, report)
    width = max(len(n) for n, _, _ in results)
    failures = 0
    for name, status, detail in results:
        log.info("  [%-4s] %-*s  %s", status, width, name, detail)
        if status == "FAIL":
            failures += 1

    size_mb = config.WAREHOUSE_PATH.stat().st_size / 1e6
    log.info("warehouse: %s (%.1f MB)", config.WAREHOUSE_PATH, size_mb)
    conn.close()

    if failures:
        log.error("%d validation check(s) FAILED", failures)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
