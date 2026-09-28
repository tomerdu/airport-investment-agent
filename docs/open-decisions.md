# Open Decisions & Acceptance Tests

Date: 2026-09-27. Phase 1 (research) complete. **No code written, no dependencies installed.**

---

## Part A — Decisions requiring your approval

### D1. Scope the product as a *screening* tool, not a profitability model ⭐ most important

The assignment's stated goal is *"identify airports where renovations will be most profitable."* **Profitability is not derivable from public data** — it needs construction cost, financing terms, concession revenue, PFC/AIP structure, and airline use-and-lease agreements. None are published.

My recommendation: build a **screening and explanation tool** that ranks airports by measurable demand pressure and congestion, and says explicitly that profitability requires inputs we don't have.

- **Option A (recommended)** — Screening tool, honest about the gap. Directly serves the graded criterion *"clearly communicate assumption, uncertainty and scoping."*
- **Option B** — Add a speculative ROI proxy (e.g. passengers × assumed revenue per passenger). Looks more complete; invents the most important number in the model. I'd advise against it.

**Needs your call.** Everything downstream assumes A.

### D2. Two indices + divergence, or one blended score?

I propose **TDPI (terminal demand pressure)** and **ACI (airside congestion)** kept separate, with the divergence quadrant as the headline output — because an airport constrained on the runway does not need a terminal, and a blended score hides exactly that.

- **Option A (recommended)** — Two indices + divergence class. Analytically honest, and the quadrant is a genuinely good demo artifact.
- **Option B** — Single "Investment Opportunity Score." Simpler leaderboard, easier to present, loses the distinction the assignment implicitly asks about.

### D3. Long-haul threshold

Verified at ANC that the answer swings **52.7% (≥1,500 sm) → 34.3% (≥3,000 sm)**.

- **Option A (recommended)** — Headline **≥ 3,000 sm**, always accompanied by the full sensitivity table. Aligns with common long-haul usage; the table makes the choice auditable.
- **Option B** — Headline ≥ 1,500 sm (ICAO-ish short/long split). Produces a higher, more dramatic number for ANC.

I recommend A, and the tool returns no scalar field at all — only the table — so the number can never be quoted without its definition.

### D4. Carrier scope for ANC / long-haul

ANC is a top-tier **cargo** hub. Including all-cargo carriers is what makes it Anchorage.

- **Option A (recommended)** — All carriers by default, `passenger_carriers_only` flag available, scope always stated in the answer.
- **Option B** — Passenger-only by default, consistent with a passenger-terminal investment thesis.

A is more truthful about the airport; B is more consistent with the investor's question. I lean A **with the agent proactively noting the cargo split**, which turns the tension into an insight rather than a hidden assumption.

### D5. "LA" disambiguation

- **Option A (recommended)** — Default to **LAX**, state the assumption, offer the LA basin (LAX+BUR+LGB+ONT+SNA) as a follow-up.
- **Option B** — Ask a clarifying question before answering.

A demos better and still satisfies "communicate assumptions." B is safer but makes the agent feel sluggish.

### D6. Model choice

`claude-sonnet-5` for the tool loop (fast, cheap, sufficient — the reasoning load is light because analytics are deterministic), with `claude-opus-5` as a config flag if ambiguous-question handling disappoints. **Confirm you have an API key available**, or I'll plan for a local/mock LLM path.

### D7. Analysis window

T-100 ends **2026-04**, OTP ends **2026-07**. I propose pinning everything to **12 months ending 2026-04** so nothing silently mixes vintages.

- **Option A (recommended)** — Single pinned window, stated in every answer.
- **Option B** — Each metric uses its freshest available window, with per-metric labels. Fresher, but cross-metric comparisons stop being strictly like-for-like.

### D8. Airport universe size

~150 airports (large + medium hubs, all New England primary, plus LAX/SNA/SFO/ANC). Larger cohorts give better percentile normalisation but lengthen ETL. Alternative: all ~380 primary airports — T-100 and FAA data are cheap, only OTP aggregation grows, and it's still bounded. **I lean toward all primary airports** for a more defensible cohort; confirm you're happy with the slightly longer build.

---

## Part B — Acceptance tests

### B1. The four assignment questions

**AT-1 — "Which airports in New England are strong candidates for terminal expansion?"**
- Resolves to BOS, BDL, PVD, MHT, PWM, BTV
- Returns a TDPI-ranked list with divergence class per airport
- States the window and that terminal *capacity* is not measured — only demand pressure
- Every score carries components + coverage
- ACI suppressed (`insufficient_flight_volume`) for low-volume airports rather than fabricated
- **Fail if:** a capacity deficit is claimed, or an unsourced number appears

