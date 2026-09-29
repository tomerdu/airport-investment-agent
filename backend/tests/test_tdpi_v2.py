"""TDPI v2 — offline unit and regression tests.

Two jobs:

1. Pin the v2 component formulas and eligibility rules.
2. Prove v2 is genuinely opt-in — that adding it changed nothing about TDPI v1,
   ACI, UDEI or any production path.

No network, no API calls.
"""

from __future__ import annotations

import pytest

from app.analytics import definitions as D
from app.analytics.metrics import AirportMetrics
from app.analytics.scoring import Cohort, compute_aci, compute_tdpi, compute_tdpi_v2
from tests.test_analytics_units import mk, spread_cohort
from etl import config as etl_config


def months(start_year: int, start_month: int, values: list[float]) -> dict[str, float]:
    """Build a 'YYYY-MM' -> value series of len(values) consecutive months."""
    out: dict[str, float] = {}
    y, m = start_year, start_month
    for v in values:
        out[f"{y:04d}-{m:02d}"] = v
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def with_series(
    iata: str = "TST",
    window: list[float] | None = None,
    prior: list[float] | None = None,
    **kw,
) -> AirportMetrics:
    """Metrics carrying a 12+12 month passenger series aligned to the window."""
    window = window if window is not None else [100.0] * 12
    prior = prior if prior is not None else [100.0] * 12
    m = mk(iata, **kw)
    m.monthly_passengers = months(2025, 5, window)
    m.monthly_passengers_prior = months(2024, 5, prior)
    m.passengers = sum(window)
    m.passengers_prior = sum(prior)
    return m


# ---------------------------------------------------------------------------
# Component formulas
# ---------------------------------------------------------------------------


def test_pax_per_departure_uses_actual_passengers_not_seats():
    """The whole point of replacing load factor + gauge: a half-empty jumbo
    must not look like a full regional."""
    big_empty = mk("A", passengers=100.0, seats=1000.0, departures=1.0)
    small_full = mk("B", passengers=100.0, seats=100.0, departures=1.0)
    assert big_empty.pax_per_departure == small_full.pax_per_departure == 100.0
    assert big_empty.load_factor != small_full.load_factor


def test_pax_per_departure_growth_is_year_over_year():
    m = mk("X", passengers=1200.0, departures=10.0,
           passengers_prior=1000.0, departures_prior=10.0)
    assert m.pax_per_departure_growth == pytest.approx(0.20)


def test_pax_per_departure_growth_none_without_prior():
    assert mk("X", passengers_prior=None).pax_per_departure_growth is None
    assert mk("X", departures_prior=0.0).pax_per_departure_growth is None


def test_sustained_growth_counts_positive_months():
    m = with_series(window=[110] * 9 + [90] * 3, prior=[100] * 12)
    assert m.yoy_months_evaluable == 12
    assert m.sustained_growth == pytest.approx(9 / 12)


def test_sustained_growth_separates_steady_from_spiky():
    """Same annual growth, very different durability — the reason V2 exists."""
    # Both total 1,260 against a prior of 1,200 — identical +5% annual growth.
    # Steady grows every month; spiky shrinks for eleven and spikes once.
    steady = with_series("STEADY", window=[105] * 12, prior=[100] * 12)
    spiky = with_series("SPIKY", window=[95] * 11 + [215], prior=[100] * 12)
    assert steady.pax_growth == pytest.approx(spiky.pax_growth, abs=0.01)
    assert steady.sustained_growth == 1.0
    assert spiky.sustained_growth == pytest.approx(1 / 12)


def test_sustained_growth_requires_enough_month_pairs():
    short = with_series(window=[110] * 12, prior=[100] * 5)
    assert short.yoy_months_evaluable == 5
    assert short.sustained_growth is None, "must suppress, never impute"


def test_sustained_growth_at_exactly_the_threshold():
    n = D.MIN_YOY_MONTHS_FOR_SUSTAINED
    m = with_series(window=[110] * 12, prior=[100] * n)
    assert m.yoy_months_evaluable == n
    assert m.sustained_growth is not None


def test_yoy_pairs_align_on_the_same_calendar_month():
    """Comparing July to January would make every seasonal airport look wild."""
    m = with_series(window=[100] * 12, prior=[100] * 12)
    pairs = m._yoy_month_pairs()
    for month, _ in pairs:
        y, mm = month.split("-")
        assert f"{int(y)-1:04d}-{mm}" in m.monthly_passengers_prior


