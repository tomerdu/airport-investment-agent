# Phase 8.2 — ACI Temporal Persistence Evaluation

> **Outcome (Phase 8.2b).** Reviewed and approved. ACI is retained unchanged; the
> concentration and spread measures were integrated into the airport profile and
> comparison as a supplementary `temporal` block, with elevated share kept as
> secondary context. Implementation, before/after behaviour and the naming and
> ceiling decisions are recorded in
> `phase-8.2b-aci-temporal-integration.md`. The analysis below is unchanged from
> the evaluation, including §6's finding that elevated share is largely redundant
> and §7's refuted hypothesis.

**Status:** complete, uncommitted, awaiting review
**Window:** 2025-05 .. 2026-04 (unchanged)
**Cohort:** 399 airports; **237** clear the 1,000-flight ACI gate
**Tests:** 458 passed, 0 failed, 0 skipped — full offline suite
**External calls:** none. No Anthropic API, no downloads.
**Production changes:** none. ACI cohort score total is **bit-identical**
(10931.2699 before and after), and TDPI, UDEI, classification, API contracts,
the agent and the frontend are untouched.

Reproduce with:

```
python evaluate_aci_persistence.py
python evaluate_aci_persistence.py --section data|seasonality|cohort|cases|volume|added|threshold
```

---

## Recommendation

**Retain ACI unchanged. Add a supplementary persistence diagnostic — but only
two of its three measures, because the third is redundant.**

| measure | Spearman vs annual ACI | verdict |
|---|---:|---|
| Monthly **concentration** (points lost when the 2 worst months are removed) | **+0.493** | **adopt** — genuinely additional |
| Monthly **spread** (max − min monthly ACI) | **+0.531** | **adopt** — genuinely additional |
| **Elevated-month share** | **+0.926** | **do not headline** — largely restates the ACI ranking |

A diagnostic that correlates +0.926 with the score it annotates is telling the
reader what they already know. The concentration and spread measures are only
about half-correlated with ACI, and the airport pairs in §6 show they separate
airports the ranking treats as identical.

Also recorded, **not** as a scoring change: exactly **2 of 237** ACI-scored
airports (ACK, MVY) are scored and classified on **half a year** of OTP data.
That is a transparency gap worth a decision, not a bug. §7 covers it, including
a hypothesis about it that the data refuted.

No new weights are proposed. Nothing in the data demonstrates a defect in the
current ACI components or weights.

---

## 1. Methodology and available data

### What the warehouse holds

`airport_delay_month` — 4,118 rows, 2,832 of them for ACI-eligible airports —
carries a **numerator and a denominator for every ACI component** at monthly
granularity:

| component | weight | monthly numerator / denominator | zero-denominator months |
|---|---:|---|---:|
| A1 Average taxi-out | 0.30 | `taxi_out_sum` / `taxi_out_n` | **0** |
| A2 NAS delay per flight | 0.30 | `nas_delay_sum` / `nas_delay_n` | **0** |
| A3 Departures delayed >15 min | 0.25 | `dep_del15` / `dep_del15_n` | **0** |
| A4 Cancellation rate | 0.15 | `cancelled` / `flights` | **0** |

**All four components are evaluable monthly.** None had to be dropped or
approximated — the monthly table is the same source production aggregates, so
the diagnostic and the score are computed from identical inputs.

Coverage: 235 of 237 eligible airports have all 12 months; 2 have 6 (§7).

### Three design decisions, and why

**1. The annual ACI is a ratio of sums, not a mean of monthly ratios.** Every
component is `SUM(numerator) / SUM(denominator)` over the window, so busy months
already carry more weight. Averaging monthly rates would describe a different
quantity from the published one. The diagnostic therefore never averages monthly
rates; where it needs a counterfactual it **re-sums the retained months and calls
the production `compute_aci()`**. A test asserts that re-summing *all* months
reproduces the published score exactly, which is what makes "ACI excluding the
worst 2 months" comparable to the real figure.

