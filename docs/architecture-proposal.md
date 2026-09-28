# Architecture Proposal

## 1. Shape of the system

One orchestrating agent with explicit tools. **No multi-agent topology.** Every question in the assignment decomposes into "resolve airports → call one or two deterministic analytics functions → narrate the result." A planner/researcher/critic crew would add latency, cost, non-determinism and failure modes without changing a single number, and the numbers are the product.

```
┌───────────────────────────────────────────────────────────────────────┐
│  React + TypeScript                                                   │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────────┐ ┌──────────────┐  │
│  │ Chat stream │ │ Score        │ │ Comparison    │ │ Source &     │  │
│  │ (SSE)       │ │ breakdown    │ │ table / chart │ │ freshness    │  │
│  │ + Web Speech│ │ (components) │ │               │ │ panel        │  │
│  └─────────────┘ └──────────────┘ └───────────────┘ └──────────────┘  │
└────────────────────────────────┬──────────────────────────────────────┘
                                 │ POST /chat (SSE), GET /airports
┌────────────────────────────────▼──────────────────────────────────────┐
│  FastAPI                                                              │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │ Orchestrator — Claude + tool calling                            │  │
│  │  · system prompt: no arithmetic, no unsourced numerals          │  │
│  │  · tool loop (max 5 hops)                                       │  │
│  │  · session memory + entity resolution for follow-ups            │  │
│  └───────────────────────────────┬─────────────────────────────────┘  │
│                                  │                                    │
│  ┌───────────────────────────────▼─────────────────────────────────┐  │
│  │ DETERMINISTIC ANALYTICS  (pure Python — no LLM, unit-tested)    │  │
│  │  resolve_airports · get_airport_profile · compare_airports      │  │
│  │  rank_airports · long_haul_breakdown · unmet_demand_evidence    │  │
│  └───────────────────────────────┬─────────────────────────────────┘  │
│  ┌───────────────────────────────▼─────────────────────────────────┐  │
│  │ NUMERIC AUDIT — every numeral in the draft must trace to a      │  │
│  │ tool return, else regenerate once, then template fallback       │  │
│  └───────────────────────────────┬─────────────────────────────────┘  │
└──────────────────────────────────┼────────────────────────────────────┘
                                   │ read-only, sub-ms
┌──────────────────────────────────▼────────────────────────────────────┐
│  SQLite warehouse  (built offline, committed to repo)                 │
│  airports · airport_month · airport_delay_month · segments            │
│  enplanements · runways · source_registry                             │
└──────────────────────────────────▲────────────────────────────────────┘
                                   │ OFFLINE ETL — never on request path
      BTS Socrata r495-tyji · BTS OTP zips · T-100 Segment form ·
      FAA enplanements xlsx · OurAirports csv · Consumer Airfare
```

## 2. The decision that matters most: ETL is offline, always

**No external call happens during a user request.** Measured: one monthly OTP zip took **453 seconds** (31.5 MB at ~71 KB/s). A request-time fetch would blow any reasonable timeout, and the TranStats T-100 form is a screen-scrape that can break at any moment.

So the ETL is a set of scripts run ahead of time, producing a **SQLite file committed to the repository**. This is legally clean — every primary source is `USGOV_WORKS` public domain or an explicit public-domain dedication.

This single choice delivers three things at once:
- **Latency:** every tool call is a local indexed read, sub-millisecond.
- **Determinism:** the same question returns the same numbers during the demo as during development.
- **The fallback:** if every upstream API is down during review, the app is unaffected. The fallback isn't a contingency plan — it's the default operating mode.

`source_registry` stores `dataset, source_url, retrieved_at, coverage_start, coverage_end, license` per table, so citations and the freshness panel are **generated from data**, not hand-maintained strings that rot.

## 3. Data model

