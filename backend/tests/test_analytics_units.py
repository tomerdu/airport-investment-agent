"""Unit tests for the deterministic analytics engine.

Synthetic cohorts only — no warehouse, no network. These tests pin the
*formulas*; `test_analytics_integration.py` checks them against real data.
"""

from __future__ import annotations

import math

import pytest

from app.analytics import definitions as D
from app.analytics.metrics import AirportMetrics
from app.analytics.models import UnmetDemandEvidence
from app.analytics.normalize import CohortStats, percentile
from app.analytics.scoring import (
    Cohort,
    build_cohort,
    classify,
    compute_aci,
    compute_tdpi,
    score_airport,
)
from app.analytics.unmet import unmet_demand_evidence


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def mk(iata: str, **kw) -> AirportMetrics:
    """AirportMetrics with sane defaults; override what a test cares about."""
    base = dict(
        name=f"{iata} Airport", city="C", state="XX", region=None,
        hub_class="M", runway_count=2, longest_runway_ft=9000,
        departures=10_000.0, passengers=1_000_000.0, seats=1_250_000.0,
        intl_out_departures=0.0, freight_lbs=0.0, traffic_months=12,
        departures_prior=10_000.0, passengers_prior=1_000_000.0,
        seats_prior=1_250_000.0, traffic_months_prior=12,
        flights=20_000, cancelled=200, diverted=20,
        dep_del15=4_000, dep_del15_n=19_800,
        taxi_out_sum=300_000.0, taxi_out_n=19_800,
        nas_delay_sum=60_000.0, nas_delay_n=19_800,
        dep_delay_sum=250_000.0, dep_delay_n=19_800,
        delay_months=12,
        enplanements=500_000.0, enplanements_prior=480_000.0,
        enplanement_growth=0.0417, enplanements_preliminary=True,
    )
    base.update(kw)
    return AirportMetrics(iata=iata, **base)  # type: ignore[arg-type]


def spread_cohort(n: int = 40) -> list[AirportMetrics]:
    """A cohort with a genuine spread on every scored metric."""
    out = []
    for i in range(n):
        f = i / (n - 1)
        out.append(
            mk(
                f"A{i:02d}",
                passengers=500_000 + f * 5_000_000,
                seats=(500_000 + f * 5_000_000) / (0.70 + 0.2 * f),
                departures=8_000 + f * 40_000,
                passengers_prior=500_000 + f * 4_000_000,
                seats_prior=(500_000 + f * 4_000_000) / 0.78,
                departures_prior=8_000 + f * 38_000,
                enplanement_growth=-0.05 + f * 0.20,
                taxi_out_sum=(10 + f * 20) * 19_800,
                nas_delay_sum=(1 + f * 8) * 19_800,
                dep_del15=int((0.10 + f * 0.30) * 19_800),
                cancelled=int((0.005 + f * 0.04) * 20_000),
            )
        )
    return out


# ---------------------------------------------------------------------------
# percentile / normalisation
# ---------------------------------------------------------------------------


def test_percentile_matches_linear_interpolation():
    v = [1.0, 2.0, 3.0, 4.0]
    assert percentile(v, 0) == 1.0
    assert percentile(v, 100) == 4.0
    assert percentile(v, 50) == 2.5
    assert percentile(v, 25) == 1.75


def test_percentile_of_single_value():
    assert percentile([7.0], 50) == 7.0


def test_percentile_rejects_empty():
    with pytest.raises(ValueError):
        percentile([], 50)


def test_winsorized_normalization_maps_bounds_to_0_and_100():
    s = CohortStats([float(i) for i in range(101)])   # 0..100
    assert s.normalize(s.p_low) == pytest.approx(0.0)
    assert s.normalize(s.p_high) == pytest.approx(100.0)


