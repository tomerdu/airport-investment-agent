"""Phase 9 — keep the evaluation matrix honest.

Deliberately small. The properties the matrix relies on are mostly already
proven elsewhere (session state in `test_session_continuity.py`, missing-data
representation and the audit pool in `test_udei_transparency.py` and
`test_temporal_integration.py`), and duplicating them would inflate the count
without adding information.

What is NOT covered elsewhere, and is covered here:

  * the matrix is structurally valid — real tool names, unique ids, all ten
    categories;
  * every numeric expectation is actually present in the payload the model would
    see, so a case cannot demand a figure the agent was never given;
  * the unsupported cases are unsupported *structurally*, not just by prompt;
  * the expectation matchers behave as intended, including tolerating the
    rounding the system permits.

If a scoring change moves a figure, the answerability test fails here rather than
producing a mysterious live failure later.
"""

from __future__ import annotations

from collections import Counter

import pytest

from app.agent.audit import collect_numbers
from app.agent.tools import TOOL_NAMES, ToolBox
from app.analytics import AnalyticsEngine
from evaluation.agent_eval_cases import (
    Absent,
    Number,
    Phrase,
    cases,
    live_cases,
    truth,
)
from evaluation.run_offline import NO_TOOL_EXPECTED, PROBES


@pytest.fixture(scope="module")
def engine():
    e = AnalyticsEngine()
    yield e
    e.close()


@pytest.fixture(scope="module")
def all_cases():
    return cases()


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_matrix_is_structurally_valid(all_cases):
    assert len(all_cases) >= 24, "the matrix should cover ~24+ cases"
    ids = [c.id for c in all_cases]
    assert len(ids) == len(set(ids)), f"duplicate ids: {Counter(ids).most_common(3)}"
    assert len(Counter(c.category for c in all_cases)) == 10
    for c in all_cases:
        assert c.question.strip(), c.id
        assert c.required_facts or c.forbidden_claims, f"{c.id} asserts nothing"
        for t in c.expected_tools:
            assert t in TOOL_NAMES, f"{c.id} expects unknown tool {t}"


def test_every_case_is_either_probed_or_expects_no_tool(all_cases):
    for c in all_cases:
        assert c.id in PROBES or c.id in NO_TOOL_EXPECTED, (
            f"{c.id} has no offline probe and is not marked no-tool"
        )


def test_static_classification_is_complete(all_cases):
    allowed = {"EXPECTED_TO_PASS", "RISK", "UNSUPPORTED_BY_DESIGN"}
    for c in all_cases:
        assert c.static_class in allowed, c.id
        if c.static_class != "EXPECTED_TO_PASS":
            assert c.static_note, f"{c.id} is {c.static_class} with no rationale"


# ---------------------------------------------------------------------------
# Answerability — the important one
# ---------------------------------------------------------------------------


def test_every_numeric_expectation_exists_in_the_payload(engine, all_cases):
    """A case must not demand a figure the model was never shown."""
    tb = ToolBox(engine)
    ne = engine.resolve_region("new_england")
    missing = []
    for c in all_cases:
        numeric = [f for f in c.required_facts if isinstance(f, Number)]
        probes = PROBES.get(c.id)
        if not numeric or not probes:
            continue
        pool: set[float] = set()
        for name, args in probes:
            a = dict(args)
            if a.get("iatas") == "__NEW_ENGLAND__":
                a["iatas"] = ne
            collect_numbers(tb.call(name, a), pool)
        for f in numeric:
            if not any(abs(v - f.value) <= max(f.tol, 0.051) for v in pool):
                missing.append(f"{c.id}:{f.label}={f.value}")
    assert not missing, f"expectations not answerable from the payload: {missing}"


def test_ground_truth_comes_from_the_engine(engine):
    """`truth()` must track the engine, not a copied constant."""
    t = truth()
    assert t["airports"]["BOS"]["tdpi"] == pytest.approx(
        engine.profile("BOS").tdpi.score)
    assert t["udei"]["SFO"]["band"] == engine.unmet_demand("SFO").evidence_band
    assert t["window"] == engine.window


