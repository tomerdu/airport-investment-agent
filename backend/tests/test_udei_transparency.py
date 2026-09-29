"""Phase 8.3b — UDEI evidence transparency.

Two things are pinned:
  * the disclosure is present, reaches the model, and uses evidence language;
  * the band classification, the thresholds and every score are UNCHANGED.

No network, no API calls.
"""

from __future__ import annotations

import pytest

from app.agent.tools import ToolBox
from app.analytics import AnalyticsEngine
from app.analytics import definitions as D
from app.analytics.unmet import unmet_demand_evidence

CAUSAL_PHRASES = (
    "classic slot/gate-constrained signature",
    "hitting operational limits",
    "airlines adding seats they cannot add",
    "supply-constrained market pricing",
)


@pytest.fixture(scope="module")
def engine():
    e = AnalyticsEngine()
    yield e
    e.close()


@pytest.fixture(scope="module")
def sfo(engine):
    return engine.unmet_demand("SFO")


# ---------------------------------------------------------------------------
# REGRESSION — nothing about the classification moved
# ---------------------------------------------------------------------------


def test_band_thresholds_and_constants_unchanged():
    assert D.UDEI_COHORT_PERCENTILE == 75.0
    assert D.UDEI_UPGAUGE_THRESHOLD == 0.02


def test_sfo_remains_weak_with_one_of_four(sfo):
    assert sfo.evidence_band == "Weak"
    assert sfo.triggered_count == 1
    assert sfo.available_count == 4
    assert sfo.total_count == 5


def test_sfo_indicator_outcomes_unchanged(sfo):
    fired = {i.id: i.triggered for i in sfo.indicators}
    assert fired == {"U1": True, "U2": False, "U3": False, "U4": False,
                     "U5": None}


def test_cohort_band_distribution_unchanged(engine):
    """Measured before the change, in the Phase 8.3 audit."""
    ctx = engine._udei_cohort_context()
    assert ctx["band_counts"] == {"Weak": 328, "Moderate": 70, "Strong": 1}
    assert ctx["cohort_size"] == 399


def test_tdpi_and_aci_checksums_unchanged(engine):
    from app.analytics.scoring import compute_aci, compute_tdpi

    cohort = engine.cohort()
    assert sum(compute_aci(m, cohort).score or 0 for m in cohort.members) == \
        pytest.approx(10931.2699, abs=1e-3)
    assert sum(compute_tdpi(m, cohort).score or 0 for m in cohort.members) == \
        pytest.approx(16146.9211, abs=1e-3)


