"""ACI temporal persistence — a DIAGNOSTIC, not a score (Phase 8.2).

EXPERIMENTAL and opt-in. Nothing here is wired into `score_airport()`, the API,
the agent or the frontend. It changes no ACI component, weight or threshold.

The question it answers: when an airport's annual ACI is high, is that pressure
present through the window, or is the annual figure carried by a few unusual
months?

Three things make this non-trivial, and each is handled explicitly:

1. **The annual ACI is a ratio of sums, not a mean of monthly ratios.** Every
   component is `SUM(numerator) / SUM(denominator)` over the window, so busy
   months already count for more. A diagnostic that averaged monthly rates would
   describe a different quantity from the one production reports.

2. **Monthly rates on small samples are noise.** The production index refuses
   ACI below 1,000 window flights for exactly this reason. The same logic
   applies per month, so a month below `MIN_FLIGHTS_PER_MONTH` is reported as
   unevaluable rather than scored.

3. **Seasonality is real and is not a defect.** Cohort-wide NAS delay per flight
   nearly doubles in summer and cancellations swing roughly ninefold across the
   year. Monthly values are therefore normalised against the ANNUAL cohort
   bounds, so a month that is bad for everyone shows up as bad for everyone
   rather than being normalised away.

None of this measures a capacity constraint. OTP records observed operational
outcomes; it does not identify a runway, gate or airspace cause.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .definitions import ACI_METRICS, MIN_OTP_FLIGHTS_FOR_ACI
from .metrics import AirportMetrics
from .scoring import Cohort, compute_aci

# A month carrying fewer than this many reported flights is not scored. Derived
# from the production annual gate (1,000 flights per 12-month window) rather
# than chosen independently, so the diagnostic inherits the index's own
# tolerance for small samples instead of inventing a second one.
MIN_FLIGHTS_PER_MONTH = MIN_OTP_FLIGHTS_FOR_ACI // 12          # 83

# A month is called "elevated" at or above this monthly ACI. The value matches
# the cohort-relative sense of "high" used elsewhere in the system, but this
# constant is deliberately separate: the diagnostic never feeds `classify()`,
# and the raw monthly values ship alongside it so no conclusion rests on the
# exact figure — a reader can apply their own cut.
ELEVATED_MONTH_ACI = 60.0

# Below this share of evaluable months elevated, sustained pressure is not
# evident; at or above it, it is. Reported alongside the raw counts so a reader
# can apply their own cut.
PERSISTENT_SHARE = 0.50

# At or above this ACI, the normalised score is at the cohort ceiling: the raw
# component values exceed the winsorization P95, so removing bad months cannot
# move the score until the remainder falls below P95. A concentration of ~0 there
# is an artefact of clipping and is NOT evidence of temporal stability, so it is
# reported as unreliable rather than as a low number. ASE is the current case.
CEILING_ACI = 99.0

# Months in the analysis window, so "available vs expected" can be reported.
MONTHS_EXPECTED = 12

# An annual score that falls by more than this many points when its two worst
# months are removed is CONCENTRATED in those months.
#
# Calibrated from the measured cohort rather than chosen a priori. On the
# committed warehouse the worst-2 drop is distributed
# p25 +4.1 / median +6.1 / p75 +8.1 / max +17.2, so an earlier guess of 5.0 sat
# below the median and labelled the typical airport "episodic" — describing the
# cohort norm as though it were an exception. 8.0 is the measured p75: the
# quarter of airports whose annual figure leans hardest on its worst two months.
#
# That the drop measures behaviour at all is established by `null_drop`: removing
# two MIDDLE-ranked months instead moves the score by a median of only -0.2
# points, so the worst-2 drop is not an artefact of having fewer months.
CONCENTRATED_DROP_POINTS = 8.0

_DELAY_FIELDS = (
    "flights", "cancelled", "diverted", "dep_del15", "dep_del15_n",
    "taxi_out_sum", "taxi_out_n", "nas_delay_sum", "nas_delay_n",
    "dep_delay_sum", "dep_delay_n",
)


@dataclass(frozen=True)
class MonthlyDelay:
    """One airport-month of raw OTP sums, straight from the warehouse."""

    month: str
    flights: int
    cancelled: int
    diverted: int
    dep_del15: int
    dep_del15_n: int
    taxi_out_sum: float
    taxi_out_n: int
    nas_delay_sum: float
    nas_delay_n: int
    dep_delay_sum: float
    dep_delay_n: int

    @property
    def evaluable(self) -> bool:
        return self.flights >= MIN_FLIGHTS_PER_MONTH

    @property
    def taxi_out_avg(self) -> float | None:
        return self.taxi_out_sum / self.taxi_out_n if self.taxi_out_n else None

    @property
    def nas_delay_per_flight(self) -> float | None:
        return self.nas_delay_sum / self.nas_delay_n if self.nas_delay_n else None

    @property
    def dep_del15_rate(self) -> float | None:
        return self.dep_del15 / self.dep_del15_n if self.dep_del15_n else None

    @property
    def cancel_rate(self) -> float | None:
        return self.cancelled / self.flights if self.flights else None


@dataclass(frozen=True)
class MonthScore:
    month: str
    flights: int
    aci: float | None
    reason: str | None = None          # why it is None


@dataclass
class PersistenceProfile:
    """Diagnostic summary for one airport. Carries no score of its own."""

    iata: str
    annual_aci: float | None
    months: list[MonthScore]
    excluding_worst: dict[int, float | None]
    notes: list[str]
    # Null baseline: ACI with two MIDDLE-ranked months removed. Same reduction
    # in sample size as `excluding_worst[2]`, but no extreme months, so it shows
    # how much of the worst-2 drop is arithmetic rather than behaviour.
    excluding_middle: float | None = None

    # -- coverage ------------------------------------------------------
    @property
    def evaluable_months(self) -> list[MonthScore]:
        return [m for m in self.months if m.aci is not None]

    @property
    def months_evaluated(self) -> int:
        return len(self.evaluable_months)

    @property
    def months_suppressed(self) -> int:
        return len(self.months) - self.months_evaluated

    # -- persistence ---------------------------------------------------
    @property
    def elevated_months(self) -> int:
        return sum(1 for m in self.evaluable_months
                   if m.aci is not None and m.aci >= ELEVATED_MONTH_ACI)

    @property
    def elevated_share(self) -> float | None:
        n = self.months_evaluated
        return self.elevated_months / n if n else None

    @property
    def episodic_drop(self) -> float | None:
        """Points the annual ACI loses when its two worst months are removed."""
        if self.annual_aci is None:
            return None
        alt = self.excluding_worst.get(2)
        return None if alt is None else self.annual_aci - alt

    @property
    def null_drop(self) -> float | None:
        """Points lost when two MIDDLE-ranked months are removed instead.

        The control for `episodic_drop`. Near zero means the worst-2 drop
        reflects those months' behaviour rather than the smaller sample.
        """
        if self.annual_aci is None or self.excluding_middle is None:
            return None
        return self.annual_aci - self.excluding_middle

    @property
    def excess_drop(self) -> float | None:
        """Worst-2 drop net of the null baseline."""
        d, n = self.episodic_drop, self.null_drop
        return None if d is None or n is None else d - n

    @property
    def spread(self) -> float | None:
        vals = [m.aci for m in self.evaluable_months if m.aci is not None]
        return max(vals) - min(vals) if len(vals) >= 2 else None

    # -- ceiling -------------------------------------------------------
    @property
    def at_ceiling(self) -> bool:
        """Is the score clipped by winsorization, making concentration moot?

        When both the published score and the counterfactual sit at the cohort
        ceiling, removing months cannot move the figure. See `CEILING_ACI`.
        """
        if self.annual_aci is None or self.annual_aci < CEILING_ACI:
            return False
        alt = self.excluding_worst.get(2)
        return alt is None or alt >= CEILING_ACI

    @property
    def concentration_reliable(self) -> bool:
        return self.episodic_drop is not None and not self.at_ceiling

    # -- pattern -------------------------------------------------------
    @property
    def temporal_pattern(self) -> str:
        """PERSISTENT / EPISODIC / INTERMITTENT / INSUFFICIENT_DATA.

        Named `temporal_pattern`, not `class`, and its intermediate value is
        INTERMITTENT rather than MIXED, so it can never be confused with the
        divergence classification's MIXED. It is not a score, never an input to
        `classify()`, and never an assertion about physical capacity.
        Thresholds are cohort-calibrated; see `CONCENTRATED_DROP_POINTS`.
        """
        if self.annual_aci is None or self.months_evaluated < 6:
            return "INSUFFICIENT_DATA"
        share, drop = self.elevated_share, self.episodic_drop
        if share is None or drop is None:
            return "INSUFFICIENT_DATA"
        # At the ceiling the concentration measure carries no information, so
        # the pattern rests on the elevated share alone.
        if self.at_ceiling:
            return "PERSISTENT" if share >= PERSISTENT_SHARE else "INTERMITTENT"
        sustained = share >= PERSISTENT_SHARE
        concentrated = drop > CONCENTRATED_DROP_POINTS
        if sustained and not concentrated:
            return "PERSISTENT"
        if not sustained and concentrated:
            return "EPISODIC"
        return "INTERMITTENT"

    # Retained so the Phase 8.2 evaluation harness keeps working unchanged.
    @property
    def label(self) -> str:
        return self.temporal_pattern

    @property
    def no_elevated_months(self) -> bool:
        """Did no evaluated month reach the elevated threshold?

        Distinguishes "pressure came and went" from "there was never elevated
        pressure at all". An INTERMITTENT airport with zero elevated months —
        LAX, at ACI 37.9 — must not read as intermittent congestion.
        """
        return self.months_evaluated > 0 and self.elevated_months == 0

    @property
    def pattern_label(self) -> str:
        """Short human-readable form of `temporal_pattern`."""
        if self.temporal_pattern == "INSUFFICIENT_DATA":
            return "Not enough evaluable months"
        if self.no_elevated_months:
            # Nothing crossed the threshold, so "partly concentrated" would
            # overstate it.
            return "No elevated months"
        if self.temporal_pattern == "PERSISTENT" and self.at_ceiling:
            # The concentration measure is unavailable here, so the label must
            # not borrow its meaning ("does not depend on a few months").
            return "Elevated in most months (concentration unmeasurable)"
        return {
            "PERSISTENT": "Sustained across the window",
            "EPISODIC": "Concentrated in a few months",
            "INTERMITTENT": "Partly concentrated",
        }[self.temporal_pattern]

    @property
    def elevated_season(self) -> str | None:
        """Which part of the year the elevated months fall in — DESCRIPTIVE.

        Names the months. It does not attribute a cause: OTP records outcomes,
        so "winter-concentrated operational pressure" is a statement about
        timing, never about weather.
        """
        hits = [m.month for m in self.evaluable_months
                if m.aci is not None and m.aci >= ELEVATED_MONTH_ACI]
        if not hits:
            return None
        groups = {"winter": {12, 1, 2}, "spring": {3, 4, 5},
                  "summer": {6, 7, 8}, "autumn": {9, 10, 11}}
        counts = {g: sum(1 for h in hits if int(h[-2:]) in mset)
                  for g, mset in groups.items()}
        top = max(counts, key=lambda g: counts[g])
        if counts[top] < len(hits) / 2:
            return "spread across the year"
        return f"{top}-concentrated"

    @property
    def description(self) -> str:
        """One sentence. Describes distribution, never cause."""
        n = self.months_evaluated
        if self.temporal_pattern == "INSUFFICIENT_DATA":
            return (f"Only {n} month(s) carry enough reported flights to "
                    f"evaluate, so the temporal distribution of this score "
                    f"cannot be characterised.")
        if self.elevated_months == 0:
            drop = self.episodic_drop
            tail = (f" Removing the two worst months lowers the annual score by "
                    f"{drop:.1f} points." if drop is not None else "")
            return (f"No month reached the elevated threshold "
                    f"(monthly ACI >= {ELEVATED_MONTH_ACI:.0f}) across "
                    f"{n} evaluated months.{tail}")
        season = self.elevated_season
        where = f", {season}" if season and season != "spread across the year" \
            else (" spread across the year" if season else "")
        counted = f"Elevated in {self.elevated_months} of {n} evaluated months{where}"

        if self.temporal_pattern == "PERSISTENT":
            if self.at_ceiling:
                # The claim "does not depend on a few months" IS the concentration
                # result, which is unavailable at the ceiling. The label rests on
                # the elevated count alone, and the sentence must say only that.
                return (
                    f"{counted}. This pattern rests on the count of elevated "
                    f"months alone: the score is at the cohort ceiling, so the "
                    f"worst-two-month effect cannot be measured, and its absence "
                    f"must not be read as stability. The monthly spread is "
                    f"{self.spread:.1f} points."
                )
            return (f"{counted}; removing the two worst months lowers the annual "
                    f"score by only {self.episodic_drop:.1f} points, so it does "
                    f"not depend on a few months.")

        if self.temporal_pattern == "EPISODIC":
            return (f"Elevated in only {self.elevated_months} of {n} evaluated "
                    f"months{where}; removing the two worst months lowers the "
                    f"annual score by {self.episodic_drop:.1f} points.")

        # INTERMITTENT, with at least one elevated month.
        base = (f"{counted}; pressure is present in part of the window rather "
                f"than throughout.")
        if self.at_ceiling:
            base += (" The score is at the cohort ceiling, so the "
                     "worst-two-month effect cannot be measured and must not be "
                     "read as stability.")
        return base


    # -- serialisation -------------------------------------------------
    @property
    def coverage_complete(self) -> bool:
        return (len(self.months) >= MONTHS_EXPECTED
                and self.months_suppressed == 0)

    @property
    def uncertainty(self) -> list[str]:
        """Explicit caveats. Empty only when coverage is complete and usable."""
        out: list[str] = []
        present = len(self.months)
        if present < MONTHS_EXPECTED:
            out.append(
                f"Only {present} of {MONTHS_EXPECTED} window months are reported "
                f"for this airport. The score is computed from the months that "
                f"exist and the rest are absent from the source, not treated as "
                f"zero — but it is not a full-year measurement and is not "
                f"directly comparable with an airport reporting all "
                f"{MONTHS_EXPECTED}."
            )
        if self.months_suppressed:
            out.append(
                f"{self.months_suppressed} reported month(s) carry fewer than "
                f"{MIN_FLIGHTS_PER_MONTH} flights and are left unevaluated; "
                f"monthly rates on samples that small are noise."
            )
        if self.at_ceiling:
            out.append(
                "This score sits at the cohort ceiling, where the normalisation "
                "clips the underlying value. Removing the worst months cannot "
                "lower it, so the worst-two-month effect is unmeasurable here "
                "and a value near zero is NOT evidence of temporal stability."
            )
        thin = [m for m in self.evaluable_months if m.flights < 500]
        if thin and len(thin) >= self.months_evaluated / 2:
            out.append(
                f"{len(thin)} of {self.months_evaluated} evaluated months carry "
                f"under 500 flights. Individual monthly values at this volume "
                f"move substantially on a handful of events."
            )
        return out

    def to_dict(self) -> dict:
        """Compact, structured payload for the API. Numbers, not prose."""
        drop = self.episodic_drop
        return {
            "months_available": len(self.months),
            "months_expected": MONTHS_EXPECTED,
            "months_evaluated": self.months_evaluated,
            "months_unevaluated": self.months_suppressed,
            "coverage_complete": self.coverage_complete,
            "temporal_pattern": self.temporal_pattern,
            "pattern_label": self.pattern_label,
            "description": self.description,
            "concentration": {
                "worst_two_month_drop": None if not self.concentration_reliable
                else round(drop, 1),
                "aci_excluding_worst_two": (
                    None if self.excluding_worst.get(2) is None
                    else round(self.excluding_worst[2], 1)),
                "reliable": self.concentration_reliable,
                "unreliable_reason": (
                    "score_at_cohort_ceiling" if self.at_ceiling
                    else (None if self.concentration_reliable
                          else "insufficient_evaluable_months")),
                "null_baseline_drop": (
                    None if self.null_drop is None else round(self.null_drop, 1)),
            },
            "monthly_spread": None if self.spread is None else round(self.spread, 1),
            "elevated_months": self.elevated_months,
            # True when nothing crossed the threshold, so a reader (or the model)
            # cannot mistake an INTERMITTENT pattern for intermittent congestion.
            "no_elevated_months": self.no_elevated_months,
            "elevated_share": (None if self.elevated_share is None
                               else round(self.elevated_share, 3)),
            "elevated_threshold": ELEVATED_MONTH_ACI,
            "elevated_season": self.elevated_season,
            "months": [
                {"month": m.month, "flights": m.flights,
                 "aci": None if m.aci is None else round(m.aci, 1),
                 "evaluated": m.aci is not None,
                 "elevated": m.aci is not None and m.aci >= ELEVATED_MONTH_ACI,
                 "reason": m.reason}
                for m in self.months
            ],
            "uncertainty": self.uncertainty,
            "notes": self.notes,
        }


def _monthly_aci(md: MonthlyDelay, cohort: Cohort) -> float | None:
    """Monthly ACI on the annual scale.

    Each raw monthly component is normalised against the ANNUAL cohort bounds
    and combined with the production ACI weights, renormalised over whichever
    components are present — the same arithmetic `_compose` applies to the
    annual figure. Seasonality is intentionally preserved: no per-month
    re-normalisation happens, so a month that is bad cohort-wide reads as bad.
    """
    present = 0.0
    total = 0.0
    for d in ACI_METRICS:
        raw = getattr(md, d.attr, None)
        if raw is None:
            continue
        n = cohort.stat(d.attr).normalize(raw)
        if n is None:
            continue
        if not d.higher_is_more_pressure:
            n = 100.0 - n
        present += d.weight
        total += d.weight * n
    return total / present if present else None


def _summed(m: AirportMetrics, keep: list[MonthlyDelay]) -> AirportMetrics:
    """A copy of `m` whose delay sums cover only `keep`."""
    agg = {f: 0 for f in _DELAY_FIELDS}
    for md in keep:
        for f in _DELAY_FIELDS:
            agg[f] += getattr(md, f)
    return replace(m, **agg, delay_months=len(keep))


def build_profile(
    m: AirportMetrics,
    monthly: list[MonthlyDelay],
    cohort: Cohort,
    *,
    worst_n: tuple[int, ...] = (1, 2, 3),
) -> PersistenceProfile:
    """Diagnostic profile for one airport. Reads nothing global; writes nothing."""
    annual = compute_aci(m, cohort)
    notes = [
        "Diagnostic only. It does not change ACI, its weights or any "
        "classification threshold, and it is not part of any production score.",
        "ACI measures observed delay OUTCOMES. Neither the annual score nor "
        "this diagnostic identifies a runway, gate or airspace cause, and "
        "neither establishes that a capacity constraint is binding.",
        f"A month below {MIN_FLIGHTS_PER_MONTH} reported flights is left "
        f"unevaluated rather than scored; monthly rates on small samples are "
        f"noise.",
    ]

    scored: list[MonthScore] = []
    for md in sorted(monthly, key=lambda x: x.month):
        if not md.evaluable:
            scored.append(MonthScore(md.month, md.flights, None,
                                     "insufficient_monthly_flights"))
            continue
        scored.append(MonthScore(md.month, md.flights, _monthly_aci(md, cohort)))

    # Counterfactual: drop the worst months by monthly ACI and rescore with the
    # production function, against unchanged cohort bounds.
    ranked = sorted(
        (md for md in monthly if md.evaluable),
        key=lambda md: (-(_monthly_aci(md, cohort) or -1.0), md.month),
    )
    unevaluable = [md for md in monthly if not md.evaluable]
    excluding: dict[int, float | None] = {}
    for n in worst_n:
        keep = ranked[n:] + unevaluable
        if not keep:
            excluding[n] = None
            continue
        alt = compute_aci(_summed(m, keep), cohort)
        excluding[n] = alt.score
        if alt.score is None and annual.score is not None:
            notes.append(
                f"Excluding the worst {n} month(s) drops this airport below the "
                f"{MIN_OTP_FLIGHTS_FOR_ACI:,}-flight ACI gate, so the "
                f"counterfactual is unavailable rather than zero."
            )

    # Null baseline: remove two months from the middle of the ranking instead.
    excluding_middle: float | None = None
    if len(ranked) >= 4:
        mid = len(ranked) // 2
        keep_mid = ranked[:mid - 1] + ranked[mid + 1:] + unevaluable
        excluding_middle = compute_aci(_summed(m, keep_mid), cohort).score

    return PersistenceProfile(
        iata=m.iata,
        annual_aci=annual.score,
        months=scored,
        excluding_worst=excluding,
        notes=notes,
        excluding_middle=excluding_middle,
    )


_MONTHLY_DELAY_SQL = """
SELECT iata, month, flights, cancelled, diverted, dep_del15, dep_del15_n,
       taxi_out_sum, taxi_out_n, nas_delay_sum, nas_delay_n,
       dep_delay_sum, dep_delay_n
FROM airport_delay_month
WHERE month BETWEEN ? AND ?
ORDER BY iata, month
"""


def load_monthly_delay(conn, window_start: str, window_end: str
                       ) -> dict[str, list[MonthlyDelay]]:
    """Read monthly OTP rows for the window. Read-only."""
    out: dict[str, list[MonthlyDelay]] = {}
    for r in conn.execute(_MONTHLY_DELAY_SQL, (window_start, window_end)):
        out.setdefault(r["iata"], []).append(MonthlyDelay(
            month=r["month"], flights=r["flights"], cancelled=r["cancelled"],
            diverted=r["diverted"], dep_del15=r["dep_del15"],
            dep_del15_n=r["dep_del15_n"], taxi_out_sum=r["taxi_out_sum"],
            taxi_out_n=r["taxi_out_n"], nas_delay_sum=r["nas_delay_sum"],
            nas_delay_n=r["nas_delay_n"], dep_delay_sum=r["dep_delay_sum"],
            dep_delay_n=r["dep_delay_n"],
        ))
    return out
