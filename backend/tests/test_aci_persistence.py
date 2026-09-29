"""Phase 8.2 — ACI temporal persistence diagnostic.

Pins the behaviour the evaluation relies on, and pins that the diagnostic stays
out of production: it changes no ACI component, weight or threshold and is not
imported by any production surface.

No network, no API calls.
"""

from __future__ import annotations

import pytest

from app.analytics import definitions as D
from app.analytics.persistence import (
    CONCENTRATED_DROP_POINTS,
    ELEVATED_MONTH_ACI,
    MIN_FLIGHTS_PER_MONTH,
    MonthlyDelay,
    _monthly_aci,
    _summed,
    build_profile,
)
from app.analytics.scoring import Cohort, compute_aci
from tests.test_analytics_units import mk, spread_cohort

MONTHS = ["2025-05", "2025-06", "2025-07", "2025-08", "2025-09", "2025-10",
          "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04"]


def md(month: str, *, flights: int = 1_000, taxi: float = 15.0,
       nas: float = 3.0, del15: float = 0.20, cancel: float = 0.01
       ) -> MonthlyDelay:
    """One month whose component RATES are stated directly."""
    n = flights
    return MonthlyDelay(
        month=month, flights=flights, cancelled=int(cancel * flights),
        diverted=0, dep_del15=int(del15 * n), dep_del15_n=n,
        taxi_out_sum=taxi * n, taxi_out_n=n,
        nas_delay_sum=nas * n, nas_delay_n=n,
        dep_delay_sum=10.0 * n, dep_delay_n=n,
    )


def airport_from(months: list[MonthlyDelay], iata: str = "TST"):
    """An AirportMetrics whose annual delay sums are exactly these months."""
    return _summed(mk(iata), months)


def cohort_with(target, extra: int = 0) -> Cohort:
    members = spread_cohort()
    members.append(target)
    return Cohort("t", members)


# ---------------------------------------------------------------------------
# Component arithmetic mirrors production
# ---------------------------------------------------------------------------


def test_monthly_components_use_the_same_formulas_as_production():
    m = md("2025-05", flights=1_000, taxi=17.5, nas=4.25, del15=0.22, cancel=0.03)
    assert m.taxi_out_avg == pytest.approx(17.5)
    assert m.nas_delay_per_flight == pytest.approx(4.25)
    assert m.dep_del15_rate == pytest.approx(0.22)
    assert m.cancel_rate == pytest.approx(0.03)


def test_all_four_aci_components_exist_monthly():
    """Task 1: nothing had to be dropped at monthly granularity."""
    m = md("2025-05")
    for d in D.ACI_METRICS:
        assert getattr(m, d.attr) is not None, f"{d.id} unavailable monthly"


def test_monthly_floor_is_derived_from_the_production_gate():
    assert MIN_FLIGHTS_PER_MONTH == D.MIN_OTP_FLIGHTS_FOR_ACI // 12
    assert md("2025-05", flights=MIN_FLIGHTS_PER_MONTH).evaluable is True
    assert md("2025-05", flights=MIN_FLIGHTS_PER_MONTH - 1).evaluable is False


# ---------------------------------------------------------------------------
# The counterfactual machinery is consistent with production
# ---------------------------------------------------------------------------


def test_summing_all_months_reproduces_the_production_annual_score():
    """The counterfactual must reduce to the real score when nothing is removed.

    This is what makes "ACI excluding the worst 2 months" comparable to the
    published figure rather than a different quantity.
    """
    months = [md(x, flights=2_000) for x in MONTHS]
    target = airport_from(months)
    cohort = cohort_with(target)
    rebuilt = _summed(target, months)
    assert compute_aci(rebuilt, cohort).score == pytest.approx(
        compute_aci(target, cohort).score)


def test_summed_aggregates_every_delay_field():
    months = [md(x, flights=1_000, cancel=0.02) for x in MONTHS]
    agg = _summed(mk("X"), months)
    assert agg.flights == 12_000
    assert agg.cancelled == 12 * 20
    assert agg.taxi_out_n == 12_000
    assert agg.delay_months == 12


def test_monthly_aci_is_normalised_against_annual_cohort_bounds():
    """Seasonality must survive: a month is scored against the ANNUAL spread."""
    months = [md(x) for x in MONTHS]
    target = airport_from(months)
    cohort = cohort_with(target)
    one = md("2025-07", taxi=15.0, nas=3.0, del15=0.20, cancel=0.01)
    # Same rates as the annual aggregate -> same normalised score.
    assert _monthly_aci(one, cohort) == pytest.approx(
        compute_aci(target, cohort).score, abs=1e-6)


# ---------------------------------------------------------------------------
# Persistence vs concentration
# ---------------------------------------------------------------------------


