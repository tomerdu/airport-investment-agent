# Decision Record — TDPI Formulation (Phases 8.1, 8.1b, 8.1c)

**Status:** decided
**Date:** 2026-09-28
**Decision:** Retain TDPI v1. Reject v2 and v2c. Adopt one narrowly scoped
data-quality correction to v1 (the year-over-year window-comparability rule).
Defer further formula research until suitable data is available.

Full evidence: `phase-8.1-tdpi-v2-evaluation.md` (v2),
`phase-8.1b-tdpi-v2c-validation.md` (v2c),
`phase-8.1c-tdpi-decision-and-comparability.md` (the adopted correction).

---

## Context

TDPI v1 weights T1 load factor 0.20, T2 passenger growth 0.30, T3 gauge 0.15,
T4 throughput per runway 0.20, T5 enplanement growth 0.15. Two concerns motivated
revisiting it: T4 is an acknowledged proxy, and T3 (gauge) is a per-movement
measure standing in for a terminal-side quantity. Two replacement formulations
were built as opt-in experiments and measured against the declared objective —
a *demand-pressure proxy for the passenger-handling side* — on 399 US primary
commercial service airports over the window 2025-05..2026-04.

---

## Decision 1 — Reject TDPI v2

v2 replaced v1's level terms with shape-based ones: V1 growth 0.30, V2 sustained
growth 0.25, V3 peak concentration 0.20, V4 absolute growth 0.15, V5 change in
passengers per departure 0.10.

**Why it was rejected:**

1. **It inverted airport size.** Mean score by passenger volume ran 46.6 (<100k)
   → 47.4 (100k–1M) → 37.9 (1M–10M) → **27.0 (>10M)**. Large hubs scored *lowest*
   as a class. BOS fell to rank 385 of 399, ATL to 368, JFK to 369.
2. **V3 measured seasonality, not pressure.** Peak concentration is
   busiest-month ÷ mean-month, which rises for a summer-only airport that is
   empty for nine months. Seasonal airports (peak/mean ≥ 2.0) gained **+13.7**
   points relative to non-seasonal ones, against v1's −12.7. HYA rose to rank 28
   nationally on 33,092 annual passengers.
3. **V1 and V4 double-counted growth** — the same quantity in percentage and
   absolute form.
4. **Rank correlation with v1 was only +0.412** with a 4/20 top-20 overlap,
   i.e. it was a different index rather than a refinement of the same one.

## Decision 2 — Reject TDPI v2c

v2c was the corrected candidate: C1 growth 0.30, C2 sustained growth 0.25,
C3 passengers per departure (level) 0.30, C4 change in passengers per departure
0.15. It dropped V3 and V4, fixing both of v2's clearest faults — the seasonal
bonus fell to −10.4 and the size inversion disappeared.

It was still rejected, on evidence that contradicted the Phase 8.1
recommendation.

1. **C3 was aircraft gauge relabelled.** `pax/dep = (seats/dep) × load factor`,
   and US load factors cluster in 78–86%, so C3 ~ seats per departure measured
   **+0.979 Spearman** (C3 ~ load factor only +0.661). C3 is v1's T3 carrying
   0.30 instead of 0.15. The formulation intended to reduce reliance on gauge
   doubled it.
2. **Gauge is not terminal-capacity pressure**, and the difference is not
   subtle. Gauge is per-*movement*; terminal pressure is per-*time-period*. Two
   airports each handling 20,000 passengers — one on 100 flights of 200, one on
   200 flights of 100 — have identical terminal throughput and differ 2× on C3.
   Empirically the highest-C3 airports were ULCC bases plus HGR (44,735 annual
   passengers) and STC (26,016); BLV, at 196,442 passengers, ranked **5th of
   399**.
