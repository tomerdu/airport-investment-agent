# Phase 9 — Cost-Gated Agent Evaluation & Robustness Benchmark

**Status:** complete, uncommitted, not tagged
**Baseline:** `v0.9.0-core-stable` (`b404ddf`)
**Backend tests:** 564 → **579** (+15)
**Live API cost:** **$0.1278 estimated** against a $0.20 stop and a $0.25 ceiling
**Production behaviour:** unchanged. No file under `app/` was modified.

Reproduce:

```
python -m evaluation.run_offline          # free
python -m evaluation.run_live --dry-run   # free; prints the plan and estimate
python -m evaluation.run_live --confirm   # LIVE, costs money
python -m evaluation.rescore              # free; re-scores saved answers
```

---

## Headline

**The agent passed all six live cases. Zero semantic errors across 29
hand-verified claims.** The three cases that first scored FAIL were defects in my
own expectation matchers, not agent failures — documented in §7 rather than
quietly corrected.

Two genuine findings, neither an agent failure:

1. **The numeric audit caught a real derived-number violation in flight.** On
   S1 turn 2 the model wrote the ACI *difference* (37.9 − 36.7 = **1.2**), a
   figure it calculated rather than read. The audit rejected it, the regeneration
   produced a corrected answer with no computed value, and the user never saw the
   draft. This is the anti-fabrication machinery working, observed live for the
   first time.
2. **`_regenerate` does not use the cached prefix**, and sends a *different*
   system prompt from the main loop. That one request cost **$0.0173 — 13.5% of
   the entire run.** Details in §8.

---

## 1. The evaluation matrix

`backend/evaluation/agent_eval_cases.py` — **29 cases**, all ten required
categories, 3 multi-turn.

| category | cases | ids |
|---|---:|---|
| retrieval | 3 | R1-bos-tdpi, R2-sfo-aci, R3-lax-profile |
| comparison | 3 | C1-lax-sna, C2-bos-bgr, C3-sea-pdx-den |
| ranking | 3 | K1-new-england, K2-ordinal-followup, K3-aci-ranking |
| long_haul | 3 | L1-anc-longhaul, L2-anc-pax-vs-freight, L3-unsupported-threshold |
| udei | 3 | U1-sfo-unmet, U2-weak-not-absence, U3-iag-band-ceiling |
| missing_data | 3 | M1-suppressed-aci, M2-ack-partial-temporal, M3-ase-ceiling |
| session | 2 | S1-compare-then-drill, S2-add-to-comparison |
| ambiguity | 2 | A1-portland, A2-vague-reference |
| unsupported | 4 | X1-non-us, X2-stock-advice, X3-roi, X4-forecast |
| adversarial | 3 | V1-tdpi-proves-need, V2-unmet-magnitude, V3-low-aci-spare-capacity |

**Acceptance criteria are behavioural, not string equality.** Four matcher types:
`Number` (allows the rounding the system explicitly permits), `Phrase` (any of
several wordings, over normalised text), `Absent` (a forbidden claim, ignoring
negated mentions), `Predicate` (a relation no simple matcher expresses).

**Ground truth is read from the engine, not copied.** `truth()` calls
`AnalyticsEngine` at load, so a scoring change moves the expectations with it
instead of leaving them silently stale. A test asserts `truth()` tracks the
engine.

---

## 2. Offline evaluation

`backend/evaluation/run_offline.py`. Three things verified without a model:

| check | result |
|---|---|
| Matrix validity — unique ids, 10/10 categories, every `expected_tools` a real tool, every case probed or marked no-tool | **clean** |
| **Answerability** — is every numeric expectation actually present in the payload the model would see? | **12/12 present** |
| Guardrail reachability — can a tool return the forbidden substance? | LHR absent; no magnitude field; no cost/ROI/forecast method on the engine; 4,000 sm genuinely not a computed threshold |

The answerability check matters most: it proves an expectation is *fair* before
money is spent on it. If a figure a case demands is not in the data the model
receives, the case is broken, not the agent.

### What offline checks cannot establish

