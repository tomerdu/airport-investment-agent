"""Phase 9 §3 — what can be evaluated without a live model.

Three things, none of which needs an API call:

1. **Matrix validity.** Ids unique, categories covered, every `expected_tools`
   entry is a real tool.
2. **Answerability.** For each case, call its expected tools against the real
   engine and check that every numeric expectation is actually present in the
   payload. This is the important one: it proves an expectation is *fair* before
   money is spent on it. If a figure the case demands is not in the data the
   model will see, the case is broken, not the agent.
3. **Guardrail reachability.** For the unsupported cases, confirm no tool *can*
   return the forbidden substance — the structural half of the guardrail, as
   opposed to the prompt half.

    python -m evaluation.run_offline
"""

from __future__ import annotations

import sys
from collections import Counter

from app.agent.audit import collect_numbers
from app.agent.tools import TOOL_NAMES, ToolBox
from app.analytics import AnalyticsEngine

from .agent_eval_cases import Absent, Number, cases, truth

# Which tool call stands in for each case when checking answerability. Derived
# from `expected_tools` plus the airport the question is about; a few cases need
# an explicit argument set.
PROBES: dict[str, list[tuple[str, dict]]] = {
    "R1-bos-tdpi": [("get_airport_profile", {"iata": "BOS"})],
    "R2-sfo-aci": [("get_airport_profile", {"iata": "SFO"})],
    "R3-lax-profile": [("get_airport_profile", {"iata": "LAX"})],
    "C1-lax-sna": [("compare_airports", {"iatas": ["LAX", "SNA"]})],
    "C2-bos-bgr": [("compare_airports", {"iatas": ["BOS", "BGR"]})],
    "C3-sea-pdx-den": [("compare_airports", {"iatas": ["SEA", "PDX", "DEN"]})],
    "K1-new-england": [("rank_airports", {"iatas": "__NEW_ENGLAND__"})],
    "K2-ordinal-followup": [("rank_airports", {"iatas": "__NEW_ENGLAND__"}),
                            ("get_airport_profile", {"iata": "BOS"})],
    "K3-aci-ranking": [("rank_airports",
                        {"iatas": ["BOS", "BGR", "ACK", "ASE"], "index": "ACI"})],
    "L1-anc-longhaul": [("long_haul_breakdown", {"iata": "ANC"})],
    "L2-anc-pax-vs-freight": [("long_haul_breakdown", {"iata": "ANC"})],
    "L3-unsupported-threshold": [("long_haul_breakdown", {"iata": "ANC"})],
    "U1-sfo-unmet": [("unmet_demand_evidence", {"iata": "SFO"})],
    "U2-weak-not-absence": [("unmet_demand_evidence", {"iata": "SFO"})],
    "U3-iag-band-ceiling": [("unmet_demand_evidence", {"iata": "IAG"})],
    "M1-suppressed-aci": [("get_airport_profile", {"iata": "HVN"})],
    "M2-ack-partial-temporal": [("get_airport_profile", {"iata": "ACK"})],
    "M3-ase-ceiling": [("get_airport_profile", {"iata": "ASE"})],
    "S1-compare-then-drill": [("compare_airports", {"iatas": ["LAX", "SNA"]}),
                              ("get_airport_profile", {"iata": "LAX"})],
    "S2-add-to-comparison": [("compare_airports", {"iatas": ["LAX", "SNA"]}),
                             ("get_airport_profile", {"iata": "ANC"}),
                             ("compare_airports",
                              {"iatas": ["LAX", "SNA", "BOS"]})],
    "A1-portland": [("resolve_airports", {"queries": ["Portland"]})],
    "V1-tdpi-proves-need": [("get_airport_profile", {"iata": "SFO"})],
    "V2-unmet-magnitude": [("unmet_demand_evidence", {"iata": "SFO"})],
    "V3-low-aci-spare-capacity": [("compare_airports", {"iatas": ["LAX", "BOS"]})],
}

# Cases with no probe: the correct behaviour is to call no tool at all.
NO_TOOL_EXPECTED = {"A2-vague-reference", "X1-non-us", "X2-stock-advice",
                    "X3-roi", "X4-forecast"}


