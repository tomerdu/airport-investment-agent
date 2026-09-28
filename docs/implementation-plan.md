# Implementation Plan — 24 Hours

## Smallest viable scope that still credibly demonstrates the assignment

**In scope (MVP):**
- ~150 US airports: all FAA large + medium hubs, all six New England primary airports, plus the four question airports (LAX, SNA, SFO, ANC) guaranteed present
- 12-month rolling window ending **2026-04** (T-100-limited)
- Two deterministic indices (TDPI, ACI) + divergence class + UDEI evidence band
- Long-haul breakdown with mandatory sensitivity table
- Chat UI with score-breakdown pane, comparison table, source/freshness panel
- Voice I/O via Web Speech API (bonus)
- Numeric audit guardrail
- Acceptance tests for all four questions

**Explicitly out of scope — and the deliverable says so:**
- Forecasting (FAA TAF download is down as of 2026-09-27)
- Profitability / ROI modelling (no public cost or revenue data)
- Terminal capacity measurement (no public gate/holdroom/checkpoint data)
- Live real-time flight tracking
- User accounts, persistence beyond session, deployment infrastructure

---

## ⚠️ The schedule's critical path: OTP download throughput

**Measured twice, on 2026-09-27:**

| Sample | File | Size | Time | Throughput |
|---|---|---:|---:|---:|
| 1 | OTP 2026-07 | 31.5 MB | 453 s | 71 KB/s |
| 2 | OTP 2026-06 | 30.1 MB | 313 s | 99 KB/s |

So **~70–100 KB/s sustained**, i.e. **5–7.5 minutes per month-file** and **60–90 minutes for twelve**, before any parsing. It is a consistent property of the BTS host, not a one-off fluke — which is why it drives the schedule.

**Mitigation, in priority order:**
1. **Kick off the OTP fetch in the very first 15 minutes**, backgrounded, parallel (4 concurrent), and let it run while everything else is built. This alone converts a 90-minute blocker into zero marginal cost.
2. **Stream-parse and discard.** Never hold the 273 MB CSV in memory — read the zip entry as a stream, aggregate to `airport_delay_month` on the fly, keep only the ~150 target airports. Per-file memory stays flat; output per month is a few KB.
3. **Graceful degradation ladder:** 12 months ideal → **6 months acceptable** → **3 months minimum** (still enough for a defensible ACI, with the window stated). The scoring code reads whatever months are present, so a short window is a config value, not a rewrite.
4. T-100 Socrata, FAA enplanements and OurAirports are all fast (seconds). Only OTP is slow. **Do not sequence anything behind it.**

---

## Hour-by-hour

### Phase 0 — Foundations (H0–H2)
- **H0:00** — *First action:* launch backgrounded parallel OTP download for the 12 target months. Nothing else waits on it.
- Repo scaffold: FastAPI backend, Vite/React frontend, pytest.
- `fetch_ourairports.py` + `fetch_faa_enplanements.py` (fast; remember the **browser User-Agent — faa.gov 403s without it**).
- **Build and test the airport crosswalk.** IATA ↔ ICAO ↔ FAA Locid. Assert on ANC/PANC, HNL, SJU, and all six New England airports. *This is the most likely source of silent wrong answers; do it early and test it.*
- **Exit:** `airports` table populated, crosswalk tests green.

### Phase 1 — Data warehouse (H2–H6)
- `fetch_t100_socrata.py` — paginate `r495-tyji`, map columns **via display names** (the `outbound_international_1/_2/_3` trap), populate `airport_month`.
- `fetch_t100_segment.py` — the ASP.NET form flow (harvest `__VIEWSTATE`/`__EVENTVALIDATION` → POST → unzip). Pull 12 months into `segments`. **Cache raw CSVs to disk** so a mid-build form breakage never costs a re-fetch.
- `build_warehouse.py` — assemble SQLite, populate `source_registry` with `retrieved_at` / coverage / license.
- Fold in OTP months as they land.
- **Exit:** `warehouse.db` committed; sanity query reproduces the LAX/SFO/BOS/SNA figures from `data-source-research.md`. *If the numbers don't match the ones already verified there, the ETL is wrong — that table is the regression baseline.*