**2. Monthly rates on small samples are noise.** Production refuses ACI below
1,000 window flights for this reason. `MIN_FLIGHTS_PER_MONTH` is derived as
`MIN_OTP_FLIGHTS_FOR_ACI // 12` = **83** rather than chosen independently, so the
diagnostic inherits the index's own tolerance instead of inventing a second one.
44 of 2,832 airport-months fall below it and are reported **unevaluated, never
scored as zero**.

**3. Seasonality is real and is not a defect.** Monthly values are normalised
against the **annual** cohort bounds, with no per-month re-normalisation. A month
that is bad cohort-wide therefore reads as bad for everyone, instead of being
normalised away — which is the behaviour needed to answer the question asked.

### The measures

- **monthly ACI** — the four monthly rates, normalised against annual cohort
  bounds, combined with the production weights and renormalised over present
  components. Same arithmetic as `_compose`.
- **elevated months** — monthly ACI ≥ 60.
- **concentration** — points the annual ACI loses when its 2 worst months are
  removed and the score is recomputed by the production function.
- **null baseline** — the same thing with 2 **middle**-ranked months removed.
- **spread** — max − min monthly ACI.

### The null baseline, and a measure I nearly got wrong

A drop when the two worst months are removed could be pure arithmetic: take the
top 2 of 12 observations away and any average falls. The control is to remove two
*middle*-ranked months instead — same reduction in sample size, no extremes:

| removal | median change | mean |
|---|---:|---:|
| 2 **worst** months | **+6.13** | +6.54 |
| 2 **middle** months | **−0.23** | −0.18 |
| 2 **best** months | −5.26 | −5.37 |

Removing two typical months moves the score by a fifth of a point. The worst-2
drop therefore reflects those months' behaviour, not the smaller sample — median
excess **+6.07**, and 184 of 218 airports exceed the null by more than 3 points.
**The measure is validated rather than assumed.**

This also corrected my first threshold. I had set "concentrated" at a 5-point
drop, which sits *below* the cohort median of 6.1 and so labelled the typical
airport episodic — describing the norm as an exception. It is now **8.0, the
measured cohort p75**, and §8 reports how the label counts move at other cuts.

---

## 2. Cohort-wide seasonality

Flight-weighted cohort averages, eligible airports only:

| month | flights | taxi-out | NAS/flight | del15 | cancel | mean monthly ACI |
|---|---:|---:|---:|---:|---:|---:|
| 2025-05 | 600,319 | 18.52 | 4.42 | 22.5% | 1.05% | 42.6 |
| 2025-06 | 605,785 | 18.67 | 4.49 | 27.5% | 1.59% | 52.5 |
| 2025-07 | 625,219 | 18.76 | 4.85 | 28.6% | 2.45% | 54.0 |
| 2025-08 | 596,253 | 18.27 | 3.16 | 22.4% | 1.07% | 38.3 |
| 2025-09 | 557,070 | 18.17 | 2.52 | 16.5% | 0.51% | **21.9** |
| 2025-10 | 600,350 | 18.70 | 2.99 | 19.6% | 0.53% | 27.5 |
| 2025-11 | 565,308 | 19.10 | 4.02 | 19.9% | 2.49% | 49.2 |
| 2025-12 | 576,813 | 19.61 | 3.83 | 26.9% | 1.52% | **54.6** |
| 2026-01 | 538,644 | 19.41 | 2.83 | 20.6% | **4.71%** | 48.0 |
| 2026-02 | 509,928 | 18.92 | 2.53 | 20.1% | 2.25% | 38.9 |
| 2026-03 | 606,106 | 18.62 | 3.97 | 25.1% | 2.89% | 52.6 |
| 2026-04 | 592,014 | 18.57 | 2.82 | 20.1% | 0.88% | 34.2 |

Range across the year: taxi-out **1.1×**, NAS per flight **1.9×**, del15 **1.7×**,
cancellations **9.2×**.