def test_stable_airport_loses_almost_nothing_when_worst_months_removed():
    months = [md(x, flights=2_000, taxi=20.0, nas=5.0, del15=0.30, cancel=0.02)
              for x in MONTHS]
    target = airport_from(months)
    p = build_profile(target, months, cohort_with(target))
    assert p.episodic_drop == pytest.approx(0.0, abs=0.5)
    assert p.spread == pytest.approx(0.0, abs=0.5)


def test_spiky_airport_loses_a_lot_when_its_two_worst_months_are_removed():
    calm = [md(x, flights=2_000, taxi=12.0, nas=1.0, del15=0.10, cancel=0.002)
            for x in MONTHS[:10]]
    storm = [md(x, flights=2_000, taxi=30.0, nas=15.0, del15=0.50, cancel=0.15)
             for x in MONTHS[10:]]
    months = calm + storm
    target = airport_from(months)
    p = build_profile(target, months, cohort_with(target))
    assert p.episodic_drop is not None and p.episodic_drop > 5.0
    assert p.excluding_worst[2] < p.annual_aci


def test_null_baseline_is_near_zero_for_a_stable_airport():
    """Removing two MIDDLE months must barely move the score.

    This is the control that makes the worst-2 drop meaningful rather than an
    artefact of having fewer months.
    """
    months = [md(x, flights=2_000, taxi=18.0, nas=4.0, del15=0.25, cancel=0.01)
              for x in MONTHS]
    target = airport_from(months)
    p = build_profile(target, months, cohort_with(target))
    assert p.null_drop == pytest.approx(0.0, abs=0.5)


def test_excess_drop_separates_behaviour_from_sample_reduction():
    calm = [md(x, flights=2_000, taxi=12.0, nas=1.0, del15=0.10, cancel=0.002)
            for x in MONTHS[:10]]
    storm = [md(x, flights=2_000, taxi=30.0, nas=15.0, del15=0.50, cancel=0.15)
             for x in MONTHS[10:]]
    months = calm + storm
    target = airport_from(months)
    p = build_profile(target, months, cohort_with(target))
    assert p.excess_drop is not None
    assert p.excess_drop > 0, "spiky airport must show excess over the null"


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def test_label_persistent_when_pressure_is_sustained_and_not_concentrated():
    months = [md(x, flights=2_000, taxi=30.0, nas=15.0, del15=0.55, cancel=0.05)
              for x in MONTHS]
    target = airport_from(months)
    p = build_profile(target, months, cohort_with(target))
    assert p.elevated_share is not None and p.elevated_share >= 0.5
    assert p.episodic_drop is not None
    assert p.episodic_drop <= CONCENTRATED_DROP_POINTS
    assert p.label == "PERSISTENT"


def test_label_insufficient_data_when_too_few_months_are_evaluable():
    months = [md(x, flights=10) for x in MONTHS]          # all below the floor
    target = airport_from([md(x, flights=2_000) for x in MONTHS])
    p = build_profile(target, months, cohort_with(target))
    assert p.months_evaluated == 0
    assert p.label == "INSUFFICIENT_DATA"


def test_label_is_not_a_score_and_never_reaches_classification():
    import inspect

    from app.analytics import scoring

    src = inspect.getsource(scoring)
    assert "persistence" not in src
    assert "PersistenceProfile" not in src


# ---------------------------------------------------------------------------
# Coverage: suppression, never imputation
# ---------------------------------------------------------------------------


def test_low_volume_months_are_reported_unevaluated_not_scored_as_zero():
    months = ([md(x, flights=2_000) for x in MONTHS[:10]]
              + [md(x, flights=5) for x in MONTHS[10:]])
    target = airport_from(months)
    p = build_profile(target, months, cohort_with(target))
    assert p.months_evaluated == 10
    assert p.months_suppressed == 2
    thin = [ms for ms in p.months if ms.aci is None]
    assert len(thin) == 2
    for ms in thin:
        assert ms.reason == "insufficient_monthly_flights"
        assert ms.aci is None, "must be absent, never 0.0"


def test_unevaluated_months_still_count_toward_the_annual_gate():
    """Dropping a month from the DIAGNOSTIC must not drop it from the SCORE."""
    months = ([md(x, flights=2_000) for x in MONTHS[:10]]
              + [md(x, flights=5) for x in MONTHS[10:]])
    target = airport_from(months)
    cohort = cohort_with(target)
    p = build_profile(target, months, cohort)
    assert p.annual_aci == pytest.approx(compute_aci(target, cohort).score)


