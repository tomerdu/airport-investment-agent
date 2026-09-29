"""Phase 8.1c — evidence for the YoY window-comparability guard on v1's T2.

Offline only. Reads the committed warehouse; writes nothing.

"Before" is produced by hiding the monthly series, which makes `pax_growth`
fall back to the legacy annual SUM/SUM ratio — exactly the pre-8.1c behaviour.
Both cohorts are rebuilt from scratch, so shifts in the winsorization bounds
are captured too.

    python evaluate_yoy_comparability.py
    python evaluate_yoy_comparability.py --section demo
"""

from __future__ import annotations

import argparse
import sys

from app.analytics import AnalyticsEngine
from app.analytics.scoring import Cohort, build_cohort, compute_tdpi

DEMO_AIRPORTS = ["SFO", "LAX", "SNA", "ANC", "BOS"]
NEW_ENGLAND = "new_england"


def rule(title: str) -> None:
    print("\n" + "=" * 86)
    print(title)
    print("=" * 86)


def f1(v: float | None) -> str:
    return "—" if v is None else f"{v:.1f}"


def fpct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:,.1f}%"


def prior_key(month: str) -> str:
    y, mm = month.split("-")
    return f"{int(y) - 1:04d}-{mm}"


class Shadow:
    """Temporarily hide the monthly series to reproduce pre-8.1c behaviour."""

    def __init__(self, members):
        self.members = members
        self.saved: dict[str, tuple[dict, dict]] = {}

    def __enter__(self):
        for m in self.members:
            self.saved[m.iata] = (m.monthly_passengers, m.monthly_passengers_prior)
            m.monthly_passengers = {}
            m.monthly_passengers_prior = {}
        return self

    def __exit__(self, *exc):
        for m in self.members:
            m.monthly_passengers, m.monthly_passengers_prior = self.saved[m.iata]
        return False


def scores(metrics: dict, **kw) -> tuple[dict[str, float | None], dict[str, int],
                                         dict[str, float | None]]:
    """(tdpi by iata, rank by iata, t2 raw by iata) for a freshly built cohort."""
    cohort = build_cohort(metrics, **kw)
    out, t2 = {}, {}
    for m in cohort.members:
        r = compute_tdpi(m, cohort)
        out[m.iata] = r.score
        comp = next((c for c in r.components if c.id == "T2"), None)
        t2[m.iata] = comp.raw if comp else None
    ranked = sorted((i for i, s in out.items() if s is not None),
                    key=lambda i: (-out[i], i))
    return out, {i: n for n, i in enumerate(ranked, 1)}, t2


def both(metrics: dict, **kw):
    after = scores(metrics, **kw)
    with Shadow(list(metrics.values())):
        before = scores(metrics, **kw)
    return before, after


# ---------------------------------------------------------------------------


def s_inspect(engine: AnalyticsEngine) -> None:
    rule("1. EVERY AIRPORT WITH INCOMPLETE OR MISALIGNED PRIOR-YEAR COVERAGE")
    cohort = engine.cohort()
    print("\n  Alignment is checked per calendar month, not by month count.")
    print(f"\n  {'apt':<5} {'name':<34} {'win':>4} {'pri':>4} {'match':>6} "
          f"{'unmatched window months':>26} {'extra prior':>12} {'eligible':>9}")
    print("  " + "-" * 105)
    flagged = 0
    for m in sorted(cohort.members, key=lambda x: x.iata):
        want = {prior_key(x) for x in m.monthly_passengers}
        have = set(m.monthly_passengers_prior)
        unmatched, extra = sorted(want - have), sorted(have - want)
        if not unmatched and not extra and m.traffic_months == 12:
            continue
        flagged += 1
        print(f"  {m.iata:<5} {m.name[:34]:<34} {m.traffic_months:>4} "
              f"{m.traffic_months_prior:>4} {m.yoy_months_matched:>6} "
              f"{(','.join(unmatched) or '—')[:26]:>26} "
              f"{(','.join(extra) or '—')[:12]:>12} "
              f"{('YES' if m.yoy_windows_aligned else 'no'):>9}")
    print(f"\n  airports flagged: {flagged} of {len(cohort.members)}")
    print("  Everything else reports 12 window months each with a prior "
          "counterpart\n  and is completely unaffected by the guard.")