def test_winsorization_clamps_outliers_rather_than_letting_them_dominate():
    """A single extreme value must not compress the rest of the cohort.

    Real case: the FAA enplanement-growth column contains a +126,403% value
    from a tiny airport starting near-zero service.
    """
    normal = [float(i) for i in range(100)]
    s_clean = CohortStats(normal)
    s_outlier = CohortStats(normal + [1_000_000.0])

    # The outlier is clamped to P95, scoring 100 rather than pushing a
    # mid-cohort airport toward 0.
    assert s_outlier.normalize(1_000_000.0) == pytest.approx(100.0)
    mid_clean = s_clean.normalize(50.0)
    mid_outlier = s_outlier.normalize(50.0)
    assert abs(mid_clean - mid_outlier) < 5.0


def test_degenerate_cohort_returns_neutral_midpoint_not_divide_by_zero():
    s = CohortStats([5.0] * 20)
    assert s.degenerate is True
    assert s.normalize(5.0) == 50.0
    assert s.normalize(999.0) == 50.0


def test_normalize_returns_none_for_missing_value():
    s = CohortStats([1.0, 2.0, 3.0])
    assert s.normalize(None) is None


def test_missing_values_excluded_from_cohort_bounds():
    """A gap must not shift the distribution it is measured against."""
    rows = [mk("A", passengers=1.0), mk("B", passengers=None), mk("C", passengers=3.0)]
    from app.analytics.normalize import build_cohort_stats
    stats = build_cohort_stats(rows, ["passengers"])
    assert stats["passengers"].n == 2


def test_percentile_of_is_informational_rank():
    s = CohortStats([float(i) for i in range(100)])   # 0..99
    assert s.percentile_of(49.0) == pytest.approx(50.0)
    assert s.percentile_of(-100.0) == pytest.approx(0.0)
    assert s.percentile_of(1000.0) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# metric aggregation — sums, never means of means
# ---------------------------------------------------------------------------


def test_delay_metrics_are_ratios_of_window_sums():
    m = mk("X", taxi_out_sum=1000.0, taxi_out_n=50)
    assert m.taxi_out_avg == 20.0


def test_load_factor_and_gauge_derive_from_sums():
    m = mk("X", passengers=800.0, seats=1000.0, departures=10.0)
    assert m.load_factor == 0.8
    assert m.seats_per_departure == 100.0


def test_growth_metrics_use_prior_window():
    m = mk("X", passengers=1100.0, passengers_prior=1000.0,
           departures=90.0, departures_prior=100.0)
    assert m.pax_growth == pytest.approx(0.10)
    assert m.departure_growth == pytest.approx(-0.10)


def test_derived_metrics_return_none_when_inputs_missing():
    m = mk("X", seats=None, passengers=None, departures=None,
           passengers_prior=None, runway_count=None,
           taxi_out_n=0, nas_delay_n=0, dep_del15_n=0, flights=0)
    assert m.load_factor is None
    assert m.seats_per_departure is None
    assert m.pax_growth is None
    assert m.pax_per_runway is None
    assert m.taxi_out_avg is None
    assert m.nas_delay_per_flight is None
    assert m.dep_del15_rate is None
    assert m.cancel_rate is None


def test_zero_prior_year_does_not_divide_by_zero():
    m = mk("X", passengers_prior=0.0)
    assert m.pax_growth is None


# ---------------------------------------------------------------------------
# TDPI / ACI composition
# ---------------------------------------------------------------------------


def test_tdpi_weights_are_the_approved_values():
    w = {d.id: d.weight for d in D.TDPI_METRICS}
    assert w == {"T1": 0.20, "T2": 0.30, "T3": 0.15, "T4": 0.20, "T5": 0.15}
    assert sum(w.values()) == pytest.approx(1.0)


def test_aci_weights_are_the_approved_values():
    w = {d.id: d.weight for d in D.ACI_METRICS}
    assert w == {"A1": 0.30, "A2": 0.30, "A3": 0.25, "A4": 0.15}
    assert sum(w.values()) == pytest.approx(1.0)