def main() -> int:
    engine = AnalyticsEngine()
    tb = ToolBox(engine)
    cs = cases()
    problems: list[str] = []

    print(f"Phase 9 offline evaluation — {len(cs)} cases, window {engine.window}\n")

    # -- 1. matrix validity --------------------------------------------
    print("### 1. matrix validity")
    ids = [c.id for c in cs]
    dupes = [i for i, n in Counter(ids).items() if n > 1]
    print(f"  unique ids            : {'OK' if not dupes else f'DUPES {dupes}'}")
    if dupes:
        problems.append(f"duplicate ids {dupes}")
    cats = Counter(c.category for c in cs)
    print(f"  categories covered    : {len(cats)}/10  {dict(cats)}")
    if len(cats) != 10:
        problems.append("not all ten categories covered")
    bad_tools = {c.id: [t for t in c.expected_tools if t not in TOOL_NAMES]
                 for c in cs}
    bad_tools = {k: v for k, v in bad_tools.items() if v}
    print(f"  expected_tools valid  : {'OK' if not bad_tools else bad_tools}")
    if bad_tools:
        problems.append(f"unknown tools {bad_tools}")
    missing_probe = [c.id for c in cs
                     if c.id not in PROBES and c.id not in NO_TOOL_EXPECTED]
    print(f"  every case has a probe or is no-tool: "
          f"{'OK' if not missing_probe else missing_probe}")
    if missing_probe:
        problems.append(f"no probe for {missing_probe}")

    # -- 2. answerability ----------------------------------------------
    print("\n### 2. answerability — is every required figure in the payload?")
    ne = engine.resolve_region("new_england")
    checked = unanswerable = 0
    for c in cs:
        probes = PROBES.get(c.id)
        numeric = [f for f in c.required_facts if isinstance(f, Number)]
        if not probes or not numeric:
            continue
        pool: set[float] = set()
        for name, args in probes:
            a = dict(args)
            if a.get("iatas") == "__NEW_ENGLAND__":
                a["iatas"] = ne
            collect_numbers(tb.call(name, a), pool)
        for f in numeric:
            checked += 1
            ok = any(abs(v - f.value) <= max(f.tol, 0.051) for v in pool)
            if not ok:
                unanswerable += 1
                problems.append(f"{c.id}: {f.label}={f.value} absent from payload")
                print(f"  [MISS] {c.id:<26} {f.label} = {f.value}")
    print(f"  numeric expectations checked: {checked}, "
          f"present in payload: {checked - unanswerable}, missing: {unanswerable}")

    # -- 3. guardrail reachability -------------------------------------
    print("\n### 3. guardrail reachability — can a tool return the forbidden thing?")
    lhr = engine.get_metrics("LHR")
    print(f"  LHR in the warehouse            : {lhr is not None}  "
          f"(expected False)")
    if lhr is not None:
        problems.append("LHR is present; the non-US guardrail is not structural")
    from app.analytics.models import UnmetDemandEvidence
    ann = set(getattr(UnmetDemandEvidence, "__annotations__", {}))
    mag = [a for a in ann if any(w in a.lower() for w in
                                 ("magnitude", "unmet_passengers", "shortfall",
                                  "unserved", "estimate"))]
    print(f"  UnmetDemandEvidence magnitude field: {mag or 'none'}  "
          f"(expected none)")
    if mag:
        problems.append(f"magnitude-shaped field {mag}")
    cost_fields = [a for a in dir(engine) if any(
        w in a.lower() for w in ("cost", "roi", "revenue", "forecast"))]
    print(f"  engine cost/ROI/forecast methods   : {cost_fields or 'none'}  "
          f"(expected none)")
    if cost_fields:
        problems.append(f"engine exposes {cost_fields}")
    t = truth()
    print(f"  ANC supported thresholds           : {t['anc_thresholds']}  "
          f"(4,000 absent = L3 is genuinely unsupported)")
    if 4000 in t["anc_thresholds"]:
        problems.append("4,000 sm is a supported threshold; L3 is invalid")

    # -- 4. what still needs a live model ------------------------------
    print("\n### 4. what offline checks CANNOT establish")
    print("  * whether the model selects the right tool")
    print("  * whether it states a relation in the right direction")
    print("  * whether it declines an unsupported request instead of answering")
    print("  * whether it resists pressure to invent a magnitude")
    print("  * whether a follow-up resolves the intended airport")
    print("  These are exactly the properties the live sample is chosen for.")

    print("\n### static classification")
    for k, n in Counter(c.static_class for c in cs).most_common():
        print(f"  {k:<22} {n}")
    print(f"\n  live sample ({sum(1 for c in cs if c.live)}): "
          f"{[c.id for c in cs if c.live]}")

    engine.close()
    print("\n" + ("OFFLINE EVALUATION CLEAN — no broken expectations"
                 if not problems else f"PROBLEMS ({len(problems)}):"))
    for p in problems:
        print(f"  - {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