```sql
airports(iata PK, icao, faa_locid, name, city, state, region,
         hub_class, service_level, lat, lon, runway_count, longest_runway_ft)

airport_month(iata, month, departures, passengers, seats, load_factor,
              seats_per_dep, avg_distance_sm,
              dom_departures, dom_passengers, dom_seats,
              intl_out_departures, intl_out_passengers, intl_out_seats,
              freight_lbs, mail_lbs,  PRIMARY KEY(iata, month))

airport_delay_month(iata, month, flights, cancelled, diverted,
                    dep_del15, taxi_out_sum, taxi_out_n,
                    nas_delay_sum, dep_delay_sum, dep_delay_n,
                    PRIMARY KEY(iata, month))

segments(origin, dest, month, distance_sm, departures_performed,
         seats, passengers, origin_country, dest_country, carrier_group)

enplanements(locid, cy, enplanements, pct_change, hub_class, preliminary)

scores_cache(iata, window, tdpi, aci, class, coverage, components_json)
```

`airport_delay_month` stores **sums and counts, not averages**, so any window can be re-aggregated correctly. Averaging monthly averages is a real and common bug here — BTV's 594-flight month would otherwise carry the same weight as LAX's 17,454.

**The crosswalk is a real task, not a formality.** OTP uses IATA-style codes, T-100 carries `ORIGIN` + `ORIGIN_AIRPORT_ID`, FAA uses `Locid`, OurAirports uses ICAO `ident` + `iata_code`. ICAO prefixes diverge outside the lower 48 (**PANC**, not KANC). Build the crosswalk from OurAirports, then assert on ANC/HNL/SJU/OGG plus all six New England airports.

## 4. Tool surface

Six tools. Small, orthogonal, each returning `sources[]` and `window`.

| Tool | Input | Returns |
|---|---|---|
| `resolve_airports` | free text ("LA", "New England", "Santa Ana") | resolved IATA list + interpretation note + ambiguity flag |
| `get_airport_profile` | iata, window | traffic, delay, runways, TDPI/ACI/class with components |
| `compare_airports` | iata[], window, dimension | aligned metric table + per-metric winner + volume-vs-intensity split |
| `rank_airports` | region/state/hub_class, window, index | ranked cohort with scores, classes, coverage |
| `long_haul_breakdown` | iata, window, carrier_scope | distance histogram + **sensitivity table across all thresholds** |
| `unmet_demand_evidence` | iata, window | 5 indicators, triggers, band, mandatory caveat string |

**`resolve_airports` earns its place.** "Compare LA and Santa Ana" contains two traps: "LA" may mean LAX alone or the LA metro (LAX+BUR+LGB+ONT+SNA), and "Santa Ana" is SNA, whose name contains neither. Resolution is where a naive build silently answers a different question than the one asked. The tool returns its interpretation so the agent can state it: *"Reading 'LA' as LAX specifically — say the word if you want the full LA basin."*

**Ambiguity surfaces rather than resolving silently.** If `resolve_airports` returns `ambiguous: true`, the system prompt requires the agent to state its assumption in the answer.

## 5. LLM usage — exactly three jobs

AI is used for **intent, narration, and dialogue**, never for computation:

1. **Intent → tool calls.** Mapping "which New England airports should we expand" to `rank_airports(region="new_england", index="tdpi")`.
2. **Narration.** Turning a score object into an explanation that names the drivers and the caveats.
3. **Follow-up handling.** Resolving "what about the second one?" or "why?" against session state.

Claude with tool calling (`claude-sonnet-5` for the loop; `claude-opus-5` available if reasoning quality on ambiguous questions warrants it). Streamed over SSE so the UI feels responsive while tools run.

**Guardrails, in order of reliability (weakest first):**
- System prompt: never compute, never state a figure absent from a tool result, always state the window, always surface assumptions.
- Tool schemas shaped to prevent misuse — `long_haul_breakdown` returns a sensitivity table with no scalar field, `unmet_demand_evidence` has no numeric magnitude field. **The model cannot report a single long-haul number because no such field exists.** Schema design is a stronger control than instruction.
- Numeric audit post-generation (`scoring-proposal.md` §10).

## 6. Conversation memory

Per-session, server-side, in-process dict (Redis is unnecessary at this scale and adds a dependency to the demo):