def test_zero_prior_month_is_skipped_not_infinite():
    m = with_series(window=[100] * 12, prior=[0] + [100] * 11)
    assert m.yoy_months_evaluable == 11


def test_peak_concentration_is_peak_over_mean():
    m = with_series(window=[100] * 11 + [200])
    expected = 200 / (sum([100] * 11 + [200]) / 12)
    assert m.peak_concentration == pytest.approx(expected)


def test_peak_concentration_is_one_for_flat_demand():
    assert with_series(window=[100] * 12).peak_concentration == pytest.approx(1.0)


def test_peak_concentration_rewards_seasonality_the_known_weakness():
    """Documented, not hidden: a summer-only airport outscores a steady one."""
    seasonal = with_series("SEA", window=[10] * 9 + [300] * 3)
    steady = with_series("STD", window=[100] * 12)
    assert seasonal.peak_concentration > 2.0
    assert steady.peak_concentration == pytest.approx(1.0)
    assert seasonal.peak_concentration > steady.peak_concentration


def test_peak_concentration_requires_enough_months():
    m = with_series(window=[100] * 12)
    m.monthly_passengers = months(2025, 5, [100] * (D.MIN_MONTHS_FOR_PEAK - 1))
    assert m.peak_concentration is None


def test_absolute_pax_growth_is_a_delta_and_size_biased():
    big = mk("BIG", passengers=10_200_000.0, passengers_prior=10_000_000.0)
    small = mk("SML", passengers=140_000.0, passengers_prior=100_000.0)
    assert big.absolute_pax_growth == pytest.approx(200_000)
    assert small.absolute_pax_growth == pytest.approx(40_000)
    # 2% growth beats 40% growth on this component — the bias V4 is testing for.
    assert big.absolute_pax_growth > small.absolute_pax_growth
    assert big.pax_growth < small.pax_growth


def test_absolute_growth_is_negative_when_traffic_falls():
    assert mk("X", passengers=90.0, passengers_prior=100.0).absolute_pax_growth == -10.0


# ---------------------------------------------------------------------------
# Eligibility and suppression
# ---------------------------------------------------------------------------


def test_v2_eligible_with_a_full_series():
    assert with_series().tdpi_v2_eligible is True


def test_v2_ineligible_without_enough_yoy_pairs():
    assert with_series(prior=[100] * 3).tdpi_v2_eligible is False


def test_v2_suppressed_rather_than_imputed():
    members = spread_cohort()
    target = with_series("Z", prior=[100] * 2)
    cohort = Cohort("t", members + [target])
    r = compute_tdpi_v2(target, cohort)
    assert r.score is None
    assert r.suppressed_reason == "insufficient_coverage"
    assert any("Not imputed" in n for n in r.notes)


def test_v2_weights_sum_to_one():
    assert sum(d.weight for d in D.TDPI_V2_METRICS) == pytest.approx(1.0)


def test_v2_component_ids_are_unique():
    ids = [d.id for d in D.TDPI_V2_METRICS]
    assert len(ids) == len(set(ids)) == 5


# ---------------------------------------------------------------------------
# Composition behaviour
# ---------------------------------------------------------------------------


def _v2_cohort():
    members = [
        with_series(
            f"A{i:02d}",
            window=[100 + i * 5] * 12,
            prior=[100] * 12,
            departures=8000.0 + i * 500,
            departures_prior=8000.0,
        )
        for i in range(30)
    ]
    return members, Cohort("v2test", members)


def test_v2_score_equals_sum_of_contributions():
    members, cohort = _v2_cohort()
    r = compute_tdpi_v2(members[15], cohort)
    total = sum(c.contribution for c in r.components if c.contribution is not None)
    assert r.score == pytest.approx(total)


def test_v2_effective_weights_renormalise_to_one():
    members, cohort = _v2_cohort()
    r = compute_tdpi_v2(members[10], cohort)
    eff = [c.effective_weight for c in r.components if c.effective_weight is not None]
    assert sum(eff) == pytest.approx(1.0)


def test_v2_weight_override_changes_the_score():
    members, cohort = _v2_cohort()
    base = compute_tdpi_v2(members[20], cohort).score
    alt = compute_tdpi_v2(
        members[20], cohort,
        weights={"V1": 1.0, "V2": 0.0, "V3": 0.0, "V4": 0.0, "V5": 0.0},
    ).score
    assert base is not None and alt is not None
    assert base != pytest.approx(alt)


