# Feasibility Matrix — Can we actually answer the four questions?

Date: 2026-09-27. Verdicts are based on calculations I already ran against live data (see `data-source-research.md`), not on expectation.

---

## The measurement honesty ladder

Before the matrix, the distinction the whole project hinges on. These four things get casually merged in aviation conversation, and merging them is how an analyst tool starts lying:

| Layer | What it means | Can we measure it? | From what |
|---|---|---|---|
| **Airside congestion** | Runway + airspace throughput limits | **YES — directly** | Taxi-out, NAS delay, dep>15, cancellations (OTP) |
| **Terminal capacity** | Gates, holdrooms, security, baggage, curb | **NO — no public dataset** | Gate counts, terminal sq ft, checkpoint throughput are not published in any feed we found |
| **Passenger demand (realised)** | People who actually flew | **YES — directly** | FAA enplanements, T-100 passengers |
| **Unmet demand** | People/flights that *didn't* happen | **NO — unobservable** | Only proxy evidence; see §Q4 |
| **Renovation profitability** | ROI on capital | **NO — out of scope** | Requires project cost, financing, concession revenue, AIP/PFC structure. None public. |

**Two consequences we will enforce in the product:**

1. The phrase "terminal expansion candidate" cannot mean "we measured a terminal capacity deficit." It can only mean **"demand pressure on the passenger-handling side is high and rising, while the airside is not the binding constraint."** That is a genuinely useful screen — it is just not the same claim.
2. The assignment's framing goal ("where renovations will be most profitable") is **not answerable from public data**. We will deliver a *screening* tool that shortlists and explains, and say plainly that profitability requires cost and revenue data we do not have. Overclaiming here would be the single easiest way to fail this assignment.

---

## Q1 — "Which airports in New England are strong candidates for terminal expansion?"

**Verdict: ANSWERABLE as a ranked screen, with a named caveat.**

Cohort (verified present in the data): **BOS, BDL, PVD, MHT, PWM, BTV**, plus optional ACK/HYA/RUT/LEB/ORH for completeness. Six primary airports is a clean, defensible New England set (MA, CT, RI, NH, ME, VT).

Measurable inputs, all confirmed live:
- Passengers, seats, departures, load factor, gauge — T-100 `r495-tyji`
- Enplanements + official hub class + YoY % change — FAA CY2025
- Delay/taxi/cancel profile — OTP July 2026
- Runway count/length — OurAirports

What we **cannot** measure: gate counts, terminal square footage, checkpoint wait times, current CIP/master-plan spend. So the output is a **Terminal Demand Pressure ranking** plus an explicit airside cross-check, not a capacity-deficit calculation.

Early signal from data already pulled: PWM (82.8% LF) and BTV (83.0% LF) run *higher* load factors than BOS (81.7%) at a fraction of the scale, while MHT sits at 75.5% — so the New England answer will not simply be "Boston is biggest." That is exactly the kind of non-obvious result that makes the demo land.

**Risk:** small airports have thin OTP coverage (BTV 594 flights, MHT 529 in July 2026) — monthly noise is high. Mitigation: use a rolling 12-month window and suppress delay metrics below a minimum flight count.

---

## Q2 — "Compare LA and Santa Ana airport congestion levels."

**Verdict: FULLY ANSWERABLE, and the honest answer is counter-intuitive.**

Already computed (July 2026):

| | LAX | SNA |
|---|---:|---:|
| Flights | 17,454 | 3,992 |
| Dep >15 min | 25.5% | **26.0%** |
| Avg taxi-out | **18.8 min** | 16.8 min |
| Avg dep delay | 18.8 min | **20.2 min** |
| NAS delay/flight | 4.1 min | **4.2 min** |
| Cancel rate | 1.20% | 1.13% |
| Runways / longest | 4 / 12,894 ft | **2 / 5,700 ft** |

**On a per-flight basis SNA is not less congested than LAX — it is marginally worse on three of five measures.** LAX's congestion advantage shows up in *taxi-out* (18.8 vs 16.8), which is the metric most directly tied to surface/runway queuing at scale.

This forces the product to answer the question properly: **"congestion" must be disambiguated into volume vs. intensity.** LAX has 4.4× the movements; SNA has comparable per-flight delay on two runways with a 5,700 ft primary. Reporting a single "congestion score" that ranks LAX above SNA would be defensible only if we say it is volume-weighted — and would be actively misleading as a statement about the passenger or airline experience.

**Caveat to state in-answer:** SNA's operating ceiling is set substantially by its noise curfew and access agreement (a legal cap, not a physical or ATC one). That fact is **not in any dataset we verified** and must be presented as analyst context, explicitly labelled as outside the data.

---

## Q3 — "What is the percentage of long-haul flights out of Anchorage?"

**Verdict: FULLY ANSWERABLE — already computed — but only if we define terms, and the definition dominates the answer.**

From T-100 Segment (All Carriers), ANC, December 2025, 463 segments / 6,872 departures performed:

| Threshold | Departures | **Share** |
|---|---:|---:|
| ≥ 1,500 sm | 3,620 | **52.7%** |
| ≥ 2,000 sm | 3,477 | **50.6%** |
| ≥ 2,500 sm | 3,213 | **46.8%** |
| ≥ 3,000 sm | 2,355 | **34.3%** |

**The answer swings 18.4 points on threshold choice alone.** A single number without its definition is not an answer; it is a coin flip presented as a fact.

