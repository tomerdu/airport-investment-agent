"""Phase 8.2b — the ACI temporal diagnostic as an integrated, additive field.

Two things are pinned here:
  * the diagnostic reaches the profile, the comparison and the agent payload;
  * it changes no score, no ranking, no classification, and no existing field.

No network, no API calls.
"""

from __future__ import annotations

import pytest

from app.agent.tools import ToolBox
from app.analytics import AnalyticsEngine
from app.analytics.persistence import CEILING_ACI, MONTHS_EXPECTED

ATTENTION = ["BOS", "BGR", "ACK", "MVY", "LAX", "SNA", "ASE"]


@pytest.fixture(scope="module")
def engine():
    e = AnalyticsEngine()
    yield e
    e.close()


# ---------------------------------------------------------------------------
# The API change is additive and backward compatible
# ---------------------------------------------------------------------------


def test_airport_scores_temporal_defaults_to_none():
    """An AirportScores built the old way still constructs."""
    from app.analytics.models import AirportScores, IndexResult

    idx = IndexResult(index="TDPI", label="t", score=50.0, coverage=1.0,
                      suppressed_reason=None, components=[], cohort_size=1,
                      notes=[])
    s = AirportScores(
        iata="X", name="n", state=None, hub_class=None, window="w", cohort="c",
        tdpi=idx, aci=idx, divergence_class="MIXED", divergence_reading="r",
        sources=[], limitations=[],
    )
    assert s.temporal is None
    assert s.to_dict()["temporal"] is None


def test_profile_carries_the_diagnostic(engine):
    s = engine.profile("BOS")
    assert s.temporal is not None
    d = s.to_dict()
    assert d["temporal"]["temporal_pattern"] == "PERSISTENT"
    # every pre-existing key is still present
    for k in ("iata", "tdpi", "aci", "divergence_class", "divergence_reading",
              "sources", "limitations", "window", "cohort"):
        assert k in d


def test_compare_carries_a_row_level_summary(engine):
    out = engine.compare(["LAX", "SNA"])
    for row in out["airports"]:
        assert "temporal" in row
        t = row["temporal"]
        assert t["months_expected"] == MONTHS_EXPECTED
        assert "monthly_spread" in t
        # the heavy monthly series stays out of the row summary
        assert "months" not in t
    # ...but remains available in the full scores payload
    assert out["airports"][0]["scores"]["temporal"]["months"]


# ---------------------------------------------------------------------------
# Production output is unchanged
# ---------------------------------------------------------------------------


def test_scores_and_classes_are_unchanged_by_the_diagnostic(engine):
    """Checksums measured before integration, in Phase 8.2."""
    cohort = engine.cohort()
    from app.analytics.scoring import compute_aci, compute_tdpi

    total_aci = sum(compute_aci(m, cohort).score or 0 for m in cohort.members)
    total_tdpi = sum(compute_tdpi(m, cohort).score or 0 for m in cohort.members)
    assert total_aci == pytest.approx(10931.2699, abs=1e-3)
    assert total_tdpi == pytest.approx(16146.9211, abs=1e-3)


def test_known_classes_unchanged(engine):
    expected = {
        "SFO": "MIXED", "LAX": "TERMINAL_LED", "SNA": "TERMINAL_LED",
        "ANC": "NO_NEAR_TERM_CASE", "BOS": "MIXED", "ACK": "AIRSIDE_LED",
        "MVY": "AIRSIDE_LED", "BGR": "MIXED",
    }
    for code, cls in expected.items():
        assert engine.profile(code).divergence_class == cls, code


def test_ranking_never_populates_the_diagnostic(engine):
    """Rank rows carry the field (it comes from AirportScores) but never a value.

    That is the point: a ranking is decided by ACI and TDPI alone, so the
    diagnostic is left unattached rather than computed and ignored.
    """
    out = engine.rank(["BOS", "LAX", "SNA"])
    assert "temporal" not in out
    rows = out["ranked"] + out["unscored"]
    assert rows
    for row in rows:
        assert row.get("temporal") is None, f"{row.get('iata')} ranked with a diagnostic"