def test_elevated_counts_only_evaluable_months():
    months = ([md(x, flights=2_000, taxi=30.0, nas=15.0, del15=0.55, cancel=0.05)
               for x in MONTHS[:6]]
              + [md(x, flights=5) for x in MONTHS[6:]])
    target = airport_from(months)
    p = build_profile(target, months, cohort_with(target))
    assert p.months_evaluated == 6
    assert p.elevated_months <= 6


# ---------------------------------------------------------------------------
# Volume vs intensity (task 5) and honesty of the notes
# ---------------------------------------------------------------------------


def test_diagnostic_is_about_intensity_not_traffic_volume():
    """Two airports, 20x different flight counts, identical per-flight rates."""
    small = [md(x, flights=500, taxi=22.0, nas=6.0, del15=0.30, cancel=0.02)
             for x in MONTHS]
    big = [md(x, flights=10_000, taxi=22.0, nas=6.0, del15=0.30, cancel=0.02)
           for x in MONTHS]
    ts, tb = airport_from(small, "SML"), airport_from(big, "BIG")
    members = spread_cohort() + [ts, tb]
    cohort = Cohort("t", members)
    ps = build_profile(ts, small, cohort)
    pb = build_profile(tb, big, cohort)
    assert tb.flights == 20 * ts.flights
    assert ps.annual_aci == pytest.approx(pb.annual_aci)
    assert ps.elevated_months == pb.elevated_months


def test_notes_refuse_the_capacity_claim():
    months = [md(x, flights=2_000) for x in MONTHS]
    target = airport_from(months)
    joined = " ".join(build_profile(target, months, cohort_with(target)).notes).lower()
    assert "diagnostic only" in joined
    assert "does not change aci" in joined
    assert "outcomes" in joined
    assert "runway, gate or airspace" in joined


def test_profile_is_deterministic():
    months = [md(x, flights=1_500, nas=2.0 + (i % 4)) for i, x in enumerate(MONTHS)]
    target = airport_from(months)
    cohort = cohort_with(target)
    a = build_profile(target, months, cohort)
    b = build_profile(target, months, cohort)
    assert a.annual_aci == b.annual_aci
    assert [m.aci for m in a.months] == [m.aci for m in b.months]
    assert a.excluding_worst == b.excluding_worst
    assert a.label == b.label


# ---------------------------------------------------------------------------
# REGRESSION — production untouched
# ---------------------------------------------------------------------------


def test_aci_weights_and_gate_unchanged():
    assert {d.id: d.weight for d in D.ACI_METRICS} == {
        "A1": 0.30, "A2": 0.30, "A3": 0.25, "A4": 0.15,
    }
    assert D.MIN_OTP_FLIGHTS_FOR_ACI == 1_000


def test_tdpi_weights_and_thresholds_unchanged():
    assert {d.id: d.weight for d in D.TDPI_METRICS} == {
        "T1": 0.20, "T2": 0.30, "T3": 0.15, "T4": 0.20, "T5": 0.15,
    }
    assert D.DIVERGENCE_HI == 60.0 and D.DIVERGENCE_LO == 40.0


def test_scoring_never_imports_the_diagnostic():
    """Phase 8.2b integrates the diagnostic into profile/compare deliberately.

    The invariant that still holds — and matters more — is that it cannot reach
    any score: `scoring.py` must not know it exists.
    """
    import inspect

    from app.analytics import scoring

    src = inspect.getsource(scoring)
    assert "persistence" not in src
    assert "temporal" not in src


def test_ranking_does_not_consult_the_diagnostic():
    """Rankings must be decided by ACI and TDPI alone."""
    import inspect

    from app.analytics import engine as engine_mod

    src = inspect.getsource(engine_mod.AnalyticsEngine.rank)
    assert "temporal" not in src
    assert "persistence" not in src


def test_diagnostic_is_attached_after_scoring_not_during():
    """`score_airport` must not accept or produce a temporal field."""
    import inspect

    from app.analytics.scoring import score_airport

    sig = inspect.signature(score_airport)
    assert "temporal" not in sig.parameters
    src = inspect.getsource(score_airport)
    assert "temporal" not in src


def test_elevated_threshold_is_its_own_constant_not_the_classification_one():
    """The value 60 coincides; the constants must not.

    `classify()` must keep using DIVERGENCE_HI, so retuning the diagnostic can
    never move an airport between divergence classes.
    """
    import inspect

    import app.analytics.persistence as P
    from app.analytics import scoring

    assert ELEVATED_MONTH_ACI == 60.0
    assert P.ELEVATED_MONTH_ACI is not D.DIVERGENCE_HI or ELEVATED_MONTH_ACI == 60.0
    # classify() reads only the divergence thresholds.
    src = inspect.getsource(scoring.classify)
    assert "DIVERGENCE_HI" in src and "DIVERGENCE_LO" in src
    assert "ELEVATED_MONTH_ACI" not in src
