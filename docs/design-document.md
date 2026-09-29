# Design Document — Airport Investment Intelligence Agent

Technical design for the Deloitte Digital FDE exercise.

---

## 1. Problem and scope

An investment firm backs US airport modernisation and wants to identify airports
where renovation would be most valuable, based on flight and passenger capacity.

**What the brief asks for and what public data supports are not the same
thing.** Profitability requires construction cost, financing terms, concession
revenue, PFC/AIP structure and airline use-and-lease agreements. None are
published. Terminal capacity — gates, holdroom area, checkpoint lanes, baggage
throughput — is likewise absent from every source verified during research.

So the system is scoped as an **investment screening tool**:

| In scope | Out of scope |
|---|---|
| Ranking airports on observable demand pressure | Profitability or ROI |
| Measuring delay outcomes | Terminal capacity measurement |
| Distinguishing terminal-side from airside signals | Diagnosing the cause of congestion |
| Explaining every score's derivation | Forecasting |
| Organising evidence about supply constraint | Quantifying unmet demand |

Being explicit about this is the design, not a disclaimer bolted on. A tool
that silently presented a proxy as a measurement would be worse than useless to
an analyst making a capital decision.

### The measurement honesty ladder

Four things get casually merged in aviation conversation, and merging them is
how a tool starts lying:

| Layer | Measurable here? | From what |
|---|---|---|
| Delay outcomes | **Yes, directly observed** | Taxi-out, NAS delay, delay rate, cancellations |
| Realised passenger demand | **Yes, directly observed** | FAA enplanements, T-100 passengers |
| Airside *capacity* | **No** | Would need declared capacity rates; ASPM is login-walled |
| Terminal capacity | **No** | Not published anywhere we verified |
| Unmet demand | **No — counterfactual** | Proxy indicators only |
| Renovation profitability | **No** | Requires private cost and revenue data |

---

## 2. Architecture and request flow

```
┌──────────────────────────────────────────────────────────────────────┐
│ React + TypeScript                                                   │
│  chat pane              │  analytics pane                            │
│  (model narration)      │  (rendered from structured JSON, never     │
│                         │   from the model's prose)                  │
└───────────┬─────────────────────────────────▲────────────────────────┘
            │ POST /chat                      │ AgentReply JSON
┌───────────▼─────────────────────────────────┴────────────────────────┐
│ FastAPI                                                              │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ Orchestrator — Claude Sonnet 5, manual tool loop (max 5 hops)   │ │
│  │   system prompt (cached prefix) + per-turn session state        │ │
│  └───────────────┬───────────────────────────────┬────────────────┘  │
│                  │ compact tool view             │ full payload      │
│  ┌───────────────▼───────────────┐   ┌───────────▼────────────────┐  │
│  │ 6 deterministic tools          │   │ numeric provenance audit   │  │
│  └───────────────┬───────────────┘   └───────────┬────────────────┘  │
│  ┌───────────────▼──────────────────────────────▼─────────────────┐  │
│  │ Analytics engine — pure Python, no LLM, unit-tested            │  │
│  └───────────────┬────────────────────────────────────────────────┘  │
└──────────────────┼───────────────────────────────────────────────────┘
                   │ read-only, sub-millisecond
        ┌──────────▼──────────┐
        │ SQLite warehouse    │ ◄── offline ETL ◄── BTS · FAA · OurAirports
        └─────────────────────┘
```

**One request, end to end:**

1. `POST /chat` with a message and optional session id.
2. The orchestrator builds the request: a frozen, cached system prefix plus a
   small per-turn block carrying focus airports, the last ranking, the last
   comparison and accumulated assumptions.
3. Claude either answers or requests tools. Tool calls execute against the
   engine — pure local reads, sub-millisecond.
4. Each result is kept in two forms: the **full payload** (to the frontend and
   the audit) and a **compact view** (to the model).
5. Loop until the model stops calling tools, capped at 5 hops.
6. The draft answer goes through the **numeric provenance audit**. A failure
   triggers one regeneration, then a templated fallback.
7. The response carries the answer, every tool call and result, sources,
   limitations, assumptions, scores, audit outcome and token usage.

### Why one agent, not several

Every question here decomposes into *resolve airports → call one or two
deterministic functions → narrate*. A planner/researcher/critic crew would add
latency, cost and non-determinism without changing a single number — and the
numbers are the product.

