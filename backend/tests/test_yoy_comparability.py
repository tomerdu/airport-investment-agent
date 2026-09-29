"""Phase 8.1c — the year-over-year window-comparability guard on v1's T2.

THE RULE: a YoY passenger-growth ratio is computed only when every month of the
current window has a prior-year counterpart, and the ratio is summed over the
matched months alone. There is no month-count threshold.

The three cases that fixed the design, all present in the real warehouse:

  GUF  12 window months vs 2 prior months (7 passengers total) -> suppressed
  WYS   6 window vs 6 prior, months align exactly              -> KEPT (+22%)
  GST  11 window vs 11 prior, but Dec-2025 vs Apr-2025         -> suppressed

WYS is why an absolute month floor was rejected; GST is why equal month counts
were rejected.

No network, no API calls.
"""

from __future__ import annotations

import pytest

from app.analytics import definitions as D
from app.analytics.metrics import AirportMetrics
from app.analytics.scoring import Cohort, compute_tdpi
from tests.test_analytics_units import mk, spread_cohort
from tests.test_tdpi_v2 import months, with_series


def series(iata: str, window: dict[str, float], prior: dict[str, float],
           **kw) -> AirportMetrics:
    """Metrics with explicit, possibly misaligned, monthly series."""
    m = mk(iata, **kw)
    m.monthly_passengers = dict(window)
    m.monthly_passengers_prior = dict(prior)
    m.passengers = sum(window.values())
    m.passengers_prior = sum(prior.values())
    m.traffic_months = len(window)
    m.traffic_months_prior = len(prior)
    return m


# ---------------------------------------------------------------------------
# Matching months — the happy path
# ---------------------------------------------------------------------------


def test_fully_aligned_windows_are_comparable():
    m = with_series("A", window=[110.0] * 12, prior=[100.0] * 12)
    assert m.yoy_windows_aligned is True
    assert m.yoy_months_matched == 12
    assert m.pax_growth == pytest.approx(0.10)


def test_aligned_ratio_equals_the_legacy_annual_ratio():
    """For an airport with complete data the guard changes nothing at all."""
    m = with_series("B", window=[100.0, 200.0, 300.0] + [150.0] * 9,
                    prior=[90.0, 180.0, 280.0] + [140.0] * 9)
    legacy = m.passengers / m.passengers_prior - 1.0
    assert m.pax_growth == pytest.approx(legacy)


# ---------------------------------------------------------------------------
# Valid seasonal windows — the WYS case, which must NOT be suppressed
# ---------------------------------------------------------------------------


def test_seasonal_airport_with_matching_months_keeps_its_growth():
    """WYS (Yellowstone): 6 window and 6 prior months, May-Oct both years."""
    wys = series(
        "WYS",
        window=months(2025, 5, [100.0, 200.0, 400.0, 380.0, 180.0, 60.0]),
        prior=months(2024, 5, [90.0, 170.0, 330.0, 300.0, 140.0, 50.0]),
    )
    assert wys.traffic_months == 6 and wys.traffic_months_prior == 6
    assert wys.yoy_windows_aligned is True
    assert wys.yoy_months_matched == 6
    assert wys.pax_growth is not None
    assert wys.pax_growth == pytest.approx(1320.0 / 1080.0 - 1.0)


def test_a_six_month_seasonal_airport_is_not_penalised_for_being_seasonal():
    """The rule counts ALIGNMENT, never the number of months."""
    short = series("S6", window=months(2025, 6, [50.0] * 4),
                   prior=months(2024, 6, [40.0] * 4))
    long = with_series("L12", window=[50.0] * 12, prior=[40.0] * 12)
    assert short.yoy_windows_aligned is long.yoy_windows_aligned is True
    assert short.pax_growth == pytest.approx(long.pax_growth)


# ---------------------------------------------------------------------------
# Missing months — the GUF case
# ---------------------------------------------------------------------------


def test_thin_prior_window_is_suppressed_not_scored():
    """GUF: 12 window months, 2 prior months totalling 7 passengers."""
    guf = series(
        "GUF",
        window=months(2025, 5, [1570.0, 6528.0, 7051.0, 5234.0, 3154.0, 5535.0,
                                5088.0, 3743.0, 3642.0, 4760.0, 5660.0, 6812.0]),
        prior={"2024-06": 5.0, "2024-08": 2.0},
    )
    assert guf.yoy_windows_aligned is False
    assert guf.yoy_months_matched == 2
    assert guf.pax_growth is None, "must not report a growth figure at all"


