# Phase 8.1c — TDPI Decision and the Year-over-Year Comparability Guard

**Status:** complete, uncommitted, awaiting review
**Window:** 2025-05 .. 2026-04 (unchanged)
**Cohort:** 399 US primary commercial service airports
**Tests:** 435 passed, 0 failed, 0 skipped — full offline suite
**External calls:** none. No Anthropic API, no downloads, no paid services.

Reproduce every figure below with:

```
python evaluate_yoy_comparability.py            # all four sections
python evaluate_yoy_comparability.py --section inspect|effect|demo|rule
```

---

## 1. Decisions carried forward (unchanged)

| Decision | Status |
|---|---|
| TDPI **v1** is the production scoring formula | retained, weights untouched |
| TDPI **v2** and **v2c** | rejected; remain opt-in experiments, unreachable from production |
| ACI, UDEI | unchanged — ACI scores are bit-identical before/after (see §5) |
| 60/40 divergence thresholds | unchanged |
| API contracts, frontend, agent | unchanged |
| Further TDPI formula research | deferred until suitable data exists |

Rationale for rejecting v2 and v2c is recorded in
`phase-8.1-decision-record.md`. This document covers only the one approved
change: the year-over-year comparability guard on v1's T2.

---

## 2. The defect

v1's T2 (passenger growth, weight 0.30) computed
`window_passengers / prior_window_passengers − 1` with **no check that the two
windows covered the same months**.

**GUF (Gulf Shores International / Jack Edwards Field, AL)** reports 12 window
months against a prior year containing **two months totalling seven
passengers** — 5 in 2024-06 and 2 in 2024-08. v1 computed **+839,571% growth**
and ranked it **3rd of 399**. That number measures reporting coverage, not
demand.

---

## 3. The rule, and why it is not a month-count threshold

> **T2 is computed only when every month of the current window has a prior-year
> counterpart, and the ratio is summed over the matched months alone.**
> Otherwise T2 is dropped — never imputed — and the remaining weights
> renormalise through the existing machinery.

The brief asked me to verify whether comparable windows cover *matching calendar
months* rather than merely equal month counts. They do not, and the distinction
decided the design. Three formulations were measured against all six airports
that differ:

| apt | win | pri | matched | (a) floor ≥10 | (b) equal counts | (c) matching months |
|---|---:|---:|---:|---|---|---|
| GUF | 12 | 2 | 2 | suppress | suppress | **suppress** ✓ |
| WYS | 6 | 6 | 6 | ~~suppress~~ ✗ | keep | **keep** ✓ |
| GST | 11 | 11 | 10 | ~~keep~~ ✗ | ~~keep~~ ✗ | **suppress** ✓ |
| KLW | 11 | 11 | 10 | ~~keep~~ ✗ | ~~keep~~ ✗ | **suppress** ✓ |
| BGM | 11 | 12 | 11 | keep | keep | **keep** ✓ |
| BLD | 11 | 12 | 11 | keep | keep | **keep** ✓ |

- **(a) An absolute floor (prior ≥ 10 of 12 months) fails on WYS.** Yellowstone
  is a seasonal airport reporting 2025-05..2025-10 and 2024-05..2024-10 — six
  months on each side, *perfectly aligned*. Its +21.8% is a valid like-for-like
  figure. A floor would discard a correct number and move WYS 118 rank places
  for no analytical reason. This is why no minimum of 10 or 12 was imposed.
- **(b) Equal month counts fails on GST and KLW.** Both report 11 window and 11
  prior months and pass any count-based test, yet their window contains
  **2025-12** while their prior side contains **2025-04** instead. A SUM/SUM
  ratio there compares December traffic against April traffic.
- **(c) Matching calendar months** states the property a ratio actually needs,
  has **no tunable parameter**, preserves valid seasonal comparisons, and
  catches the misalignment counts hide. **Adopted.**

### Why complete alignment, rather than scoring whatever overlaps

A natural alternative is to keep the component and restrict the ratio to matched
months. Measured, that is **not sufficient**: GUF's two matched months still
yield **+167,929%**, because the prior year does not cover the airport's
operation at all. When that is true, no ratio against it measures demand, so the
component is dropped rather than rescaled.

