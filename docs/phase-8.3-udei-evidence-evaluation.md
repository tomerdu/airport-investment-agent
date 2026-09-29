# Phase 8.3 — UDEI Evidence Quality Review

**Status:** audit complete, uncommitted, awaiting review
**Window:** 2025-05 .. 2026-04 (unchanged)
**Cohort:** 399 airports; 237 with a computable ACI
**Production changes:** **none.** No scoring, API contract, prompt or frontend
file was modified. One new read-only script.
**Tests:** 499 passed, 0 failed, 0 skipped — unchanged from commit `f53db35`
**External calls:** none. No downloads, no live LLM calls.

Reproduce with:

```
python evaluate_udei_evidence.py
python evaluate_udei_evidence.py --section coverage|sfo|peers|confounders|claims|band
```

---

## Headline

**Is "how much unmet demand is there at SFO?" quantitatively answerable with this
data? No — and that is a property of the data, not a gap in the implementation.**
The datasets record what was flown and carried. A flight an airline chose not to
schedule and a passenger who never booked leave no row in T-100, the on-time
database or FAA enplanements. There is no denominator from which a magnitude
could be recovered, and the implementation correctly refuses to invent one: the
`UnmetDemandEvidence` class has **no magnitude field**, so there is physically
nowhere to put such a number.

**The more useful finding is about SFO itself.** SFO's evidence band is **Weak**:
only **1 of 4** evaluable indicators fires. Departures grew *faster* than
passengers (+4.2% vs +3.4%) and average gauge *fell* (−0.8%). Its slot- and
gate-constrained peers — JFK, LGA, EWR, ORD — all band **Moderate**. On this
evidence SFO does not look supply-constrained in this window, and the current
answer's job is to say so clearly.