### Why a manual loop, not the SDK tool runner

The layer needs three things the runner does not expose: every tool call and
result captured for the API response, the numeric audit running over the draft
before it is returned, and per-turn session-state injection. It also keeps the
deliverable off a beta dependency.

---

## 3. Offline ETL and the SQLite warehouse

**No external call happens on the request path.** The warehouse is built ahead
of time and committed to the repository.

This was forced by measurement, not preference: BTS serves the On-Time
Performance archives at **70–100 KB/s** (two independent samples), so a
12-month pull takes 60–90 minutes. The T-100 Segment source is a scraped
ASP.NET form that can break without notice. Fetching either at request time
would blow any reasonable timeout.

Committing the warehouse delivers three things at once:

- **Latency** — every tool call is a local indexed read
- **Determinism** — the same question returns the same numbers in the demo as in
  development
- **Resilience** — if every upstream source is down, the application is
  unaffected. The offline fallback is the default operating mode, not a
  contingency.

Every primary source is U.S. federal public domain (`USGOV_WORKS`) or an
explicit public-domain dedication, so redistributing a cached snapshot is clean.

### Schema

```sql
airports(iata PK, icao, faa_locid, name, city, state, region, hub_class,
         service_level, lat, lon, runway_count, longest_runway_ft, in_universe)

airport_month(iata, month, departures, passengers, seats, freight_lbs, mail_lbs,
              dom_*, intl_out_*, avg_distance_sm, PK(iata, month))

airport_delay_month(iata, month, flights, cancelled, diverted,
                    dep_del15, dep_del15_n, taxi_out_sum, taxi_out_n,
                    nas_delay_sum, nas_delay_n, dep_delay_sum, dep_delay_n,
                    PK(iata, month))

segments(origin, dest, month, carrier_group, aircraft_config, service_class,
         origin_country, dest_country, distance_sm, departures_performed,
         seats, passengers)

enplanements(faa_locid, cy, enplanements, prior_year, pct_change, hub_class,
             service_level, rank, preliminary, PK(faa_locid, cy))

source_registry(dataset PK, source_name, source_url, license,
                coverage_start, coverage_end, retrieved_at, row_count, notes)

etl_validation(check_name PK, status, detail, checked_at)
```

`airport_delay_month` stores **sums and counts, never averages**. Averaging
monthly averages would weight BTV's ~520-flight month equally with LAX's
~17,500 — a real and easy bug, covered by a regression test showing the error is
about 10 minutes wide on taxi-out.

### Traps the pipeline defuses

Each was found during development and carries a regression test.

1. **US territories are not `iso_country == 'US'`.** OurAirports codes Puerto
   Rico as `PR`, Guam as `GU`, USVI as `VI`. Filtering on `'US'` silently
   dropped all 12 FAA-primary territory airports — including **SJU at ~6.7M
   annual enplanements** — with no error raised.
2. **Identifier drift.** Palm Beach International was recoded **PBI → DJT**
   between FAA CY2024 and CY2025, but BTS still reports PBI. A medium hub with
   4.26M enplanements had zero traffic data. Fixed with an explicit alias map
   *and* a `material_dropped_traffic` check that fails the build on any future
   unmapped rename above 500k passengers.
3. **Truncated Socrata field names.** In `r495-tyji`,
   `outbound_international_1/_2/_3/_4` are *passengers*, *pax/flight*,
   *distance/flight* and *distance/passenger* — not variants of one metric.
   Fields are resolved through the dataset's live **display names**.
4. **ICAO prefixes diverge outside the lower 48** — PANC not KANC, TJSJ not
   KSJU. A wrong prefix attaches another airport's runways.
5. **Missing is not zero.** `to_float` returns `None` for blanks and sentinels;
   nothing is imputed anywhere.
6. **faa.gov returns 403** to non-browser user agents.

### Validation

`build_warehouse.py` runs 16 checks and records results in `etl_validation`.
The strongest is `t100_regression_vs_phase1`: five airports' departures,
passengers and seats must match values computed directly from the live API
during research, *before any ETL code existed*. A mismatch means the pipeline is
wrong, not the baseline.

---

## 4. Data sources, coverage and freshness