def _all_keys(obj, out=None):
    out = set() if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k).lower())
            _all_keys(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _all_keys(v, out)
    return out


def test_no_magnitude_field_was_introduced(sfo):
    """Checks KEYS, not prose.

    The limitations text legitimately contains the word "magnitude" — as in "no
    magnitude of unmet passengers exists in this data". A substring search over
    the whole payload therefore false-positives on the very sentence that
    forbids the thing. What matters is that no FIELD could hold such a value.
    """
    keys = _all_keys(sfo.to_dict())
    for bad in ("unmet_passengers", "unmet_flights", "magnitude",
                "missing_passengers", "shortfall", "estimated_demand",
                "unserved", "estimate"):
        assert bad not in keys, f"a magnitude-shaped field appeared: {bad}"
    assert not hasattr(sfo, "magnitude")

    # And no numeric field anywhere is plausibly a passenger/flight magnitude:
    # every number in the structure is an indicator value, a threshold or a count.
    numeric_keys = {
        k for k, v in sfo.to_dict().items() if isinstance(v, (int, float))
    }
    assert numeric_keys <= {
        "triggered_count", "available_count", "total_count",
        "unavailable_count", "max_attainable_triggered",
    }, f"unexpected numeric top-level field: {numeric_keys}"


def test_u5_still_unavailable_with_its_original_reason(engine, sfo):
    u5 = next(i for i in sfo.indicators if i.id == "U5")
    assert u5.available is False
    assert u5.triggered is None
    assert "Not ingested in the current warehouse" in u5.unavailable_reason
    # ...and everywhere, not just at SFO.
    cohort = engine.cohort()
    for m in cohort.members[:40]:
        ev = unmet_demand_evidence(m, cohort, window=engine.window)
        assert next(i for i in ev.indicators if i.id == "U5").available is False


# ---------------------------------------------------------------------------
# Evidence language, not causal language
# ---------------------------------------------------------------------------


def test_no_indicator_asserts_a_cause(engine):
    cohort = engine.cohort()
    for code in ("SFO", "JFK", "EWR", "ASE", "ACK"):
        m = engine.get_metrics(code)
        ev = unmet_demand_evidence(m, cohort, window=engine.window)
        for i in ev.indicators:
            text = f"{i.direction} {i.cannot_establish} {i.threshold_note}".lower()
            for phrase in CAUSAL_PHRASES:
                assert phrase not in text, f"{code}/{i.id} asserts a cause: {phrase}"


def test_directions_use_consistency_wording(sfo):
    for i in sfo.indicators:
        assert i.direction, f"{i.id} has no direction text"
        assert "consistent with" in i.direction.lower(), i.id


def test_every_indicator_states_what_it_cannot_establish(sfo):
    for i in sfo.indicators:
        assert i.cannot_establish, f"{i.id} has no cannot_establish text"
        assert "cannot establish" in i.cannot_establish.lower() or \
            "establishes nothing" in i.cannot_establish.lower(), i.id


def test_every_indicator_carries_provenance(sfo):
    for i in sfo.indicators:
        assert i.source and len(i.source) > 5, i.id


# ---------------------------------------------------------------------------
# Band disclosure
# ---------------------------------------------------------------------------


def test_band_reports_counts_and_attainable_maximum(sfo):
    assert sfo.unavailable_count == 1
    assert sfo.max_attainable_triggered == 4
    assert sfo.max_attainable_band == "Strong"
    assert "absolute counts" in sfo.band_definition.lower()
    assert "not fully comparable" in sfo.band_comparability_note.lower()


def test_unavailable_reasons_are_enumerated_with_ids(sfo):
    assert sfo.unavailable_reasons
    for entry in sfo.unavailable_reasons:
        assert entry["id"] and entry["label"] and entry["reason"]
    assert {e["id"] for e in sfo.unavailable_reasons} == {"U5"}


def test_every_unavailable_indicator_has_a_reason(engine):
    cohort = engine.cohort()
    for m in cohort.members[:60]:
        ev = unmet_demand_evidence(m, cohort, window=engine.window)
        for i in ev.indicators:
            if not i.available:
                assert i.unavailable_reason, f"{m.iata}/{i.id} unavailable, no reason"
        assert ev.unavailable_count == sum(
            1 for i in ev.indicators if not i.available)


def test_limitations_carry_the_comparability_and_arithmetic_notes(sfo):
    joined = " ".join(sfo.limitations)
    assert "not fully comparable" in joined
    assert "decomposes exactly" in joined


# ---------------------------------------------------------------------------
# The 3-of-3 vs 4-of-4 asymmetry is disclosed, not silently applied
# ---------------------------------------------------------------------------


def _find(engine, avail: int):
    cohort = engine.cohort()
    for m in cohort.members:
        ev = unmet_demand_evidence(m, cohort, window=engine.window)
        if ev.available_count == avail:
            return ev
    return None


def test_three_available_airport_cannot_reach_strong(engine):
    ev = _find(engine, 3)
    assert ev is not None, "no airport with exactly 3 evaluable indicators"
    assert ev.max_attainable_triggered == 3
    assert ev.max_attainable_band == "Moderate"
    assert "cannot reach Strong" in ev.band_comparability_note


def test_four_available_airport_can_reach_strong(engine):
    ev = _find(engine, 4)
    assert ev is not None
    assert ev.max_attainable_triggered == 4
    assert ev.max_attainable_band == "Strong"


def test_equal_completeness_bands_differently_and_that_is_disclosed(engine):
    """IAG fires 3 of 3, ROC fires 4 of 4 — both 100% of what was measurable.

    The band still separates them (Moderate vs Strong): the classification is
    preserved deliberately for compatibility. What is new is that both results
    now carry the ceiling, so a reader can see the difference is data coverage
    rather than evidence strength.
    """
    iag = engine.unmet_demand("IAG")
    roc = engine.unmet_demand("ROC")

    assert (iag.triggered_count, iag.available_count) == (3, 3)
    assert (roc.triggered_count, roc.available_count) == (4, 4)

    # Classification unchanged...
    assert iag.evidence_band == "Moderate"
    assert roc.evidence_band == "Strong"

    # ...and the reason is now visible on both.
    assert iag.max_attainable_triggered == 3
    assert iag.max_attainable_band == "Moderate"
    assert iag.unavailable_count == 2
    assert roc.max_attainable_triggered == 4
    assert roc.max_attainable_band == "Strong"
    assert roc.unavailable_count == 1

    # IAG reached its own ceiling; its band is not evidence of weaker signal.
    assert iag.triggered_count == iag.max_attainable_triggered
    assert "cannot reach Strong" in iag.band_comparability_note


def test_iag_names_both_missing_indicators(engine):
    iag = engine.unmet_demand("IAG")
    missing = {e["id"] for e in iag.unavailable_reasons}
    assert missing == {"U4", "U5"}
    reasons = {e["id"]: e["reason"] for e in iag.unavailable_reasons}
    assert "ACI suppressed" in reasons["U4"]
    assert "Not ingested" in reasons["U5"]


# ---------------------------------------------------------------------------
# U2/U3 shared arithmetic
# ---------------------------------------------------------------------------


def test_growth_indicators_declare_their_shared_arithmetic(sfo):
    by = {i.id: i for i in sfo.indicators}
    assert set(by["U2"].shares_arithmetic_with) == {"U1", "U3"}
    assert set(by["U3"].shares_arithmetic_with) == {"U1", "U2"}
    assert set(by["U1"].shares_arithmetic_with) == {"U2", "U3"}
    assert by["U4"].shares_arithmetic_with == ()


def test_arithmetic_relationship_is_stated(sfo):
    assert sfo.indicator_relationships
    text = " ".join(sfo.indicator_relationships).lower()
    assert "decomposes exactly" in text
    assert "not independent confirmations" in text


def test_the_identity_actually_holds_for_sfo(engine):
    """The disclosure must be arithmetically true, not just asserted."""
    m = engine.get_metrics("SFO")
    lf_prior = m.passengers_prior / m.seats_prior
    lf_growth = m.load_factor / lf_prior - 1.0
    lhs = 1 + m.pax_growth
    rhs = (1 + m.departure_growth) * (1 + m.gauge_growth) * (1 + lf_growth)
    assert lhs == pytest.approx(rhs, abs=1e-9)


# ---------------------------------------------------------------------------
# U1 threshold disclosure
# ---------------------------------------------------------------------------


def test_u1_discloses_the_cohort_relative_threshold(sfo):
    u1 = next(i for i in sfo.indicators if i.id == "U1")
    note = u1.threshold_note.lower()
    assert "whole cohort" in note
    assert "hub class" in note


def test_u1_within_class_context_is_labelled_and_reproducible(sfo):
    u1 = next(i for i in sfo.indicators if i.id == "U1")
    note = u1.threshold_note
    assert "Within hub class L" in note
    assert "ranks 2 of 30" in note
    assert "Reproduce by ranking load_factor" in note
    assert "Context only" in note
    assert "trigger is unchanged" in note


def test_u1_class_context_omitted_when_the_class_is_too_small(engine):
    """No within-class claim on a handful of peers."""
    cohort = engine.cohort()
    m = engine.get_metrics("SFO")
    tiny = [p for p in cohort.members if p.hub_class == m.hub_class][:3]
    small_cohort = type(cohort)("tiny", tiny + [m])
    ev = unmet_demand_evidence(m, small_cohort, window="w")
    u1 = next(i for i in ev.indicators if i.id == "U1")
    assert "Within hub class" not in u1.threshold_note


# ---------------------------------------------------------------------------
# Cohort context is calibration, never evidence
# ---------------------------------------------------------------------------


def test_cohort_context_is_present_and_disclaimed(sfo):
    ctx = sfo.cohort_context
    assert ctx is not None
    assert ctx["band_counts"]["Weak"] == 328
    note = ctx["note"].lower()
    assert "not evidence" in note
    assert "rare band is not a stronger finding" in note


def test_cohort_context_is_cached(engine):
    a = engine._udei_cohort_context()
    b = engine._udei_cohort_context()
    assert a is b


def test_direct_call_without_cohort_context_still_works(engine):
    """The engine supplies it; the function must not require it."""
    cohort = engine.cohort()
    ev = unmet_demand_evidence(engine.get_metrics("SFO"), cohort, window="w")
    assert ev.cohort_context is None
    assert ev.evidence_band == "Weak"


# ---------------------------------------------------------------------------
# Agent exposure
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sfo_compact(engine):
    tb = ToolBox(engine)
    return tb.compact_for_model(
        "unmet_demand_evidence", tb.call("unmet_demand_evidence", {"iata": "SFO"}))


def test_model_receives_every_indicator_outcome_and_a_brief_direction(sfo_compact):
    got = {i["id"]: i for i in sfo_compact["indicators"]}
    assert set(got) == {"U1", "U2", "U3", "U4", "U5"}
    assert got["U1"]["triggered"] is True and got["U1"]["value"] == "82.6%"
    assert got["U2"]["triggered"] is False
    assert got["U3"]["triggered"] is False
    assert got["U4"]["triggered"] is False
    assert got["U5"]["triggered"] is None
    for i in got.values():
        assert i["evidence"], f"{i['id']} has no evidence direction"
        assert i["threshold"], f"{i['id']} has no threshold"
        assert "consistent with" in i["evidence"].lower()


def test_model_receives_counts_and_attainable_maximum(sfo_compact):
    c = sfo_compact["counts"]
    assert c["band"] == "Weak"
    assert (c["triggered"], c["available"], c["unavailable"], c["defined"]) == \
        (1, 4, 1, 5)
    assert c["max_attainable_triggered"] == 4
    assert c["max_attainable_band"] == "Strong"


def test_model_receives_u5_reason(sfo_compact):
    u5 = next(i for i in sfo_compact["indicators"] if i["id"] == "U5")
    assert "Not ingested in the current warehouse" in u5["unavailable_reason"]


def test_model_receives_one_shared_limits_block(sfo_compact):
    limits = sfo_compact["limits"]
    for key in ("band", "u1_threshold", "u2_u3_dependence", "causation",
                "quantification", "weak_is_not_absence"):
        assert limits.get(key), f"missing shared limit: {key}"
    assert "not comparable across airports" in limits["band"]
    assert "not independent confirmations" in limits["u2_u3_dependence"]
    assert "cannot establish a cause" in limits["causation"]
    assert "Never state or imply one" in limits["quantification"]
    assert "not evidence of absence" in limits["weak_is_not_absence"].lower()


def test_shared_limits_replace_per_indicator_paragraphs(sfo_compact):
    """The long per-indicator caveats belong in the panel, not on every call."""
    for i in sfo_compact["indicators"]:
        assert "cannot_establish" not in i
        assert "threshold_note" not in i
        assert "source" not in i
        assert "shares_arithmetic_with" not in i


def test_model_receives_cohort_calibration_with_its_guard(sfo_compact):
    c = sfo_compact["cohort"]
    assert c["size"] == 399
    assert c["bands"]["Weak"] == 328
    assert "not evidence" in c["note"]


def test_model_view_carries_no_duplicated_band_fields(sfo_compact):
    for dup in ("band_definition", "band_comparability_note",
                "max_attainable_triggered", "max_attainable_band",
                "unavailable_count", "unavailable_reasons",
                "indicator_relationships", "cohort_context", "brief_limits",
                "weak_is_not_absence", "evidence_band", "triggered_count",
                "available_count", "total_count"):
        assert dup not in sfo_compact, f"{dup} duplicated at top level"


def test_model_keeps_the_imperative_not_to_quantify(sfo_compact):
    """The long `caveat` paragraph moved to the panel and the prompt.

    Its substance must survive in the model view, in two places: the imperative
    reporting requirement and the shared quantification limit.
    """
    assert "Do NOT state or imply a number" in sfo_compact["reporting_requirement"]
    assert "none is produced" in sfo_compact["limits"]["quantification"]


def test_full_payload_still_carries_the_caveat(engine):
    """Dropping it from the MODEL view must not drop it from the frontend."""
    tb = ToolBox(engine)
    raw = tb.call("unmet_demand_evidence", {"iata": "SFO"})
    assert "NOT a measurement of unmet demand" in raw["caveat"]


def test_model_view_is_substantially_smaller_than_the_payload(engine):
    import json

    tb = ToolBox(engine)
    raw = tb.call("unmet_demand_evidence", {"iata": "SFO"})
    compact = tb.compact_for_model("unmet_demand_evidence", raw)
    full_b = len(json.dumps(raw, default=str))
    small_b = len(json.dumps(compact, default=str))
    assert small_b < 4000, f"model view regressed to {small_b}B"
    assert small_b < full_b / 3, (
        f"model view {small_b}B is not materially smaller than {full_b}B"
    )


# ---------------------------------------------------------------------------
# Numeric provenance: the pool is what the model saw, nothing more
# ---------------------------------------------------------------------------


def test_audit_pool_is_the_model_view_not_the_full_payload(engine):
    from app.agent.audit import collect_numbers
    from app.agent.orchestrator import _auditable

    tb = ToolBox(engine)
    raw = tb.call("unmet_demand_evidence", {"iata": "SFO"})

    full: set[float] = set()
    collect_numbers(raw, full)
    visible: set[float] = set()
    collect_numbers(_auditable(tb, "unmet_demand_evidence", raw), visible)

    assert len(visible) < len(full), "the pool still includes hidden figures"


def test_a_frontend_only_figure_cannot_validate_an_answer(engine):
    """The concrete risk requirement 3 names.

    `cohort_context.indicator_triggered_counts` is in the frontend payload but
    not in the model view: the model gets band frequencies, not per-indicator
    cohort counts. U3 fires for 115 of 399 airports. Before this change the
    audit would have accepted "115" as provenanced, because it appeared
    somewhere in a remembered payload — even though the model was never shown
    it. A figure the model could not have read must not validate its answer.

    Note the audit tolerates rounding by design (±0.05 absolute or ±1.2%), so
    this test uses a value that is distinct, not merely more precise.
    """
    from app.agent.audit import audit_text, collect_numbers
    from app.agent.orchestrator import _auditable

    tb = ToolBox(engine)
    raw = tb.call("unmet_demand_evidence", {"iata": "SFO"})

    hidden = raw["cohort_context"]["indicator_triggered_counts"]["U3"]
    assert hidden == 115

    full: set[float] = set()
    collect_numbers(raw, full)
    visible: set[float] = set()
    collect_numbers(_auditable(tb, "unmet_demand_evidence", raw), visible)

    claim = f"Upgauging fires for {hidden} airports in the cohort."
    assert audit_text(claim, [], known=full).ok, \
        "sanity check: the figure IS in the full payload"
    assert not audit_text(claim, [], known=visible).ok, \
        "a frontend-only figure was accepted as provenanced"

    # ...while what the model WAS shown still passes.
    assert audit_text("SFO's load factor is 82.6%.", [], known=visible).ok
    assert audit_text("Weak, as are 328 of 399 airports.", [],
                      known=visible).ok


def test_the_displayed_values_the_model_quotes_all_pass(engine):
    """Every value_display the model receives must be quotable."""
    from app.agent.audit import audit_text, collect_numbers
    from app.agent.orchestrator import _auditable

    tb = ToolBox(engine)
    raw = tb.call("unmet_demand_evidence", {"iata": "SFO"})
    compact = _auditable(tb, "unmet_demand_evidence", raw)
    visible: set[float] = set()
    collect_numbers(compact, visible)

    for i in compact["indicators"]:
        if i.get("value"):
            assert audit_text(f"The value is {i['value']}.", [],
                              known=visible).ok, i["id"]


def test_prompt_figures_are_admitted_to_the_pool():
    """The system prompt is model-visible, so figures it states are quotable."""
    from app.agent.audit import audit_text
    from app.agent.session import Session

    s = Session(session_id="t")
    s.remember_prompt_numbers()
    # The ACI volume gate appears in the prompt's own explanation.
    assert audit_text("ACI is suppressed below 1,000 reported flights.", [],
                      known=s.known_numbers).ok


def test_remember_prompt_numbers_is_idempotent():
    from app.agent.session import Session

    s = Session(session_id="t")
    s.remember_prompt_numbers()
    n = len(s.known_numbers)
    s.remember_prompt_numbers()
    assert len(s.known_numbers) == n


def test_full_precision_survives_for_the_frontend_and_the_engine(engine):
    """Rounding is presentational only."""
    ev = engine.unmet_demand("SFO")
    u1 = next(i for i in ev.indicators if i.id == "U1")
    assert u1.value == pytest.approx(engine.get_metrics("SFO").load_factor)
    assert u1.value != round(u1.value, 3), "engine value lost precision"
    assert u1.value_display == "82.6%"


def test_prompt_requires_the_three_part_structure():
    from app.agent.prompts import SYSTEM_PROMPT

    flat = " ".join(SYSTEM_PROMPT.split())
    assert "three parts" in flat
    assert "What was observed" in flat
    assert "What that evidence is consistent with" in flat
    assert "What is missing to quantify it" in flat
    assert "never the label alone" in flat
    assert "not fully comparable across airports" in flat
    assert "U2 and U3 are not independent" in flat
    assert "Do not retype the whole" in flat


def test_prompt_states_that_weak_is_not_proof_of_absence():
    from app.agent.prompts import SYSTEM_PROMPT

    flat = " ".join(SYSTEM_PROMPT.split())
    assert "A Weak band is not proof that unmet demand is absent" in flat
    assert "absence of evidence is not evidence of absence" in flat.lower()
    assert 'never let "Weak" read as "there is no unmet demand here"' in flat


def test_weak_band_carries_the_not_absence_note(engine):
    sfo = engine.unmet_demand("SFO")
    assert sfo.evidence_band == "Weak"
    assert "not evidence of absence" in sfo.weak_is_not_absence.lower()
    assert any("not evidence of absence" in x.lower() for x in sfo.limitations)


def test_strong_and_moderate_bands_omit_the_not_absence_note(engine):
    roc = engine.unmet_demand("ROC")       # Strong
    iag = engine.unmet_demand("IAG")       # Moderate
    assert roc.evidence_band == "Strong" and roc.weak_is_not_absence == ""
    assert iag.evidence_band == "Moderate" and iag.weak_is_not_absence == ""
    # ...and the model view omits the key rather than sending an empty one.
    tb = ToolBox(engine)
    compact = tb.compact_for_model(
        "unmet_demand_evidence", tb.call("unmet_demand_evidence", {"iata": "ROC"}))
    assert "weak_is_not_absence" not in compact["limits"]
