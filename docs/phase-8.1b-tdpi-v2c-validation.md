# Phase 8.1b — TDPI v2c Validation Before Adoption

**Status:** complete, uncommitted, awaiting review
**Window:** 2025-05 .. 2026-04 (unchanged)
**Cohort:** 399 US primary commercial service airports
**Production changes:** none. v1 weights, ACI weights, the 60/40 divergence
thresholds, the API contracts, the frontend and the agent are all untouched.
**External calls:** none. No Anthropic API, no downloads, no paid services.

---

## Recommendation

**Retain v1. Do not adopt v2c. Investigate a further alternative.**

Separately and independently of any scoring-formula change: **adopt the
prior-window coverage guard** described in §6. It is a data-quality correction,
not a methodology change, and it is the one defect found in this phase that is
unambiguously a bug rather than a design trade-off.

This reverses the recommendation I gave at the end of Phase 8.1, where I
proposed v2c as the corrected candidate. The reversal is driven by §7: the
component I introduced to fix v2's size inversion (C3, passengers per
departure) turns out to be **aircraft gauge under a different name**, and gauge
is the component whose weakness motivated revisiting v1 in the first place. v2c
corrects the symptom — big airports scoring low — by reintroducing the cause.

---

## 1. What v2c is

Opt-in experimental path, `compute_tdpi_v2c()`. Same machinery as v1 and v2:
winsorized P5–P95 min-max normalisation to the cohort, weight renormalisation
over present components, the 0.60 coverage floor, and **no imputation of
missing components**. Differences from v1 are therefore attributable to the
component set, not to the scoring machinery.

| id | component | weight | attr |
|----|-----------|-------:|------|
| C1 | Passenger growth (YoY) | 0.30 | `pax_growth` |
| C2 | Sustained growth (share of months positive) | 0.25 | `sustained_growth` |
| C3 | Passengers per departure (level) | 0.30 | `pax_per_departure` |
| C4 | Change in passengers per departure | 0.15 | `pax_per_departure_growth` |

Relative to v2, v2c drops V3 (peak concentration — the seasonality artefact)
and V4 (absolute growth — the duplicate of V1), and promotes a *level* term to
0.30 to counteract v2's inversion of airport size.

---

## 2. Component correlations

Pearson above the diagonal, Spearman below, raw values, n = 397.

|    | C1 | C2 | C3 | C4 |
|----|---:|---:|---:|---:|
| **C1** | — | 0.575 | 0.080 | **0.991** |
| **C2** | **0.876** | — | −0.006 | 0.265 |
| **C3** | −0.040 | 0.022 | — | 0.063 |
| **C4** | 0.440 | 0.310 | −0.092 | — |

**C1~C2 = +0.876 Spearman.** The growth redundancy identified in Phase 8.1
survives into v2c essentially unchanged, and C1+C2 carry 55% of the weight.
Two components that agree on rank order to +0.876 are close to one component
with a 0.55 weight. v2c does not solve the problem it was meant to narrow.

**C1~C4 Pearson +0.991 but Spearman only +0.440.** This gap is a warning, not a
finding: the Pearson figure is manufactured by a single outlier (GUF, +839,571%
growth — see §6). The rank measure is the honest one. It is recorded here
because reading the Pearson value alone would produce exactly the wrong
conclusion.

Component-to-composite Spearman: C1 +0.826, C2 +0.846, C3 +0.401, C4 +0.445.
The composite is mostly the growth pair.

---

## 3. Weight sensitivity

`rho` and rank moves are against the proposed 30/25/30/15 baseline.

| scenario | rho | med move | max | top-20 kept | NE top 3 | >10M mean |
|---|---:|---:|---:|---:|---|---:|
| proposed 30/25/30/15 | 1.000 | 0 | 0 | 20/20 | same | 47.7 |
| C3 heavier 25/20/40/15 | 0.968 | 17 | 84 | 10/20 | same | **53.5** |
| C3 lighter 35/30/20/15 | 0.978 | 15 | 66 | 16/20 | same | 41.9 |
| C3 removed 40/35/0/25 | 0.868 | 38 | 140 | 12/20 | HYA, PSM, HVN | **30.4** |
| equal 25 each | 0.989 | 11 | 71 | 17/20 | same | 45.0 |
| growth-led 40/30/20/10 | 0.975 | 16 | 75 | 16/20 | same | 41.8 |

