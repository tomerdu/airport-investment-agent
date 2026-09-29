# Phase 8.2b — Integrating the ACI Temporal Diagnostic

**Status:** accepted; final corrections applied; committed locally, not pushed
**Window:** 2025-05 .. 2026-04 (unchanged)
**Tests:** 499 passed, 0 failed, 0 skipped — full offline backend suite
**Frontend build:** verified by the user on Windows (`tsc -b && vite build`), dev
server starts. Not runnable in this session — no Node runtime here (§8).
**External calls:** none. No Anthropic API, no downloads, no live LLM calls.

> **Acceptance.** Substantive behaviour accepted after manual testing of BOS, ACK
> and ASE. Five final corrections were then applied; they are recorded in §10 and
> changed no score, weight, threshold, cohort definition or the persistence
> methodology. Checksums re-confirmed identical afterwards.

Evaluation and evidence: `phase-8.2-aci-persistence-evaluation.md`.

---

## 1. What was integrated, and what was deliberately not

Approved from Phase 8.2, and implemented:

| approved | status |
|---|---|
| Retain ACI formula, weights, flight gate and 60/40 thresholds | unchanged; verified by checksum (§4) |
| Add worst-two-month concentration and monthly spread | done |
| Keep elevated share as secondary context, not a headline | done — last row of the summary table, never in the comparison header |
| Display temporal coverage, especially partial-year | done — `months_available` / `months_expected`, plus explicit uncertainty |
| Do not suppress ACK or MVY for partial coverage | done — both still report ACI and a divergence class |

Not added, per the same instruction: the diagnostic takes no part in TDPI, ACI,
UDEI, rankings or classification. `score_airport()` neither accepts nor returns
it; the engine attaches it *after* scoring, so it cannot influence the number it
explains. Three tests pin this, and one asserts `scoring.py` never mentions the
word `temporal`.

---

## 2. The API change

The smallest backward-compatible change: **one optional field with a default.**

```python
@dataclass(frozen=True)
class AirportScores:
    ...
    temporal: dict[str, Any] | None = None   # new, defaulted
```

Every existing constructor call still works, and every existing response keeps
its shape with `temporal: null` where nothing is attached. A test builds an
`AirportScores` the old way to prove it.

Where it appears:

| surface | field | contents |
|---|---|---|
| `GET /analytics/profile/{iata}` | `temporal` (top level) | full block including the 12 monthly rows |
| `GET /analytics/compare` | `airports[].temporal` | row summary, no monthly series |
| `get_airport_profile` tool | `scores.temporal` (full) → `aci_temporal` (model) | full for the panel, summary for the model |
| `compare_airports` tool | `airports[].temporal` → `aci_temporal` | as above |
| `GET /analytics/rank` | present but **always `null`** | rankings never consult it |

The two profile shapes differ because they already did: the REST route returns
`AirportScores` flat, while the tool payload nests it under `scores`. Nothing was
changed about that.

**The monthly series is loaded lazily.** `AnalyticsEngine._monthly_delay` is
populated on first use, so ranking — which never needs it — costs exactly what it
did before. A test asserts that `rank()` leaves it unloaded and `profile()`
populates it.

### Payload shape

```json
"temporal": {
  "months_available": 6, "months_expected": 12,
  "months_evaluated": 4, "months_unevaluated": 2,
  "coverage_complete": false,
  "temporal_pattern": "INSUFFICIENT_DATA",
  "pattern_label": "Not enough evaluable months",
  "description": "Only 4 month(s) carry enough reported flights to evaluate, ...",
  "concentration": {
    "worst_two_month_drop": null,
    "aci_excluding_worst_two": null,
    "reliable": false,
    "unreliable_reason": "insufficient_evaluable_months",
    "null_baseline_drop": 2.0
  },
  "monthly_spread": 49.6,
  "elevated_months": 3, "elevated_share": 0.75, "elevated_threshold": 60.0,
  "elevated_season": "summer-concentrated",
  "months": [{"month": "2025-05", "flights": 57, "aci": null,
              "evaluated": false, "elevated": false,
              "reason": "insufficient_monthly_flights"}, ...],
  "uncertainty": ["Only 6 of 12 window months are reported ...", ...],
  "notes": ["Diagnostic only. It does not change ACI ...", ...]
}
```

