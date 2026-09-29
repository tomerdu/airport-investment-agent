# Phase 8.3b — UDEI Evidence Transparency Integration

**Status:** accepted; optimised; uncommitted, awaiting final approval
**Window:** 2025-05 .. 2026-04 (unchanged)
**Tests:** 549 passed, 0 failed, 0 skipped — full offline backend suite
**Frontend build:** **not run** — no Node runtime in this environment (§7)
**External calls:** none. No downloads, no live LLM calls, no new datasets.

> **Acceptance.** The analytical implementation was accepted after manual browser
> testing of the SFO response. A final optimisation pass then cut the model-facing
> payload from **7.6 KB to 3.3 KB (−57%)** and made the numeric-provenance audit
> check only model-visible figures. Both are recorded in §10b. No score, weight,
> threshold or band changed.

Audit and evidence: `phase-8.3-udei-evidence-evaluation.md`.

---

## 1. What changed, and what deliberately did not

**Unchanged — verified by test, not by assertion:**

| | |
|---|---|
| Band thresholds | 0–1 Weak, 2–3 Moderate, 4+ Strong; `UDEI_COHORT_PERCENTILE = 75.0`, `UDEI_UPGAUGE_THRESHOLD = 0.02` |
| Every band in the cohort | 328 Weak / 70 Moderate / 1 Strong — identical |
| SFO | **Weak, 1 of 4 available**, `{U1: True, U2: False, U3: False, U4: False, U5: None}` |
| TDPI / ACI | Σ TDPI 16146.9211, Σ ACI 10931.2699 — bit-identical |
| U5 | unavailable everywhere, original reason verbatim |
| Magnitude | still no field, anywhere |

**Changed: disclosure only.** No new composite, no new weights, no new
thresholds, no estimate of missing flights or passengers.

---

## 2. Per-indicator direction and provenance (requirements 1–2)

The audit found `direction` and per-indicator `source` were computed, serialised,
and **displayed nowhere** — neither to the model nor in the panel. Both now reach
both, along with two new fields.

Each indicator carries:

| field | purpose |
|---|---|
| `direction` | what a trigger is **consistent with** — never a cause |
| `cannot_establish` | what it cannot show even when it fires |
| `threshold_note` | how the threshold is built and where it is not comparable |
| `source` | provenance of the row |
| `shares_arithmetic_with` | indicators measured on the same quantities |

### Causal wording rewritten

| id | before | after |
|---|---|---|
| U3 | "Airlines adding seats they cannot add as flights — **a classic slot/gate-constrained signature**" | "Consistent with capacity being added as larger aircraft rather than as additional flights." |
| U4 | "Airport is **hitting operational limits**" | "Consistent with the airport operating with high observed delay and queuing relative to peers." |
| U1 | "Little slack in existing seats" | "Consistent with little residual slack in the seats actually offered, measured as a seat-weighted 12-month average." |
| U2 | "Demand absorbed without adding flights" | "Consistent with additional passengers being absorbed without adding flights." |
| U5 | "Supply-constrained market pricing" | "Would be consistent with pricing in a supply-constrained market. Not evaluated — see the unavailable reason." |

A test asserts every `direction` contains "consistent with", and that none of the
four previous causal phrases survives anywhere across five sampled airports.

Each indicator's limit is now stated rather than left to a report. U3's, for
example: *"Cannot establish a slot, gate or runway constraint. The US narrowbody
fleet has been trending larger for years independently of any individual airport…
Cohort-wide the link between gauge growth and departure growth is weak (Spearman
−0.10), which is not what a strong substitution effect would look like."*

---

## 3. Band context (requirements 3–5)

Every result now carries, alongside the band:

```
triggered_count             1
available_count             4
unavailable_count           1
unavailable_reasons         [{id: U5, label: …, reason: …}]
max_attainable_triggered    4
max_attainable_band         "Strong"
band_definition             absolute counts, 0-1/2-3/4+, not weighted
band_comparability_note     why bands are not fully comparable
```

`max_attainable_*` applies the **same unchanged thresholds** to the attainable
maximum. It discloses the ceiling; it does not move the band.

