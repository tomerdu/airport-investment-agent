"""Manual smoke test — the four mandatory assignment questions, once each.

⚠ THIS SPENDS API CREDITS. It makes at most four chat requests (one per
question), plus at most one regeneration each if the numeric audit rejects a
draft. Nothing else in the repo calls the API unless you ask it to.

It refuses to run without an explicit confirmation flag, prints a cost estimate
before and after, and can be limited to a single question.

    python smoke_test.py --list              # offline: show the checklist
    python smoke_test.py --dry-run           # offline: show what would be sent
    python smoke_test.py --confirm           # LIVE: all four questions
    python smoke_test.py --confirm --only 3  # LIVE: just question 3
    python smoke_test.py --confirm --fresh   # LIVE: a new session per question
"""

from __future__ import annotations

import argparse
import sys

from app.agent import config
from app.agent.orchestrator import Orchestrator
from app.agent.usage import TRACKER

# Exactly the four questions from the assignment brief.
CHECKLIST: list[tuple[str, str, list[str]]] = [
    (
        "Q1 — New England terminal expansion",
        "Which airports in New England are strong candidates for terminal expansion?",
        [
            "Ranks the New England cohort by TDPI",
            "States that no airport is a clean TERMINAL_LED case",
            "HVN's missing ACI is reported as unmeasured, NOT as low",
            "Explains the excluded small airports and the passenger floor",
            "Does not retype the ranking table already in the panel",
            "Does not claim terminal capacity is short or an expansion is profitable",
        ],
    ),
    (
        "Q2 — LAX vs SNA congestion",
        "Compare LA and Santa Ana airport congestion levels.",
        [
            "States the 'LA' → LAX reading as an assumption",
            "Resolves 'Santa Ana' to SNA",
            "Separates VOLUME from PER-FLIGHT INTENSITY",
            "Notes SNA is close to LAX per flight despite far less traffic",
            "Reports both TDPI and ACI, and the divergence class",
        ],
    ),
    (
        "Q3 — Anchorage long-haul percentage",
        "What is the percentage of long haul flights out of Anchorage airport?",
        [
            "Opens with the headline figure (~30.1% at >=3,000 sm)",
            "States the threshold AND the period in the first sentence",
            "Gives the passenger (~4.1%) vs freighter (~51.2%) split",
            "Accounts for combi aircraft (888 departures)",
            "Refers to the panel for the full sensitivity grid",
            "Does not describe passenger and cargo volumes as roughly equal",
        ],
    ),
    (
        "Q4 — SFO unmet-demand evidence",
        "What is the unmet flight demand in SFO airport and why?",
        [
            "States plainly that unmet demand cannot be measured",
            "Emits NO numeric unmet-demand figure",
            "Reports the indicator table and the evidence band (Weak)",
            "Reports U5 (fare premium) as unavailable, not as not-triggered",
        ],
    ),
]


def print_checklist() -> None:
    print("Manual smoke-test checklist — four mandatory questions\n")
    for i, (title, question, checks) in enumerate(CHECKLIST, 1):
        print(f"{i}. {title}")
        print(f'   Ask: "{question}"')
        for c in checks:
            print(f"     [ ] {c}")
        print()
    print("Also verify once, in the browser:")
    print("     [ ] The analytics panel matches the figures quoted in the answer")
    print("     [ ] The 'N figures verified' badge shows the audit passed")
    print("     [ ] Sources and the analysis window are visible")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="print the checklist and exit (offline)")
    ap.add_argument("--dry-run", action="store_true", help="show what would be sent (offline)")
    ap.add_argument("--confirm", action="store_true", help="REQUIRED to make live API calls")
    ap.add_argument("--only", type=int, metavar="N", help="run only question N (1-4)")
    ap.add_argument("--fresh", action="store_true", help="use a new session per question")
    args = ap.parse_args()

    if args.list:
        print_checklist()
        return 0

    selected = (
        [CHECKLIST[args.only - 1]] if args.only and 1 <= args.only <= len(CHECKLIST)
        else CHECKLIST
    )

    if args.dry_run or not args.confirm:
        print("OFFLINE — no API calls made.\n")
        print(f"Would send {len(selected)} chat request(s) to {config.MODEL}:")
        for title, question, _ in selected:
            print(f"  · {title}: {question}")
        print(
            "\nEach question is one request, plus at most one regeneration if the\n"
            "numeric audit rejects a draft. Re-run with --confirm to execute."
        )
        if not args.confirm:
            print("\nRefusing to spend credits without --confirm.")
        return 0

    orch = Orchestrator()
    print(f"LIVE RUN — model {orch.model}, window {orch.engine.window}")
    print(f"Questions: {len(selected)}\n")

    failures = 0
    for i, (title, question, checks) in enumerate(selected, 1):
        session = f"smoke-{i}" if args.fresh else "smoke"
        print("=" * 76)
        print(f"{title}\n{'=' * 76}")
        print(f"> {question}\n")

        reply = orch.chat(question, session_id=session)
        print(reply.answer.strip())

        tools = ", ".join(f"{c.name}{'' if c.ok else ' (ERROR)'}" for c in reply.tool_calls)
        print(f"\n  tools      : {tools or 'none'}")
        print(f"  audit      : {reply.audit['summary']}")
        print(f"  regenerated: {reply.degraded}")
        print(f"  tokens     : in={reply.usage.get('input_tokens', 0):,} "
              f"out={reply.usage.get('output_tokens', 0):,}")
        if reply.error_category:
            print(f"  ERROR      : {reply.error_category}")
            failures += 1
        if not reply.audit["passed"]:
            print("  ** NUMERIC AUDIT DID NOT PASS **")
            failures += 1

        print("\n  Check by eye:")
        for c in checks:
            print(f"    [ ] {c}")
        print()

    print("=" * 76)
    print("API USAGE THIS RUN")
    print("=" * 76)
    for line in TRACKER.summary_lines():
        print(line)
    print(f"\n  {TRACKER.totals()['estimate_disclaimer']}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
