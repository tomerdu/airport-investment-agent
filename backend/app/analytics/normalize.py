"""Cohort normalisation.

Implements the approved formula: winsorize each raw value to the cohort's
5th/95th percentile, then min-max scale that winsorized range onto 0-100.

    x̃ = min(max(x, P5), P95)
    n  = 100 * (x̃ - P5) / (P95 - P5)

Why winsorize rather than plain min-max: one outlier compresses everyone else
into a narrow band. The FAA enplanement growth column, for example, contains a
+126,403% value from a tiny airport starting near-zero service; without
winsorization every other airport would normalise to ~0.

Documented methodology clarification
------------------------------------
The Phase 1 proposal called this "winsorized percentile rank" and then
glossed a score of 94 as "worse than ~94% of peers". Those are two different
statistics, and the *formula* is the one that was specified, so it is what is
implemented. The gloss is wrong for it: under winsorized min-max, 94 means
"94% of the way from the cohort's 5th to its 95th percentile", which preserves
magnitude (an airport twice as congested scores higher) where a rank would
only preserve order.

Because the rank reading is genuinely useful for interpretation, every
component also reports a true `percentile` alongside `normalized` — clearly
labelled, and never used in the arithmetic.
"""

from __future__ import annotations

from .definitions import WINSOR_HIGH_PCTL, WINSOR_LOW_PCTL


def percentile(sorted_values: list[float], p: float) -> float:
    """Linear-interpolation percentile over an ascending list (numpy default).

    Pure Python and deterministic: the same input always produces bit-identical
    output, which the reproducibility tests depend on.
    """
    if not sorted_values:
        raise ValueError("percentile of empty sequence")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (p / 100.0) * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac


class CohortStats:
    """Winsorization bounds for one metric over one cohort."""

    __slots__ = ("values", "p_low", "p_high", "n", "degenerate")

    def __init__(self, values: list[float]) -> None:
        self.values = sorted(values)
        self.n = len(self.values)
        if self.n == 0:
            self.p_low = self.p_high = 0.0
            self.degenerate = True
        else:
            self.p_low = percentile(self.values, WINSOR_LOW_PCTL)
            self.p_high = percentile(self.values, WINSOR_HIGH_PCTL)
            self.degenerate = self.p_high <= self.p_low

    def normalize(self, x: float | None) -> float | None:
        """Winsorized min-max onto 0-100, or None if the value is missing."""
        if x is None or self.n == 0:
            return None
        if self.degenerate:
            # Every value in the 5-95 band is identical; a spread of zero
            # carries no information, so we return the neutral midpoint rather
            # than dividing by zero or implying discrimination we do not have.
            return 50.0
        clamped = min(max(x, self.p_low), self.p_high)
        return 100.0 * (clamped - self.p_low) / (self.p_high - self.p_low)

    def percentile_of(self, x: float | None) -> float | None:
        """True rank: percent of cohort values at or below x. Informational."""
        if x is None or self.n == 0:
            return None
        # Bisect-right over the ascending list.
        lo, hi = 0, self.n
        while lo < hi:
            mid = (lo + hi) // 2
            if self.values[mid] <= x:
                lo = mid + 1
            else:
                hi = mid
        return 100.0 * lo / self.n

    def value_at_percentile(self, p: float) -> float | None:
        if self.n == 0:
            return None
        return percentile(self.values, p)


def build_cohort_stats(
    rows: list[object], attrs: list[str]
) -> dict[str, CohortStats]:
    """Compute winsorization bounds for each attribute across the cohort.

    Missing values are excluded from the cohort distribution rather than
    imputed — a gap must not shift the bounds it is measured against.
    """
    out: dict[str, CohortStats] = {}
    for attr in attrs:
        vals = [
            v for v in (getattr(r, attr, None) for r in rows)
            if v is not None
        ]
        out[attr] = CohortStats(vals)
    return out