def test_the_legacy_ratio_this_replaces_was_absurd():
    """Records what production produced before the guard: +839,571%."""
    guf = series(
        "GUF",
        window=months(2025, 5, [1570.0, 6528.0, 7051.0, 5234.0, 3154.0, 5535.0,
                                5088.0, 3743.0, 3642.0, 4760.0, 5660.0, 6812.0]),
        prior={"2024-06": 5.0, "2024-08": 2.0},
    )
    legacy = guf.passengers / guf.passengers_prior - 1.0
    assert legacy > 8000          # > 800,000%
    assert guf.pax_growth is None


def test_matched_months_alone_would_not_have_saved_guf():
    """Why the rule demands COMPLETE alignment, not just an overlap.

    Restricting GUF's ratio to its two matched months still yields +167,929%,
    because the prior year does not cover the airport's operation.
    """
    guf = series(
        "GUF",
        window=months(2025, 5, [1570.0, 6528.0, 7051.0, 5234.0, 3154.0, 5535.0,
                                5088.0, 3743.0, 3642.0, 4760.0, 5660.0, 6812.0]),
        prior={"2024-06": 5.0, "2024-08": 2.0},
    )
    cur, prior, matched, unmatched = guf._yoy_passenger_totals()
    assert matched == 2 and unmatched == 10
    assert cur / prior - 1.0 > 1000        # still > 100,000%
    assert guf.pax_growth is None          # so the component is dropped instead


def test_a_single_missing_prior_month_suppresses_the_ratio():
    """GST/KLW: 11 window and 11 prior months, but Dec has no counterpart.

    This is the case equal-month-count rules let through. The window contains
    2025-12 while the prior side contains 2025-04 instead, so a SUM/SUM ratio
    compares December traffic against April traffic.
    """
    gst = series(
        "GST",
        window=months(2025, 5, [100.0] * 11),                  # 2025-05..2026-03
        prior={**months(2024, 5, [100.0] * 7), "2025-01": 100.0,
               "2025-02": 100.0, "2025-03": 100.0, "2025-04": 100.0},
    )
    assert gst.traffic_months == gst.traffic_months_prior == 11
    assert gst.yoy_windows_aligned is False, "equal counts are not equal months"
    assert gst.yoy_months_matched == 10
    assert gst.pax_growth is None


def test_extra_prior_month_is_excluded_from_the_denominator():
    """BGM/BLD: 11 window months, 12 prior months.

    The unmatched prior month must not inflate the denominator — otherwise the
    ratio compares 11 months of traffic against 12.
    """
    bgm = series(
        "BGM",
        window=months(2025, 5, [100.0] * 11),
        prior=months(2024, 5, [100.0] * 12),
    )
    assert bgm.yoy_windows_aligned is True      # every WINDOW month is matched
    assert bgm.yoy_months_matched == 11
    # Naive SUM/SUM would give 1100/1200 - 1 = -8.3%; like-for-like is 0%.
    assert bgm.passengers / bgm.passengers_prior - 1.0 == pytest.approx(-1 / 12)
    assert bgm.pax_growth == pytest.approx(0.0)


def test_zero_prior_traffic_in_matched_months_is_suppressed():
    m = series("Z", window=months(2025, 5, [100.0] * 12),
               prior=months(2024, 5, [0.0] * 12))
    assert m.pax_growth is None


# ---------------------------------------------------------------------------
# Suppression and renormalisation through the scoring machinery
# ---------------------------------------------------------------------------


def test_suppressed_t2_is_dropped_and_weights_renormalise_to_one():
    members = spread_cohort()
    cohort = Cohort("t", members)
    target = members[5]
    target.monthly_passengers = months(2025, 5, [100.0] * 12)
    target.monthly_passengers_prior = {"2024-06": 5.0}
    assert target.pax_growth is None

    r = compute_tdpi(target, cohort)
    t2 = next(c for c in r.components if c.id == "T2")
    assert t2.available is False
    assert t2.normalized is None
    assert t2.contribution is None, "a dropped component contributes nothing"

    eff = [c.effective_weight for c in r.components if c.effective_weight is not None]
    assert sum(eff) == pytest.approx(1.0), "surviving weights must renormalise"
    assert r.score is not None, "0.70 coverage clears the 0.60 floor"