`null_baseline_drop` is carried deliberately: it is the control that makes the
concentration figure meaningful (removing two *middle* months moves the score by
a cohort median of −0.2 points, against +6.1 for the two worst), so a reviewer can
see the measure is behaviour rather than arithmetic.

---

## 3. The two naming and honesty requirements

### `temporal_pattern`, and INTERMITTENT rather than MIXED

The field is `temporal_pattern`, not `class`, and its intermediate value is
**INTERMITTENT**. Phase 8.2 used MIXED internally, which collides with the
divergence class of the same name; that value no longer exists anywhere in the
diagnostic. A test asserts the string `MIXED` never appears as a
`temporal_pattern`, and the comparison panel states in copy that "Partly
concentrated" is unrelated to the MIXED class.

| `temporal_pattern` | label | meaning |
|---|---|---|
| PERSISTENT | Sustained across the window | elevated in most measured months, and the concentration measure confirms it |
| PERSISTENT *at the ceiling* | Elevated in most months (concentration unmeasurable) | see §10.3 — the label rests on the elevated count alone |
| EPISODIC | Concentrated in a few months | worst two months carry the score |
| INTERMITTENT | Partly concentrated | between the two; read the monthly figures |
| INTERMITTENT *with no elevated month* | No elevated months | see §10.2 — variation around a level that never became elevated |
| INSUFFICIENT_DATA | Not enough evaluable months | too few evaluable months to characterise |

### The ceiling case is never shown as stability

ASE scores ACI 100.0. Winsorization clips values above the cohort P95, so removing
its worst months cannot lower the score — the drop computes as +0.0, which would
read as perfect stability. It is instead reported as **unmeasurable, with a
reason**:

```json
"concentration": { "worst_two_month_drop": null, "reliable": false,
                   "unreliable_reason": "score_at_cohort_ceiling" }
```

and the description appends *"The score is at the cohort ceiling, so the
worst-two-month effect cannot be measured and must not be read as stability."*
The panel shows "not measurable" with a caution box; the model receives
`concentration_unavailable: "score_at_cohort_ceiling"` and is instructed never to
present it as stability. ASE's monthly spread (59.6 points) is still shown, so the
underlying variation remains visible.

### Timing, never cause

`elevated_season` yields values like `"summer-concentrated"` and
`"spread across the year"` — it names months, never a mechanism. A test asserts
the words *weather*, *storm*, *snow*, *fog*, *because of*, *caused by* appear in
no description, uncertainty note or note across all seven attention airports. The
system prompt says explicitly: say "winter-concentrated operational pressure",
never "caused by winter weather".

---

## 4. Before / after — production is unchanged

Checksums measured in Phase 8.2, before any integration, and asserted as tests:

| | before | after |
|---|---:|---:|
| Σ ACI across the cohort | 10931.2699 | **10931.2699** |
| Σ TDPI across the cohort | 16146.9211 | **16146.9211** |

Divergence classes, asserted for eight airports including all four scenario
subjects: SFO MIXED, LAX TERMINAL_LED, SNA TERMINAL_LED, ANC NO_NEAR_TERM_CASE,
BOS MIXED, BGR MIXED, ACK AIRSIDE_LED, MVY AIRSIDE_LED — **all unchanged**.

`GET /analytics/rank?region=new_england` returns 16 ranked rows with
`temporal: null` on every one, and ACI ordering unchanged.

### The seven attention airports