The matched-months restriction is still applied — it is what makes BGM and BLD
correct (§4) — but it is a *refinement of the ratio*, not the eligibility test.

---

## 4. Every airport with incomplete or misaligned prior-year coverage

All 399 were inspected. Six are flagged; the other 393 report 12 window months
each with a prior counterpart and are untouched by the rule.

| apt | name | win | pri | matched | unmatched window months | extra prior | eligible |
|---|---|---:|---:|---:|---|---|---|
| BGM | Greater Binghamton / Edwin A Link Field | 11 | 12 | 11 | — | 2025-03 | **YES** |
| BLD | Boulder City Municipal | 11 | 12 | 11 | — | 2025-03 | **YES** |
| GST | Gustavus | 11 | 11 | 10 | 2024-12 | 2025-04 | no |
| GUF | Gulf Shores Intl / Jack Edwards Field | 12 | 2 | 2 | 10 months | — | no |
| KLW | Klawock | 11 | 11 | 10 | 2024-12 | 2025-04 | no |
| WYS | Yellowstone | 6 | 6 | 6 | — | — | **YES** |

### Deviations from the expected outcome, reported rather than suppressed

The brief anticipated that GUF would be excluded and valid seasonal comparisons
preserved. Both happened. Three further effects were **not** anticipated by the
Phase 8.1b analysis and are reported here rather than engineered away:

1. **GST and KLW also lose T2.** Phase 8.1b's count-based rule would have kept
   them. Inspecting calendar months shows their windows are genuinely
   misaligned (December against April), so suppression is correct — but it is a
   change beyond the single airport 8.1b predicted.
2. **BGM and BLD keep T2 with a corrected value.** Each has an *extra* prior
   month (2025-03) with no window counterpart, which inflated the denominator so
   that 11 months of traffic were divided by 12. Excluding it moves BGM from
   −39.7% to −36.4% and BLD from −26.4% to −22.7%.
3. **The other 394 airports shift by up to 0.22 TDPI points.** See §5.

---

## 5. Before / after

### The five airports whose T2 value changes

| apt | T2 before | T2 after | TDPI before | TDPI after | rank before | rank after | move |
|---|---:|---:|---:|---:|---:|---:|---:|
| GUF | **+839,571.4%** | **—** | 72.4 | 60.6 | **3** | **35** | +32 |
| KLW | −3.7% | — | 17.5 | 15.8 | 367 | 375 | +8 |
| GST | −1.4% | — | 15.9 | 11.6 | 373 | 388 | +15 |
| BLD | −26.4% | −22.7% | 14.9 | 14.9 | 378 | 377 | −1 |
| BGM | −39.7% | −36.4% | 12.9 | 12.9 | 384 | 383 | −1 |

**WYS is deliberately absent from this table**: it keeps T2 = **+21.8%**
unchanged, which is the outcome the rule was designed to protect.

### The other 394 airports

| measure | value |
|---|---|
| TDPI score changed | 357 |
| max \|change\| | **0.219 pt** |
| median \|change\| | 0.172 pt |
| changes above 0.5 pt | **0** |
| rank number changed | 120 |
| max rank move | **3** |

**Cause — expected, not a defect.** TDPI normalises against cohort percentiles,
so suppressing a component removes observations from the `pax_growth`
distribution and shifts the winsorization bounds:

```
before: P5=-0.1454  P95=+0.3577  n=399
after : P5=-0.1491  P95=+0.3564  n=396
```

This is inherent to cohort-relative scoring — any change in component
availability has a small cohort-wide footprint. It is reported because it is
real, and it is bounded at a fifth of a point.

### Invariants

| | before | after |
|---|---:|---:|
| TDPI scored | 399 | **399** |
| ACI scored | 237 | 237 |
| TDPI suppressed entirely | 0 | **0** |
| min TDPI coverage | 1.00 | **0.70** |
| ACI score total | 10931.2699 | **10931.2699** |

- **No airport lost its TDPI score.** Dropping T2 leaves 0.70 coverage, which
  clears the existing 0.60 floor, so the three affected airports are still
  scored — with the component reported as unavailable rather than guessed.
- **ACI is bit-identical.** It shares no component with T2.

---

## 6. Effect on the four demo scenarios

**Substantively: none.** No divergence classification changes, no demo-relevant
airport loses T2, and the New England ordering is identical.

