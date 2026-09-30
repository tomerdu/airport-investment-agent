# Design Document — Airport Investment Intelligence Agent

Technical design for the Deloitte Digital FDE exercise.

## Executive summary

A conversational screening tool for US airport modernisation investment, over a pinned
12-month window of public BTS and FAA data (2025-05..2026-04, 401 airports). **The LLM
decides what to do and how to explain it; deterministic code decides the numbers.**

- **TDPI** (Terminal Demand Pressure Index) — a cohort-relative **proxy** for
  passenger-handling demand pressure. Not a measurement of terminal or gate capacity.
- **ACI** (Airside Congestion Index) — a **proxy** built from observed delay *outcomes*.
  It does not measure runway or airspace capacity and identifies no cause.
- **UDEI** — organises indicators *consistent with* constrained supply. It has no
  magnitude field by design: unmet demand is a counterfactual, never quantified here.
- **Claude** handles intent, tool selection and arguments, conversational context and
  calibrated explanation. It calculates nothing.
- A **screening tool, not an ROI or profitability model** — the cost, financing and
  revenue inputs those need are not public.

The governing tradeoff: analytical reproducibility and calibrated interpretation over
speculative completeness. Where the data cannot support a claim, the system says so
rather than estimating.

## 1. Scope — what public data actually supports

What the brief asks for and what public data supports are not the same thing.
Profitability needs construction cost, financing terms and use-and-lease agreements;
terminal capacity needs gates, holdroom area, checkpoint lanes and baggage throughput.
Neither is published. Four things get casually merged in aviation conversation, and
merging them is how a tool starts lying:

| Layer | Measurable here? | From what |
|---|---|---|
| Delay outcomes | **Yes, directly observed** | Taxi-out, NAS delay, delay rate, cancellations |
| Realised passenger demand | **Yes, directly observed** | FAA enplanements, T-100 passengers |
| Airside *capacity* | **No** | Needs declared capacity rates; ASPM is login-walled |
| Terminal capacity | **No** | Not published anywhere I verified |
| Unmet demand | **No — counterfactual** | Proxy indicators only |
| Renovation profitability | **No** | Requires private cost and revenue data |

So the system ranks airports on observable demand pressure and delay outcomes, separates
terminal-side from airside signals, and organises evidence about supply constraint. It
does not measure capacity, diagnose causes, forecast or quantify unmet demand.

## 2. Architecture

```
React + TypeScript      chat pane (model narration) │ analytics pane
        │ POST /chat                                ▲ AgentReply JSON
        ▼                                           │
FastAPI
  └─ Orchestrator — Claude Sonnet 5, manual tool loop (≤ 5 hops)
       │   cached system prefix + per-turn session state
       ├─► 6 deterministic tools ──┐ compact view to model, full payload out
       └─► numeric provenance audit ┘
                  │
       Analytics engine — pure Python, no LLM, unit-tested
                  │ read-only, sub-millisecond
       SQLite warehouse ◄── offline ETL ◄── BTS · FAA · OurAirports
```

The orchestrator assembles a cached system prefix plus a per-turn block (focus airports,
last ranking, last comparison, assumptions). Claude either answers or calls tools; tool
calls are local reads. Each result is kept in two forms — the **full payload** for the
frontend and the audit, a **compact view** for the model — and the loop runs until the
model stops calling tools, capped at 5 hops. The draft then goes through the numeric
provenance audit, and the response carries the answer plus every tool call and result,
sources, limitations, assumptions, scores, audit outcome and token usage.

**The analytics pane renders from that structured response, never from the model's
prose.** It reads the engine's own JSON — scores, component breakdowns, comparison and
indicator tables — so a chart cannot disagree with the engine, and no displayed number
has passed through the model.

One agent rather than several, because every question decomposes into *resolve airports →
call one or two deterministic functions → narrate*. A manual loop rather than the SDK
runner, because I needed every call captured for the API response, the audit running
before the draft is returned, and per-turn state injection.

## 3. Data and window

Five public sources: BTS T-100 Segment Summary by Origin Airport (Socrata `r495-tyji`)
for traffic; BTS On-Time Performance for delay; BTS T-100 Segment for per-route distance;
FAA Passenger Boarding for enplanements and hub class; OurAirports plus FAA for runways
and the identifier crosswalk. All are US federal public domain or carry an explicit
public-domain dedication.

