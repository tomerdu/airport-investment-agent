"""The orchestrating agent: one model, six deterministic tools, a manual loop.

A manual loop rather than the SDK's beta tool_runner, because this layer needs
three things the runner does not expose: every tool call and result captured
for the structured API response, a numeric audit run over the draft before it
is returned, and session-state injection per turn. It also keeps the deliverable
off a beta dependency.

Model notes (Claude Sonnet 5):
  * `temperature` / `top_p` are rejected (400) — depth is controlled by
    `output_config.effort` instead.
  * Omitting `thinking` runs adaptive thinking.
  * Mid-conversation `role: "system"` messages are NOT supported, so per-turn
    state is appended to the top-level system as a separate block after the
    cached prefix.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic

from app.analytics import AnalyticsEngine

from . import config
from .audit import AuditResult, audit_text, render_fallback
from .prompts import (
    SYSTEM_PROMPT,
    data_context_block,
    limitations_block,
    session_state_block,
)
from .retry import MAX_RETRIES, RetryDecision, classify, log_decision
from .usage import TRACKER
from .session import Session, SessionStore
from .tools import TOOL_SCHEMAS, ToolBox, ToolError

log = logging.getLogger("agent")


class _ApiFailure(Exception):
    """An API request that will not be retried. Carries the user-facing text."""

    def __init__(self, decision: "RetryDecision") -> None:
        super().__init__(decision.user_message)
        self.decision = decision


def _auditable(payload: Any) -> Any:
    """A tool result with the model-invisible parts removed, for the numeric audit.

    The audit accepts any figure reachable in a remembered payload. The ACI
    temporal diagnostic's monthly series is deliberately withheld from the model
    (the panel renders it), so admitting its ~90 values per profile would widen
    the pool of "provenanced" numbers by about 40% without the model ever having
    been shown them. Everything the model *is* shown is summarised in the compact
    view and stays auditable; only the withheld series is dropped.

    The full payload still reaches the frontend unchanged — this affects the
    audit's number pool only.
    """
    if not isinstance(payload, dict):
        return payload

    def strip(scores: Any) -> Any:
        t = scores.get("temporal") if isinstance(scores, dict) else None
        if not isinstance(t, dict) or "months" not in t:
            return scores
        return {**scores, "temporal": {k: v for k, v in t.items() if k != "months"}}

    out = dict(payload)
    if isinstance(out.get("scores"), dict):
        out["scores"] = strip(out["scores"])
    if isinstance(out.get("airports"), list):
        out["airports"] = [
            {**a, "scores": strip(a["scores"])}
            if isinstance(a, dict) and isinstance(a.get("scores"), dict) else a
            for a in out["airports"]
        ]
    return out


@dataclass
class ToolInvocation:
    name: str
    input: dict[str, Any]
    ok: bool
    result: Any
    duration_ms: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "input": self.input,
            "ok": self.ok,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "result": self.result,
        }


@dataclass
class AgentReply:
    """Structured response — the contract the React frontend will consume."""

    answer: str
    session_id: str
    window: str
    tool_calls: list[ToolInvocation] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    focus_airports: list[str] = field(default_factory=list)
    scores: list[dict[str, Any]] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None
    degraded: bool = False
    # auth | billing | invalid_request | rate_limit | server | network | unknown
    error_category: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "session_id": self.session_id,
            "window": self.window,
            "tool_calls": [t.to_dict() for t in self.tool_calls],
            "sources": self.sources,
            "limitations": self.limitations,
            "assumptions": self.assumptions,
            "focus_airports": self.focus_airports,
            "scores": self.scores,
            "audit": self.audit,
            "usage": self.usage,
            "stop_reason": self.stop_reason,
            "degraded": self.degraded,
            "error_category": self.error_category,
        }


class Orchestrator:
    def __init__(
        self,
        engine: AnalyticsEngine | None = None,
        client: anthropic.Anthropic | None = None,
        store: SessionStore | None = None,
        model: str | None = None,
    ) -> None:
        self.engine = engine or AnalyticsEngine()
        self.toolbox = ToolBox(self.engine)
        self.store = store or SessionStore()
        self.model = model or config.MODEL
        self._client = client
        # Built once. Static across the whole process, so it sits inside the
        # cached system prefix instead of riding along with every tool result.
        from app.analytics.definitions import GLOBAL_LIMITATIONS

        self._static_system = (
            SYSTEM_PROMPT
            + data_context_block(self.engine.sources(), self.engine.window)
            + limitations_block(GLOBAL_LIMITATIONS)
        )

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            # max_retries=0 disables the SDK's own blind retry loop; retries
            # are governed by `retry.classify`, which refuses to re-send
            # authentication, billing or malformed-request failures.
            self._client = anthropic.Anthropic(
                api_key=config.get_api_key(), max_retries=0
            )
        return self._client

    def _create(self, *, purpose: str, **kwargs) -> Any:
        """One API request with bounded, classified retries and usage logging.

        Raises `_ApiFailure` carrying a user-facing message when the request
        cannot be completed.
        """
        last: RetryDecision | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.client.messages.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 — classified below
                decision = classify(exc, attempt)
                log_decision(decision, attempt)
                last = decision
                if not decision.should_retry:
                    raise _ApiFailure(decision) from exc
                continue
            TRACKER.record(response, model=kwargs.get("model", self.model),
                           purpose=purpose)
            return response

        raise _ApiFailure(
            last or classify(RuntimeError("retries exhausted"), MAX_RETRIES)
        )

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _run_tool(self, name: str, payload: dict[str, Any]) -> ToolInvocation:
        t0 = time.perf_counter()
        try:
            result = self.toolbox.call(name, payload)
            ok, err = True, None
        except ToolError as exc:
            result, ok, err = {"error": str(exc)}, False, str(exc)
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the turn
            log.exception("tool %s failed", name)
            result = {"error": f"Internal error in {name}: {exc}"}
            ok, err = False, str(exc)
        return ToolInvocation(
            name=name,
            input=payload,
            ok=ok,
            result=result,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            error=err,
        )

    # ------------------------------------------------------------------
    # State harvesting
    # ------------------------------------------------------------------

    @staticmethod
    def _harvest(session: Session, calls: list[ToolInvocation]) -> list[dict]:
        """Update session memory and collect score objects from tool results."""
        focus: list[str] = []
        scores: list[dict] = []

        for call in calls:
            if not call.ok or not isinstance(call.result, dict):
                continue
            res = call.result

            if call.name == "resolve_airports":
                for r in res.get("resolutions", []):
                    focus.extend(r.get("airports") or [])
                    if r.get("ambiguous") and r.get("interpretation"):
                        session.note_assumption(r["interpretation"])

            elif call.name == "get_airport_profile":
                apt = res.get("airport", {})
                if apt.get("iata"):
                    focus.append(apt["iata"])
                if res.get("scores"):
                    scores.append(res["scores"])

            elif call.name == "compare_airports":
                compared = [a["iata"] for a in res.get("airports", []) if a.get("iata")]
                session.set_comparison(compared)
                focus.extend(compared)
                for a in res.get("airports", []):
                    if a.get("scores"):
                        scores.append(a["scores"])

            elif call.name == "rank_airports":
                ordered = [r["iata"] for r in res.get("ranked", []) if r.get("iata")]
                session.set_ranking(ordered)
                focus.extend(ordered)
                scores.extend(r for r in res.get("ranked", []) if r.get("tdpi"))

            elif call.name in ("long_haul_breakdown", "unmet_demand_evidence"):
                if res.get("iata"):
                    focus.append(res["iata"])

        if focus:
            session.set_focus(focus)
        return scores

    @staticmethod
    def _collect(calls: list[ToolInvocation], key: str) -> list[Any]:
        seen: list[Any] = []
        for call in calls:
            if not call.ok or not isinstance(call.result, dict):
                continue
            for item in call.result.get(key) or []:
                if item not in seen:
                    seen.append(item)
        return seen

    # ------------------------------------------------------------------
    # Main turn
    # ------------------------------------------------------------------

    def chat(self, message: str, session_id: str | None = None) -> AgentReply:
        session = self.store.get_or_create(session_id)
        session.turns += 1
        session.messages.append({"role": "user", "content": message})
        # A figure the user themselves supplied ("use 1,500 miles instead") is
        # legitimate for the model to echo back.
        session.remember_numbers(message)
        session.trim()

        calls: list[ToolInvocation] = []
        usage = {"input_tokens": 0, "output_tokens": 0}
        stop_reason: str | None = None
        draft = ""

        system = [
            # Frozen prefix — cacheable across turns.
            {
                "type": "text",
                "text": self._static_system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        state = session_state_block(
            session.focus_airports,
            session.last_ranking,
            session.assumptions,
            session.last_comparison,
        )
        if state:
            system.append({"type": "text", "text": state})

        for hop in range(config.MAX_TOOL_HOPS + 1):
            try:
                response = self._create(
                    purpose="chat",
                    model=self.model,
                    max_tokens=config.MAX_TOKENS,
                    system=system,
                    tools=TOOL_SCHEMAS,
                    messages=session.messages,
                    output_config={"effort": config.EFFORT},
                )
            except _ApiFailure as failure:
                return self._error_reply(
                    session,
                    f"{failure.decision.final_message()} The analytics engine is "
                    f"unaffected — the deterministic /analytics endpoints still "
                    f"serve every figure without the model.",
                    category=failure.decision.category,
                )

            usage["input_tokens"] += response.usage.input_tokens
            usage["output_tokens"] += response.usage.output_tokens
            stop_reason = response.stop_reason

            if response.stop_reason == "refusal":
                return self._error_reply(
                    session, "The model declined to answer this request."
                )

            session.messages.append({"role": "assistant", "content": response.content})

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                draft = "".join(b.text for b in response.content if b.type == "text")
                break

            if hop == config.MAX_TOOL_HOPS:
                # Out of budget: stop calling tools, let the model answer from
                # what it has, and make the shortfall explicit.
                session.messages.append({
                    "role": "user",
                    "content": (
                        "Tool budget for this turn is exhausted. Answer using "
                        "only what the previous tool results contain, and state "
                        "clearly what you were unable to look up."
                    ),
                })
                continue

            results = []
            for block in tool_uses:
                call = self._run_tool(block.name, dict(block.input or {}))
                calls.append(call)
                # The model receives the compact view; `call.result` keeps the
                # full payload for the API response and the numeric audit.
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": self.toolbox.serialise_for_model(block.name, call.result),
                    **({"is_error": True} if not call.ok else {}),
                })
            # All results for one assistant turn go back in ONE user message.
            session.messages.append({"role": "user", "content": results})

        scores = self._harvest(session, calls)
        payloads = [c.result for c in calls if c.ok]
        for payload in payloads:
            session.remember_numbers(_auditable(payload))

        # Audited against the whole session, not just this turn: follow-ups
        # legitimately re-quote figures fetched earlier.
        audit = audit_text(draft, [], known=session.known_numbers)
        degraded = False

        if not audit.ok:
            log.warning("numeric audit failed: %s", audit.summary)
            draft, audit, degraded = self._regenerate(session, payloads, audit)

        session.messages.append({"role": "assistant", "content": draft})
        session.trim()

        return AgentReply(
            answer=draft,
            session_id=session.session_id,
            window=self.engine.window,
            tool_calls=calls,
            sources=self._collect(calls, "sources"),
            limitations=self._collect(calls, "limitations"),
            assumptions=list(session.assumptions),
            focus_airports=list(session.focus_airports),
            scores=scores,
            audit={
                "passed": audit.ok,
                "numerals_checked": audit.checked,
                "unmatched": audit.unmatched,
                "summary": audit.summary,
            },
            usage=usage,
            stop_reason=stop_reason,
            degraded=degraded,
        )

    # ------------------------------------------------------------------

    def _regenerate(
        self, session: Session, payloads: list[Any], first: AuditResult
    ) -> tuple[str, AuditResult, bool]:
        """One retry, then a templated fallback rendered from tool output.

        The retry instruction is phrased as an automated check rather than a
        user correction — otherwise the model opens its rewrite by apologising
        to the user for something the user never said.
        """
        session.messages.append({
            "role": "user",
            "content": (
                "[Automated provenance check — not from the user] These figures "
                f"in your draft do not appear in any tool result: "
                f"{', '.join(first.unmatched[:8])}. Produce the corrected "
                "answer directly, using only values present in the tool "
                "results. Do not calculate, derive or estimate any number; if a "
                "figure is not available, say it is not available. Do not "
                "mention this check, do not apologise, and do not refer to a "
                "previous draft — the user never saw it."
            ),
        })
        try:
            retry = self._create(
                purpose="regenerate",
                model=self.model,
                max_tokens=config.MAX_TOKENS,
                system=[{"type": "text", "text": SYSTEM_PROMPT}],
                messages=session.messages,
                output_config={"effort": config.EFFORT},
            )
        except _ApiFailure:
            # Cannot regenerate — fall back to rendering the tool output, which
            # is always correct even if it is not prose.
            return render_fallback(payloads or session.audit_payloads, ""), first, True

        text = "".join(b.text for b in retry.content if b.type == "text")
        second = audit_text(text, [], known=session.known_numbers)
        if second.ok:
            return text, second, True

        log.error("numeric audit failed twice: %s", second.summary)
        return render_fallback(payloads or session.audit_payloads, ""), second, True

    def _error_reply(
        self, session: Session, message: str, category: str = "unknown"
    ) -> AgentReply:
        return AgentReply(
            answer=message,
            session_id=session.session_id,
            window=self.engine.window,
            degraded=True,
            error_category=category,
            audit={"passed": True, "numerals_checked": 0, "unmatched": [],
                   "summary": "no model output to audit"},
        )
