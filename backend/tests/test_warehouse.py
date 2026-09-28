"""Integration tests against the built warehouse.

These are the tests that would catch a silently-wrong pipeline: identifier
joins, window discipline, provenance completeness, and regression against the
Phase 1 figures that were hand-verified directly from the live APIs before any
ETL code existed.

Skipped (not failed) when the warehouse has not been built yet.
"""

from __future__ import annotations

import sqlite3

import pytest

from etl import config

pytestmark = pytest.mark.skipif(
    not config.WAREHOUSE_PATH.exists(), reason="warehouse not built yet"
)


@pytest.fixture(scope="module")
def conn():
    c = sqlite3.connect(config.WAREHOUSE_PATH)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Universe and crosswalk
# ---------------------------------------------------------------------------


def test_airport_universe_is_roughly_380_primary_airports(conn):
    n = conn.execute("SELECT COUNT(*) FROM airports WHERE in_universe=1").fetchone()[0]
    assert 300 <= n <= 450, f"expected ~380-400 FAA primary airports, got {n}"


@pytest.mark.parametrize("code", config.REQUIRED_AIRPORTS)
def test_required_airports_present_and_fully_crosswalked(conn, code):
    row = conn.execute(
        "SELECT iata, icao, faa_locid, name, state FROM airports WHERE iata=?", (code,)
    ).fetchone()
    assert row is not None, f"{code} missing from airports"
    assert row["icao"], f"{code} has no ICAO"
    assert row["faa_locid"], f"{code} has no FAA Locid"


@pytest.mark.parametrize(
    "iata,icao",
    [("ANC", "PANC"), ("HNL", "PHNL"), ("SJU", "TJSJ"), ("LAX", "KLAX"), ("BOS", "KBOS")],
)
def test_icao_prefix_divergence_resolved(conn, iata, icao):
    """Non-contiguous US airports use P/T prefixes, not K. A wrong prefix here
    attaches the wrong runway inventory to the airport."""
    row = conn.execute("SELECT icao FROM airports WHERE iata=?", (iata,)).fetchone()
    assert row is not None and row["icao"] == icao


@pytest.mark.parametrize(
    "iata,icao,name",
    [
        ("SJU", "TJSJ", "San Juan"),
        ("GUM", "PGUM", "Guam"),
        ("STT", "TIST", "St Thomas"),
        ("BQN", "TJBQ", "Aguadilla"),
    ],
)
def test_us_territory_airports_present(conn, iata, icao, name):
    """Regression guard: OurAirports codes territories as PR/VI/GU/MP/AS
    rather than US, which silently dropped all 12 FAA-primary territory
    airports (SJU alone carries ~6.7M enplanements)."""
    row = conn.execute(
        "SELECT iata, icao, in_universe FROM airports WHERE iata=?", (iata,)
    ).fetchone()
    assert row is not None, f"{iata} ({name}) missing — territory filter regression"
    assert row["icao"] == icao
    assert row["in_universe"] == 1


def test_renamed_airport_carries_its_traffic(conn):
    """PBI -> DJT identifier drift. BTS reports the old code, the FAA
    workbook the new one; without the alias the airport exists in the
    universe with no traffic at all."""
    row = conn.execute(
        """
        SELECT SUM(passengers) pax, COUNT(DISTINCT month) months
        FROM airport_month WHERE iata='DJT' AND month BETWEEN ? AND ?
        """,
        (config.WINDOW_START, config.WINDOW_END),
    ).fetchone()
    assert row["months"] == 12, "DJT should carry a full window of traffic via the PBI alias"
    assert row["pax"] > 3_000_000, f"DJT passengers implausibly low: {row['pax']}"

    # The old code must not also be present, or the airport is double-counted.
    stale = conn.execute("SELECT COUNT(*) FROM airport_month WHERE iata='PBI'").fetchone()[0]
    assert stale == 0, "PBI rows survived aliasing — airport would be double-counted"


def test_no_material_traffic_is_dropped_as_unknown(conn):
    """Guards the whole identifier-drift class, not just the PBI instance."""
    row = conn.execute(
        "SELECT status, detail FROM etl_validation WHERE check_name='material_dropped_traffic'"
    ).fetchone()
    if row is None:
        pytest.skip("check not recorded")
    assert row["status"] == "PASS", row["detail"]