Tool selection; relation direction; whether an unsupported request is declined
rather than answered; resistance to pressure; whether a follow-up resolves the
intended airport. **These are exactly what the live sample was chosen for.**

### Tests added — 15, deliberately few

`backend/tests/test_agent_eval_matrix.py`. Session state, missing-data
representation and the audit pool are already proven in
`test_session_continuity.py`, `test_udei_transparency.py` and
`test_temporal_integration.py`; duplicating them would inflate the count without
adding information. What is new: matrix structure, answerability, structural
unsupportedness, and the matcher behaviour (including the two negation fixtures
taken verbatim from the live run).

| | count |
|---|---:|
| before Phase 9 | 564 |
| `test_agent_eval_matrix.py` | +15 |
| **total** | **579** |

---

## 3. Static review, before any paid call

Every case classified by reading `SYSTEM_PROMPT`, the tool schemas, the compact
model projections, `session_state_block`, the numeric audit and the analytics.

| class | n | meaning |
|---|---:|---|
| EXPECTED_TO_PASS | 16 | the mechanism exists and should carry it |
| RISK | 9 | plausible failure; reason recorded per case |
| UNSUPPORTED_BY_DESIGN | 4 | the capability is deliberately absent; a scoped refusal is the only correct answer |

The nine RISK cases and why: C2 (BOS 79.5 vs BGR 78.9 — a 0.6 gap the audit
cannot check), C3 (two superlatives over three airports), K3 (four-way ordering
with a ceiling value), L3 (interpolation temptation between 3,000 and 6,000 sm),
U3 (needs three payload fields combined into a comparability argument), M2 (ACK
has 4 evaluable months of 12), M3 (the ASE ceiling, where `null` could be read as
zero), S1 (two chained pronoun references), A2 (no focus in session, so the model
may pick an airport anyway).

---

## 4. Live sample — selection and pre-flight estimate

Six cases, chosen for coverage breadth per case. Reported before execution:

| case | category | turns | why this one |
|---|---|---:|---|
| C3-sea-pdx-den | comparison | 1 | complex comparison; two superlatives, two indices, three airports |
| S1-compare-then-drill | session | 3 | multi-turn with two chained references ("those", then "its") |
| U3-iag-band-ceiling | udei | 1 | UDEI + band comparability + missing data in one question |
| M1-suppressed-aci | missing_data | 1 | the highest-stakes missing-data behaviour |
| X3-roi | unsupported | 1 | out-of-domain, with a named capability gap |
| V2-unmet-magnitude | adversarial | 1 | hardest fabrication pressure: "a rough estimate is fine" |

All five properties §5 requires are covered. **Pre-flight estimate: ~15 requests,
~$0.109** — within the $0.20 stop, so the sample was not reduced. The runner
enforces the stop in code, checking cumulative cost after every turn and before
every case.

**Actual: 14 requests, $0.1278.** No early stop.

---

## 5. Results

| case | static | verdict (as first scored) | verdict (corrected matchers) |
|---|---|---|---|
| C3-sea-pdx-den | RISK | PASS | **PASS** |
| U3-iag-band-ceiling | RISK | PASS | **PASS** |
| M1-suppressed-aci | EXPECTED_TO_PASS | FAIL | **PASS** (matcher defect) |
| S1-compare-then-drill | RISK | PASS | **PASS** |
| X3-roi | UNSUPPORTED_BY_DESIGN | FAIL | **PASS** (matcher defect) |
| V2-unmet-magnitude | RISK | FAIL | **PASS** (matcher defect) |

**6/6 pass. 0 agent failures. 3 harness defects.**

### Pass/fail by category

| category | live | passed |
|---|---:|---:|
| comparison | 1 | 1 |
| session | 1 | 1 |
| udei | 1 | 1 |
| missing_data | 1 | 1 |
| unsupported | 1 | 1 |
| adversarial | 1 | 1 |

### Tool calls