3. **The size "correction" was a single weight knob.** The >10M mean ran 30.4 →
   41.9 → 47.7 → 53.5 as C3's weight went 0.00 → 0.20 → 0.30 → 0.40. Choosing
   0.30 because it placed large airports near the cohort mean would have been
   fitting the formula to a desired ranking, so 0.30 was never validated — only
   shown to be arbitrary.
4. **The growth redundancy survived.** C1 ~ C2 measured +0.876 Spearman while
   jointly carrying 55% of the weight. v2c did not fix the problem it was meant
   to narrow.
5. **It was not robust where it mattered.** Raising C3 from 0.30 to 0.40 — well
   inside the range a reasonable analyst might pick — retained only **10 of 20**
   top-20 airports. The cohort ordering was stable (rho ≥ 0.868) but the
   shortlist, which is the actual product, was not.
6. **Adoption would have distorted classification.** Under the inherited 60/40
   thresholds, v2c more than doubled TERMINAL_LED (7 → 15) and more than tripled
   the count of high-TDPI airports whose airside congestion is unmeasured
   (9 → 30). The second figure conflicts with the system's core principle that
   absence of a congestion measurement is not evidence of absence of congestion.

## Decision 3 — No ground truth, so no formulation is validated

No dataset available to this system records which airports actually required
terminal investment. Every comparison across v1, v2 and v2c is therefore
**internal consistency and conceptual validity, not predictive accuracy**. This
cuts both ways and is stated plainly:

- v2 and v2c were rejected for identifiable conceptual defects, not for
  performing worse against outcomes.
- **v1 is retained by default, not because it was shown to be correct.** Its own
  weights are equally unvalidated, and T4 remains an acknowledged proxy.

Any future claim that one formulation is *better* requires outcome data this
project does not have.

## Decision 4 — Adopt the YoY window-comparability rule

The one defect found that was unambiguously a bug rather than a design
trade-off, and it is independent of any scoring formula.

v1's T2 divided window passengers by prior-window passengers with no check that
the two windows covered the same months. GUF — whose prior year contains two
months totalling **seven passengers** — was computed at **+839,571%** growth and
ranked **3rd of 399**.

Adopted rule: compute T2 only when every current-window month has a prior-year
counterpart, summing over matched months only; otherwise drop the component.
Parameter-free. Two simpler formulations were measured and rejected: an absolute
month floor (discards the valid seasonal comparison at WYS) and equal month
counts (lets GST and KLW through, whose 11-vs-11 windows compare December against
April). Implemented in Phase 8.1c; evidence and measured effects in
`phase-8.1c-tdpi-decision-and-comparability.md`.

## Decision 5 — Keep the experiments, opt-in and out of the production path

`compute_tdpi_v2()` and `compute_tdpi_v2c()` remain in `scoring.py`, reachable
only by explicit call. `score_airport()` — the sole production entry point —
calls `compute_tdpi()` alone, and a test asserts that no v2 variant appears in
it. They are retained because the measurements above are reproducible from them;
they are not wired to any API route, the agent or the frontend.

---

## What was deliberately not done

- **No weight was tuned to lift any particular airport.** BOS and ATL rank
  mid-table under both candidates because both had slightly negative
  year-over-year passenger growth in this window. Under a demand-*pressure*
  index that is arguably correct behaviour, and it was left alone rather than
  engineered away.
- **The 60/40 divergence thresholds were not reused unexamined for the
  candidates, nor changed for production.** Threshold distributions were measured
  per formulation; production classification is untouched.
- **ACI and UDEI were not modified.** ACI shares no component with T2 and its
  scores are bit-identical before and after the Phase 8.1c change.

## Deferred

Further TDPI formula research, until data exists for:

1. A **per-time-period level anchor** — passengers per gate, per processing
   position, or peak-hour counts. Every level term in T-100 is per-movement and
   therefore describes aircraft rather than buildings.
2. **A single growth component** replacing the correlated magnitude +
   consistency pair.
3. **Outcome data** against which any formulation could actually be validated.
