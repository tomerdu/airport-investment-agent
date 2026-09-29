"""Deterministic tests for conversational state.

Proposed in the architecture audit (docs/agent-architecture-interview-guide.md
section E) and implemented here. Four properties the rest of the suite exercised
only indirectly:

  1. an ordinal follow-up ("the second one") resolves to the right airport
  2. a comparison survives intervening turns that change the focus
  3. two sessions cannot see each other's history or numbers
  4. trimming never separates a `tool_use` from its `tool_result`

The fourth is the one that would break the API outright if it regressed: a
`tool_use` block with no matching result makes the next request invalid.

Offline throughout — the model is a scripted fake, so no API key and no network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent import config as agent_config
from app.agent.orchestrator import Orchestrator
from app.agent.prompts import session_state_block
from app.agent.session import Session, SessionStore
from app.analytics import AnalyticsEngine


# --- scripted model -------------------------------------------------------
# Mirrors the helpers in test_agent.py rather than importing them, so this file
# stays readable on its own.


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool_use(tid, name, payload):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=payload)


def _msg(content, stop_reason="end_turn", inp=100, out=50):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=inp, output_tokens=out),
    )


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            return _msg([_text("done")])
        return self.script.pop(0)


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)


@pytest.fixture(scope="module")
def engine():
    e = AnalyticsEngine()
    yield e
    e.close()


def make_orch(engine, script, store=None):
    # `is not None`, not `or` — an empty SessionStore is falsy (it defines
    # __len__), and this helper hit the very bug the tests below pin.
    return Orchestrator(
        engine=engine,
        client=FakeClient(script),
        store=store if store is not None else SessionStore(),
        model="fake",
    )


# ---------------------------------------------------------------------------
# 1. Ordinal follow-up — "why the second one?"
# ---------------------------------------------------------------------------


def test_ranking_state_block_numbers_the_airports():
    """The state block is what lets the model resolve an ordinal at all."""
    block = session_state_block(
        focus_airports=["HVN", "BOS", "BGR"],
        last_ranking=["HVN", "BOS", "BGR"],
        assumptions=[],
        last_comparison=[],
    )
    assert "1. HVN" in block
    assert "2. BOS" in block
    assert "3. BGR" in block
    assert "the second one" in block          # the instruction, verbatim


def test_ranking_is_harvested_from_a_rank_tool_call(engine):
    """A ranking must land in session state without the model being asked."""
    codes = engine.resolve_region("new_england")
    script = [
        _msg([_tool_use("t1", "rank_airports", {"iatas": codes, "index": "TDPI"})]),
        _msg([_text("Ranked by TDPI.")]),
    ]
    orch = make_orch(engine, script)
    reply = orch.chat("Rank New England by terminal demand pressure.")

    session = orch.store.get(reply.session_id)
    assert session.last_ranking[:3] == ["HVN", "BOS", "BGR"]
    assert session.focus_airports[:3] == ["HVN", "BOS", "BGR"]


def test_ordinal_followup_resolves_to_the_second_airport(engine):
    """End to end: rank, then ask about "the second one".

    The assertion is on what the SECOND request carries — the state block must
    name BOS as #2 — and on the tool call the model then makes. That is the
    mechanism; the model's prose is not under test.
    """
    codes = engine.resolve_region("new_england")
    script = [
        _msg([_tool_use("t1", "rank_airports", {"iatas": codes, "index": "TDPI"})]),
        _msg([_text("Ranked by TDPI: HVN, BOS, BGR.")]),
        # Turn two: the model reads the state block and profiles BOS.
        _msg([_tool_use("t2", "get_airport_profile", {"iata": "BOS"})]),
        _msg([_text("BOS detail.")]),
    ]
    orch = make_orch(engine, script)
    first = orch.chat("Rank New England by terminal demand pressure.")
    second = orch.chat("Why the second one?", session_id=first.session_id)

    # The state block reached the model on the follow-up request.
    system_blocks = orch.client.messages.calls[2]["system"]
    state = "\n".join(b["text"] for b in system_blocks)
    assert "2. BOS" in state

    # ...and the tool call it made was for that airport.
    profile_calls = [c for c in second.tool_calls if c.name == "get_airport_profile"]
    assert profile_calls and profile_calls[0].input["iata"] == "BOS"
    assert profile_calls[0].ok


# ---------------------------------------------------------------------------
# 2. Comparison persistence across intervening turns
# ---------------------------------------------------------------------------


def test_comparison_survives_turns_that_change_the_focus(engine):
    """`last_comparison` exists because focus moves faster than the comparison.

    Compare LAX/SNA, then spend two turns on unrelated airports, then confirm
    the comparison is still in the state block so "add Boston to that" resolves.
    """
    script = [
        _msg([_tool_use("t1", "compare_airports", {"iatas": ["LAX", "SNA"]})]),
        _msg([_text("Compared.")]),
        _msg([_tool_use("t2", "get_airport_profile", {"iata": "ANC"})]),
        _msg([_text("ANC detail.")]),
        _msg([_tool_use("t3", "get_airport_profile", {"iata": "SFO"})]),
        _msg([_text("SFO detail.")]),
        _msg([_text("Adding BOS to the LAX/SNA comparison.")]),
    ]
    orch = make_orch(engine, script)
    r = orch.chat("Compare LAX and SNA.")
    sid = r.session_id
    orch.chat("Now tell me about Anchorage.", session_id=sid)
    orch.chat("And San Francisco?", session_id=sid)

    session = orch.store.get(sid)
    # Focus has moved on...
    assert session.focus_airports == ["SFO"]
    # ...but the comparison is remembered.
    assert session.last_comparison == ["LAX", "SNA"]

    orch.chat("Add Boston to that comparison.", session_id=sid)
    state = "\n".join(
        b["text"] for b in orch.client.messages.calls[-1]["system"]
    )
    assert "LAX, SNA" in state
    assert "that comparison" in state          # the resolving instruction


def test_focus_and_comparison_are_separate_fields(engine):
    session = Session(session_id="s")
    session.set_comparison(["LAX", "SNA"])
    session.set_focus(["ANC"])
    assert session.last_comparison == ["LAX", "SNA"]
    assert session.focus_airports == ["ANC"]


# ---------------------------------------------------------------------------
# 3. Session isolation
# ---------------------------------------------------------------------------


def test_two_sessions_do_not_share_history_or_numbers(engine):
    """Distinct ids must not leak messages, focus or the audit number pool."""
    script = [
        _msg([_tool_use("a1", "get_airport_profile", {"iata": "BOS"})]),
        _msg([_text("BOS answer.")]),
        _msg([_tool_use("b1", "get_airport_profile", {"iata": "LAX"})]),
        _msg([_text("LAX answer.")]),
    ]
    store = SessionStore()
    orch = make_orch(engine, script, store=store)

    a = orch.chat("Tell me about Boston.", session_id="session-a")
    b = orch.chat("Tell me about LAX.", session_id="session-b")
    assert a.session_id == "session-a" and b.session_id == "session-b"

    sa, sb = store.get("session-a"), store.get("session-b")
    assert sa is not sb
    assert sa.focus_airports == ["BOS"]
    assert sb.focus_airports == ["LAX"]

    # No message object is shared between the two conversations.
    assert not any(m is n for m in sa.messages for n in sb.messages)
    # A's prose must not appear in B's history, or vice versa.
    assert "BOS answer." not in _flatten(sb.messages)
    assert "LAX answer." not in _flatten(sa.messages)

    # The audit pools are per-session, so B cannot provenance A's figures.
    bos_only = sa.known_numbers - sb.known_numbers
    assert bos_only, "expected BOS figures unique to session A"


def _flatten(messages) -> str:
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    out.append(str(b.get("content", "")))
                else:
                    out.append(str(getattr(b, "text", "")))
    return "\n".join(out)


def test_a_users_own_figure_does_not_leak_to_another_session(engine):
    """`remember_numbers(message)` admits user figures — per session only."""
    store = SessionStore()
    orch = make_orch(engine, [_msg([_text("ok")]), _msg([_text("ok")])], store=store)
    orch.chat("Use 1,500 miles instead.", session_id="s1")
    orch.chat("Anything.", session_id="s2")
    assert 1500.0 in store.get("s1").known_numbers
    assert 1500.0 not in store.get("s2").known_numbers


def test_an_injected_empty_store_is_actually_used(engine):
    """Regression: `SessionStore` defines __len__, so an empty store is FALSY.

    `self.store = store or SessionStore()` therefore discarded the caller's
    store whenever it was empty — which it always is at construction. main.py
    builds a store and passes it, so the app held two: `/health.active_sessions`
    always read 0 and GET/DELETE /sessions/{id} used a store that never received
    a session.
    """
    store = SessionStore()
    assert not store, "precondition: an empty SessionStore is falsy"
    orch = make_orch(engine, [_msg([_text("ok")])], store=store)
    assert orch.store is store

    reply = orch.chat("hello", session_id="wired")
    assert store.get("wired") is not None
    assert len(store) == 1
    assert reply.session_id == "wired"


def test_main_module_shares_one_store_with_its_orchestrator():
    """The wiring main.py actually performs."""
    import app.main as main_mod

    assert main_mod.orchestrator.store is main_mod.store


def test_store_reset_replaces_the_session_object(engine):
    store = SessionStore()
    orch = make_orch(engine, [_msg([_text("ok")])], store=store)
    orch.chat("hello", session_id="keep")
    before = store.get("keep")
    assert before.messages
    after = store.reset("keep")
    assert after is not before
    assert after.messages == []
    assert after.session_id == "keep"


def test_unknown_session_id_is_adopted_not_rejected(engine):
    """Documented behaviour, pinned so a change is deliberate.

    `POST /chat` with an id the store has never seen creates a session UNDER
    that id rather than erroring. Worth a test because it is a security-relevant
    surface: ids are the only isolation boundary.
    """
    store = SessionStore()
    orch = make_orch(engine, [_msg([_text("ok")])], store=store)
    reply = orch.chat("hello", session_id="never-seen-before")
    assert reply.session_id == "never-seen-before"
    assert store.get("never-seen-before") is not None


# ---------------------------------------------------------------------------
# 4. Trim-boundary integrity
# ---------------------------------------------------------------------------


def _tool_use_ids(messages) -> list[str]:
    ids = []
    for m in messages:
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if isinstance(c, list):
            ids += [getattr(b, "id", None) for b in c
                    if getattr(b, "type", None) == "tool_use"]
    return [i for i in ids if i]


def _tool_result_ids(messages) -> list[str]:
    ids = []
    for m in messages:
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, list):
            ids += [b.get("tool_use_id") for b in c
                    if isinstance(b, dict) and b.get("type") == "tool_result"]
    return [i for i in ids if i]


def test_every_retained_tool_use_keeps_its_result(engine):
    """The invariant `trim()` exists to protect.

    Drive more tool-using turns than the history budget, then assert that every
    `tool_use` still in `messages` has its matching `tool_result`. A dangling
    `tool_use` makes the next Messages API request invalid.
    """
    turns = agent_config.MAX_HISTORY_TURNS + 8
    script = []
    for i in range(turns):
        script.append(_msg([_tool_use(f"t{i}", "get_airport_profile",
                                      {"iata": "BOS"})]))
        script.append(_msg([_text(f"answer {i}")]))
    orch = make_orch(engine, script)

    sid = None
    for i in range(turns):
        reply = orch.chat(f"question {i}", session_id=sid)
        sid = reply.session_id
        msgs = orch.store.get(sid).messages
        uses, results = _tool_use_ids(msgs), _tool_result_ids(msgs)
        assert set(uses) == set(results), (
            f"turn {i}: dangling tool_use {set(uses) ^ set(results)}"
        )


def test_trim_keeps_the_budgeted_number_of_turns(engine):
    turns = agent_config.MAX_HISTORY_TURNS + 8
    script = []
    for i in range(turns):
        script.append(_msg([_tool_use(f"t{i}", "get_airport_profile",
                                      {"iata": "BOS"})]))
        script.append(_msg([_text(f"answer {i}")]))
    orch = make_orch(engine, script)

    sid = None
    for i in range(turns):
        sid = orch.chat(f"question {i}", session_id=sid).session_id

    session = orch.store.get(sid)
    starts = [m for m in session.messages if Session._is_turn_start(m)]
    assert len(starts) <= agent_config.MAX_HISTORY_TURNS
    # A tool-using turn is four messages, so a turn budget must retain far more
    # than `MAX_HISTORY_TURNS` raw messages — the bug this replaced.
    assert len(session.messages) > agent_config.MAX_HISTORY_TURNS


def test_trim_never_cuts_mid_turn():
    """The first retained message must always start a turn."""
    s = Session(session_id="s")
    for i in range(agent_config.MAX_HISTORY_TURNS + 5):
        s.messages.append({"role": "user", "content": f"q{i}"})
        s.messages.append({"role": "assistant",
                           "content": [_tool_use(f"t{i}", "x", {})]})
        s.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": "{}"}]})
        s.messages.append({"role": "assistant", "content": f"a{i}"})
        s.trim()
        assert Session._is_turn_start(s.messages[0]), "trim cut mid-turn"


def test_tool_result_message_is_not_counted_as_a_turn_start():
    """The distinction the trim bug turned on: a tool_result is role 'user'."""
    plain = {"role": "user", "content": "hello"}
    tool_res = {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]}
    assert Session._is_turn_start(plain) is True
    assert Session._is_turn_start(tool_res) is False
