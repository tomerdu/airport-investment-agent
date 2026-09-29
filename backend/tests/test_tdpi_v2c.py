"""TDPI v2c — offline validation tests (Phase 8.1b).

These pin the measured findings that decided against adopting v2c, so a later
change cannot quietly reintroduce the problems:

  * C3 is arithmetically gauge, not terminal throughput
  * C1 and C2 remain highly rank-correlated
  * v2c is opt-in and production is untouched

No network, no API calls.
"""

from __future__ import annotations

import pytest

from app.analytics import definitions as D
from app.analytics.scoring import (
    Cohort,
    compute_aci,
    compute_tdpi,
    compute_tdpi_v2,
    compute_tdpi_v2c,
)
from tests.test_analytics_units import mk, spread_cohort
from tests.test_tdpi_v2 import with_series
from etl import config as etl_config


def _cohort():
    members = [
        with_series(
            f"B{i:02d}",
            window=[100 + i * 4] * 12,
            prior=[100] * 12,
            departures=5000.0 + i * 400,
            departures_prior=5000.0,
            seats=900_000.0 + i * 20_000,
        )
        for i in range(30)
    ]
    return members, Cohort("v2c", members)


# ---------------------------------------------------------------------------
# Definition and wiring
# ---------------------------------------------------------------------------


def test_v2c_weights_match_the_specification():
    assert {d.id: d.weight for d in D.TDPI_V2C_METRICS} == {
        "C1": 0.30, "C2": 0.25, "C3": 0.30, "C4": 0.15,
    }
    assert sum(d.weight for d in D.TDPI_V2C_METRICS) == pytest.approx(1.0)


def test_v2c_drops_peak_and_absolute_growth():
    attrs = {d.attr for d in D.TDPI_V2C_METRICS}
    assert "peak_concentration" not in attrs, "V3 measured seasonality"
    assert "absolute_pax_growth" not in attrs, "V4 duplicated V1"
    assert "pax_per_runway" not in attrs


def test_v2c_score_equals_sum_of_contributions():
    members, cohort = _cohort()
    r = compute_tdpi_v2c(members[14], cohort)
    total = sum(c.contribution for c in r.components if c.contribution is not None)
    assert r.score == pytest.approx(total)
    assert 0.0 <= r.score <= 100.0


def test_v2c_is_labelled_experimental_and_opt_in():
    members, cohort = _cohort()
    r = compute_tdpi_v2c(members[2], cohort)
    assert r.index == "TDPI_V2C"
    assert "experimental" in r.label.lower()
    joined = " ".join(r.notes).lower()
    assert "opt-in" in joined
    assert "does not measure terminal capacity" in joined


def test_v2c_weight_override_does_not_mutate_definitions():
    before = [d.weight for d in D.TDPI_V2C_METRICS]
    members, cohort = _cohort()
    compute_tdpi_v2c(members[0], cohort, weights={"C3": 0.99})
    assert [d.weight for d in D.TDPI_V2C_METRICS] == before


def test_v2c_suppressed_when_shape_data_is_thin():
    members, _ = _cohort()
    target = with_series("Z", prior=[100] * 3)
    cohort = Cohort("t", members + [target])
    r = compute_tdpi_v2c(target, cohort)
    assert r.score is None
    assert r.suppressed_reason == "insufficient_coverage"
    assert any("Not imputed" in n for n in r.notes)


def test_v2c_is_deterministic():
    members, cohort = _cohort()
    a, b = compute_tdpi_v2c(members[6], cohort), compute_tdpi_v2c(members[6], cohort)
    assert a.score == b.score


# ---------------------------------------------------------------------------
# The finding that decided against v2c: C3 is gauge
# ---------------------------------------------------------------------------


def test_c3_is_gauge_times_load_factor_by_construction():
    """pax/dep = (seats/dep) x (pax/seats). Not an independent quantity."""
    m = mk("X", passengers=800.0, seats=1000.0, departures=10.0)
    assert m.pax_per_departure == pytest.approx(
        m.seats_per_departure * m.load_factor
    )


def _spearman(a: list[float], b: list[float]) -> float:
    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out = [0.0] * len(xs)
        for pos, i in enumerate(order, 1):
            out[i] = pos
        return out

    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den