def test_suppressed_t2_is_never_imputed_as_zero_growth():
    """A dropped component must not be silently treated as average or zero."""
    members = spread_cohort()
    cohort = Cohort("t", members)

    a, b = members[4], members[5]
    for m in (a, b):
        m.monthly_passengers = months(2025, 5, [100.0] * 12)
    a.monthly_passengers_prior = months(2024, 5, [100.0] * 12)   # aligned, 0%
    b.monthly_passengers_prior = {"2024-06": 5.0}                # suppressed

    ra, rb = compute_tdpi(a, cohort), compute_tdpi(b, cohort)
    t2a = next(c for c in ra.components if c.id == "T2")
    t2b = next(c for c in rb.components if c.id == "T2")
    assert t2a.available is True and t2b.available is False
    assert t2a.effective_weight == pytest.approx(0.30)
    assert t2b.effective_weight is None
    # b's other components are each worth more, because T2's weight is shared out.
    t1a = next(c for c in ra.components if c.id == "T1")
    t1b = next(c for c in rb.components if c.id == "T1")
    assert t1b.effective_weight > t1a.effective_weight


def test_coverage_reported_when_t2_is_dropped():
    members = spread_cohort()
    cohort = Cohort("t", members)
    target = members[3]
    target.monthly_passengers = months(2025, 5, [100.0] * 12)
    target.monthly_passengers_prior = {"2024-06": 5.0}
    r = compute_tdpi(target, cohort)
    assert r.coverage == pytest.approx(0.70)
    assert r.suppressed_reason is None


# ---------------------------------------------------------------------------
# Backward compatibility and scope
# ---------------------------------------------------------------------------


def test_no_monthly_series_falls_back_to_the_legacy_annual_ratio():
    """Fixtures and any future loader without a monthly series must still score."""
    m = mk("NOSERIES", passengers=110.0, passengers_prior=100.0)
    assert m.monthly_passengers == {}
    assert m.yoy_windows_aligned is True, "cannot judge alignment without data"
    assert m.pax_growth == pytest.approx(0.10)


def test_guard_does_not_touch_t5_enplanement_growth():
    """T5 is FAA annual enplanements — a different source with no monthly series."""
    m = series("X", window=months(2025, 5, [100.0] * 12),
               prior={"2024-06": 5.0},
               enplanements=500_000.0, enplanements_prior=400_000.0,
               enplanement_growth=0.25)
    assert m.pax_growth is None                      # T2 suppressed
    assert m.enplanement_growth == pytest.approx(0.25)   # T5 untouched


def test_t2_note_documents_the_like_for_like_rule():
    t2 = next(d for d in D.TDPI_METRICS if d.id == "T2")
    note = (t2.note or "").lower()
    assert "like-for-like" in note
    assert "dropped" in note


def test_v1_weights_and_thresholds_unchanged():
    assert {d.id: d.weight for d in D.TDPI_METRICS} == {
        "T1": 0.20, "T2": 0.30, "T3": 0.15, "T4": 0.20, "T5": 0.15,
    }
    assert D.DIVERGENCE_HI == 60.0 and D.DIVERGENCE_LO == 40.0


def test_the_rejected_threshold_constants_are_gone():
    """No arbitrary month minimum survives anywhere in the definitions."""
    assert not hasattr(D, "MIN_PRIOR_MONTHS_FOR_GROWTH")
    assert not hasattr(D, "MAX_WINDOW_MONTH_SHORTFALL")


def test_guard_is_deterministic():
    a = series("D", window=months(2025, 5, [100.0] * 11),
               prior=months(2024, 5, [100.0] * 12))
    b = series("D", window=months(2025, 5, [100.0] * 11),
               prior=months(2024, 5, [100.0] * 12))
    assert a.pax_growth == b.pax_growth
    assert a.yoy_windows_aligned == b.yoy_windows_aligned
