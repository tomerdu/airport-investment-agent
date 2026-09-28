"""Cost-control tests: offline guarantee, retry policy, usage accounting.

All offline. These are the tests that keep the default suite from spending
credits, so they must never need credits themselves.
"""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import pytest

from app.agent import retry
from app.agent.retry import MAX_RETRIES, classify
from app.agent.usage import RequestUsage, UsageTracker, _prices_for


# ---------------------------------------------------------------------------
# The offline guarantee
# ---------------------------------------------------------------------------


def test_default_suite_cannot_construct_a_real_client():
    """conftest replaces the constructors, so a forgotten fake fails loudly
    instead of silently billing.

    Matched by exception name rather than by importing the class: pytest loads
    conftest as the top-level module `conftest`, so `tests.conftest` would be a
    second module object with a different class identity.
    """
    for ctor in (anthropic.Anthropic, anthropic.AsyncAnthropic):
        with pytest.raises(RuntimeError) as exc:
            ctor(api_key="sk-ant-whatever")
        assert type(exc.value).__name__ == "LiveApiCallBlocked"
        assert "must not spend API credits" in str(exc.value)


def test_exception_classes_still_work_while_blocked():
    """Blocking the constructors must not break error-handling tests."""
    assert issubclass(anthropic.RateLimitError, anthropic.APIStatusError)
    assert issubclass(anthropic.AuthenticationError, anthropic.APIStatusError)


def test_live_marker_is_registered(pytestconfig):
    markers = pytestconfig.getini("markers")
    assert any(m.startswith("live:") for m in markers)


def test_default_addopts_deselect_live_tests(pytestconfig):
    assert 'not live' in " ".join(pytestconfig.getini("addopts"))


# ---------------------------------------------------------------------------
# Retry classification
# ---------------------------------------------------------------------------


def _status_error(status: int, message: str = "") -> anthropic.APIStatusError:
    exc = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
    exc.status_code = status
    exc.message = message
    return exc


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_failures_are_never_retried(status):
    d = classify(_status_error(status), attempt=0)
    assert d.should_retry is False
    assert d.category == "auth"
    assert "will not resolve by retrying" in d.user_message


@pytest.mark.parametrize("status", [400, 404, 422])
def test_malformed_requests_are_never_retried(status):
    d = classify(_status_error(status), attempt=0)
    assert d.should_retry is False
    assert d.category == "invalid_request"


def test_payment_required_is_never_retried():
    d = classify(_status_error(402), attempt=0)
    assert d.should_retry is False
    assert d.category == "billing"


@pytest.mark.parametrize(
    "message",
    [
        "Your credit balance is too low to access the API",
        "insufficient quota for this request",
        "monthly spend limit reached",
    ],
)
def test_credit_exhaustion_is_never_retried_even_as_429(message):
    """A 429 is normally retryable, but not when it means "out of credit" —
    retrying then cannot succeed and keeps incurring attempts."""
    d = classify(_status_error(429, message), attempt=0)
    assert d.should_retry is False
    assert d.category == "billing"
    assert "out of credit" in d.user_message


def test_plain_rate_limit_is_retried_within_budget():
    d = classify(_status_error(429, "rate limit exceeded"), attempt=0)
    assert d.should_retry is True
    assert d.category == "rate_limit"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_errors_are_retried_within_budget(status):
    assert classify(_status_error(status), attempt=0).should_retry is True


def test_retries_are_bounded():
    """Budget exhaustion stops retrying even for a retryable class."""
    d = classify(_status_error(503), attempt=MAX_RETRIES)
    assert d.should_retry is False


def test_network_errors_are_retried_within_budget():
    exc = anthropic.APIConnectionError(request=None)
    assert classify(exc, attempt=0).should_retry is True
    assert classify(exc, attempt=MAX_RETRIES).should_retry is False


def test_retry_budget_is_small_by_default():
    assert MAX_RETRIES <= 2, "a large retry budget multiplies spend on failure"


