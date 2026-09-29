"""Agent-layer tests.

Every test here uses a fake Anthropic client — no network, no tokens, no cost.
The real API is exercised once, manually, via `python -m app.agent.preflight
--live` and the demo script.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agent import config as agent_config
from app.agent.audit import audit_text, collect_numbers, render_fallback
from app.agent.orchestrator import Orchestrator
from app.agent.prompts import SYSTEM_PROMPT, session_state_block
from app.agent.session import Session, SessionStore
from app.agent.tools import TOOL_NAMES, TOOL_SCHEMAS, ToolBox, ToolError
from app.analytics import AnalyticsEngine
from app.analytics.resolve import resolve
from etl import config as etl_config

pytestmark = pytest.mark.skipif(
    not etl_config.WAREHOUSE_PATH.exists(), reason="warehouse not built"
)


# ---------------------------------------------------------------------------
# Fake Anthropic client
# ---------------------------------------------------------------------------


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool_use(tid, name, payload):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=payload)


def _msg(content, stop_reason="end_turn", inp=100, out=50):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=inp, output_tokens=out),
        model="fake-model",
    )


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            return _msg([_text("(no more scripted replies)")])
        return self.script.pop(0)


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)


@pytest.fixture(scope="module")
def engine():
    e = AnalyticsEngine()
    yield e
    e.close()


@pytest.fixture
def toolbox(engine):
    return ToolBox(engine)


def make_orch(engine, script):
    return Orchestrator(
        engine=engine, client=FakeClient(script), store=SessionStore(), model="fake"
    )


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------


def test_all_six_tools_are_exposed():
    assert set(TOOL_NAMES) == {
        "resolve_airports", "get_airport_profile", "compare_airports",
        "rank_airports", "long_haul_breakdown", "unmet_demand_evidence",
    }


def test_schemas_are_well_formed():
    for schema in TOOL_SCHEMAS:
        assert schema["name"] and len(schema["description"]) > 80
        s = schema["input_schema"]
        assert s["type"] == "object" and s["properties"]
        for req in s.get("required", []):
            assert req in s["properties"]


def test_long_haul_schema_has_no_single_answer_field():
    """Schema design is the control: the model cannot report one percentage
    because no such field is returned."""
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "long_haul_breakdown")
    desc = schema["description"].lower()
    assert "sensitivity table" in desc
    assert "no single" in desc


def test_unmet_demand_schema_forbids_a_number():
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "unmet_demand_evidence")
    assert "cannot be measured" in schema["description"].lower()


# ---------------------------------------------------------------------------
# Tool implementations delegate to the deterministic engine
# ---------------------------------------------------------------------------


def test_profile_tool_matches_engine_exactly(toolbox, engine):
    out = toolbox.call("get_airport_profile", {"iata": "SFO"})
    assert out["scores"]["tdpi"]["score"] == engine.profile("SFO").tdpi.score
    assert out["traffic"]["load_factor"] == engine.get_metrics("SFO").load_factor
    assert out["window"] == engine.window


def test_long_haul_tool_returns_bands_not_a_scalar(toolbox):
    out = toolbox.call("long_haul_breakdown", {"iata": "ANC"})
    assert "long_haul_percentage" not in out
    assert "answer" not in out
    for scope in out["scopes"]:
        assert len(scope["bands"]) >= 5
    assert "12 months" in out["period"]


def test_long_haul_scopes_reconcile_to_the_total(toolbox):
    """Regression: the panel once showed only passenger and all-cargo, whose
    departures did not sum to the total. At ANC the residual was 888 combi
    departures (T-100 AIRCRAFT_CONFIG 3 — passengers AND freight on one main
    deck), presented as if the two scopes were exhaustive."""
    out = toolbox.call("long_haul_breakdown", {"iata": "ANC"})
    scopes = {s["scope"] for s in out["scopes"]}
    assert {"all_carriers", "passenger", "cargo", "combi"} <= scopes

    rec = out["reconciliation"]
    assert rec["reconciles"] is True
    assert abs(rec["residual"]) < 0.5
    assert rec["total_departures"] == pytest.approx(87210, abs=1)
    assert rec["breakdown"]["combi"]["departures"] == pytest.approx(888, abs=1)


def test_long_haul_omits_scopes_with_no_operations(toolbox):
    """A typical airport should not show four empty columns."""
    out = toolbox.call("long_haul_breakdown", {"iata": "ANC"})
    for scope in out["scopes"]:
        if scope["scope"] != "all_carriers":
            assert scope["total_departures"] > 0
    # ANC flies no amphibious service, so that scope is absent from the panel
    # but still present in the reconciliation.
    assert "amphibious" not in {s["scope"] for s in out["scopes"]}
    assert out["reconciliation"]["breakdown"]["amphibious"]["departures"] == 0


def test_unmet_tool_has_no_magnitude_anywhere(toolbox):
    out = toolbox.call("unmet_demand_evidence", {"iata": "SFO"})
    blob = json.dumps(out).lower()
    for forbidden in ("unmet_passengers", "unmet_flights", "estimated_demand"):
        assert forbidden not in blob
    assert out["evidence_band"] in ("Weak", "Moderate", "Strong", "Indeterminate")


@pytest.mark.parametrize(
    "name,payload",
    [
        ("resolve_airports", {"queries": ["Boston"]}),
        ("get_airport_profile", {"iata": "SFO"}),
        ("compare_airports", {"iatas": ["LAX", "SNA"]}),
        ("rank_airports", {"iatas": ["BOS", "BDL", "PVD"]}),
        ("long_haul_breakdown", {"iata": "ANC"}),
        ("unmet_demand_evidence", {"iata": "SFO"}),
    ],
)
def test_every_tool_returns_provenance(toolbox, name, payload):
    """Regression guard: get_airport_profile once nested `sources` inside
    `scores`, so single-airport answers came back with no citations at all."""
    out = toolbox.call(name, payload)
    assert out.get("sources"), f"{name} returned no sources"
    for src in out["sources"]:
        assert src["source_url"] and src["retrieved_at"] and src["coverage"]


@pytest.mark.parametrize(
    "name,payload",
    [
        ("get_airport_profile", {"iata": "SFO"}),
        ("compare_airports", {"iatas": ["LAX", "SNA"]}),
        ("rank_airports", {"iatas": ["BOS", "BDL"]}),
        ("long_haul_breakdown", {"iata": "ANC"}),
        ("unmet_demand_evidence", {"iata": "SFO"}),
    ],
)
def test_every_analytic_tool_states_its_window(toolbox, name, payload):
    out = toolbox.call(name, payload)
    stamp = out.get("window") or out.get("period") or ""
    assert "2025-05" in stamp or "2026-04" in stamp, f"{name}: {stamp!r}"


# ---------------------------------------------------------------------------
# LLM view vs frontend payload (token optimisation)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,payload",
    [
        ("resolve_airports", {"queries": ["New England"]}),
        ("get_airport_profile", {"iata": "SFO"}),
        ("compare_airports", {"iatas": ["LAX", "SNA"]}),
        ("rank_airports", {"iatas": ["BOS", "BDL", "PVD", "MHT", "PWM", "BTV"]}),
        ("long_haul_breakdown", {"iata": "ANC"}),
        ("unmet_demand_evidence", {"iata": "SFO"}),
    ],
)
def test_compact_view_is_smaller_but_keeps_the_window(toolbox, name, payload):
    full = toolbox.call(name, payload)
    compact = toolbox.compact_for_model(name, full)
    assert len(json.dumps(compact, default=str)) < len(json.dumps(full, default=str))
    stamp = compact.get("window") or compact.get("period") or ""
    assert "2025-05" in stamp or "2026-04" in stamp


def test_compact_view_drops_only_the_static_blocks(toolbox):
    full = toolbox.call("get_airport_profile", {"iata": "SFO"})
    compact = toolbox.compact_for_model("get_airport_profile", full)
    # Removed from the model's view...
    assert "sources" not in compact and "limitations" not in compact
    # ...but still present in the payload the frontend receives.
    assert full["sources"] and full["limitations"]


def test_compact_view_preserves_every_headline_number(toolbox, engine):
    full = toolbox.call("get_airport_profile", {"iata": "SFO"})
    compact = toolbox.compact_for_model("get_airport_profile", full)
    m = engine.get_metrics("SFO")
    blob = json.dumps(compact)

    assert compact["traffic"]["load_factor"] == pytest.approx(m.load_factor)
    # Index scores are rounded to one decimal for the model view (Phase 8.2
    # final: no four-decimal precision in narrative). The headline number is
    # unchanged at reading precision; full precision stays in `full`.
    exact = engine.profile("SFO").tdpi.score
    assert compact["scores"]["tdpi"]["score"] == pytest.approx(round(exact, 1))
    assert full["scores"]["tdpi"]["score"] == pytest.approx(exact)
    assert compact["scores"]["class"] == engine.profile("SFO").divergence_class
    for comp in compact["scores"]["tdpi"]["components"]:
        assert comp["id"] and comp["raw"] is not None
        assert comp["contribution"] is not None
    assert "T4" in blob


def test_long_haul_compact_keeps_the_full_sensitivity_table(toolbox):
    full = toolbox.call("long_haul_breakdown", {"iata": "ANC"})
    compact = toolbox.compact_for_model("long_haul_breakdown", full)
    assert len(compact["scopes"]) == len(full["scopes"])
    for scope in compact["scopes"]:
        assert len(scope["bands"]) == 5
    assert "12 months" in compact["period"]
    # The model must be able to see that the scopes reconcile.
    assert compact["reconciliation"]["reconciles"] is True
    assert compact["reconciliation"]["breakdown"]["combi"]["departures"] == 888


def test_unmet_compact_keeps_every_indicator_and_the_refusal(toolbox):
    """Phase 8.3 final: the band moved into `counts` and the long `caveat`
    paragraph into the panel and the prompt. Its substance must still reach the
    model — via the imperative requirement and the shared quantification limit.
    """
    full = toolbox.call("unmet_demand_evidence", {"iata": "SFO"})
    compact = toolbox.compact_for_model("unmet_demand_evidence", full)
    assert len(compact["indicators"]) == len(full["indicators"])
    assert compact["counts"]["band"] == full["evidence_band"]
    assert "do not state or imply a number" in compact["reporting_requirement"].lower()
    assert "none is produced" in compact["limits"]["quantification"]
    # ...and the full caveat is still in the payload the frontend receives.
    assert "not a measurement" in full["caveat"].lower()
    u5 = next(i for i in compact["indicators"] if i["id"] == "U5")
    assert u5["triggered"] is None and u5["unavailable_reason"]


def test_rank_compact_keeps_all_rows_and_suppression_reasons(toolbox, engine):
    codes = engine.resolve_region("new_england")
    full = toolbox.call("rank_airports", {"iatas": codes})
    compact = toolbox.compact_for_model("rank_airports", full)
    assert len(compact["ranked"]) == len(full["ranked"])
    assert len(compact["unscored"]) == len(full["unscored"])
    for row in compact["ranked"]:
        assert row["scores"]["tdpi"]["score"] is not None
        if row["scores"]["aci"]["score"] is None:
            assert row["scores"]["aci"]["suppressed_reason"]
    # Components only for the leaders, to keep history small.
    assert compact["ranked"][0]["scores"]["tdpi"]["components"]
    if len(compact["ranked"]) > 4:
        assert "components" not in compact["ranked"][4]["scores"]["tdpi"]


def test_compact_view_materially_reduces_payload_size(toolbox, engine):
    codes = engine.resolve_region("new_england")
    full = toolbox.call("rank_airports", {"iatas": codes})
    a = len(json.dumps(full, default=str))
    b = len(toolbox.serialise_for_model("rank_airports", full))
    assert b < a * 0.35, f"only reduced {a} -> {b}"


def test_error_results_pass_through_uncompacted(toolbox):
    err = {"error": "boom"}
    assert toolbox.compact_for_model("get_airport_profile", err) == err


def test_orchestrator_sends_compact_but_returns_full(engine):
    orch = make_orch(engine, [
        _msg([_tool_use("t1", "get_airport_profile", {"iata": "SFO"})],
             stop_reason="tool_use"),
        _msg([_text("Done.")]),
    ])
    reply = orch.chat("SFO?")
    sent = orch.client.messages.calls[-1]["messages"]
    tool_result = next(
        b
        for m in sent
        if isinstance(m.get("content"), list)
        for b in m["content"]
        # assistant blocks are SDK objects; tool_result blocks we build are dicts
        if isinstance(b, dict) and b.get("type") == "tool_result"
    )
    model_saw = json.loads(tool_result["content"])
    assert "sources" not in model_saw
    # The API response still carries everything.
    assert reply.tool_calls[0].result["sources"]
    assert reply.sources


def test_static_context_is_in_the_system_prompt_not_every_tool_result(engine):
    orch = make_orch(engine, [_msg([_text("hi")])])
    orch.chat("hello")
    system = orch.client.messages.calls[-1]["system"]
    prefix = system[0]["text"]
    assert "Data context" in prefix
    assert "Standing limitations" in prefix
    assert "2025-05..2026-04" in prefix
    assert system[0].get("cache_control") == {"type": "ephemeral"}


def test_rank_tool_separates_unscored_from_low_scoring(toolbox, engine):
    out = toolbox.call("rank_airports", {"iatas": engine.resolve_region("new_england")})
    assert out["ranked"]
    for row in out["unscored"]:
        assert row["rank"] is None and row["unscored_reason"]


def test_compare_tool_requires_two_airports(toolbox):
    with pytest.raises(ToolError, match="at least two"):
        toolbox.call("compare_airports", {"iatas": ["LAX"]})


def test_unknown_airport_raises_with_suggestions_not_invention(toolbox):
    with pytest.raises(ToolError) as exc:
        toolbox.call("get_airport_profile", {"iata": "ZZZ"})
    assert "not a US primary" in str(exc.value)


def test_unknown_tool_name_is_rejected(toolbox):
    with pytest.raises(ToolError, match="Unknown tool"):
        toolbox.call("calculate_profit", {})


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def test_la_resolves_to_lax_and_flags_the_alternative(engine):
    r = resolve(engine, "LA")
    assert r.airports == ["LAX"]
    assert r.ambiguous is True
    assert "SNA" in r.alternatives[0]["airports"]


def test_santa_ana_resolves_to_sna(engine):
    r = resolve(engine, "Santa Ana")
    assert r.airports == ["SNA"] and r.found


def test_new_england_resolves_to_the_region(engine):
    r = resolve(engine, "New England")
    assert r.method == "region"
    for code in ("BOS", "BDL", "PVD", "MHT", "PWM", "BTV"):
        assert code in r.airports


def test_iata_code_resolves_directly(engine):
    assert resolve(engine, "ANC").airports == ["ANC"]


def test_unknown_place_is_not_invented(engine):
    r = resolve(engine, "Kalamazoo Intergalactic Spaceport")
    assert r.found is False and r.airports == []


def test_non_us_airport_is_out_of_scope(engine):
    assert resolve(engine, "Heathrow").found is False


# ---------------------------------------------------------------------------
# Numeric audit
# ---------------------------------------------------------------------------


def test_audit_accepts_values_present_in_tool_output():
    payload = {"load_factor": 0.826, "departures": 190280}
    assert audit_text("Load factor is 82.6% on 190,280 departures.", [payload]).ok


def test_audit_accepts_rounding():
    assert audit_text("about 83%", [{"load_factor": 0.826}]).ok


def test_audit_rejects_a_fabricated_number():
    r = audit_text("Unmet demand is about 2,300,000 passengers.", [{"load_factor": 0.826}])
    assert not r.ok and "2,300,000" in r.unmatched


def test_audit_rejects_a_derived_number():
    """The model must not do arithmetic, even correct arithmetic."""
    payload = {"lax": 270853, "sna": 51609}
    r = audit_text("LAX has 219,244 more departures than SNA.", [payload])
    assert not r.ok


def test_audit_ignores_years_and_small_ordinals():
    assert audit_text("In 2026, the top 3 airports ranked 1, 2 and 3.", [{}]).ok


def test_audit_ignores_numbers_inside_code_and_iso_dates():
    assert audit_text("Window `2025-05..2026-04` covers 12 months.", [{}]).ok


def test_collect_numbers_walks_nested_structures():
    nums = collect_numbers({"a": [{"b": 30.1}], "c": "share was 4.1%"})
    assert 30.1 in nums and 4.1 in nums


def test_collect_numbers_handles_percent_scaling():
    nums = collect_numbers({"load_factor": 0.826})
    assert any(abs(n - 82.6) < 1e-6 for n in nums)


def test_fallback_renders_tool_output():
    text = render_fallback([{"window": "2025-05..2026-04", "x": 1}], "q")
    assert "2025-05..2026-04" in text and "```json" in text


# ---------------------------------------------------------------------------
# Session memory
# ---------------------------------------------------------------------------


def test_session_tracks_focus_and_ranking():
    s = Session(session_id="t")
    s.set_focus(["bos", "BOS", "BDL"])
    assert s.focus_airports == ["BOS", "BDL"]
    s.set_ranking(["HVN", "BOS", "BGR"])
    assert s.last_ranking[1] == "BOS"


def test_session_assumptions_deduplicate():
    s = Session(session_id="t")
    s.note_assumption("Reading 'LA' as LAX.")
    s.note_assumption("Reading 'LA' as LAX.")
    assert len(s.assumptions) == 1


def _tool_turn(s: Session, label: str) -> None:
    s.messages.append({"role": "user", "content": label})
    s.messages.append({"role": "assistant", "content": [{"type": "tool_use"}]})
    s.messages.append({"role": "user", "content": [{"type": "tool_result"}]})
    s.messages.append({"role": "assistant", "content": "a"})


def test_trim_never_splits_a_tool_exchange():
    """Cutting between a tool_use and its tool_result would 400 the API."""
    s = Session(session_id="t")
    for i in range(40):
        _tool_turn(s, f"q{i}")
    s.trim()
    assert s.messages[0]["role"] == "user"
    first = s.messages[0]["content"]
    assert isinstance(first, str) or all(
        b.get("type") != "tool_result" for b in first
    )


def test_trim_counts_turns_not_messages():
    """Regression: the budget was MAX_HISTORY_TURNS * 2 messages, which assumes
    two messages per turn. A tool-using turn is four or more, so a 12-turn
    budget retained only ~6 turns — and a follow-up referring back to an
    earlier tool result found it had been trimmed away."""
    s = Session(session_id="t")
    for i in range(30):
        _tool_turn(s, f"q{i}")
    s.trim()

    kept = [m["content"] for m in s.messages if Session._is_turn_start(m)]
    assert len(kept) == agent_config.MAX_HISTORY_TURNS
    assert kept[-1] == "q29"
    assert kept[0] == f"q{30 - agent_config.MAX_HISTORY_TURNS}"


def test_trim_keeps_a_tool_result_reachable_after_several_turns():
    """The concrete failure: ANC long-haul ran, four more turns happened, and
    the follow-up could no longer see it."""
    s = Session(session_id="t")
    _tool_turn(s, "long haul for ANC?")
    for i in range(4):
        _tool_turn(s, f"other question {i}")
    s.trim()
    assert any(
        m.get("content") == "long haul for ANC?" for m in s.messages
    ), "the originating turn was trimmed away too early"


def test_trim_is_a_noop_below_the_budget():
    s = Session(session_id="t")
    for i in range(3):
        _tool_turn(s, f"q{i}")
    before = len(s.messages)
    s.trim()
    assert len(s.messages) == before


def test_state_block_renders_ordinals_for_followups():
    block = session_state_block(["BOS"], ["HVN", "BOS"], ["Reading 'LA' as LAX."])
    assert "1. HVN" in block and "2. BOS" in block
    assert "Reading 'LA' as LAX." in block


def test_state_block_surfaces_the_last_comparison():
    """Regression: 'add Burbank to that comparison' failed after intervening
    turns overwrote the focus, because only focus was carried."""
    block = session_state_block(["BOS"], [], [], ["LAX", "SNA"])
    assert "Most recent comparison" in block
    assert "LAX, SNA" in block


def test_empty_state_block_is_empty():
    assert session_state_block([], [], []) == ""


def test_session_remembers_numbers_across_turns():
    s = Session(session_id="t")
    s.remember_numbers({"share_pct": 30.1})
    s.remember_numbers("use 1,500 miles")
    assert any(abs(n - 30.1) < 1e-9 for n in s.known_numbers)
    assert any(abs(n - 1500.0) < 1e-9 for n in s.known_numbers)


def test_store_reset_clears_memory():
    store = SessionStore()
    s = store.get_or_create("abc")
    s.set_focus(["BOS"])
    assert store.reset("abc").focus_airports == []


# ---------------------------------------------------------------------------
# Orchestration loop (mocked model)
# ---------------------------------------------------------------------------


def test_single_turn_without_tools(engine):
    orch = make_orch(engine, [_msg([_text("Hello.")])])
    reply = orch.chat("hi")
    assert reply.answer == "Hello."
    assert reply.tool_calls == []
    assert reply.window == engine.window


def test_tool_call_is_executed_and_fed_back(engine):
    orch = make_orch(engine, [
        _msg([_tool_use("t1", "get_airport_profile", {"iata": "SFO"})],
             stop_reason="tool_use"),
        _msg([_text("SFO load factor is 82.6%.")]),
    ])
    reply = orch.chat("tell me about SFO")
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].name == "get_airport_profile"
    assert reply.tool_calls[0].ok
    assert reply.audit["passed"]
    assert reply.sources


def test_parallel_tool_results_returned_in_one_message(engine):
    """Splitting them across messages silently trains the model out of
    parallel calls."""
    orch = make_orch(engine, [
        _msg([
            _tool_use("t1", "get_airport_profile", {"iata": "LAX"}),
            _tool_use("t2", "get_airport_profile", {"iata": "SNA"}),
        ], stop_reason="tool_use"),
        _msg([_text("Done.")]),
    ])
    reply = orch.chat("compare")
    assert len(reply.tool_calls) == 2
    sent = orch.client.messages.calls[-1]["messages"]
    tool_result_msgs = [
        m for m in sent
        if m["role"] == "user" and isinstance(m["content"], list)
        and any(b.get("type") == "tool_result" for b in m["content"])
    ]
    assert len(tool_result_msgs) == 1
    assert len(tool_result_msgs[0]["content"]) == 2


def test_tool_error_is_reported_not_crashed(engine):
    orch = make_orch(engine, [
        _msg([_tool_use("t1", "get_airport_profile", {"iata": "ZZZ"})],
             stop_reason="tool_use"),
        _msg([_text("That airport is not in the dataset.")]),
    ])
    reply = orch.chat("tell me about ZZZ")
    assert reply.tool_calls[0].ok is False
    assert reply.tool_calls[0].error
    assert "not in the dataset" in reply.answer


def test_tool_hop_budget_is_enforced(engine):
    """A model that keeps calling tools must not loop forever."""
    script = [
        _msg([_tool_use(f"t{i}", "get_airport_profile", {"iata": "SFO"})],
             stop_reason="tool_use")
        for i in range(agent_config.MAX_TOOL_HOPS + 2)
    ]
    script.append(_msg([_text("Stopping.")]))
    orch = make_orch(engine, script)
    reply = orch.chat("loop")
    assert len(reply.tool_calls) <= agent_config.MAX_TOOL_HOPS


def test_fabricated_number_triggers_regeneration(engine):
    orch = make_orch(engine, [
        _msg([_tool_use("t1", "unmet_demand_evidence", {"iata": "SFO"})],
             stop_reason="tool_use"),
        _msg([_text("SFO has 2,300,000 passengers of unmet demand.")]),
        _msg([_text("SFO shows Weak evidence of constrained supply.")]),
    ])
    reply = orch.chat("unmet demand at SFO?")
    assert reply.degraded is True
    assert "2,300,000" not in reply.answer
    assert reply.audit["passed"]


def test_repeated_fabrication_falls_back_to_tool_output(engine):
    bad = "Unmet demand is 9,876,543 passengers."
    orch = make_orch(engine, [
        _msg([_tool_use("t1", "unmet_demand_evidence", {"iata": "SFO"})],
             stop_reason="tool_use"),
        _msg([_text(bad)]),
        _msg([_text(bad)]),
    ])
    reply = orch.chat("unmet demand?")
    assert reply.degraded is True
    assert "9,876,543" not in reply.answer
    assert "```json" in reply.answer


def test_followup_may_reuse_figures_from_an_earlier_turn(engine):
    """Regression: the audit checked only the CURRENT turn's payloads, so a
    follow-up that correctly re-quoted an earlier tool result was reported as
    '21 of 21 numerals not found' — and then silently ignored."""
    orch = make_orch(engine, [
        _msg([_tool_use("t1", "long_haul_breakdown", {"iata": "ANC"})],
             stop_reason="tool_use"),
        _msg([_text("At 3,000 sm the all-carrier share is 30.1%.")]),
        # Second turn calls no tools but re-quotes the earlier figures.
        _msg([_text("At 1,500 sm it is 48.3%, versus 30.1% at 3,000 sm.")]),
    ])
    orch.chat("ANC long haul?", session_id="s9")
    second = orch.chat("What about a 1,500 mile threshold?", session_id="s9")

    assert second.tool_calls == []
    assert second.audit["passed"], second.audit["summary"]
    assert second.degraded is False


def test_user_supplied_numbers_may_be_echoed(engine):
    """Echoing a figure the user typed is not fabrication."""
    orch = make_orch(engine, [_msg([_text("Using 1,500 miles as you asked.")])])
    reply = orch.chat("Use 1,500 miles instead", session_id="s10")
    assert reply.audit["passed"]


def test_audit_failure_without_tools_this_turn_still_regenerates(engine):
    """Previously an unverifiable answer with no tool calls this turn slipped
    through, because regeneration was gated on the turn having payloads."""
    orch = make_orch(engine, [
        _msg([_text("Unmet demand is 4,321,000 passengers.")]),
        _msg([_text("That figure is not available in this system.")]),
    ])
    reply = orch.chat("how many unmet passengers?", session_id="s11")
    assert reply.degraded is True
    assert "4,321,000" not in reply.answer


def test_comparison_context_survives_intervening_turns(engine):
    orch = make_orch(engine, [
        _msg([_tool_use("t1", "compare_airports", {"iatas": ["LAX", "SNA"]})],
             stop_reason="tool_use"),
        _msg([_text("Compared.")]),
        _msg([_tool_use("t2", "get_airport_profile", {"iata": "BOS"})],
             stop_reason="tool_use"),
        _msg([_text("BOS profile.")]),
        _msg([_text("Adding Burbank.")]),
    ])
    orch.chat("compare LA and Santa Ana", session_id="s12")
    orch.chat("tell me about Boston", session_id="s12")
    assert orch.store.get("s12").last_comparison == ["LAX", "SNA"]

    orch.chat("now add Burbank to that comparison", session_id="s12")
    system = orch.client.messages.calls[-1]["system"]
    joined = " ".join(b["text"] for b in system)
    assert "Most recent comparison" in joined and "LAX, SNA" in joined


def test_regeneration_prompt_is_marked_as_automated(engine):
    """Otherwise the model opens its rewrite apologising to the user for a
    correction the user never made."""
    orch = make_orch(engine, [
        _msg([_tool_use("t1", "get_airport_profile", {"iata": "SFO"})],
             stop_reason="tool_use"),
        _msg([_text("The figure is 987,654,321.")]),
        _msg([_text("Clean answer.")]),
    ])
    orch.chat("SFO?", session_id="s13")
    prompts_sent = [
        m["content"] for m in orch.store.get("s13").messages
        if m["role"] == "user" and isinstance(m["content"], str)
    ]
    correction = next(p for p in prompts_sent if "provenance check" in p)
    assert "not from the user" in correction
    assert "do not apologise" in correction.lower()


def test_session_memory_persists_across_turns(engine):
    orch = make_orch(engine, [
        _msg([_tool_use("t1", "rank_airports",
                        {"iatas": engine.resolve_region("new_england")})],
             stop_reason="tool_use"),
        _msg([_text("Ranked.")]),
        _msg([_text("Follow-up answer.")]),
    ])
    first = orch.chat("rank New England", session_id="s1")
    assert orch.store.get("s1").last_ranking

    orch.chat("what about the second one?", session_id="s1")
    system = orch.client.messages.calls[-1]["system"]
    joined = " ".join(b["text"] for b in system)
    assert "Most recent ranking" in joined
    assert first.session_id == "s1"


def test_ambiguous_resolution_is_recorded_as_an_assumption(engine):
    orch = make_orch(engine, [
        _msg([_tool_use("t1", "resolve_airports", {"queries": ["LA"]})],
             stop_reason="tool_use"),
        _msg([_text("Reading LA as LAX.")]),
    ])
    reply = orch.chat("compare LA and Santa Ana", session_id="s2")
    assert any("LAX" in a for a in reply.assumptions)


def test_sessions_are_isolated(engine):
    orch = make_orch(engine, [
        _msg([_text("a")]), _msg([_text("b")]),
    ])
    orch.chat("one", session_id="x")
    orch.chat("two", session_id="y")
    assert orch.store.get("x").turns == 1
    assert orch.store.get("y").turns == 1


def test_structured_reply_shape_for_frontend(engine):
    orch = make_orch(engine, [
        _msg([_tool_use("t1", "get_airport_profile", {"iata": "BOS"})],
             stop_reason="tool_use"),
        _msg([_text("BOS summary.")]),
    ])
    d = orch.chat("BOS?").to_dict()
    for key in (
        "answer", "session_id", "window", "tool_calls", "sources",
        "limitations", "assumptions", "focus_airports", "scores",
        "audit", "usage", "degraded",
    ):
        assert key in d
    assert d["scores"] and d["scores"][0]["tdpi"]["components"]
    assert json.dumps(d, default=str)


def test_api_errors_degrade_without_crashing(engine):
    import anthropic

    class Boom:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise anthropic.APIConnectionError(request=None)

    orch = Orchestrator(engine=engine, client=Boom(), store=SessionStore(), model="f")
    reply = orch.chat("hello")
    assert reply.degraded
    assert reply.error_category == "network"
    assert "analytics engine is unaffected" in reply.answer
    # Once the bounded retry budget is spent the message must not still claim
    # a retry is coming.
    assert "Retrying." not in reply.answer
    assert "within the retry budget" in reply.answer


def test_model_never_receives_a_calculation_tool():
    joined = " ".join(t["name"] for t in TOOL_SCHEMAS)
    for forbidden in ("calculate", "compute", "math", "eval"):
        assert forbidden not in joined.lower()


# ---------------------------------------------------------------------------
# Prompt contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "You do not calculate",
        "NOT a measurement of terminal capacity",
        "Profitability is out of scope",
        # Scoped to this system's datasets, not an absolute claim about all data.
        "cannot be quantified from the datasets this system uses",
        # Suppression is unknown, never low.
        "Absence of a measurement is NOT evidence",
        "PROXY",
    ],
)
def test_system_prompt_states_the_hard_constraints(phrase):
    assert phrase in SYSTEM_PROMPT


def test_system_prompt_is_stable_for_caching():
    """No timestamps or ids, or the cached prefix breaks every turn."""
    import re

    assert SYSTEM_PROMPT == SYSTEM_PROMPT
    assert not re.search(r"\d{4}-\d{2}-\d{2}T", SYSTEM_PROMPT)


def test_credentials_are_never_exposed():
    info = agent_config.describe_credentials()
    blob = json.dumps(info)
    assert "sk-ant-" not in blob
    assert set(info) >= {"api_key_loaded", "api_key_length", "model"}
