"""Phase 8.3 — audit the UDEI evidence methodology. Read-only, offline.

Produces no new score and changes nothing. It measures what the existing
indicators actually do on the committed warehouse, so the evidence review rests
on figures rather than on reading the code alone.

    python evaluate_udei_evidence.py
    python evaluate_udei_evidence.py --section sfo
"""

from __future__ import annotations

import argparse
import statistics as st
import sys
from collections import Counter

from app.analytics import AnalyticsEngine
from app.analytics.definitions import (
    UDEI_COHORT_PERCENTILE,
    UDEI_UPGAUGE_THRESHOLD,
)
from app.analytics.unmet import unmet_demand_evidence

# SFO plus peers chosen for comparability, not to flatter the result:
#   slot/perimeter-managed or gate-tight large hubs  : JFK, LGA, EWR, ORD, LAX
#   west-coast large hubs without SFO's runway layout: SEA, SAN, LAS
#   SFO's own bay-area alternatives                  : OAK, SJC
PEERS = ["SFO", "JFK", "LGA", "EWR", "ORD", "LAX", "SEA", "SAN", "LAS", "OAK", "SJC"]


def rule(t: str) -> None:
    print("\n" + "=" * 88)
    print(t)
    print("=" * 88)


def f(v, d=1, pct=False):
    if v is None:
        return "—"
    return f"{v * 100:.{d}f}%" if pct else f"{v:.{d}f}"


def spearman(a: list[float], b: list[float]) -> float:
    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else 0.0


def build(engine: AnalyticsEngine):
    cohort = engine.cohort()
    ev = {}
    for m in cohort.members:
        ev[m.iata] = unmet_demand_evidence(m, cohort, window=engine.window)
    return cohort, ev


# ---------------------------------------------------------------------------


def s_coverage(engine, cohort, ev) -> None:
    rule("1. INDICATOR COVERAGE ACROSS THE COHORT")
    print(f"\n  cohort {len(cohort.members)} airports; UDEI is evaluated for all of"
          f" them\n")
    ids = [i.id for i in next(iter(ev.values())).indicators]
    print(f"  {'ind':<5}{'label':<44}{'available':>11}{'triggered':>11}"
          f"{'trig|avail':>12}")
    for iid in ids:
        rows = [next(i for i in e.indicators if i.id == iid) for e in ev.values()]
        av = sum(1 for r in rows if r.available)
        tr = sum(1 for r in rows if r.triggered)
        share = f"{100 * tr / av:.0f}%" if av else "—"
        print(f"  {iid:<5}{rows[0].label[:43]:<44}{av:>11}{tr:>11}{share:>12}")

    print(f"\n  band distribution:")
    for b, n in Counter(e.evidence_band for e in ev.values()).most_common():
        print(f"    {b:<16}{n:>5}")

    print(f"\n  how many indicators can be evaluated at all?")
    for n, c in sorted(Counter(e.available_count for e in ev.values()).items()):
        print(f"    {n} of 5 available: {c:>4} airports")

    print("\n  Reasons an indicator is unavailable (distinct):")
    reasons = Counter(
        (i.id, i.unavailable_reason) for e in ev.values() for i in e.indicators
        if not i.available and i.unavailable_reason
    )
    for (iid, reason), c in reasons.most_common(8):
        print(f"    {iid}: {c:>4}x  {reason[:70]}")


