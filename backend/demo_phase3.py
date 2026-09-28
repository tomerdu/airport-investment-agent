"""Live end-to-end demo: the four assignment questions plus follow-ups.

This is the only script that spends real tokens. It runs one session so the
follow-ups genuinely exercise conversational memory rather than restating
context.

    python demo_phase3.py            # all four questions + 4 follow-ups
    python demo_phase3.py --quick    # questions only
"""

from __future__ import annotations

import argparse
import sys

from app.agent.orchestrator import Orchestrator

SESSION = "demo"

QUESTIONS = [
    ("Q1", "Which airports in New England are strong candidates for terminal expansion?"),
    ("Q2", "Compare LA and Santa Ana airport congestion levels."),
    ("Q3", "What is the percentage of long haul flights out of Anchorage airport?"),
    ("Q4", "What is the unmet flight demand in SFO airport and why?"),
]

FOLLOW_UPS = [
    ("F1", "Why is the second one ranked there?"),
    ("F2", "What if we used 1,500 miles as the long-haul threshold instead?"),
    ("F3", "Now add Burbank to that congestion comparison."),
    ("F4", "What assumptions have you made so far in this conversation?"),
]


def show(label: str, question: str, reply) -> None:
    print("\n" + "=" * 78)
    print(f"{label}: {question}")
    print("=" * 78)

    if reply.tool_calls:
        print("\n-- tools called --")
        for call in reply.tool_calls:
            status = "ok" if call.ok else f"ERROR: {call.error}"
            args = ", ".join(f"{k}={v!r}" for k, v in call.input.items())
            print(f"   {call.name}({args[:90]}) [{call.duration_ms} ms] {status}")
    else:
        print("\n-- no tools called --")

    print("\n-- answer --")
    print(reply.answer.strip())

    print("\n-- verification --")
    print(f"   numeric audit : {reply.audit['summary']}")
    print(f"   window        : {reply.window}")
    print(f"   regenerated   : {reply.degraded}")
    print(f"   tokens        : in={reply.usage.get('input_tokens')} "
          f"out={reply.usage.get('output_tokens')}")
    if reply.focus_airports:
        print(f"   focus         : {', '.join(reply.focus_airports[:10])}")
    if reply.assumptions:
        for a in reply.assumptions:
            print(f"   assumption    : {a}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="skip the follow-ups")
    args = ap.parse_args()

    orch = Orchestrator()
    print(f"Model : {orch.model}")
    print(f"Window: {orch.engine.window}")
    print(f"Cohort: {orch.cohort_size if hasattr(orch, 'cohort_size') else orch.engine.cohort().size} airports")

    totals = {"input_tokens": 0, "output_tokens": 0}
    prompts = QUESTIONS if args.quick else QUESTIONS + FOLLOW_UPS

    for label, question in prompts:
        reply = orch.chat(question, session_id=SESSION)
        show(label, question, reply)
        totals["input_tokens"] += reply.usage.get("input_tokens", 0)
        totals["output_tokens"] += reply.usage.get("output_tokens", 0)

    session = orch.store.get(SESSION)
    print("\n" + "=" * 78)
    print("SESSION SUMMARY")
    print("=" * 78)
    print(f"  turns          : {session.turns}")
    print(f"  focus airports : {', '.join(session.focus_airports[:12])}")
    print(f"  last ranking   : {', '.join(session.last_ranking[:8])}")
    print(f"  assumptions    : {len(session.assumptions)}")
    print(f"  total tokens   : in={totals['input_tokens']:,} "
          f"out={totals['output_tokens']:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