### Phase 2 — Deterministic analytics (H6–H11) — *the graded core*
- `normalize.py` — winsorized percentile rank, cohort selection, degenerate-distribution handling.
- `scoring.py` — TDPI, ACI, weight renormalisation, coverage, volume gate, divergence class.
- `longhaul.py` — distance histogram + sensitivity table, carrier-scope flag.
- `unmet.py` — five indicators, triggers, band, mandatory caveat string.
- **Unit tests first for the edge cases:** missing components, coverage < 0.60, flights < 1,000, empty cohort, single-airport cohort, degenerate percentiles.
- **Exit:** `long_haul_breakdown("ANC")` reproduces **52.7% / 50.6% / 46.8% / 34.3%**. That is a hard, pre-verified number — it either matches or the pipeline is broken.

### Phase 3 — Agent layer (H11–H16)
- Tool registry: the six tools with strict JSON schemas (`architecture-proposal.md` §4).
- System prompt: no arithmetic; no numeral absent from a tool result; always state window; always surface assumptions; name uncertainty.
- Tool loop, max 5 hops, SSE streaming.
- Session memory: `focus_airports`, `last_ranking`, pinned `window`, `assumptions`.
- **Numeric audit** — extract numerals from draft, match against tool returns, one regeneration, then template fallback.
- **Exit:** all four questions answered end-to-end in the terminal, with citations.

### Phase 4 — Frontend (H16–H21)
- Chat stream (SSE) + markdown rendering.
- **Score breakdown pane** — component / raw / normalised / weight / contribution / source. *Build this before any styling; it is the visible proof of determinism.*
- Comparison table + a simple chart.
- Source & freshness panel driven by `source_registry`.
- Voice I/O (Web Speech API) — timeboxed to 1 hour, dropped without regret if Phase 3 overran.
- **Exit:** all four questions demoable in the browser.

### Phase 5 — Acceptance, docs, buffer (H21–H24)
- Run the full acceptance suite (`open-decisions.md` §Acceptance Tests).
- Write the deliverable design doc: scoring methodology, tradeoffs, where AI is used — reusing §9 limitations from `scoring-proposal.md` **verbatim**.
- README with setup + a "what this does not do" section.
- **Buffer.** Something will overrun.

---

## Fallback ladder

| If this fails | Then |
|---|---|
| OTP download too slow | 6-month, then 3-month window; window is a config value and is stated in every answer |
| TranStats T-100 form breaks | Ship cached segment CSVs (already pulled in research); long-haul still works |
| Socrata `r495-tyji` down | Warehouse is pre-built and committed — no runtime dependency at all |
| faa.gov 403s persist | CY2024 final file, or drop T5/enplanements and renormalise (coverage handles it) |
| LLM API unavailable | Analytics REST endpoints still serve; UI renders tables without narration |
| Frontend overruns | FastAPI + a minimal HTML chat page; the graded core is the scoring layer |

**The structural insight:** because the warehouse is built offline and committed, *every* upstream failure after Phase 1 is survivable. All the real risk is concentrated in the first six hours, which is why ETL starts at minute zero.

---

## Risk register

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| OTP download eats the day | High | **High** | Background at H0; degradation ladder |
| Airport code crosswalk errors | **High — silent wrong answers** | Medium | Build + test in Phase 0; assert on ANC/HNL/SJU/New England |
| T-100 column-name trap (`_1/_2/_3`) | High — wrong metrics, no error | Medium | Map via display names; assert against verified table |
| TranStats form changes mid-build | Medium | Low | Cache raw CSVs on first pull |
| Averaging monthly averages | Medium — subtly wrong | Medium | Store sums+counts, never averages; unit test |
| Scope creep into forecasting/ROI | Medium | Medium | Out-of-scope list is in the deliverable doc |
| Voice eats time | Low | Medium | Hard 1 h timebox, last feature |

---

## Definition of done

- [ ] All four assignment questions answered correctly, with citations and stated windows
- [ ] `long_haul_breakdown("ANC")` returns the pre-verified 52.7 / 50.6 / 46.8 / 34.3 sensitivity table
- [ ] Follow-up questions work against session memory ("why?", "what about the second one?")
- [ ] Missing-data paths return `null` + reason, never a fabricated score
- [ ] Numeric audit demonstrably catches an injected fabrication
- [ ] Score breakdown pane shows the full arithmetic for any score
- [ ] Design doc covers scoring methodology, tradeoffs, where AI is used, and limitations
- [ ] README states plainly what the system does **not** measure