def test_score_equals_sum_of_contributions():
    members = spread_cohort()
    cohort = Cohort("test", members)
    r = compute_tdpi(members[20], cohort)
    total = sum(c.contribution for c in r.components if c.contribution is not None)
    assert r.score == pytest.approx(total)


def test_full_coverage_when_all_components_present():
    members = spread_cohort()
    cohort = Cohort("test", members)
    r = compute_tdpi(members[10], cohort)
    assert r.coverage == pytest.approx(1.0)
    assert all(c.effective_weight == pytest.approx(c.weight) for c in r.components)


def test_weights_renormalize_over_present_components():
    """Dropping T5 (w=0.15) must rescale the remaining 0.85 back to 1.0."""
    members = spread_cohort()
    target = members[20]
    target.enplanement_growth = None
    cohort = Cohort("test", members)
    r = compute_tdpi(target, cohort)

    present = [c for c in r.components if c.available]
    assert len(present) == 4
    assert sum(c.effective_weight for c in present) == pytest.approx(1.0)
    assert r.coverage == pytest.approx(0.85)
    t1 = next(c for c in present if c.id == "T1")
    assert t1.effective_weight == pytest.approx(0.20 / 0.85)


def test_missing_component_is_dropped_never_imputed():
    members = spread_cohort()
    target = members[20]
    target.enplanement_growth = None
    cohort = Cohort("test", members)
    r = compute_tdpi(target, cohort)
    t5 = next(c for c in r.components if c.id == "T5")
    assert t5.available is False
    assert t5.raw is None
    assert t5.normalized is None
    assert t5.contribution is None          # NOT 0.0
    assert t5.effective_weight is None


def test_score_suppressed_below_minimum_coverage():
    """T2+T4+T5 missing leaves 0.35 coverage, under the 0.60 floor."""
    members = spread_cohort()
    target = members[20]
    target.passengers_prior = None      # kills T2
    target.runway_count = None          # kills T4
    target.enplanement_growth = None    # kills T5
    cohort = Cohort("test", members)
    r = compute_tdpi(target, cohort)
    assert r.coverage == pytest.approx(0.35)
    assert r.score is None
    assert r.suppressed_reason == "insufficient_coverage"


def test_score_survives_at_exactly_the_coverage_floor():
    members = spread_cohort()
    target = members[20]
    target.passengers_prior = None      # T2 (0.30) gone -> coverage 0.70
    cohort = Cohort("test", members)
    r = compute_tdpi(target, cohort)
    assert r.coverage == pytest.approx(0.70)
    assert r.score is not None


def test_no_data_at_all_reports_no_data():
    target = mk("Z", passengers=None, seats=None, departures=None,
                passengers_prior=None, runway_count=None, enplanement_growth=None)
    cohort = Cohort("test", spread_cohort() + [target])
    r = compute_tdpi(target, cohort)
    assert r.score is None
    assert r.suppressed_reason == "no_data"


# ---------------------------------------------------------------------------
# ACI volume gate
# ---------------------------------------------------------------------------


def test_aci_suppressed_below_flight_volume_gate():
    members = spread_cohort()
    target = members[20]
    target.flights = D.MIN_OTP_FLIGHTS_FOR_ACI - 1
    cohort = Cohort("test", members)
    r = compute_aci(target, cohort)
    assert r.score is None
    assert r.suppressed_reason == "insufficient_flight_volume"
    assert any("below the 1,000 minimum" in n for n in r.notes)


def test_aci_allowed_at_exactly_the_gate():
    members = spread_cohort()
    target = members[20]
    target.flights = D.MIN_OTP_FLIGHTS_FOR_ACI
    cohort = Cohort("test", members)
    assert compute_aci(target, cohort).score is not None


def test_aci_cohort_bounds_exclude_sub_threshold_airports():
    """Noisy small-sample delay values must not drag the cohort bounds."""
    members = spread_cohort()
    for extreme in range(5):
        members.append(
            mk(f"TINY{extreme}", flights=50, taxi_out_sum=200 * 50, taxi_out_n=50)
        )
    cohort = Cohort("test", members)
    assert cohort.aci_eligible_size == 40
    assert cohort.stat("taxi_out_avg").n == 40