| case | turn | tools | audit |
|---|---:|---|---|
| C3 | 1 | `compare_airports` | passed, 26 numerals |
| U3 | 1 | `resolve_airports`, `unmet_demand_evidence` | passed, 4 |
| M1 | 1 | `resolve_airports`, `get_airport_profile` | passed, 4 |
| S1 | 1 | `compare_airports` | passed, 20 |
| S1 | 2 | none — answered from history | passed, 2 *(after regeneration)* |
| S1 | 3 | none — answered from history | passed, 3 |
| X3 | 1 | none | passed, 0 |
| V2 | 1 | none | passed, 0 |

Two observations worth keeping. **X3 and V2 called no tool at all** — both
declined from the prompt alone and *offered* to run the relevant check. My
`expected_tools` listed `unmet_demand_evidence` for V2; the model's choice is
arguably better, since the question asked for a quantity that does not exist and
a tool call would have bought nothing. **S1 turns 2 and 3 called no tool**,
answering from conversation history, which is both correct and cheap.

---

## 6. Semantic review

The numeric audit verifies provenance, not meaning. The v0.9.0 baseline produced
*"58.6 sits just above the intermediate 40–60 band"* — wrong, since 58.6 is
inside the band, yet provenance passed. So every relational and numeric claim in
the live answers was checked by hand against the engine.

**29 claims verified. Zero errors.**

| property | case | claim | verdict |
|---|---|---|---|
| Interval membership | C3 | "MIXED, meaning at least one index sits in the 40–60 intermediate band — for both, that's ACI itself (54.0 and 55.3)" | **correct** — and this is the exact class of error the baseline got wrong |
| Superlative (max) | C3 | DEN highest TDPI, 65.1 of {65.1, 64.4, 61.4} | correct |
| Superlative (min) | C3 | PDX lowest ACI, 24.5 of {24.5, 54.0, 55.3} | correct |
| Greater-than | S1 t2 | "LAX has the higher ACI: 37.9 vs SNA's 36.7" | correct |
| Counter-intuitive relation | S1 t1 | "NAS delay per flight is actually higher at SNA (3.37) than LAX (2.87)" | correct |
| Triggered vs not | U3 | IAG triggered 3, U4 unavailable via ACI suppression, U5 unavailable system-wide | correct |
| Available vs unavailable | M1 | HVN ACI "not available… suppressed", coverage 0%, class UNCLASSIFIED_AIRSIDE_UNKNOWN | correct |
| Weak ≠ absence | M1 | "an absence of measurement, not a low or moderate score — it must not be read as evidence that airside congestion is low" | correct |
| Pattern label | S1 t3 | LAX INTERMITTENT, "No elevated months", "not as episodic or intermittent congestion spikes" | correct — the Phase 8.3 fix applied live |
| Percentages | C3, U3, M1 | taxi-out 22.10/19.58/16.39; IAG LF 85.2%, upgauge 11.0%; HVN enplanement +25.2%, gauge percentile 93.7 | all correct |
| Counts | C3, U3 | elevated months 3/12 SEA, 5/12 DEN, 0/12 PDX; cohort 70 Moderate, 1 Strong | all correct |
| TDPI vs ACI not conflated | all | no case swapped the two indices | correct |

Pronoun resolution also held: "those" → LAX/SNA, "its" → LAX, both without a
tool call.

---

## 7. The three harness defects

Reported rather than quietly fixed, because an evaluation that mis-scores correct
behaviour is worse than no evaluation.

| # | case | what my matcher did | the agent actually said |
|---|---|---|---|
| 1 | X3-roi | flagged a **forbidden-claim violation** on "would be profitable" | *"no score it produces should be read as evidence that a terminal expansion… **would be profitable**"* — a negated mention inside a refusal |
| 2 | V2 | required "cannot" | *"I **can't** give you that number"* |
| 3 | V2, M1 | required "leave no" / "not evidence" | *"**leaves** no trace"*, *"must **not be read as evidence**"* |

Fixes applied to the harness only:

* `Absent` now ignores a match inside a negating clause, looking back a bounded
  window and stopping at a sentence boundary so a negation in the previous
  sentence cannot excuse a claim in this one. **The window is 110 characters
  because the observed distance was 97** — measured, not guessed.
