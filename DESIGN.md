# Design Document — Airport Investment Intelligence Agent

Technical design for the Deloitte Digital FDE exercise. Scoring methodology, key
tradeoffs, and where AI is used.

## 1. Overview and architecture

A conversational screening tool for US airport modernisation investment. An analyst asks in
natural language — "which New England airports are candidates for terminal expansion?",
"compare LA and Santa Ana congestion" — and gets a ranked, explained answer with every figure
traceable to a deterministic calculation.

The organising principle is a hard split: **the LLM decides what to do and how to explain it;
deterministic code decides the numbers.** Claude interprets the question, picks tools, handles
follow-ups and writes the explanation, but never computes a figure. Every score, rate and
classification comes from pure Python over a fixed dataset, which is what makes each number
reproducible and auditable.

It is a **screening tool, not an ROI or profitability model**: construction cost, financing
terms and concession revenue are not public, so the honest deliverable shortlists airports on
measurable demand pressure and delay outcomes and leaves the capital judgement to the analyst.

```
React + TypeScript      chat pane (model narration) │ analytics pane
        │ POST /chat                                ▲ AgentReply JSON
        ▼                                           │
FastAPI
  └─ Orchestrator — Claude Sonnet 5, manual tool loop (≤ 5 hops)
       ├─► 6 deterministic tools ──┐ compact view to model, full payload out
       └─► numeric provenance audit ┘
                  │
       Analytics engine — pure Python, no LLM, unit-tested
                  │ read-only, sub-millisecond
       SQLite warehouse ◄── offline ETL ◄── BTS · FAA · OurAirports
```

One consequence is worth naming: **the analytics pane renders from the structured response,
never from the model's prose**. It reads the engine's own JSON, so a chart cannot disagree with
the engine and no displayed number has passed through the model.

**Data and the pinned warehouse.** Five public sources, all US federal public domain or
explicitly dedicated to it: BTS T-100 Segment Summary (traffic), BTS On-Time Performance
(delay), BTS T-100 Segment (per-route distance), FAA Passenger Boarding (enplanements, hub
class) and OurAirports (runways, crosswalk). The **analysis window is pinned to 2025-05 …
2026-04** across 401 airports, because the sources publish on different schedules and
truncating them to one window is what stops an answer mixing vintages. The ETL runs offline and
the built SQLite warehouse is committed, so every tool call is a local read and the same
question returns the same numbers on any machine.

## 2. Scoring methodology

Two indices, not one. A single "investment score" would average airside congestion against
terminal demand pressure, and those point at *different capital projects* — so the system
computes two independent proxies and treats their **divergence** as the analytical output.

Both are normalised by **winsorized min-max against a peer cohort** of US primary
commercial service airports: values are clipped to the cohort's 5th–95th percentile, then
rescaled 0–100 across that range. Clipping matters because the FAA growth column contains
six-figure percentages from tiny airports starting near zero service, which plain min-max
would let compress everything else to nothing. **A normalised value is not a percentile** —
94 means "94% of the way from the cohort's P5 to its P95", not "higher than 94% of peers";
the true percentile is reported alongside and never used in the arithmetic.

Missing components are never imputed: the component drops out, the remaining weights
renormalise, coverage is reported, and below 0.60 coverage no score is produced at all.

### TDPI — Terminal Demand Pressure Index

A composite proxy for how hard an airport's passenger-handling is being pushed relative to
peers. It exists because the terminal-side question has no direct public measurement, so the
best available answer combines the observable signals that a terminal is under load.

| # | Component | Weight | Rationale |
|---|---|---:|---|
| T1 | Load factor | 0.20 | Level of fill; saturating, so not dominant |
| T2 | Passenger growth YoY (like-for-like months) | 0.30 | Investment follows the trend, not the level |
| T3 | Gauge (seats per departure) | 0.15 | Upgauging pushes more passengers through the same footprint; the most terminal-specific signal available |
| T4 | Throughput per runway ⚠ **proxy** | 0.20 | Closest available stand-in for volume against physical scale |
| T5 | Enplanement growth (FAA) | 0.15 | Independent second opinion; down-weighted because CY2025 is preliminary |

**What it can claim:** that an airport is under more passenger-handling pressure than its
peers, with each component's contribution exposed. **What it cannot:** that the terminal is
full, or that capital is warranted. T4 carries an explicit warning in the code, the API
response and the UI, because runways are a weak stand-in for terminal size. T2 is computed
only across calendar months present in both the current and prior year and dropped when the
windows are not comparable, since an incomparable ratio is missing data rather than a value.

### ACI — Airside Congestion Index

A composite proxy built from observed delay **outcomes**.

| # | Component | Weight | Rationale |
|---|---|---:|---|
| A1 | Average taxi-out | 0.30 | Physical surface queuing; most airport-specific |
| A2 | NAS delay per flight | 0.30 | FAA's own attribution to the National Airspace System |
| A3 | Departures delayed >15 min | 0.25 | Contaminated by schedule padding and upstream late aircraft |
| A4 | Cancellation rate | 0.15 | Weather-driven and lumpy; deliberately down-weighted |

