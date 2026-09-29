# Phase 8.1 — TDPI v2 Evaluation

**Recommendation: do not adopt v2 as proposed. Keep v1 in production.**
Three of the five proposed components are sound; two produce distortions severe
enough to make the composite unfit for investment screening. A corrected
variant (**v2c**) is specified and measured below and is the recommended basis
for Phase 8.2.

All figures measured offline against the committed warehouse, window
**2025-05 … 2026-04**, cohort **399 airports with traffic**. Reproduce with
`python evaluate_tdpi_v2.py`.

---

## 1. Components, formulas, weights, sources

| id | Component | Formula | Weight | Source |
|---|---|---|---:|---|
| V1 | Passenger growth YoY | `pax_w / pax_prior − 1` | 0.30 | BTS T-100 |
| V2 | Sustained growth | `positive_YoY_months / evaluable_months` | 0.25 | BTS T-100 |
| V3 | Peak demand concentration | `max(monthly_pax) / mean(monthly_pax)` | 0.20 | BTS T-100 |
| V4 | Absolute passenger growth | `pax_w − pax_prior` | 0.15 | BTS T-100 |
| V5 | Change in passengers/departure | `(pax/dep)_w / (pax/dep)_prior − 1` | 0.10 | BTS T-100 |

Normalisation, weight renormalisation, the 0.60 coverage floor and the
no-imputation rule are **identical to v1**, so every v1/v2 difference is
attributable to the component set rather than the machinery.

**Eligibility** (`MIN_YOY_MONTHS_FOR_SUSTAINED = 8`, `MIN_MONTHS_FOR_PEAK = 10`):
V2 needs ≥8 of 12 calendar-aligned YoY month-pairs; V3 needs ≥10 window months.
Below either, the component returns `None` — dropped, never imputed. An airport
failing the shape rules has its whole v2 score suppressed with
`insufficient_coverage`.

**Verified against the warehouse, not assumed.** `airport_month` holds 24
monthly rows per airport (2024-05…2026-04), so all five components are
computable. Month-pairs are matched on the same calendar month, so seasonal
airports are not compared July-to-January.

---

## 2. Data availability

| | |
|---|---|
| TDPI v1 scored | 399 / 399 (100%) |
| TDPI v2 scored | 397 / 399 (99.5%) |
| Lost to v2 eligibility | 2 — GUF (2 YoY pairs), WYS (6 months) |

392 of 399 airports have all 12 YoY month-pairs. Availability is not a reason to
reject v2.

---

## 3. Redundancy — the double-counting concern is confirmed

Spearman (lower triangle) / Pearson (upper), raw values, n ≈ 397:

| | V1 | V2 | V3 | V4 | V5 |
|---|---|---|---|---|---|
| **V1** | — | 0.575 | 0.012 | 0.013 | **0.991** |
| **V2** | **0.876** | — | −0.071 | 0.396 | 0.265 |
| **V3** | 0.026 | −0.043 | — | 0.038 | 0.021 |
| **V4** | **0.747** | **0.827** | −0.021 | — | 0.016 |
| **V5** | 0.440 | 0.310 | 0.117 | 0.172 | — |

**V1, V2 and V4 form a growth cluster** (Spearman 0.75–0.88) carrying **70% of
the weight**. Growth is effectively counted three times — exactly the risk
flagged in the brief, now measured.

- **V4 is the clearest redundancy.** Spearman 0.747 with V1 and 0.827 with V2;
  it is the same quantity in absolute form. It also carries a size bias by
  construction: a 2% gain at a 10M-passenger hub outweighs a 40% gain at a
  100k airport.
- **V5 is unstable.** Pearson 0.991 with V1 but Spearman only 0.440 — the
  linear agreement is driven by a few extreme values, not by consistent
  ordering. Useful, but it is close to V1 whenever departures are flat.
- **V3 is genuinely orthogonal** (|r| ≤ 0.07 against everything) — it adds
  independent information. Section 4 shows what that information actually is.

Component-to-composite Spearman: V1 +0.901, V2 +0.886, V4 +0.771, V5 +0.471,
V3 +0.295.

---

## 4. V3 measures seasonality, not pressure

Top 12 by peak/mean — **all nonhub**:

| Airport | peak/mean | Passengers | v1 | v2 |
|---|---:|---:|---:|---:|
| AKN King Salmon | 4.67 | 37,623 | 8.9 | **52.4** |
| MVY Martha's Vineyard | 4.38 | 87,319 | 27.7 | 51.2 |
| HYA Cape Cod Gateway | 4.02 | 33,092 | 39.8 | **73.1** |
| ACK Nantucket | 3.47 | 149,803 | 22.1 | 42.1 |
| GST Gustavus | 3.45 | 9,934 | 15.9 | 48.5 |
| EGE Eagle County | 2.34 | 326,522 | 46.4 | 76.7 |