def test_c3_tracks_gauge_far_more_closely_than_load_factor():
    """Measured on the real cohort: C3~gauge spearman +0.979, C3~LF +0.661.

    Because US load factors cluster in a narrow band (~78-86%), pax/dep is
    dominated by the gauge term. Reproduced synthetically: rank agreement with
    gauge is near-perfect but NOT perfect — load factor perturbs the ordering
    slightly, which is exactly what the 0.979 (not 1.000) measurement shows.
    """
    lf_band = [0.78, 0.80, 0.82, 0.84, 0.86]
    gauges, lfs, c3s = [], [], []
    for i, gauge in enumerate(range(50, 190, 5)):
        lf = lf_band[i % len(lf_band)]
        gauges.append(float(gauge))
        lfs.append(lf)
        c3s.append(gauge * lf)

    rho_gauge = _spearman(c3s, gauges)
    rho_lf = _spearman(c3s, lfs)
    # Bounds are set from the real-cohort measurement (0.979 vs 0.661), not
    # from this synthetic fixture, so the test asserts the finding rather than
    # the fixture's particular arithmetic.
    assert rho_gauge > 0.95, f"C3 should track gauge closely, got {rho_gauge:.3f}"
    assert rho_gauge < 1.0, "not identical — load factor perturbs the ordering"
    assert rho_lf < 0.75, f"C3 is not primarily load factor, got {rho_lf:.3f}"
    assert rho_gauge - rho_lf > 0.25, (
        f"C3 must be far closer to gauge ({rho_gauge:.3f}) than to load "
        f"factor ({rho_lf:.3f})"
    )


def test_c3_is_blind_to_terminal_throughput():
    """Two airports with identical passenger volume but different fleet mix
    score differently on C3 — the conceptual objection in one test."""
    few_big = mk("BIG", passengers=20_000.0, departures=100.0)
    many_small = mk("SML", passengers=20_000.0, departures=200.0)
    assert few_big.passengers == many_small.passengers
    assert few_big.pax_per_departure == pytest.approx(200.0)
    assert many_small.pax_per_departure == pytest.approx(100.0)


def test_c3_note_warns_it_is_per_movement_not_terminal_capacity():
    c3 = next(d for d in D.TDPI_V2C_METRICS if d.id == "C3")
    note = (c3.note or "").lower()
    assert "per-movement" in note
    assert "not a terminal-throughput measure" in note
    assert "not a measurement of terminal capacity" in note


def test_c1_and_c2_remain_the_growth_pair():
    """The v2 redundancy survives into v2c — C1+C2 carry 55% of the weight."""
    ids = {d.id: d for d in D.TDPI_V2C_METRICS}
    assert ids["C1"].attr == "pax_growth"
    assert ids["C2"].attr == "sustained_growth"
    assert ids["C1"].weight + ids["C2"].weight == pytest.approx(0.55)


# The YoY comparability guard proposed here in Phase 8.1b was reformulated and
# adopted in Phase 8.1c. Its tests live in tests/test_yoy_comparability.py.


# ---------------------------------------------------------------------------
# Classification thresholds must be re-derived, not reused
# ---------------------------------------------------------------------------


def test_divergence_thresholds_unchanged_in_production():
    assert D.DIVERGENCE_HI == 60.0
    assert D.DIVERGENCE_LO == 40.0


def test_no_v2_variant_is_wired_into_classification():
    """classify() takes IndexResults; nothing routes v2/v2c into it."""
    import inspect
    from app.analytics import scoring

    src = inspect.getsource(scoring.score_airport)
    assert "compute_tdpi_v2" not in src
    assert "compute_tdpi(" in src


# ---------------------------------------------------------------------------
# REGRESSION — production untouched
# ---------------------------------------------------------------------------


def test_v1_and_aci_weights_unchanged():
    assert {d.id: d.weight for d in D.TDPI_METRICS} == {
        "T1": 0.20, "T2": 0.30, "T3": 0.15, "T4": 0.20, "T5": 0.15,
    }
    assert {d.id: d.weight for d in D.ACI_METRICS} == {
        "A1": 0.30, "A2": 0.30, "A3": 0.25, "A4": 0.15,
    }


def test_window_unchanged():
    assert etl_config.WINDOW_START == "2025-05"
    assert etl_config.WINDOW_END == "2026-04"


def test_all_three_variants_share_the_same_machinery():
    members, cohort = _cohort()
    m = members[11]
    for r in (compute_tdpi(m, cohort), compute_tdpi_v2(m, cohort),
              compute_tdpi_v2c(m, cohort)):
        if r.score is not None:
            assert 0.0 <= r.score <= 100.0
            eff = [c.effective_weight for c in r.components
                   if c.effective_weight is not None]
            assert sum(eff) == pytest.approx(1.0)


def test_v1_still_scores_without_monthly_series():
    members = spread_cohort()
    cohort = Cohort("t", members)
    assert members[3].monthly_passengers == {}
    assert compute_tdpi(members[3], cohort).score is not None
    assert compute_aci(members[3], cohort).score is not None