Two things follow.

First, v2c is **not robust in the top 20**. A modest reweighting — C3 from 0.30
to 0.40, well inside the range a reasonable analyst might choose — retains only
**10 of 20** top airports. The regional shortlist is more stable (New England's
top 3 is unchanged in every scenario except C3 removal) and the ordering of the
cohort as a whole is stable (rho ≥ 0.868 throughout), but the specific output a
user of this tool would actually read — the shortlist — moves substantially on
a defensible change of opinion about one weight.

Second, and more important: **the large-airport mean is a C3 knob.** It runs
30.4 → 41.9 → 47.7 → 53.5 as C3's weight goes 0.00 → 0.20 → 0.30 → 0.40. The
"correction" of v2's size inversion is entirely attributable to how hard C3 is
weighted. Choosing 0.30 because it puts large airports near the cohort mean
would be fitting the formula to a desired ranking, which this phase was
explicitly instructed not to do. I therefore do **not** present 0.30 as
validated; I present it as arbitrary.

---

## 4. Distribution

**By passenger volume** (mean score):

| band | n | v1 | v2 | v2c |
|---|---:|---:|---:|---:|
| < 100k | 165 | 31.9 | 46.6 | 37.7 |
| 100k–1M | 129 | 42.6 | 47.4 | 46.8 |
| 1M–10M | 75 | 46.8 | 37.9 | 47.2 |
| > 10M | 30 | **61.5** | **27.0** | 47.7 |

**By FAA hub class** (mean score):

| hub | n | v1 | v2 | v2c |
|---|---:|---:|---:|---:|
| L | 30 | 61.5 | 27.0 | 47.7 |
| M | 35 | 48.1 | 31.5 | 44.6 |
| S | 79 | 45.0 | 45.8 | **49.8** |
| N | 255 | 35.4 | 46.8 | 40.4 |

v2's monotone inversion by size is gone. But v2c does not produce a monotone
relationship either — it produces a **flat** one, and under it **small hubs
score highest (49.8), above large hubs (47.7)**. That is not obviously wrong;
a demand-pressure proxy need not be monotone in size. It is, however, a
different claim from the one v2c was built to support, and it is not a claim
the evidence here establishes.

**Seasonality** (peak/mean ≥ 2.0):

| group | n | v1 | v2 | v2c |
|---|---:|---:|---:|---:|
| seasonal | 16 | 28.2 | 57.0 | 33.3 |
| non-seasonal | 381 | 40.9 | 43.3 | 43.7 |

Seasonal bonus: **v1 −12.7, v2 +13.7, v2c −10.4.** v2's seasonal bias is
corrected in v2c — this was the clearest success of the v2c design, and it is
the direct consequence of dropping V3. ACK falls from v2 42.1 to v2c 13.3;
MVY from 51.2 to 21.9; HYA from 73.1 to 46.5.

**Spread:**

| index | n | min | p25 | med | p75 | max |
|---|---:|---:|---:|---:|---:|---:|
| v1 | 399 | 1.5 | 30.8 | 41.5 | 50.2 | 77.8 |
| v2 | 397 | 5.2 | 31.5 | 43.6 | 55.0 | 92.6 |
| v2c | 397 | 0.8 | 31.0 | 43.7 | 55.3 | 90.1 |

---

## 5. v1 vs v2 vs v2c

| pair | Spearman | med rank move | max move | top-20 overlap |
|---|---:|---:|---:|---:|
| v1 ~ v2 | +0.412 | 60 | 365 | 4/20 |
| v1 ~ v2c | **+0.808** | 34 | 249 | **6/20** |
| v2 ~ v2c | +0.790 | 41 | 221 | 14/20 |