Two things follow. First, the year has **two** congestion peaks — summer
(Jun–Jul, NAS-driven) and winter (Dec–Mar, cancellation-driven) — separated by a
September trough at less than half the peak. Second, **A1's stability and A4's
volatility are consistent with their production weights** (0.30 and 0.15). The
component the index leans on hardest is the one that varies least across the
year, and the lumpiest component is already the most down-weighted. That is an
independent check on the existing weighting, and it passes.

---

## 3. Cohort-wide persistence

| label | n |
|---|---:|
| PERSISTENT | 27 |
| MIXED | 159 |
| EPISODIC | 37 |
| INSUFFICIENT_DATA | 14 |

Concentration (points lost removing the 2 worst months), n = 223:

| min | p25 | median | p75 | max |
|---:|---:|---:|---:|---:|
| +0.0 | +4.1 | **+6.2** | **+8.1** | +17.2 |

58 airports exceed the calibrated 8-point cut.

**By annual ACI band:**

| band | n | mean elevated share | mean worst-2 drop | mean spread |
|---|---:|---:|---:|---:|
| ACI ≥ 60 | 52 | 62.1% | +8.4 | 64.2 |
| 40 ≤ ACI < 60 | 94 | 26.8% | +7.4 | 61.6 |
| ACI < 40 | 91 | 3.7% | +4.6 | 44.5 |

So high-ACI airports do tend to have more elevated months — but they also have
*larger* concentration, not smaller. A high annual ACI is **not** by itself
evidence of sustained pressure.

**The highest-concentration airports with ACI ≥ 60:**

| apt | annual | excl. worst 2 | drop | elevated |
|---|---:|---:|---:|---:|
| DLH | 73.3 | 57.0 | **+16.3** | 6/12 |
| MBS | 67.6 | 52.9 | +14.7 | 7/12 |
| TVC | 84.9 | 70.2 | +14.7 | 8/12 |
| SYR | 64.5 | 50.1 | +14.4 | 5/12 |
| MQT | 65.9 | 51.8 | +14.1 | 6/12 |
| SBN | 78.6 | 64.7 | +13.9 | 7/12 |
| GRR | 61.6 | 47.7 | +13.9 | 6/12 |
| CAK | 73.6 | 60.4 | +13.2 | 6/12 |
| RST | 84.8 | 72.3 | +12.4 | 8/12 |
| ROC | 65.2 | 53.5 | +11.7 | 6/12 |

Duluth, Saginaw, Traverse City, Syracuse, Marquette, South Bend, Grand Rapids,
Akron, Rochester MN, Rochester NY. **Every one is a northern snow-belt airport.**
Their high annual ACI is substantially a winter-weather signal, and the ranking
alone does not say so. This is the clearest demonstration that the diagnostic
adds interpretive information.

It is worth being explicit about what this does *not* establish: a winter-driven
ACI is still a real operational outcome for passengers and airlines. It is simply
not evidence of a persistent structural constraint, and the current output gives
a reader no way to tell the two apart.

---

## 4. Airport case studies

Monthly ACI marked `*` where ≥ 60.

### BOS — ACI 79.5, **PERSISTENT**, 142,624 flights

| month | flights | mACI | taxi | NAS | del15 | cancel |
|---|---:|---:|---:|---:|---:|---:|
| 2025-05 | 12,547 | 71.5 * | 20.5 | 5.2 | 24.2% | 0.81% |
| 2025-06 | 12,372 | 79.3 * | 21.1 | 5.5 | 25.1% | 1.29% |
| 2025-07 | 12,912 | 100.0 * | 22.6 | 7.5 | 27.8% | 4.07% |
| 2025-08 | 12,461 | 47.4 | 20.2 | 3.0 | 19.9% | 1.00% |
| 2025-09 | 11,597 | 60.1 * | 20.0 | 3.0 | 26.1% | 1.35% |
| 2025-10 | 12,647 | 65.3 * | 20.7 | 3.1 | 28.3% | 1.51% |
| 2025-11 | 11,385 | 71.9 * | 21.1 | 4.9 | 20.2% | 2.76% |
| 2025-12 | 11,248 | 89.6 * | 23.0 | 6.6 | 27.1% | 1.69% |
| 2026-01 | 10,298 | 90.2 * | 23.9 | 6.2 | 21.8% | 7.50% |
| 2026-02 | 10,232 | 89.1 * | 23.7 | 5.0 | 23.8% | 8.15% |
| 2026-03 | 12,299 | 82.4 * | 20.9 | 5.5 | 23.8% | 2.69% |
| 2026-04 | 12,626 | 47.9 | 19.8 | 3.4 | 20.2% | 0.31% |