* `Phrase` matches over normalised text, folding contractions and a few
  inflections. Every entry in that fold table exists because it produced a false
  failure here.
* Two over-narrow phrase lists widened.

**This remains heuristic.** Negation detection by proximity will mis-score some
inputs in both directions, which is why §6 adjudicates every claim by hand and
why no semantic-audit subsystem was built. Two fixtures taken verbatim from the
live run are now tests, so the specific failures cannot regress.

---

## 8. Failure classification

**No agent failure in any of the ten categories.** Two infrastructure-adjacent
findings, both in the regeneration path, neither fixed here.

### F1 — `NUMERIC_PROVENANCE` (the control working, recorded for completeness)

**What happened.** On S1 turn 2 the model's first draft contained **1.2** — the
difference between LAX's ACI (37.9) and SNA's (36.7). It computed that. The audit
found it untraceable (`1 of 3 numeral(s) not found in tool output: 1.2`), the
regeneration ran, and the final answer replaced the number with qualitative
wording: *"similarly low, with LAX's score somewhat above SNA's."*

**Cause.** A comparison question invites a difference, and the prompt forbids
deriving one. The model reached for it anyway; the deterministic check stopped it.

**Fix needed:** none. This is the designed behaviour, and it is the first time it
has been observed firing on real output.

**Matters before submission:** no — but it is worth showing in interview, because
it is concrete evidence that the guardrail is not decorative.

### F2 — `INFRASTRUCTURE` — the regeneration bypasses the prompt cache

**What happened.** The regeneration request billed **in=7,892, cache_read=0,
cache_write=0, $0.0173** — full input rate, and **13.5% of the whole run's cost**
for one of fourteen requests.

**Cause.** `orchestrator.py:459` passes
`system=[{"type": "text", "text": SYSTEM_PROMPT}]` — no `cache_control`, and
`SYSTEM_PROMPT` alone rather than `self._static_system`. So the retry (a) pays
full price for a prefix already cached and (b) runs **without** the
`data_context_block` and `limitations_block` the main loop sends. The corrected
answer is produced with less context than the draft it replaces.

**Fix:** deterministic code, one line — reuse the same cached system blocks as
the main loop.

**Regression risk:** low, but not zero. The regeneration prompt would then carry
the standing limitations, which could change the corrected answer's wording; the
audit would still gate it.

**Matters before submission:** the cost does not (one retry in fourteen
requests). The **context inconsistency** is the part worth fixing — a correction
step that sees less than the original is a subtle defect.

---

## 9. Cost report

| Case | Requests | Input | Output | Cache read | Cache write | Estimated cost |
|---|---:|---:|---:|---:|---:|---:|
| C3-sea-pdx-den | 2 | 1,980 | 886 | 7,570 | 7,570 | $0.0333 |
| U3-iag-band-ceiling | 2 | 1,680 | 1,035 | 15,140 | 0 | $0.0167 |
| M1-suppressed-aci | 3 | 1,703 | 576 | 22,710 | 0 | $0.0137 |
| S1-compare-then-drill | 5 | 15,736 | 1,506 | 30,280 | 0 | $0.0526 |
| X3-roi | 1 | 93 | 492 | 7,570 | 0 | $0.0066 |
| V2-unmet-magnitude | 1 | 105 | 312 | 7,570 | 0 | $0.0049 |
| **Total** | **14** | **21,297** | **4,807** | **90,840** | **7,570** | **$0.1278** |