def test_ranking_order_is_unaffected_by_the_diagnostic(engine):
    """BOS is PERSISTENT and LAX INTERMITTENT; ACI order must ignore that."""
    out = engine.rank(["BOS", "LAX", "SNA"], index="ACI")
    order = [r["iata"] for r in out["ranked"]]
    scores = [r["aci"]["score"] for r in out["ranked"]]
    assert order[0] == "BOS"                       # highest ACI
    assert scores == sorted(scores, reverse=True)


def test_diagnostic_is_not_needed_to_rank(engine):
    """Ranking must not trigger the lazy monthly load."""
    fresh = AnalyticsEngine()
    try:
        fresh.rank(["BOS", "LAX"])
        assert fresh._monthly_delay is None, "ranking loaded monthly OTP data"
        fresh.profile("BOS")
        assert fresh._monthly_delay is not None
    finally:
        fresh.close()


# ---------------------------------------------------------------------------
# Naming: no collision with the divergence class
# ---------------------------------------------------------------------------


def test_temporal_pattern_never_uses_the_word_mixed(engine):
    patterns = set()
    for code in ATTENTION:
        t = engine.profile(code).temporal
        if t:
            patterns.add(t["temporal_pattern"])
    assert patterns
    assert "MIXED" not in patterns
    assert patterns <= {"PERSISTENT", "EPISODIC", "INTERMITTENT",
                        "INSUFFICIENT_DATA"}


def test_intermediate_pattern_is_intermittent(engine):
    """The intermediate value is INTERMITTENT, never MIXED.

    LAX carries that pattern but, having no elevated month, is LABELLED
    "No elevated months" — see
    `test_zero_elevated_months_is_not_presented_as_intermittent_congestion`.
    ATW is the case where the plain label applies.
    """
    lax = engine.profile("LAX").temporal
    assert lax["temporal_pattern"] == "INTERMITTENT"

    atw = engine.profile("ATW").temporal
    assert atw["temporal_pattern"] == "INTERMITTENT"
    assert atw["elevated_months"] > 0
    assert atw["pattern_label"] == "Partly concentrated"


# ---------------------------------------------------------------------------
# Coverage and ceiling must be visible
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["ACK", "MVY"])
def test_partial_coverage_is_reported_not_suppressed(engine, code):
    """Approved decision: do NOT suppress ACK/MVY — disclose instead."""
    s = engine.profile(code)
    assert s.aci.score is not None, "ACI must still be reported"
    t = s.temporal
    assert t["months_available"] == 6
    assert t["months_expected"] == 12
    assert t["coverage_complete"] is False
    assert t["temporal_pattern"] == "INSUFFICIENT_DATA"
    assert any("6 of 12" in u for u in t["uncertainty"])


def test_ceiling_case_is_flagged_and_not_shown_as_stable(engine):
    """ASE sits at the cohort ceiling: 0 concentration is unmeasurable, not stable."""
    t = engine.profile("ASE").temporal
    assert t["concentration"]["worst_two_month_drop"] is None
    assert t["concentration"]["reliable"] is False
    assert t["concentration"]["unreliable_reason"] == "score_at_cohort_ceiling"
    assert any("ceiling" in u.lower() for u in t["uncertainty"])
    assert "must not be read as stability" in t["description"]
    assert engine.profile("ASE").aci.score >= CEILING_ACI


def test_unevaluated_months_show_their_reason(engine):
    months = engine.profile("ACK").temporal["months"]
    thin = [m for m in months if not m["evaluated"]]
    assert thin
    for m in thin:
        assert m["aci"] is None
        assert m["reason"] == "insufficient_monthly_flights"
        assert m["flights"] is not None, "flight count must still be shown"