| apt | scenario | TDPI before | TDPI after | rank before | rank after |
|---|---|---:|---:|---:|---:|
| SFO | Q4 unmet demand | 70.8 | 71.0 | 5 | 4 |
| LAX | Q2 congestion | 64.0 | 64.2 | 21 | 19 |
| SNA | Q2 congestion | 60.1 | 60.2 | 37 | 37 |
| ANC | Q3 long-haul | 34.1 | 34.2 | 277 | 277 |
| BOS | Q1 New England | 58.4 | 58.6 | 43 | 43 |

**Q1 — New England ranking, before → after:**

| # | apt | TDPI before | TDPI after | national before | national after |
|---:|---|---:|---:|---:|---:|
| 1 | HVN | 60.9 | 61.1 | 34 | 33 |
| 2 | BOS | 58.4 | 58.6 | 43 | 43 |
| 3 | BGR | 53.0 | 53.2 | 76 | 76 |
| 4 | BDL | 49.1 | 49.3 | 111 | 110 |
| 5 | PSM | 48.1 | 48.3 | 118 | 118 |
| 6 | PVD | 47.9 | 48.1 | 126 | 125 |
| 7 | PWM | 47.5 | 47.7 | 130 | 130 |
| 8 | BTV | 43.0 | 43.2 | 177 | 177 |
| 9 | MHT | 42.5 | 42.7 | 182 | 182 |
| 10 | HYA | 39.8 | 39.9 | 217 | 217 |
| 11 | BID | 35.2 | 35.4 | 270 | 271 |
| 12 | ORH | 34.7 | 34.9 | 272 | 272 |
| 13 | WST | 34.5 | 34.6 | 273 | 275 |
| 14 | MVY | 27.7 | 27.8 | 322 | 322 |
| 15 | ACK | 22.1 | 22.3 | 348 | 348 |
| 16 | PQI | 19.7 | 19.8 | 357 | 357 |

- New England order **identical** before and after.
- Max |TDPI change| across all demo-relevant airports: **0.189 pt**.
- Divergence classifications changed: **none**.
- Q3 (Anchorage long-haul) draws on departure and aircraft-configuration data,
  not on T2, so it is unaffected by construction; ANC's TDPI moves 0.1 pt from
  the cohort-bounds shift only.

Q1's expected answer — HVN top of the region, no clean TERMINAL_LED case, HVN's
ACI reported as unmeasured rather than low — is unchanged.

---

## 7. Implementation

### Production change (the smallest that achieves the rule)

One property's semantics, in `app/analytics/metrics.py`:

- `_yoy_passenger_totals()` — returns current sum, prior sum, matched months and
  unmatched window months, summing only over months present on both sides.
- `yoy_windows_aligned` — the eligibility test: every window month has a prior
  counterpart.
- `yoy_months_matched` — diagnostic.
- `pax_growth` — now returns the like-for-like ratio, or `None` when the windows
  are not aligned.

`_compose()` in `scoring.py` was **not modified**: a `None` raw value already
makes a component unavailable, which drops it and renormalises the surviving
weights. The guard reuses the existing missing-data path rather than adding one.

### Backward compatibility

When an `AirportMetrics` carries no monthly series (some unit-test fixtures, and
any future loader that omits it), `pax_growth` falls back to the legacy annual
ratio and `yoy_windows_aligned` returns `True`. The rule suppresses on evidence
of misalignment, never on absence of evidence.

### Scope

The rule governs **T2** (BTS T-100 monthly passengers). It does **not** govern
**T5**, which is FAA annual enplanements — a different source, with its own
coverage characteristics and no monthly series to align. A test asserts T5 is
untouched when T2 is suppressed.

### Removed

`MIN_PRIOR_MONTHS_FOR_GROWTH` and `MAX_WINDOW_MONTH_SHORTFALL`, the two
month-count constants proposed in Phase 8.1b, are deleted along with their
properties. A test asserts neither name survives, so no arbitrary month minimum
can reappear unnoticed.

---

## 8. Tests

`tests/test_yoy_comparability.py` — **19 deterministic tests**, no network.