**AT-2 — "Compare LA and Santa Ana airport congestion levels."**
- Resolves "LA"→LAX, "Santa Ana"→SNA; states the LAX interpretation
- Separates **volume** (LAX ≫ SNA) from **per-flight intensity** (comparable, SNA marginally worse on several metrics)
- Surfaces SNA's 2 runways / 5,700 ft as a structural constraint
- **Fail if:** it reports LAX as simply "more congested" without the volume/intensity distinction — that is the trap this question sets

**AT-3 — "What is the percentage of long-haul flights out of Anchorage?"**
- Returns the sensitivity table, not a bare number
- Reproduces the pre-verified values for 2025-12 (≥1,500: **52.7%**; ≥2,000: **50.6%**; ≥2,500: **46.8%**; ≥3,000: **34.3%**)
- States threshold, unit (departures performed), and carrier scope
- Notes the cargo character of the long sectors
- **Fail if:** a single percentage is returned without its definition

**AT-4 — "What is the unmet flight demand in SFO and why?"**
- Returns the five-indicator evidence table + band
- **Emits no numeric unmet-demand quantity**
- Includes the mandatory caveat that unmet demand is not directly observable
- The "why" cites SFO's measured congestion (35.5% dep>15, 25.0 min taxi-out) and labels runway-geometry context as analyst input, not data
- **Fail if:** any number is presented as "unmet demand"

### B2. Follow-up / conversational

- **AT-5** After AT-1: *"Why is the top one ranked first?"* → resolves the ordinal from `last_ranking`, explains via components
- **AT-6** After AT-1: *"What about the second one?"* → correct airport, no re-asking
- **AT-7** After AT-2: *"Now add Burbank."* → extends the comparison, keeps the window
- **AT-8** After AT-3: *"What if we used 1500 miles instead?"* → re-reads the same table, no recomputation drift
- **AT-9** *"What assumptions are you making?"* → returns accumulated session assumptions

### B3. Determinism & anti-fabrication

- **AT-10** Same question twice → byte-identical numbers
- **AT-11** Injected fabricated numeral in a draft → numeric audit catches it; regeneration or template fallback
- **AT-12** Every numeral in an answer traces to a tool return
- **AT-13** Scores recomputed directly from SQLite match the agent's reported values

### B4. Missing data & edge cases

- **AT-14** Airport with no OTP rows → ACI `null` + reason; TDPI still returned; class = `UNCLASSIFIED_AIRSIDE_UNKNOWN`
- **AT-15** Coverage < 0.60 → `score: null`, `reason: insufficient_coverage`, missing components listed
- **AT-16** Unknown airport ("Kalamazoo Intergalactic") → `not_found` + suggestions, no invented profile
- **AT-17** Single-airport cohort → degenerate-distribution flag, no divide-by-zero
- **AT-18** Airport in FAA file but absent from T-100 → partial profile, explicit gaps
- **AT-19** Out-of-scope question ("What's the ROI on a BOS terminal?") → declines with reason, offers the measurable adjacent answer
- **AT-20** Question about a non-US airport → scope boundary stated

### B5. Calculation correctness (unit)

- **AT-21** Load factor = pax/seats, recomputed from raw monthly sums
- **AT-22** Windowed averages computed from **sums ÷ counts**, never mean-of-monthly-means (the BTV-weighted-as-LAX bug)
- **AT-23** Winsorized percentile rank: known vector → known output; P5 = P95 → 50 + flag
- **AT-24** Weight renormalisation sums to 1.0 over present components
- **AT-25** Crosswalk: ANC↔PANC, HNL↔PHNL, SJU↔TJSJ, and all six New England airports
- **AT-26** ETL regression: warehouse reproduces the verified table in `data-source-research.md` §2.1 (LAX 270,853 departures / 82.1% LF, etc.)

---

## Part C — Known unknowns I could not close in Phase 1

1. **ATADS/OPSNET parameter contract** — the page loads anonymously but `opsnet-server-x.asp` returned an empty body to my POST. Timeboxed and dropped; would give true tower operations counts if solved. Listed as a stretch.
2. **FAA TAF** — bulk download is broken on FAA's side as of 2026-09-27. If it returns during the build, forecast-based growth becomes available; I have **not** planned for it.
3. **Socrata rate limits** — no throttling observed anonymously, but the documented behaviour is IP-based limiting. Recommend registering a free app token before the build.
4. **CY2025 enplanements are preliminary** — will be restated; flagged in the UI.
5. **Consumer Airfare (U5)** — endpoint and schema verified, but I have **not** yet run the distance-matched fare-premium calculation. It is the least-proven component of UDEI; if it proves awkward, UDEI degrades to four indicators and renormalises.

---

## Status

**Phase 1 complete. Stopping here as instructed — no application code, no dependencies, no implementation.**

Awaiting your decisions on **D1–D8**, with D1 (screening vs. profitability framing) and D2 (two indices vs. one) being the two that change the shape of the build.