def test_monthly_rows_always_carry_flight_counts(engine):
    for code in ATTENTION:
        t = engine.profile(code).temporal
        if not t:
            continue
        for m in t["months"]:
            assert isinstance(m["flights"], int)


# ---------------------------------------------------------------------------
# Language: timing, never cause
# ---------------------------------------------------------------------------


def test_descriptions_never_claim_a_cause(engine):
    banned = ("weather", "storm", "snow", "fog", "because of", "caused by",
              "due to bad")
    for code in ATTENTION:
        t = engine.profile(code).temporal
        if not t:
            continue
        text = " ".join([t["description"], *t["uncertainty"], *t["notes"]]).lower()
        for word in banned:
            assert word not in text, f"{code} description implies cause: {word}"


def test_notes_keep_the_no_capacity_claim(engine):
    t = engine.profile("BOS").temporal
    joined = " ".join(t["notes"]).lower()
    assert "runway, gate or airspace" in joined
    assert "outcomes" in joined


def test_seasonal_wording_is_descriptive(engine):
    t = engine.profile("ACK").temporal
    assert t["elevated_season"] == "summer-concentrated"


# ---------------------------------------------------------------------------
# Agent exposure
# ---------------------------------------------------------------------------


def test_agent_profile_payload_includes_the_diagnostic(engine):
    tb = ToolBox(engine)
    raw = tb.call("get_airport_profile", {"iata": "BOS"})
    compact = tb.compact_for_model("get_airport_profile", raw)
    assert "aci_temporal" in compact
    t = compact["aci_temporal"]
    assert t["temporal_pattern"] == "PERSISTENT"
    # the model does not receive 12 monthly rows
    assert "months" not in t


def test_agent_payload_flags_unmeasurable_concentration(engine):
    tb = ToolBox(engine)
    compact = tb.compact_for_model(
        "get_airport_profile", tb.call("get_airport_profile", {"iata": "ASE"}))
    t = compact["aci_temporal"]
    assert t["worst_two_month_drop"] is None
    assert t["concentration_unavailable"] == "score_at_cohort_ceiling"


def test_agent_compare_payload_includes_the_diagnostic(engine):
    tb = ToolBox(engine)
    raw = tb.call("compare_airports", {"iatas": ["LAX", "SNA"]})
    compact = tb.compact_for_model("compare_airports", raw)
    for row in compact["airports"]:
        assert "aci_temporal" in row
        assert "months" not in row["aci_temporal"]


def test_system_prompt_explains_when_not_why():
    from app.agent.prompts import SYSTEM_PROMPT

    p = SYSTEM_PROMPT.lower()
    assert "aci_temporal" in p
    assert "describes when" in p or "describes when, not why" in p
    assert "intermittent" in p
    assert "never" in p and "cause" in p
    assert "score_at_cohort_ceiling" in p


# ---------------------------------------------------------------------------
# The numeric audit must not be loosened by data the model never sees
# ---------------------------------------------------------------------------


def test_auditable_excludes_the_monthly_series(engine):
    """The monthly series is withheld from the model, so it must not widen the
    pool of numbers the provenance audit will accept."""
    from app.agent.audit import collect_numbers
    from app.agent.orchestrator import _auditable

    tb = ToolBox(engine)
    raw = tb.call("get_airport_profile", {"iata": "BOS"})

    full: set[float] = set()
    collect_numbers(raw, full)
    filtered: set[float] = set()
    collect_numbers(_auditable(raw), filtered)

    assert len(filtered) < len(full), "monthly series still inflating the pool"
    assert filtered <= full


def test_auditable_keeps_the_figures_the_model_is_shown(engine):
    from app.agent.orchestrator import _auditable

    tb = ToolBox(engine)
    a = _auditable(tb.call("get_airport_profile", {"iata": "BOS"}))
    t = a["scores"]["temporal"]
    assert "months" not in t
    assert t["monthly_spread"] is not None
    assert t["concentration"]["worst_two_month_drop"] is not None
    assert t["months_evaluated"] == 12