**10/12 elevated, worst-2 drop only +6.8.** Taxi-out never falls below 19.8
minutes in any month. This is genuine year-round pressure, and the diagnostic
confirms rather than qualifies the score.

### BGR — ACI 78.9, **PERSISTENT**, 3,674 flights — but read the volume column

| month | flights | mACI | taxi | NAS | del15 | cancel |
|---|---:|---:|---:|---:|---:|---:|
| 2025-05 | 184 | 68.9 * | 18.0 | 6.3 | 17.6% | 4.35% |
| 2025-06 | 345 | 81.7 * | 18.5 | 10.7 | 23.5% | 7.54% |
| 2025-07 | 549 | 86.6 * | 19.2 | 13.1 | 25.0% | 6.56% |
| 2025-08 | 562 | 68.3 * | 17.7 | 8.9 | 23.0% | 1.78% |
| 2025-09 | 518 | 47.1 | 16.0 | 5.7 | 16.6% | 0.97% |
| 2025-10 | 471 | 36.9 | 16.4 | 4.1 | 17.5% | 0.42% |
| 2025-11 | 165 | 82.4 * | 20.7 | 6.5 | 27.0% | 1.21% |
| 2025-12 | 146 | 88.9 * | 22.8 | 4.5 | 25.4% | 8.22% |
| 2026-01 | 147 | 100.0 * | 27.0 | 6.2 | 30.2% | **14.29%** |
| 2026-02 | 162 | 79.2 * | 24.4 | 3.9 | 24.8% | 3.09% |
| 2026-03 | 182 | 100.0 * | 24.1 | 8.3 | 31.2% | 4.95% |
| 2026-04 | 243 | 38.0 | 17.5 | 3.5 | 17.8% | 0.82% |

9/12 elevated, so the label reads PERSISTENT — but **January's 14.29%
cancellation rate is 21 cancellations out of 147 flights.** BGR clears the
annual gate (3,674 flights) while individual months run 146–562. The label is
defensible; the confidence a reader should attach to any single month is not.
This is the case that argues for showing monthly flight counts beside any
monthly figure, never the monthly figure alone.

### LAX — ACI 37.9, 0/12 elevated, 189,899 flights

| month | flights | mACI | taxi | NAS | del15 | cancel |
|---|---:|---:|---:|---:|---:|---:|
| 2025-05 | 16,546 | 37.2 | 17.5 | 3.7 | 17.1% | 0.35% |
| 2025-07 | 17,104 | 50.4 | 17.5 | 3.7 | 22.8% | 1.20% |
| 2025-09 | 15,262 | 22.4 | 17.6 | 2.3 | 14.5% | 0.28% |
| 2025-11 | 15,422 | 50.8 | 19.1 | 3.6 | 19.4% | 1.78% |
| 2026-01 | 14,815 | 39.8 | 17.5 | 2.5 | 19.3% | 2.17% |
| 2026-04 | 15,732 | 33.1 | 17.9 | 2.5 | 18.9% | 0.44% |

*(abridged — 12 months evaluated)*

**The volume-versus-intensity case, exactly as task 5 requires.** LAX has the
largest flight count in the cohort and one of the *stable* profiles: taxi-out
17.4–19.1 all year, **zero** elevated months, worst-2 drop +2.6. High traffic
volume is not high congestion intensity per flight, and neither the annual ACI
nor the diagnostic confuses the two.

### SNA — ACI 36.7, 0/12 elevated, 45,464 flights

