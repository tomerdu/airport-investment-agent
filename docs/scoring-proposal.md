# Scoring Methodology Proposal

**Design rule that governs everything below: the LLM never produces a number.** All scores are computed in Python from cached tables, returned to the model as structured JSON, and the model's only job is to narrate what the deterministic layer produced. Any numeral in an answer must be traceable to a tool return value.

---

## 0. Why three scores instead of one

A single "investment score" would have to average airside congestion against terminal demand pressure, and those two things point at *different capital projects*. An airport with severe runway queuing and a half-empty terminal does not need a terminal; it needs runway or ATC capacity — which our investor cannot buy. Collapsing them hides the most decision-relevant fact.

So we compute **two independent indices** and use their **divergence** as the actual analytical product:

```
                    Airside Congestion Index (ACI)
                    LOW                    HIGH
                ┌────────────────────┬────────────────────┐
     TDPI  HIGH │  TERMINAL-LED      │  SYSTEMIC          │
                │  Best fit for      │  Constrained both  │
                │  terminal capital  │  sides; large,     │
                │  ← our target      │  multi-year program│
                ├────────────────────┼────────────────────┤
           LOW  │  NO NEAR-TERM      │  AIRSIDE-LED       │
                │  CASE              │  Terminal spend    │
                │                    │  won't fix it      │
                └────────────────────┴────────────────────┘
```

Plus a third, deliberately non-numeric output: the **Unmet Demand Evidence Index (UDEI)**, which returns a band and an evidence table, never a quantity.

---

## 1. Common analysis window

Because sources have different cutoffs (OTP → 2026-07; T-100 → 2026-04), **all scores use one declared window** and every response states it:

> **Analysis window: 2025-05 … 2026-04 (12 months).** T-100-limited. OTP is published through 2026-07 but is **deliberately truncated** to these same 12 calendar months.

Mixing a July delay figure with an April traffic figure without saying so is a silent error, so the window is an explicit field in every tool return, enforced in the ETL (`config.WINDOW_START/WINDOW_END`) and covered by tests.

### 1.1 Research-stage example values are not production results

The Phase 1 research documents contain worked examples computed from **whatever period was convenient for verifying a source**, not from the production window. They exist to prove an endpoint returns real, sane data — they are **not** results.

| Research figure | Period actually used | Production status |
|---|---|---|
| ANC long-haul 52.7% / 50.6% / 46.8% / 34.3% | **December 2025 only** | ⚠ **single month** — must be recomputed over 2025-05…2026-04 |
| LAX/SFO/BOS/SNA/ANC delay metrics (taxi-out, dep>15, cancel %) | **July 2026 only** | ⚠ **single month, outside the window** — must be recomputed |
| LAX/SFO/BOS/SNA/ANC traffic (departures, pax, seats, LF) | 2025-05…2026-04 | ✔ already the production window — used as the ETL regression baseline |

**Rules now enforced in code:**

1. **No research-stage value is reused as a production result** unless recomputed against the final warehouse and window. The traffic figures qualify; the delay and long-haul figures do not.
2. **Every analytics tool response carries `window`** (`"2025-05..2026-04"`) and `source_coverage` per contributing dataset. There is no code path that returns a metric without them.
3. **The long-haul tool states its own data period explicitly** and must never present a single-month result as a 12-month one. Where a month is requested deliberately, the response says `period: "2025-12 (single month)"`.
4. A warehouse test (`test_december_2025_research_figure_is_not_the_window_figure`) asserts the single-month and 12-month ANC shares actually differ, so a regression that silently substitutes one for the other fails the build.

**Measured consequence.** Recomputed over the full window, ANC's long-haul share at ≥3,000 sm is **30.1%**, not the 34.3% the December-only research sample showed — a 4.2-point difference that would have been quietly wrong had the research figure been carried forward.

---

## 2. Cohort and normalisation

**Cohort.** Percentile ranks are computed within a **peer cohort**, not against all 86,000 airports. Default cohort = US **primary commercial service** airports (FAA `S/L = P`), ~380 airports. Optionally narrowed to the FAA hub class (L/M/S/N) of the subject airport, which is the fairer comparison and is available directly from the FAA file.