def test_auditable_does_not_mutate_the_frontend_payload(engine):
    from app.agent.orchestrator import _auditable

    tb = ToolBox(engine)
    raw = tb.call("get_airport_profile", {"iata": "BOS"})
    before = len(raw["scores"]["temporal"]["months"])
    _auditable(raw)
    assert len(raw["scores"]["temporal"]["months"]) == before == 12


def test_auditable_handles_compare_rows_and_odd_shapes(engine):
    from app.agent.orchestrator import _auditable

    tb = ToolBox(engine)
    c = _auditable(tb.call("compare_airports", {"iatas": ["LAX", "SNA"]}))
    for row in c["airports"]:
        assert "months" not in row["scores"]["temporal"]
    # Shapes with nothing to strip pass through untouched.
    assert _auditable({"a": 1}) == {"a": 1}
    assert _auditable([1, 2]) == [1, 2]
    assert _auditable("x") == "x"
    assert _auditable({"scores": {"aci": {}}}) == {"scores": {"aci": {}}}
    assert _auditable({"airports": [{"iata": "X"}]}) == {"airports": [{"iata": "X"}]}


# ---------------------------------------------------------------------------
# Phase 8.2 final corrections
# ---------------------------------------------------------------------------


def test_mixed_reading_does_not_claim_both_scores_are_mid_range(engine):
    """BOS is MIXED with TDPI 58.6 and ACI 79.5 — 'both in the middle band' is
    false and contradicts the figures shown beside it."""
    from app.analytics.definitions import DIVERGENCE_READINGS

    reading = DIVERGENCE_READINGS["MIXED"].lower()
    assert "both" not in reading.split("does not mean")[0], \
        "the leading claim must not say BOTH indices are mid-range"
    assert "at least one" in reading
    assert "40" in reading and "60" in reading

    bos = engine.profile("BOS")
    assert bos.divergence_class == "MIXED"
    assert bos.tdpi.score < 60.0            # intermediate
    assert bos.aci.score >= 60.0            # NOT intermediate
    assert "at least one" in bos.divergence_reading.lower()


def test_prompt_mixed_rule_is_consistent_with_real_scores():
    from app.agent.prompts import SYSTEM_PROMPT

    p = SYSTEM_PROMPT
    assert "at least ONE index" in p
    assert "58.6" in p and "79.5" in p, "the prompt cites the real BOS figures"
    assert "Never describe a MIXED airport as" in p


def test_zero_elevated_months_is_not_presented_as_intermittent_congestion(engine):
    """LAX: ACI 37.9, INTERMITTENT, but no month crossed the threshold."""
    t = engine.profile("LAX").temporal
    assert t["temporal_pattern"] == "INTERMITTENT"
    assert t["elevated_months"] == 0
    assert t["no_elevated_months"] is True
    assert t["pattern_label"] == "No elevated months"
    d = t["description"].lower()
    assert "no month reached the elevated threshold" in d
    assert "concentrated" not in d


def test_elevated_months_keep_the_normal_labels(engine):
    """The zero-elevated override must not swallow genuine patterns."""
    t = engine.profile("BOS").temporal
    assert t["no_elevated_months"] is False
    assert t["pattern_label"] == "Sustained across the window"


def test_ceiling_persistent_label_does_not_borrow_the_concentration_result(engine):
    """ASE is PERSISTENT on elevated count alone; the drop is unmeasurable."""
    t = engine.profile("ASE").temporal
    assert t["temporal_pattern"] == "PERSISTENT"
    assert t["concentration"]["reliable"] is False
    assert t["pattern_label"] == "Elevated in most months (concentration unmeasurable)"
    d = t["description"]
    assert "does not depend on a few months" not in d, \
        "that claim IS the concentration result, which is unavailable here"
    assert "rests on the count of elevated months alone" in d
    assert "must not be read as stability" in d