def test_weight_override_does_not_mutate_the_shared_definitions():
    """A sensitivity sweep must not leak into later calls."""
    before = [d.weight for d in D.TDPI_V2_METRICS]
    members, cohort = _v2_cohort()
    compute_tdpi_v2(members[0], cohort, weights={"V1": 0.99})
    assert [d.weight for d in D.TDPI_V2_METRICS] == before


def test_v2_reports_context_without_scoring_it():
    members, cohort = _v2_cohort()
    r = compute_tdpi_v2(members[5], cohort)
    ctx = next(n for n in r.notes if n.startswith("Context (not scored)"))
    assert "cross-source" in ctx or "context only" in ctx
    # FAA growth must not appear as a scored component.
    assert "enplanement_growth" not in {c.id for c in r.components}
    assert all(d.attr != "enplanement_growth" for d in D.TDPI_V2_METRICS)


def test_v2_drops_throughput_per_runway_from_the_composite():
    assert all(d.attr != "pax_per_runway" for d in D.TDPI_V2_METRICS)
    # ...but v1 still has it, untouched.
    assert any(d.attr == "pax_per_runway" for d in D.TDPI_METRICS)


def test_v2_drops_seat_based_components():
    attrs = {d.attr for d in D.TDPI_V2_METRICS}
    assert "load_factor" not in attrs
    assert "seats_per_departure" not in attrs
    assert "pax_per_departure_growth" in attrs


def test_v2_is_labelled_experimental():
    members, cohort = _v2_cohort()
    r = compute_tdpi_v2(members[1], cohort)
    assert r.index == "TDPI_V2"
    assert "experimental" in r.label.lower()
    assert any("EXPERIMENTAL" in n and "opt-in" in n for n in r.notes)


def test_v2_carries_the_same_proxy_disclaimer_as_v1():
    joined = " ".join(D.TDPI_V2_NOTES).lower()
    assert "does not measure terminal capacity" in joined
    assert "not an investment recommendation" in joined


# ---------------------------------------------------------------------------
# REGRESSION: v2 must not have disturbed anything in production
# ---------------------------------------------------------------------------


def test_v1_weights_are_unchanged():
    assert {d.id: d.weight for d in D.TDPI_METRICS} == {
        "T1": 0.20, "T2": 0.30, "T3": 0.15, "T4": 0.20, "T5": 0.15,
    }


def test_aci_weights_are_unchanged():
    assert {d.id: d.weight for d in D.ACI_METRICS} == {
        "A1": 0.30, "A2": 0.30, "A3": 0.25, "A4": 0.15,
    }


def test_v1_still_scores_without_any_monthly_series():
    """v1 must not have acquired a hidden dependency on the new fields."""
    members = spread_cohort()
    cohort = Cohort("t", members)
    target = members[12]
    assert target.monthly_passengers == {}
    r = compute_tdpi(target, cohort)
    assert r.score is not None
    assert {c.id for c in r.components} == {"T1", "T2", "T3", "T4", "T5"}


def test_aci_unaffected_by_the_v2_additions():
    members = spread_cohort()
    cohort = Cohort("t", members)
    r = compute_aci(members[7], cohort)
    assert r.score is not None
    assert {c.id for c in r.components} == {"A1", "A2", "A3", "A4"}


def test_analysis_window_is_unchanged():
    assert etl_config.WINDOW_START == "2025-05"
    assert etl_config.WINDOW_END == "2026-04"


def test_v1_and_v2_use_the_same_machinery():
    """Any v1/v2 difference must come from the components, not the plumbing."""
    members, cohort = _v2_cohort()
    a, b = compute_tdpi(members[9], cohort), compute_tdpi_v2(members[9], cohort)
    for r in (a, b):
        if r.score is not None:
            assert 0.0 <= r.score <= 100.0
            assert sum(
                c.effective_weight for c in r.components
                if c.effective_weight is not None
            ) == pytest.approx(1.0)


def test_v2_is_deterministic():
    members, cohort = _v2_cohort()
    a = compute_tdpi_v2(members[3], cohort)
    b = compute_tdpi_v2(members[3], cohort)
    assert a.score == b.score
    assert [c.contribution for c in a.components] == [c.contribution for c in b.components]