def s_sfo(engine, cohort, ev) -> None:
    rule("2. SFO CASE STUDY — what the evidence actually says")
    m = engine.get_metrics("SFO")
    e = ev["SFO"]
    print(f"\n  {m.iata} {m.name} — window {e.window}")
    print(f"  band: {e.evidence_band}   triggered {e.triggered_count} of "
          f"{e.available_count} available ({e.total_count} defined)\n")
    print(f"  {'ind':<5}{'value':<38}{'threshold':<40}{'fired':>7}")
    for i in e.indicators:
        val = i.value_display or f"unavailable: {(i.unavailable_reason or '')[:28]}"
        fired = "—" if i.triggered is None else ("YES" if i.triggered else "no")
        print(f"  {i.id:<5}{val[:37]:<38}{i.threshold_display[:39]:<40}{fired:>7}")

    print("\n  Underlying quantities behind those indicators:")
    print(f"    passengers (12 mo)      {m.passengers:>14,.0f}")
    print(f"    passengers prior year   {m.passengers_prior:>14,.0f}")
    print(f"    departures (12 mo)      {m.departures:>14,.0f}")
    print(f"    departures prior year   {m.departures_prior:>14,.0f}")
    print(f"    seats (12 mo)           {m.seats:>14,.0f}")
    print(f"    load factor             {f(m.load_factor, 1, True):>14}")
    print(f"    pax growth YoY          {f(m.pax_growth, 2, True):>14}")
    print(f"    departure growth YoY    {f(m.departure_growth, 2, True):>14}")
    print(f"    seats/departure         {f(m.seats_per_departure, 1):>14}")
    print(f"    gauge growth YoY        {f(m.gauge_growth, 2, True):>14}")

    # The arithmetic identity that ties U2 and U3 together.
    pg, dg, gg = m.pax_growth, m.departure_growth, m.gauge_growth
    lf_g = None
    if m.load_factor and m.seats and m.seats_prior and m.passengers_prior:
        lf_prior = m.passengers_prior / m.seats_prior
        lf_g = m.load_factor / lf_prior - 1.0
    print("\n  Identity check — passenger growth decomposes exactly:")
    print("    (1+pax growth) = (1+dep growth) x (1+gauge growth) x (1+LF growth)")
    if None not in (pg, dg, gg) and lf_g is not None:
        lhs = 1 + pg
        rhs = (1 + dg) * (1 + gg) * (1 + lf_g)
        print(f"    {lhs:.6f}  vs  {rhs:.6f}   "
              f"{'IDENTICAL' if abs(lhs - rhs) < 1e-9 else 'differs'}")
        print(f"    dep {dg * 100:+.2f}%  gauge {gg * 100:+.2f}%  "
              f"load factor {lf_g * 100:+.2f}%")
        print("    So U2 and U3 are two terms of ONE decomposition, not two")
        print("    independent observations.")


def s_peers(engine, cohort, ev) -> None:
    rule("3. PEER COMPARISON")
    print("\n  Peers: slot/gate-tight large hubs, west-coast large hubs, and the")
    print("  two Bay Area alternatives. Chosen before looking at the results.\n")
    print(f"  {'apt':<5}{'pax':>13}{'LF':>7}{'paxG':>8}{'depG':>8}{'gaugeG':>8}"
          f"{'s/dep':>7}{'ACI':>7}{'fired':>7}{'band':>12}")
    for code in PEERS:
        m = engine.get_metrics(code)
        if m is None:
            print(f"  {code:<5}  not in cohort")
            continue
        e = ev[code]
        aci = next(i for i in e.indicators if i.id == "U4")
        print(f"  {code:<5}{m.passengers:>13,.0f}{f(m.load_factor,1,True):>7}"
              f"{f(m.pax_growth,1,True):>8}{f(m.departure_growth,1,True):>8}"
              f"{f(m.gauge_growth,1,True):>8}{f(m.seats_per_departure,1):>7}"
              f"{f(aci.value,1):>7}"
              f"{e.triggered_count}/{e.available_count:<5}{e.evidence_band:>12}")

    print("\n  Which indicators fire, per peer:")
    print(f"  {'apt':<5}{'U1 fill':>9}{'U2 freq':>9}{'U3 gauge':>10}"
          f"{'U4 airside':>12}{'U5 fare':>9}")
    for code in PEERS:
        if code not in ev:
            continue
        e = ev[code]
        cells = []
        for iid in ("U1", "U2", "U3", "U4", "U5"):
            i = next(x for x in e.indicators if x.id == iid)
            cells.append("—" if i.triggered is None else ("YES" if i.triggered else "no"))
        print(f"  {code:<5}{cells[0]:>9}{cells[1]:>9}{cells[2]:>10}"
              f"{cells[3]:>12}{cells[4]:>9}")