def test_non_ceiling_persistent_still_cites_its_measured_drop(engine):
    t = engine.profile("BOS").temporal
    d = t["description"]
    assert "does not depend on a few months" in d
    assert f"{t['concentration']['worst_two_month_drop']}" in d


def test_prompt_covers_both_temporal_correction_cases():
    from app.agent.prompts import SYSTEM_PROMPT

    p = SYSTEM_PROMPT
    assert "no_elevated_months" in p
    assert "never as intermittent or episodic congestion" in p
    assert "count of elevated months only" in p
    # The prompt is line-wrapped, so normalise whitespace before matching.
    flat = " ".join(p.split())
    assert 'do not say the score "does not depend on a few months"' in flat


def test_model_view_uses_one_decimal_for_index_scores(engine):
    """No four-decimal precision in the figures the narrative is built from."""
    tb = ToolBox(engine)
    compact = tb.compact_for_model(
        "get_airport_profile", tb.call("get_airport_profile", {"iata": "BOS"}))

    for key in ("tdpi", "aci"):
        score = compact["scores"][key]["score"]
        assert score == round(score, 1), f"{key} score {score} has >1 decimal"
        for c in compact["scores"][key]["components"]:
            for f in ("norm", "contribution", "percentile"):
                v = c.get(f)
                if isinstance(v, float):
                    assert v == round(v, 1), f"{key}.{c['id']}.{f}={v}"

    t = compact["aci_temporal"]
    for f in ("monthly_spread", "worst_two_month_drop"):
        v = t.get(f)
        if isinstance(v, float):
            assert v == round(v, 1), f"{f}={v}"


def test_full_precision_is_preserved_in_the_deterministic_payload(engine):
    """The rounding is presentational: the data itself keeps its precision."""
    tb = ToolBox(engine)
    raw = tb.call("get_airport_profile", {"iata": "BOS"})
    score = raw["scores"]["aci"]["score"]
    assert score != round(score, 1), "frontend payload must keep full precision"
    # And the engine's own value is untouched.
    assert engine.profile("BOS").aci.score == score


def test_minutes_are_two_decimals_in_the_model_view(engine):
    tb = ToolBox(engine)
    compact = tb.compact_for_model(
        "compare_airports", tb.call("compare_airports", {"iatas": ["LAX", "SNA"]}))
    for row in compact["airports"]:
        for k, v in row["intensity"].items():
            if k.endswith("_min") and isinstance(v, float):
                assert v == round(v, 2), f"{k}={v}"


def test_prompt_states_the_formatting_policy():
    from app.agent.prompts import SYSTEM_PROMPT

    p = SYSTEM_PROMPT
    assert "How to write figures" in p
    assert "one decimal" in p
    assert "79.4545" in p, "shows the precision to avoid"


def test_real_temporal_figures_still_pass_the_audit(engine):
    """A sentence quoting the diagnostic must not be rejected as fabricated."""
    from app.agent.audit import audit_text, collect_numbers
    from app.agent.orchestrator import _auditable

    tb = ToolBox(engine)
    raw = tb.call("get_airport_profile", {"iata": "BOS"})
    known: set[float] = set()
    collect_numbers(_auditable(raw), known)

    t = raw["scores"]["temporal"]
    text = (
        f"Airside pressure at BOS is sustained: {t['elevated_months']} of "
        f"{t['months_evaluated']} evaluated months are elevated, the monthly "
        f"spread is {t['monthly_spread']} points, and removing the two worst "
        f"months lowers the score by "
        f"{t['concentration']['worst_two_month_drop']} points."
    )
    assert audit_text(text, [], known=known).ok


def test_profile_tool_description_mentions_the_diagnostic():
    from app.agent.tools import TOOL_SCHEMAS

    spec = next(t for t in TOOL_SCHEMAS if t["name"] == "get_airport_profile")
    assert "aci_temporal" in spec["description"]

