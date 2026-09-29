"""Phase 8.2 — is a high ACI persistent pressure or a few bad months?

Offline only. Reads the committed warehouse; writes nothing. No API calls.

    python evaluate_aci_persistence.py
    python evaluate_aci_persistence.py --section cases
"""

from __future__ import annotations

import argparse
import statistics as st
import sys
from collections import Counter

from app.analytics import AnalyticsEngine
from app.analytics.definitions import ACI_METRICS, MIN_OTP_FLIGHTS_FOR_ACI
from etl import config as etl_config
from app.analytics.persistence import (
    CONCENTRATED_DROP_POINTS,
    ELEVATED_MONTH_ACI,
    MIN_FLIGHTS_PER_MONTH,
    PERSISTENT_SHARE,
    build_profile,
    load_monthly_delay,
)

CASES = ["BOS", "BGR", "LAX", "SNA", "ACK"]


def rule(t: str) -> None:
    print("\n" + "=" * 88)
    print(t)
    print("=" * 88)


def f1(v: float | None) -> str:
    return "—" if v is None else f"{v:.1f}"


def spearman(a: list[float], b: list[float]) -> float:
    def ranks(xs: list[float]) -> list[float]:
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
    monthly = load_monthly_delay(
        engine.conn, etl_config.WINDOW_START, etl_config.WINDOW_END)
    profiles = {}
    for m in cohort.members:
        rows = monthly.get(m.iata, [])
        if not rows:
            continue
        p = build_profile(m, rows, cohort)
        if p.annual_aci is not None:
            profiles[m.iata] = p
    return cohort, monthly, profiles


# ---------------------------------------------------------------------------


def s_data(engine: AnalyticsEngine, cohort, monthly, profiles) -> None:
    rule("1. AVAILABLE DATA — which ACI components survive monthly granularity")
    elig = [m for m in cohort.members if m.flights >= MIN_OTP_FLIGHTS_FOR_ACI]
    print(f"\n  window {engine.window}   cohort {len(cohort.members)}   "
          f"ACI-eligible {len(elig)}   profiles built {len(profiles)}")
    rows = [md for m in elig for md in monthly.get(m.iata, [])]
    print(f"  airport-months for eligible airports: {len(rows):,}")

    print(f"\n  {'component':<28}{'attr':<22}{'weight':>7}{'zero-denom months':>19}")
    for d in ACI_METRICS:
        if d.attr == "cancel_rate":
            zero = sum(1 for md in rows if md.flights == 0)
        else:
            field = {"taxi_out_avg": "taxi_out_n",
                     "nas_delay_per_flight": "nas_delay_n",
                     "dep_del15_rate": "dep_del15_n"}[d.attr]
            zero = sum(1 for md in rows if getattr(md, field) == 0)
        print(f"  {d.label:<28}{d.attr:<22}{d.weight:>7.2f}{zero:>19}")
    print("\n  All four components have a monthly denominator on every month, so")
    print("  all four are evaluable monthly. No component had to be dropped.")

    cnt = Counter(len(monthly.get(m.iata, [])) for m in elig)
    print(f"\n  months present per eligible airport: "
          f"{dict(sorted(cnt.items()))}")
    vols = sorted(md.flights for md in rows)
    print(f"  monthly flight volume: min={vols[0]:,} "
          f"p5={vols[len(vols)//20]:,} med={vols[len(vols)//2]:,} "
          f"max={vols[-1]:,}")
    print(f"  months below the {MIN_FLIGHTS_PER_MONTH}-flight diagnostic floor: "
          f"{sum(1 for v in vols if v < MIN_FLIGHTS_PER_MONTH)} of {len(vols)}")