# ---------------------------------------------------------------------------
# The unsupported cases are unsupported structurally
# ---------------------------------------------------------------------------


def test_non_us_airport_is_absent_from_the_warehouse(engine):
    for code in ("LHR", "CDG", "NRT", "DXB"):
        assert engine.get_metrics(code) is None, f"{code} unexpectedly present"


def test_no_cost_roi_or_forecast_capability_exists(engine):
    banned = [a for a in dir(engine)
              if any(w in a.lower() for w in ("cost", "roi", "revenue", "forecast"))]
    assert not banned, f"engine exposes {banned}"


def test_the_unsupported_long_haul_threshold_really_is_unsupported():
    assert 4000 not in truth()["anc_thresholds"], (
        "L3 assumes 4,000 sm is not computed; it now is, so the case is invalid"
    )


# ---------------------------------------------------------------------------
# Matchers
# ---------------------------------------------------------------------------


def test_number_matcher_allows_permitted_rounding():
    f = Number(58.6, "BOS TDPI")
    assert f.holds("BOS scores 58.6 on TDPI.")
    assert f.holds("a TDPI of 58.65")           # within tolerance
    assert not f.holds("a TDPI of 61.4")
    assert f.holds("passengers 20,983,745 and TDPI 58.6")   # comma handling


def test_phrase_and_absent_matchers():
    p = Phrase(("not measured", "suppressed"), "reports suppression")
    assert p.holds("ACI was SUPPRESSED for this airport")
    assert not p.holds("ACI is 37.9")
    a = Absent(("proves it needs",), "a proven shortage")
    assert a.holds("this is a screening signal, not proof")
    assert not a.holds("that proves it needs a terminal")


def test_phrase_matcher_folds_contractions_and_inflections():
    """Both folds were added because they produced false failures live."""
    p = Phrase(("cannot",), "refuses")
    assert p.holds("I can't give you that number")
    q = Phrase(("leave no",), "explains unobservability")
    assert q.holds("unmet demand leaves no trace in these datasets")
    r = Phrase(("fabricat",), "names fabrication")
    assert r.holds("inventing a figure would be fabrication")


def test_absent_matcher_allows_a_negated_mention():
    """A refusal that names the forbidden thing must not count as claiming it.

    This is verbatim from the first Phase 9 live run, where it was scored as a
    violation. The nearest negator is 97 characters before the phrase.
    """
    a = Absent(("would be profitable",), "profitability")
    refusal = (
        "This engine has no construction cost, financing, concession revenue, "
        "PFC/AIP funding, or any financial data. It cannot calculate a return "
        "on any capital project, and no score it produces should be read as "
        "evidence that a terminal expansion at SFO — or anywhere — would be "
        "profitable."
    )
    assert a.holds(refusal), "a negated mention was counted as a claim"


def test_absent_matcher_still_catches_an_unnegated_claim():
    a = Absent(("would be profitable",), "profitability")
    assert not a.holds(
        "Expanding the terminal would be profitable within eight years."
    )
    # ...and a negation in a PREVIOUS sentence must not excuse this one.
    assert not a.holds(
        "This system cannot model costs. Expanding SFO would be profitable."
    )


# ---------------------------------------------------------------------------
# Live sample discipline
# ---------------------------------------------------------------------------


def test_live_sample_is_small_and_covers_the_required_properties():
    live = live_cases()
    assert 5 <= len(live) <= 7, f"live sample is {len(live)}, spec allows 5-7"
    cats = {c.category for c in live}
    for required in ("comparison", "session", "udei", "unsupported", "adversarial"):
        assert required in cats, f"live sample misses {required}"
    assert any(c.is_multi_turn for c in live), "no multi-turn case selected"
    for c in live:
        assert c.live_rationale or c.static_note, f"{c.id} selected without rationale"


def test_evaluation_package_is_not_imported_by_production():
    import inspect

    import app.agent.orchestrator as orch
    import app.agent.tools as tools
    import app.analytics.engine as eng
    import app.main as main

    for mod in (main, eng, tools, orch):
        assert "evaluation" not in inspect.getsource(mod), mod.__name__