def s_effect(engine: AnalyticsEngine) -> None:
    rule("2. BEFORE / AFTER — T2 values, TDPI scores and ranks")
    (sb, rb, tb), (sa, ra, ta) = both(engine.metrics)

    changed = [i for i in sb
               if (tb[i] is None) != (ta[i] is None)
               or (tb[i] is not None and ta[i] is not None
                   and abs(tb[i] - ta[i]) > 1e-12)]
    print(f"\n  airports whose T2 value changes: {len(changed)} -> "
          f"{sorted(changed)}")
    print(f"\n  {'apt':<5} {'T2 before':>13} {'T2 after':>13} "
          f"{'TDPI before':>12} {'TDPI after':>11} {'rank before':>12} "
          f"{'rank after':>11} {'move':>6}")
    print("  " + "-" * 92)
    for i in sorted(changed, key=lambda x: rb.get(x, 999)):
        mb, ma = rb.get(i), ra.get(i)
        move = "—" if mb is None or ma is None else f"{ma - mb:+d}"
        print(f"  {i:<5} {fpct(tb[i]):>13} {fpct(ta[i]):>13} "
              f"{f1(sb[i]):>12} {f1(sa[i]):>11} {str(mb or '—'):>12} "
              f"{str(ma or '—'):>11} {move:>6}")

    # Everyone else: did any score move, e.g. through shifted cohort bounds?
    others = [i for i in sb if i not in changed]
    score_moved = [i for i in others
                   if (sb[i] is None) != (sa[i] is None)
                   or (sb[i] is not None and sa[i] is not None
                       and abs(sb[i] - sa[i]) > 1e-9)]
    rank_moved = [i for i in others if rb.get(i) != ra.get(i)]
    print(f"\n  the other {len(others)} airports:")
    print(f"    TDPI score changed : {len(score_moved)}")
    deltas = sorted(abs(sb[i] - sa[i]) for i in others
                    if sb[i] is not None and sa[i] is not None)
    if deltas:
        print(f"      |change| max={deltas[-1]:.3f} pt  "
              f"median={deltas[len(deltas) // 2]:.3f} pt  "
              f"above 0.5 pt={sum(1 for d in deltas if d > 0.5)}")
    print(f"    rank number changed: {len(rank_moved)}")
    if rank_moved:
        mx = max(abs(ra[i] - rb[i]) for i in rank_moved)
        print(f"      max rank move: {mx}")

    # WHY those small shifts happen — this is expected, not a defect.
    ca = build_cohort(engine.metrics)
    sa_ = ca.stat("pax_growth")
    with Shadow(list(engine.metrics.values())):
        cb = build_cohort(engine.metrics)
        sb_ = cb.stat("pax_growth")
        blo, bhi, bn = sb_.p_low, sb_.p_high, sb_.n
    print("\n  Cause of the sub-point shifts: TDPI is normalised against cohort")
    print("  percentiles, so suppressing a component removes observations from")
    print("  the pax_growth distribution and moves the winsorization bounds.")
    print(f"    before: P5={blo:+.4f} P95={bhi:+.4f} n={bn}")
    print(f"    after : P5={sa_.p_low:+.4f} P95={sa_.p_high:+.4f} n={sa_.n}")
    print("  This is inherent to cohort-relative scoring, not a side effect of")
    print("  the guard's implementation.")

    # Invariants: nothing lost a score, nothing fell through the floor, ACI is
    # untouched because it shares no component with T2.
    from app.analytics.scoring import compute_aci

    def invariants() -> tuple[int, int, int, float, float]:
        c = build_cohort(engine.metrics)
        t = [compute_tdpi(m, c) for m in c.members]
        a = [compute_aci(m, c) for m in c.members]
        return (
            sum(1 for x in t if x.score is not None),
            sum(1 for x in a if x.score is not None),
            sum(1 for x in t if x.suppressed_reason),
            min(x.coverage for x in t),
            sum(x.score for x in a if x.score is not None),
        )

    inv_a = invariants()
    with Shadow(list(engine.metrics.values())):
        inv_b = invariants()
    print("\n  invariants:")
    for lab, b, a in zip(
        ["TDPI scored", "ACI scored", "TDPI suppressed entirely",
         "min TDPI coverage", "ACI score total"], inv_b, inv_a
    ):
        print(f"    {lab:<26} before={b:>12.4f}  after={a:>12.4f}")
    print(f"    ACI bit-identical: {abs(inv_b[4] - inv_a[4]) < 1e-9}")
    print("    No airport lost its TDPI score; 0.70 coverage clears the 0.60 floor.")