```python
Session:
  history: list[Message]          # trimmed to last ~12 turns
  focus_airports: list[str]       # last resolved set — drives "the second one"
  last_ranking: list[str]         # ordered, for ordinal references
  window: str                     # pinned so follow-ups stay comparable
  assumptions: list[str]          # surfaced and re-surfaced on request
```

`focus_airports` and `last_ranking` are injected into the system prompt each turn. This is what makes "and why?" or "compare that to Boston" work without re-asking — the assignment explicitly grades conversational follow-up, so this is a scored feature, not polish.

## 7. Frontend

React + TypeScript + Vite. Four panes: chat stream, score breakdown (every component with raw value, normalised value, weight, source), comparison table, and a source/freshness panel showing each dataset's coverage window and `retrieved_at`.

**Voice (the stated bonus)** via the browser-native Web Speech API — `SpeechRecognition` for input, `speechSynthesis` for output. Zero dependencies, zero cost, no additional API keys. Chromium-only, degraded gracefully with a visible note. Roughly an hour of work for an explicitly flagged bonus.

**The score breakdown pane is the real differentiator.** It renders the arithmetic — component, raw, normalised, weight, contribution, source — beside the narrative. That is the visible proof that the ranking is deterministic and not model output, which is exactly what the brief asks to see.

## 8. Error handling

| Failure | Behaviour |
|---|---|
| Airport not in warehouse | `resolve_airports` returns `not_found` + nearest matches; agent asks |
| Metric missing | component dropped, weights renormalised, coverage reported |
| Coverage < 0.60 | `score: null`, `reason: insufficient_coverage`, missing list |
| OTP flights < 1,000 | ACI `null`, `reason: insufficient_flight_volume`; class → `UNCLASSIFIED_AIRSIDE_UNKNOWN` |
| Numeric audit fails | one regeneration, then templated answer from tool output |
| Tool exception | structured error to model; agent states what it couldn't compute |
| Tool loop > 5 hops | halt, answer from what was gathered, say what's missing |
| LLM API down | analytics endpoints still serve; UI shows tables without narration |

The consistent principle: **degrade to a narrower true answer, never to a wider guess.**

## 9. Repository layout

```
backend/
  app/main.py              FastAPI, SSE
  app/agent/               orchestrator, prompts, tool registry, numeric audit
  app/analytics/           scoring.py, normalize.py, longhaul.py, unmet.py
  app/data/                warehouse.db  ← committed
  etl/                     fetch_t100_socrata.py, fetch_otp.py,
                           fetch_t100_segment.py, fetch_faa_enplanements.py,
                           fetch_ourairports.py, build_warehouse.py
  tests/                   test_scoring.py, test_longhaul.py, test_acceptance.py
frontend/
  src/components/          Chat, ScoreBreakdown, ComparisonTable, SourcePanel, Voice
docs/                      this research set + final design doc
```

ETL scripts are committed and runnable, so the pipeline is reviewable even though the DB ships pre-built — a reviewer can see exactly how each number was obtained.

## 10. Tradeoffs, stated plainly

| Decision | Chosen | Given up | Why |
|---|---|---|---|
| Offline ETL, committed DB | ✔ | Live freshness | 453 s/file measured; demo reliability outweighs recency on data already 2–5 months lagged |
| Single agent + tools | ✔ | Multi-agent sophistication | No number would change; adds latency and failure modes |
| SQLite | ✔ | Postgres/DuckDB | Zero-install, single file, sufficient at this volume |
| Two indices + divergence | ✔ | One tidy leaderboard | A single score hides whether terminal capital is even the right instrument |
| Percentile normalisation | ✔ | Absolute thresholds | Robust to outliers, self-explanatory; cost is cohort-relativity (documented) |
| Web Speech API | ✔ | Whisper/TTS quality | Bonus feature; zero cost and zero keys |
| Numeric audit | ✔ | ~2 h build time | Turns "deterministic, not LLM output" from claim into demonstrable property |
| No forecasting | ✔ | Forward-looking scores | FAA TAF download is down (2026-09-27); trailing growth only, stated |