Salmon-fishing Alaska, summer islands, ski resorts. The list is a seasonality
ranking.

- 16 airports have peak/mean ≥ 2.0. Their **mean v2 is 57.0 against 43.3** for
  everyone else — a **+13.7 point bonus for being seasonal**.
- peak/mean vs airport size: **Spearman −0.362**. Smaller airports peak harder.

Terminals *are* sized for peak, so the intuition behind V3 is reasonable. But
peak/mean cannot distinguish "constrained at peak" from "empty for nine
months". King Salmon rising from 8.9 to 52.4 is not a finding about terminal
demand pressure.

---

## 5. Ranking impact

Spearman v1~v2 = **0.412**. Median rank move **60**, 90th percentile **223**,
max **365**. **Top-20 overlap: 4 / 20.**

| Airport | Passengers | v1 rank | v2 rank | Move |
|---|---:|---:|---:|---:|
| ATL | 51,423,800 | 14 | **368** | −354 |
| JFK | 30,556,247 | 16 | **369** | −353 |
| SEA | — | 18 | 336 | −318 |
| MIA | — | 17 | 305 | −288 |
| SFO | 26,642,605 | 5 | 135 | −130 |
| ORD | 41,990,769 | 8 | 90 | −82 |

v2's own top 20 is BIH (Bishop), LAL (Lakeland), OGS (Ogdensburg), LAF, MKL
(+111), RHI (+142) — small airports throughout.

**Size behaviour is inverted:**

| Passengers | n | mean v1 | mean v2 | Δ |
|---|---:|---:|---:|---:|
| < 100k | 165 | 31.9 | 46.6 | **+15.1** |
| 100k–1M | 129 | 42.6 | 47.4 | +4.9 |
| 1M–10M | 75 | 46.8 | 37.9 | −8.8 |
| **> 10M** | 30 | **61.5** | **27.0** | **−34.5** |

A terminal-demand-pressure index that ranks Rhinelander, Wisconsin above
Atlanta is not fit for the brief's purpose.

### New England

| Airport | v1 | v2 | v1 rk | v2 rk | Move | Passengers | peak | sust |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| HVN | 60.9 | 53.2 | 34 | 112 | −78 | 732,816 | 1.17 | 83% |
| **BOS** | 58.4 | **12.0** | 43 | **385** | **−342** | 20,983,745 | 1.16 | 8% |
| BGR | 53.0 | 62.3 | 76 | 60 | +16 | 441,517 | 1.72 | 50% |
| BDL | 49.1 | 22.9 | 111 | 343 | −232 | 3,251,263 | 1.12 | 25% |
| PWM | 47.5 | 54.2 | 130 | 106 | +24 | 1,277,351 | 1.58 | 67% |
| BTV | 43.0 | 41.4 | 177 | 222 | −45 | 702,627 | 1.35 | 50% |
| **HYA** | 39.8 | **73.1** | 217 | **28** | **+189** | 33,092 | 4.02 | 33% |
| **BID** | 35.2 | 65.9 | 270 | 47 | **+223** | 17,466 | 2.19 | 50% |
| **WST** | 34.5 | 65.0 | 273 | 49 | **+224** | 17,469 | 2.25 | 50% |
| MVY | 27.7 | 51.2 | 322 | 129 | +193 | 87,319 | 4.38 | 33% |
| ACK | 22.1 | 42.1 | 348 | 213 | +135 | 149,803 | 3.47 | 17% |

Under v2 the New England terminal-expansion shortlist becomes **Cape Cod
Gateway, Block Island and Westerly** — 17k–33k passengers a year — while Boston
Logan ranks 385th of 397.

**But v2 surfaces one genuine finding v1 hides:** BOS's sustained-growth share
is **8%** — passengers fell year-on-year in 11 of 12 months. v1 scored BOS 58.4
mostly on load factor (normalised 95.4) and throughput per runway (80.1), both
*level* metrics. v1 is partly measuring "is large" rather than "is under
pressure". That critique of v1 is fair and survives this evaluation.

---

## 6. Weight sensitivity

Against the proposed weights, over 397 airports:

| Scenario | Spearman | median move | max move | top-20 overlap |
|---|---:|---:|---:|---:|
| proposed 30/25/20/15/10 | 1.000 | 0 | 0 | 20/20 |
| drop V3 (35/30/0/20/15) | 0.950 | 20 | 133 | 17/20 |
| drop V4 (35/30/25/0/10) | 0.994 | 6 | 66 | 18/20 |
| drop V3+V4 (40/35/0/0/25) | 0.937 | 24 | 152 | 15/20 |
| equal (20 each) | 0.987 | 9 | 83 | 16/20 |
| growth-heavy (50/30/0/10/10) | 0.947 | 22 | 144 | 16/20 |