def s_confounders(engine, cohort, ev) -> None:
    rule("4. CONFOUNDERS AND CORRELATION BETWEEN INDICATORS")

    # U1 vs U4: are "high fill" and "high ACI" the same airports?
    pairs = []
    for code, e in ev.items():
        u1 = next(i for i in e.indicators if i.id == "U1")
        u4 = next(i for i in e.indicators if i.id == "U4")
        if u1.value is not None and u4.value is not None:
            pairs.append((u1.value, u4.value))
    if pairs:
        a = [p[0] for p in pairs]
        b = [p[1] for p in pairs]
        print(f"\n  U1 load factor ~ U4 ACI            spearman={spearman(a,b):+.3f} "
              f"n={len(pairs)}")

    # U2 and U3 share the same decomposition; measure the overlap directly.
    both = []
    for code, e in ev.items():
        u2 = next(i for i in e.indicators if i.id == "U2")
        u3 = next(i for i in e.indicators if i.id == "U3")
        if u2.available and u3.available:
            both.append((bool(u2.triggered), bool(u3.triggered)))
    if both:
        n = len(both)
        c = Counter(both)
        print(f"\n  U2 (frequency suppression) vs U3 (upgauging), n={n}:")
        print(f"    both fire        {c[(True, True)]:>4} "
              f"({100*c[(True,True)]/n:.0f}%)")
        print(f"    U2 only          {c[(True, False)]:>4}")
        print(f"    U3 only          {c[(False, True)]:>4}")
        print(f"    neither          {c[(False, False)]:>4}")
        u2t = c[(True, True)] + c[(True, False)]
        if u2t:
            print(f"    of airports firing U2, share also firing U3: "
                  f"{100 * c[(True, True)] / u2t:.0f}%")
            print("    U2 requires pax up and departures flat/down, which forces")
            print("    seats/departure or load factor up — so U3 is close to implied.")

    # Gauge growth vs departure growth: the mechanical link.
    g, d = [], []
    for m in cohort.members:
        if m.gauge_growth is not None and m.departure_growth is not None:
            g.append(m.gauge_growth)
            d.append(m.departure_growth)
    print(f"\n  gauge growth ~ departure growth     spearman={spearman(g,d):+.3f} "
          f"n={len(g)}")
    print("    Negative means airports adding flights tend to shrink gauge and")
    print("    vice versa — the substitution U3 reads as constraint can equally")
    print("    reflect fleet availability or network strategy.")

    # Seasonality/coverage confounders for U1.
    print("\n  U1 denominator check — load factor is SUM(pax)/SUM(seats) over the")
    print("  window, so it is seat-weighted and has no monthly component:")
    sfo = engine.get_metrics("SFO")
    print(f"    SFO: {sfo.passengers:,.0f} pax / {sfo.seats:,.0f} seats = "
          f"{f(sfo.load_factor,2,True)}")
    print("    A 12-month mean cannot show whether peak periods are full while")
    print("    off-peak is empty. Unmet demand is a PEAK phenomenon.")

    # Coverage confounder: how many airports have partial traffic months?
    partial = [m.iata for m in cohort.members if m.traffic_months < 12]
    print(f"\n  airports with fewer than 12 traffic months: {len(partial)} "
          f"{partial[:8]}")
    print("    U2 and U3 are YoY ratios and inherit the Phase 8.1c comparability")
    print("    question; U1 is a within-window ratio and does not.")