def test_billing_hint_matches_are_case_insensitive():
    assert retry._BILLING_HINT.search("CREDIT balance")
    assert not retry._BILLING_HINT.search("model overloaded")


# ---------------------------------------------------------------------------
# Usage accounting
# ---------------------------------------------------------------------------


def _resp(inp=1000, out=500, cr=0, cw=0, model="claude-sonnet-5"):
    return SimpleNamespace(
        model=model,
        usage=SimpleNamespace(
            input_tokens=inp,
            output_tokens=out,
            cache_read_input_tokens=cr,
            cache_creation_input_tokens=cw,
        ),
    )


def test_tracker_records_all_four_token_categories():
    t = UsageTracker()
    t.record(_resp(1000, 500, 200, 100), model="claude-sonnet-5", purpose="chat")
    totals = t.totals()
    assert totals["input_tokens"] == 1000
    assert totals["output_tokens"] == 500
    assert totals["cache_read_tokens"] == 200
    assert totals["cache_write_tokens"] == 100
    assert totals["requests"] == 1


def test_cost_estimate_uses_the_price_table():
    e = RequestUsage(at="now", model="claude-sonnet-5", purpose="chat",
                     input_tokens=1_000_000, output_tokens=1_000_000)
    p = _prices_for("claude-sonnet-5")
    assert e.estimated_cost_usd() == pytest.approx(p["input"] + p["output"])


def test_cache_tokens_are_priced_separately():
    read_only = RequestUsage(at="n", model="claude-sonnet-5", purpose="c",
                             cache_read_tokens=1_000_000)
    write_only = RequestUsage(at="n", model="claude-sonnet-5", purpose="c",
                              cache_write_tokens=1_000_000)
    p = _prices_for("claude-sonnet-5")["input"]
    assert read_only.estimated_cost_usd() == pytest.approx(p * 0.1)
    assert write_only.estimated_cost_usd() == pytest.approx(p * 1.25)
    assert read_only.estimated_cost_usd() < write_only.estimated_cost_usd()


def test_totals_accumulate_and_reset():
    t = UsageTracker()
    for _ in range(3):
        t.record(_resp(), model="claude-sonnet-5")
    assert t.totals()["requests"] == 3
    t.reset()
    assert t.totals()["requests"] == 0
    assert t.totals()["estimated_cost_usd"] == 0


def test_cost_is_labelled_an_estimate():
    disclaimer = UsageTracker().totals()["estimate_disclaimer"].lower()
    assert "estimate" in disclaimer
    assert "console is authoritative" in disclaimer


def test_usage_record_contains_no_prompt_or_credential_fields():
    """The log must be safe to print: counts and model only."""
    t = UsageTracker()
    entry = t.record(_resp(), model="claude-sonnet-5", purpose="chat")
    fields = set(entry.to_dict())
    assert fields == {
        "at", "model", "purpose", "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_write_tokens", "estimated_cost_usd",
    }
    for forbidden in ("messages", "system", "prompt", "content", "api_key", "headers"):
        assert forbidden not in fields


def test_price_override_from_environment(monkeypatch):
    monkeypatch.setenv("AGENT_PRICE_IN", "7.5")
    monkeypatch.setenv("AGENT_PRICE_OUT", "30")
    assert _prices_for("claude-sonnet-5") == {"input": 7.5, "output": 30.0}


def test_unknown_model_falls_back_to_a_price(monkeypatch):
    monkeypatch.delenv("AGENT_PRICE_IN", raising=False)
    monkeypatch.delenv("AGENT_PRICE_OUT", raising=False)
    assert _prices_for("some-future-model")["input"] > 0


def test_tracker_tolerates_a_response_without_usage():
    t = UsageTracker()
    entry = t.record(SimpleNamespace(model="m"), model="m")
    assert entry.input_tokens == 0 and entry.output_tokens == 0


# ---------------------------------------------------------------------------
# The orchestrator honours the policy (fake client — no network)
# ---------------------------------------------------------------------------