| Dataset | Source | Access | Coverage |
|---|---|---|---|
| `airport_month` | BTS T-100 Segment Summary by Origin Airport (Socrata `r495-tyji`) | JSON API | 2024-05…2026-04 (24 mo) |
| `airport_delay_month` | BTS On-Time Performance | Bulk ZIP | 2025-05…2026-04 |
| `segments` | BTS T-100 Segment (All Carriers) | Scripted form → ZIP | 2025-05…2026-04 |
| `enplanements` | FAA Passenger Boarding | XLSX | CY2024 final, CY2025 preliminary |
| `airports` | OurAirports + FAA | CSV + XLSX | current |

**Analysis window: 2025-05 … 2026-04.** Pinned in `etl/config.py` and asserted
by tests. Traffic stores 24 months because year-over-year growth needs the prior
year; the *window* is still 12, enforced separately.

**Coverage of the 401-airport universe:** runways 100%, enplanements 100%,
segments 99.5%, T-100 traffic 98.3%, OTP delay 86.8%, ACI-eligible 59.1% (the
rest fall below the volume gate and are suppressed, not estimated).

**Provenance.** `source_registry` records URL, licence, coverage window,
retrieval timestamp and row count per dataset. Citations and the UI freshness
panel are generated from it, so they cannot drift from what was loaded.

---

## 5. Scoring methodology

### Why two indices, not one

A single "investment score" would average airside congestion against terminal
demand pressure — and those point at *different capital projects*. Collapsing
them hides the most decision-relevant fact. So two independent proxy indices are
computed, and their **divergence** is the analytical product.

### Normalisation

Winsorized min-max against a peer cohort (US primary commercial service
airports):

```
x̃ = min(max(x, P5), P95)
n  = 100 · (x̃ − P5) / (P95 − P5)
```

Winsorizing matters: the FAA enplanement-growth column contains a +126,403%
value from a tiny airport starting near-zero service. Plain min-max would
compress every other airport to ~0.

**A normalised value is not a percentile.** 94 means "94% of the way from the
cohort's 5th to its 95th percentile", not "higher than 94% of peers". The true
percentile is reported alongside, clearly labelled, and never used in the
arithmetic.

### TDPI — Terminal Demand Pressure Index

A composite proxy for passenger-handling load relative to peers. **Not** a
measurement of terminal capacity.

| # | Component | Weight | Rationale |
|---|---|---:|---|
| T1 | Load factor | 0.20 | Level of fill; saturating, so not dominant |
| T2 | Passenger growth YoY ⚠ **like-for-like months** | **0.30** | Investment follows the trend, not the level |
| T3 | Gauge (seats/departure) | 0.15 | Upgauging = more passengers through the same footprint — the most terminal-specific signal available |
| T4 | Throughput per runway ⚠ **proxy** | 0.20 | Closest available stand-in for volume against physical scale |
| T5 | Enplanement growth (FAA) | 0.15 | Independent second opinion; down-weighted because CY2025 is preliminary |

**T4 carries an explicit warning** in the code, the API response and the UI. It
ignores runway geometry, spacing and weather-dependence, and correlates only
loosely with terminal size. It is never evidence that a terminal is full.

**T2 is compared like-for-like.** The ratio spans only calendar months present in
both the current and prior windows, and it is computed **only when every window
month has a prior-year counterpart**. Otherwise T2 is dropped and the remaining
weights renormalise — an incomparable ratio is treated as missing data, not as a
value.

The rule has no month-count threshold. That matters in both directions: an
absolute floor would discard WYS (Yellowstone), a genuinely seasonal airport
whose 6 window and 6 prior months align exactly; and an equal-*count* rule lets
GST and KLW through, whose 11-vs-11 windows are offset by a month so the ratio
compares December against April. Before this rule, GUF — whose prior year
contains 2 months totalling **seven passengers** — was ranked **3rd of 399** on a
computed growth of **+839,571%**. See §12 and
`phase-8.1c-tdpi-decision-and-comparability.md`.

### ACI — Airside Congestion Index

A composite proxy built from observed delay **outcomes**. The inputs are
measured; the index does not measure runway or airspace capacity and does not
establish that any constraint is binding.

| # | Component | Weight | Rationale |
|---|---|---:|---|
| A1 | Average taxi-out | 0.30 | Physical surface queuing; most airport-specific |
| A2 | NAS delay per flight | 0.30 | FAA's own attribution to the National Airspace System |
| A3 | Departures delayed >15 min | 0.25 | Contaminated by schedule padding and upstream late aircraft |
| A4 | Cancellation rate | 0.15 | Weather-driven and lumpy; deliberately down-weighted |