| apt | ACI | class | pattern | months | worst-2 drop | spread |
|---|---:|---|---|---:|---:|---:|
| BOS | 79.5 | MIXED | **PERSISTENT** | 12/12 | −6.8 | 52.6 |
| BGR | 78.9 | MIXED | **PERSISTENT** | 12/12 | −5.7 | 63.1 |
| ACK | 83.4 | AIRSIDE_LED | **INSUFFICIENT_DATA** | **4/12** (6 reported) | not measurable | 49.6 |
| MVY | 77.4 | AIRSIDE_LED | **INSUFFICIENT_DATA** | **4/12** (6 reported) | not measurable | 39.4 |
| LAX | 37.9 | TERMINAL_LED | INTERMITTENT | 12/12 | −2.6 | 28.4 |
| SNA | 36.7 | TERMINAL_LED | INTERMITTENT | 12/12 | −3.9 | 38.2 |
| ASE | 100.0 | AIRSIDE_LED | PERSISTENT | 11/12 | **not measurable (ceiling)** | 59.6 |

ACK and MVY keep their scores and classes, as approved, with three uncertainty
statements each. ASE is the ceiling case. LAX and SNA, with zero elevated months,
get a description that says so plainly rather than implying partial pressure.

---

## 5. A regression I introduced and fixed

> ### ⚠ SUPERSEDED — implementation only, not the finding
>
> **The `_auditable()` implementation described in this section no longer
> exists.** It stripped the monthly series *by name*. In **Phase 8.3** that
> approach was replaced: as the frontend payload grew richer than the model view,
> subtracting named fields stopped being reliable, so `_auditable()` now returns
> `ToolBox.compact_for_model(...)` — the model-visible projection by
> construction, which cannot drift
> (`backend/app/agent/orchestrator.py:54-73`).
>
> The finding below — that adding the monthly series widened the audit pool by
> ~40% with data the model never sees — is unchanged and still the reason the
> filter exists. Only the mechanism changed. Current behaviour:
> `docs/phase-8.3b-udei-integration.md` §10b.
>
> The original text is kept below unedited, as the record of what was done at the
> time.

Adding twelve monthly rows to the tool payload **weakened the numeric provenance
audit**, which accepts any number reachable in a remembered payload:

| profile | pool without diagnostic | with it | after the fix |
|---|---:|---:|---:|
| BOS | 280 | 393 (+40%) | **303** |
| LAX | 265 | 377 (+42%) | **281** |
| ACK | 257 | 308 (+19%) | **277** |

Roughly 90 numbers per profile came from the monthly series — data the model is
never shown, since the panel renders it. Admitting them would let the audit
"provenance" figures the model could not have read.

`_auditable()` in the orchestrator strips the withheld series before numbers are
remembered. The remaining increase is exactly the summary figures the model does
receive, which should be auditable. The full payload still reaches the frontend
untouched; a test asserts `_auditable()` does not mutate it and that a sentence
quoting real temporal figures still passes the audit.

Worth stating plainly: the audit is a **provenance** check, not a semantic one. It
verifies a number exists in the data, not that it is the right number for the
sentence — a wrong-but-present figure still passes. Narrowing the pool to what the
model was actually shown is what keeps the check meaningful; it does not make it a
correctness proof.

---

## 6. The four exam scenarios

Run offline through the same `ToolBox` the agent uses. No LLM calls.

| scenario | tool | result | token reduction |
|---|---|---|---:|
| Q1 New England | `rank_airports` | 16 ranked, top 3 HVN/BOS/BGR — unchanged; `temporal` null in all rows | 93% |
| Q2 LAX vs SNA | `compare_airports` | both INTERMITTENT; volume 189,899 vs 45,464 with spread 28.4 vs 38.2 | 91% |
| Q3 Anchorage | `long_haul_breakdown` | 4 scopes; no `temporal` field — unrelated to delay | 50% |
| Q4 SFO | `unmet_demand_evidence` | 5 indicators; no `temporal` field | 73% |
| Profile (BOS) | `get_airport_profile` | 12 monthly rows to the panel, summary to the model | 83% |

The 83% figure for the profile is unchanged from Phase 4, so the diagnostic did
not undo the token optimisation: the monthly series goes to the panel only.

**Q1 gains the most.** The regional ACI column previously read as a flat ranking.
It now separates BOS/BGR (sustained, 12/12 months) from ACK/MVY — the region's
only two AIRSIDE_LED airports, each with four evaluable months of six reported.
That distinction is the main practical benefit of this phase.