def test_no_faa_primary_airport_is_silently_dropped(conn):
    """The universe must be within a couple of airports of the FAA primary
    count; a larger gap means airports vanished without an error."""
    n = conn.execute("SELECT COUNT(*) FROM airports WHERE in_universe=1").fetchone()[0]
    row = conn.execute(
        "SELECT status, detail FROM etl_validation WHERE check_name='crosswalk_primary_match_rate'"
    ).fetchone()
    if row is not None:
        assert row["status"] == "PASS", f"unmatched primary airports: {row['detail']}"
    assert n >= 395, f"only {n} primary airports in universe"


def test_iata_codes_are_unique(conn):
    dupes = conn.execute(
        "SELECT iata, COUNT(*) n FROM airports GROUP BY iata HAVING n > 1"
    ).fetchall()
    assert not dupes, f"duplicate IATA codes: {[d['iata'] for d in dupes]}"


def test_new_england_cohort_resolves(conn):
    rows = conn.execute(
        "SELECT iata FROM airports WHERE region='new_england' AND in_universe=1"
    ).fetchall()
    found = {r["iata"] for r in rows}
    for code in ("BOS", "BDL", "PVD", "MHT", "PWM", "BTV"):
        assert code in found, f"{code} not in New England cohort"


def test_runway_inventory_matches_known_values(conn):
    """Hand-checked against OurAirports during Phase 1."""
    expected = {
        "LAX": (4, 12894), "SFO": (4, 11870), "BOS": (6, 10083),
        "SNA": (2, 5700), "ANC": (3, 12400),
    }
    for code, (count, longest) in expected.items():
        row = conn.execute(
            "SELECT runway_count, longest_runway_ft FROM airports WHERE iata=?", (code,)
        ).fetchone()
        assert row["runway_count"] == count, f"{code} runway count"
        assert row["longest_runway_ft"] == longest, f"{code} longest runway"


def test_sna_runway_is_short_enough_to_constrain_long_haul(conn):
    """Substantive check, not a tautology: SNA's 5,700 ft primary is the
    physical reason it cannot host the long-haul widebody flying LAX does."""
    row = conn.execute("SELECT longest_runway_ft FROM airports WHERE iata='SNA'").fetchone()
    assert row["longest_runway_ft"] < 7000
    lax = conn.execute("SELECT longest_runway_ft FROM airports WHERE iata='LAX'").fetchone()
    assert lax["longest_runway_ft"] > 12000


# ---------------------------------------------------------------------------
# Window discipline (approved correction C)
# ---------------------------------------------------------------------------


def test_traffic_table_holds_analysis_window_plus_prior_year(conn):
    """The warehouse deliberately stores 24 months, not 12.

    TDPI component T2 is year-over-year passenger growth, so the prior 12
    months must be present. The *analysis window* is still 12 months — that
    distinction is enforced by the next two tests, not by truncating storage.
    """
    row = conn.execute(
        "SELECT MIN(month) mn, MAX(month) mx, COUNT(DISTINCT month) n FROM airport_month"
    ).fetchone()
    assert row["mn"] == config.PRIOR_WINDOW_START
    assert row["mx"] == config.WINDOW_END
    assert row["n"] == 24


def test_analysis_window_is_exactly_twelve_months_of_traffic(conn):
    n = conn.execute(
        "SELECT COUNT(DISTINCT month) FROM airport_month WHERE month BETWEEN ? AND ?",
        (config.WINDOW_START, config.WINDOW_END),
    ).fetchone()[0]
    assert n == 12


def test_traffic_never_extends_beyond_the_window_end(conn):
    """Nothing later than the pinned window end may exist, or a future ETL run
    could silently widen the window under the scoring engine."""
    n = conn.execute(
        "SELECT COUNT(*) FROM airport_month WHERE month > ?", (config.WINDOW_END,)
    ).fetchone()[0]
    assert n == 0


def test_delay_data_never_exceeds_the_pinned_window(conn):
    """OTP publishes through 2026-07 but must be truncated to 2026-04 so no
    answer mixes a July delay figure with an April traffic figure."""
    row = conn.execute(
        "SELECT MIN(month) mn, MAX(month) mx FROM airport_delay_month"
    ).fetchone()
    if row["mn"] is None:
        pytest.skip("no OTP data loaded")
    assert row["mn"] >= config.WINDOW_START
    assert row["mx"] <= config.WINDOW_END


