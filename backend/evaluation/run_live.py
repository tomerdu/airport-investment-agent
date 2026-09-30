"""Phase 9 §6 — execute the minimal live sample.

Costs real money, so:

* only the cases marked `live` in the matrix run — six of twenty-nine;
* every case runs in ONE process, so the cached system prefix is written once
  and read thereafter;
* cumulative estimated cost is checked after every turn and the run STOPS at
  `HARD_STOP`, before starting anything that would cross it;
* `--dry-run` prints the plan and the estimate and calls nothing.

Model configuration is untouched: whatever `app.agent.config` holds is what runs.

    python -m evaluation.run_live --dry-run
    python -m evaluation.run_live --confirm
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

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
    args = ap.parse_args()

    cs = live_cases()
    est, est_requests = estimate(cs)

    print("Phase 9 — minimal live sample")
    print(f"model={agent_config.MODEL}  effort={agent_config.EFFORT}  "
          f"max_tokens={agent_config.MAX_TOKENS}  "
          f"max_tool_hops={agent_config.MAX_TOOL_HOPS}")
    print(f"hard stop: ${HARD_STOP:.2f} cumulative estimated cost\n")
    print(f"{'case':<26}{'cat':<13}{'turns':>6}  rationale")
    for c in cs:
        print(f"{c.id:<26}{c.category:<13}{len(c.turns):>6}  "
              f"{(c.live_rationale or c.static_note)[:52]}")
    print(f"\n{len(cs)} cases, {sum(len(c.turns) for c in cs)} turns, "
          f"~{est_requests} model requests")
    print(f"pre-flight estimate: ~${est:.3f}  "
          f"({'WITHIN' if est <= HARD_STOP else 'OVER'} the ${HARD_STOP:.2f} stop)")

    if est > HARD_STOP:
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
    orch = Orchestrator(engine=engine, store=SessionStore())
    results: list[dict] = []
    stopped = None

    for c in cs:
        before_requests = len(TRACKER.requests)
        before_cost = TRACKER.totals()["estimated_cost_usd"]
        if before_cost >= HARD_STOP:
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
            if after >= HARD_STOP:
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
        "model": agent_config.MODEL,
        "effort": agent_config.EFFORT,
        "hard_stop_usd": HARD_STOP,
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
          f"(stop was ${HARD_STOP:.2f})")
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