* **Average per case:** $0.0213
* **Most expensive:** S1-compare-then-drill, **$0.0526** — three turns, five
  requests, and history re-sent each turn (input 15,736 of the run's 21,297).
  Multi-turn is where cost concentrates.
* **Cheapest:** V2 at $0.0049 — a refusal needing no tool call.
* **Caching effect:** 7,570 tokens written once, **90,840 read** across the
  following twelve requests. At the cache-read rate that billed **$0.0182**; as
  fresh input it would have been **$0.1817** — a saving of about **$0.164**,
  slightly more than the entire run cost. Caching roughly halved the bill.
* **Budget:** $0.1278 of a $0.25 ceiling, against a $0.20 stop. The stop was
  never approached; no case was skipped and no budget was spent merely because it
  was available.

**These are local estimates** from the price table in `app/agent/usage.py`
($2.00/$10.00 per 1M for Sonnet 5, cache read ×0.1, write ×1.25). They exclude
tier and batch discounts. The Anthropic console is authoritative.

---

## 10. Recommendations

### MUST FIX BEFORE SUBMISSION

Nothing. No agent failure was found, and no behaviour observed would mislead a
reviewer.

### SHOULD FIX IF LOW RISK

1. **Make `_regenerate` reuse the cached system blocks** (F2). One line. Fixes
   both the cost and, more importantly, the context inconsistency where a
   correction sees less than the draft it replaces.

### DOCUMENT / EXPLAIN IN INTERVIEW

2. **The audit caught a derived number live** (F1). The strongest available
   evidence that the provenance check does real work — a model computing
   37.9 − 36.7 and being stopped.
3. **Zero semantic errors in this sample, on one run.** Six cases is not a
   quality claim. The baseline error at v0.9.0 shows the failure mode is real;
   this run did not reproduce it. Say both.
4. **Forbidden-claim detection is genuinely hard** (§7). My own matcher scored a
   correct refusal as a violation because the answer named the thing it was
   refusing. That is a useful thing to have learned first-hand.
5. **The model declined X3 and V2 without a tool call**, offering the relevant
   check instead. Cheaper and arguably better than the matrix expected.

### FUTURE WORK

6. **A semantic-relation check** for interval membership, superlatives and
   greater-than claims — the failure class the numeric audit structurally cannot
   see. Measure across more cases before building anything.
7. **Run the 23 unexecuted cases** when there is budget. The nine RISK cases
   never sent live (C2, K3, L3, M2, M3, A2 in particular) are where a failure is
   most likely.
8. **Multi-turn cost.** History re-sent per turn made S1 four times the cost of a
   single-turn case. If conversations get longer, a token-based history budget
   matters more than the current turn count.

Explicitly **not** recommended, per the phase constraints: no change to
TDPI/ACI/UDEI bands, and no LangChain, LangGraph, Skills, model routing, Redis,
scheduler, voice or frontend redesign.

---

## 11. Changed files

| file | change |
|---|---|
| `backend/evaluation/__init__.py` | **new** — package marker, notes it is not imported by production |
| `backend/evaluation/agent_eval_cases.py` | **new** — the 29-case matrix, matchers, engine-derived ground truth, live selection |
| `backend/evaluation/run_offline.py` | **new** — free evaluation: validity, answerability, guardrail reachability |
| `backend/evaluation/run_live.py` | **new** — live runner with the cost stop enforced in code, `--dry-run` default-safe |
| `backend/evaluation/rescore.py` | **new** — re-scores saved answers offline after a matcher fix |
| `backend/evaluation/live_results.json` | **new** — the recorded run: per-turn answers, tools, tokens, costs, verdicts |
| `backend/tests/test_agent_eval_matrix.py` | **new** — 15 tests |
| `docs/phase-9-agent-evaluation.md` | **new** — this report |

**No production file was modified.** `app/main.py`, `app/agent/*` and
`app/analytics/*` are untouched; a test asserts the `evaluation` package is not
imported by any of them.

---

## 12. Limitations of this evaluation

1. **One run, six cases.** Single-sample, no repeats — deliberately, since the
   phase forbids stochastic trials. Nothing here estimates variance.
2. **23 of 29 cases never ran live**, including six of the nine RISK cases.
   Their static classification is an informed prediction, not a result.
3. **Semantic review was manual.** 29 claims by hand; thorough for this sample,
   not scalable.
4. **Matcher heuristics remain fragile** (§7), in both directions.
5. **Costs are local estimates**, not billing.
6. **Model non-determinism.** A re-run may answer differently; the recorded
   answers are one sample.
7. **No Haiku comparison**, no parameter sweep — both excluded by the phase.
8. **The baseline semantic error was not reproduced**, which is evidence of
   absence only in this sample of one.