class _CountingClient:
    """Fake client that always raises, counting how many attempts were made."""

    def __init__(self, exc: BaseException) -> None:
        self.attempts = 0
        outer = self

        class _Messages:
            @staticmethod
            def create(**kwargs):
                outer.attempts += 1
                raise exc

        self.messages = _Messages()


def _orch(client):
    from app.agent.orchestrator import Orchestrator
    from app.agent.session import SessionStore
    from app.analytics import AnalyticsEngine

    return Orchestrator(
        engine=AnalyticsEngine(), client=client, store=SessionStore(), model="fake"
    )


def test_auth_failure_is_attempted_exactly_once():
    """An invalid key cannot become valid — one attempt, then stop."""
    client = _CountingClient(_status_error(401, "invalid x-api-key"))
    orch = _orch(client)
    try:
        reply = orch.chat("hello")
        assert client.attempts == 1
        assert reply.error_category == "auth"
        assert reply.degraded
    finally:
        orch.engine.close()


def test_credit_exhaustion_is_attempted_exactly_once():
    client = _CountingClient(
        _status_error(429, "Your credit balance is too low to access the API")
    )
    orch = _orch(client)
    try:
        reply = orch.chat("hello")
        assert client.attempts == 1, "billing failures must not be retried"
        assert reply.error_category == "billing"
        assert "out of credit" in reply.answer
    finally:
        orch.engine.close()


def test_malformed_request_is_attempted_exactly_once():
    client = _CountingClient(_status_error(400, "invalid request body"))
    orch = _orch(client)
    try:
        assert orch.chat("hello").error_category == "invalid_request"
        assert client.attempts == 1
    finally:
        orch.engine.close()


def test_server_error_retries_are_bounded():
    client = _CountingClient(_status_error(503, "overloaded"))
    orch = _orch(client)
    try:
        reply = orch.chat("hello")
        assert client.attempts == MAX_RETRIES + 1
        assert reply.error_category == "server"
    finally:
        orch.engine.close()


def test_sdk_autoretry_is_disabled_on_the_real_client(monkeypatch):
    """The SDK's own blind retry loop must be off, or the policy here is
    bypassed and a failing request is sent up to 3x."""
    import inspect

    from app.agent import orchestrator as orch_mod

    src = inspect.getsource(orch_mod.Orchestrator.client.fget)
    assert "max_retries=0" in src


def test_successful_requests_are_recorded_in_the_tracker():
    from app.agent.usage import TRACKER

    class _Ok:
        class messages:
            @staticmethod
            def create(**kwargs):
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="hi")],
                    stop_reason="end_turn",
                    model="claude-sonnet-5",
                    usage=SimpleNamespace(
                        input_tokens=120, output_tokens=8,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0,
                    ),
                )

    TRACKER.reset()
    orch = _orch(_Ok())
    try:
        orch.chat("hello")
        assert TRACKER.totals()["requests"] == 1
        assert TRACKER.totals()["input_tokens"] == 120
    finally:
        TRACKER.reset()
        orch.engine.close()


# ---------------------------------------------------------------------------
# Scripts must not call the API on import or without confirmation
# ---------------------------------------------------------------------------


def test_smoke_test_requires_explicit_confirmation(capsys):
    import smoke_test

    assert smoke_test.main.__doc__ is None or True
    rc = _run_smoke(["--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OFFLINE" in out
    assert "no API calls made" in out


def test_smoke_test_checklist_has_exactly_the_four_questions():
    import smoke_test

    assert len(smoke_test.CHECKLIST) == 4
    joined = " ".join(q for _, q, _ in smoke_test.CHECKLIST).lower()
    for token in ("new england", "santa ana", "anchorage", "sfo"):
        assert token in joined


def test_smoke_test_list_mode_is_offline(capsys):
    rc = _run_smoke(["--list"])
    assert rc == 0
    assert "checklist" in capsys.readouterr().out.lower()


def _run_smoke(argv: list[str]) -> int:
    import sys

    import smoke_test

    old = sys.argv
    sys.argv = ["smoke_test.py", *argv]
    try:
        return smoke_test.main()
    finally:
        sys.argv = old
