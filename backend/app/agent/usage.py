"""Per-request token accounting and cost estimation.

Records only what Anthropic returns in `response.usage` — token counts and the
model id. It never records prompts, completions, credentials, headers or
anything derived from message content, so the log is safe to print and ship.

Costs here are **estimates**. They are computed from a local price table and
can drift from actual billing (published prices change, and cache, batch and
tier discounts are not modelled). The authoritative figure is always the
Anthropic console.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("agent.usage")

# USD per 1,000,000 tokens. Override with AGENT_PRICE_IN / AGENT_PRICE_OUT
# when prices change — these are a local estimate, not a billing source.
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}
FALLBACK_PRICE = {"input": 2.00, "output": 10.00}

# Cache reads bill at a fraction of the input rate; cache writes at a premium.
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


def _prices_for(model: str) -> dict[str, float]:
    env_in = os.environ.get("AGENT_PRICE_IN")
    env_out = os.environ.get("AGENT_PRICE_OUT")
    if env_in and env_out:
        try:
            return {"input": float(env_in), "output": float(env_out)}
        except ValueError:
            log.warning("AGENT_PRICE_IN/OUT are not numeric; using the built-in table")
    for key, prices in DEFAULT_PRICES.items():
        if model.startswith(key):
            return prices
    return FALLBACK_PRICE


@dataclass
class RequestUsage:
    """One API request's token counts. Contains no message content."""

    at: str
    model: str
    purpose: str                 # e.g. "chat", "regenerate", "preflight"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def billable_input(self) -> int:
        """Uncached input tokens — cache reads/writes are priced separately."""
        return self.input_tokens

    def estimated_cost_usd(self) -> float:
        p = _prices_for(self.model)
        return (
            self.input_tokens * p["input"]
            + self.cache_read_tokens * p["input"] * CACHE_READ_MULTIPLIER
            + self.cache_write_tokens * p["input"] * CACHE_WRITE_MULTIPLIER
            + self.output_tokens * p["output"]
        ) / 1_000_000

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["estimated_cost_usd"] = round(self.estimated_cost_usd(), 6)
        return d


@dataclass
class UsageTracker:
    """Cumulative usage for the life of this process."""

    requests: list[RequestUsage] = field(default_factory=list)
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, response: Any, *, model: str, purpose: str = "chat") -> RequestUsage:
        """Record one response's usage. Reads only `response.usage`."""
        u = getattr(response, "usage", None)
        entry = RequestUsage(
            at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            model=getattr(response, "model", None) or model,
            purpose=purpose,
            input_tokens=int(getattr(u, "input_tokens", 0) or 0),
            output_tokens=int(getattr(u, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(u, "cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        )
        with self._lock:
            self.requests.append(entry)

        log.info(
            "api-usage purpose=%s model=%s in=%d out=%d cache_read=%d "
            "cache_write=%d est_cost=$%.4f",
            entry.purpose, entry.model, entry.input_tokens, entry.output_tokens,
            entry.cache_read_tokens, entry.cache_write_tokens,
            entry.estimated_cost_usd(),
        )
        return entry

    # -- aggregates ------------------------------------------------------

    def totals(self) -> dict[str, Any]:
        with self._lock:
            rows = list(self.requests)
        return {
            "requests": len(rows),
            "input_tokens": sum(r.input_tokens for r in rows),
            "output_tokens": sum(r.output_tokens for r in rows),
            "cache_read_tokens": sum(r.cache_read_tokens for r in rows),
            "cache_write_tokens": sum(r.cache_write_tokens for r in rows),
            "estimated_cost_usd": round(sum(r.estimated_cost_usd() for r in rows), 4),
            "session_started_at": self.started_at,
            "estimate_disclaimer": (
                "Costs are ESTIMATES from a local price table "
                "(AGENT_PRICE_IN / AGENT_PRICE_OUT to override). They exclude "
                "tier and batch discounts and may drift from published prices. "
                "The Anthropic console is authoritative."
            ),
        }

    def summary_lines(self) -> list[str]:
        t = self.totals()
        return [
            f"  requests            : {t['requests']}",
            f"  input tokens        : {t['input_tokens']:,}",
            f"  output tokens       : {t['output_tokens']:,}",
            f"  cache read tokens   : {t['cache_read_tokens']:,}",
            f"  cache write tokens  : {t['cache_write_tokens']:,}",
            f"  estimated cost      : ${t['estimated_cost_usd']:.4f}  (ESTIMATE)",
        ]

    def reset(self) -> None:
        with self._lock:
            self.requests.clear()


# Process-wide tracker. The API exposes it at GET /usage.
TRACKER = UsageTracker()