> **Implementation note (Phase 2).** This section originally called the method
> "winsorized percentile rank" and then glossed a score of 94 as *"worse than
> ~94% of peers."* Those are two different statistics. The **formula below is
> what is implemented** — winsorized min-max — because it preserves magnitude
> (an airport twice as congested scores higher) where a rank preserves only
> order. Under it, 94 means *"94% of the way from the cohort's 5th to its 95th
> percentile."* Because the rank reading is still useful for interpretation,
> every component additionally reports a true `percentile` field, clearly
> labelled and never used in the arithmetic.

**Normalisation = winsorized min-max over the cohort's 5th–95th percentile.** For raw metric $x_i$ over cohort $C$:

$$
\tilde{x}_i = \min\!\big(\max(x_i,\ P_{5}(C)),\ P_{95}(C)\big)
$$

$$
n_i = 100 \cdot \frac{\tilde{x}_i - P_{5}(C)}{P_{95}(C) - P_{5}(C)}
$$

If $P_{95} = P_{5}$, set $n_i = 50$ and flag `degenerate_distribution`.

Rationale: min-max on raw values lets one outlier (ATL, or a single cancelled-out small airport) compress everyone else into a narrow band. Winsorizing at the 5th/95th percentile keeps the scale interpretable and stable month to month. Percentile-based normalisation is also self-documenting — "SFO is at 94 on taxi-out" means "worse than ~94% of its peers," which an analyst can sanity-check.

**Direction.** Metrics where *higher = more pressure* are used as-is. Where lower = more pressure (none currently), $n_i \rightarrow 100 - n_i$. Direction is declared per metric in the config, not inferred.

---

## 3. Index A — Terminal Demand Pressure Index (TDPI)

**What it claims:** passenger-handling load is high and rising relative to peers.
**What it does NOT claim:** that a terminal capacity deficit has been measured. No public dataset gives gates, holdroom area, checkpoint lanes or baggage throughput; we searched and did not find one.

| # | Component | Formula | Source | Weight |
|---|---|---|---|---:|
| T1 | Load factor | $\text{pax}_{12} / \text{seats}_{12}$ | T-100 `r495-tyji` | **0.20** |
| T2 | Passenger growth ⚠ **like-for-like months only** | $\sum_{m \in M}\text{pax}_m \big/ \sum_{m \in M}\text{pax}_{m-12} - 1$, where $M$ = window months with a prior-year counterpart | T-100 | **0.30** |
| T3 | Gauge (seats/departure) | $\text{seats}_{12} / \text{dep}_{12}$ | T-100 | **0.15** |
| T4 | Throughput per runway ⚠ **proxy** | $\text{pax}_{12} / \text{runway count}$ | T-100 + OurAirports | **0.20** |
| T5 | Enplanement YoY | FAA `% Change` (CY25 vs CY24) | FAA enplanements | **0.15** |

$$
\text{TDPI} = \frac{\sum_{k \in A} w_k \cdot n_k}{\sum_{k \in A} w_k}, \qquad A = \{\text{components with data}\}
$$

**Weight reasoning (stated so a reviewer can disagree precisely):**
- **T2 (0.30) is the heaviest** because investment follows the *trend*, not the level. A big flat airport is not an expansion candidate; a mid-size airport growing 9%/yr is.
- **T4 (0.20)** — see the boxed warning below. It is a *proxy for relative airport throughput against physical scale*, nothing more.
- **T3 (0.15)** captures **upgauging**: when airlines put bigger aircraft on the same number of flights, passenger volume rises without a single extra movement — terminal load grows while airside load does not. This is the most *terminal-specific* signal available to us.
- **T1 (0.20)** is level-of-fill; useful but saturating (most US airports now sit 78–85%), so it is not dominant.
- **T5 (0.15)** is a second opinion on growth from an independent official source, deliberately down-weighted because CY2025 is **preliminary**.

---