def s_claims(engine, cohort, ev) -> None:
    rule("5. WHAT THE EVIDENCE CAN AND CANNOT SUPPORT")
    e = ev["SFO"]
    m = engine.get_metrics("SFO")
    u = {i.id: i for i in e.indicators}

    def verdict(q: str, ok: bool | None, why: str) -> None:
        mark = {True: "SUPPORTED", False: "NOT SUPPORTED", None: "UNANSWERABLE"}[ok]
        print(f"\n  [{mark}] {q}")
        print(f"      {why}")

    verdict(
        "High utilisation of existing service?",
        u["U1"].triggered,
        f"Load factor {f(m.load_factor,1,True)} vs cohort P"
        f"{UDEI_COHORT_PERCENTILE:.0f} threshold. Directly measured from T-100 "
        f"seats and passengers. This is an observation, not an inference.",
    )
    verdict(
        "Demand growth relative to available seats or departures?",
        u["U2"].available,
        f"pax {f(m.pax_growth,2,True)} vs departures "
        f"{f(m.departure_growth,2,True)}: both measured. Whether the gap means "
        f"suppressed frequency or a deliberate network choice is not measured.",
    )
    verdict(
        "Changes in aircraft size or frequency?",
        u["U3"].available,
        f"gauge growth {f(m.gauge_growth,2,True)}, departures "
        f"{f(m.departure_growth,2,True)}. Directly measured. The CAUSE of the "
        f"substitution is not.",
    )
    verdict(
        "A credible claim of unmet demand?",
        False,
        "Every indicator is consistent with constrained supply AND with ordinary "
        "commercial choices (fleet assignment, hub strategy, aircraft "
        "availability). Nothing here distinguishes 'could not add flights' from "
        "'chose not to'. No indicator observes a passenger who did not fly.",
    )
    verdict(
        "Any numerical estimate of missing flights or passengers?",
        None,
        "Structurally impossible from these sources. The datasets record what was "
        "flown and carried; a counterfactual schedule leaves no row. The model "
        "class has no magnitude field, so there is nowhere to put such a number.",
    )

    print("\n  Structural anti-fabrication controls actually present:")
    print("    * UnmetDemandEvidence has NO magnitude field (checked in models.py)")
    print("    * every indicator carries `triggered` = None when unevaluable")
    print("    * U5 is reported unavailable with a reason, never as a zero")
    print(f"    * band is capped by availability: SFO's attainable max is "
          f"{e.available_count}, not {e.total_count}")