**Analysis window: 2025-05 … 2026-04**, pinned in `etl/config.py` and asserted by tests.
The sources publish on different schedules, so truncating everything to one window is
what stops an answer silently mixing vintages. Traffic stores 24 months because
year-over-year growth needs the prior year. Coverage runs from 100% (runways,
enplanements) down to 59.1% ACI-eligible, and airports below the volume gate are
**suppressed, not estimated**.

The ETL runs offline and the built warehouse is committed, because the delay archives
take over an hour to pull and the T-100 Segment source is a scraped form that can break
without notice. That buys local-read latency, determinism, and resilience with every
upstream source down.

## 4. Scoring methodology

**Why two indices, not one.** A single "investment score" would average airside
congestion against terminal demand pressure, and those point at *different capital
projects*. So the system computes two independent proxy indices and treats their
**divergence** as the analytical product.

**Normalisation** is winsorized min-max against a peer cohort of US primary commercial
service airports:

```
x̃ = min(max(x, P5), P95)          n = 100 · (x̃ − P5) / (P95 − P5)
```

Winsorizing matters — the FAA enplanement-growth column holds a +126,403% value from a
tiny airport starting near-zero service, and plain min-max would compress everything else
to ~0. **A normalised value is not a percentile**: 94 means "94% of the way from the
cohort's 5th to its 95th percentile", not "higher than 94% of peers". The true percentile
is reported alongside and never used in the arithmetic.

### TDPI — Terminal Demand Pressure Index

A composite proxy for passenger-handling load relative to peers. **Not** a measurement of
terminal capacity.

| # | Component | Weight | Rationale |
|---|---|---:|---|
| T1 | Load factor | 0.20 | Level of fill; saturating, so not dominant |
| T2 | Passenger growth YoY (like-for-like months) | 0.30 | Investment follows the trend, not the level |
| T3 | Gauge (seats/departure) | 0.15 | Upgauging = more passengers through the same footprint; the most terminal-specific signal available |
| T4 | Throughput per runway ⚠ **proxy** | 0.20 | Closest available stand-in for volume against physical scale |
| T5 | Enplanement growth (FAA) | 0.15 | Independent second opinion; down-weighted because CY2025 is preliminary |

**T4 carries an explicit warning** in the code, the API response and the UI: it ignores
runway geometry and weather-dependence, and is never evidence that a terminal is full.
**T2 is compared like-for-like** — only calendar months present in both the current and
prior windows, and only when every window month has a prior-year counterpart; otherwise
T2 is dropped and the weights renormalise, because an incomparable ratio is missing data
rather than a value. Without that rule an airport whose prior year held seven passengers
ranked 3rd of 399 on +839,571% growth.

### ACI — Airside Congestion Index

A composite proxy built from observed delay **outcomes**. The inputs are measured; the
index does not measure runway or airspace capacity and does not establish that any
constraint is binding.

| # | Component | Weight | Rationale |
|---|---|---:|---|
| A1 | Average taxi-out | 0.30 | Physical surface queuing; most airport-specific |
| A2 | NAS delay per flight | 0.30 | FAA's own attribution to the National Airspace System |
| A3 | Departures delayed >15 min | 0.25 | Contaminated by schedule padding and upstream late aircraft |
| A4 | Cancellation rate | 0.15 | Weather-driven and lumpy; deliberately down-weighted |

**Volume gate:** ACI is suppressed below 1,000 OTP flights in the window — delay rates on
a few hundred observations are noise, and a confident score on noise is worse than no
score. Profiles also carry a `temporal` block (monthly values, spread, the drop from
removing the two worst months); it is **diagnostic only**, never seen by the classifier,
and describes timing rather than cause.

### Divergence classification

Screening classifications — **not recommendations or infrastructure diagnoses**. With
τ_hi = 60, τ_lo = 40:

| TDPI | ACI | Class |
|---|---|---|
| ≥60 | <40 | TERMINAL_LED — most consistent with a terminal-side question |
| ≥60 | ≥60 | SYSTEMIC |
| <40 | ≥60 | AIRSIDE_LED |
| <40 | <40 | NO_NEAR_TERM_CASE |
| else | else | MIXED — read both scores, not the label |
| any | suppressed | UNCLASSIFIED_AIRSIDE_UNKNOWN |