> ### ⚠ T2 requires comparable year-over-year windows (Phase 8.1c)
>
> **The rule.** T2 is computed only when **every month of the current window has
> a prior-year counterpart**, and the ratio is summed over those matched months
> alone. If any window month is missing from the prior year, T2 is **dropped** —
> the remaining weights renormalise, exactly as for any other missing component,
> and the value is never imputed.
>
> **There is no month-count threshold**, deliberately. Three formulations were
> measured; only the third states the property a ratio actually needs:
>
> | Rule | Verdict |
> |---|---|
> | Absolute floor (prior ≥ 10 of 12 months) | **Rejected.** Discards WYS (Yellowstone), a seasonal airport with 6 window and 6 prior months that align exactly. Its +22% is valid; the floor would have moved it 118 rank places for no reason. |
> | Equal month counts (prior ≥ window − 1) | **Rejected.** Counts do not imply the same months. GST and KLW each report 11 and 11 and pass this rule, yet their window holds 2025-12 while the prior side holds 2025-04 — the ratio compares December against April. |
> | **Matching calendar months** | **Adopted.** Parameter-free, preserves valid seasonal comparisons, and catches misalignment that counts hide. |
>
> **Why complete alignment, rather than scoring whatever overlaps.** GUF reports
> 12 window months against a prior year of 2 months totalling **seven
> passengers**. Restricting the ratio to those two matched months still yields
> **+167,929%**. When the prior year does not cover the airport's operation, no
> ratio against it measures demand, so the component is dropped rather than
> rescaled.
>
> **Scope.** This governs T2 (BTS T-100 monthly passengers). It does **not**
> govern T5, which is FAA annual enplanements — a different source with its own
> coverage characteristics and no monthly series to align.
>
> **Measured effect:** 5 of 399 airports change. GUF falls from rank 3 to 35;
> GST and KLW lose T2 (ranks 373→388, 367→375); BGM and BLD keep a corrected
> ratio. The rule is implemented in `app/analytics/scoring.py` and pinned by
> `backend/tests/test_yoy_comparability.py`.

---

> ### ⚠ T4 is a proxy, not a measurement of terminal congestion
>
> **What T4 is.** Passengers in the window divided by open runway count. It is a
> proxy for **relative airport throughput against physical scale** — a rough
> indication of how much passenger volume an airport handles for its size.
>
> **What T4 is NOT.** It is not a measurement of terminal capacity, terminal
> congestion, gate utilisation, holdroom crowding, checkpoint queuing or
> baggage-system load. **None of those are published in any public dataset we
> verified**, which is precisely why a proxy is being used at all.
>
> **Why runway count is a weak denominator.** It ignores runway geometry,
> spacing, independence and weather-dependence. SFO's four closely-spaced
> parallels do not provide four runways' worth of capacity; DFW's seven are
> not equivalent to SFO's four. Runway count also correlates only loosely with
> *terminal* size, which is the thing T4 is standing in for — an airport can
> add gates without adding runways, and frequently does.
>
> **Consequence for interpretation.** A high T4 means "this airport moves a lot
> of passengers relative to a crude scale measure." It does **not** mean "this
> airport's terminal is full." TDPI as a whole — and T4 in particular — is
> **never** evidence that an airport requires terminal expansion. It is a
> screening signal that an airport merits a closer look by an analyst who can
> obtain the terminal data we cannot.
>
> **Where this warning must also appear:** the final architecture/design
> document, and the application's scoring-breakdown panel next to the T4 row
> (not buried in a footnote or tooltip).

---

---

## 4. Index B — Airside Congestion Index (ACI)

**What it claims:** measured runway/airspace/surface constraint. This one *is* direct measurement.

| # | Component | Formula | Source | Weight |
|---|---|---|---|---:|
| A1 | Avg taxi-out | mean `TaxiOut` | OTP | **0.30** |
| A2 | NAS delay per flight | $\sum$`NASDelay` / non-cancelled flights | OTP | **0.30** |
| A3 | Departure delay >15 min rate | `DepDel15` share | OTP | **0.25** |
| A4 | Cancellation rate | `Cancelled` share | OTP | **0.15** |

$$
\text{ACI} = \frac{\sum_{k \in B} w_k \cdot n_k}{\sum_{k \in B} w_k}
$$

**Weight reasoning:**
- **A1 + A2 carry 0.60 together** because they are the two metrics most specific to *airport/airspace* capacity rather than airline behaviour. Taxi-out is physical surface queuing; `NASDelay` is the FAA's own attribution of delay to the National Airspace System.
- **A3 (0.25)** is the headline passenger-experience metric but is contaminated by airline scheduling padding and upstream late aircraft, so it is not allowed to dominate.
- **A4 (0.15)** is down-weighted because cancellations are heavily weather-driven and lumpy — visible in our own July 2026 pull, where PVD (6.47%) and BOS (5.92%) exceed LAX (1.20%) for reasons that are seasonal, not structural.