Taxi-out 15.5–16.6 across the whole year; worst-2 drop +3.9; spread 38.2. Like
LAX, unremarkable per flight. Note for Q2: SNA's ACI (36.7) sits just below LAX's
(37.9), and the diagnostic agrees they are behaviourally similar — both stable,
neither with an elevated month.

### ACK — ACI 83.4, **INSUFFICIENT_DATA**, 1,582 flights

| month | flights | mACI | taxi | NAS | del15 | cancel |
|---|---:|---:|---:|---:|---:|---:|
| 2025-05 | 57 | — | *unevaluated* | | | |
| 2025-06 | 300 | 87.0 * | 18.8 | 9.8 | 25.9% | 7.33% |
| 2025-07 | 496 | 95.7 * | 21.0 | 13.3 | 27.2% | 8.06% |
| 2025-08 | 481 | 46.1 | 18.9 | 4.6 | 14.9% | 1.25% |
| 2025-09 | 177 | 76.6 * | 19.7 | 13.4 | 25.7% | 1.13% |
| 2025-10 | 71 | — | *unevaluated* | | | |

**Production reports a confident ACI of 83.4 and classifies ACK
`AIRSIDE_LED`. The airport has six months of OTP data in the window, four of
them evaluable.** There is no November–April data at all. See §7.

### Informative outliers

**GRB — largest concentration in the cohort.** ACI 58.5 → **41.3** when its two
worst months are removed (drop **+17.2**), spread 86.7. December 98.4 and March
92.1 against September 12.8 and October 11.7. A mid-table annual score built from
two very different halves of a year.

**JNU — the clearest single-month case.** ACI 19.1, spread **98.4**. Eleven
months sit between 1.6 and 14.7; December 2025 alone reads 100.0. Removing two
months halves the score (19.1 → 9.3). A *low* annual ACI concealing one extreme
month — the diagnostic is informative at the bottom of the ranking too, not only
the top.

**ASE — where the concentration measure fails.** ACI 100.0, 7/11 elevated,
spread 59.6, and worst-2 drop **+0.0**. That zero is a **ceiling artefact**: the
raw value sits above the cohort P95, so winsorization clips it to 100 and
removing bad months cannot move it until it falls below P95. **A drop of ~0 at
the top of the scale must not be read as stability.** One airport is affected
here, and the spread and elevated-share columns still describe it correctly.

---

## 5. Sensitivity to low volume and seasonality

**By annual flight volume:**

| flights | n | mean spread | mean worst-2 drop | % EPISODIC |
|---|---:|---:|---:|---:|
| < 5,000 | 94 | 60.3 | +7.5 | **24%** |
| 5k–25k | 86 | 55.8 | +6.4 | 12% |
| 25k–100k | 35 | 47.2 | +5.2 | 6% |
| ≥ 100,000 | 22 | 48.2 | +5.8 | 9% |

Small airports do show more volatility and are labelled EPISODIC four times as
often as large ones. **Part of this is real and part is sampling.** A 300-flight
month genuinely moves more on a handful of events than a 16,000-flight month, and
nothing in this data separates the two cleanly. Consequences for how the
diagnostic may be presented:

- the monthly flight count must always appear beside any monthly value;
- the 83-flight floor removes the worst of it (44 months) but does not make a
  300-flight month reliable;
- a volatility figure for a small airport should be read as "this airport's
  measured months varied", not "this airport is operationally unstable".

**Airports with unevaluated months:** 22. The most affected are LCK (6 of 12
below the floor), GFK (5), PAE (4), GUC (3), then ACK, AEX, BLV, LCH (2 each).
Those reduced to fewer than 6 evaluable months are reported
INSUFFICIENT_DATA rather than given a label.

**Are elevated months concentrated in particular calendar months?**

```
2025-05    49/231   21.2% ##########
2025-06    96/236   40.7% ####################
2025-07   101/236   42.8% #####################
2025-08    38/235   16.2% ########
2025-09     5/234    2.1% #
2025-10    10/232    4.3% ##
2025-11    69/232   29.7% ##############
2025-12    97/233   41.6% ####################
2026-01    72/228   31.6% ###############
2026-02    47/225   20.9% ##########
2026-03    98/235   41.7% ####################
2026-04    29/231   12.6% ######
```