### The asymmetry, now visible on real airports

| apt | triggered / available | band | max attainable | unavailable |
|---|---|---|---|---|
| **IAG** | **3 / 3** — everything measurable fired | **Moderate** | 3 (Moderate) | 2 (U4, U5) |
| **ROC** | **4 / 4** — everything measurable fired | **Strong** | 4 (Strong) | 1 (U5) |

Both airports triggered 100% of what could be evaluated, and they band
differently. **The classification is preserved for compatibility**, as instructed
— what is new is that IAG's payload states it reached its own ceiling, so a reader
can see the difference is data coverage rather than evidence strength. A test
pins both airports and asserts `iag.triggered_count == iag.max_attainable_triggered`.

Full distribution of availability/trigger combinations (all 399 airports):

| available | triggered | band | max band | n | example |
|---:|---:|---|---|---:|---|
| 2 | 0 | Weak | Moderate | 1 | KLW |
| 2 | 1 | Weak | Moderate | 2 | GST |
| 3 | 0 | Weak | Moderate | 89 | ABR |
| 3 | 1 | Weak | Moderate | 54 | ABY |
| 3 | 2 | Moderate | Moderate | 15 | CWA |
| 3 | 3 | Moderate | Moderate | 1 | IAG |
| 4 | 0 | Weak | Strong | 75 | ABI |
| 4 | 1 | Weak | Strong | 107 | ABE |
| 4 | 2 | Moderate | Strong | 43 | ALB |
| 4 | 3 | Moderate | Strong | 11 | ATW |
| 4 | 4 | Strong | Strong | 1 | ROC |

### Cohort context, with a guard

`cohort_context` reports band frequencies (328 / 70 / 1 of 399) and the
distribution of attainable maxima. It carries an explicit note:

> Cohort frequencies are provided so a band can be calibrated — whether it is
> common or unusual. They are **NOT** evidence that any particular airport does or
> does not have unmet demand, and a rare band is not a stronger finding for the
> airport that holds it.

That guard is asserted by test. The context is computed once per engine and
cached, because it evaluates UDEI for all 399 members.

---

## 4. Shared arithmetic (requirement 6)

The identity, verified to 1e-9 on SFO's own figures by a test:

```
(1 + pax growth) = (1 + departure growth) × (1 + gauge growth) × (1 + LF growth)
       1.033545  =  1.033545
  departures +4.22%   gauge −0.82%   load factor −0.02%
```

U2 reads the first term, U3 the second, U1 the level of the third.
`indicator_relationships` states this on every result, each indicator declares
`shares_arithmetic_with` (U1↔U2↔U3; U4 declares none), the panel prints "shares
arithmetic with U1, U3" beside the label, and the prompt forbids presenting two of
them as independent confirmations.

The test also asserts the claim is *arithmetically true*, not merely written down.

---

## 5. U1's threshold (requirement 7)

`threshold_note` on U1 states that the bar is the percentile of the **whole**
cohort, which is dominated by small airports, and that load factor varies by hub
class — large hubs trigger U1 about **67%** of the time against roughly **25%**
cohort-wide. So a trigger means "high relative to all US primary airports", not
"high for an airport of this size".

The optional within-class comparison is included, clearly labelled and
reproducible:

> Within hub class L this load factor ranks **2 of 30** (median 80.8%). Reproduce
> by ranking `load_factor` among cohort members with `hub_class == 'L'`. Context
> only: the U1 trigger is unchanged and remains the cohort-wide P75 comparison.

It is omitted when the class has fewer than 5 peers, so no within-class claim
rests on a handful of airports — asserted by test.

U3 and U4 also gained `threshold_note`s: U3's +2% bar is disclosed as a judgement
constant, and U4's is disclosed as another cohort-P75 quartile flag.

---

## 6. Agent answer structure (requirements 9–10)

The prompt now requires three parts, kept distinct — conflating them was the main
failure mode the audit identified:

1. **What was observed** — load factor, passenger growth, departure growth, seats
   per departure. Facts from T-100.
2. **What that evidence is consistent with** — using each indicator's
   `consistent_with` wording, with its `cannot_establish` limit in the answer, not
   just the payload.