A suppressed ACI **never** falls through to TERMINAL_LED: absence of a measurement is not
evidence of absence, and the fall-through would be an investment signal built on a gap.
**MIXED is the residual class**, holding whenever *at least one* index lands in the 40–60
band — it does not mean both scores are mid-range, and BOS is MIXED with TDPI 58.6 and
ACI 79.5. Both rules are test-enforced.

### UDEI — Unmet Demand Evidence

Unmet demand cannot be quantified from these datasets. UDEI returns a band and an
indicator table, and **`UnmetDemandEvidence` has no magnitude field** — there is nowhere
to put a fabricated number. The absence of the field is the control, not the prompt.

| ID | Indicator | Trigger |
|---|---|---|
| U1 | High load factor | ≥ cohort P75 |
| U2 | Frequency suppression | passenger growth > 0 **and** departure growth ≤ 0 |
| U3 | Upgauging | seats/departure up ≥ 2% YoY |
| U4 | Airside throughput ceiling | ACI ≥ cohort P75 |
| U5 | Fare premium | *unavailable — Consumer Airfare not ingested* |

Band by triggered count: **0–1 Weak, 2–3 Moderate, 4+ Strong**. An indicator without data
is reported **unavailable with a reason**, never assumed false, and each ships what a
trigger is *consistent with* and what it *cannot establish* — phrased as consistency,
never causation. Three limitations travel with every result:

- **U2 and U3 are not independent evidence.** Passenger growth decomposes exactly as
  `(1 + pax) = (1 + departures) × (1 + gauge) × (1 + load factor)`, so two of them firing
  is not two independent findings.
- **The band uses absolute counts while the number of evaluable indicators varies.** U4
  needs a computable ACI and U5 is unavailable everywhere, so an airport with three
  evaluable indicators cannot reach Strong however strong its evidence. Results therefore
  carry `available_count`, `max_attainable_triggered` and `max_attainable_band`, and the
  counts must appear with the label.
- **A band is not calibration.** Cohort frequencies (328 Weak, 70 Moderate, 1 Strong of
  399) ship with a note that they are not evidence about any particular airport.

### Long-haul

**Long-haul = segment great-circle distance ≥ 3,000 statute miles**, as a share of
departures performed rather than seats or passengers. The threshold dominates the answer —
at ANC the share moves from 48.3% (≥1,500 sm) to 30.1% (≥3,000 sm) — so
`long_haul_breakdown` returns a **sensitivity table with no scalar field**: the model
cannot quote one number without its definition. Configuration matters as much, because
passenger and all-cargo are **not exhaustive** (T-100 also codes combi and amphibious),
and a `reconciliation` block proves the scopes partition the total. At ANC the split is
the story: **30.1% all-carrier, 4.1% passenger-only, 51.2% freighter-only** — for a
passenger-terminal thesis the answer is 4.1%.

### Missing data

Never impute — no mean-filling, carry-forward or regression fill. Weights renormalise
over present components, coverage is reported on every score, and below 0.60 coverage the
score is `null` with a reason. Every score carries `components[]` with raw value,
normalised value, percentile, weight, contribution and source.

## 5. Where and how AI is used

**The LLM decides what to do and how to explain it; deterministic code decides the
numbers.**

| Claude is responsible for | Deterministic code is responsible for |
|---|---|
| Understanding user intent | Retrieving and aggregating the data |
| Selecting which tools to call | Calculating TDPI, ACI and UDEI |
| Supplying tool arguments | Calculating rankings |
| Maintaining conversational continuity | Calculating comparisons |
| Interpreting the model-visible evidence | Calculating long-haul statistics |
| Explaining results in calibrated language | Constructing unmet-demand evidence |
| — | Producing every analytical number |

Each of the six tools is a thin wrapper over the engine. **None computes an analytical
value** — every score, rate, growth figure and classification originates in
`app/analytics` and passes through unchanged.

| Tool | Returns |
|---|---|
| `resolve_airports` | IATA codes + interpretation + ambiguity flag |
| `get_airport_profile` | Traffic, delay, both indices, full component derivation |
| `compare_airports` | Volume and per-flight intensity as *separate* blocks |
| `rank_airports` | Ranked cohort with scores, classes, absolute scale |
| `long_haul_breakdown` | Sensitivity table across thresholds × configurations |
| `unmet_demand_evidence` | Indicator table + band, no magnitude field |