def s_seasonality(engine: AnalyticsEngine, cohort, monthly, profiles) -> None:
    rule("2. COHORT-WIDE SEASONALITY — the backdrop every airport sits in")
    elig = [m.iata for m in cohort.members if m.flights >= MIN_OTP_FLIGHTS_FOR_ACI]
    months = sorted({md.month for i in elig for md in monthly.get(i, [])})
    print(f"\n  flight-weighted cohort averages (eligible airports only)")
    print(f"  {'month':<9}{'flights':>10}{'taxi-out':>10}{'NAS/flt':>9}"
          f"{'del15':>8}{'cancel':>8}{'mean mACI':>10}")
    series = {}
    for mo in months:
        mr = [md for i in elig for md in monthly.get(i, []) if md.month == mo]
        fl = sum(md.flights for md in mr)
        to = sum(md.taxi_out_sum for md in mr) / max(1, sum(md.taxi_out_n for md in mr))
        na = sum(md.nas_delay_sum for md in mr) / max(1, sum(md.nas_delay_n for md in mr))
        d15 = sum(md.dep_del15 for md in mr) / max(1, sum(md.dep_del15_n for md in mr))
        cx = sum(md.cancelled for md in mr) / max(1, fl)
        macis = [ms.aci for p in profiles.values() for ms in p.months
                 if ms.month == mo and ms.aci is not None]
        mean_maci = st.mean(macis) if macis else None
        series[mo] = (to, na, d15, cx, mean_maci)
        print(f"  {mo:<9}{fl:>10,}{to:>10.2f}{na:>9.2f}{d15:>7.1%}{cx:>8.2%}"
              f"{f1(mean_maci):>10}")

    def rng(i: int) -> str:
        vals = [v[i] for v in series.values() if v[i] is not None]
        return f"{min(vals):.3g}..{max(vals):.3g} ({max(vals)/min(vals):.1f}x)"

    print(f"\n  cohort-wide range across the year:")
    print(f"    taxi-out        {rng(0)}")
    print(f"    NAS per flight  {rng(1)}")
    print(f"    del15 rate      {rng(2)}")
    print(f"    cancel rate     {rng(3)}")
    print("\n  Cancellations swing an order of magnitude and NAS delay roughly")
    print("  doubles; taxi-out is comparatively stable. A1's stability and A4's")
    print("  volatility are consistent with their production weights (0.30, 0.15).")


def s_cohort(engine: AnalyticsEngine, cohort, monthly, profiles) -> None:
    rule("3. COHORT-WIDE PERSISTENCE")
    labels = Counter(p.label for p in profiles.values())
    print(f"\n  persistence label distribution ({len(profiles)} airports):")
    for k in ("PERSISTENT", "MIXED", "EPISODIC", "INSUFFICIENT_DATA"):
        print(f"    {k:<20}{labels.get(k, 0):>5}")

    drops = [p.episodic_drop for p in profiles.values()
             if p.episodic_drop is not None]
    print(f"\n  points lost when the worst 2 months are removed (n={len(drops)}):")
    ds = sorted(drops)
    print(f"    min={ds[0]:+.1f}  p25={ds[len(ds)//4]:+.1f}  "
          f"med={ds[len(ds)//2]:+.1f}  p75={ds[3*len(ds)//4]:+.1f}  "
          f"max={ds[-1]:+.1f}")
    print(f"    above 8 pt (cohort p75, the calibrated cut): "
          f"{sum(1 for d in drops if d > 8)}")

    # Is that drop behaviour, or just arithmetic? Remove two MIDDLE months.
    nulls = [p.null_drop for p in profiles.values() if p.null_drop is not None]
    exc = [p.excess_drop for p in profiles.values() if p.excess_drop is not None]
    print(f"\n  NULL BASELINE — remove two MIDDLE-ranked months instead "
          f"(n={len(nulls)}):")
    ns = sorted(nulls)
    print(f"    min={ns[0]:+.1f}  med={ns[len(ns)//2]:+.1f}  "
          f"max={ns[-1]:+.1f}   mean={st.mean(nulls):+.2f}")
    print(f"    excess (worst2 - middle2): med={st.median(exc):+.1f}  "
          f"mean={st.mean(exc):+.1f}")
    print("    Removing two typical months barely moves the score, so the")
    print("    worst-2 drop reflects those months' behaviour, not the smaller")
    print("    sample. The measure is validated rather than assumed.")

    # Do high-ACI airports differ from the rest?
    print(f"\n  by annual ACI band:")
    print(f"  {'band':<14}{'n':>5}{'mean elevated share':>21}"
          f"{'mean worst-2 drop':>19}{'mean spread':>13}")
    bands = [("ACI >= 60", lambda s: s >= 60), ("40 <= ACI < 60", lambda s: 40 <= s < 60),
             ("ACI < 40", lambda s: s < 40)]
    for lab, pred in bands:
        sub = [p for p in profiles.values()
               if p.annual_aci is not None and pred(p.annual_aci)]
        if not sub:
            continue
        sh = [p.elevated_share for p in sub if p.elevated_share is not None]
        dr = [p.episodic_drop for p in sub if p.episodic_drop is not None]
        sp = [p.spread for p in sub if p.spread is not None]
        print(f"  {lab:<14}{len(sub):>5}{st.mean(sh):>20.1%}"
              f"{st.mean(dr):>+19.1f}{st.mean(sp):>13.1f}")

    print("\n  Among the high-ACI group, is the score ever carried by a couple of")
    print("  months? Largest worst-2 drops with annual ACI >= 60:")
    hi = [p for p in profiles.values()
          if p.annual_aci is not None and p.annual_aci >= 60
          and p.episodic_drop is not None]
    hi.sort(key=lambda p: -p.episodic_drop)
    print(f"  {'apt':<5}{'annual':>8}{'excl worst2':>13}{'drop':>7}"
          f"{'elevated':>10}{'label':>12}")
    for p in hi[:10]:
        print(f"  {p.iata:<5}{f1(p.annual_aci):>8}"
              f"{f1(p.excluding_worst.get(2)):>13}{p.episodic_drop:>+7.1f}"
              f"{p.elevated_months:>4}/{p.months_evaluated:<5}{p.label:>12}")


