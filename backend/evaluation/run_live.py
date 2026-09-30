"""Phase 9 §6 — execute the minimal live sample.

Costs real money, so:

* only the cases marked `live` in the matrix run — six of twenty-nine;
* every case runs in ONE process, so the cached system prefix is written once
  and read thereafter;
* cumulative estimated cost is checked after every turn and the run STOPS at
  `HARD_STOP`, before starting anything that would cross it;
* `--dry-run` prints the plan and the estimate and calls nothing.

Model configuration defaults to whatever `app.agent.config` holds. `--model`
overrides it for THIS RUN ONLY — the Orchestrator takes `model or config.MODEL`,
so omitting the flag leaves production behaviour exactly as it was. Added for the
final model-suitability check (Sonnet vs a smaller candidate); it changes no
default and no production file.

    python -m evaluation.run_live --dry-run
    python -m evaluation.run_live --confirm
    python -m evaluation.run_live --confirm --model claude-haiku-4-5-20251001 \
        --only C3-sea-pdx-den,S1-compare-then-drill --hard-stop 0.10
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from app.agent import config as agent_config
from app.agent.usage import TRACKER
from app.analytics import AnalyticsEngine

from .agent_eval_cases import Absent, EvalCase, Number, live_cases

# Hard ceiling for the whole phase. The spec's stop-line, enforced in code
# rather than trusted to judgement.
HARD_STOP = 0.20

# Observed on the v0.9.0 smoke test, used only for the pre-flight estimate.
EST_CACHE_WRITE_USD = 0.0189      # first request only
EST_TOOL_REQUEST_USD = 0.0022     # a tool-selection request, warm cache
EST_ANSWER_REQUEST_USD = 0.0093   # a final-answer request, warm cache


class _NoEffortMessages:
    """Delegates to the real messages resource, minus `output_config`."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def create(self, **kwargs: Any) -> Any:
        kwargs.pop("output_config", None)
        return self._inner.create(**kwargs)