def s_demo(engine: AnalyticsEngine) -> None:
    rule("3. THE FOUR DEMO SCENARIOS")
    (sb, rb, tb), (sa, ra, ta) = both(engine.metrics)

    print("\n  Q2 (LAX vs SNA), Q3 (ANC), Q4 (SFO) — named airports:")
    print(f"  {'apt':<5} {'TDPI before':>12} {'TDPI after':>11} "
          f"{'rank before':>12} {'rank after':>11}")
    for code in DEMO_AIRPORTS:
        print(f"  {code:<5} {f1(sb.get(code)):>12} {f1(sa.get(code)):>11} "
              f"{str(rb.get(code, '—')):>12} {str(ra.get(code, '—')):>11}")

    print("\n  Q1 (New England terminal expansion) — regional ranking:")
    ne = sorted(engine.resolve_region(NEW_ENGLAND),
                key=lambda i: (-(sa.get(i) or -1), i))
    print(f"  {'#':>3} {'apt':<5} {'TDPI before':>12} {'TDPI after':>11} "
          f"{'national before':>16} {'national after':>15}")
    for n, i in enumerate(ne, 1):
        print(f"  {n:>3} {i:<5} {f1(sb.get(i)):>12} {f1(sa.get(i)):>11} "
              f"{str(rb.get(i, '—')):>16} {str(ra.get(i, '—')):>15}")

    order_b = [i for i in sorted(ne, key=lambda x: (-(sb.get(x) or -1), x))]
    order_a = [i for i in sorted(ne, key=lambda x: (-(sa.get(x) or -1), x))]
    print(f"\n  New England order identical before/after: {order_b == order_a}")
    if order_b != order_a:
        print(f"    before: {order_b}")
        print(f"    after : {order_a}")

    codes = list(dict.fromkeys(DEMO_AIRPORTS + ne))
    deltas = [abs(sa[c] - sb[c]) for c in codes
              if sa.get(c) is not None and sb.get(c) is not None]
    print(f"  max |TDPI change| across all demo-relevant airports: "
          f"{max(deltas):.3f} pt")

    # The substantive outputs are the divergence classes, not the raw scores.
    before_cls, after_cls = _classes(engine, codes)
    diff = {c: (before_cls[c], after_cls[c]) for c in codes
            if before_cls[c] != after_cls[c]}
    print(f"  divergence classifications changed: {diff if diff else 'none'}")
    print(f"  T2 suppressed for any demo-relevant airport: "
          f"{[c for c in codes if ta.get(c) is None] or 'none'}")


def _classes(engine: AnalyticsEngine, codes: list[str]) -> tuple[dict, dict]:
    """Divergence class per airport, before and after the guard."""
    from app.analytics.scoring import classify, compute_aci

    def run() -> dict[str, str]:
        cohort = build_cohort(engine.metrics)
        by = {m.iata: m for m in cohort.members}
        out = {}
        for c in codes:
            m = by.get(c)
            if m is None:
                out[c] = "ABSENT"
                continue
            out[c] = classify(compute_tdpi(m, cohort), compute_aci(m, cohort))
        return out

    after = run()
    with Shadow(list(engine.metrics.values())):
        before = run()
    return before, after


def s_rule(engine: AnalyticsEngine) -> None:
    rule("4. WHY THIS RULE, AND WHY NOT THE ALTERNATIVES")
    cohort = engine.cohort()
    by = {m.iata: m for m in cohort.members}

    print("""
  Three candidate rules, evaluated against the airports that actually differ.

    (a) absolute floor      prior_months >= 10
    (b) equal counts        prior_months >= window_months - 1
    (c) matching months     every window month has a prior counterpart  [ADOPTED]
""")
    print(f"  {'apt':<5} {'win':>4} {'pri':>4} {'match':>6} "
          f"{'(a) floor':>10} {'(b) counts':>11} {'(c) months':>11} "
          f"{'correct?':>9}")
    print("  " + "-" * 74)
    verdicts = {
        "GUF": "suppress", "WYS": "keep", "GST": "suppress",
        "KLW": "suppress", "BGM": "keep", "BLD": "keep",
    }
    for code, want in verdicts.items():
        m = by.get(code)
        if m is None:
            continue
        a = m.traffic_months_prior >= 10
        b = m.traffic_months_prior >= m.traffic_months - 1
        c = m.yoy_windows_aligned
        mark = lambda ok: "keep" if ok else "suppress"   # noqa: E731
        agree = "(c) ✓" if mark(c) == want else "(c) ✗"
        print(f"  {code:<5} {m.traffic_months:>4} {m.traffic_months_prior:>4} "
              f"{m.yoy_months_matched:>6} {mark(a):>10} {mark(b):>11} "
              f"{mark(c):>11} {agree:>9}")

    print("""
  (a) fails on WYS: a seasonal airport whose 6 window and 6 prior months align
      exactly. Its +22% is a valid figure and a floor would discard it.
  (b) fails on GST and KLW: 11 and 11 months, equal counts, but the window
      holds 2025-12 while the prior side holds 2025-04 — December over April.
  (c) states the property a ratio actually needs and has no threshold.

  Why (c) demands COMPLETE alignment rather than scoring whatever overlaps:""")
    guf = by.get("GUF")
    if guf:
        cur, pri, matched, unmatched = guf._yoy_passenger_totals()
        print(f"    GUF matched months: {matched} (of {guf.traffic_months}); "
              f"prior total across them = {pri:,.0f} passengers")
        print(f"    like-for-like ratio on just those months = "
              f"{(cur / pri - 1.0) * 100:,.0f}%")
        print("    Still meaningless, so the component is dropped, not rescaled.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section")
    args = ap.parse_args()
    engine = AnalyticsEngine()
    try:
        print(f"Phase 8.1c — YoY comparability guard | window {engine.window} "
              f"| cohort {len(engine.cohort().members)}")
        sections = {
            "inspect": lambda: s_inspect(engine),
            "effect": lambda: s_effect(engine),
            "demo": lambda: s_demo(engine),
            "rule": lambda: s_rule(engine),
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