**Suppression rule:** ACI is not computed below **1,000 reported flights** in the window.
Delay rates over a few hundred observations are noise, and a confident score on noise is
worse than no score, so those airports are reported as *unscored with a reason* — never as
low. About 59% of the universe is ACI-eligible; the rest are suppressed, not estimated.

**What it can claim:** that flights here experience more delay than at peer airports.
**What it cannot:** that runway or airspace capacity is the constraint, or that any
constraint is binding. The inputs are outcomes, and an outcome does not identify its cause —
the system says "winter-concentrated pressure", never "caused by winter weather".

### Divergence classification

The two indices are read together, because which one is elevated determines which kind of
capital is even relevant. With thresholds at 60 and 40:

| TDPI | ACI | Class |
|---|---|---|
| ≥60 | <40 | TERMINAL_LED — the profile most consistent with a terminal-side question |
| ≥60 | ≥60 | SYSTEMIC |
| <40 | ≥60 | AIRSIDE_LED |
| <40 | <40 | NO_NEAR_TERM_CASE |
| else | else | MIXED — read both scores, not the label |
| any | suppressed | UNCLASSIFIED_AIRSIDE_UNKNOWN |

These are screening labels, not recommendations. Two rules are test-enforced: a suppressed ACI
**never** falls through to TERMINAL_LED, because that would be an investment signal built on a
gap in the data; and MIXED is the residual class, holding whenever *at least one* index sits in
the intermediate band rather than meaning both are mid-range.

### UDEI — Unmet Demand Evidence

Unmet demand is a counterfactual: passengers who did not book because flights were full leave no
trace in operational data. UDEI therefore produces **qualitative evidence, not a numeric
estimate** — five indicators and an evidence band. The result type has **no magnitude field at
all**, so there is nowhere to put a fabricated number; the schema is the control, not the prompt.

| ID | Indicator | Trigger |
|---|---|---|
| U1 | High load factor | ≥ cohort P75 |
| U2 | Frequency suppression | passenger growth > 0 **and** departure growth ≤ 0 |
| U3 | Upgauging | seats per departure up ≥ 2% YoY |
| U4 | Airside throughput ceiling | ACI ≥ cohort P75 |
| U5 | Fare premium | *unavailable — Consumer Airfare not ingested* |

The band counts triggered indicators: **0–1 Weak, 2–3 Moderate, 4+ Strong**. An indicator
without data is reported **unavailable with a reason**, never assumed false.

Two limitations ship with every result. The band uses absolute counts while the number of
*evaluable* indicators varies — U4 needs a computable ACI and U5 is unavailable everywhere —
so an airport with only three evaluable indicators **cannot reach Strong however strong its
evidence**. Results therefore carry the attainable ceiling alongside the band, and the counts
must be quoted with the label. And U2 and U3 are not independent evidence: passenger growth
decomposes exactly into departures, gauge and load factor, so two of them firing is one
finding viewed twice.

### Long-haul

**Long-haul is a segment great-circle distance of ≥ 3,000 statute miles**, measured as a
share of departures performed. One scope distinction is essential to interpreting it: the
aircraft mix changes the answer completely. At Anchorage 30.1% of all departures are
long-haul, but only **4.1% of passenger-configured** departures are, against **51.2% of
freighters** — so for a passenger-terminal thesis the answer is 4.1%, not 30.1%. The
threshold matters as much, so the tool returns a sensitivity table across thresholds with no
single scalar field, and the model cannot quote a number without its definition.

## 3. Where and how AI is used

**The LLM decides what to do and how to explain it; deterministic code decides the
numbers.**

| Claude is responsible for | Deterministic code is responsible for |
|---|---|
| Understanding user intent | Retrieving and aggregating the data |
| Choosing the appropriate tool | Calculating TDPI, ACI and UDEI |
| Supplying tool arguments | Calculating rankings |
| Conversational follow-ups and context | Calculating comparisons |
| Interpreting the returned evidence | Calculating long-haul statistics |
| Explaining the result in calibrated language | Constructing unmet-demand evidence |
| — | Producing every analytical number |

Six tools, each a thin wrapper over the engine. **None computes an analytical value** —
scores, rates, growth figures and classifications all originate in the analytics layer and
pass through unchanged.

| Tool | Returns |
|---|---|
| `resolve_airports` | IATA codes + interpretation + ambiguity flag |
| `get_airport_profile` | Traffic, delay, both indices, full component derivation |
| `compare_airports` | Volume and per-flight intensity as *separate* blocks |
| `rank_airports` | Ranked cohort with scores, classes, absolute scale |
| `long_haul_breakdown` | Sensitivity table across thresholds and configurations |
| `unmet_demand_evidence` | Indicator table + band, no magnitude field |