class NoEffortClient:
    """Evaluation-only shim that strips the effort parameter.

    Claude Haiku 4.5 rejects it outright:

        400 invalid_request_error
        "This model does not support the effort parameter."

    The orchestrator always sends `output_config={"effort": ...}` because that is
    how depth is controlled on Sonnet 5, so a 4.5-generation candidate cannot run
    the production request shape unchanged. Rather than branch production code on
    the model id, the comparison drops the parameter here — inside evaluation
    tooling — and the report states the resulting asymmetry explicitly.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.messages = _NoEffortMessages(inner.messages)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def estimate(cs: list[EvalCase]) -> tuple[float, int]:
    """Rough pre-flight cost and request count. Deliberately pessimistic."""
    total = EST_CACHE_WRITE_USD
    requests = 0
    for c in cs:
        for turn_i, _ in enumerate(c.turns):
            # A turn that calls a tool is two requests; one that does not is one.
            calls_tool = bool(c.expected_tools) and turn_i == 0 or bool(c.expected_tools)
            if calls_tool:
                total += EST_TOOL_REQUEST_USD + EST_ANSWER_REQUEST_USD
                requests += 2
            else:
                total += EST_ANSWER_REQUEST_USD
                requests += 1
    return total, requests


def check(case: EvalCase, text: str) -> tuple[list[str], list[str]]:
    """Which required facts held, and which forbidden claims appeared."""
    met = [f.describe() for f in case.required_facts if f.holds(text)]
    unmet = [f.describe() for f in case.required_facts if not f.holds(text)]
    violated = [f.describe() for f in case.forbidden_claims if not f.holds(text)]
    return unmet, violated


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--confirm", action="store_true",
                    help="REQUIRED to make live API calls")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and estimate; call nothing")
    ap.add_argument("--out", default="evaluation/live_results.json")
    ap.add_argument("--model", default=None,
                    help="override the model for this run only "
                         "(default: app.agent.config.MODEL)")
    ap.add_argument("--only", default=None,
                    help="comma-separated case ids to run, from the live set")
    ap.add_argument("--hard-stop", type=float, default=HARD_STOP,
                    help=f"cumulative estimated-cost stop (default {HARD_STOP})")
    ap.add_argument("--no-effort", action="store_true",
                    help="strip output_config.effort — required for models that "
                         "reject it, e.g. Haiku 4.5")
    args = ap.parse_args()

    hard_stop = args.hard_stop
    model = args.model or agent_config.MODEL

    cs = live_cases()
    if args.only:
        wanted = [s.strip() for s in args.only.split(",") if s.strip()]
        known = {c.id for c in cs}
        unknown = [w for w in wanted if w not in known]
        if unknown:
            print(f"unknown case id(s): {unknown}\navailable: {sorted(known)}")
            return 1
        cs = [c for c in cs if c.id in wanted]

    est, est_requests = estimate(cs)

    print("Phase 9 — minimal live sample")
    print(f"model={model}  "
          f"effort={'STRIPPED' if args.no_effort else agent_config.EFFORT}  "
          f"max_tokens={agent_config.MAX_TOKENS}  "
          f"max_tool_hops={agent_config.MAX_TOOL_HOPS}")
    if args.no_effort:
        print("  (output_config.effort removed — this model rejects it; the "
              "comparison is asymmetric on that one parameter)")
    if args.model:
        print(f"  (model overridden for this run; production default is "
              f"{agent_config.MODEL} and is unchanged)")
    print(f"hard stop: ${hard_stop:.2f} cumulative estimated cost\n")
    print(f"{'case':<26}{'cat':<13}{'turns':>6}  rationale")
    for c in cs:
        print(f"{c.id:<26}{c.category:<13}{len(c.turns):>6}  "
              f"{(c.live_rationale or c.static_note)[:52]}")
    print(f"\n{len(cs)} cases, {sum(len(c.turns) for c in cs)} turns, "
          f"~{est_requests} model requests")
    print(f"pre-flight estimate: ~${est:.3f}  "
          f"({'WITHIN' if est <= hard_stop else 'OVER'} the ${hard_stop:.2f} stop)")
    if args.model:
        print("  (estimate constants were measured on Sonnet, so for a cheaper "
              "model it is an over-estimate — deliberately the safe direction)")

    if est > hard_stop:
        print("\nESTIMATE EXCEEDS THE STOP — reduce the sample before running.")
        return 1

    if args.dry_run or not args.confirm:
        print("\nDRY RUN — no API calls made. Re-run with --confirm to execute.")
        return 0

    # -- execute -------------------------------------------------------
    from app.agent.orchestrator import Orchestrator
    from app.agent.session import SessionStore

    TRACKER.reset()
    engine = AnalyticsEngine()
    client = None
    if args.no_effort:
        import anthropic
        client = NoEffortClient(
            anthropic.Anthropic(api_key=agent_config.get_api_key(), max_retries=0)
        )

    # model=None falls back to config.MODEL inside the Orchestrator.
    orch = Orchestrator(engine=engine, store=SessionStore(),
                        model=args.model, client=client)
    results: list[dict] = []
    stopped = None

    for c in cs:
        before_requests = len(TRACKER.requests)
        before_cost = TRACKER.totals()["estimated_cost_usd"]
        if before_cost >= hard_stop:
            stopped = f"cost stop reached before {c.id} (${before_cost:.4f})"
            print(f"\n!! {stopped}")
            break

        print(f"\n{'=' * 78}\n{c.id}  [{c.category}]  {c.static_class}\n{'=' * 78}")
        turns_out = []
        sid = None
        failed = False
        for i, q in enumerate(c.turns, 1):
            print(f"\n--- turn {i}: {q}")
            t0 = time.perf_counter()
            try:
                reply = orch.chat(q, session_id=sid)
            except Exception as exc:                      # noqa: BLE001
                print(f"    INFRASTRUCTURE FAILURE: {exc}")
                turns_out.append({"turn": i, "question": q,
                                  "error": str(exc), "kind": "INFRASTRUCTURE"})
                failed = True
                break
            sid = reply.session_id
            tools = [{"name": t.name, "input": t.input, "ok": t.ok}
                     for t in reply.tool_calls]
            print(f"    tools: {[t['name'] for t in tools] or 'none'}")
            print(f"    audit: passed={reply.audit['passed']} "
                  f"checked={reply.audit['numerals_checked']} "
                  f"unmatched={reply.audit['unmatched']}")
            print(f"    tokens: {reply.usage}  ({int((time.perf_counter()-t0)*1000)}ms)")
            print(f"    answer:\n{_indent(reply.answer)}")
            turns_out.append({
                "turn": i, "question": q, "answer": reply.answer,
                "tools": tools, "audit": reply.audit, "usage": reply.usage,
                "degraded": reply.degraded, "stop_reason": reply.stop_reason,
            })
            after = TRACKER.totals()["estimated_cost_usd"]
            if after >= hard_stop:
                stopped = (f"cost stop reached mid-case {c.id} after turn {i} "
                           f"(${after:.4f})")
                print(f"\n!! {stopped}")
                failed = True
                break

        joined = "\n\n".join(t.get("answer", "") for t in turns_out)
        unmet, violated = check(c, joined)
        case_requests = len(TRACKER.requests) - before_requests
        case_cost = TRACKER.totals()["estimated_cost_usd"] - before_cost
        case_tokens = _sum_tokens(TRACKER.requests[before_requests:])
        verdict = "PASS" if (not unmet and not violated and not failed) else "FAIL"
        print(f"\n    >>> {verdict}"
              + (f"  unmet={unmet}" if unmet else "")
              + (f"  VIOLATED={violated}" if violated else ""))
        print(f"    requests={case_requests}  est_cost=${case_cost:.4f}")

        results.append({
            "id": c.id, "category": c.category, "static_class": c.static_class,
            "verdict": verdict, "unmet_facts": unmet,
            "violated_claims": violated, "turns": turns_out,
            "requests": case_requests, "estimated_cost_usd": round(case_cost, 5),
            "tokens": case_tokens,
        })
        if stopped:
            break

    totals = TRACKER.totals()
    out = {
        "model": model,
        "production_model": agent_config.MODEL,
        "model_overridden": bool(args.model),
        "effort": None if args.no_effort else agent_config.EFFORT,
        "effort_stripped": bool(args.no_effort),
        "hard_stop_usd": hard_stop,
        "stopped_early": stopped,
        "cases": results,
        "totals": totals,
        "per_request": [r.to_dict() for r in TRACKER.requests],
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")

    print(f"\n{'=' * 78}\nTOTALS")
    print(f"  cases run       : {len(results)} of {len(cs)}")
    print(f"  model requests  : {totals['requests']}")
    print(f"  input tokens    : {totals['input_tokens']:,}")
    print(f"  output tokens   : {totals['output_tokens']:,}")
    print(f"  cache read      : {totals['cache_read_tokens']:,}")
    print(f"  cache write     : {totals['cache_write_tokens']:,}")
    print(f"  estimated cost  : ${totals['estimated_cost_usd']:.4f}  "
          f"(stop was ${hard_stop:.2f})")
    print(f"  passed          : {sum(1 for r in results if r['verdict'] == 'PASS')}"
          f"/{len(results)}")
    if stopped:
        print(f"  STOPPED EARLY   : {stopped}")
    print(f"\n  written to {path}")
    engine.close()
    return 0


def _indent(text: str, pad: str = "      ") -> str:
    return "\n".join(pad + ln for ln in text.splitlines())


def _sum_tokens(reqs) -> dict:
    out = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    for r in reqs:
        d = r.to_dict()
        out["input"] += d["input_tokens"]
        out["output"] += d["output_tokens"]
        out["cache_read"] += d["cache_read_tokens"]
        out["cache_write"] += d["cache_write_tokens"]
    return out


if __name__ == "__main__":
    sys.exit(main())