Coverage: v1 399/399, v2 397/399, v2c 397/399 (2 × `insufficient_coverage`;
the shape-based C2 needs ≥ 8 evaluable YoY month-pairs, which two airports lack.
Those two are suppressed, not imputed).

v2c is much closer to v1 than v2 was, but a **6/20 top-20 overlap** still means
adopting it would replace 70% of the shortlist this tool exists to produce.
That is a large change to hand a user on the strength of a weight choice §3
shows to be arbitrary.

### Diagnostic airports

| apt | pax | v1 | v2 | v2c | v1 rk | v2 rk | v2c rk | growth | sust | pax/dep |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BOS | 20,983,745 | 58.4 | 12.0 | 38.1 | 43 | 385 | 250 | −2% | 8% | 110.6 |
| ATL | 51,423,800 | 64.9 | 15.0 | 46.0 | 14 | 368 | 181 | −2% | 25% | 130.5 |
| GUF | 58,777 | 72.4 | — | — | **3** | — | — | **839571%** | — | 120.7 |
| HVN | 732,816 | 60.9 | 53.2 | 68.1 | 34 | 112 | 28 | 10% | 83% | 124.7 |
| SFO | 26,642,605 | 70.8 | 50.6 | 66.0 | 5 | 135 | 40 | 3% | 83% | 140.0 |
| ORD | 41,990,769 | 68.0 | 56.2 | 63.6 | 8 | 90 | 52 | 7% | 100% | 96.6 |
| JFK | 30,556,247 | 64.8 | 14.9 | 44.5 | 16 | 369 | 193 | −3% | 17% | 140.8 |
| ACK | 149,803 | 22.1 | 42.1 | 13.3 | 348 | 213 | 378 | −2% | 17% | 12.7 |
| AKN | 37,623 | 8.9 | 52.4 | 23.9 | 393 | 124 | 333 | −5% | 50% | 7.5 |
| HYA | 33,092 | 39.8 | 73.1 | 46.5 | 217 | 28 | 179 | 29% | 33% | 5.4 |

BOS still ranks 250th of 399 under v2c, and ATL 181st, because both had
slightly negative YoY passenger growth in this window and 55% of v2c's weight
is growth. **This is not necessarily an error** — a demand-*pressure* index
that ranks a shrinking airport low is behaving as designed, and I have not
attempted to reweight until BOS and ATL rise, which the brief prohibited. It is
recorded as a property a reviewer should decide about, not as a defect.

### New England

| apt | pax | v1 | v2 | v2c | v1 rk | v2c rk | peak |
|---|---:|---:|---:|---:|---:|---:|---:|
| PSM | 82,175 | 48.1 | 65.7 | 71.3 | 118 | **18** | 1.62 |
| HVN | 732,816 | 60.9 | 53.2 | 68.1 | 34 | **28** | 1.17 |
| BGR | 441,517 | 53.0 | 62.3 | 53.4 | 76 | 112 | 1.72 |
| PWM | 1,277,351 | 47.5 | 54.2 | 51.8 | 130 | 128 | 1.58 |
| MHT | 683,107 | 42.5 | 50.1 | 50.5 | 182 | 141 | 1.23 |
| PVD | 2,095,785 | 47.9 | 38.1 | 48.1 | 126 | 166 | 1.18 |
| HYA | 33,092 | 39.8 | 73.1 | 46.5 | 217 | 179 | 4.02 |
| BOS | 20,983,745 | 58.4 | 12.0 | 38.1 | 43 | 250 | 1.16 |
| ACK | 149,803 | 22.1 | 42.1 | 13.3 | 348 | 378 | 3.47 |

v2c's New England ordering is more defensible than v2's — the two highest
peak-ratio airports (ACK 3.47, MVY 4.38) are no longer promoted by seasonality
alone. But under v2c the region's top pick is PSM (Portsmouth, 82,175
passengers), ranked 18th nationally, ahead of every large hub. Whether that is
insight or artefact is not something this evidence settles.

---