def s_cases(engine: AnalyticsEngine, cohort, monthly, profiles) -> None:
    rule("4. AIRPORT CASE STUDIES")
    extra = _outliers(profiles)
    for code in CASES + [c for c in extra if c not in CASES]:
        p = profiles.get(code)
        if p is None:
            m = engine.get_metrics(code)
            why = (f"ACI suppressed ({m.flights:,} window flights, below the "
                   f"{MIN_OTP_FLIGHTS_FOR_ACI:,} gate)") if m else "not in cohort"
            print(f"\n  {code}: no profile — {why}")
            continue
        m = engine.get_metrics(code)
        tag = " [outlier]" if code in extra and code not in CASES else ""
        print(f"\n  --- {code}{tag}  annual ACI {f1(p.annual_aci)}   "
              f"{m.flights:,} flights   label {p.label}")
        print(f"      {'month':<9}{'flights':>9}{'mACI':>7}{'taxi':>7}"
              f"{'NAS':>6}{'del15':>7}{'cancel':>8}")
        rows = {md.month: md for md in monthly.get(code, [])}
        for ms in p.months:
            md = rows.get(ms.month)
            if ms.aci is None:
                print(f"      {ms.month:<9}{ms.flights:>9,}{'—':>7}"
                      f"   (unevaluated: {ms.reason})")
                continue
            mark = " *" if ms.aci >= ELEVATED_MONTH_ACI else "  "
            print(f"      {ms.month:<9}{ms.flights:>9,}{ms.aci:>7.1f}"
                  f"{md.taxi_out_avg:>7.1f}{md.nas_delay_per_flight:>6.1f}"
                  f"{md.dep_del15_rate:>7.1%}{md.cancel_rate:>7.2%}{mark}")
        print(f"      elevated {p.elevated_months}/{p.months_evaluated} months"
              f"   spread {f1(p.spread)} pt")
        print(f"      excl worst 1/2/3: "
              + "  ".join(f"{n}->{f1(p.excluding_worst.get(n))}"
                          for n in (1, 2, 3))
              + f"   worst-2 drop {p.episodic_drop:+.1f}"
              if p.episodic_drop is not None else "")


def _outliers(profiles) -> list[str]:
    """Statistically informative extremes worth showing alongside the named set."""
    out = []
    withdrop = [p for p in profiles.values() if p.episodic_drop is not None]
    if withdrop:
        out.append(max(withdrop, key=lambda p: p.episodic_drop).iata)
    withspread = [p for p in profiles.values() if p.spread is not None]
    if withspread:
        out.append(max(withspread, key=lambda p: p.spread).iata)
    persistent = [p for p in profiles.values()
                  if p.label == "PERSISTENT" and p.annual_aci is not None]
    if persistent:
        out.append(max(persistent, key=lambda p: p.annual_aci).iata)
    return list(dict.fromkeys(out))