| group | covers |
|---|---|
| Matching months | aligned windows comparable; the guarded ratio equals the legacy ratio when data is complete |
| Valid seasonal windows | the WYS case kept at +21.8%; a 4-month and a 12-month airport treated identically |
| Missing months | GUF suppressed; the legacy +839,571% recorded; **matched months alone would not have saved GUF**; the GST/KLW single-missing-month case; BGM/BLD extra-prior-month denominator; zero prior traffic |
| Suppression / renormalisation | T2 dropped not zeroed, surviving weights sum to 1.0, coverage 0.70 reported, score still produced, other components' effective weights rise |
| Compatibility & scope | no-monthly-series fallback; T5 untouched; v1 weights and 60/40 thresholds unchanged; rejected constants gone; determinism |

GUF, WYS, GST, KLW, BGM and BLD are all present as regression fixtures with
their real monthly values.

### Full suite

```
435 passed in 13.86s
```

Run on 2026-09-29 with the project's mocked LLM clients (`conftest.py` replaces
the Anthropic client unless a test is marked `live`; `pytest.ini` adds
`-m "not live"`). **No live API calls.** 0 failures, 0 skips.

---

## 9. Files changed

| file | change |
|---|---|
| `backend/app/analytics/metrics.py` | **production** — `_yoy_passenger_totals()`, `yoy_windows_aligned`, `yoy_months_matched`; `pax_growth` made alignment-aware; the two Phase 8.1b guard properties removed |
| `backend/app/analytics/definitions.py` | **production** — rule documented in full with the rejected alternatives; T2 note describes the like-for-like behaviour; the two month-count constants removed |
| `backend/evaluate_yoy_comparability.py` | new — offline evidence harness (4 sections) |
| `backend/tests/test_yoy_comparability.py` | new — 19 tests |
| `backend/tests/test_tdpi_v2c.py` | superseded 8.1b guard tests removed, with a pointer to the new file |
| `docs/scoring-proposal.md` | T2 formula restated; new boxed subsection; §8 rule 7 and the cohort-normalisation note |
| `docs/design-document.md` | §5 T2 paragraph; §12 deferred formula research |
| `docs/phase-8.1-decision-record.md` | new — why v2 and v2c were rejected |
| `docs/phase-8.1c-tdpi-decision-and-comparability.md` | new — this document |

**Not changed:** v1 weights, ACI, UDEI, `DIVERGENCE_HI`/`DIVERGENCE_LO`, the
analysis window, `score_airport()`, `_compose()`, any API contract, the
frontend, the agent.

`frontend/vite.config.ts` also shows as modified in `git status`: that is your
own dev-server proxy port change (8001), untouched by this phase.

---

## 10. Limitations and remaining uncertainty

1. **One window, one warehouse.** Every figure is measured on
   2025-05..2026-04. Only 6 of 399 airports have any coverage irregularity, so
   the rule's behaviour on a warehouse with many partial-year reporters is
   untested.
2. **The rule is stricter than strictly necessary for GST and KLW.** Their
   matched-month ratios (−0.4%, −2.3%) would have been usable. Keeping them
   while still excluding GUF would require a coverage threshold, which is the
   arbitrary parameter the brief ruled out — so the parameter-free rule was
   preferred and its cost is stated here rather than hidden.
3. **GUF still scores 60.6 and ranks 35th** on 58,777 passengers after losing
   T2. The rule removes a fabricated growth figure; it does not claim to make
   the rest of GUF's score correct, and I have not investigated what else
   elevates it.
4. **The 0.22-point cohort-wide shift is unavoidable, not eliminated.** It
   follows from cohort-relative normalisation.
5. **No score here measures terminal capacity.** TDPI remains a demand-pressure
   proxy built from traffic data. A high score is not evidence that an airport
   requires terminal expansion, and this change does not alter that.

---

## 11. Review checklist

- [x] Smallest necessary production change — one property's semantics; no change to `_compose()` or any weight
- [x] v1 weights and unrelated scoring behaviour preserved (ACI bit-identical)
- [x] Tests for matching months, missing months, valid seasonal windows, suppression/renormalisation
- [x] Full offline suite run — 435 passed
- [x] Scoring documentation and design document updated
- [x] Phase 8.1 decision record added
- [x] Experimental code kept out of the production path
- [x] No external API calls or downloads
- [x] **Not committed, not pushed** — awaiting your review of the diff