def test_segments_confined_to_window(conn):
    row = conn.execute("SELECT MIN(month) mn, MAX(month) mx FROM segments").fetchone()
    if row["mn"] is None:
        pytest.skip("no segment data loaded")
    assert row["mn"] >= config.WINDOW_START
    assert row["mx"] <= config.WINDOW_END


# ---------------------------------------------------------------------------
# Regression vs Phase 1 hand-verified values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,departures,passengers,seats",
    [
        ("LAX", 270853, 36590656, 44584901),
        ("SFO", 190280, 26642605, 32273396),
        ("BOS", 189745, 20983745, 25675310),
        ("SNA", 51609, 5590354, 6950118),
        ("ANC", 86373, 2725281, 3745847),
    ],
)
def test_t100_regression_against_phase1(conn, code, departures, passengers, seats):
    """These were computed straight from the live Socrata API before the ETL
    existed. A mismatch means the pipeline is wrong, not the baseline."""
    row = conn.execute(
        """
        SELECT SUM(departures) d, SUM(passengers) p, SUM(seats) s
        FROM airport_month WHERE iata=? AND month BETWEEN ? AND ?
        """,
        (code, config.WINDOW_START, config.WINDOW_END),
    ).fetchone()
    assert int(row["d"]) == departures
    assert int(row["p"]) == passengers
    assert int(row["s"]) == seats


# ---------------------------------------------------------------------------
# Data integrity
# ---------------------------------------------------------------------------


def test_no_negative_measurements(conn):
    n = conn.execute(
        """
        SELECT COUNT(*) FROM airport_month
        WHERE departures < 0 OR passengers < 0 OR seats < 0
        """
    ).fetchone()[0]
    assert n == 0


def test_passengers_exceeding_seats_is_immaterial(conn):
    """A handful of very small fields report more passengers than seats.

    Investigated: 5 airport-months, 61 passengers out of ~1.95 billion, all at
    airports with 1-13 departures/month. That is a source-side BTS reporting
    quirk, not a pipeline defect, so the test judges affected *volume* rather
    than row count — a count-based assertion would either fail permanently or
    have to be silenced.
    """
    row = conn.execute(
        """
        SELECT COUNT(*) n, COALESCE(SUM(passengers), 0) pax
        FROM airport_month WHERE seats > 0 AND passengers > seats * 1.02
        """
    ).fetchone()
    total = conn.execute("SELECT COALESCE(SUM(passengers),0) FROM airport_month").fetchone()[0]
    share = row["pax"] / total if total else 0.0
    assert share < 1e-4, f"{row['n']} rows affecting {share:.6%} of passengers"


def test_no_scored_airport_has_impossible_load_factor(conn):
    """The materiality allowance above must not hide a bad row at an airport
    that actually gets scored."""
    bad = conn.execute(
        """
        SELECT m.iata, m.month, m.passengers, m.seats
        FROM airport_month m JOIN airports a ON a.iata = m.iata
        WHERE a.in_universe = 1 AND a.hub_class IN ('L','M','S')
          AND m.seats > 0 AND m.passengers > m.seats * 1.02
        """
    ).fetchall()
    assert not bad, f"implausible load factor at hub airports: {[dict(r) for r in bad]}"


def test_delay_counts_never_exceed_flight_counts(conn):
    n = conn.execute(
        """
        SELECT COUNT(*) FROM airport_delay_month
        WHERE cancelled > flights OR dep_del15 > flights OR taxi_out_n > flights
        """
    ).fetchone()[0]
    assert n == 0