Q3 and Q4 are untouched by construction: neither consults delay data.

---

## 7. Files changed

| file | change |
|---|---|
| `backend/app/analytics/persistence.py` | `temporal_pattern` (INTERMITTENT replaces MIXED), `pattern_label`, `description`, `elevated_season`, `at_ceiling`, `concentration_reliable`, `no_elevated_months`, `uncertainty`, `coverage_complete`, `to_dict()`, `CEILING_ACI`, `MONTHS_EXPECTED` |
| `backend/app/analytics/definitions.py` | corrected `DIVERGENCE_READINGS["MIXED"]` (§10.1) — no weight, threshold or component touched |
| `backend/app/analytics/models.py` | `AirportScores.temporal` — optional, defaulted |
| `backend/app/analytics/engine.py` | lazy `monthly_delay()`, `temporal_diagnostic()`, attach in `profile()`, row summary in `compare()` via `_temporal_summary()` |
| `backend/app/agent/tools.py` | `_temporal()` compact view; `aci_temporal` on profile and compare; profile tool description; **fixed a pre-existing mojibake character** in a comment (a U+FFFD replacement character where an em-dash belonged, in the `get_airport_profile` branch) |
| `backend/app/agent/prompts.py` | new `aci_temporal` section — when not why, INTERMITTENT ≠ MIXED, ceiling rule, partial-coverage rule |
| `backend/app/agent/orchestrator.py` | `_auditable()` — keeps the withheld monthly series out of the audit pool (§5) |
| `backend/app/agent/tools.py` (precision) | `_r_score()` / `_r_minutes()` narrative precision policy (§10.4) |
| `backend/tests/test_aci_persistence.py` | replaced the Phase 8.2 isolation test with three sharper ones (scoring, ranking, attach-after-scoring) |
| `backend/tests/test_temporal_integration.py` | **new** — 39 integration tests, 11 of them covering the final corrections |
| `backend/tests/test_agent.py` | one pre-existing precision tolerance updated for §10.4, now also asserting the full-precision payload |
| `frontend/src/components/primitives.tsx` | corrected `CLASS_META.MIXED.blurb` (§10.1) |
| `frontend/src/types.ts` | `TemporalPattern`, `TemporalMonth`, `TemporalConcentration`, `TemporalDiagnostic`, `TemporalSummary`; optional fields on `AirportScores` and `CompareAirport` |
| `frontend/src/components/panels.tsx` | `PATTERN_META`, `TemporalPanel`; collapsible section in `ProfilePanel` (open by default when coverage is partial); four temporal rows plus caveats in `ComparePanel` |
| `frontend/src/styles.css` | temporal panel, badge, monthly bar and muted styles — built from existing tokens, so dark mode follows automatically |
| `docs/design-document.md` | §5 ACI temporal diagnostic subsection; audit-pool paragraph in §8 |
| `docs/phase-8.2-aci-persistence-evaluation.md` | outcome banner pointing here |
| `docs/phase-8.2b-aci-temporal-integration.md` | **new** — this document |

**Not changed:** ACI and TDPI weights, `MIN_OTP_FLIGHTS_FOR_ACI`,
`DIVERGENCE_HI`/`DIVERGENCE_LO`, `classify()`, `score_airport()`, `_compose()`,
the analysis window, UDEI, `rank()`'s logic, or the ETL.

One drive-by fix is listed above and flagged here rather than buried: a single
replacement character in a `tools.py` comment, committed in an earlier phase,
repaired while editing that function.

---

## 8. Verification — including what could not be verified

### Backend

```
488 passed in 55.66s
```

Full offline suite (458 from Phase 8.2 + 28 new integration tests + 2 replacing
1 obsolete). Mocked LLM clients, `-m "not live"`. **No live API calls.**

Also verified directly against the running app via `TestClient`:
`/analytics/profile/{BOS,ACK,ASE}`, `/analytics/compare`, `/analytics/rank`,
`/health` — all 200, with coverage and ceiling fields present as specified.

### Frontend — not verified, and I am not claiming otherwise