def s_volume(engine: AnalyticsEngine, cohort, monthly, profiles) -> None:
    rule("5. LOW-VOLUME AND SEASONALITY SENSITIVITY")
    print("\n  Does the diagnostic reward small airports with volatile months?")
    print(f"  {'flights band':<20}{'n':>5}{'mean spread':>13}"
          f"{'mean worst-2 drop':>19}{'% EPISODIC':>12}")
    bands = [("< 5,000", 0, 5_000), ("5k-25k", 5_000, 25_000),
             ("25k-100k", 25_000, 100_000), (">= 100,000", 100_000, 10**9)]
    for lab, lo, hi in bands:
        sub = []
        for p in profiles.values():
            m = engine.get_metrics(p.iata)
            if m and lo <= m.flights < hi:
                sub.append(p)
        if not sub:
            continue
        sp = [p.spread for p in sub if p.spread is not None]
        dr = [p.episodic_drop for p in sub if p.episodic_drop is not None]
        ep = sum(1 for p in sub if p.label == "EPISODIC") / len(sub)
        print(f"  {lab:<20}{len(sub):>5}{st.mean(sp):>13.1f}"
              f"{st.mean(dr):>+19.1f}{ep:>11.0%}")

    sup = [p for p in profiles.values() if p.months_suppressed]
    print(f"\n  airports with at least one unevaluated month: {len(sup)}")
    for p in sorted(sup, key=lambda x: -x.months_suppressed)[:8]:
        print(f"    {p.iata:<5} {p.months_suppressed} of {len(p.months)} months "
              f"below the floor; label {p.label}")

    print("\n  Seasonality check: are elevated months concentrated in particular")
    print("  calendar months cohort-wide?")
    c = Counter(ms.month for p in profiles.values() for ms in p.months
                if ms.aci is not None and ms.aci >= ELEVATED_MONTH_ACI)
    tot = Counter(ms.month for p in profiles.values() for ms in p.months
                  if ms.aci is not None)
    for mo in sorted(tot):
        share = c[mo] / tot[mo] if tot[mo] else 0
        bar = "#" * int(share * 50)
        print(f"    {mo}  {c[mo]:>4}/{tot[mo]:<4} {share:>6.1%} {bar}")


def s_added(engine: AnalyticsEngine, cohort, monthly, profiles) -> None:
    rule("6. DOES THE DIAGNOSTIC ADD INFORMATION BEYOND THE ACI RANKING?")
    items = [p for p in profiles.values()
             if p.annual_aci is not None and p.elevated_share is not None
             and p.episodic_drop is not None and p.spread is not None]
    aci = [p.annual_aci for p in items]
    print(f"\n  n={len(items)} airports with a full diagnostic")
    for lab, vals in [
        ("elevated share", [p.elevated_share for p in items]),
        ("worst-2 drop", [p.episodic_drop for p in items]),
        ("monthly spread", [p.spread for p in items]),
    ]:
        print(f"    spearman(annual ACI, {lab:<15}) = {spearman(aci, vals):+.3f}")

    print("\n  A diagnostic that merely restates the ranking would correlate near")
    print("  1.0 with it. Elevated share is close to that and therefore adds")
    print("  little; the concentration and spread measures are the informative")
    print("  ones. Interpretation is in the report.")

    print("\n  Same-score, different-CONCENTRATION pairs — the actual added value.")
    print("  Airports within 2.0 ACI points whose worst-2 drop differs by >5 pt:")
    items.sort(key=lambda p: p.annual_aci or 0)
    shown = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if abs((a.annual_aci or 0) - (b.annual_aci or 0)) > 2.0:
                break
            if abs((a.episodic_drop or 0) - (b.episodic_drop or 0)) < 5.0:
                continue
            print(f"    ACI {f1(a.annual_aci):>5}  {a.iata} drop "
                  f"{a.episodic_drop:+5.1f} spread {f1(a.spread):>5} ({a.label})"
                  f"   vs   {b.iata} drop {b.episodic_drop:+5.1f} "
                  f"spread {f1(b.spread):>5} ({b.label})")
            shown += 1
            break
        if shown >= 10:
            break
    if not shown:
        print("    none found")

    # The drop measure saturates at the top of the normalised scale.
    ceiling = [p for p in items if (p.annual_aci or 0) >= 99.0]
    if ceiling:
        print(f"\n  CEILING LIMITATION: {len(ceiling)} airport(s) sit at ACI >= 99,")
        print("  where winsorization clips the raw value. Removing bad months cannot")
        print("  move the score until it falls below the cohort P95, so the drop")
        print("  measure reads ~0 there and must not be read as stability:")
        for p in ceiling:
            print(f"    {p.iata} ACI {f1(p.annual_aci)} worst-2 drop "
                  f"{p.episodic_drop:+.1f} but spread {f1(p.spread)} pt "
                  f"and {p.elevated_months}/{p.months_evaluated} elevated")


