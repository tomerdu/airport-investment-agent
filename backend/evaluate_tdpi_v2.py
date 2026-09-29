"""Phase 8.1 — TDPI v1 vs candidate v2 evaluation.

Entirely offline: reads the committed warehouse, computes both composites for
the eligible cohort, and prints the evidence needed to decide whether to adopt
v2, modify it, or keep v1.

    python evaluate_tdpi_v2.py            # full report
    python evaluate_tdpi_v2.py --section correlation

No API calls, no downloads, no writes to the warehouse.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import Counter

from app.analytics import AnalyticsEngine
from app.analytics.definitions import (
    SEASONALITY_WARN_RATIO,
    TDPI_METRICS,
    TDPI_V2_METRICS,
)
from app.analytics.scoring import compute_tdpi, compute_tdpi_v2

NE_FOCUS = ["HVN", "BOS", "BGR", "BTV", "ACK", "BDL", "PVD", "PWM", "MHT", "MVY", "ORH"]


# ---------------------------------------------------------------------------
# statistics (pure Python — no scipy dependency)
# ---------------------------------------------------------------------------


def _ranks(xs: list[float]) -> list[float]:
    """Average ranks, ties shared."""
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


def pearson(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / math.sqrt(va * vb)


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    return pearson(_ranks(a), _ranks(b))


def paired(rows: list[dict], x: str, y: str) -> tuple[list[float], list[float]]:
    xs, ys = [], []
    for r in rows:
        if r[x] is not None and r[y] is not None:
            xs.append(r[x])
            ys.append(r[y])
    return xs, ys


# ---------------------------------------------------------------------------
# data assembly
# ---------------------------------------------------------------------------


def build(engine: AnalyticsEngine) -> list[dict]:
    cohort = engine.cohort()
    rows = []
    for m in cohort.members:
        v1 = compute_tdpi(m, cohort)
        v2 = compute_tdpi_v2(m, cohort)
        raw = {
            "pax_growth": m.pax_growth,
            "sustained_growth": m.sustained_growth,
            "peak_concentration": m.peak_concentration,
            "absolute_pax_growth": m.absolute_pax_growth,
            "pax_per_departure_growth": m.pax_per_departure_growth,
            "load_factor": m.load_factor,
            "seats_per_departure": m.seats_per_departure,
            "pax_per_runway": m.pax_per_runway,
            "enplanement_growth": m.enplanement_growth,
            "pax_per_departure": m.pax_per_departure,
        }
        rows.append({
            "iata": m.iata, "name": m.name, "state": m.state,
            "hub": m.hub_class, "region": m.region,
            "passengers": m.passengers, "departures": m.departures,
            "yoy_months": m.yoy_months_evaluable,
            "window_months": len(m.monthly_passengers),
            "v2_eligible": m.tdpi_v2_eligible,
            "v1": v1.score, "v1_cov": v1.coverage,
            "v2": v2.score, "v2_cov": v2.coverage,
            "v2_reason": v2.suppressed_reason,
            "v1_comp": {c.id: c for c in v1.components},
            "v2_comp": {c.id: c for c in v2.components},
            **raw,
        })
    return rows


def ranked(rows: list[dict], key: str) -> list[dict]:
    scored = [r for r in rows if r[key] is not None]
    scored.sort(key=lambda r: (-r[key], r["iata"]))
    for i, r in enumerate(scored, 1):
        r[f"rank_{key}"] = i
    return scored


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------


def rule(t: str) -> None:
    print(f"\n{'=' * 86}\n{t}\n{'=' * 86}")


def f1(v: float | None, dash: str = "—") -> str:
    """One-decimal, or a dash. Keeps format strings free of nested quoting."""
    return dash if v is None else f"{v:.1f}"


def f2(v: float | None, dash: str = "—") -> str:
    return dash if v is None else f"{v:.2f}"


def fpct(v: float | None, dash: str = "—") -> str:
    return dash if v is None else f"{v * 100:.0f}%"


def fnum(v: float | None, dash: str = "—") -> str:
    return dash if v is None else f"{v:,.0f}"


def s_definitions() -> None:
    rule("1. COMPONENT DEFINITIONS, WEIGHTS, SOURCES")
    for title, defs in (("TDPI v1 (production)", TDPI_METRICS),
                        ("TDPI v2 (candidate)", TDPI_V2_METRICS)):
        print(f"\n{title}")
        print(f"  {'id':<4} {'component':<44} {'wt':>5}  source")
        for d in defs:
            print(f"  {d.id:<4} {d.label[:44]:<44} {d.weight:>5.2f}  {d.source[:34]}")
        print(f"  {'':4} {'TOTAL':<44} {sum(d.weight for d in defs):>5.2f}")


def s_coverage(rows: list[dict]) -> None:
    rule("7a. DATA AVAILABILITY AND ELIGIBILITY")
    n = len(rows)
    v1n = sum(1 for r in rows if r["v1"] is not None)
    v2n = sum(1 for r in rows if r["v2"] is not None)
    print(f"  cohort with traffic          : {n}")
    print(f"  TDPI v1 scored               : {v1n}  ({100*v1n/n:.1f}%)")
    print(f"  TDPI v2 scored               : {v2n}  ({100*v2n/n:.1f}%)")
    print(f"  lost to v2 eligibility rules : {v1n - v2n}")

    print("\n  v2 suppression reasons:")
    for reason, k in Counter(
        r["v2_reason"] for r in rows if r["v2"] is None
    ).most_common():
        print(f"    {str(reason):<26} {k}")

    print("\n  per-component availability (of the scored cohort):")
    for d in TDPI_V2_METRICS:
        have = sum(1 for r in rows if r[d.attr] is not None)
        print(f"    {d.id} {d.label[:40]:<40} {have:>4}/{n}")

    print("\n  YoY month-pair distribution:")
    for pairs, k in sorted(Counter(r["yoy_months"] for r in rows).items(), reverse=True):
        print(f"    {pairs:>2} pairs : {k:>4} airports")


def s_top20(rows: list[dict]) -> None:
    rule("3. TOP-20 RANKING COMPARISON")
    a, b = ranked(list(rows), "v1"), ranked(list(rows), "v2")
    r1 = {r["iata"]: r["rank_v1"] for r in a}
    r2 = {r["iata"]: r["rank_v2"] for r in b}

    print(f"\n  {'#':>3} {'v1':<6} {'score':>6} {'v2 rank':>8}  |  "
          f"{'#':>3} {'v2':<6} {'score':>6} {'v1 rank':>8}  {'move':>6}")
    for i in range(20):
        L = a[i] if i < len(a) else None
        R = b[i] if i < len(b) else None
        lm = r2.get(L["iata"], "—") if L else ""
        rm = r1.get(R["iata"], "—") if R else ""
        move = ""
        if R and isinstance(rm, int):
            d = rm - R["rank_v2"]
            move = f"{d:+d}" if d else "0"
        print(f"  {i+1:>3} {L['iata'] if L else '':<6} {L['v1'] if L else 0:>6.1f} "
              f"{str(lm):>8}  |  {i+1:>3} {R['iata'] if R else '':<6} "
              f"{R['v2'] if R else 0:>6.1f} {str(rm):>8}  {move:>6}")

    both = [(r1[k], r2[k]) for k in r1 if k in r2]
    if both:
        rho = spearman([x for x, _ in both], [y for _, y in both])
        moves = [abs(x - y) for x, y in both]
        moves.sort()
        print(f"\n  airports ranked by both : {len(both)}")
        print(f"  Spearman rank corr      : {rho:.3f}")
        print(f"  median |rank move|      : {moves[len(moves)//2]:.0f}")
        print(f"  90th pct |rank move|    : {moves[int(len(moves)*0.9)]:.0f}")
        print(f"  max |rank move|         : {moves[-1]:.0f}")
        print(f"  top-20 overlap          : "
              f"{len({r['iata'] for r in a[:20]} & {r['iata'] for r in b[:20]})}/20")


def s_new_england(rows: list[dict]) -> None:
    rule("4. NEW ENGLAND COMPARISON")
    a, b = ranked(list(rows), "v1"), ranked(list(rows), "v2")
    r1 = {r["iata"]: r["rank_v1"] for r in a}
    r2 = {r["iata"]: r["rank_v2"] for r in b}
    ne = [r for r in rows if r["region"] == "new_england"]
    ne.sort(key=lambda r: -(r["v1"] if r["v1"] is not None else -1))

    print(f"\n  {'apt':<5} {'v1':>6} {'v2':>7} {'Δ':>6} {'v1 rk':>6} {'v2 rk':>6} "
          f"{'move':>6} {'pax':>10} {'peak':>6} {'sust':>6}")
    for r in ne:
        v1s = f1(r["v1"])
        v2s = f1(r["v2"], dash="suppr")
        delta = ("—" if r["v1"] is None or r["v2"] is None
                 else f"{r['v2'] - r['v1']:+.1f}")
        k1, k2 = r1.get(r["iata"], "—"), r2.get(r["iata"], "—")
        mv = f"{k1 - k2:+d}" if isinstance(k1, int) and isinstance(k2, int) else "—"
        print(f"  {r['iata']:<5} {v1s:>6} {v2s:>7} {delta:>6} {str(k1):>6} "
              f"{str(k2):>6} {mv:>6} {fnum(r['passengers']):>10} "
              f"{f2(r['peak_concentration']):>6} {fpct(r['sustained_growth']):>6}")

    rule("4b. FOCUS AIRPORT COMPONENT DETAIL")
    for code in NE_FOCUS[:5]:
        r = next((x for x in rows if x["iata"] == code), None)
        if not r:
            continue
        print(f"\n  {code} — {r['name'][:46]}  ({(r['passengers'] or 0):,.0f} pax)")
        print(f"    {'':4} {'component':<40} {'raw':>14} {'norm':>7} {'contrib':>8}")
        for tag, comps in (("v1", r["v1_comp"]), ("v2", r["v2_comp"])):
            for cid, c in comps.items():
                print(f"    {tag:<4} {c.label[:40]:<40} "
                      f"{str(c.raw_display or 'n/a'):>14} "
                      f"{(f'{c.normalized:.1f}' if c.normalized is not None else '—'):>7} "
                      f"{(f'{c.contribution:.2f}' if c.contribution is not None else '—'):>8}")
            score = r["v1"] if tag == "v1" else r["v2"]
            print(f"    {tag:<4} {'=> SCORE':<40} "
                  f"{'':>14} {'':>7} {(f'{score:.1f}' if score is not None else 'suppr'):>8}")


def s_correlation(rows: list[dict]) -> None:
    rule("5. CORRELATION / REDUNDANCY BETWEEN v2 COMPONENTS")
    attrs = [(d.id, d.attr, d.label) for d in TDPI_V2_METRICS]
    print(f"\n  Pearson (upper) / Spearman (lower), raw values\n")
    print("       " + "".join(f"{i:>10}" for i, _, _ in attrs))
    for i, (ida, aa, _) in enumerate(attrs):
        cells = []
        for j, (_, ab, _) in enumerate(attrs):
            if i == j:
                cells.append(f"{'—':>10}")
                continue
            xs, ys = paired(rows, aa, ab)
            v = pearson(xs, ys) if j > i else spearman(xs, ys)
            cells.append(f"{v:>10.3f}" if v is not None else f"{'n/a':>10}")
        print(f"  {ida:<4}" + "".join(cells))

    print("\n  Flagged pairs (|r| >= 0.60):")
    flagged = False
    for i, (ida, aa, la) in enumerate(attrs):
        for (idb, ab, lb) in attrs[i + 1:]:
            xs, ys = paired(rows, aa, ab)
            p, s = pearson(xs, ys), spearman(xs, ys)
            if p is not None and (abs(p) >= 0.60 or (s and abs(s) >= 0.60)):
                flagged = True
                print(f"    {ida}~{idb}  pearson={p:+.3f} spearman={s:+.3f}  "
                      f"n={len(xs)}\n        {la[:38]} vs {lb[:38]}")
    if not flagged:
        print("    none")

    print("\n  Each component vs the composite it feeds (v2):")
    for d in TDPI_V2_METRICS:
        xs, ys = paired(rows, d.attr, "v2")
        s = spearman(xs, ys)
        print(f"    {d.id} {d.label[:44]:<44} spearman={s:+.3f}" if s is not None
              else f"    {d.id} n/a")

    print("\n  v1 vs v2 score agreement:")
    xs, ys = paired(rows, "v1", "v2")
    print(f"    pearson={pearson(xs, ys):+.3f}  spearman={spearman(xs, ys):+.3f}  n={len(xs)}")


def s_seasonality(rows: list[dict]) -> None:
    rule("6a. SEASONALITY — does peak concentration reward seasonal airports?")
    have = [r for r in rows if r["peak_concentration"] is not None]
    have.sort(key=lambda r: -r["peak_concentration"])
    print(f"\n  Top 12 by peak/mean ratio:")
    print(f"  {'apt':<5} {'peak/mean':>10} {'pax':>11} {'hub':>4} {'v2':>6} {'v1':>6}  name")
    for r in have[:12]:
        print(f"  {r['iata']:<5} {r['peak_concentration']:>10.2f} "
              f"{fnum(r['passengers']):>11} {str(r['hub']):>4} "
              f"{f1(r['v2']):>6} {f1(r['v1']):>6}  {r['name'][:34]}")

    strongly = [r for r in have if r["peak_concentration"] >= SEASONALITY_WARN_RATIO]
    print(f"\n  airports with peak/mean >= {SEASONALITY_WARN_RATIO}: {len(strongly)}")
    if strongly:
        v2s = [r["v2"] for r in strongly if r["v2"] is not None]
        others = [r["v2"] for r in have
                  if r["peak_concentration"] < SEASONALITY_WARN_RATIO and r["v2"] is not None]
        if v2s and others:
            print(f"    mean v2 (seasonal)     : {sum(v2s)/len(v2s):.1f}")
            print(f"    mean v2 (non-seasonal) : {sum(others)/len(others):.1f}")
            print(f"    difference             : {sum(v2s)/len(v2s) - sum(others)/len(others):+.1f}")

    xs, ys = paired(rows, "peak_concentration", "passengers")
    print(f"\n  peak/mean vs airport size (passengers): spearman="
          f"{spearman(xs, ys):+.3f}  → negative means small airports peak harder")


def s_sensitivity(engine: AnalyticsEngine, rows: list[dict]) -> None:
    rule("6b. WEIGHT SENSITIVITY")
    cohort = engine.cohort()
    base = {r["iata"]: r["v2"] for r in rows if r["v2"] is not None}
    base_rank = {r["iata"]: i for i, r in enumerate(ranked(list(rows), "v2"), 1)}

    scenarios = {
        "proposed (30/25/20/15/10)": None,
        "drop V3 peak (35/30/0/20/15)": {"V1": .35, "V2": .30, "V3": 0, "V4": .20, "V5": .15},
        "drop V4 absolute (35/30/25/0/10)": {"V1": .35, "V2": .30, "V3": .25, "V4": 0, "V5": .10},
        "drop V3+V4 (40/35/0/0/25)": {"V1": .40, "V2": .35, "V3": 0, "V4": 0, "V5": .25},
        "equal (20 each)": {k: .20 for k in ("V1", "V2", "V3", "V4", "V5")},
        "growth-heavy (50/30/0/10/10)": {"V1": .50, "V2": .30, "V3": 0, "V4": .10, "V5": .10},
    }

    print(f"\n  {'scenario':<34} {'spearman':>9} {'med move':>9} {'max move':>9} {'top20 ovl':>10}")
    for label, w in scenarios.items():
        if w is None:
            scores = base
        else:
            scores = {}
            for m in cohort.members:
                res = compute_tdpi_v2(m, cohort, weights=w)
                if res.score is not None:
                    scores[m.iata] = res.score
        order = sorted(scores, key=lambda k: (-scores[k], k))
        rk = {k: i for i, k in enumerate(order, 1)}
        common = [k for k in base_rank if k in rk]
        rho = spearman([base_rank[k] for k in common], [rk[k] for k in common])
        moves = sorted(abs(base_rank[k] - rk[k]) for k in common)
        top_base = {k for k, _ in sorted(base_rank.items(), key=lambda kv: kv[1])[:20]}
        ovl = len(top_base & set(order[:20]))
        print(f"  {label:<34} {rho:>9.3f} {moves[len(moves)//2]:>9.0f} "
              f"{moves[-1]:>9.0f} {ovl:>8}/20")


def s_small_airports(rows: list[dict]) -> None:
    rule("7b. SMALL-AIRPORT AND MISSING-DATA BEHAVIOUR")
    buckets = [("< 100k", 0, 1e5), ("100k–1M", 1e5, 1e6),
               ("1M–10M", 1e6, 1e7), ("> 10M", 1e7, float("inf"))]
    print(f"\n  {'size':<10} {'n':>4} {'v1 scored':>10} {'v2 scored':>10} "
          f"{'mean v1':>8} {'mean v2':>8} {'mean Δ':>8}")
    for label, lo, hi in buckets:
        grp = [r for r in rows if lo <= (r["passengers"] or 0) < hi]
        if not grp:
            continue
        v1 = [r["v1"] for r in grp if r["v1"] is not None]
        v2 = [r["v2"] for r in grp if r["v2"] is not None]
        both = [(r["v1"], r["v2"]) for r in grp
                if r["v1"] is not None and r["v2"] is not None]
        d = sum(b - a for a, b in both) / len(both) if both else 0
        print(f"  {label:<10} {len(grp):>4} {len(v1):>10} {len(v2):>10} "
              f"{(sum(v1)/len(v1) if v1 else 0):>8.1f} "
              f"{(sum(v2)/len(v2) if v2 else 0):>8.1f} {d:>+8.1f}")

    print("\n  Airports v1 scores but v2 suppresses (top 10 by passengers):")
    lost = [r for r in rows if r["v1"] is not None and r["v2"] is None]
    lost.sort(key=lambda r: -(r["passengers"] or 0))
    for r in lost[:10]:
        print(f"    {r['iata']:<5} {(r['passengers'] or 0):>10,.0f} pax  "
              f"yoy_pairs={r['yoy_months']:>2} months={r['window_months']:>2}  "
              f"{r['v2_reason']}")
    if not lost:
        print("    none")


def s_surprises(rows: list[dict]) -> None:
    rule("8. LARGEST RANK MOVEMENTS, WITH DRIVERS")
    a, b = ranked(list(rows), "v1"), ranked(list(rows), "v2")
    r1 = {r["iata"]: r["rank_v1"] for r in a}
    r2 = {r["iata"]: r["rank_v2"] for r in b}
    moved = [(r1[k] - r2[k], k) for k in r1 if k in r2]
    moved.sort(reverse=True)

    def explain(code: str) -> None:
        r = next(x for x in rows if x["iata"] == code)
        bits = []
        for d in TDPI_V2_METRICS:
            c = r["v2_comp"].get(d.id)
            if c and c.normalized is not None:
                bits.append(f"{d.id}={c.normalized:.0f}")
        print(f"    {code:<5} v1 #{r1[code]:<4} -> v2 #{r2[code]:<4} "
              f"({r1[code]-r2[code]:+d})  pax={(r['passengers'] or 0):>10,.0f}  "
              f"{' '.join(bits)}")

    print("\n  Biggest RISES under v2:")
    for _, k in moved[:10]:
        explain(k)
    print("\n  Biggest FALLS under v2:")
    for _, k in moved[-10:]:
        explain(k)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", help="run one section only")
    args = ap.parse_args()

    engine = AnalyticsEngine()
    try:
        rows = build(engine)
        print(f"TDPI v1 vs v2 evaluation — window {engine.window}")
        print(f"Cohort: {len(rows)} airports with traffic "
              f"(of {len(engine.metrics)} in the universe)")

        sections = {
            "definitions": lambda: s_definitions(),
            "coverage": lambda: s_coverage(rows),
            "top20": lambda: s_top20(rows),
            "newengland": lambda: s_new_england(rows),
            "correlation": lambda: s_correlation(rows),
            "seasonality": lambda: s_seasonality(rows),
            "sensitivity": lambda: s_sensitivity(engine, rows),
            "small": lambda: s_small_airports(rows),
            "surprises": lambda: s_surprises(rows),
        }
        if args.section:
            sections[args.section]()
        else:
            for fn in sections.values():
                fn()
    finally:
        engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