Three definitional choices must be surfaced, because each moves the number:
1. **Threshold** — we propose ≥ 3,000 sm as headline (see `scoring-proposal.md`), always with the sensitivity table.
2. **Numerator/denominator unit** — % of *departures*, of *seats*, or of *passengers*? These differ sharply at ANC because the long sectors are freighters with no passengers at all.
3. **Carrier scope** — all carriers (incl. all-cargo) or passenger carriers only? At ANC this is the difference between describing a transpacific freight hub and describing a regional passenger airport.

ANC's destination mix proves the point: ENA at 59 sm (818 departures) and HKG at 5,081 sm (177 departures) are in the same dataset. **An average distance would describe neither.** We report the distribution.

⚠️ **OTP cannot answer this question** — it is domestic-only and excludes all-cargo carriers. ANC shows 2,513 flights in OTP vs 6,872 in T-100. Using OTP here would silently delete the entire freight operation, which *is* Anchorage. This is the clearest case in the whole assignment where picking the convenient dataset produces a confidently wrong answer.

---

## Q4 — "What is the unmet flight demand in SFO and why?"

**Verdict: ANSWERABLE ONLY AS LABELLED PROXY EVIDENCE. Not directly measurable. We will say so, every time.**

Unmet demand is, by construction, **counterfactual**: passengers who did not book and flights airlines did not schedule leave no record in any dataset. No public API contains it. Any product that returns "SFO has 2.3M passengers of unmet demand" has fabricated it.

What we *can* do is assemble convergent observable indicators and present them as an evidence table with explicit direction — never a single invented quantity.

> ### ⚠ Corrected 2026-09-27 (Phase 2, checkpoint 2)
>
> The table below came from **July 2026 alone**, compared against a
> nine-airport convenience sample. Recomputed over the production window
> (**2025-05…2026-04**) against **all 30 US large hubs**, the picture changes
> materially:
>
> | | Research (Jul 2026, 10-airport sample) | Production (12-month window, 30 large hubs) |
> |---|---|---|
> | Avg taxi-out | 25.0 min — worst of sample | **21.6 min** |
> | Departures >15 min | 35.5% — worst of sample | **20.2%** |
> | Rank among large hubs | implied worst | **18th of 30 (ACI 54.3, mid-table)** |
>
> The genuinely congested large hubs are the northeast/Chicago complex —
> LGA (ACI 95.1), DCA (88.1), EWR (87.2), ORD (86.3), JFK (85.4), PHL (81.1),
> BOS (79.5). **SFO is not among the most congested US large hubs.**
>
> SFO's UDEI band over the production window is consequently **Weak** (1 of 4
> evaluable indicators fires), not the "Strong" the July snapshot suggested.
> The direction the research got right: SFO does still outrank LAX on
> congestion.
>
> This is precisely the error the pinned analysis window exists to prevent,
> and it is why research-stage values are never promoted to results.

SFO's indicators from data already pulled (**July 2026 only — superseded, see above**):

| Indicator | SFO value | Direction | What it is consistent with |
|---|---|---|---|
| Load factor (12 mo) | **82.6%** — highest of the 10 tested | ↑ | Little slack on existing seats |
| Dep >15 min | **35.5%** — worst of the 10 | ↑ | Throughput ceiling being hit |
| Avg taxi-out | **25.0 min** — worst of the 10 | ↑ | Surface/runway queuing |
| NAS delay/flight | 5.3 min | ↑ | ATC-attributed constraint |
| Runways | 4, but **closely-spaced parallels** | ↑ | Known weather-driven capacity collapse |
| Intl dep share | 20.7% | context | Long-haul mix raises gauge/terminal load |

The *why* at SFO also has a well-documented physical cause worth naming as analyst context: its parallel runways are spaced such that low-visibility conditions force single-stream arrivals, cutting arrival capacity sharply. This is not in our datasets; it is domain context and will be labelled as such.

**Additional proxies we can compute** (see `scoring-proposal.md` §Unmet Demand Evidence Index):
- **Frequency suppression** — passenger growth positive while departures flat/declining
- **Upgauging** — seats/departure rising over time (airlines adding seats they cannot add as flights — a classic slot/gate-constrained signature)
- **Fare premium** — Consumer Airfare Report `tfrh-tu9e`, SFO routes vs distance-matched comparators
- **Spill to neighbours** — OAK/SJC growth outpacing SFO on shared markets

Each is reported as an **indicator with a value and an arrow**, aggregated only into a qualitative band (Weak / Moderate / Strong evidence). **No numeric "unmet demand" figure will ever be emitted.**

---

## Summary

| Question | Verdict | Primary source | Main honesty constraint |
|---|---|---|---|
| Q1 New England terminal expansion | **Ranked screen** | T-100 + FAA enplanements | Terminal capacity is unmeasurable; this is demand *pressure* |
| Q2 LAX vs SNA congestion | **Fully answerable** | OTP + runways | Must split volume vs per-flight intensity; SNA legal cap is outside data |
| Q3 ANC long-haul % | **Fully answerable (computed)** | T-100 Segment | Answer depends on threshold/unit/carrier scope — must show sensitivity |
| Q4 SFO unmet demand | **Proxy evidence only** | OTP + T-100 + airfare | Counterfactual; never emit a number |

All four are demonstrable. Two of them (Q3, Q4) are only *honestly* demonstrable if the agent is built to talk about definitions and uncertainty — which happens to be exactly what the assignment says it is grading ("clearly communicate assumption, uncertainty and scoping").