**Minimum-volume gate.** ACI is suppressed and returned as `null` with reason `insufficient_flight_volume` when an airport has **< 1,000 OTP flights in the window**. Small New England airports (BTV 594, MHT 529 flights in a single month) produce unstable delay rates, and a confident score on 500 observations would be false precision.

---

## 5. Divergence classification (the actual output)

Both indices on 0–100. With $\tau_{hi}=60$, $\tau_{lo}=40$ (configurable, reported):

| TDPI | ACI | Class | Investment reading |
|---|---|---|---|
| ≥60 | <40 | **TERMINAL-LED** | Demand pressure without airside constraint — best fit for terminal capital |
| ≥60 | ≥60 | **SYSTEMIC** | Both constrained; terminal alone won't unlock capacity |
| <40 | ≥60 | **AIRSIDE-LED** | Runway/ATC bound; terminal spend does not address the constraint |
| <40 | <40 | **NO NEAR-TERM CASE** | Neither side under pressure |
| else | else | **MIXED** | In the middle band; report both numbers, no label |

The agent's job in Q1 is to return the New England cohort ranked by TDPI **with the class attached**, so "strong candidate for terminal expansion" means TERMINAL-LED with high TDPI — not merely "high score."

---

## 6. Unmet Demand Evidence Index (UDEI) — deliberately not a number

**Unmet demand is counterfactual and unobservable.** Passengers who didn't book and flights airlines didn't file leave no trace in any dataset we verified. UDEI therefore returns **a band and an evidence table**, never a quantity, and the tool schema has no numeric "unmet demand" field for the model to latch onto.

Five binary/graded indicators, each with a declared trigger:

| Indicator | Trigger | Reasoning |
|---|---|---|
| **U1 High fill** | LF ≥ cohort $P_{75}$ | Little slack in existing seats |
| **U2 Frequency suppression** | pax growth > 0 **and** departure growth ≤ 0 | Demand absorbed without added flights |
| **U3 Upgauging** | seats/dep up ≥ 2% YoY | Airlines adding seats they cannot add as flights — slot/gate-constrained signature |
| **U4 Throughput ceiling** | ACI ≥ cohort $P_{75}$ | Airport is hitting operational limits |
| **U5 Fare premium** | route fares above distance-matched cohort median (Consumer Airfare `tfrh-tu9e`) | Supply-constrained market pricing |

Band = count of triggered indicators: **0–1 Weak · 2–3 Moderate · 4–5 Strong**.

Every rendering of UDEI carries this sentence, generated by the tool (not the model):

> *These are convergent indicators consistent with constrained supply. They are not a measurement of unmet demand, which cannot be observed in public data.*

**U2 and U3 are the analytically interesting pair** — together they describe an airport absorbing growth through aircraft size rather than frequency, which is what a capacity-constrained airport looks like from the outside.

---

## 7. Long-haul definition (Q3)

Stated explicitly because the answer moves 18 points across thresholds (verified at ANC: 52.7% at ≥1,500 sm vs 34.3% at ≥3,000 sm).

**Primary definition:**

| Band | Great-circle distance |
|---|---|
| Short-haul | < 1,500 statute miles |
| Medium-haul | 1,500 – 2,999 sm |
| **Long-haul** | **≥ 3,000 sm** |
| Ultra-long-haul | ≥ 6,000 sm |

**Unit:** share of **departures performed** (`DEPARTURES_PERFORMED`, T-100 Segment), *not* seats or passengers.
**Carrier scope:** **all carriers including all-cargo** by default, because at ANC excluding freight would delete the airport's defining activity. A `passenger_carriers_only` flag is available and the tool always reports which scope produced the number.

**Mandatory output shape.** The long-haul tool returns the full sensitivity table (1,500 / 2,000 / 2,500 / 3,000 / 6,000), never a bare scalar — so the model physically cannot report one number without its context.

---

## 8. Missing data — never impute

Rules, in order:

1. **Never impute a missing metric.** No mean-filling, no carry-forward, no regression fill. A filled value is indistinguishable from a measured one once it reaches the model.
2. **Renormalise weights over present components** (the $\sum_{k \in A} w_k$ denominator in §3/§4).
3. **Report coverage** on every score:
   $$\text{coverage} = \frac{\sum_{k \in A} w_k}{\sum_{k \in \text{all}} w_k}$$