Resolution is deterministic too: "Compare LA and Santa Ana" holds two traps — "LA" may
mean LAX alone or the whole basin, and "Santa Ana" is SNA — so `resolve_airports` handles
codes, regions, metro aliases and states in Python and reports an `ambiguous` flag, so
the agent states the assumption aloud.

### Why the LLM does not calculate

An LLM producing an aviation statistic is producing a *plausible-looking* number. It may
be right; nothing in the architecture makes it right, and nothing makes it *checkably*
right. For an investment screen a figure that cannot be audited is worse than no figure —
it carries unearned authority. Three enforcement layers, weakest first:

1. **System prompt** — never compute, never state an unsourced figure.
2. **Schema shape** — no scalar long-haul field, no unmet-demand magnitude field. The
   model cannot report what the schema does not contain: stronger than instruction,
   because it makes the failure impossible rather than discouraged.
3. **Numeric provenance audit** — mechanical, post-generation.

### Numeric provenance audit

Every numeral in a draft answer is checked against the numbers the engine returned this
session — forgiving where it does not matter (rounding, natural scalings, years and small
ordinals ignored) and strict where it does. The pool is every number in a remembered
payload **minus the parts the model never saw**. On failure: **one regeneration**, with
the correction framed as an automated check rather than a user message, because phrased as
a user correction the model apologises for something the user never said. If that also
fails, the answer is a template rendered from tool output — correctness over prose.

**What it cannot do.** It establishes provenance, **not semantic correctness** — that a
number *exists* in the data, not that it is the right number for the sentence. §6 has a
measured example.

### Compact model view, session state, caching

Sources and limitations are identical on every tool result, so they live in the cached
prefix rather than riding along with each payload, and per-tool **compact views** ship
only what the model needs to narrate. Measured on a fixed replay harness: input tokens
**679,153 → 112,818 (−83.4%)**, tool payload **230,996 → 24,441 chars (−89.4%)**. The
full payload still reaches the frontend and the audit — a two-representation split, not a
truncation.

**Prompt caching:** the static prefix carries an ephemeral `cache_control` breakpoint and
is byte-identical across every request in a session, including the regeneration retry, so
later turns read it from cache instead of re-paying input rate.

**Session state:** each session holds `messages` (trimmed to 12 real turns),
`focus_airports`, `last_ranking`, `last_comparison`, `assumptions` and `known_numbers`,
injected as a volatile block after the cached prefix. Trimming counts **turn boundaries,
not messages** — a tool-using turn is four or more messages, so a message budget kept
only about six turns and follow-ups lost the tool result they referred to.

**Failure handling** always degrades to a narrower true answer: a missing metric drops
its component and renormalises, sub-gate ACI is suppressed, a tool exception returns a
structured error the model must describe, and the loop halts at 5 hops saying what it
could not finish. **If the LLM is unavailable, `/analytics/*` still serve every figure and
the UI shows an error — it never fabricates an answer.**

## 6. Tradeoffs and limitations

| Decision | Chosen | Given up | Why |
|---|---|---|---|
| Offline ETL, committed warehouse | ✔ | Live freshness | Slow upstream fetches; data already 2–5 months lagged |
| Single agent + tools | ✔ | Multi-agent sophistication | No number would change; adds latency and failure modes |
| SQLite | ✔ | Postgres/DuckDB | Zero-install, single file, sufficient at this volume |
| Two indices + divergence | ✔ | One tidy leaderboard | A single score hides whether terminal capital is even the right instrument |
| Winsorized min-max | ✔ | Pure percentile rank | Preserves magnitude; cost is cohort-relativity, documented |
| Manual tool loop | ✔ | SDK tool runner | Needed call capture, audit and state injection |
| Numeric audit | ✔ | Build time | Turns "deterministic, not LLM output" into a demonstrable property |
| Compact model view | ✔ | Model sees less detail | 83% fewer tokens; full payload still reaches the frontend and audit |
| Claude Sonnet 5 | ✔ | ~2.5× lower cost on Haiku 4.5 | Measured semantic regression where the LLM matters here |
| In-process session store | ✔ | Durability, multi-process | Right for a prototype; production path below |