I found **no fabrication risk** in the existing implementation. I did find one
genuine structural defect (the band's ceiling varies with airport size, §6) and
several presentation gaps, including two fields that are computed and then
displayed nowhere.

---

## 1. End-to-end trace

| layer | file | what it does |
|---|---|---|
| Warehouse | `airport_month` (T-100), `airport_delay_month` (OTP), `enplanements` | U1–U3 read T-100 sums; U4 reads OTP via ACI; U5 has no table |
| Metrics | `metrics.py` | `load_factor`, `pax_growth`, `departure_growth`, `gauge_growth`, `seats_per_departure` |
| Indicators | `unmet.py` → `unmet_demand_evidence()` | builds U1–U5, counts triggers, assigns the band |
| Constants | `definitions.py` | `UDEI_COHORT_PERCENTILE = 75.0`, `UDEI_UPGAUGE_THRESHOLD = 0.02`, `UDEI_CAVEAT` |
| Model classes | `models.py` | `Indicator`, `UnmetDemandEvidence` — **no magnitude field** |
| Engine | `engine.py` → `unmet_demand()` | per-airport entry point |
| API | `GET /analytics/unmet-demand/{iata}` | returns the full structure |
| Agent tool | `tools.py` → `unmet_demand_evidence` | compact view keeps every indicator |
| Prompt | `prompts.py` § "Unmet demand" | forbids any magnitude; scopes the claim to *this system's* datasets |
| Frontend | `panels.tsx` → `UnmetDemandPanel` | disclaimer, band badge, indicator list |
| Tests | 17 tests across 4 files | schema, caveat, availability, band, language calibration |
| Docs | `scoring-proposal.md` §6, `design-document.md` §5 | "deliberately not a number" |

The chain is coherent and the anti-fabrication controls are real, not aspirational
(§7).

---

## 2. Indicator-by-indicator evidence table

### U1 — High load factor

| | |
|---|---|
| **Directly measures** | `SUM(passengers) / SUM(seats)` over the 12-month window, from T-100. A seat-weighted annual mean. |
| **May suggest** | Little residual slack in the seats actually offered. |
| **Cannot establish** | That demand exceeded supply. A full aircraft is evidence of a well-matched schedule as much as of an underserved market — airlines *target* high load factors. |
| **Denominator / window** | Seats offered, same 12 months. No prior-year term. |
| **Confounders** | **Seasonality** — an annual mean cannot show whether peaks are full and off-peaks empty, and unmet demand is a *peak* phenomenon. **Gauge** — the same load factor on larger aircraft is a different operational situation. **Revenue management** — the figure is partly a pricing outcome, not a demand ceiling. **Not class-normalised** — see below. |
| **Trigger** | ≥ cohort P75, so it fires for **exactly ~25% of the cohort by construction** (measured: 100/399). |

**The threshold is cohort-wide, and load factor varies by hub class:**

| class | n | min | median | max | fires U1 |
|---|---:|---:|---:|---:|---:|
| L | 30 | 75.1% | 80.7% | 83.2% | **67%** |
| M | 35 | 72.3% | 76.5% | 84.6% | 17% |
| S | 79 | 64.2% | 77.9% | 85.8% | 37% |
| N | 255 | 25.2% | 70.3% | 86.5% | 18% |

Threshold = 79.8%. Large hubs clear it 67% of the time against 25% cohort-wide,
because their load factors sit higher as a class. **This is a cross-class
comparability defect, not a reason to dismiss the value for a given airport** —
a distinction I initially got wrong and corrected on measurement. SFO's 82.6%
ranks **2nd of 30** large hubs, so U1 firing *is* informative for SFO
specifically.

### U2 — Frequency suppression

| | |
|---|---|
| **Directly measures** | Two YoY ratios: passenger growth and departure growth. |
| **May suggest** | Demand absorbed without adding flights. |
| **Cannot establish** | *Why* frequency did not rise. "Could not add flights" and "chose not to" are indistinguishable here. Fleet availability, pilot supply, hub restructuring and profitability all produce the same signature. |
| **Denominator / window** | Prior 12 months vs current 12 months, both T-100. Inherits the Phase 8.1c calendar-month comparability rule. |
| **Confounders** | **Network changes** — a carrier moving a hub shifts both terms. **Fleet strategy** — retiring small jets shrinks departures by design. **Threshold fragility** — the rule is `pax growth > 0 AND dep growth ≤ 0`, so a departure change of +0.1% versus −0.1% flips it. |
| **Trigger** | Fires **44/396 = 11%** cohort-wide, and for **0 of the 11 peers** examined. |

### U3 — Upgauging

| | |
|---|---|
| **Directly measures** | YoY change in `seats / departure`. |
| **May suggest** | Airlines adding seats they are not adding as flights. |
| **Cannot establish** | A slot or gate constraint. Upgauging is also a fleet-renewal artefact: the US narrowbody fleet has been trending larger for a decade independently of any airport's constraints. |
| **Denominator / window** | Departures, prior vs current 12 months. |
| **Confounders** | **Fleet renewal** (industry-wide). **Route mix** — adding long-haul raises gauge with no constraint implied. **Partial overlap with U2** — see §5. |
| **Trigger** | ≥ +2% YoY. Fires **115/399 = 29%**. The 2% figure is a judgement, not derived. |

### U4 — Airside throughput ceiling

| | |
|---|---|
| **Directly measures** | The ACI composite — taxi-out, NAS delay, delay rate, cancellations. |
| **May suggest** | The airport is operating near practical limits. |
| **Cannot establish** | A capacity ceiling. ACI measures *outcomes*, and the Phase 8.2 review showed those outcomes can be winter-concentrated rather than structural. |
| **Denominator / window** | Per-flight rates over the same 12 months; requires ≥ 1,000 OTP flights. |
| **Confounders** | **Weather**, which OTP does not attribute. **Upstream delay** propagating from other airports. **Suppression** — unavailable for **162 of 399** airports. |
| **Trigger** | ≥ cohort P75, again **~25% by construction** (measured 60/237). |

### U5 — Fare premium

| | |
|---|---|
| **Directly measures** | Nothing. **Unavailable for all 399 airports.** |
| **Would suggest** | Supply-constrained market pricing — arguably the most direct of the five, since a fare premium is a market's own statement about scarcity. |
| **Cannot establish** | Anything at present. |
| **Status** | BTS Consumer Airfare Report (Socrata `tfrh-tu9e`) verified accessible but not ingested. Correctly reported as **unavailable with a reason**, never as a non-trigger. |

---

## 3. SFO case study

**Band: Weak — 1 of 4 available indicators fired (5 defined).**

| ind | value | threshold | fired |
|---|---|---|---|
| U1 | 82.6% | ≥ cohort P75 (79.8%) | **YES** |
| U2 | passengers +3.4% / departures +4.2% | pax > 0 AND dep ≤ 0 | no |
| U3 | −0.8% | ≥ +2% YoY | no |
| U4 | ACI 54.3 | ≥ cohort P75 (ACI 57.7) | no |
| U5 | — | fares vs distance-matched cohort | unavailable |

Underlying quantities:

| | |
|---|---:|
| passengers, 12 mo | 26,642,605 |
| passengers, prior 12 mo | 25,777,875 |
| departures, 12 mo | 190,280 |
| departures, prior 12 mo | 182,567 |
| seats, 12 mo | 32,273,396 |
| load factor | 82.6% |
| passenger growth YoY | +3.35% |
| departure growth YoY | **+4.22%** |
| seats per departure | 169.6 |
| gauge growth YoY | **−0.82%** |

**SFO added capacity faster than it added passengers.** Departures rose 4.2%
against 3.4% passenger growth, and average gauge fell slightly. That is the
opposite of the frequency-suppression and upgauging signatures. Whatever
constraints SFO may face — and its runway geometry is genuinely unusual — they did
not manifest as suppressed frequency in this window.

### An identity that matters for §5

Passenger growth decomposes exactly into three multiplicative terms, verified to
1e-9 on SFO's own figures:

```
(1 + pax growth) = (1 + departure growth) x (1 + gauge growth) x (1 + LF growth)
        1.033545 =            1.033545
   departures +4.22%   gauge -0.82%   load factor -0.02%
```

U2 reads the first term, U3 the second, U1 the level of the third. **They are
three views of one decomposition, not three independent observations.**

---

## 4. Peer comparison

Peers chosen before looking at results: slot- or gate-tight large hubs, west-coast
large hubs, and SFO's own Bay Area alternatives.

| apt | passengers | LF | pax G | dep G | gauge G | ACI | fired | band |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| **SFO** | 26,642,605 | 82.6% | +3.4% | +4.2% | −0.8% | 54.3 | **1/4** | **Weak** |
| JFK | 30,556,247 | 82.0% | −2.8% | −2.6% | +0.9% | 85.4 | 2/4 | Moderate |
| LGA | 16,128,777 | 80.8% | −2.5% | −0.9% | 0.0% | 95.1 | 2/4 | Moderate |
| EWR | 23,461,161 | 83.2% | −3.9% | −5.0% | +2.9% | 87.2 | 3/4 | Moderate |
| ORD | 41,990,769 | 81.0% | +7.4% | +11.5% | −1.8% | 86.3 | 2/4 | Moderate |
| LAX | 36,590,656 | 82.1% | −3.3% | −1.5% | −0.2% | 37.9 | 1/4 | Weak |
| SEA | 25,347,779 | 81.8% | −1.1% | −0.5% | +0.9% | 54.0 | 1/4 | Weak |
| SAN | 13,117,490 | 79.9% | +2.4% | +5.4% | −1.3% | 59.7 | 2/4 | Moderate |
| LAS | 26,165,825 | 78.6% | −6.5% | −5.4% | −0.8% | 45.3 | 0/4 | Weak |
| OAK | 4,366,089 | 74.2% | −13.9% | −11.1% | −4.2% | 16.0 | 0/4 | Weak |
| SJC | 5,070,780 | 73.9% | −11.3% | −9.9% | −1.3% | 14.1 | 0/4 | Weak |

Which indicators fire:

| apt | U1 fill | U2 freq | U3 gauge | U4 airside | U5 fare |
|---|---|---|---|---|---|
| SFO | YES | no | no | no | — |
| JFK | YES | no | no | YES | — |
| LGA | YES | no | no | YES | — |
| EWR | YES | no | **YES** | YES | — |
| ORD | YES | no | no | YES | — |
| LAX | YES | no | no | no | — |
| SAN | YES | no | no | YES | — |
| LAS / OAK / SJC | no | no | no | no | — |

Three observations:

1. **SFO bands below every slot-constrained peer.** The difference is entirely
   U4: SFO's ACI of 54.3 sits under the P75 bar of 57.7, while JFK (85.4),
   LGA (95.1), EWR (87.2) and ORD (86.3) clear it comfortably.
2. **U2 fires for none of the eleven.** An indicator that never fires among the
   airports most likely to be supply-constrained is contributing very little.
3. **OAK and SJC are contracting sharply** (−13.9% and −11.3% passengers). If SFO
   were turning demand away, spillover to the Bay Area alternatives would be the
   expected signature, and the opposite is happening. *This is an inference about
   a plausible mechanism, not a measurement* — passenger substitution is not
   observable in these datasets — but it is the kind of context an analyst needs,
   and it points away from a binding constraint at SFO.

---

## 5. Double counting and correlated indicators

| pair | measurement | verdict |
|---|---|---|
| U1 load factor ~ U4 ACI | Spearman **+0.160** (n=237) | **Independent.** Fill and delay outcomes are genuinely different signals. No double counting. |
| U2 ~ U3 | Of airports firing U2, **57%** also fire U3. Both fire 25, U2 only 19, U3 only 88 | **Partial overlap, with a structural cause.** U2 requires passengers up and departures flat or down, which arithmetically forces gauge or load factor up. |
| gauge growth ~ departure growth | Spearman **−0.100** (n=399) | **Weak.** The substitution story U3 relies on is only faintly visible cohort-wide, which undercuts reading upgauging as constraint evidence. |

The identity in §3 is the cleanest statement of the U2/U3 problem: they are two of
the three terms into which passenger growth exactly decomposes. Counting both as
separate evidence toward a band double-weights one underlying fact. The effect is
bounded — the overlap is 57%, not 100% — but it is real and it is in the direction
of overstating convergence.

**U1 and U4 are cohort-quartile flags by construction.** Both trigger at cohort
P75, so each fires for ~25% of its evaluable population by definition (measured:
100/399 and 60/237, i.e. exactly 25% each). A quartile flag states a *relative
position*. It can never state "at capacity", and the band should not be read as if
it did.

---

## 6. The one structural defect: the band's ceiling moves with airport size

The band uses absolute trigger counts (0–1 Weak, 2–3 Moderate, 4+ Strong), but the
number of indicators that *can* fire varies:

| indicators available | airports | best band reachable |
|---:|---:|---|
| 2 | 3 | Moderate |
| 3 | 159 | Moderate |
| 4 | 237 | Strong |

**162 of 399 airports (41%) cannot reach "Strong" whatever their evidence**,
because U4 needs a computable ACI and U5 is never available. Those 162 are exactly
the airports whose ACI is suppressed for low flight volume, so **the ceiling
tracks airport size rather than evidence quality.**

The same arithmetic makes equally complete evidence band differently:

| available | triggered | share of measurable | band |
|---:|---:|---:|---|
| 3 | 3 | **100%** | Moderate |
| 4 | 4 | **100%** | **Strong** |
| 4 | 3 | 75% | Moderate |
| 4 | 2 | 50% | Moderate |

3-of-3 and 4-of-4 are both everything that could be measured, yet band
differently. Cohort-wide the practical consequence is severe: **328 Weak, 70
Moderate, 1 Strong.** A scale whose top value is reached once in 399 is carrying
almost no information.

This is a real defect and it is narrow. It is also **not** a reason to invent new
weights — see §8.

---

## 7. Does the agent ever present a proxy as a measured quantity?

**No. The controls are structural, not just instructional, and they hold.**

Verified directly on the SFO payload:

| control | status |
|---|---|
| No magnitude field anywhere in the payload | confirmed — searched for `unmet_passengers`, `unmet_flights`, `magnitude`, `shortfall`, `missing_passengers`: all absent |
| `UnmetDemandEvidence` cannot hold a number | confirmed in `models.py`; two tests assert it |
| Unevaluable indicator → `triggered: null`, not `false` | confirmed for U5 |
| U5 carries an explicit `unavailable_reason` | confirmed, and it reaches the model |
| `caveat` denying measurement reaches the model | confirmed, in full |
| `reporting_requirement` instructing the model not to imply a quantity | confirmed, in the model view |
| Prompt scopes the claim to *this system's* datasets rather than claiming no source anywhere could estimate it | confirmed; two language-calibration tests assert it |
| Band capped by availability, and the cap is stated in `limitations` | confirmed |

The model receives 113 distinct numbers for SFO, every one an indicator value or a
threshold. There is no quantity in the payload that *could* be misreported as
unserved passengers.

### Two fields computed and shown nowhere

| field | in payload | to model | in panel |
|---|---|---|---|
| `direction` (what a trigger would be consistent with) | yes | **no** | **no** |
| per-indicator `source` | yes | **no** | **no** |

`direction` holds the careful interpretation text — for U3, *"Airlines adding
seats they cannot add as flights — a classic slot/gate-constrained signature."*
Nobody ever sees it. That cuts both ways: the wording is more causally assertive
than the rest of the system permits, so shipping it as-is would be a
regression, but leaving the model to invent its own interpretation of a firing
indicator is worse. Per-indicator `source` is the provenance of each row and is
simply missing from the display.

---

## 8. Assessment of the current Q4 answer

The Q4 checklist in `smoke_test.py` requires: unmet demand cannot be measured; no
numeric figure; the indicator table and band (Weak); U5 reported unavailable
rather than not-triggered.

| criterion | assessment |
|---|---|
| **Numerical provenance** | **Strong.** Every figure available to the model is an indicator value or threshold from the payload, and the audit accepts only numbers present there. |
| **Appropriate uncertainty** | **Strong on the impossibility claim**, correctly scoped to *this system's* datasets rather than overclaiming about all possible data. |
| **Clarity** | **Adequate but bland.** The answer reports a table and a band. It does not say the thing an analyst most needs: that a Weak band here means the evidence points *away* from a supply constraint in this window. |
| **Usefulness to an investment analyst** | **The weakest dimension.** "Weak, 1 of 4" is nearly meaningless without knowing that 328 of 399 airports are also Weak, that only one airport in the cohort reaches Strong, and that SFO's slot-constrained peers band Moderate. The band has no cohort context attached, so the reader cannot calibrate it. |

The answer is honest. It is not yet *informative*.

---

## 9. Proposed corrections — smallest justified set

Ordered by value per unit of change. **None adds an indicator, a weight or a
threshold.**

**C1 — Report the band with cohort context.** Attach the cohort band
distribution, or the airport's triggered-count percentile, to the payload.
"Weak — as are 328 of 399 airports; 1 reaches Strong" converts an uncalibrated
label into a usable one. Pure disclosure; no methodology change. *Highest value,
smallest change.*

**C2 — State the attainable maximum on the band itself.** The `limitations` text
already says the maximum is capped; the band field does not. Adding
`max_attainable_band` (or simply surfacing "3 of 3 measurable indicators fired")
fixes the §6 asymmetry **presentationally**, without re-tuning the count
thresholds. An airport at 3-of-3 should not read as weaker than one at 4-of-4.

**C3 — Surface `direction` and per-indicator `source`,** after rewording
`direction` to the system's own evidential standard — "consistent with" rather
than "a classic signature of". They are already computed; displaying them costs
nothing and closes the gap where the model invents its own interpretation.

**C4 — Note U2/U3's shared derivation where they are displayed.** One sentence
stating that passenger growth decomposes into frequency, gauge and load factor, so
those indicators are related views rather than independent confirmations. Prevents
a reader from treating 2-of-4 as two independent findings.

**C5 — Disclose U1's cross-class comparability limit.** One sentence: the
threshold is cohort-wide, and large hubs clear it 67% of the time against 25%
cohort-wide. Optionally report the airport's within-class rank alongside — SFO is
2nd of 30 large hubs, which is genuinely more informative than "above P75".

**C6 — Consider ingesting U5** (BTS Consumer Airfare, verified accessible) in a
later phase. It is the only one of the five that observes a *market's* own
statement about scarcity rather than an operational outcome, and it would also
raise the attainable maximum for the 162 airports currently capped. Out of scope
here: it needs a download, which this phase prohibits.

**Explicitly not recommended:** new weights, a composite UDEI score, re-tuned
count thresholds, or any attempt to estimate a magnitude. The band's problem is
calibration and disclosure, not arithmetic, and §7 shows the anti-fabrication
design is working and should not be loosened.

---

## 10. Is the original question quantitatively answerable?

**No, and the honest answer has three parts.**

1. **The magnitude is unanswerable from these sources, as a matter of structure
   rather than effort.** Unmet demand is counterfactual. T-100 records flown
   flights, OTP records their punctuality, FAA records enplanements. None contains
   a row for a flight not scheduled or a passenger who did not book. No
   transformation of the three recovers a quantity that none observes. Data that
   *could* support an estimate exists — booking and fare data, schedule-request
   and slot-application records, airline internal demand models, stated-preference
   surveys — and this system has none of it.

2. **Several component questions *are* answerable, and were answered.** Whether
   existing service runs full (yes: 82.6%, 2nd of 30 large hubs), whether demand
   outgrew seats or departures (no: departures +4.2% against passengers +3.4%),
   and whether aircraft size or frequency shifted (gauge −0.8%, frequency up) are
   all measured facts from T-100.

3. **For SFO specifically, the evidence points away from a binding constraint in
   this window.** One of four indicators fires; it added flights faster than
   passengers; its gauge fell; its ACI sits below the cohort's upper quartile; and
   its Bay Area alternatives are contracting rather than absorbing spillover. A
   diligent answer says that plainly instead of leaving "Weak" to speak for
   itself.

That is a more useful answer to an investment analyst than any number would have
been, because a number here could only have been fabricated.

---

## 11. Limitations of this review

1. **One window, one cohort.** SFO's 2025-05..2026-04 figures may not be
   typical; a single year of YoY ratios cannot distinguish a trend from a blip.
2. **No ground truth.** Nothing records which airports genuinely turned demand
   away, so indicator *validity* cannot be tested — only internal consistency and
   conceptual soundness.
3. **The peer set is eleven airports**, chosen by judgement (stated before the
   results were seen). A different set could shift the comparative picture.
4. **The OAK/SJC spillover argument is an inference**, not a measurement. Traffic
   substitution between airports is not observable in these datasets, and both
   could be contracting for unrelated reasons.
5. **U5's absence is doing real work.** The indicator most likely to bear on
   market scarcity is the one missing everywhere, so every band in the system is
   computed without it.
6. **I did not evaluate the 2% upgauging threshold's sensitivity.** It is a
   judgement constant; measuring how the trigger rate moves across plausible
   values would be worth doing if U3 is retained in its current form.

---

## 12. Changed files

| file | change |
|---|---|
| `backend/evaluate_udei_evidence.py` | **new** — read-only audit harness, 6 sections |
| `docs/phase-8.3-udei-evidence-evaluation.md` | **new** — this report |

**No production file was modified.** `git status` shows one untracked script and
this document. Scoring, API contracts, agent prompts, tool schemas and the
frontend are all untouched, as instructed. No new tests were added, because
nothing was implemented; the existing 17 UDEI tests already cover the controls
verified in §7.

### Test results

```
499 passed in 16.09s
```

Full offline backend suite, unchanged from commit `f53db35`. No live API calls.

---

## 13. Decision requested

1. **Adopt C1 and C2** — cohort context for the band, and the attainable maximum
   stated on the band itself. Together they fix the §6 defect presentationally and
   turn an uncalibrated label into a usable one. *Recommended.*
2. **Adopt C3, C4, C5** — surface `direction` (reworded) and `source`; note the
   U2/U3 shared derivation; disclose U1's cross-class limit. All disclosure-only.
   *Recommended.*
3. **Defer C6** (ingest BTS Consumer Airfare for U5) to a phase where a download
   is in scope.
4. **Reject** any new weights, composite score, re-tuned thresholds or magnitude
   estimate.

Nothing is committed. Awaiting review before implementation.