4. **Refuse below 0.60 coverage** — return `score: null`, `reason: "insufficient_coverage"`, plus the list of missing components. A score built on two of five components is not a score.
5. **Volume gate** (§4) applies independently of coverage.
6. **Every score object carries `components[]`** with each component's raw value, normalised value, weight, and source — so the UI can always show the arithmetic.
7. **A metric that cannot be computed comparably counts as missing.** T2's year-over-year window-comparability rule (§3) is an instance of this: when the prior year does not cover the current window's months, T2 is absent rather than wrong, and rules 1–4 apply to it unchanged. An incomparable ratio is not data.

> **Note on cohort-relative normalisation.** Because scores are normalised
> against cohort percentiles, suppressing a component removes observations from
> that metric's distribution and shifts the winsorization bounds slightly for
> everyone. When the T2 guard was introduced this moved 357 of the other 394
> airports by at most **0.22 TDPI points** (max rank move 3). This is inherent
> to cohort-relative scoring, not a defect — but it means any change to
> component availability has a small cohort-wide footprint that should be
> measured rather than assumed to be zero.

Example return shape:

```json
{
  "airport": "PWM",
  "window": "2025-05..2026-04",
  "tdpi": { "score": 71.4, "coverage": 1.0, "components": [
      {"id":"T1","label":"Load factor","raw":0.828,"normalized":78.2,"weight":0.20,
       "source":"BTS T-100 r495-tyji"}
  ]},
  "aci": { "score": null, "coverage": 1.0,
           "reason": "insufficient_flight_volume", "flights": 812 },
  "class": "UNCLASSIFIED_AIRSIDE_UNKNOWN"
}
```

Note the last line: when ACI is suppressed, the divergence class **must not** silently default to TERMINAL-LED. Absence of a congestion measurement is not evidence of absence of congestion.

---

## 9. Known limitations (to be reproduced verbatim in the deliverable design doc)

1. **Terminal capacity is never measured.** TDPI is demand pressure. No gate, holdroom, checkpoint or baggage data exists in any public feed we verified.
2. **Profitability is not modelled at all.** No construction cost, financing, concession revenue, PFC/AIP structure. The assignment's stated goal ("where renovations will be most profitable") is answerable only as a *screen*, and we say so.
3. **OTP is domestic and reporting-carrier only.** ACI systematically under-observes international and all-cargo operations. At ANC, OTP sees 2,513 flights where T-100 sees 6,872.
4. **No forecasts.** FAA TAF bulk download is out of service as of 2026-09-27, so all growth figures are **trailing**, never projected.
5. **Runway count is a weak proxy for airport physical scale** (T4). SFO's 4 closely-spaced runways are not equivalent to DFW's 7; the metric ignores geometry, spacing and weather-dependence.
6. **Percentile ranks are cohort-relative.** A TDPI of 80 means "high versus US primary airports in this window," not an absolute capacity statement.
7. **Weights are judgement.** They are argued in §3–§4, configurable, and reported with every score — but they are not empirically derived from investment outcomes, because no such labelled dataset exists.
8. **CY2025 enplanements are preliminary** and will be restated.
9. **Legal/regulatory caps are invisible to the data.** SNA's noise curfew and access agreement materially cap its operations and appear in no dataset; such context is surfaced as clearly-labelled analyst input.

---

## 10. Enforcing "the LLM never fabricates numbers"

Three layers, because a system prompt alone is not a control:

1. **Tool-only numerics.** Scores exist solely as tool return values. The system prompt forbids arithmetic and forbids stating any figure not present in a tool result.
2. **Post-generation numeric audit.** A validator extracts every numeral from the draft answer and checks it against the union of numbers in that turn's tool returns (with tolerance for rounding and for years/counts the model may legitimately restate). Unmatched numerals trigger one regeneration, then a fallback to a templated answer rendered directly from tool output.
3. **Provenance in the UI.** Every figure is rendered with its source dataset, coverage window and `retrieved_at`, so a reviewer can audit without reading the transcript.

This is worth building even in 24 hours: "deterministic scoring, not only LLM output" is an explicit grading criterion, and layer 2 is what turns that claim from an assertion into a demonstrable property.