**Volume gate:** suppressed below 1,000 OTP flights in the window. Delay rates
on a few hundred observations are noise, and a confident score on noise is worse
than no score. Cohort bounds for ACI also exclude sub-gate airports so
small-sample values cannot drag them.

### Divergence classification

Screening classifications — **not recommendations or infrastructure diagnoses**.
With τ_hi = 60, τ_lo = 40:

| TDPI | ACI | Class |
|---|---|---|
| ≥60 | <40 | TERMINAL_LED — the profile most consistent with a terminal-side question |
| ≥60 | ≥60 | SYSTEMIC |
| <40 | ≥60 | AIRSIDE_LED |
| <40 | <40 | NO_NEAR_TERM_CASE |
| else | else | MIXED — read both scores, not the label |
| any | suppressed | UNCLASSIFIED_AIRSIDE_UNKNOWN |

A suppressed ACI **never** falls through to TERMINAL_LED. Absence of a
measurement is not evidence of absence, and the fall-through would be an
investment signal built on a gap. Test-enforced.

### UDEI — Unmet Demand Evidence

Unmet demand cannot be quantified from the datasets this system uses. UDEI
therefore returns a band and an indicator table, and **`UnmetDemandEvidence` has
no magnitude field** — there is nowhere to put a fabricated number. The absence
of the field is the control.

| ID | Indicator | Trigger |
|---|---|---|
| U1 | High load factor | ≥ cohort P75 |
| U2 | Frequency suppression | passenger growth > 0 **and** departure growth ≤ 0 |
| U3 | Upgauging | seats/departure up ≥ 2% YoY |
| U4 | Airside throughput ceiling | ACI ≥ cohort P75 |
| U5 | Fare premium | *unavailable — Consumer Airfare not ingested* |

Band by triggered count: 0–1 Weak, 2–3 Moderate, 4+ Strong. An indicator without
data is reported **unavailable with a reason**, never assumed false.

### Missing data

1. **Never impute.** No mean-filling, carry-forward or regression fill.
2. **Renormalise weights** over present components.
3. **Report coverage** on every score.
4. **Refuse below 0.60 coverage** — `score: null` plus a reason.
5. **Every score carries `components[]`** with raw value, normalised value,
   percentile, weight, contribution and source.

---

## 6. Long-haul and aircraft configuration

**Definition:** long-haul = segment great-circle distance **≥ 3,000 statute
miles**. Unit: share of **departures performed**, not seats or passengers.

The threshold dominates the answer — at ANC the share moves from 48.3% (≥1,500
sm) to 30.1% (≥3,000 sm). So `long_haul_breakdown` returns a **sensitivity
table with no scalar field**: the model physically cannot quote one number
without its definition.

**Aircraft configuration** (T-100 `AIRCRAFT_CONFIG`, BTS Directive No. 125):

| Code | Meaning | ANC, 12 months |
|---|---|---:|
| 1 | Passenger | 38,407 dep (44.0%) |
| 2 | All-cargo | 47,915 dep (54.9%) |
| 3 | **Combi** — passengers *and* freight, one main deck | 888 dep (1.0%) |
| 4 | Amphibious | 0 |

Passenger and all-cargo are **not exhaustive**. An earlier version showed only
those two, and their departures did not sum to the total — an unexplained
residual of 888. Combi aircraft are genuinely both, so they belong in neither
bucket. The result now carries a `reconciliation` block proving the scopes
partition the total exactly, and this is asserted across six airports.

At ANC the scope split is the whole story: **30.1% all-carrier, 4.1%
passenger-only, 51.2% freighter-only**. For a passenger-terminal thesis the
answer is 4.1%, not 30.1%.

---

## 7. Airport resolution and conversational memory

**Resolution is deterministic Python, never the model.** "Compare LA and Santa
Ana" contains two traps: "LA" may mean LAX alone or the whole basin, and "Santa
Ana" is SNA — a code sharing no letters with the words. `resolve_airports`
handles IATA codes, regions, metro aliases, spoken names, states and substring
matches, and reports its own interpretation plus an `ambiguous` flag so the
agent states the assumption out loud.