**What the system does not do**, stated once and enforced throughout: TDPI is not terminal
capacity; ACI is not physical airside capacity and identifies no cause; UDEI does not
quantify unmet demand; nothing here calculates ROI or profitability.

**Proxy metrics vs infrastructure capacity.** Public aviation data is good at *realised*
quantities — passengers, seats, departures, taxi-out, NAS delay, cancellations — because
carriers must report them. It is silent on the physical plant: no public source I verified
publishes gate counts, holdroom area, checkpoint lanes or baggage throughput, and declared
capacity rates sit behind a login. So observable pressure and outcomes support a
defensible shortlist, while capacity, causation and profitability do not — claiming the
stronger version would mean inventing the inputs.

**Deterministic analytics vs LLM flexibility.** Calculation lives in Python because a
number has to be reproducible and auditable. Orchestration and explanation live in the
model because mapping a question onto tool calls, resolving "the second one" three turns
later, and explaining a suppressed index in calibrated prose are exactly what rigid code
is bad at. Giving up model-side arithmetic costs nothing analytically — every figure it
might compute already exists in a tool result — and buys the property that any number in
an answer can be traced.

**Session persistence.** `SessionStore` is a plain in-process dictionary: sessions are
**held in memory, lost on backend restart, and visible only to the process that created
them**. For a prototype that is the right trade, keeping the deliverable to two commands
with no external service. It is explicitly **not the production architecture**, which
would put it behind an interface backed by a durable shared store (Redis or a database),
with a TTL and sessions tied to an authenticated user rather than resting on id secrecy.

**Model choice — Sonnet 5, on measured evidence.** Production runs Claude Sonnet 5. Before
freezing I compared **Claude Haiku 4.5** on five difficult scenarios from the existing
evaluation bank, holding the prompt, tools, analytics, data, sessions and audit constant.
Haiku was materially cheaper and handled tool selection, arguments and three-turn
conversational context correctly. It regressed where the LLM carries weight here: it
misread the UDEI band mechanism, treating data availability as moving a band *threshold*
when what availability changes is the attainable *ceiling*, and **it derived a percentage
against the explicit no-calculation rule**. That figure is also the clearest demonstration
of the audit's limit — the wrong value passed provenance because it coincidentally matched
an unrelated number in the same payload. (Haiku 4.5 also rejects the production
`output_config` effort parameter.) **Retained Sonnet 5** — evidence-based for this project
and this sample. Five scenarios do not establish statistical superiority, general model
quality or production equivalence, and nothing here says Sonnet is universally better or
Haiku generally unreliable. Results: `backend/evaluation/compare_haiku.json`.

## 7. Production direction

**Data freshness.** The pinned snapshot is a deliberate take-home choice: one frozen
window buys deterministic scoring, reproducible evaluation, and debugging where a changed
number means a changed calculation rather than changed upstream data. A production version
would separate ingestion from serving:

```
public sources → scheduled ingestion → validation →
versioned snapshot → atomic promotion → analytics / agent
```

Cadence follows each source's own publication frequency — BTS monthly releases, the FAA
workbook and reference data update on different and much slower schedules, so a uniform
cadence would be wrong. Serving keeps reading the current promoted snapshot, so a failed
or half-finished ingest can never be what a user queries. The operational work that comes
with it: retries, freshness monitoring, schema-drift detection, data-quality validation
before promotion, versioned snapshots for rollback, and atomic promotion. None of it is
implemented here.

**Voice interaction (bonus, shipped).** Implemented on the browser's Web Speech APIs:
**speech-to-text** for asking a question and **text-to-speech** playback of an answer. A
transcript enters the composer for review and then takes the same `POST /chat` path as
typed text, with no voice-specific code path and no audio reaching the backend. It is
deliberately **not** a realtime or full-duplex voice agent and not speech-to-speech — no
continuous listening, no wake word. English (`en-US`) only.

**Analytical next steps.** Ingest BTS Consumer Airfare to activate UDEI's U5 indicator;
curate airside capacity constants from FAA Airport Capacity Profiles, moving ACI closer to
a genuine utilisation measure; publish a weight-sensitivity analysis so the judgement in
the weights is explicit. One caveat on all of it: there is **no ground truth** — no
dataset records which airports actually needed terminal investment, so no formulation
here, including the shipped one, has been validated against outcomes.