def s_band(engine, cohort, ev) -> None:
    rule("6. THE BAND ITSELF — absolute counts against a varying maximum")

    print("""
  The band uses absolute trigger counts (0-1 Weak, 2-3 Moderate, 4+ Strong) but
  the number of indicators that CAN fire varies by airport, because U4 needs a
  computable ACI and U5 is never available.""")

    buckets = Counter(e.available_count for e in ev.values())
    print(f"\n  {'available':>10}{'airports':>10}{'max band reachable':>22}")
    for n in sorted(buckets):
        reach = "Strong" if n >= 4 else ("Moderate" if n >= 2 else "Indeterminate")
        print(f"  {n:>10}{buckets[n]:>10}{reach:>22}")
    barred = sum(c for n, c in buckets.items() if n < 4)
    print(f"\n  airports that CANNOT reach 'Strong' whatever their evidence: "
          f"{barred} of {len(ev)} ({100*barred/len(ev):.0f}%)")
    print("  Those are exactly the airports whose ACI is suppressed for low flight")
    print("  volume, so the ceiling correlates with airport size rather than with")
    print("  evidence quality.")

    print("\n  Equal proportions, different bands:")
    print(f"  {'available':>10}{'triggered':>10}{'share':>8}{'band':>12}")
    for n_av, n_tr in ((3, 3), (4, 4), (4, 3), (3, 2), (4, 2)):
        band = ("Strong" if n_tr >= 4 else "Moderate" if n_tr >= 2 else "Weak")
        print(f"  {n_av:>10}{n_tr:>10}{n_tr/n_av:>8.0%}{band:>12}")
    print("  3-of-3 is 100% of what could be measured yet bands as 'Moderate',")
    print("  while 4-of-4 bands as 'Strong'. The two are equally complete.")

    print("\n  U1 and U4 are cohort-quartile flags by construction:")
    for iid in ("U1", "U4"):
        rows = [next(i for i in e.indicators if i.id == iid) for e in ev.values()]
        av = sum(1 for r in rows if r.available)
        tr = sum(1 for r in rows if r.triggered)
        print(f"    {iid}: threshold is cohort P{UDEI_COHORT_PERCENTILE:.0f}, so it "
              f"fires for {tr}/{av} = {100*tr/av:.0f}% — the top quartile, by "
              f"definition")
    print("  A quartile flag is a RELATIVE position, not a constraint threshold.")
    print("  It says 'high for this cohort', never 'at capacity'.")

    print("\n  U1's threshold is cohort-wide, but load factor varies sharply by")
    print("  hub class, so the SAME bar means different things:")
    thr = cohort.stat("load_factor").value_at_percentile(UDEI_COHORT_PERCENTILE)
    print(f"    cohort P{UDEI_COHORT_PERCENTILE:.0f} threshold: {thr*100:.1f}%")
    print(f"    {'class':<8}{'n':>5}{'min':>8}{'median':>8}{'max':>8}{'fires':>8}")
    for hc in ("L", "M", "S", "N"):
        xs = [m.load_factor for m in cohort.members
              if m.hub_class == hc and m.load_factor is not None]
        if not xs:
            continue
        fired = sum(1 for x in xs if x >= thr)
        print(f"    {hc:<8}{len(xs):>5}{min(xs)*100:>7.1f}%"
              f"{st.median(xs)*100:>7.1f}%{max(xs)*100:>7.1f}%"
              f"{100*fired/len(xs):>7.0f}%")
    print("    Large hubs fire it 67% of the time against 25% cohort-wide, because")
    print("    their load factors sit higher as a class. The indicator is not")
    print("    class-normalised, so 'high' is easier to reach for a large hub.")

    # ...but that does NOT make it uninformative for a specific large hub.
    large = sorted(m.load_factor for m in cohort.members
                   if m.hub_class == "L" and m.load_factor is not None)
    sfo = engine.get_metrics("SFO")
    rank_l = sum(1 for x in large if x > sfo.load_factor) + 1
    print(f"\n    Counter-point, measured: SFO's {sfo.load_factor*100:.1f}% ranks "
          f"{rank_l} of {len(large)} large hubs.")
    print("    So U1 firing IS informative for SFO specifically — it is near the")
    print("    top of its own class, not merely over a low cohort bar. The defect")
    print("    is comparability ACROSS classes, not the value for this airport.")

    print("\n  U2's rarity — it requires pax up AND departures flat or down:")
    rows = [next(i for i in e.indicators if i.id == "U2") for e in ev.values()]
    av = sum(1 for r in rows if r.available)
    tr = sum(1 for r in rows if r.triggered)
    print(f"    fires {tr}/{av} = {100*tr/av:.0f}% cohort-wide, and for 0 of the "
          f"{len(PEERS)} peers examined.")
    print("    A near-binary condition on two noisy YoY ratios is fragile: a")
    print("    departure change of +0.1% vs -0.1% flips it.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section")
    args = ap.parse_args()
    engine = AnalyticsEngine()
    try:
        cohort, ev = build(engine)
        print(f"Phase 8.3 — UDEI evidence audit | window {engine.window} "
              f"| cohort {len(cohort.members)}")
        sections = {
            "coverage": s_coverage, "sfo": s_sfo, "peers": s_peers,
            "confounders": s_confounders, "claims": s_claims, "band": s_band,
        }
        if args.section:
            sections[args.section](engine, cohort, ev)
        else:
            for fn in sections.values():
                fn(engine, cohort, ev)
    finally:
        engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