**`npm run build` was not run. There is no Node runtime on this machine** —
`node`, `npm`, `npx`, `pnpm` and `yarn` are all absent from PATH, and a recursive
search of Program Files, LocalAppData, AppData and the user profile found no
`node.exe`. Consequently:

- **`tsc -b` (typecheck) did not run.** The TypeScript changes are unverified by a
  compiler.
- **`vite build` did not run.** The existing `frontend/dist` artifacts date from
  2026-09-28 and predate these changes.
- **No frontend tests were added.** No runner is installed (`vitest`, `jest` and
  `@testing-library/react` are all absent) and installing one needs network
  access, which this phase prohibits. Adding an unrunnable test file would have
  been worse than none.

What I did instead, and what it is worth:

1. **Verified every imported signature by hand** against `primitives.tsx` —
   `fmtScore`/`fmtNum` accept `number | null | undefined`; `Term` takes
   `k: string` plus children. This caught a real bug: I had written `k="aci"`
   where the glossary keys are uppercase, which would have silently dropped the
   tooltip. Fixed to `k="ACI"`.
2. **Delimiter-balance check on the added lines.** `panels.tsx` +310 lines:
   `()` 73/73, `[]` 4/4, `{}` 105/105. `types.ts` and `styles.css` likewise
   balanced. A whole-file checker flagged a line — but it flags the *committed*
   file identically (JSX apostrophes defeat it), so that signal is a checker
   limitation, not a defect.
3. **Confirmed the data contract end to end** against the real payloads, which is
   what the panel consumes: the frontend reads `call.result` from
   `reply.tool_calls`, i.e. the full tool payload, so `ProfilePanel`'s
   `scores.temporal` and `ComparePanel`'s row-level `temporal` both resolve.

Balanced delimiters and hand-checked signatures are **not** a substitute for a
typecheck. Please run `npm run build` in `frontend/` on a machine with Node before
relying on the UI.

---

## 9. Limitations

1. **Frontend unverified by tooling** (§8). The most likely residual failure mode
   is a TypeScript type error, not a logic error.
2. **The pattern thresholds are cohort-calibrated** — the 8.0-point concentration
   cut is this cohort's p75. On a different window it would need recalibrating,
   and the label counts move with the cut (Phase 8.2 §8). The panel therefore
   always shows the underlying numbers beside the label.
3. **The concentration measure saturates at both ends** of the winsorized scale.
   The ceiling case is detected and disclosed; compression near the floor is not
   separately flagged.
4. **Small-airport volatility is part real, part sampling**, and this data cannot
   separate them. Mitigated by always showing monthly flight counts, not removed.
5. **No ground truth.** "PERSISTENT" is not validated against any outcome.
6. **Neither ACI nor this diagnostic measures capacity.** Both describe observed
   delay outcomes. Nothing here identifies a runway, gate or airspace cause, and
   no value on either is evidence that a constraint is binding.

---

## 10. Final corrections after acceptance

Five presentational corrections. **No score, weight, threshold, cohort definition
or persistence method changed** — checksums re-verified identical (§4).

### 10.1 The MIXED explanation was factually wrong

The frontend read *"Both indices fall in the middle band"*, which contradicted the
two figures printed directly above it: **BOS is MIXED with TDPI 58.6 and ACI
79.5** — the ACI is not mid-range at all.

MIXED is in fact the residual class: it holds whenever **at least one** index sits
in the intermediate 40–60 band, so the pair matches none of the four corner
profiles. All three explanations now say that, and say that the label combines a
demand-side and an airside signal without distinguishing them:

| place | before | after |
|---|---|---|
| `definitions.py` `DIVERGENCE_READINGS["MIXED"]` (API + model + panel) | "Indices fall in the middle band" | "At least one of the two indices sits in the intermediate band (40–60) … it does NOT mean both scores are mid-range" |
| `primitives.tsx` `CLASS_META.MIXED.blurb` | "Both indices fall in the middle band" | same correction, ending "Read the two scores above rather than the label" |
| `prompts.py` | "MIXED — middle band" | states the rule, cites BOS's real 58.6 / 79.5, and forbids describing a MIXED airport as having both indices mid-range |