def test_all_traffic_rows_reference_a_known_airport(conn):
    orphans = conn.execute(
        "SELECT COUNT(*) FROM airport_month WHERE iata NOT IN (SELECT iata FROM airports)"
    ).fetchone()[0]
    assert orphans == 0


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_every_populated_table_has_a_registered_source(conn):
    for table in ("airports", "airport_month", "enplanements"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if n == 0:
            continue
        src = conn.execute(
            "SELECT * FROM source_registry WHERE dataset=?", (table,)
        ).fetchone()
        assert src is not None, f"{table} has no source_registry entry"
        assert src["source_url"], f"{table} source has no URL"
        assert src["retrieved_at"], f"{table} source has no retrieved_at"
        assert src["license"], f"{table} source has no licence"


def test_source_registry_reports_actual_loaded_coverage(conn):
    """The registry must state what is really in the table, not the analysis
    window. Citations are generated from it, so an optimistic coverage string
    would be a false claim about the data."""
    row = conn.execute(
        "SELECT coverage_start, coverage_end FROM source_registry WHERE dataset='airport_month'"
    ).fetchone()
    actual = conn.execute(
        "SELECT MIN(month) mn, MAX(month) mx FROM airport_month"
    ).fetchone()
    assert row["coverage_start"] == actual["mn"]
    assert row["coverage_end"] == actual["mx"]
    # ...and the analysis window must fall inside it.
    assert row["coverage_start"] <= config.WINDOW_START
    assert row["coverage_end"] >= config.WINDOW_END


def test_delay_source_coverage_matches_pinned_window(conn):
    row = conn.execute(
        "SELECT coverage_start, coverage_end FROM source_registry WHERE dataset='airport_delay_month'"
    ).fetchone()
    if row is None:
        pytest.skip("no OTP source registered")
    assert row["coverage_start"] == config.WINDOW_START
    assert row["coverage_end"] == config.WINDOW_END


# ---------------------------------------------------------------------------
# Segment data — long-haul and the cargo split (approved decisions D3/D4)
# ---------------------------------------------------------------------------


def test_anc_long_haul_is_computable_and_threshold_sensitive(conn):
    """The whole point of the sensitivity table: the answer must move
    materially between thresholds, or we are hiding a definitional choice."""
    total = conn.execute(
        "SELECT SUM(departures_performed) d FROM segments WHERE origin='ANC' "
        "AND month BETWEEN ? AND ?",
        (config.WINDOW_START, config.WINDOW_END),
    ).fetchone()["d"]
    if not total:
        pytest.skip("no ANC segment data")

    shares = {}
    for th in config.LONG_HAUL_THRESHOLDS_SM:
        lh = conn.execute(
            "SELECT SUM(departures_performed) d FROM segments WHERE origin='ANC' "
            "AND month BETWEEN ? AND ? AND distance_sm >= ?",
            (config.WINDOW_START, config.WINDOW_END, th),
        ).fetchone()["d"] or 0
        shares[th] = 100 * lh / total

    # Monotonically non-increasing as the threshold rises.
    values = [shares[t] for t in sorted(shares)]
    assert values == sorted(values, reverse=True)
    # And the choice genuinely matters.
    assert shares[1500] - shares[3000] > 10


def test_anc_cargo_and_passenger_activity_are_separable(conn):
    """Approved decision D4: cargo must be distinguishable from passenger
    aviation, not silently blended."""
    rows = conn.execute(
        """
        SELECT aircraft_config,
               SUM(departures_performed) dep,
               SUM(COALESCE(passengers,0)) pax
        FROM segments WHERE origin='ANC' AND month BETWEEN ? AND ?
        GROUP BY aircraft_config
        """,
        (config.WINDOW_START, config.WINDOW_END),
    ).fetchall()
    if not rows:
        pytest.skip("no ANC segment data")

    by_cfg = {r["aircraft_config"]: r for r in rows}
    assert "1" in by_cfg and "2" in by_cfg, "expected both passenger and cargo configs"
    # Freighters carry essentially no passengers — this is what makes the
    # config field a trustworthy discriminator.
    assert by_cfg["2"]["pax"] < 100
    assert by_cfg["1"]["pax"] > 1_000_000
    # And freight is a large share of ANC movements.
    total_dep = sum(r["dep"] for r in rows)
    assert by_cfg["2"]["dep"] / total_dep > 0.4


def test_december_2025_research_figure_is_not_the_window_figure(conn):
    """Approved correction C: the Phase 1 example (34.3% at >=3,000 sm for
    December 2025 alone) must not be reused as a 12-month result."""
    def share(where, params):
        total = conn.execute(
            f"SELECT SUM(departures_performed) d FROM segments WHERE origin='ANC' AND {where}",
            params,
        ).fetchone()["d"]
        lh = conn.execute(
            f"SELECT SUM(departures_performed) d FROM segments WHERE origin='ANC' AND {where} "
            "AND distance_sm >= 3000",
            params,
        ).fetchone()["d"] or 0
        return 100 * lh / total if total else None

    dec = share("month = '2025-12'", ())
    win = share("month BETWEEN ? AND ?", (config.WINDOW_START, config.WINDOW_END))
    if dec is None or win is None:
        pytest.skip("no ANC segment data")
    assert abs(dec - win) > 1.0, (
        "single-month and 12-month long-haul shares should differ; "
        "reusing the research figure would misstate the window"
    )