## 6. v1 eligibility defect and the proposed guard

*This section is independent of v2c and stands whatever is decided about the
scoring formula.*

v1's T2 (passenger growth, weight 0.30) computes `window_pax / prior_pax − 1`.
It requires only that the prior total is non-zero. **v1 has no month-count
guard anywhere**, so an airport with two reported prior months is compared
against a full twelve, and the resulting "growth" is an artefact of data
coverage rather than a measurement of demand.

Airports scored by v1 with fewer than 12 prior months: **4 of 399.**

| apt | prior mo | window mo | pax growth | v1 | v1 rank | v2c |
|---|---:|---:|---:|---:|---:|---:|
| GUF | **2** | 12 | **+839,571%** | 72.4 | **3** | — |
| WYS | 6 | 6 | +22% | 48.1 | 121 | — |
| GST | 11 | 11 | −1% | 15.9 | 373 | 21.5 |
| KLW | 11 | 11 | −4% | 17.5 | 367 | 19.2 |

**GUF (Gulf Shores International / Jack Edwards Field, AL — 58,777 passengers)
is ranked 3rd of 399 by production v1 today, on a growth figure of +839,571%
derived from two months of prior data.** This is the clearest defect found in
either phase. It is small in scope — one airport in the top 50 — but it is in
the part of the output a user reads first, and the number is not a measurement
of anything.

### Two formulations were measured; the first was wrong

My initial proposal was an absolute floor on prior months:

```python
MIN_PRIOR_MONTHS_FOR_GROWTH = 10   # REJECTED
```

That is the wrong rule. It also discards **WYS (Yellowstone)**, which has 6
prior months *and* 6 window months — a seasonal airport that only operates part
of the year, compared six-against-six. Its +22% growth is a legitimate
like-for-like figure, and an absolute floor would have thrown it away and
dropped WYS 118 rank places for no analytical reason.

The defect is not that the prior window is *short*. It is that the two windows
cover **different amounts of time**, so the ratio measures reporting coverage
rather than demand.

### Recommended correction

```python
MAX_WINDOW_MONTH_SHORTFALL = 1     # prior may trail the window by at most 1 month
```

Suppress a **T-100-derived** YoY growth component when
`traffic_months_prior < traffic_months - 1`.

| apt | prior | window | growth | absolute floor ≥ 10 | comparability |
|---|---:|---:|---:|---|---|
| GUF | 2 | 12 | +839,571% | DROP | **DROP** |
| WYS | 6 | 6 | +22% | DROP ✗ | **keep** ✓ |
| GST | 11 | 11 | −1% | keep | keep |
| KLW | 11 | 11 | −4% | keep | keep |

Properties:

- It is a **coverage rule, not a methodology change.** It changes no weight and
  introduces no component.
- It **drops** the component; it never imputes it. The existing weight
  renormalisation and 0.60 coverage floor then apply unchanged — the same
  treatment every other missing component already receives.
- It applies uniformly to v1's T2 and to the T-100 growth components of v2 and
  v2c, so it does not privilege one formulation.
- It does **not** govern v1's T5 (FAA annual enplanements), which is a different
  source with its own coverage characteristics.
- It tolerates a one-month reporting lag, which is normal in T-100.

### Measured effect (simulated; not applied)

**Exactly one airport in 399 is affected.**

| apt | prior mo | v1 before | v1 after | rank before | rank after | move |
|---|---:|---:|---:|---:|---:|---:|
| GUF | 2 | 72.4 | 60.6 | **3** | **35** | +32 |

Collateral effect on the other 398 airports: **no score changes at all**, max
rank move **1**, v1-before ~ v1-after Spearman **+0.9999**. The 32 airports
whose rank number changes each move up one place as GUF vacates 3rd.

This is as narrow as a correction can be: it removes one meaningless number and
leaves the rest of the index alone.

**Remaining uncertainty:** GUF still scores 60.6 and ranks 35th after the guard,
on 58,777 passengers. The guard removes the fabricated growth term but does not
address whatever else is elevating GUF. I have not investigated that further in
this phase, and I am not claiming the guard makes GUF's score correct — only
that it removes a component that was meaningless.