# ---------------------------------------------------------------------------
# divergence classification
# ---------------------------------------------------------------------------


class _Idx:
    def __init__(self, score):
        self.score = score
        self.suppressed_reason = None if score is not None else "insufficient_coverage"


@pytest.mark.parametrize(
    "tdpi,aci,expected",
    [
        (80.0, 20.0, "TERMINAL_LED"),
        (60.0, 39.9, "TERMINAL_LED"),
        (80.0, 80.0, "SYSTEMIC"),
        (60.0, 60.0, "SYSTEMIC"),
        (20.0, 80.0, "AIRSIDE_LED"),
        (39.9, 60.0, "AIRSIDE_LED"),
        (20.0, 20.0, "NO_NEAR_TERM_CASE"),
        (50.0, 50.0, "MIXED"),
        (80.0, 50.0, "MIXED"),
        (50.0, 80.0, "MIXED"),
    ],
)
def test_divergence_quadrants(tdpi, aci, expected):
    assert classify(_Idx(tdpi), _Idx(aci)) == expected


def test_suppressed_aci_blocks_classification():
    """Absence of a congestion measurement is not evidence of low congestion.

    A high-TDPI airport with unmeasured ACI must NOT fall through to
    TERMINAL_LED, which would be an investment recommendation built on a gap.
    """
    assert classify(_Idx(90.0), _Idx(None)) == "UNCLASSIFIED_AIRSIDE_UNKNOWN"


def test_suppressed_tdpi_blocks_classification():
    assert classify(_Idx(None), _Idx(90.0)) == "UNCLASSIFIED"


def test_every_class_has_a_written_reading():
    for cls in D.DIVERGENCE_READINGS:
        assert len(D.DIVERGENCE_READINGS[cls]) > 40


# ---------------------------------------------------------------------------
# methodology guardrails
# ---------------------------------------------------------------------------


def test_t4_is_labelled_a_proxy_in_its_definition():
    t4 = next(d for d in D.TDPI_METRICS if d.id == "T4")
    assert "proxy" in t4.label.lower()
    assert "PROXY ONLY" in (t4.note or "")
    for forbidden in ("terminal capacity", "gate availability", "checkpoint"):
        assert forbidden in (t4.note or "").lower() or forbidden.title() in (t4.note or "")


def test_tdpi_result_carries_the_not_terminal_capacity_note():
    members = spread_cohort()
    r = compute_tdpi(members[5], Cohort("test", members))
    joined = " ".join(r.notes).lower()
    assert "not a measurement of terminal capacity" in joined


def test_global_limitations_disclaim_profitability():
    joined = " ".join(D.GLOBAL_LIMITATIONS).lower()
    assert "profitability is not modelled" in joined
    assert "terminal capacity is never measured" in joined


def test_score_airport_attaches_limitations_and_window():
    members = spread_cohort()
    s = score_airport(members[3], Cohort("test", members), window="2025-05..2026-04")
    assert s.window == "2025-05..2026-04"
    assert s.limitations
    assert s.divergence_reading


# ---------------------------------------------------------------------------
# UDEI
# ---------------------------------------------------------------------------


def test_udei_has_no_numeric_magnitude_field():
    """The control against fabrication is structural: there is nowhere to put
    a made-up 'unmet passengers' figure."""
    field_names = set(UnmetDemandEvidence.__dataclass_fields__)
    for forbidden in (
        "unmet_passengers", "unmet_flights", "magnitude", "estimate",
        "unmet_demand", "spill",
    ):
        assert forbidden not in field_names


def test_udei_reports_unavailable_indicator_rather_than_assuming():
    members = spread_cohort()
    u = unmet_demand_evidence(members[20], Cohort("test", members), window="w")
    u5 = next(i for i in u.indicators if i.id == "U5")
    assert u5.available is False
    assert u5.triggered is None          # NOT False
    assert u5.value is None
    assert u5.unavailable_reason