**Session memory** carries what follow-ups need:

| State | Enables |
|---|---|
| `messages` | Conversation, trimmed to 12 real turns |
| `focus_airports` | "why?", "and Boston?" |
| `last_ranking` | "the second one" |
| `last_comparison` | "add Burbank to that comparison" |
| `assumptions` | "what have you assumed?" |
| `known_numbers` | Audit validation of figures from earlier turns |

Trimming counts **turn boundaries, not messages**. An earlier version used a
message budget assuming two messages per turn; a tool-using turn is four or
more, so a "12-turn" budget kept only about six and a follow-up found its
originating tool result already gone.

---

## 8. Agent tools and orchestration

Six tools, each a thin wrapper over the engine. None computes anything.

| Tool | Returns |
|---|---|
| `resolve_airports` | IATA codes + interpretation + ambiguity flag |
| `get_airport_profile` | Traffic, delay, both indices, full component derivation |
| `compare_airports` | Volume and per-flight intensity as *separate* blocks |
| `rank_airports` | Ranked cohort with scores, classes, absolute scale |
| `long_haul_breakdown` | Sensitivity table across thresholds × configurations |
| `unmet_demand_evidence` | Indicator table + band, no magnitude field |

**Schema design is a stronger control than instruction.** Two schemas are shaped
so the failure mode is impossible rather than discouraged.

### Why the LLM does not calculate

This is the central design decision.

An LLM producing an aviation statistic is producing a plausible-looking number.
It may be right; nothing in the architecture makes it right, and nothing makes
it *checkably* right. For an investment screen, a figure that cannot be audited
is worse than no figure — it carries unearned authority.

So the model's role is narrowly defined: **intent, narration, dialogue**. It
maps a question to tool calls, explains what came back, and handles follow-ups.
Three enforcement layers, weakest first:

1. **System prompt** — never compute, never state an unsourced figure.
2. **Schema shape** — no scalar long-haul field, no unmet-demand magnitude
   field. The model cannot report what the schema does not contain.
3. **Numeric provenance audit** — mechanical, post-generation.

This also satisfies the brief's requirement for *"deterministic scoring logic,
not only LLM output"* — and turns it from a claim into a demonstrable property.

### Numeric provenance audit

Every numeral in a draft answer is checked against the numbers the engine
returned in this session. It is deliberately forgiving where it does not matter
(rounding is allowed; percentages and ratios are matched at natural scalings;
years, small ordinals and list indices are ignored) and strict where it does.

On failure: one regeneration with a correction marked as an automated check
(not a user message, or the model apologises for something the user never
said). If that also fails, a templated answer rendered directly from tool
output — correctness over prose.

The audit fired for real during live testing, catching a fabricated percentile
and a bad figure in two separate runs.

---

## 9. Token optimisation and cost control

A 24-turn measurement harness (`measure_tokens.py`) replays a fixed scenario
and counts input tokens with `count_tokens` — deterministic, so before/after
measures the change rather than model variance.

| | Before | After | Cut |
|---|---:|---:|---:|
| 8-turn harness total | 679,153 | 112,818 | **−83.4%** |
| Tool payload to model | 230,996 chars | 24,441 chars | **−89.4%** |

**How.** Sources and limitations are identical on every tool result, so they
moved into the cached system prefix. Per-tool compact views ship only what the
model needs to reason and narrate — component derivations for the top 3 ranked
airports rather than all 16, note prose dropped (it is already in the prompt),
floats rounded, compact JSON separators.

**The full payload still reaches the frontend and the audit**, so nothing is
lost analytically. This is a two-representation split, not a truncation.

### Cost controls

- **Offline by enforcement.** `tests/conftest.py` replaces the Anthropic client
  constructors during tests; a forgotten mock raises rather than bills.
  `pytest.ini` also deselects `-m "not live"`.
- **Bounded, classified retries.** The SDK's retry loop is disabled. 401/403
  (auth), 402 and credit-exhaustion 429s (billing), and 400/404/422 (malformed)
  are **never retried** — a second attempt cannot succeed and costs another
  request. 408/409/5xx/network retry once.
- **Usage logging** records token counts and model id only — never prompts,
  completions, keys or headers. Input, output, cache-read and cache-write are
  tracked separately because they bill differently. Exposed at `GET /usage`.