Emphatically yes — 42.8% of airports are elevated in July against **2.1%** in
September. Any persistence figure is therefore partly a statement about which
months an airport operates in, which is precisely why §7 matters.

---

## 6. Does the diagnostic add information?

Spearman against the annual ACI, n = 223:

| measure | ρ | reading |
|---|---:|---|
| elevated share | **+0.926** | almost a restatement of the ranking |
| worst-2 drop | **+0.493** | substantially independent |
| monthly spread | **+0.531** | substantially independent |

**The elevated-month share largely reproduces the ACI ranking and should not be
presented as a headline finding.** In hindsight this is unsurprising: a high
annual score is a flight-weighted average of monthly rates, so it is high
precisely when many months were high. I built it expecting it to be the primary
measure; the measurement says otherwise.

The concentration and spread measures do add information. Airports within 2.0
ACI points of each other whose concentration differs by more than 5 points:

| ACI | airport A | airport B |
|---:|---|---|
| 17.6 | GPT drop +4.1, spread 46.5 | **JNU drop +9.8, spread 98.4** |
| 19.7 | **FAI drop +8.2, spread 76.8** | GEG drop +1.5, spread 16.4 |
| 21.3 | GEG drop +1.5, spread 16.4 | **HRL drop +7.1, spread 55.6** |
| 22.2 | **JAN drop +7.5, spread 58.2** | BOI drop +2.3, spread 27.6 |
| 23.7 | SLC drop +2.0, spread 20.2 | **CLL drop +7.6, spread 52.9** |
| 24.5 | PDX drop +2.9, spread 29.6 | **BFL drop +8.1, spread 60.9** |

SLC and CLL are indistinguishable on ACI (23.7 vs 25.5) and behave completely
differently: SLC varies by 20 points across the year, CLL by 53. GEG is the most
temporally stable airport in the cohort (spread 16.4). **The ranking cannot
express any of this.**

---

## 7. A transparency gap, and a hypothesis the data refuted

**Exactly 2 of 237 ACI-scored airports have partial-year OTP coverage:**

| apt | months present | flights | per month | TDPI | ACI | class | diagnostic |
|---|---|---:|---:|---:|---:|---|---|
| ACK | 2025-05 .. 2025-10 | 1,582 | 263 | 22.3 | **83.4** | **AIRSIDE_LED** | INSUFFICIENT_DATA (4 evaluable) |
| MVY | 2025-05 .. 2025-10 | 1,070 | 178 | 27.8 | **77.4** | **AIRSIDE_LED** | INSUFFICIENT_DATA (4 evaluable) |

Both are summer-only seasonal operations with no November–April data whatsoever.
Both clear the 1,000-flight gate. Both receive a confident two-significant-figure
ACI and a definite divergence class, presented identically to a score built on a
full twelve months.

**I expected this to be a seasonal bias and it is not.** The hypothesis was that
May–October is the cohort's harsh season, so these airports would be scored on
their worst months against everyone else's annual average. Measured against the
cohort's monthly backdrop:

| | mean cohort monthly ACI |
|---|---:|
| over ACK/MVY's six months (May–Oct) | **39.5** |
| over the full year | **42.9** |

Their covered months are **3.4 points milder** than the annual average, because
May–October contains September (21.9) and October (27.5), the two calmest months
in the cohort. The hypothesis is refuted: their high ACI is not an artefact of
which season was measured. It reflects genuinely high per-flight delay intensity
in the months that exist.

So the issue is narrower than I first thought, and it is **not** the same defect
as the Phase 8.1c TDPI one. There the ratio compared mismatched periods and was
arithmetically wrong. Here the arithmetic is sound; what is missing is any
indication that the sample is half a year. The gap is **presentational**:

- it cannot be fixed from the committed data — the months are absent from the
  source, not mis-aggregated;