def test_udei_excludes_unavailable_indicators_from_counts():
    members = spread_cohort()
    u = unmet_demand_evidence(members[20], Cohort("test", members), window="w")
    assert u.total_count == 5
    assert u.available_count <= 4
    assert u.triggered_count <= u.available_count


def test_udei_band_thresholds():
    members = spread_cohort()
    cohort = Cohort("test", members)

    # Force all four evaluable indicators to fire.
    strong = mk(
        "STR",
        passengers=6_000_000.0, seats=6_300_000.0,       # LF ~95% -> U1
        passengers_prior=5_500_000.0,
        departures=30_000.0, departures_prior=32_000.0,  # pax up, dep down -> U2
        seats_prior=5_800_000.0,                         # gauge up -> U3
        taxi_out_sum=40.0 * 19_800, nas_delay_sum=15.0 * 19_800,
        dep_del15=int(0.55 * 19_800), cancelled=int(0.08 * 20_000),  # -> U4
    )
    u = unmet_demand_evidence(strong, Cohort("test", members + [strong]), window="w")
    assert u.triggered_count == 4
    assert u.evidence_band == "Strong"

    weak = mk("WEAK", passengers=500_000.0, seats=1_000_000.0,
              passengers_prior=600_000.0, departures=10_000.0,
              departures_prior=9_000.0, seats_prior=1_000_000.0,
              taxi_out_sum=5.0 * 19_800, nas_delay_sum=0.1 * 19_800,
              dep_del15=int(0.02 * 19_800), cancelled=10)
    u2 = unmet_demand_evidence(weak, Cohort("test", members + [weak]), window="w")
    assert u2.evidence_band == "Weak"


def test_udei_indeterminate_when_too_few_indicators_evaluable():
    target = mk("Z", passengers=None, seats=None, passengers_prior=None,
                seats_prior=None, departures=None, departures_prior=None,
                flights=10)
    cohort = Cohort("test", spread_cohort() + [target])
    u = unmet_demand_evidence(target, cohort, window="w")
    assert u.available_count < 2
    assert u.evidence_band == "Indeterminate"


def test_udei_caveat_denies_measurement():
    members = spread_cohort()
    u = unmet_demand_evidence(members[1], Cohort("test", members), window="w")
    low = u.caveat.lower()
    assert "not a measurement" in low
    # Scoped to this system's data rather than claiming nothing anywhere could
    # estimate unmet demand — see tests/test_language_calibration.py.
    assert "cannot be quantified from the datasets this system uses" in low


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_scores_are_reproducible():
    members = spread_cohort()
    c1, c2 = Cohort("t", list(members)), Cohort("t", list(members))
    a = compute_tdpi(members[17], c1)
    b = compute_tdpi(members[17], c2)
    assert a.score == b.score
    assert [c.contribution for c in a.components] == [c.contribution for c in b.components]


def test_cohort_member_order_does_not_change_scores():
    members = spread_cohort()
    forward = Cohort("t", list(members))
    backward = Cohort("t", list(reversed(members)))
    assert compute_tdpi(members[7], forward).score == pytest.approx(
        compute_tdpi(members[7], backward).score
    )


def test_build_cohort_filters_and_sorts_deterministically():
    members = spread_cohort()
    members[0].hub_class = "L"
    metrics = {m.iata: m for m in members}
    c = build_cohort(metrics, hub_class="M")
    assert all(m.hub_class == "M" for m in c.members)
    assert [m.iata for m in c.members] == sorted(m.iata for m in c.members)


def test_scores_are_finite():
    members = spread_cohort()
    cohort = Cohort("t", members)
    for m in members:
        for r in (compute_tdpi(m, cohort), compute_aci(m, cohort)):
            if r.score is not None:
                assert math.isfinite(r.score)
                assert 0.0 <= r.score <= 100.0