3. **What is missing to quantify it** — no booking, fare, schedule-request or
   slot-application data; U5 unavailable for every airport; and what kind of data
   would be needed.

Plus: report the band **with its counts and attainable maximum, never the label
alone**; use `cohort_context` to calibrate but never as evidence about the
airport; never present U2 and U3 as independent; and **do not retype the whole
indicator table** — the panel is the source of detail.

### What the model now receives

| | before | after |
|---|---:|---:|
| Q4 model view | ~1.8 KB | **7.6 KB** |
| Q4 full payload | 6.7 KB | 12.9 KB |

**This is a real cost and worth stating plainly.** The model view is ~4× larger
because the honesty payload — five `cannot_establish` texts, the threshold notes,
the band definition and the comparability note — is text. Two things mitigate it:
the six band fields are carried **once**, inside `band_context`, rather than
duplicated at top level (a test pins that), and the monthly-style bulk detail
stays out. It applies to one tool call per unmet-demand question, not to every
turn.

---

## 7. Verification

### Backend

```
532 passed in 29.06s
```

Full offline suite: 499 from `f53db35` + 33 new in `tests/test_udei_transparency.py`.
Mocked LLM clients, `-m "not live"`. **No live API calls.**

Acceptance criteria, each asserted:

| requirement | test |
|---|---|
| Band classifications unchanged | `test_cohort_band_distribution_unchanged` (328/70/1) |
| TDPI and ACI unchanged | `test_tdpi_and_aci_checksums_unchanged` |
| No magnitude introduced | `test_no_magnitude_field_was_introduced` |
| SFO Weak with 1/4 | `test_sfo_remains_weak_with_one_of_four` |
| U5 disclosed with its reason | `test_u5_still_unavailable_with_its_original_reason` |
| All unavailable indicators have reasons | `test_every_unavailable_indicator_has_a_reason` (60 airports) |
| 3/3 vs 4/4 | `test_equal_completeness_bands_differently_and_that_is_disclosed` (IAG, ROC) |
| No causal language | `test_no_indicator_asserts_a_cause` (5 airports × 5 indicators) |
| Identity is true | `test_the_identity_actually_holds_for_sfo` |

### The four exam scenarios — deterministic results unchanged

| scenario | result |
|---|---|
| Q1 New England | top 3 **HVN, BOS, BGR**; 16 ranked |
| Q2 LAX vs SNA | ACI **37.9** / **36.7** |
| Q3 Anchorage | 4 scopes; combi departures **888** |
| Q4 SFO | band **Weak**, **1/4** triggered |

No magnitude-shaped key appears in any of the four payloads.

### Frontend — not verified here

**`npm run build` was not run: there is no Node runtime in this environment**
(`node`, `npm`, `npx`, `pnpm`, `yarn` all absent). `tsc -b` did not typecheck the
TypeScript. What I did instead:

- **Verified every CSS class the panel uses exists**: `.note`, `.note.caution`,
  `.indicator .lbl/.thr/.val`, `.band`, `.muted`, `.small`. Confirmed `.indicator`
  is a 3-column grid with `align-items: start`, so the taller multi-line rows lay
  out correctly.
- **Delimiter balance on added lines**: `panels.tsx` +98 lines `()` 30/30,
  `[]` 4/4, `{}` 50/50; `types.ts` and `styles.css` likewise balanced.
- **All new `UnmetDemandResult` and `Indicator` fields are optional** (`?`), so an
  older payload still typechecks against the new interfaces.

Balanced delimiters are not a typecheck. **Please run `npm run build` in
`frontend/` on Windows before relying on the UI.**

---

## 8. Files changed