- **Gated live scripts.** `smoke_test.py` refuses without `--confirm`.

---

## 10. Security and failure handling

**Credentials** are read from the environment only, loaded from a gitignored
`.env`. `.env.example` holds placeholders. Nothing logs or returns the key;
`describe_credentials()` returns length and a boolean.

**Degradation ladder** — always to a narrower true answer, never a wider guess:

| Failure | Behaviour |
|---|---|
| Airport not found | Nearest matches; the agent asks |
| Metric missing | Component dropped, weights renormalised, coverage reported |
| Coverage < 0.60 | `score: null` + reason |
| OTP flights < 1,000 | ACI suppressed; class becomes UNCLASSIFIED_AIRSIDE_UNKNOWN |
| Numeric audit fails | One regeneration, then templated tool output |
| Tool exception | Structured error to the model; it states what it could not compute |
| Tool loop > 5 hops | Halt, answer from what was gathered, say what is missing |
| LLM unavailable | `/analytics/*` still serve every figure; the UI shows an error and **never fabricates an answer** |

The last row was verified with a deliberately invalid key: the analytics
endpoints returned SFO TDPI 70.8 / ACI 54.3 while `/chat` returned *"The
language model returned an error (401). The analytics engine is unaffected."*

---

## 11. Trade-offs

| Decision | Chosen | Given up | Why |
|---|---|---|---|
| Offline ETL, committed warehouse | ✔ | Live freshness | 70–100 KB/s measured; data already 2–5 months lagged |
| Single agent + tools | ✔ | Multi-agent sophistication | No number would change; adds latency and failure modes |
| SQLite | ✔ | Postgres/DuckDB | Zero-install, single file, sufficient at this volume |
| Two indices + divergence | ✔ | One tidy leaderboard | A single score hides whether terminal capital is even the right instrument |
| Winsorized min-max | ✔ | Pure percentile rank | Preserves magnitude; cost is cohort-relativity, documented |
| Manual tool loop | ✔ | SDK tool runner | Needed call capture, audit and state injection; avoids a beta dependency |
| Numeric audit | ✔ | ~2h build time | Turns "deterministic, not LLM output" into a demonstrable property |
| Compact model view | ✔ | Model sees less detail | 83% fewer tokens; full payload still reaches the frontend and audit |

## 12. Future improvements

- **Ingest BTS Consumer Airfare** to activate UDEI's U5 fare-premium indicator
  (verified accessible, out of scope for the data foundation).
- **FAA Terminal Area Forecast** for forward-looking growth, once the bulk
  download is restored.
- **Curated airside capacity constants** from FAA Airport Capacity Profiles
  (PDF, 2014–2019 vintage) to compute operations against declared capacity —
  would move ACI closer to a genuine utilisation measure.
- **Solve the ATADS parameter contract** for true tower operations counts.
- **Weight sensitivity analysis** — show how rankings shift under alternative
  weightings, making the judgement explicit rather than fixed. Partially done:
  Phases 8.1/8.1b measured this for two candidate formulations (see below).
- **Voice I/O** via the Web Speech API (the brief's stated bonus).

### Deferred: a per-time-period level anchor for TDPI

Phases 8.1 and 8.1b evaluated two alternative TDPI formulations (v2, v2c) and
**rejected both**; v1 is retained. The decision record is
`phase-8.1-decision-record.md`, with the full evidence in
`phase-8.1-tdpi-v2-evaluation.md` and `phase-8.1b-tdpi-v2c-validation.md`.

Further formula research is deferred until suitable data exists, because the two
open problems both need inputs this system does not have:

1. **A level anchor that is actually about the terminal.** Every level term
   available from T-100 is per-*movement* (gauge, load factor, passengers per
   departure) and therefore describes aircraft, not buildings. What is needed is
   a per-*time-period* measure of passenger handling — passengers per gate, per
   processing position, or peak-hour counts. None is present in T-100, FAA
   enplanements or the on-time database.
2. **Collapsing the growth pair.** Any formulation scoring growth magnitude and
   growth consistency separately double-counts: measured Spearman between them
   was +0.876 while they jointly carried 55% of the weight.

There is also **no ground truth** available — no dataset records which airports
actually needed terminal investment — so no formulation, including v1, has been
validated against outcomes. All comparisons to date are internal consistency and
conceptual validity only.
