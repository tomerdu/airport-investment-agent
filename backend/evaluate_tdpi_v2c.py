"""Phase 8.1b — validate TDPI v2c as a candidate, rather than assume it.

Offline only. Reads the committed warehouse; writes nothing.

    python evaluate_tdpi_v2c.py
    python evaluate_tdpi_v2c.py --section c3
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from app.analytics import AnalyticsEngine
from app.analytics.definitions import (
    DIVERGENCE_HI,
    DIVERGENCE_LO,
    SEASONALITY_WARN_RATIO,
    TDPI_V2C_METRICS,
)
from app.analytics.scoring import (
    compute_aci,
    compute_tdpi,
    compute_tdpi_v2,
    compute_tdpi_v2c,
)
from evaluate_tdpi_v2 import f1, f2, fnum, fpct, paired, pearson, rule, spearman

DIAGNOSTIC = ["BOS", "ATL", "GUF", "HVN", "SFO", "ORD", "JFK", "ACK", "AKN", "HYA"]


def build(engine: AnalyticsEngine) -> list[dict]:
    cohort = engine.cohort()
    rows = []
    for m in cohort.members:
        v1 = compute_tdpi(m, cohort)
        v2 = compute_tdpi_v2(m, cohort)
        v2c = compute_tdpi_v2c(m, cohort)
        rows.append({
            "iata": m.iata, "name": m.name, "hub": m.hub_class, "region": m.region,
            "passengers": m.passengers, "departures": m.departures,
            "yoy_months": m.yoy_months_evaluable,
            "prior_months": m.traffic_months_prior,
            "window_months": m.traffic_months,
            "v1": v1.score, "v2": v2.score, "v2c": v2c.score,
            "v2c_reason": v2c.suppressed_reason,
            "aci": compute_aci(m, cohort).score,
            "pax_growth": m.pax_growth,
            "sustained_growth": m.sustained_growth,
            "pax_per_departure": m.pax_per_departure,
            "pax_per_departure_growth": m.pax_per_departure_growth,
            "peak_concentration": m.peak_concentration,
            "seats_per_departure": m.seats_per_departure,
            "load_factor": m.load_factor,
            "v2c_comp": {c.id: c for c in v2c.components},
        })
    return rows


def rank_map(rows: list[dict], key: str) -> dict[str, int]:
    s = [r for r in rows if r[key] is not None]
    s.sort(key=lambda r: (-r[key], r["iata"]))
    return {r["iata"]: i for i, r in enumerate(s, 1)}


# ---------------------------------------------------------------------------


def s_correlation(rows: list[dict]) -> None:
    rule("2. v2c COMPONENT CORRELATIONS")
    attrs = [(d.id, d.attr, d.label) for d in TDPI_V2C_METRICS]
    print("\n  Pearson (upper) / Spearman (lower), raw values\n")
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

    print("\n  C1~C2 specifically (the growth-redundancy question):")
    xs, ys = paired(rows, "pax_growth", "sustained_growth")
    print(f"    pearson={pearson(xs, ys):+.3f}  spearman={spearman(xs, ys):+.3f}  n={len(xs)}")

    print("\n  component vs composite (spearman):")
    for d in TDPI_V2C_METRICS:
        xs, ys = paired(rows, d.attr, "v2c")
        print(f"    {d.id} {d.label[:44]:<44} {spearman(xs, ys):+.3f}")


def s_c3(rows: list[dict]) -> None:
    rule("7. DOES C3 (passengers per departure) ADD ANYTHING? — gauge vs pressure")

    print("\n  C3 is arithmetically gauge x load factor. Check against both:")
    for label, attr in (("seats per departure (gauge)", "seats_per_departure"),
                        ("load factor", "load_factor")):
        xs, ys = paired(rows, "pax_per_departure", attr)
        print(f"    C3 ~ {label:<30} pearson={pearson(xs, ys):+.3f} "
              f"spearman={spearman(xs, ys):+.3f}")

    print("\n  If C3 is mostly gauge, it inherits v1's T3 weakness.")
    xs, ys = paired(rows, "pax_per_departure", "passengers")
    print(f"    C3 ~ total passengers (terminal throughput): "
          f"spearman={spearman(xs, ys):+.3f}")

    print("\n  The conceptual test: two airports, same terminal load, different C3.")
    print("  100 flights x 200 pax = 20,000 pax   -> C3 = 200")
    print("  200 flights x 100 pax = 20,000 pax   -> C3 = 100")
    print("  Identical terminal throughput; C3 differs 2x. C3 is per-MOVEMENT.")

    print("\n  Highest C3 in the cohort:")
    have = sorted([r for r in rows if r["pax_per_departure"]],
                  key=lambda r: -r["pax_per_departure"])[:10]
    print(f"  {'apt':<5} {'pax/dep':>8} {'gauge':>7} {'LF':>6} {'pax':>12} {'hub':>4} {'v2c rk':>7}")
    rk = rank_map(rows, "v2c")
    for r in have:
        print(f"  {r['iata']:<5} {r['pax_per_departure']:>8.1f} "
              f"{f1(r['seats_per_departure']):>7} {fpct(r['load_factor']):>6} "
              f"{fnum(r['passengers']):>12} {str(r['hub']):>4} "
              f"{rk.get(r['iata'], '—'):>7}")


def s_distribution(rows: list[dict]) -> None:
    rule("4. SCORE DISTRIBUTION — size, hub class, seasonality")

    print(f"\n  by passenger volume:")
    print(f"  {'band':<12} {'n':>4} {'v1':>7} {'v2':>7} {'v2c':>7}")
    for lab, lo, hi in [("< 100k", 0, 1e5), ("100k–1M", 1e5, 1e6),
                        ("1M–10M", 1e6, 1e7), ("> 10M", 1e7, float("inf"))]:
        g = [r for r in rows if lo <= (r["passengers"] or 0) < hi]
        if not g:
            continue
        def mean(k):
            v = [r[k] for r in g if r[k] is not None]
            return sum(v) / len(v) if v else 0.0
        print(f"  {lab:<12} {len(g):>4} {mean('v1'):>7.1f} {mean('v2'):>7.1f} "
              f"{mean('v2c'):>7.1f}")

    print(f"\n  by FAA hub class:")
    print(f"  {'hub':<6} {'n':>4} {'v1':>7} {'v2':>7} {'v2c':>7}")
    for hub in ["L", "M", "S", "N"]:
        g = [r for r in rows if r["hub"] == hub]
        if not g:
            continue
        def mean(k):
            v = [r[k] for r in g if r[k] is not None]
            return sum(v) / len(v) if v else 0.0
        print(f"  {hub:<6} {len(g):>4} {mean('v1'):>7.1f} {mean('v2'):>7.1f} "
              f"{mean('v2c'):>7.1f}")

    print(f"\n  seasonality (peak/mean >= {SEASONALITY_WARN_RATIO}):")
    seas = [r for r in rows if (r["peak_concentration"] or 0) >= SEASONALITY_WARN_RATIO]
    other = [r for r in rows if r["peak_concentration"] is not None
             and r["peak_concentration"] < SEASONALITY_WARN_RATIO]
    print(f"  {'group':<16} {'n':>4} {'v1':>7} {'v2':>7} {'v2c':>7}")
    for lab, g in (("seasonal", seas), ("non-seasonal", other)):
        def mean(k):
            v = [r[k] for r in g if r[k] is not None]
            return sum(v) / len(v) if v else 0.0
        print(f"  {lab:<16} {len(g):>4} {mean('v1'):>7.1f} {mean('v2'):>7.1f} "
              f"{mean('v2c'):>7.1f}")
    def gap(k):
        a = [r[k] for r in seas if r[k] is not None]
        b = [r[k] for r in other if r[k] is not None]
        return (sum(a)/len(a) - sum(b)/len(b)) if a and b else 0.0
    print(f"\n  seasonal bonus: v1 {gap('v1'):+.1f}   v2 {gap('v2'):+.1f}   "
          f"v2c {gap('v2c'):+.1f}")

    print("\n  score spread:")
    for k in ("v1", "v2", "v2c"):
        v = sorted(r[k] for r in rows if r[k] is not None)
        n = len(v)
        print(f"    {k:<4} n={n:>3}  min={v[0]:5.1f}  p25={v[n//4]:5.1f}  "
              f"med={v[n//2]:5.1f}  p75={v[3*n//4]:5.1f}  max={v[-1]:5.1f}")


def s_compare(rows: list[dict]) -> None:
    rule("5. v1 vs v2 vs v2c")
    R = {k: rank_map(rows, k) for k in ("v1", "v2", "v2c")}

    print("\n  rank correlations (spearman on ranks):")
    for a, b in (("v1", "v2"), ("v1", "v2c"), ("v2", "v2c")):
        common = [k for k in R[a] if k in R[b]]
        rho = spearman([R[a][k] for k in common], [R[b][k] for k in common])
        moves = sorted(abs(R[a][k] - R[b][k]) for k in common)
        top_a = {k for k, v in R[a].items() if v <= 20}
        top_b = {k for k, v in R[b].items() if v <= 20}
        print(f"    {a:<4} ~ {b:<4}  rho={rho:+.3f}  med move={moves[len(moves)//2]:>3}  "
              f"max={moves[-1]:>3}  top20 overlap={len(top_a & top_b)}/20")

    print("\n  coverage:")
    for k in ("v1", "v2", "v2c"):
        print(f"    {k:<4} scored {sum(1 for r in rows if r[k] is not None):>3}/{len(rows)}")
    print("  v2c suppression reasons:",
          dict(Counter(r["v2c_reason"] for r in rows if r["v2c"] is None)))

    rule("5b. DIAGNOSTIC AIRPORTS")
    print(f"  {'apt':<5} {'pax':>12} {'v1':>6} {'v2':>6} {'v2c':>6} "
          f"{'v1rk':>5} {'v2rk':>5} {'v2crk':>6} {'growth':>8} {'sust':>6} {'pax/dep':>8}")
    for c in DIAGNOSTIC:
        r = next((x for x in rows if x["iata"] == c), None)
        if not r:
            continue
        print(f"  {c:<5} {fnum(r['passengers']):>12} {f1(r['v1']):>6} {f1(r['v2']):>6} "
              f"{f1(r['v2c']):>6} {str(R['v1'].get(c, '—')):>5} "
              f"{str(R['v2'].get(c, '—')):>5} {str(R['v2c'].get(c, '—')):>6} "
              f"{fpct(r['pax_growth']):>8} {fpct(r['sustained_growth']):>6} "
              f"{f1(r['pax_per_departure']):>8}")

    rule("5c. NEW ENGLAND")
    ne = [r for r in rows if r["region"] == "new_england"]
    ne.sort(key=lambda r: -(r["v2c"] if r["v2c"] is not None else -1))
    print(f"  {'apt':<5} {'pax':>11} {'v1':>6} {'v2':>6} {'v2c':>6} "
          f"{'v1rk':>5} {'v2crk':>6} {'peak':>6}")
    for r in ne:
        print(f"  {r['iata']:<5} {fnum(r['passengers']):>11} {f1(r['v1']):>6} "
              f"{f1(r['v2']):>6} {f1(r['v2c']):>6} {str(R['v1'].get(r['iata'], '—')):>5} "
              f"{str(R['v2c'].get(r['iata'], '—')):>6} {f2(r['peak_concentration']):>6}")


def s_sensitivity(engine: AnalyticsEngine, rows: list[dict]) -> None:
    rule("3. v2c WEIGHT SENSITIVITY")
    cohort = engine.cohort()
    base_rank = rank_map(rows, "v2c")
    ne_codes = {r["iata"] for r in rows if r["region"] == "new_england"}

    scenarios = {
        "proposed 30/25/30/15": None,
        "C3 heavier 25/20/40/15": {"C1": .25, "C2": .20, "C3": .40, "C4": .15},
        "C3 lighter 35/30/20/15": {"C1": .35, "C2": .30, "C3": .20, "C4": .15},
        "C3 removed 40/35/0/25": {"C1": .40, "C2": .35, "C3": 0.0, "C4": .25},
        "equal 25 each": {k: .25 for k in ("C1", "C2", "C3", "C4")},
        "growth-led 40/30/20/10": {"C1": .40, "C2": .30, "C3": .20, "C4": .10},
    }

    print(f"\n  {'scenario':<26} {'rho':>7} {'med':>5} {'max':>5} {'top20':>7} "
          f"{'NE top3 change':>16} {'>10M mean':>10}")
    for label, w in scenarios.items():
        scores = {}
        for m in cohort.members:
            res = compute_tdpi_v2c(m, cohort, weights=w) if w else None
            s = res.score if w else next(
                r["v2c"] for r in rows if r["iata"] == m.iata)
            if s is not None:
                scores[m.iata] = s
        order = sorted(scores, key=lambda k: (-scores[k], k))
        rk = {k: i for i, k in enumerate(order, 1)}
        common = [k for k in base_rank if k in rk]
        rho = spearman([base_rank[k] for k in common], [rk[k] for k in common])
        moves = sorted(abs(base_rank[k] - rk[k]) for k in common)
        top_base = {k for k, v in base_rank.items() if v <= 20}
        ne_order = [k for k in order if k in ne_codes][:3]
        base_ne = [k for k in sorted(base_rank, key=lambda x: base_rank[x])
                   if k in ne_codes][:3]
        big = [scores[r["iata"]] for r in rows
               if (r["passengers"] or 0) >= 1e7 and r["iata"] in scores]
        print(f"  {label:<26} {rho:>7.3f} {moves[len(moves)//2]:>5} {moves[-1]:>5} "
              f"{len(top_base & set(order[:20])):>5}/20 "
              f"{('same' if ne_order == base_ne else ','.join(ne_order)):>16} "
              f"{(sum(big)/len(big) if big else 0):>10.1f}")


def s_v1_eligibility(rows: list[dict]) -> None:
    rule("6. v1 ELIGIBILITY AUDIT — misleading scores on thin prior-year data")
    print("""
  v1's T2 (passenger growth, weight 0.30) divides window passengers by prior
  passengers. It requires only that the prior total is non-zero — an airport
  with ONE reported prior month is compared against a full twelve, producing a
  growth figure of several hundred percent that is an artefact of coverage, not
  demand. v1 has no month-count guard anywhere.""")

    suspect = [r for r in rows
               if r["prior_months"] < 12 and r["v1"] is not None]
    suspect.sort(key=lambda r: r["prior_months"])
    print(f"\n  airports scored by v1 with < 12 prior months: {len(suspect)}")
    print(f"  {'apt':<5} {'prior mo':>8} {'win mo':>7} {'pax growth':>11} "
          f"{'v1':>6} {'v1 rk':>6} {'v2c':>6}")
    rk1 = rank_map(rows, "v1")
    for r in suspect[:12]:
        print(f"  {r['iata']:<5} {r['prior_months']:>8} {r['window_months']:>7} "
              f"{fpct(r['pax_growth']):>11} {f1(r['v1']):>6} "
              f"{str(rk1.get(r['iata'], '—')):>6} {f1(r['v2c']):>6}")

    top50 = [r for r in suspect if rk1.get(r["iata"], 999) <= 50]
    print(f"\n  of those, ranked in v1's top 50: {len(top50)}")
    for r in top50:
        print(f"    {r['iata']}  v1 rank {rk1[r['iata']]}, "
              f"{r['prior_months']} prior months, growth {fpct(r['pax_growth'])}")

    print("""
  PROPOSED DATA-QUALITY FIX (independent of any scoring formula):
    Suppress any T-100-derived YoY growth component when the prior window has
    fewer than MIN_PRIOR_MONTHS_FOR_GROWTH months. This is a coverage rule, not
    a methodology change: it applies to v1's T2 and to v2/v2c's T-100 growth
    components, and it DROPS the component rather than imputing it — the
    existing renormalisation and 0.60 coverage floor then apply unchanged.
    v1's T5 is FAA annual enplanements, a different source with its own
    coverage, so this guard does not govern it.""")


def s_guard_simulation(engine: AnalyticsEngine, rows: list[dict]) -> None:
    """Measure the guard rather than assert it. Production is not modified:
    the simulation blanks passengers_prior on a throwaway metrics load, which
    makes pax_growth return None so T2 is dropped and renormalised."""
    rule("6b. GUARD SIMULATION — measured effect on v1")

    cohort = engine.cohort()
    before = {r["iata"]: r["v1"] for r in rows}
    rk_before = rank_map(rows, "v1")

    affected = [m for m in cohort.members if not m.growth_windows_comparable]
    saved = {m.iata: m.passengers_prior for m in affected}
    for m in affected:
        m.passengers_prior = None          # -> pax_growth None -> T2 dropped
    try:
        after_rows = [{"iata": m.iata, "v1": compute_tdpi(m, cohort).score}
                      for m in cohort.members]
    finally:
        for m in affected:
            m.passengers_prior = saved[m.iata]

    after = {r["iata"]: r["v1"] for r in after_rows}
    rk_after = rank_map(after_rows, "v1")

    print(f"\n  airports failing the RECOMMENDED comparability guard: {len(affected)}")
    print(f"  {'apt':<5} {'prior mo':>8} {'v1 before':>10} {'v1 after':>9} "
          f"{'rk before':>10} {'rk after':>9} {'move':>6}")
    for m in sorted(affected, key=lambda x: rk_before.get(x.iata, 999)):
        a = rk_after.get(m.iata)
        b = rk_before.get(m.iata)
        move = "—" if (a is None or b is None) else f"{a - b:+d}"
        print(f"  {m.iata:<5} {m.traffic_months_prior:>8} "
              f"{f1(before.get(m.iata)):>10} {f1(after.get(m.iata)):>9} "
              f"{str(b or '—'):>10} {str(a or '—'):>9} {move:>6}")

    unaffected_moves = [
        abs(rk_after[i] - rk_before[i])
        for i in rk_before
        if i in rk_after and i not in saved
    ]
    changed = sum(1 for d in unaffected_moves if d != 0)
    print(f"\n  collateral effect on the other {len(unaffected_moves)} airports:")
    print(f"    ranks changed: {changed}   max move: {max(unaffected_moves)}")
    pb, pa = paired(
        [{"a": before[i], "b": after[i]} for i in before if i in after],
        "a", "b",
    )
    print(f"    v1 before ~ v1 after: spearman={spearman(pb, pa):+.4f} n={len(pb)}")
    # --- alternative formulation -------------------------------------------
    # An absolute month floor discards WYS (Yellowstone), whose prior and
    # window months are BOTH 6: a seasonal airport compared like-for-like. The
    # defect is window COMPARABILITY, not prior-window length.
    print("\n  WHY COMPARABILITY AND NOT AN ABSOLUTE FLOOR "
          "(prior >= window - 1, vs prior >= 10)")
    print(f"  {'apt':<5} {'prior':>6} {'window':>7} {'growth':>11} "
          f"{'absolute >=10':>14} {'comparability':>14}")
    for m in sorted(cohort.members, key=lambda x: x.traffic_months_prior):
        if m.traffic_months_prior >= 12:
            continue
        abs_ok = m.traffic_months_prior >= 10
        cmp_ok = m.traffic_months_prior >= m.traffic_months - 1
        print(f"  {m.iata:<5} {m.traffic_months_prior:>6} {m.traffic_months:>7} "
              f"{fpct(m.pax_growth):>11} "
              f"{('keep' if abs_ok else 'DROP'):>14} "
              f"{('keep' if cmp_ok else 'DROP'):>14}")
    # How sensitive is the tolerance? Bimodal here, so: not at all.
    shortfall = Counter(max(0, m.traffic_months - m.traffic_months_prior)
                        for m in cohort.members)
    print(f"\n  window-shortfall distribution (window - prior): "
          f"{dict(sorted(shortfall.items()))}")
    print(f"  {'tolerance':>10} {'affected':>9}  airports")
    for tol in range(0, 11):
        hit = [m.iata for m in cohort.members
               if m.traffic_months_prior < m.traffic_months - tol]
        print(f"  {tol:>10} {len(hit):>9}  {hit}")
    print("  The distribution is bimodal with nothing between 0 and 10, so every\n"
          "  tolerance from 0 to 9 isolates GUF. The exact value is not what makes\n"
          "  this guard work; comparing like windows is.")

    n = len(affected)
    print(f"\n  The comparability rule isolates GUF alone — the one airport whose\n"
          f"  prior and window periods are genuinely not comparable (2 vs 12).\n"
          f"  The absolute floor would additionally drop WYS (Yellowstone), a\n"
          f"  seasonal airport compared 6-against-6, whose +22% is a valid figure.")

    print(f"\n  The guard is narrow by construction: it changes "
          f"{'only the 1 airport' if n == 1 else f'only the {n} airports'} whose\n"
          f"  prior and window periods are not comparable. Every other airport "
          f"keeps its\n  score exactly; the 'ranks changed' count above is "
          f"airports shifting up by one\n  as the moved airport vacates its "
          f"place (max move 1).")


def s_thresholds(rows: list[dict]) -> None:
    rule("9. CLASSIFICATION THRESHOLDS — 60/40 cannot be reused unexamined")
    print(f"\n  share of cohort above each threshold:")
    print(f"  {'index':<6} {'>=60 (HI)':>10} {'<40 (LO)':>10} {'p60 value':>11} {'p40 value':>11}")
    for k in ("v1", "v2", "v2c"):
        v = sorted(r[k] for r in rows if r[k] is not None)
        n = len(v)
        hi = sum(1 for x in v if x >= DIVERGENCE_HI)
        lo = sum(1 for x in v if x < DIVERGENCE_LO)
        print(f"  {k:<6} {hi:>4} ({100*hi/n:>4.1f}%) {lo:>4} ({100*lo/n:>4.1f}%) "
              f"{v[int(n*0.60)]:>11.1f} {v[int(n*0.40)]:>11.1f}")

    print("\n  TERMINAL_LED count under the CURRENT 60/40 rule (TDPI>=60 and ACI<40):")
    for k in ("v1", "v2", "v2c"):
        n = sum(1 for r in rows
                if r[k] is not None and r[k] >= DIVERGENCE_HI
                and r["aci"] is not None and r["aci"] < DIVERGENCE_LO)
        unk = sum(1 for r in rows
                  if r[k] is not None and r[k] >= DIVERGENCE_HI and r["aci"] is None)
        print(f"    {k:<4} TERMINAL_LED={n:>3}   high-TDPI but ACI unmeasured={unk:>3}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section")
    args = ap.parse_args()
    engine = AnalyticsEngine()
    try:
        rows = build(engine)
        print(f"TDPI v2c validation — window {engine.window}, cohort {len(rows)}")
        sections = {
            "correlation": lambda: s_correlation(rows),
            "sensitivity": lambda: s_sensitivity(engine, rows),
            "distribution": lambda: s_distribution(rows),
            "compare": lambda: s_compare(rows),
            "eligibility": lambda: s_v1_eligibility(rows),
            "guard": lambda: s_guard_simulation(engine, rows),
            "c3": lambda: s_c3(rows),
            "thresholds": lambda: s_thresholds(rows),
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