| file | change |
|---|---|
| `backend/app/analytics/definitions.py` | `UDEI_BAND_DEFINITION`, `UDEI_BAND_COMPARABILITY_NOTE`, `UDEI_ARITHMETIC_NOTE`, `UDEI_U1_COMPARABILITY_NOTE`. **No threshold or weight touched.** |
| `backend/app/analytics/models.py` | `Indicator`: `cannot_establish`, `threshold_note`, `shares_arithmetic_with`. `UnmetDemandEvidence`: `unavailable_count`, `unavailable_reasons`, `max_attainable_triggered`, `max_attainable_band`, `band_definition`, `band_comparability_note`, `indicator_relationships`, `cohort_context`. All defaulted and additive. |
| `backend/app/analytics/unmet.py` | reworded all five `direction` texts; added the limits, threshold notes and shared-arithmetic declarations; U1 within-class context; band disclosure fields. **Band logic byte-for-byte unchanged.** |
| `backend/app/analytics/engine.py` | `_udei_cohort_context()` (cached) and its wiring into `unmet_demand()` |
| `backend/app/agent/tools.py` | model view now carries `consistent_with`, `cannot_establish`, `source`, `threshold_note`, `shares_arithmetic_with`, `band_context`, `indicator_relationships`, `cohort_context`; drops the six duplicated top-level fields |
| `backend/app/agent/prompts.py` | three-part answer structure; band-with-counts rule; U2/U3 non-independence; panel-is-the-detail rule |
| `backend/app/agent/session.py` | `remember_prompt_numbers()` — the system prompt is model-visible, so its figures are quotable |
| `backend/app/agent/orchestrator.py` | `_auditable()` rewritten to be the model-visible projection (§10b) |
| `backend/tests/test_udei_transparency.py` | **new** — 48 tests |
| `backend/tests/test_temporal_integration.py` | `_auditable` tests updated for the new signature |
| `backend/tests/test_agent.py` | UDEI compact-view test updated for the optimised shape |
| `frontend/src/types.ts` | `EvidenceBand`, `UdeiCohortContext`; optional fields on `Indicator` and `UnmetDemandResult` |
| `frontend/src/components/panels.tsx` | `UnmetDemandPanel`: counts with the band, attainable ceiling, unavailable reasons, non-independence note, per-indicator consistent-with / cannot-establish / threshold / source, cohort context |
| `frontend/src/styles.css` | hierarchy for the stacked indicator sub-lines |
| `docs/design-document.md` | §5 UDEI — indicator disclosures, the identity, the band's comparability limit |
| `docs/phase-8.3b-udei-integration.md` | **new** — this document |

Also present from the audit, unchanged: `backend/evaluate_udei_evidence.py`,
`docs/phase-8.3-udei-evidence-evaluation.md`.

**Not changed:** UDEI band thresholds, `UDEI_COHORT_PERCENTILE`,
`UDEI_UPGAUGE_THRESHOLD`, TDPI, ACI, `classify()`, the divergence thresholds, the
analysis window, the ETL, or any dataset.

---

## 9. Before / after for SFO

| | before | after |
|---|---|---|
| Band | Weak | **Weak** (unchanged) |
| Triggered / available | 1 / 4 | **1 / 4** (unchanged) |
| Indicator outcomes | U1 ✓, U2 ✗, U3 ✗, U4 ✗, U5 — | **identical** |
| Unavailable count stated | no | **1, with U5's reason enumerated** |
| Attainable maximum stated | only inside `limitations` prose | **`max_attainable_triggered: 4`, `max_attainable_band: "Strong"`** |
| Band construction explained | no | **yes, with its comparability limit** |
| U2/U3 non-independence | no | **yes, with the verified identity** |
| U1 threshold explained | no | **yes, plus "2 of 30 large hubs", labelled and reproducible** |
| Per-indicator "consistent with" | computed, shown nowhere | **model + panel** |
| Per-indicator "cannot establish" | did not exist | **model + panel** |
| Per-indicator provenance | computed, shown nowhere | **model + panel** |
| Cohort calibration | no | **328/70/1 of 399, with a not-evidence guard** |
| Causal wording | U3 "classic slot/gate-constrained signature", U4 "hitting operational limits" | **removed; consistency language only** |

The substantive reading is unchanged and now legible: SFO fires one of four
indicators, and the three that did not fire are the ones a supply-constrained
airport would be expected to show.

---

## 10. Remaining limitations

1. **Frontend unverified by tooling** (§7). The likeliest residual failure is a
   TypeScript error, not a logic error.