`glossary.ts` already said "One or both indices" and needed no change.

### 10.2 INTERMITTENT with no elevated month

LAX is INTERMITTENT at ACI 37.9 with **zero** months over the threshold. "Partly
concentrated" implied intermittent congestion where there had never been elevated
pressure. A derived flag `no_elevated_months` now travels with the payload; where
it is set, the label becomes **"No elevated months"** and the copy says the values
vary around a level that never became elevated. The prompt carries the same rule.
Airports that do have elevated months keep the ordinary labels — ATW, INTERMITTENT
with 6 of 12, still reads "Partly concentrated".

### 10.3 ASE's label no longer borrows an unavailable result

The PERSISTENT description ended *"the annual score does not depend on a few
months"* — which **is** the concentration result, and at the ceiling that result
could not be measured. Contradictory in the same sentence as the ceiling caveat.

At the ceiling the description now reads: *"…This pattern rests on the count of
elevated months alone: the score is at the cohort ceiling, so the
worst-two-month effect cannot be measured, and its absence must not be read as
stability. The monthly spread is 59.6 points."* The label becomes **"Elevated in
most months (concentration unmeasurable)"**. Non-ceiling PERSISTENT airports now
cite their measured drop explicitly instead ("lowers the annual score by only 6.8
points, so it does not depend on a few months").

### 10.4 Narrative numeric precision

The model view rounded everything to four decimals, so prose could read "ACI
79.4545". Precision is now matched to the quantity, in the **model view only**:

| quantity | model view | rationale |
|---|---|---|
| index scores, normalised components, percentiles, monthly ACI, point drops | 1 dp | cohort-relative positions; more is false precision |
| minutes | 2 dp | 18.52, not 18.5234 |
| rates and ratios | 4 dp (unchanged) | a 0.0105 cancellation rate needs them |
| coverage | 2 dp | 0.70 |

`_r_score()` and `_r_minutes()` implement it. **Full precision is preserved in the
frontend payload and in every calculation** — a test asserts the payload score
still differs from its 1-dp rounding while the compact view matches it. The
numeric audit permits rounding, so narrative figures still pass; a test covers
that too. The system prompt now states the formatting policy explicitly.

One pre-existing test asserted the compact score to 1e-3 and was updated: its
intent (no headline number lost) holds at reading precision, and it now also
asserts the full-precision payload alongside.

### 10.5 Diff hygiene

Every one of the 18 deleted lines across the diff was reviewed and is
intentional. `frontend/dist/`, `frontend/.env.local`, `.env`, `__pycache__/`,
`.venv/` and `node_modules/` are all gitignored and excluded.
`frontend/vite.config.ts` is untouched (still proxying to 8000, per Phase 8.1's
decision). No BOMs and no replacement characters remain in any changed file.

---

## 11. Review checklist

- [x] `persistence.py` and the evaluation report reviewed before integrating
- [x] Smallest backward-compatible API change — one optional defaulted field
- [x] Structured frontend panels; no numeric value parsed from model prose
- [x] Coverage, concentration, spread, description and explicit uncertainty all present
- [x] `temporal_pattern` distinct from `divergence_class`; intermediate label INTERMITTENT
- [x] Ceiling case reported as unmeasurable with a reason, never as stability
- [x] Descriptive seasonality only; no weather causality, enforced by test
- [x] Available to the agent; excluded from TDPI, ACI, UDEI, rankings and classification
- [x] System prompt updated only where needed
- [x] Backend tests added (28); **frontend tests not feasible — see §8**
- [x] ACI scores and all classifications confirmed unchanged
- [x] Four exam scenarios run offline
- [x] BOS, BGR, ACK, MVY, LAX, SNA, ASE all checked
- [x] Partial coverage and ceiling limits displayed
- [x] Full offline backend suite run — 488 passed; **frontend build NOT run (no Node)**
- [x] Design document and evaluation report updated
- [x] **Not committed, not pushed**