**Both guards are implemented as opt-in properties
(`growth_windows_comparable`, recommended; `growth_coverage_ok`, the rejected
variant kept for reproducibility) and neither is wired into scoring.** Two tests
(`test_growth_guard_is_not_yet_applied_to_scoring`,
`test_comparability_guard_is_not_wired_into_scoring`) pin that, so neither can
become active without an explicit decision.

---

## 7. Does C3 add information? Gauge vs terminal-capacity pressure

**This is the finding that decides against v2c.**

C3 is `passengers / departures`. That decomposes exactly:

```
passengers/departure  =  (seats/departure) × (passengers/seats)
                      =        gauge        ×    load factor
```

Measured against both factors across the cohort:

| relationship | Pearson | Spearman |
|---|---:|---:|
| C3 ~ seats per departure (**gauge**) | **+0.981** | **+0.979** |
| C3 ~ load factor | +0.630 | +0.661 |
| C3 ~ total passengers (throughput) | — | +0.808 |

**C3 ~ gauge is +0.979 Spearman.** US load factors cluster tightly in roughly
78–86%, so the load-factor factor is nearly constant across airports and
pax/dep ≈ 0.82 × gauge. **C3 is v1's T3 (gauge) relabelled**, carrying 0.30 of
the weight instead of T3's 0.15.

This matters because reviewing T3 was part of the original motivation for
revisiting TDPI. v2c does not reduce the tool's reliance on gauge; it doubles
it.

### Gauge is not terminal-capacity pressure — the distinction, explicitly

The two are conceptually different quantities, and the difference is not subtle:

- **Gauge / passengers per departure is a per-MOVEMENT measure.** It describes
  how full the average aircraft is. It is a property of airline fleet
  assignment.
- **Terminal pressure is a per-TIME-PERIOD measure.** It concerns how many
  people are in the building, at once, relative to what the building holds.

They can move in opposite directions. Two airports:

| | flights | pax/flight | total pax | C3 |
|---|---:|---:|---:|---:|
| A | 100 | 200 | 20,000 | **200** |
| B | 200 | 100 | 20,000 | **100** |

**Identical terminal throughput. C3 differs by 2×.** C3 ranks A twice as
pressured as B on a quantity that says nothing about how many people either
terminal handles.

The empirical consequence is visible in the cohort's highest C3 values:

| apt | pax/dep | gauge | LF | passengers | hub | v2c rank |
|---|---:|---:|---:|---:|---|---:|
| PGD | 151.6 | 176.7 | 86% | 1,125,322 | S | 37 |
| PIE | 149.6 | 178.5 | 84% | 1,395,715 | S | 15 |
| SFB | 147.8 | 178.2 | 83% | 1,533,164 | S | 50 |
| AZA | 146.7 | 177.8 | 82% | 1,033,572 | S | 114 |
| **HGR** | 145.7 | 175.7 | 83% | **44,735** | N | 54 |
| **STC** | 145.3 | 175.8 | 83% | **26,016** | N | 125 |
| MCO | 144.8 | 176.9 | 82% | 28,447,821 | L | 42 |
| **BLV** | 143.4 | 173.4 | 83% | **196,442** | N | **5** |
| JFK | 140.8 | 171.6 | 82% | 30,556,247 | L | 193 |

The top four (PGD, PIE, SFB, AZA) are **Allegiant / ULCC bases**: airports whose
traffic is a small number of departures on large single-aisle aircraft. HGR
(44,735 annual passengers) and STC (26,016) score near the top of C3 while
handling fewer passengers in a year than a large hub handles in a morning.
**BLV, with 196,442 passengers, ranks 5th of 399 under v2c.** C3 anchors the
index to aircraft density, not to the scale of passenger handling.