2. **The band is still not comparable across airports.** It is *disclosed*, not
   fixed — as instructed, for compatibility. IAG at 3-of-3 still bands below ROC
   at 4-of-4 despite both being complete. Anyone aggregating or sorting by band
   across airports will still be comparing unlike things; the fields needed to
   avoid that are now present, but nothing forces their use.
3. **U5 remains unavailable for all 399 airports**, so no band in this system has
   ever included the one indicator that would observe a market's own pricing
   response. Ingesting BTS Consumer Airfare was explicitly out of scope here.
4. **Token cost rose ~4× for this one tool call** (§6). Justified by the
   requirements, but it is a real cost and the mitigations are partial.
5. **The +2% upgauging bar is still an unexamined judgement constant.** It is now
   *disclosed* as one; its sensitivity was not measured.
6. **`cannot_establish` and `threshold_note` are static text**, not computed per
   airport (except U1's class rank). They describe the indicator, so they cannot
   flag an airport-specific confounder such as a hub closure in the window.
7. **One window, one cohort.** SFO's figures are a single year; a Weak band this
   window is not a claim about other years.
8. **Nothing here measures unmet demand.** That remains unanswerable from these
   datasets, which is the audit's central finding and is unchanged.

---

## 10b. Final optimisation (Phase 8.3 acceptance pass)

### Model-view size

| tool | full payload | model view | reduction |
|---|---:|---:|---:|
| `unmet_demand_evidence` | 13,487 B | **3,287 B** | **76%** |
| `get_airport_profile` | 16,447 B | 3,092 B | 82% |
| `compare_airports` | 29,163 B | 2,778 B | 91% |
| `long_haul_breakdown` | 9,196 B | 4,627 B | 50% |
| `rank_airports` | 156,532 B | 13,240 B | 92% |

For UDEI specifically, across the phase:

| | model view |
|---|---:|
| Before Phase 8.3b | ~1,800 B |
| After 8.3b transparency work | 7,598 B |
| **After this optimisation** | **3,287 B** |

**−4,311 B, a 57% cut** against the 8.3b peak. At roughly 4 characters per token
that is about **1,100 tokens saved per unmet-demand call**, leaving the view
~1,400 tokens against the pre-transparency ~450. The residual increase buys the
band's comparability limit, the U2/U3 dependence, the causation limit and the
"weak is not absence" statement — none of which existed before.

### How the reduction was achieved

1. **One shared `limits` block replaces five per-indicator paragraphs.** The
   `cannot_establish` and `threshold_note` texts are identical on every call and
   describe the *indicator*, not the airport, so repeating them per row per call
   paid for the same words five times. They remain in full in the frontend
   payload, which is where the evidence table is read.
2. **Static explanations live in the system prompt**, which is cached once per
   conversation rather than re-sent per tool result: the three-part answer
   structure, the band rule, the U2/U3 dependence and the quantification
   refusal.
3. **Per-row fields the panel renders were dropped** from the model view: full
   `direction` (replaced by a one-line `evidence` phrase), `cannot_establish`,
   `threshold_note`, `source`, `shares_arithmetic_with`, `available` (derivable
   from `triggered is null`).
4. **The duplicated band fields were consolidated** into one `counts` block; the
   long `caveat` paragraph was dropped in favour of the imperative
   `reporting_requirement` plus `limits.quantification`, which state the same
   refusal in a fraction of the space.
5. **The brief forms moved out of the payload** into `UDEI_BRIEF_DIRECTIONS` and
   `UDEI_BRIEF_LIMITS` in `definitions.py`, read directly by the ToolBox — so
   the frontend response carries no model-view scaffolding.

### What the compact view still contains

Every item required by the brief, verified by test:

| required | where |
|---|---|
| Evidence band | `counts.band` |
| Triggered / available / unavailable counts | `counts` |
| Maximum attainable triggered count | `counts.max_attainable_triggered`, `max_attainable_band` |
| Individual U1–U5 outcomes and values | `indicators[]` — `triggered`, `value` |
| Missing indicator reasons, especially U5 | `indicators[].unavailable_reason` |
| Relevant thresholds | `indicators[].threshold` |
| Evidence direction | `indicators[].evidence` |
| Band comparability limitation | `limits.band` |
| U2/U3 arithmetic dependence | `limits.u2_u3_dependence` |
| Inability to quantify | `limits.quantification` + `reporting_requirement` |
| Observed vs interpretation vs unproven cause | `indicators[].value` / `.evidence` / `limits.causation` |
| Weak ≠ absent | `limits.weak_is_not_absence` (only on Weak/Indeterminate) |
| Cohort calibration with its guard | `cohort` |
| Pointer to the panel for detail | `detail` |

### Numeric provenance now checks only model-visible figures

`_auditable()` previously subtracted the one field known to be hidden. As the
frontend payload grew richer than the model view that stopped being reliable, so
the pool is now **derived from `compact_for_model` itself** — it cannot drift,
because it is the same function that builds what the model sees. The system
prompt's figures are admitted too, since the prompt is model-visible.

| tool | pool from full payload | pool from model view |
|---|---:|---:|
| `unmet_demand_evidence` | 164 | **57** |
| `get_airport_profile` | 396 | 218 |
| `compare_airports` | 621 | 133 |

The concrete case, now a test: `cohort_context.indicator_triggered_counts.U3` is
**115** in the frontend payload and absent from the model view. Before this
change the audit would have accepted a sentence claiming "115 airports" as
provenanced. It now rejects it, while still accepting "82.6%" and
"328 of 399" — figures the model was actually given.

Full precision is untouched for the frontend and for every calculation: a test
asserts U1's `value` still equals the engine's `load_factor` exactly while
`value_display` reads "82.6%".

### Regression results

| check | result |
|---|---|
| UDEI band distribution | **328 Weak / 70 Moderate / 1 Strong** — unchanged |
| Σ TDPI / Σ ACI | **16146.9211 / 10931.2699** — bit-identical |
| Divergence classes | 11 / 172 / 36 / 11 / 7 / 162 — unchanged |
| SFO | Weak, 1 of 4, U1 ✓ 82.6%, U2/U3/U4 ✗, U5 unavailable + reason |
| IAG (3/3) | Moderate, max 3 (Moderate), 2 unavailable |
| ROC (4/4) | Strong, max 4 (Strong), 1 unavailable |
| Q1 / Q2 / Q3 / Q4 | HVN·BOS·BGR / 37.9·36.7 / combi 888 / Weak 1-of-4 |
| Magnitude-shaped keys | none |
| Frontend payload fields | none lost |

### Frontend payload

Unchanged from Phase 8.3b apart from **one added field**,
`weak_is_not_absence` — the new information item 4 requires, rendered in the
panel. The model-view scaffolding that briefly lived there (`brief_limits`,
`direction_brief`) was removed, so the payload carries only evidence.

### Diff review

All 70 removed lines across 14 files were reviewed individually and are
intentional: the `_auditable` rewrite, the reworded `direction` texts, the
restructured compact view, and the tests that asserted the superseded shapes. No
secrets, no generated files, no environment files, no BOMs, no mojibake. The only
surviving occurrences of the old causal phrases are removed lines and the
comments explaining why they were removed.

---

## 11. Review checklist

- [x] Per-indicator direction and provenance in tool output **and** panel
- [x] Causal wording rewritten to evidence language; old phrases absent by test
- [x] Band shown with triggered / available / unavailable+reasons / attainable max
- [x] Absolute-count construction and its comparability limit explained
- [x] Cohort context added, with an explicit not-evidence guard
- [x] U2/U3 shared arithmetic disclosed, and the identity verified true
- [x] U1 threshold explained; within-class comparison labelled and reproducible
- [x] U5 preserved as unavailable with its original reason; no dataset added
- [x] Agent answer separates observation / consistency / missing information
- [x] Panel is the source of detail; prompt forbids retyping the table
- [x] Band classifications, TDPI and ACI unchanged; SFO still Weak 1/4
- [x] 3/3 vs 4/4 tested on real airports (IAG, ROC)
- [x] Full offline backend suite run — 532 passed
- [x] Frontend build **not** run — no Node here; must be verified on Windows
- [x] Design document updated
- [x] **Not committed, not pushed**
