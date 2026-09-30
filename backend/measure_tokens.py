"""Deterministic token measurement for the agent's request context.

Replays a fixed eight-turn scenario with scripted tool calls and counts the
input tokens each turn would send, using `messages.count_tokens` — no
generation, so the measurement is reproducible and costs effectively nothing.

Model output varies between demo runs, which makes end-to-end demo totals a
noisy baseline. This harness holds the tool calls constant so a before/after
comparison measures the optimisation rather than model whim.

    python measure_tokens.py
    python measure_tokens.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys

import anthropic

from app.agent import config
from app.agent.prompts import SYSTEM_PROMPT, session_state_block
from app.agent.tools import TOOL_SCHEMAS, ToolBox
from app.analytics import AnalyticsEngine

# One scenario, fixed: the four assignment questions followed by four
# conversational follow-ups. Fixed so before/after measures the change rather
# than model variance.
SCENARIO: list[tuple[str, str, list[tuple[str, dict]]]] = [
    ("Q1", "Which airports in New England are strong candidates for terminal expansion?",
     [("resolve_airports", {"queries": ["New England"]}),
      ("rank_airports", {"iatas": "__NEW_ENGLAND__"})]),
    ("Q2", "Compare LA and Santa Ana airport congestion levels.",
     [("resolve_airports", {"queries": ["LA", "Santa Ana"]}),
      ("compare_airports", {"iatas": ["LAX", "SNA"]})]),
    ("Q3", "What is the percentage of long haul flights out of Anchorage airport?",
     [("resolve_airports", {"queries": ["Anchorage"]}),
      ("long_haul_breakdown", {"iata": "ANC"})]),
    ("Q4", "What is the unmet flight demand in SFO airport and why?",
     [("resolve_airports", {"queries": ["SFO"]}),
      ("unmet_demand_evidence", {"iata": "SFO"})]),
    ("F1", "Why is the second one ranked there?",
     [("get_airport_profile", {"iata": "BOS"})]),
    ("F2", "What if we used 1,500 miles as the long-haul threshold instead?", []),
    ("F3", "Now add Burbank to that congestion comparison.",
     [("resolve_airports", {"queries": ["Burbank"]}),
      ("compare_airports", {"iatas": ["LAX", "SNA", "BUR"]})]),
    ("F4", "What assumptions have you made so far in this conversation?", []),
]

# Stand-in assistant replies, held constant across runs so the history grows
# identically in the before and after measurements.
REPLY = "Answer summarised here with the relevant figures and caveats."


def build_and_measure(client: anthropic.Anthropic, model: str) -> dict:
    engine = AnalyticsEngine()
    toolbox = ToolBox(engine)
    new_england = engine.resolve_region("new_england")

    messages: list[dict] = []
    rows = []
    payload_chars = 0

    for label, question, tool_calls in SCENARIO:
        messages.append({"role": "user", "content": question})

        for i, (name, raw_args) in enumerate(tool_calls):
            args = dict(raw_args)
            if args.get("iatas") == "__NEW_ENGLAND__":
                args["iatas"] = new_england
            result = toolbox.call(name, args)
            serialised = toolbox.serialise_for_model(name, result)
            payload_chars += len(serialised)

            messages.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": f"{label}_{i}",
                    "name": name,
                    "input": args,
                }],
            })
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": f"{label}_{i}",
                    "content": serialised,
                }],
            })

        system = [{"type": "text", "text": SYSTEM_PROMPT,
                   "cache_control": {"type": "ephemeral"}}]
        state = session_state_block(["BOS"], new_england[:8], ["Reading 'LA' as LAX."],
                                    ["LAX", "SNA"])
        if state:
            system.append({"type": "text", "text": state})

        counted = client.messages.count_tokens(
            model=model, system=system, tools=TOOL_SCHEMAS, messages=messages,
        )
        rows.append({"turn": label, "input_tokens": counted.input_tokens})

        messages.append({"role": "assistant", "content": REPLY})

    engine.close()
    total = sum(r["input_tokens"] for r in rows)
    return {
        "model": model,
        "turns": rows,
        "total_input_tokens": total,
        "tool_payload_chars": payload_chars,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write the measurement to this path")
    args = ap.parse_args()

    client = anthropic.Anthropic(api_key=config.get_api_key())
    result = build_and_measure(client, config.MODEL)

    print(f"Model: {result['model']}")
    print(f"{'Turn':<6} {'Input tokens':>14}")
    for row in result["turns"]:
        print(f"{row['turn']:<6} {row['input_tokens']:>14,}")
    print(f"{'TOTAL':<6} {result['total_input_tokens']:>14,}")
    print(f"\nTool payload sent to model: {result['tool_payload_chars']:,} chars")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        print(f"Written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
