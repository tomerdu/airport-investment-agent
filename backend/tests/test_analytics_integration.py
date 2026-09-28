"""End-to-end numerical checks against the real SQLite warehouse.

These verify that the engine's numbers reconcile with independently-written
SQL over the same data — the check that a unit test with synthetic inputs
cannot make.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.analytics import AnalyticsEngine
from etl import config

pytestmark = pytest.mark.skipif(
    not config.WAREHOUSE_PATH.exists(), reason="warehouse not built"
)


@pytest.fixture(scope="module")
def engine():
    e = AnalyticsEngine()
    yield e
    e.close()


@pytest.fixture(scope="module")
def raw():
    c = sqlite3.connect(config.WAREHOUSE_PATH)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


W = (config.WINDOW_START, config.WINDOW_END)


# ---------------------------------------------------------------------------
# window discipline
# ---------------------------------------------------------------------------


def test_engine_window_is_the_approved_window(engine):
    assert engine.window == "2025-05..2026-04"


def test_every_result_carries_the_window(engine):
    assert engine.profile("SFO").window == engine.window
    assert engine.rank(["SFO", "LAX"])["window"] == engine.window
    assert engine.compare(["SFO", "LAX"])["window"] == engine.window
    assert engine.unmet_demand("SFO").window == engine.window
    assert engine.long_haul("ANC").period.startswith("2025-05..2026-04")


def test_every_result_carries_sources(engine):
    for obj in (
        engine.profile("SFO").sources,
        engine.rank(["SFO"])["sources"],
        engine.compare(["SFO"])["sources"],
        engine.long_haul("ANC").sources,
        engine.unmet_demand("SFO").sources,
    ):
        assert obj, "result has no source citations"
        assert all(s["source_url"] and s["retrieved_at"] for s in obj)


# ---------------------------------------------------------------------------
# raw metric reconciliation — engine vs independent SQL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["LAX", "SFO", "BOS", "SNA", "ANC", "PWM"])
def test_traffic_metrics_match_direct_sql(engine, raw, code):
    m = engine.get_metrics(code)
    r = raw.execute(
        """SELECT SUM(departures) d, SUM(passengers) p, SUM(seats) s
           FROM airport_month WHERE iata=? AND month BETWEEN ? AND ?""",
        (code, *W),
    ).fetchone()
    assert m.departures == pytest.approx(r["d"])
    assert m.passengers == pytest.approx(r["p"])
    assert m.seats == pytest.approx(r["s"])
    assert m.load_factor == pytest.approx(r["p"] / r["s"])


@pytest.mark.parametrize("code", ["LAX", "SFO", "BOS", "SNA", "ANC"])
def test_delay_metrics_are_window_sum_over_window_count(engine, raw, code):
    """The anti-'mean of means' check, against real data."""
    m = engine.get_metrics(code)
    r = raw.execute(
        """SELECT SUM(taxi_out_sum) ts, SUM(taxi_out_n) tn,
                  SUM(nas_delay_sum) ns, SUM(nas_delay_n) nn,
                  SUM(dep_del15) d15, SUM(dep_del15_n) d15n,
                  SUM(cancelled) c, SUM(flights) f
           FROM airport_delay_month WHERE iata=? AND month BETWEEN ? AND ?""",
        (code, *W),
    ).fetchone()
    assert m.taxi_out_avg == pytest.approx(r["ts"] / r["tn"])
    assert m.nas_delay_per_flight == pytest.approx(r["ns"] / r["nn"])
    assert m.dep_del15_rate == pytest.approx(r["d15"] / r["d15n"])
    assert m.cancel_rate == pytest.approx(r["c"] / r["f"])


def test_window_average_differs_from_mean_of_monthly_means(engine, raw):
    """Prove the two are genuinely different on real data, so the correct
    formula is doing observable work."""
    rows = raw.execute(
        """SELECT taxi_out_sum, taxi_out_n FROM airport_delay_month
           WHERE iata='BOS' AND month BETWEEN ? AND ? AND taxi_out_n > 0""",
        W,
    ).fetchall()
    monthly = [r["taxi_out_sum"] / r["taxi_out_n"] for r in rows]
    naive = sum(monthly) / len(monthly)
    correct = engine.get_metrics("BOS").taxi_out_avg
    assert correct != pytest.approx(naive, abs=1e-9)


def test_prior_window_is_used_for_growth(engine, raw):
    m = engine.get_metrics("SFO")
    r = raw.execute(
        """SELECT SUM(passengers) p FROM airport_month
           WHERE iata='SFO' AND month BETWEEN ? AND ?""",
        (config.PRIOR_WINDOW_START, config.PRIOR_WINDOW_END),
    ).fetchone()
    assert m.passengers_prior == pytest.approx(r["p"])
    assert m.pax_growth == pytest.approx(m.passengers / r["p"] - 1)


# ---------------------------------------------------------------------------
# score integrity on real data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["LAX", "SFO", "BOS", "SNA", "ANC", "BDL", "PVD"])
def test_score_equals_sum_of_its_contributions(engine, code):
    s = engine.profile(code)
    for idx in (s.tdpi, s.aci):
        if idx.score is None:
            continue
        total = sum(c.contribution for c in idx.components if c.contribution is not None)
        assert idx.score == pytest.approx(total)


@pytest.mark.parametrize("code", ["LAX", "SFO", "BOS", "SNA", "ANC"])
def test_effective_weights_sum_to_one(engine, code):
    s = engine.profile(code)
    for idx in (s.tdpi, s.aci):
        if idx.score is None:
            continue
        eff = [c.effective_weight for c in idx.components if c.effective_weight is not None]
        assert sum(eff) == pytest.approx(1.0)


def test_every_component_exposes_full_derivation(engine):
    s = engine.profile("SFO")
    for idx in (s.tdpi, s.aci):
        for c in idx.components:
            assert c.id and c.label and c.source
            if c.available:
                assert c.raw is not None
                assert c.raw_display
                assert c.normalized is not None
                assert c.percentile is not None
                assert c.effective_weight is not None
                assert c.contribution is not None


def test_scores_bounded_and_finite_across_the_whole_universe(engine):
    for code in engine.metrics:
        s = engine.profile(code)
        for idx in (s.tdpi, s.aci):
            if idx.score is not None:
                assert 0.0 <= idx.score <= 100.0, f"{code} {idx.index}={idx.score}"
            else:
                assert idx.suppressed_reason is not None


def test_aci_suppressed_exactly_where_volume_gate_says(engine):
    for code, m in engine.metrics.items():
        if not m.has_traffic:
            continue
        aci = engine.profile(code).aci
        if m.flights < config.MIN_OTP_FLIGHTS_FOR_ACI:
            assert aci.score is None
            assert aci.suppressed_reason == "insufficient_flight_volume"


def test_suppressed_aci_never_yields_a_terminal_led_class(engine):
    for code, m in engine.metrics.items():
        if not m.has_traffic:
            continue
        s = engine.profile(code)
        if s.aci.score is None:
            assert s.divergence_class in (
                "UNCLASSIFIED_AIRSIDE_UNKNOWN", "UNCLASSIFIED"
            )


def test_engine_is_usable_from_another_thread(engine):
    """Regression: FastAPI serves sync endpoints from a threadpool, so the
    engine's SQLite connection is created on one thread and used on others.
    Without check_same_thread=False plus a lock, long_haul_breakdown — the one
    operation that queries the warehouse live — raised "SQLite objects created
    in a thread can only be used in that same thread", which the API then hid
    behind a bare 404.
    """
    import threading

    results: list[object] = []
    errors: list[BaseException] = []

    def work(code: str) -> None:
        try:
            results.append(engine.long_haul(code))
            results.append(engine.profile(code))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=work, args=(c,))
        for c in ("ANC", "SFO", "LAX", "BOS")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"threaded access failed: {errors!r}"
    assert len(results) == 8


def test_concurrent_long_haul_results_agree(engine):
    """The lock must serialise access without corrupting results."""
    import threading

    out: dict[int, float] = {}

    def work(i: int) -> None:
        r = engine.long_haul("ANC")
        scope = next(s for s in r.scopes if s.scope == "all_carriers")
        out[i] = scope.headline_share_pct

    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(out) == 6
    assert len(set(round(v, 6) for v in out.values())) == 1


def test_results_are_reproducible_across_engine_instances():
    a, b = AnalyticsEngine(), AnalyticsEngine()
    try:
        for code in ("SFO", "LAX", "BOS", "ANC"):
            sa, sb = a.profile(code), b.profile(code)
            assert sa.tdpi.score == sb.tdpi.score
            assert sa.aci.score == sb.aci.score
            assert sa.divergence_class == sb.divergence_class
        assert a.long_haul("ANC").to_dict() == b.long_haul("ANC").to_dict()
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# ranking and comparison
# ---------------------------------------------------------------------------


def test_new_england_cohort_resolves_to_expected_airports(engine):
    codes = engine.resolve_region("new_england")
    for expected in ("BOS", "BDL", "PVD", "MHT", "PWM", "BTV"):
        assert expected in codes


def test_ranking_is_ordered_and_complete(engine):
    r = engine.rank(engine.resolve_region("new_england"))
    scores = [row["tdpi"]["score"] for row in r["ranked"]]
    assert scores == sorted(scores, reverse=True)
    assert all(row["rank"] == i + 1 for i, row in enumerate(r["ranked"]))
    for row in r["unscored"]:
        assert row["rank"] is None
        assert row["unscored_reason"]


def test_ranking_exposes_absolute_scale_beside_relative_score(engine):
    r = engine.rank(engine.resolve_region("new_england"))
    for row in r["ranked"]:
        assert row["scale"]["passengers"] is not None
        assert "hub_class" in row["scale"]


def test_ranking_skips_unknown_airports_without_inventing_them(engine):
    r = engine.rank(["BOS", "ZZZ"])
    assert any(s["iata"] == "ZZZ" for s in r["skipped"])
    assert all(row["iata"] != "ZZZ" for row in r["ranked"])


def test_comparison_separates_volume_from_intensity(engine):
    c = engine.compare(["LAX", "SNA"])
    lax, sna = c["airports"]
    # Volume: LAX is far larger.
    assert lax["volume"]["departures"] > 4 * sna["volume"]["departures"]
    # Intensity: the two are close, which is the substantive finding.
    assert abs(lax["intensity"]["taxi_out_avg_min"] - sna["intensity"]["taxi_out_avg_min"]) < 5
    assert "volume" in c["note"].lower() and "intensity" in c["note"].lower()


def test_sna_runway_constraint_is_visible_in_comparison(engine):
    c = engine.compare(["LAX", "SNA"])
    lax, sna = c["airports"]
    assert sna["longest_runway_ft"] < 7000
    assert lax["longest_runway_ft"] > 12000


# ---------------------------------------------------------------------------
# long-haul
# ---------------------------------------------------------------------------


def test_anc_long_haul_matches_direct_sql(engine, raw):
    """Independently recompute the headline share straight from SQL."""
    r = engine.long_haul("ANC")
    all_scope = next(s for s in r.scopes if s.scope == "all_carriers")
    row = raw.execute(
        """SELECT SUM(departures_performed) tot,
                  SUM(CASE WHEN distance_sm >= 3000 THEN departures_performed ELSE 0 END) lh
           FROM segments WHERE origin='ANC' AND month BETWEEN ? AND ?""",
        W,
    ).fetchone()
    expected = 100.0 * row["lh"] / row["tot"]
    assert all_scope.headline_share_pct == pytest.approx(expected)
    assert all_scope.headline_share_pct == pytest.approx(30.1, abs=0.1)


def test_anc_long_haul_period_is_the_full_window(engine):
    r = engine.long_haul("ANC")
    assert r.is_full_window is True
    assert r.period_months == 12
    assert "12 months" in r.period


def test_single_month_result_is_labelled_and_differs_from_the_window():
    """Approved correction C: the December-2025 research figure must never be
    presented as the annual result."""
    e = AnalyticsEngine()
    try:
        dec = e.long_haul("ANC", month_start="2025-12", month_end="2025-12")
        year = e.long_haul("ANC")
        assert dec.is_full_window is False
        assert "single month" in dec.period
        assert any("must not be presented as a 12-month figure" in l for l in dec.limitations)

        d_share = next(s for s in dec.scopes if s.scope == "all_carriers").headline_share_pct
        y_share = next(s for s in year.scopes if s.scope == "all_carriers").headline_share_pct
        assert abs(d_share - y_share) > 1.0
        assert d_share == pytest.approx(34.3, abs=0.1)   # research-stage value
        assert y_share == pytest.approx(30.1, abs=0.1)   # production value
    finally:
        e.close()


def test_long_haul_shares_are_monotonic_in_threshold(engine):
    for code in ("ANC", "SFO", "LAX", "BOS"):
        for scope in engine.long_haul(code).scopes:
            shares = [b.share_pct for b in scope.bands]
            assert shares == sorted(shares, reverse=True), f"{code}/{scope.scope}"


def test_long_haul_threshold_choice_materially_moves_the_answer(engine):
    """If it did not, the sensitivity table would be decoration."""
    scope = next(s for s in engine.long_haul("ANC").scopes if s.scope == "all_carriers")
    by_th = {b.threshold_sm: b.share_pct for b in scope.bands}
    assert by_th[1500] - by_th[3000] > 10


def test_long_haul_scopes_partition_the_total(engine, raw):
    """Every departure must fall into exactly one aircraft-configuration scope.

    Passenger + all-cargo is NOT a partition: T-100 AIRCRAFT_CONFIG also codes
    combi (3) and amphibious (4). Checked across several airports so the
    property holds generally, not just at ANC.
    """
    for code in ("ANC", "SFO", "LAX", "BOS", "SEA", "MIA"):
        m = engine.get_metrics(code)
        if m is None or not m.has_traffic:
            continue
        rec = engine.long_haul(code).reconciliation
        assert rec["reconciles"], f"{code}: residual {rec['residual']}"
        assert abs(rec["residual"]) < 0.5

        row = raw.execute(
            "SELECT COALESCE(SUM(departures_performed),0) d FROM segments "
            "WHERE origin=? AND month BETWEEN ? AND ?",
            (code, *W),
        ).fetchone()
        assert rec["total_departures"] == pytest.approx(row["d"])


def test_anc_residual_is_combi_service(engine):
    """The 888 ANC departures unaccounted for by passenger + all-cargo are
    combi aircraft — passengers and freight on the same main deck."""
    rec = engine.long_haul("ANC").reconciliation
    b = rec["breakdown"]
    assert b["passenger"]["departures"] == pytest.approx(38407, abs=1)
    assert b["cargo"]["departures"] == pytest.approx(47915, abs=1)
    assert b["combi"]["departures"] == pytest.approx(888, abs=1)
    assert b["amphibious"]["departures"] == 0
    assert (
        b["passenger"]["departures"]
        + b["cargo"]["departures"]
        + b["combi"]["departures"]
    ) == pytest.approx(87210, abs=1)


def test_anc_passenger_and_cargo_volumes_are_not_comparable(engine):
    """Guards the calibration point: 44% vs 55% of departures is a material
    difference and must not be described as roughly equal."""
    b = engine.long_haul("ANC").reconciliation["breakdown"]
    assert b["cargo"]["share_pct"] - b["passenger"]["share_pct"] > 8


def test_anc_headline_unchanged_by_the_scope_fix(engine):
    """The reconciliation fix must not move the established result."""
    scope = next(
        s for s in engine.long_haul("ANC").scopes if s.scope == "all_carriers"
    )
    assert scope.headline_share_pct == pytest.approx(30.1, abs=0.1)
    shares = {b.threshold_sm: round(b.share_pct, 1) for b in scope.bands}
    assert shares == {1500: 48.3, 2000: 46.4, 2500: 42.1, 3000: 30.1, 6000: 0.3}


def test_anc_cargo_and_passenger_scopes_differ_sharply(engine):
    """The scope split is the whole point at ANC: a passenger-terminal
    investor and a freight investor are looking at different airports."""
    scopes = {s.scope: s for s in engine.long_haul("ANC").scopes}
    pax, cargo, allc = scopes["passenger"], scopes["cargo"], scopes["all_carriers"]

    assert cargo.total_passengers < 100          # freighters carry no passengers
    assert pax.total_passengers > 2_000_000
    assert cargo.headline_share_pct > 40
    assert pax.headline_share_pct < 10
    # The all-carrier figure sits between and is dominated by freight.
    assert pax.headline_share_pct < allc.headline_share_pct < cargo.headline_share_pct
    assert cargo.total_departures + pax.total_departures <= allc.total_departures


def test_long_haul_scope_departures_reconcile(engine, raw):
    scopes = {s.scope: s for s in engine.long_haul("ANC").scopes}
    for scope, cfg in (("passenger", "1"), ("cargo", "2")):
        row = raw.execute(
            """SELECT SUM(departures_performed) d FROM segments
               WHERE origin='ANC' AND month BETWEEN ? AND ? AND aircraft_config=?""",
            (*W, cfg),
        ).fetchone()
        assert scopes[scope].total_departures == pytest.approx(row["d"])


def test_long_haul_states_definition_and_unit(engine):
    r = engine.long_haul("ANC")
    assert "3,000 statute miles" in r.definition
    assert "departures performed" in r.unit.lower()
    assert "not seats" in r.unit.lower()


# ---------------------------------------------------------------------------
# unmet demand
# ---------------------------------------------------------------------------


def test_sfo_unmet_demand_returns_evidence_not_a_number(engine):
    u = engine.unmet_demand("SFO")
    d = u.to_dict()
    for forbidden in ("unmet_passengers", "unmet_flights", "magnitude", "estimate"):
        assert forbidden not in d
    assert u.evidence_band in ("Weak", "Moderate", "Strong", "Indeterminate")
    assert u.indicators
    assert "not a measurement" in u.caveat.lower()


def test_udei_indicator_values_reconcile_with_metrics(engine):
    m = engine.get_metrics("SFO")
    u = engine.unmet_demand("SFO")
    u1 = next(i for i in u.indicators if i.id == "U1")
    assert u1.value == pytest.approx(m.load_factor)
    u3 = next(i for i in u.indicators if i.id == "U3")
    assert u3.value == pytest.approx(m.gauge_growth)


def test_fare_premium_indicator_reported_unavailable_everywhere(engine):
    for code in ("SFO", "LAX", "BOS", "ANC"):
        u5 = next(i for i in engine.unmet_demand(code).indicators if i.id == "U5")
        assert u5.available is False
        assert u5.triggered is None
        assert "not ingested" in (u5.unavailable_reason or "").lower()


def test_udei_band_consistent_with_trigger_count(engine):
    for code in ("SFO", "LAX", "BOS", "ANC", "BDL"):
        u = engine.unmet_demand(code)
        if u.available_count < 2:
            assert u.evidence_band == "Indeterminate"
        elif u.triggered_count >= 4:
            assert u.evidence_band == "Strong"
        elif u.triggered_count >= 2:
            assert u.evidence_band == "Moderate"
        else:
            assert u.evidence_band == "Weak"


# ---------------------------------------------------------------------------
# sanity of headline numbers
# ---------------------------------------------------------------------------


def test_major_hubs_have_plausible_load_factors(engine):
    for code in ("LAX", "SFO", "BOS", "ORD", "ATL", "DFW"):
        m = engine.get_metrics(code)
        if m is None or not m.has_traffic:
            continue
        assert 0.65 < m.load_factor < 0.95, f"{code} LF={m.load_factor}"


def test_anc_is_freight_dominated(engine):
    """Substantive sanity check: ANC must look like a cargo hub, or the data
    is wrong somewhere."""
    m = engine.get_metrics("ANC")
    lax = engine.get_metrics("LAX")
    # Comparable movements to a major passenger hub, a fraction of the traffic.
    assert m.passengers / m.departures < 40
    assert lax.passengers / lax.departures > 100


def _large_hub_aci_ranking(engine) -> list[tuple[str, float]]:
    rows = []
    for code, m in engine.metrics.items():
        if m.hub_class != "L":
            continue
        score = engine.profile(code).aci.score
        if score is not None:
            rows.append((code, score))
    rows.sort(key=lambda t: (-t[1], t[0]))
    return rows


def test_sfo_congestion_is_mid_table_among_large_hubs(engine):
    """CORRECTED EXPECTATION — investigated against source data.

    Phase 1 research reported SFO as the most congested of the airports it
    sampled (taxi-out 25.0 min, 35.5% departures >15 min). That impression did
    not survive the production window, for two reasons, both verified:

    1. **Convenience sample.** Those figures compared SFO against only nine
       other airports (LAX, SNA, BOS, ANC and five small New England fields).
       Against all 30 large hubs, SFO ranks 18th.
    2. **Out-of-window month.** They came from July 2026, which is outside the
       pinned window. Over 2025-05..2026-04 SFO's taxi-out is 21.6 min and its
       >15-min rate 20.2% — materially milder.

    The northeast/ORD complex (LGA, DCA, EWR, ORD, JFK, PHL, BOS) is
    substantially more congested. The expectation is corrected here rather
    than the data, and this is exactly the failure mode the approved window
    discipline exists to prevent.
    """
    ranking = _large_hub_aci_ranking(engine)
    order = [c for c, _ in ranking]
    assert len(order) >= 25

    pos = order.index("SFO")
    assert 0.4 < pos / len(order) < 0.8, f"SFO at position {pos + 1}/{len(order)}"

    m = engine.get_metrics("SFO")
    assert m.taxi_out_avg == pytest.approx(21.6, abs=0.3)
    assert m.dep_del15_rate == pytest.approx(0.202, abs=0.01)


def test_northeast_hubs_dominate_large_hub_congestion(engine):
    """The substantive replacement finding: congestion concentrates in the
    NY/DC/Chicago complex, not on the west coast."""
    top6 = {c for c, _ in _large_hub_aci_ranking(engine)[:6]}
    northeast = {"LGA", "EWR", "JFK", "DCA", "PHL", "BOS", "ORD"}
    assert len(top6 & northeast) >= 5, f"top 6 = {top6}"


def test_sfo_outranks_lax_on_congestion(engine):
    """Still true, and the direction the original research got right."""
    scores = dict(_large_hub_aci_ranking(engine))
    assert scores["SFO"] > scores["LAX"]