**Answer to the question asked:** C3 does not add meaningful information beyond
the other components. It is 0.979-redundant with a quantity v1 already scores
(as T3), it is not a measure of terminal-capacity pressure, and the tests and
definition notes now record that explicitly. The definition of C3 carries the
warning in the code itself: *"This is a per-MOVEMENT measure, not a
terminal-throughput measure, and it is NOT a measurement of terminal
capacity."*

---

## 8. Classification thresholds — 60/40 was not reused unexamined

Production classification is **unchanged**. The 60/40 thresholds were measured
against each candidate rather than assumed transferable.

| index | ≥ 60 (HI) | < 40 (LO) | p60 value | p40 value |
|---|---:|---:|---:|---:|
| v1 | 37 (9.3%) | 186 (46.6%) | 44.8 | 37.7 |
| v2 | 77 (19.4%) | 167 (42.1%) | 47.6 | 39.4 |
| v2c | 68 (17.1%) | 160 (40.3%) | 48.7 | 39.7 |

TERMINAL_LED counts under the current rule (TDPI ≥ 60 **and** ACI < 40):

| index | TERMINAL_LED | high TDPI, ACI unmeasured |
|---|---:|---:|
| v1 | 7 | 9 |
| v2 | 11 | 45 |
| v2c | **15** | **30** |

Adopting v2c under the inherited 60/40 cut-offs would **more than double**
TERMINAL_LED (7 → 15) and more than triple the count of airports with a high
TDPI whose airside congestion is unmeasured (9 → 30). The second number is the
concerning one: those are airports the tool would present as high terminal
pressure while having no airside measurement to compare against — and the
system's design principle is that absence of a congestion measurement is not
evidence of absence of congestion. Tripling that population would weaken the
divergence claim that is the analytical centre of this tool.

Note also that none of the three indices puts 60 at the 60th percentile — for
v1, the 60th percentile is 44.8. The thresholds are absolute cut-offs on the
normalised scale, not percentile cuts. That is intended and documented, but it
means threshold behaviour must be re-measured per formulation, as done here.

---

## 9. Limitations and remaining uncertainty

1. **One window, one cohort.** All of this is measured on 2025-05..2026-04 for
   399 airports. No cross-window stability testing was done, so I cannot say
   whether the C1~C2 correlation of +0.876 or the C3~gauge correlation of
   +0.979 is stable across years. The gauge identity is arithmetic and will
   hold; the growth correlation is empirical and may not.
2. **No ground truth.** There is no dataset here recording which airports
   actually needed terminal investment, so "better" cannot be measured
   directly. Every comparison in this report is internal consistency and
   conceptual validity, not predictive accuracy. **None of v1, v2 or v2c is
   validated against outcomes.**
3. **No score here measures terminal capacity.** TDPI in any version is a
   demand-pressure proxy built from traffic data. It does not measure terminal
   floor area, gate count, processing rates, or queue lengths, and a high score
   is not evidence that an airport requires terminal expansion.
4. **The 0.30 C3 weight is arbitrary**, as §3 shows. So, for the same reason,
   are v1's weights — this is a limitation of v1 that v2c does not fix and that
   this phase did not resolve for either.
5. **The guard simulation holds cohort bounds fixed.** It blanks
   `passengers_prior` after the cohort's winsorization bounds are built. Since
   GUF's +839,571% is far outside P95 and already clipped, the effect on the
   bounds is negligible, but the simulated ranks are not bit-identical to what a
   full re-run with the guard applied at load time would produce.
6. **The guard's one-month tolerance is a judgement, not a measurement.**
   `MAX_WINDOW_MONTH_SHORTFALL = 1` allows for normal T-100 reporting lag. The
   window-shortfall distribution on this warehouse is bimodal with nothing in
   between — **398 airports at 0, GUF alone at 10** — so every tolerance from 0
   to 9 isolates GUF and no other airport. The parameter is therefore
   unconstrained by this evidence: only the guard's *form* (comparability rather
   than an absolute floor) is supported by it. The tolerance would start to
   matter on a warehouse with partial-year reporters, which this one does not
   have.