Even airport resolution is deterministic: "Compare LA and Santa Ana" is ambiguous ("LA" may
mean LAX or the whole basin), so resolution happens in Python and returns an ambiguity flag the
agent must surface as a stated assumption.

### Guardrails

Three layers keep numbers honest, weakest first.

1. **The system prompt** tells the model never to compute and never to state an unsourced
   figure — useful, but the weakest control.
2. **Schema shape** makes the failure impossible rather than discouraged: no scalar long-haul
   field, no unmet-demand magnitude field, so the model cannot report a quantity the schema
   does not contain.
3. **A numeric provenance audit** checks every numeral in the draft against the numbers the
   engine returned this session, tolerating rounding and natural rescalings, with the pool
   limited to what the model was shown. On failure it makes **one regeneration attempt**,
   framed as an automated check rather than a user correction — told it was the user, the model
   apologises for something the user never said. If the retry also fails, the answer is a
   template rendered from tool output: correctness over prose.

The model view is also compacted, shipping only what is needed to narrate while the full
payload still reaches the frontend and the audit — a two-representation split, not a truncation.

**Provenance is not semantic correctness.** The audit proves a number *exists* in the data; it
cannot prove the number belongs in that sentence. A figure can trace cleanly and still be wrong
for the claim, and a derived value can coincidentally match an unrelated number and pass. That
is why the no-calculation rule is also enforced by schema shape — the audit is the last line,
not the only one.

## 4. Key tradeoffs and limitations

| Decision                              | Why                                                                                            | Limitation / production direction                                      |
| ------------------------------------- | ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Calculations in Python, not the model | Reproducible and auditable; a plausible-looking statistic is worse than none                   | Model cannot answer anything the tools do not expose                   |
| Two proxy indices + divergence        | A single score hides whether terminal capital is even the right instrument                     | Both are cohort-relative proxies, not capacity measurements            |
| Offline ETL, committed warehouse      | Deterministic scoring, reproducible evaluation, stable demos                                   | Data is a pinned snapshot; production would schedule ingestion (below) |
| Single agent, manual tool loop        | No number would change with a multi-agent crew; needed call capture, audit and state injection | More orchestration code owned directly                                 |
| Compact model view                    | Large token reduction with no analytical loss                                                  | Model sees less raw detail than the frontend                           |
| Claude Sonnet 5                       | Measured semantic regression on the cheaper alternative                                        | See model choice below                                                 |
| In-process session store              | Zero external services for a prototype                                                         | Not durable; production needs a shared store (below)                   |

**Proxy metrics versus real capacity.** TDPI and ACI are screening proxies. The system does
not directly measure terminal capacity, gate capacity, runway capacity, or project
ROI/profitability. Public aviation data is good at *realised* quantities — passengers, seats,
departures, taxi-out, delay — because carriers must report them, but it is silent on the
physical plant: no public source I verified publishes gate counts, holdroom area or baggage
throughput, declared capacity rates sit behind a login, and cost and revenue data are
commercial. Observable pressure and outcomes therefore support a defensible shortlist while
capacity, causation and profitability do not. Proxy evidence is also not causal proof — an
elevated index is consistent with a constraint, never a demonstration of one.

**Unmet demand.** Observed public data can show evidence consistent with constrained supply
but cannot quantify latent flights or passengers. UDEI makes that boundary structural rather
than advisory, which is why the result type carries no magnitude field.

**Pinned warehouse versus live data.** Freezing one window buys reproducibility, deterministic
evaluation and stable demos, and makes debugging tractable — a changed number means a changed
calculation, not changed upstream data. For production the direction is to separate ingestion
from serving:

```
public sources → scheduled ingestion → validation →
versioned snapshot → atomic promotion → analytics / agent
```

Cadence follows each source's publication frequency rather than a uniform schedule, and serving
keeps reading the last promoted snapshot so a failed ingest is never what a user queries.

**Session persistence.** Sessions live in an in-process dictionary — held in memory, lost on
restart, visible only to the process that created them — which is the right trade for a
prototype with no external services. Production would put the store behind an interface backed
by Redis or a database, with a TTL and sessions tied to an authenticated user.

**Model choice.** Production runs **Claude Sonnet 5**. I compared **Claude Haiku 4.5** on five
difficult scenarios from the existing evaluation bank, holding prompt, tools, analytics, data,
sessions and audit constant. Haiku was materially cheaper and handled tool selection and
multi-turn context well, but showed meaningful semantic regressions: it misread the UDEI band
mechanism, treating data availability as moving a band threshold rather than the attainable
ceiling, and it derived a percentage against the no-calculation rule. That derived number also
demonstrated that numeric provenance is not semantic validation — the wrong value passed the
audit because it coincidentally matched an unrelated number. **Sonnet was retained** for this
project and this sample; five scenarios do not establish universal model superiority.

**Voice (bonus).** Browser speech-to-text input and text-to-speech playback are implemented
as the assignment's optional bonus, with the transcript taking the ordinary chat path. It is
not a realtime or full-duplex voice agent.
