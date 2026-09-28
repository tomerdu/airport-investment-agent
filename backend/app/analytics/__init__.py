"""Deterministic analytics and scoring engine.

No LLM, no randomness, no network. Every number the agent layer will ever
report originates here and arrives with its full derivation attached.
"""

from .engine import AnalyticsEngine, WINDOW_LABEL

__all__ = ["AnalyticsEngine", "WINDOW_LABEL"]