7. **Only four airports in the cohort have an incomplete prior window**, so the
   choice between guard formulations rests on two cases (GUF, WYS). The
   reasoning generalises, but the sample supporting it is very small.

---

## 10. What a further alternative would need to address

Recorded for a future phase; not implemented.

1. **Collapse the growth pair.** C1 and C2 agree at +0.876 while jointly
   carrying 0.55. One growth component with a durability requirement built in —
   rather than a magnitude term plus a consistency term scored separately —
   would remove the double-count that both v2 and v2c preserve.
2. **Find a level anchor that is actually about the terminal.** The requirement
   is a per-time-period measure of passenger handling, not a per-movement one.
   Passengers per gate, per terminal square metre, or per processing position
   would qualify; none is present in T-100, FAA enplanements or OTP, so this
   needs a data source the system does not currently have. Peak-hour or
   peak-day passenger counts would also qualify and would be derivable if
   sub-monthly traffic data were available.
3. **Do not re-anchor on gauge.** Any candidate whose level term correlates
   above ~0.9 with seats per departure is T3 with a new label, and §7 applies
   to it unchanged.
4. **Derive thresholds with the formula.** Classification cut-offs are part of
   a scoring proposal, not an inheritance from the previous one.

---

## 11. Changes made in this phase

All additive and opt-in. `git diff --stat backend/app`: **432 insertions, 2
deletions**; the two deleted lines are an import and the cohort attribute list,
both extended rather than altered.

| file | change |
|---|---|
| `app/analytics/definitions.py` | `TDPI_V2C_METRICS`, `TDPI_V2C_NOTES`, `MIN_PRIOR_MONTHS_FOR_GROWTH` (rejected variant), `MAX_WINDOW_MONTH_SHORTFALL` (recommended); C3's warning note |
| `app/analytics/metrics.py` | `pax_per_departure`, `pax_per_departure_growth`, `growth_coverage_ok`, `growth_windows_comparable`, monthly series loading |
| `app/analytics/scoring.py` | `compute_tdpi_v2c()` with weight override; cohort stats extended to the new attrs |
| `evaluate_tdpi_v2c.py` | new — 8 measurement sections (`--section correlation\|sensitivity\|distribution\|compare\|eligibility\|guard\|c3\|thresholds`) |
| `tests/test_tdpi_v2c.py` | new — 26 deterministic tests |

`frontend/vite.config.ts` also shows as modified: that is your own change of the
dev-server proxy port to 8001, untouched by this phase.

**Not changed:** v1 weights, ACI weights, `DIVERGENCE_HI`/`DIVERGENCE_LO`, the
analysis window, `score_airport()`, any API contract, the frontend, the agent.
Regression tests pin each of these.

### Test results

```
424 passed in 18.56s
```

Full suite, run with the project's mocked LLM clients. No live API calls, no
failures, no skips.

One test needed correcting during this phase:
`test_c3_tracks_gauge_far_more_closely_than_load_factor` originally asserted
that C3 is *strictly monotone* in gauge. That is false — a load factor varying
across the realistic 78–86% band can reorder adjacent gauge steps, which is
precisely why the measured Spearman is 0.979 and not 1.000. The assertion was
wrong, not the finding; it now asserts near-but-not-perfect rank agreement with
gauge and a much weaker one with load factor, with bounds taken from the
real-cohort measurement rather than from the synthetic fixture.

---

## 12. Decision requested

1. **Retain v1 as production TDPI.** (Recommended.)
2. **Adopt the window-comparability guard** (`MAX_WINDOW_MONTH_SHORTFALL = 1`)
   as a data-quality correction to v1, applied to T-100-derived growth
   components. This is the one change I recommend making to production, and it
   is independent of items 1 and 3. It affects exactly one airport: GUF moves
   from rank 3 to rank 35, and no other airport's score changes.
3. **Keep v2 and v2c as opt-in experimental paths** for the record, or remove
   them — either is defensible. They are not reachable from any production path.
4. **Defer** a further alternative to a later phase, per §10.

Nothing is committed. Awaiting review.
