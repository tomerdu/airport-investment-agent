"""Test configuration — and a hard guarantee that the default suite is offline.

Every agent test injects a fake client, but that is a convention: one forgotten
injection would silently bill a real request. This module turns the convention
into an enforced property.

Unless a test is marked `@pytest.mark.live`, constructing a real Anthropic
client raises immediately. `pytest.ini` additionally deselects `live` tests by
default, so the two mechanisms are belt and braces:

    pytest                    # offline, guaranteed — live clients raise
    pytest -m live            # opt in explicitly (spends credits)
"""

from __future__ import annotations

import anthropic
import pytest


class LiveApiCallBlocked(RuntimeError):
    """Raised when offline tests try to reach the Anthropic API."""


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live: test performs a REAL Anthropic API call and spends credits. "
        "Deselected by default; run with `pytest -m live`.",
    )


@pytest.fixture(autouse=True)
def _block_live_api(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Make real client construction impossible in offline tests.

    Only the client *constructors* are replaced. The exception classes
    (`anthropic.APIStatusError` and friends) stay intact, because the agent's
    error-handling tests raise and catch them.
    """
    if request.node.get_closest_marker("live"):
        return  # opted in explicitly

    def _blocked(*args, **kwargs):
        raise LiveApiCallBlocked(
            "A test tried to construct a real Anthropic client. The default "
            "suite must not spend API credits.\n"
            "  • Inject a fake client: Orchestrator(client=FakeClient(...))\n"
            "  • Or mark the test @pytest.mark.live and run `pytest -m live`."
        )

    monkeypatch.setattr(anthropic, "Anthropic", _blocked)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _blocked)