- the options are to suppress ACI on partial coverage, or to report coverage
  alongside the score. **Both are product decisions, not bug fixes**, and I am
  not making either in this phase.

My recommendation is the second, as part of the diagnostic: show months covered.
Suppressing ACK and MVY would remove two of the four classified New England
airports from the Q1 answer, which is a bigger change to the deliverable than the
problem warrants.

---

## 8. Threshold sensitivity

Elevated cut (drop cut held at 8.0):

| cut | PERSISTENT | MIXED | EPISODIC | mean elevated share |
|---:|---:|---:|---:|---:|
| 50 | 53 | 147 | 23 | 37.4% |
| 55 | 39 | 154 | 30 | 31.1% |
| **60** | **27** | **159** | **37** | **25.7%** |
| 65 | 22 | 160 | 41 | 20.6% |
| 70 | 18 | 156 | 49 | 16.1% |

Drop cut (elevated held at 60):

| drop cut | PERSISTENT | MIXED | EPISODIC |
|---:|---:|---:|---:|
| 5 | 8 | 108 | 107 |
| 6 | 12 | 130 | 81 |
| **8** (cohort p75) | **27** | **159** | **37** |
| 10 | 32 | 174 | 17 |
| 12 | 40 | 175 | 8 |

Both cuts move the counts substantially. **The three-way label is therefore a
convenience, not a finding**, and the report leads with the continuous measures.
If the diagnostic is exposed, the underlying numbers should be shown with it and
the label should never appear alone.

---

## 9. Could this be exposed in the profile and comparison tools?

Technically yes, with no change to ACI, its weights, the 60/40 thresholds or
`classify()`. What I would expose, per §6:

| field | why |
|---|---|
| `months_evaluated` / `months_present` | the §7 transparency gap; the single most valuable field |
| monthly series (month, flights, monthly ACI) | lets a reader see the shape; flights must sit beside each value |
| concentration (worst-2 drop) | ρ +0.493 — genuinely additional |
| spread | ρ +0.531 — genuinely additional |
| elevated share | secondary at most; ρ +0.926 |

Constraints that should travel with it:

1. **Never as a component of ACI, and never an input to `classify()`.** A test
   asserts `classify()` reads only `DIVERGENCE_HI`/`DIVERGENCE_LO`.
2. **The label must not appear without its numbers** (§8).
3. **Monthly flight counts must always accompany monthly values** (§5, BGR).
4. **The ceiling caveat must be stated** wherever the drop is shown (§4, ASE).
5. **No capacity language.** Every profile carries notes stating that ACI
   measures observed delay outcomes and that neither the score nor the diagnostic
   identifies a runway, gate or airspace cause, or establishes a binding
   constraint. A test asserts those notes are present.

This phase does not implement any exposure: API contracts, agent tools and the
frontend are untouched, as instructed.

---

## 10. Impact on the four exam scenarios

**No production output changes.** The cohort ACI total is bit-identical
(10931.2699) and no divergence class moves. What follows is what the diagnostic
would *add* if exposed.

| apt | scenario | ACI | label | elevated | drop | spread |
|---|---|---:|---|---:|---:|---:|
| SFO | Q4 unmet demand | 54.3 | MIXED | 4/12 | +4.5 | 37.6 |
| LAX | Q2 congestion | 37.9 | MIXED | 0/12 | +2.6 | 28.4 |
| SNA | Q2 congestion | 36.7 | MIXED | 0/12 | +3.9 | 38.2 |
| ANC | Q3 long-haul | 17.8 | MIXED | 1/12 | +5.7 | 78.1 |
| BOS | Q1 New England | 79.5 | **PERSISTENT** | 10/12 | +6.8 | 52.6 |

- **Q1 (New England) — materially improved.** The regional ACI column currently
  reads as a flat ranking. The diagnostic separates three groups: **BOS 79.5,
  BGR 78.9, BTV 87.8 are PERSISTENT**; **MHT (+10.5), PWM (+9.8), PVD (+8.1) are
  EPISODIC** despite mid-range scores; and **ACK 83.4, MVY 77.4 — the region's
  only two `AIRSIDE_LED` airports — have four evaluable months each.** That last
  point is the most consequential fact this phase found for the deliverable.