V3 carries disproportionate influence — dropping it moves the median airport 20
places and one airport 133 — despite correlating only +0.295 with the composite.
V4 is nearly inert at the top (18/20 overlap) while contributing redundancy,
which is the worst combination: it adds little signal and duplicates V1/V2.

---

## 7. Recommended correction — v2c (measured, not proposed blind)

Keep what works, drop what distorts, restore a level anchor:

| id | Component | Weight | Change |
|---|---|---:|---|
| C1 | Passenger growth YoY | 0.30 | keep V1 |
| C2 | Sustained growth | 0.25 | keep V2 |
| C3 | **Passengers per departure (level)** | 0.30 | **new anchor** |
| C4 | Change in passengers per departure | 0.15 | keep V5 |

V3 and V4 removed. C3 is a *level* metric — how many people each movement
delivers into the terminal — which is terminal-relevant in a way that
throughput-per-runway never was, and which anchors the index to airports that
actually handle volume.

Measured:

| Passengers | mean v1 | mean v2 | **mean v2c** |
|---|---:|---:|---:|
| < 100k | 31.9 | 46.6 | **38.2** |
| 100k–1M | 42.6 | 47.4 | **46.8** |
| 1M–10M | 46.8 | 37.9 | **47.2** |
| > 10M | 61.5 | 27.0 | **47.7** |

The inversion is gone. Seasonal and hub distortions both correct:

| Airport | v1 rk | v2 rk | v2c rk |
|---|---:|---:|---:|
| SFO | 5 | 135 | **41** |
| ORD | 8 | 90 | **53** |
| ATL | 14 | 368 | **183** |
| JFK | 16 | 369 | **195** |
| BOS | 43 | 385 | **252** |
| HYA | 217 | 28 | **181** |
| BID | 270 | 47 | **259** |
| AKN | 393 | 124 | **335** |
| ACK | 348 | 213 | **380** |

ATL and BOS still sit mid-table under v2c — because their passenger counts are
*falling*. That is a defensible analytical position, unlike ranking them near
the bottom of 397.

**v2c is not yet implemented.** It is specified and measured here so Phase 8.2
can start from evidence.

---

## 8. Surprising results worth noting

- **BOS at 8% sustained growth.** Boston lost passengers in 11 of 12 months.
  v1's 58.4 came from level metrics, not pressure — a real weakness in v1.
- **V5's Pearson 0.991 / Spearman 0.440 split.** A textbook case of outliers
  manufacturing a linear correlation that ordering does not support. Either
  statistic alone would have misled.
- **V3 is orthogonal *and* harmful.** Low correlation usually argues for
  inclusion. Here the independent information is seasonality, which is not the
  target construct — orthogonality is necessary but not sufficient.
- **v2 suppressed GUF**, which v1 ranked **3rd**. It has only 2 usable YoY
  month-pairs. v1 scored it confidently on a year of data with almost no
  comparable prior year — arguably v2's suppression is the more honest outcome.

---

## 9. What is implemented

**Implemented and tested (opt-in, experimental):**
- `pax_per_departure`, `pax_per_departure_growth`, `sustained_growth`,
  `peak_concentration`, `absolute_pax_growth` on `AirportMetrics`
- Monthly passenger/departure series loaded for both windows
- `TDPI_V2_METRICS` and `compute_tdpi_v2()` with per-call weight override
- FAA enplanement growth and throughput-per-runway reported as **context, not
  scored** — removing the v1 double-count
- `evaluate_tdpi_v2.py` — the full harness behind this report
- 36 offline tests in `tests/test_tdpi_v2.py`

**Unchanged (verified by regression tests):** TDPI v1 weights, ACI, UDEI, the
analysis window, all API endpoints, agent behaviour, the frontend. Nothing
calls v2 unless asked.

**Not implemented:** v2c; any change to divergence classification; any change
to production defaults.

## 10. Classification implications (for later, not changed now)

TERMINAL_LED requires TDPI ≥ 60 **and** ACI < 40. Under any v2 variant the TDPI
distribution shifts, so the fixed 60/40 thresholds would select a different and
much smaller set — most v2 high scorers are small airports whose ACI is
suppressed by the volume gate, landing them in `UNCLASSIFIED_AIRSIDE_UNKNOWN`
rather than TERMINAL_LED. Adopting any v2 therefore requires revisiting the
thresholds, probably as cohort percentiles rather than absolute cut-points.
Deferred deliberately.

## 11. Limitations

- One 12-month window against one prior year. "Sustained" means 12 months, not
  a multi-year trend; a longer history would test durability properly.
- CY2025 FAA enplanements are preliminary.
- Peak concentration uses monthly granularity. Terminals are sized for *hourly*
  peaks, which no dataset here contains — monthly peak is itself a proxy for a
  proxy.
- v2c's C3 weight (0.30) was chosen to balance the size buckets, not derived
  from outcomes. No labelled dataset of successful airport investments exists to
  fit weights against; this remains judgement, stated openly.