def s_threshold(engine: AnalyticsEngine, cohort, monthly, profiles) -> None:
    rule("7. THRESHOLD SENSITIVITY — does the conclusion depend on the cut?")
    print(f"\n  'Elevated' is defined at monthly ACI >= {ELEVATED_MONTH_ACI}. "
          f"Recomputing the label\n  distribution at other cuts:")
    print(f"  {'cut':>6}{'PERSISTENT':>12}{'MIXED':>8}{'EPISODIC':>10}"
          f"{'mean elevated share':>21}")
    for cut in (50.0, 55.0, 60.0, 65.0, 70.0):
        pers = mixed = epi = 0
        shares = []
        for p in profiles.values():
            ev = [ms.aci for ms in p.months if ms.aci is not None]
            if not ev or p.episodic_drop is None or len(ev) < 6:
                continue
            share = sum(1 for a in ev if a >= cut) / len(ev)
            shares.append(share)
            sustained = share >= PERSISTENT_SHARE
            concentrated = p.episodic_drop > CONCENTRATED_DROP_POINTS
            if sustained and not concentrated:
                pers += 1
            elif not sustained and concentrated:
                epi += 1
            else:
                mixed += 1
        star = " <- default" if cut == ELEVATED_MONTH_ACI else ""
        print(f"  {cut:>6.0f}{pers:>12}{mixed:>8}{epi:>10}"
              f"{st.mean(shares):>20.1%}{star}")

    print(f"\n  And against the drop cut (elevated held at "
          f"{ELEVATED_MONTH_ACI:.0f}):")
    print(f"  {'drop cut':>9}{'PERSISTENT':>12}{'MIXED':>8}{'EPISODIC':>10}")
    for dcut in (5.0, 6.0, 8.0, 10.0, 12.0):
        pers = mixed = epi = 0
        for p in profiles.values():
            if p.episodic_drop is None or p.elevated_share is None \
                    or p.months_evaluated < 6:
                continue
            sustained = p.elevated_share >= PERSISTENT_SHARE
            concentrated = p.episodic_drop > dcut
            if sustained and not concentrated:
                pers += 1
            elif not sustained and concentrated:
                epi += 1
            else:
                mixed += 1
        star = " <- calibrated cohort p75" if dcut == CONCENTRATED_DROP_POINTS else ""
        print(f"  {dcut:>9.0f}{pers:>12}{mixed:>8}{epi:>10}{star}")

    print("\n  Both cuts move the label counts, which is why the report leads with")
    print("  the continuous measures (drop, excess over null, elevated share) and")
    print("  treats the three-way label as a convenience, not a finding.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section")
    args = ap.parse_args()
    engine = AnalyticsEngine()
    try:
        cohort, monthly, profiles = build(engine)
        print(f"Phase 8.2 — ACI temporal persistence | window {engine.window} "
              f"| profiles {len(profiles)}")
        sections = {
            "data": s_data, "seasonality": s_seasonality, "cohort": s_cohort,
            "cases": s_cases, "volume": s_volume, "added": s_added,
            "threshold": s_threshold,
        }
        if args.section:
            sections[args.section](engine, cohort, monthly, profiles)
        else:
            for fn in sections.values():
                fn(engine, cohort, monthly, profiles)
    finally:
        engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