- **Q2 (LAX vs SNA) — mildly improved.** Both are stable with zero elevated
  months, which supports the existing answer that SNA is close to LAX per flight.
  It adds that neither has an episodic component, so the comparison is not an
  artefact of one bad month.
- **Q3 (Anchorage long-haul) — unaffected.** Q3 concerns departures and aircraft
  configuration, not delay. ANC's ACI is incidental to the question.
- **Q4 (SFO unmet demand) — unaffected in substance.** SFO's 4/12 elevated months
  and +4.5 drop are unremarkable and change nothing about the unmet-demand
  answer, which rests on UDEI evidence indicators rather than ACI.

---

## 11. Limitations and remaining uncertainty

1. **One window, one cohort.** Twelve months for 237 airports. Nothing here shows
   whether an airport's persistence classification is stable year to year, which
   is the property that would matter most for an investment decision.
2. **Twelve observations is a short series.** "Worst two of twelve" is a coarse
   instrument. With 24 or 36 months, concentration could be estimated properly
   rather than by a leave-two-out counterfactual.
3. **The concentration measure saturates at the ceiling** (§4, ASE) and is
   compressed near the floor, because the underlying normalisation is winsorized
   min-max against cohort P5–P95.
4. **Small-airport volatility is part real, part sampling**, and this data does
   not separate them (§5).
5. **Weather is not separated from congestion.** A January cancellation spike and
   a July NAS-delay spike both raise monthly ACI, and OTP does not attribute
   cause. The snow-belt pattern in §3 is an inference from geography, not a
   measurement.
6. **No ground truth.** As with TDPI, nothing records which airports actually had
   a binding airside constraint, so "PERSISTENT" is not validated against any
   outcome.
7. **The labels are threshold-dependent** (§8) and the drop threshold is
   calibrated to this cohort's p75, so it would need recalibrating on a different
   cohort or window.
8. **ACI still does not measure capacity.** Neither the score nor this diagnostic
   identifies a runway, gate or airspace cause, and a high value on either is not
   evidence that a capacity constraint is binding.

---

## 12. Files changed

| file | change |
|---|---|
| `backend/app/analytics/persistence.py` | **new** — the diagnostic. Opt-in, imported by nothing in production |
| `backend/evaluate_aci_persistence.py` | **new** — 7-section offline evidence harness |
| `backend/tests/test_aci_persistence.py` | **new** — 23 deterministic tests |
| `docs/phase-8.2-aci-persistence-evaluation.md` | **new** — this report |

**No existing file was modified.** ACI, TDPI, UDEI, `classify()`, the divergence
thresholds, the analysis window, `score_airport()`, every API contract, the agent
tools and the frontend are all untouched.

### Test results

```
458 passed in 15.87s
```

Full offline suite (435 pre-existing + 23 new), run with the project's mocked
LLM clients. No live API calls, 0 failures, 0 skips.

Regression tests assert: ACI weights 0.30/0.30/0.25/0.15 and the 1,000-flight
gate unchanged; TDPI weights and the 60/40 thresholds unchanged; `persistence` is
not imported by `main.py`, `engine.py`, `tools.py` or `orchestrator.py`;
`classify()` does not reference the diagnostic's threshold; and re-summing all
months reproduces the published ACI exactly.

---

## 13. Decision requested

1. **Retain ACI unchanged** — no component, weight or threshold change is
   warranted. (Recommended; the data shows no defect in the current
   implementation.)
2. **Adopt the persistence diagnostic as supplementary evidence**, exposing
   coverage, the monthly series, concentration and spread — but not the elevated
   share as a headline, and never the label alone. (Recommended.)
3. **Decide separately how to present partial-coverage ACI** (ACK, MVY). My
   recommendation is to show months covered rather than to suppress the score.
4. **Defer** any exposure work — API, agent tools and frontend — to a later
   phase, per this phase's constraints.

Nothing is committed. Awaiting review.
