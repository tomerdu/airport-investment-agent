"""Phase 9.1 — the regeneration retry must see what the failed draft saw.

Phase 9's live run measured the defect: `_regenerate` built its own system
parameter — `SYSTEM_PROMPT` alone, with no `cache_control` — so the retry paid
full input rate for a prefix already cached (13.5% of that run's cost in one of
fourteen requests) and, more importantly, ran WITHOUT the data-context block, the
standing-limitations block and the conversation-state block. A correction step
that sees less than the draft it is correcting is the part that mattered.

The fix passes the caller's `system` list straight through. These tests pin that,
and pin the surrounding behaviour that must NOT have changed.

The regeneration request is identified by the absence of `tools`: the main loop
always sends `tools=TOOL_SCHEMAS`, the retry never does.

Offline — the model is a scripted fake.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.orchestrator import Orchestrator
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.session import SessionStore
from app.analytics import AnalyticsEngine


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


def make_orch(engine, script):
    return Orchestrator(engine=engine, client=FakeClient(script),
                        store=SessionStore(), model="fake")


# A figure that cannot appear in any tool payload, so the audit must reject it.
FABRICATED = "The terminal handles 987654.3 passengers per gate."


def chat_requests(orch):
    return [c for c in orch.client.messages.calls if "tools" in c]


def regen_requests(orch):
    return [c for c in orch.client.messages.calls if "tools" not in c]


def _failing_then_clean_script():
    """Tool call, a draft with a fabricated number, then a clean rewrite."""
    return [
        _msg([_tool_use("t1", "get_airport_profile", {"iata": "BOS"})]),
        _msg([_text(FABRICATED)]),
        _msg([_text("BOS scores 58.6 on TDPI — a proxy, not a capacity measure.")]),
    ]


# ---------------------------------------------------------------------------
# 1. The retry receives the intended system context
# ---------------------------------------------------------------------------


def test_regeneration_receives_the_same_system_blocks_as_the_draft(engine):
    orch = make_orch(engine, _failing_then_clean_script())
    orch.chat("What is the TDPI for Boston?")

    chats, regens = chat_requests(orch), regen_requests(orch)
    assert regens, "no regeneration request was made"
    assert regens[0]["system"] == chats[-1]["system"], (
        "the retry's system blocks differ from the failed generation's"
    )


def test_regeneration_system_includes_the_full_static_prefix(engine):
    """Not `SYSTEM_PROMPT` alone — the data-context and limitations blocks too."""
    orch = make_orch(engine, _failing_then_clean_script())
    orch.chat("What is the TDPI for Boston?")

    system = regen_requests(orch)[0]["system"]
    joined = "\n".join(b["text"] for b in system)
    assert orch._static_system in joined
    assert len(orch._static_system) > len(SYSTEM_PROMPT), (
        "precondition: the static prefix is more than SYSTEM_PROMPT"
    )
    assert "# Data context" in joined
    assert "# Standing limitations" in joined


def test_regeneration_carries_the_conversation_state_block(engine):
    """A retry on a later turn must still see focus and ranking state."""
    script = [
        # turn 1 — establishes a ranking in session state
        _msg([_tool_use("t1", "rank_airports", {"iatas": ["BOS", "BGR", "PWM"]})]),
        _msg([_text("Ranked.")]),
        # turn 2 — draft fabricates, then a clean rewrite
        _msg([_text(FABRICATED)]),
        _msg([_text("BOS leads on TDPI.")]),
    ]
    orch = make_orch(engine, script)
    first = orch.chat("Rank BOS, BGR and PWM.")
    orch.chat("Why the first one?", session_id=first.session_id)

    system = regen_requests(orch)[0]["system"]
    joined = "\n".join(b["text"] for b in system)
    assert "# Conversation state" in joined
    assert "Most recent ranking" in joined


# ---------------------------------------------------------------------------
# 2. Caching structure preserved
# ---------------------------------------------------------------------------


def test_regeneration_preserves_the_ephemeral_cache_breakpoint(engine):
    orch = make_orch(engine, _failing_then_clean_script())
    orch.chat("What is the TDPI for Boston?")

    system = regen_requests(orch)[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}, (
        "the retry lost the cache breakpoint, so it pays full input rate"
    )
    assert system[0]["text"] == orch._static_system


def test_the_cached_block_is_identical_across_chat_and_retry(engine):
    """Byte-identical, or the cache misses."""
    orch = make_orch(engine, _failing_then_clean_script())
    orch.chat("What is the TDPI for Boston?")
    assert (regen_requests(orch)[0]["system"][0]["text"]
            == chat_requests(orch)[0]["system"][0]["text"])


def test_the_retry_still_sends_no_tools(engine):
    """Unchanged behaviour: the correction rewrites prose, it does not re-query."""
    orch = make_orch(engine, _failing_then_clean_script())
    orch.chat("What is the TDPI for Boston?")
    for req in regen_requests(orch):
        assert "tools" not in req


# ---------------------------------------------------------------------------
# 3. An audit rejection still triggers the retry
# ---------------------------------------------------------------------------


def test_a_fabricated_number_triggers_regeneration(engine):
    orch = make_orch(engine, _failing_then_clean_script())
    reply = orch.chat("What is the TDPI for Boston?")
    assert len(regen_requests(orch)) == 1
    assert FABRICATED not in reply.answer, "the rejected draft reached the user"


def test_a_clean_draft_does_not_trigger_regeneration(engine):
    orch = make_orch(engine, [
        _msg([_tool_use("t1", "get_airport_profile", {"iata": "BOS"})]),
        _msg([_text("BOS scores 58.6 on TDPI.")]),
    ])
    reply = orch.chat("What is the TDPI for Boston?")
    assert regen_requests(orch) == []
    assert reply.audit["passed"] is True
    assert reply.degraded is False


def test_the_retry_instruction_is_framed_as_an_automated_check(engine):
    """Unchanged: a user-framed correction made the model apologise."""
    orch = make_orch(engine, _failing_then_clean_script())
    orch.chat("What is the TDPI for Boston?")

    # `messages` is recorded by reference and keeps growing after the call, so
    # search it rather than indexing the end.
    msgs = regen_requests(orch)[0]["messages"]
    instructions = [
        m["content"] for m in msgs
        if m["role"] == "user" and isinstance(m["content"], str)
        and "Automated provenance check" in m["content"]
    ]
    assert len(instructions) == 1
    instruction = instructions[0]
    assert "[Automated provenance check" in instruction
    assert "not from the user" in instruction
    assert "do not apologise" in instruction.lower()
    assert "987654.3" in instruction, "the offending figure should be named"


# ---------------------------------------------------------------------------
# 4. A successful retry returns the model's corrected prose
# ---------------------------------------------------------------------------


def test_successful_regeneration_returns_corrected_prose_not_the_fallback(engine):
    orch = make_orch(engine, _failing_then_clean_script())
    reply = orch.chat("What is the TDPI for Boston?")

    assert "58.6" in reply.answer
    assert reply.audit["passed"] is True
    assert reply.audit["unmatched"] == []
    # The templated fallback has a recognisable heading; this must not be it.
    assert "Deterministic figures" not in reply.answer
    assert reply.error_category is None


def test_successful_regeneration_is_flagged_as_regenerated(engine):
    """`degraded` means "went through the retry path", not "bad answer".

    The frontend renders it as a "regenerated" chip with the tooltip "The answer
    was regenerated after a provenance check" (`components/chat.tsx:129-133`),
    so True here is the transparency signal, not a quality warning. Pinned
    because the flag's name reads like a quality judgement and a future reader
    may be tempted to invert it — that would silently remove the disclosure.
    """
    orch = make_orch(engine, _failing_then_clean_script())
    reply = orch.chat("What is the TDPI for Boston?")
    assert reply.degraded is True
    assert reply.audit["passed"] is True      # ...and the answer is sound


# ---------------------------------------------------------------------------
# 5. Fallback behaviour unchanged
# ---------------------------------------------------------------------------


def test_fallback_when_the_retry_also_fails_the_audit(engine):
    """Two bad drafts: the user gets tool output rendered directly."""
    orch = make_orch(engine, [
        _msg([_tool_use("t1", "get_airport_profile", {"iata": "BOS"})]),
        _msg([_text(FABRICATED)]),
        _msg([_text("Still wrong: 987654.3 passengers per gate.")]),
    ])
    reply = orch.chat("What is the TDPI for Boston?")

    assert len(regen_requests(orch)) == 1, "exactly one retry, then fall back"
    assert "987654.3" not in reply.answer
    assert reply.degraded is True
    assert reply.audit["passed"] is False


def test_fallback_when_the_retry_request_itself_fails(engine):
    """An API failure during the retry must not lose the turn."""
    import anthropic

    class ExplodingMessages(FakeMessages):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            if "tools" not in kwargs:          # the retry
                raise anthropic.APIConnectionError(request=None)
            if not self.script:
                return _msg([_text("done")])
            return self.script.pop(0)

    orch = make_orch(engine, [])
    orch.client.messages = ExplodingMessages([
        _msg([_tool_use("t1", "get_airport_profile", {"iata": "BOS"})]),
        _msg([_text(FABRICATED)]),
    ])
    reply = orch.chat("What is the TDPI for Boston?")

    assert FABRICATED not in reply.answer
    assert reply.degraded is True
    assert reply.answer.strip(), "the fallback produced no text"


def test_the_fix_is_a_pass_through_not_a_rebuild(engine):
    """`_regenerate` must not construct a system parameter of its own.

    Guards the actual regression: a future edit that rebuilds the blocks inside
    the retry would reintroduce the drift even if the tests above still passed
    against a coincidentally-equal list.
    """
    import inspect

    src = inspect.getsource(Orchestrator._regenerate)
    # The docstring explains the old defect and names `cache_control`; strip it
    # so the check reads code only.
    code = src.replace(Orchestrator._regenerate.__doc__ or "\0", "")
    assert "system=system" in code
    assert "cache_control" not in code, "the retry is rebuilding the prefix again"
    assert "SYSTEM_PROMPT" not in code
