"""Regression tests for calibrated analytical language.

Wording is part of the deliverable here: an overclaiming sentence in a prompt,
a divergence reading or a limitation string turns a screening proxy into an
apparent measurement. These tests pin the required phrasing so a later edit
cannot quietly reintroduce it.

All offline — they read constants, never the model.
"""

from __future__ import annotations

import pytest

from app.agent.prompts import SYSTEM_PROMPT
from app.analytics import definitions as D
from app.analytics.scoring import Cohort, compute_aci
from tests.test_analytics_units import mk, spread_cohort

# Text surfaces that reach a reader: the system prompt, every divergence
# reading, the standing limitations and the UDEI caveat.
ALL_TEXT = "\n".join(
    [SYSTEM_PROMPT, D.UDEI_CAVEAT, *D.DIVERGENCE_READINGS.values(), *D.GLOBAL_LIMITATIONS]
)


# ---------------------------------------------------------------------------
# Both indices are proxies
# ---------------------------------------------------------------------------


def test_both_indices_are_described_as_proxy_composites():
    low = SYSTEM_PROMPT.lower()
    assert "composite proxy" in low
    assert "proxy for demand pressure" in low
    assert "composite proxy index" in low


def test_aci_is_not_called_a_direct_measurement():
    """ACI reflects observed delay outcomes; it does not measure capacity."""
    for phrase in (
        "aci (airside congestion index) is a direct measurement",
        "aci is a direct measurement",
    ):
        assert phrase not in SYSTEM_PROMPT.lower()


def test_aci_disclaims_measuring_capacity_and_binding_constraints():
    low = SYSTEM_PROMPT.lower()
    assert "does not** measure" in low or "does not measure" in low
    assert "binding" in low
    assert "the cause is not identified" in low


def test_aci_runtime_note_matches_the_calibrated_wording():
    members = spread_cohort()
    notes = " ".join(compute_aci(members[5], Cohort("t", members)).notes).lower()
    assert "composite proxy" in notes
    assert "does not measure runway or airspace capacity" in notes
    assert "direct measurement" not in notes


# ---------------------------------------------------------------------------
# Divergence classes are screening classifications
# ---------------------------------------------------------------------------


def test_divergence_classes_are_described_as_screening_classifications():
    low = SYSTEM_PROMPT.lower()
    assert "screening classifications" in low
    assert "not investment" in low or "not an investment" in low


@pytest.mark.parametrize("cls", sorted(D.DIVERGENCE_READINGS))
def test_no_reading_makes_a_causal_investment_claim(cls):
    """A reading must not predict what investment would or would not achieve."""
    text = D.DIVERGENCE_READINGS[cls].lower()
    for banned in (
        "would not unlock",
        "would not resolve",
        "would not address",
        "does not address the binding limit",
        "alone would",
        "investment alone",
    ):
        assert banned not in text, f"{cls} contains a causal claim: {banned!r}"


@pytest.mark.parametrize("cls", sorted(D.DIVERGENCE_READINGS))
def test_no_reading_asserts_a_binding_constraint(cls):
    text = D.DIVERGENCE_READINGS[cls].lower()
    assert "binding airside constraint" not in text
    assert "the binding limit" not in text


def test_terminal_led_reading_is_explicitly_not_a_recommendation():
    text = D.DIVERGENCE_READINGS["TERMINAL_LED"].lower()
    assert "not a recommendation" in text
    assert "prompt to investigate" in text


def test_prompt_forbids_causal_investment_statements():
    low = SYSTEM_PROMPT.lower()
    assert "do not make causal claims" in low
    assert "terminal investment alone would not resolve" in low  # quoted as banned


def test_prompt_forbids_contradicting_the_deterministic_classification():
    assert "contradicts the deterministic" in SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Unmet demand is scoped to THIS system's datasets
# ---------------------------------------------------------------------------


def test_unmet_demand_is_scoped_to_this_systems_datasets():
    low = D.UDEI_CAVEAT.lower()
    assert "cannot be quantified from the datasets this system uses" in low
    assert "bts and faa sources" in low


def test_unmet_demand_does_not_claim_no_dataset_anywhere_could_estimate_it():
    """Bespoke survey, booking or schedule-request data could; we simply do not
    have it. Overreaching here would be its own overclaim."""
    for text in (D.UDEI_CAVEAT.lower(), SYSTEM_PROMPT.lower()):
        assert "leave no record in any dataset" not in text
        assert "no public dataset publishes" not in text
        assert "cannot be observed in public data" not in text


def test_prompt_tells_the_model_to_scope_the_unmet_demand_claim():
    low = SYSTEM_PROMPT.lower()
    assert "rather than claiming no data source anywhere" in low


def test_udei_still_refuses_to_emit_a_quantity():
    low = D.UDEI_CAVEAT.lower()
    assert "no numeric estimate" in low
    assert "not a measurement of unmet demand" in low


# ---------------------------------------------------------------------------
# Standing limitations carry the corrected framing
# ---------------------------------------------------------------------------


def test_limitations_state_both_indices_are_proxies():
    joined = " ".join(D.GLOBAL_LIMITATIONS).lower()
    assert "composite proxy indices" in joined
    assert "neither measures infrastructure capacity" in joined


def test_limitations_state_aci_identifies_no_cause():
    joined = " ".join(D.GLOBAL_LIMITATIONS).lower()
    assert "does not identify a cause" in joined
    assert "binding" in joined


def test_limitations_state_classes_are_not_recommendations():
    joined = " ".join(D.GLOBAL_LIMITATIONS).lower()
    assert "screening classifications" in joined
    assert "not investment" in joined


def test_profitability_disclaimer_survived_the_rewrite():
    joined = " ".join(D.GLOBAL_LIMITATIONS).lower()
    assert "profitability is not modelled" in joined


# ---------------------------------------------------------------------------
# Nothing regressed in the hard guarantees
# ---------------------------------------------------------------------------


def test_no_calculation_rule_is_intact():
    assert "You do not calculate" in SYSTEM_PROMPT


def test_suppressed_scores_are_still_unknown_not_low():
    assert "never low" in SYSTEM_PROMPT.lower()
    assert (
        "absence of a measurement is not evidence"
        in D.DIVERGENCE_READINGS["UNCLASSIFIED_AIRSIDE_UNKNOWN"].lower()
    )


def test_t4_proxy_warning_is_intact():
    assert "PROXY ONLY" in D.T4_PROXY_NOTE
    assert "PROXY" in SYSTEM_PROMPT


def test_scoring_behaviour_is_unchanged_by_the_wording_edits():
    """Language-only change: the arithmetic must be untouched."""
    members = spread_cohort()
    cohort = Cohort("t", members)
    target = mk("Z", flights=50_000)
    score = compute_aci(target, cohort).score
    assert score is not None and 0.0 <= score <= 100.0
    assert {d.id: d.weight for d in D.ACI_METRICS} == {
        "A1": 0.30, "A2": 0.30, "A3": 0.25, "A4": 0.15,
    }
    assert {d.id: d.weight for d in D.TDPI_METRICS} == {
        "T1": 0.20, "T2": 0.30, "T3": 0.15, "T4": 0.20, "T5": 0.15,
    }
