"""Numeric provenance audit.

A system prompt is an instruction, not a control. This module is the control:
after the model drafts an answer, every numeral in it is checked against the
numbers that actually appeared in this turn's tool results. Unmatched figures
mean the model produced a number the deterministic engine never returned.

The audit is intentionally forgiving in the ways that do not matter and strict
in the way that does:

* rounding is allowed (82.64 -> "82.6%" -> "83%"), because the prompt permits
  rounding a tool value;
* a value is matched if it appears anywhere in the tool payloads, at any of
  several natural scalings (a load factor of 0.826 legitimately reads as
  "82.6%");
* small ordinals, years, counts and list indices are ignored — they are
  narrative scaffolding, not claims about the data.

A failure does not silently pass. The orchestrator regenerates once, then falls
back to a templated answer rendered directly from tool output.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

# Numbers that are structural rather than factual claims.
_ALLOWED_BARE = {
    0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0,
    12.0, 15.0, 100.0,
}
_YEAR_RANGE = (1900.0, 2100.0)

_NUMBER_RE = re.compile(
    r"""
    (?<![\w.])                 # not mid-identifier
    (-?\d{1,3}(?:,\d{3})+      # 1,234,567
      |-?\d+)                  # 1234
    (?:\.(\d+))?               # optional decimals
    """,
    re.VERBOSE,
)

# Markdown/markup that can contain digits we should not treat as claims.
_STRIP_PATTERNS = [
    re.compile(r"`[^`]*`"),            # inline code
    re.compile(r"```.*?```", re.S),    # fenced code
    re.compile(r"\[[^\]]*\]\([^)]*\)"),  # links
    re.compile(r"https?://\S+"),
    re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b"),   # ISO dates / YYYY-MM windows
    re.compile(r"\bT\d\b|\bA\d\b|\bU\d\b"),      # component ids
]


@dataclass
class AuditResult:
    ok: bool
    unmatched: list[str] = field(default_factory=list)
    checked: int = 0
    allowed: int = 0

    @property
    def summary(self) -> str:
        if self.ok:
            return f"{self.checked} numeral(s) checked, all traceable to tool output"
        return (
            f"{len(self.unmatched)} of {self.checked} numeral(s) not found in tool "
            f"output: {', '.join(self.unmatched[:8])}"
        )


def collect_numbers(payload: Any, out: set[float] | None = None) -> set[float]:
    """Every numeric value reachable in a tool result, at natural scalings."""
    if out is None:
        out = set()

    def add(v: float) -> None:
        if not math.isfinite(v):
            return
        out.add(v)
        out.add(abs(v))
        # A ratio reported as a percentage, and vice versa.
        out.add(abs(v) * 100.0)
        out.add(abs(v) / 100.0)
        # Large counts quoted in millions/thousands.
        if abs(v) >= 1000:
            out.add(abs(v) / 1000.0)
        if abs(v) >= 1_000_000:
            out.add(abs(v) / 1_000_000.0)

    if isinstance(payload, bool):
        return out
    if isinstance(payload, (int, float)):
        add(float(payload))
    elif isinstance(payload, str):
        for m in _NUMBER_RE.finditer(payload):
            try:
                add(float(m.group(0).replace(",", "")))
            except ValueError:
                pass
    elif isinstance(payload, dict):
        for v in payload.values():
            collect_numbers(v, out)
    elif isinstance(payload, (list, tuple)):
        for v in payload:
            collect_numbers(v, out)
    return out


def _matches(value: float, known: set[float]) -> bool:
    """True if `value` is a rounding of something the tools returned."""
    av = abs(value)
    for k in known:
        if k == 0 and av == 0:
            return True
        if k == 0:
            continue
        # Absolute tolerance scaled to the precision the model wrote.
        if abs(av - k) <= max(0.05, abs(k) * 0.012):
            return True
    return False


def audit_text(
    text: str,
    tool_payloads: list[Any],
    known: set[float] | None = None,
) -> AuditResult:
    """Check every factual numeral in `text` against known tool values.

    `known` carries numbers accumulated across the whole session. A follow-up
    that re-quotes a figure fetched several turns ago is legitimate reuse, so
    auditing against the current turn alone would flag correct answers.
    """
    known = set(known) if known else set()
    for p in tool_payloads:
        collect_numbers(p, known)

    cleaned = text
    for pat in _STRIP_PATTERNS:
        cleaned = pat.sub(" ", cleaned)

    unmatched: list[str] = []
    checked = allowed = 0

    for m in _NUMBER_RE.finditer(cleaned):
        raw = m.group(0)
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue

        # Structural numerals: small integers, years, ordinals.
        if value in _ALLOWED_BARE and "." not in raw:
            allowed += 1
            continue
        if _YEAR_RANGE[0] <= value <= _YEAR_RANGE[1] and "." not in raw and "," not in raw:
            allowed += 1
            continue

        checked += 1
        if not _matches(value, known):
            unmatched.append(raw)

    return AuditResult(
        ok=not unmatched, unmatched=unmatched, checked=checked, allowed=allowed
    )


def render_fallback(tool_payloads: list[dict[str, Any]], question: str) -> str:
    """Templated answer built straight from tool output.

    Used only when the model twice produced numerals that could not be traced.
    Deliberately plain: correctness over prose.
    """
    lines = [
        "I could not produce a narrative answer whose figures all trace back to "
        "the deterministic engine, so here is the tool output directly rather "
        "than risk an unverified number.",
        "",
    ]
    for payload in tool_payloads:
        if not isinstance(payload, dict):
            continue
        window = payload.get("window") or payload.get("period")
        if window:
            lines.append(f"**Analysis window:** {window}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(payload, indent=2, default=str)[:4000])
        lines.append("```")
        lines.append("")
    return "\n".join(lines)
