"""Retry classification for Anthropic API failures.

The SDK retries some statuses automatically. That is fine for a transient 503
and actively harmful for a 401 or a credit-exhaustion 429: retrying cannot
succeed, and each attempt is another billable request or another lockout step.

This module states, explicitly and testably, which failures may be retried.

Never retried:
  * 401 / 403  authentication and permission — the key will not change mid-run
  * 400 / 404 / 422  malformed request or unknown model — deterministic failure
  * 402 and any 429 whose body indicates credit exhaustion — billing

Retried, bounded:
  * 408 / 409, 5xx, and connection errors, up to MAX_RETRIES attempts
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import anthropic

log = logging.getLogger("agent.retry")

# Deliberately low. A request that fails twice is not usually about to succeed,
# and every attempt costs credits.
MAX_RETRIES = int(os.environ.get("AGENT_MAX_RETRIES", "1"))

NEVER_RETRY_STATUSES = frozenset({400, 401, 402, 403, 404, 405, 422})
RETRY_STATUSES = frozenset({408, 409, 500, 502, 503, 504})

# A 429 usually means rate limiting (retryable). When the body mentions
# credit or quota exhaustion it is a billing failure and must not be retried.
_BILLING_HINT = re.compile(
    r"credit|quota|billing|insufficient|balance|payment|spend limit", re.I
)


@dataclass(frozen=True)
class RetryDecision:
    should_retry: bool
    reason: str
    category: str      # auth | billing | invalid_request | rate_limit | server | network | unknown
    user_message: str

    def final_message(self) -> str:
        """Message for the user once no further attempt will be made.

        A retryable category's message reads "Retrying." while attempts remain;
        once the budget is spent, saying that would be plainly wrong.
        """
        if self.should_retry:
            return self.user_message
        if self.category in ("rate_limit", "server", "network"):
            return {
                "rate_limit": (
                    "The Anthropic API rate-limited this request and it did not "
                    "succeed within the retry budget."
                ),
                "server": (
                    "Anthropic returned a server error and the request did not "
                    "succeed within the retry budget."
                ),
                "network": (
                    "Could not reach the Anthropic API within the retry budget."
                ),
            }[self.category]
        return self.user_message


def _category_message(category: str, status: int | None) -> str:
    return {
        "auth": (
            "The Anthropic API rejected the credentials. Check ANTHROPIC_API_KEY "
            "in .env — this will not resolve by retrying."
        ),
        "billing": (
            "The Anthropic account is out of credit or over its spend limit. "
            "Retrying would not help and would keep incurring attempts. The "
            "deterministic /analytics endpoints still work without the model."
        ),
        "invalid_request": (
            f"The request was rejected as invalid (HTTP {status}). This is a bug "
            f"in the request, not a transient failure, so it is not retried."
        ),
        "rate_limit": "Rate limited. Retrying after a short delay.",
        "server": f"Anthropic returned a server error (HTTP {status}). Retrying.",
        "network": "Could not reach the Anthropic API. Retrying.",
    }.get(category, f"Unexpected API failure (HTTP {status}).")


def classify(exc: BaseException, attempt: int = 0) -> RetryDecision:
    """Decide whether `exc` may be retried. `attempt` is 0-based."""
    budget_left = attempt < MAX_RETRIES

    if isinstance(exc, anthropic.APIConnectionError):
        return RetryDecision(
            budget_left, "network error", "network", _category_message("network", None)
        )

    status = getattr(exc, "status_code", None)
    body = ""
    try:
        body = str(getattr(exc, "message", "") or "")
    except Exception:  # noqa: BLE001
        pass

    if status == 429 or isinstance(exc, anthropic.RateLimitError):
        if _BILLING_HINT.search(body):
            return RetryDecision(
                False, "429 indicating credit/quota exhaustion", "billing",
                _category_message("billing", status),
            )
        return RetryDecision(
            budget_left, "rate limited", "rate_limit",
            _category_message("rate_limit", status),
        )

    if status in (401, 403) or isinstance(
        exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)
    ):
        return RetryDecision(
            False, f"authentication failure ({status})", "auth",
            _category_message("auth", status),
        )

    if status == 402 or _BILLING_HINT.search(body):
        return RetryDecision(
            False, "billing failure", "billing", _category_message("billing", status)
        )

    if status in NEVER_RETRY_STATUSES:
        return RetryDecision(
            False, f"non-retryable client error ({status})", "invalid_request",
            _category_message("invalid_request", status),
        )

    if status in RETRY_STATUSES or (status is not None and status >= 500):
        return RetryDecision(
            budget_left, f"server error ({status})", "server",
            _category_message("server", status),
        )

    return RetryDecision(False, f"unclassified failure ({status})", "unknown",
                         _category_message("unknown", status))


def log_decision(decision: RetryDecision, attempt: int) -> None:
    log.warning(
        "api-failure category=%s attempt=%d retry=%s reason=%s",
        decision.category, attempt, decision.should_retry, decision.reason,
    )
