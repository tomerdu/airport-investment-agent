"""Generate the Checkpoint 2 deliverables straight from the production engine.

Every figure printed here is produced by the same code path the agent layer
will call — nothing is recomputed or transcribed by hand.

    python report_checkpoint2.py
"""

from __future__ import annotations

from app.analytics import AnalyticsEngine


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def fmt(v, spec=".1f", dash="n/a"):
    return dash if v is None else format(v, spec)


def main() -> None:
    e = AnalyticsEngine()

    rule("0. ENGINE CONTEXT")
    c = e.cohort()
    print(f"  Analysis window : {e.window}")
    print(f"  Airport universe: {len(e.metrics)} FAA primary commercial service airports")
    print(f"  Scoring cohort  : {c.size} with traffic; {c.aci_eligible_size} clear the ACI volume gate")
    print("  Sources:")
    for s in e.sources():
        print(f"    - {s['dataset']:<20} {s['coverage']:<20} retrieved {s['retrieved_at']}")

    # ---------------------------------------------------------------
    rule("1. SAMPLE TDPI / ACI CALCULATIONS")
    for code in ("SFO", "LAX", "SNA", "BOS", "ANC"):
        s = e.profile(code)
        m = e.get_metrics(code)
        print(f"\n  --- {code} ({s.name}) — hub {s.hub_class} ---")
        print(f"      TDPI {fmt(s.tdpi.score):>5}  (coverage {s.tdpi.coverage:.0%})"
              f"   ACI {fmt(s.aci.score):>5}  (coverage {s.aci.coverage:.0%})")
        print(f"      class: {s.divergence_class}")
        for idx in (s.tdpi, s.aci):
            print(f"      [{idx.index}]")
            for comp in idx.components:
                if comp.available:
                    print(f"        {comp.id}  {comp.label[:38]:<38} raw={comp.raw_display:>14}"
                          f"  norm={fmt(comp.normalized):>5}  pctl={fmt(comp.percentile, '.0f'):>3}"
                          f"  w={comp.weight:.2f}  eff={fmt(comp.effective_weight, '.3f')}"
                          f"  contrib={fmt(comp.contribution, '.2f'):>6}")
                else:
                    print(f"        {comp.id}  {comp.label[:38]:<38} UNAVAILABLE (weight {comp.weight:.2f} renormalised away)")
            if idx.score is None:
                print(f"        -> SUPPRESSED: {idx.suppressed_reason}")

    # ---------------------------------------------------------------
    rule("2. NEW ENGLAND — TDPI RANKING")
    codes = e.resolve_region("new_england")
    r = e.rank(codes, index="TDPI")
    print(f"  cohort: {r['cohort']} (n={r['cohort_size']})\n")
    print(f"  {'#':>2} {'APT':<4} {'TDPI':>6} {'ACI':>7} {'CLASS':<30} {'PAX(12mo)':>11} {'LF':>6} {'PaxGr':>7}")
    for row in r["ranked"]:
        comp = {x["id"]: x for x in row["tdpi"]["components"]}
        aci = row["aci"]["score"]
        print(f"  {row['rank']:>2} {row['iata']:<4} {row['tdpi']['score']:>6.1f}"
              f" {fmt(aci, '.1f', 'suppr'):>7} {row['divergence_class']:<30}"
              f" {row['scale']['passengers']:>11,.0f}"
              f" {comp['T1']['raw_display'] or '-':>6} {comp['T2']['raw_display'] or '-':>7}")

    print("\n  Top-3 full breakdowns:")
    for row in r["ranked"][:3]:
        print(f"\n    {row['iata']} — TDPI {row['tdpi']['score']:.1f} (coverage {row['tdpi']['coverage']:.0%})")
        for comp in row["tdpi"]["components"]:
            print(f"      {comp['id']} {comp['label'][:40]:<40} raw={str(comp['raw_display']):>13}"
                  f" norm={fmt(comp['normalized']):>5} w={comp['weight']:.2f}"
                  f" contrib={fmt(comp['contribution'], '.2f'):>6}")

    # ---------------------------------------------------------------
    rule("3. LAX vs SNA — CONGESTION COMPARISON")
    cmp_ = e.compare(["LAX", "SNA"])
    lax, sna = cmp_["airports"]
    print(f"  {'METRIC':<34} {'LAX':>14} {'SNA':>14}")
    print("  -- VOLUME --")
    for label, key, spec in (
        ("Departures (T-100)", "departures", ",.0f"),
        ("Passengers", "passengers", ",.0f"),
        ("On-time-reported flights", "otp_flights", ",.0f"),
    ):
        print(f"  {label:<34} {lax['volume'][key]:>14{spec}} {sna['volume'][key]:>14{spec}}")
    print("  -- PER-FLIGHT INTENSITY --")
    for label, key, spec in (
        ("Avg taxi-out (min)", "taxi_out_avg_min", ".1f"),
        ("NAS delay per flight (min)", "nas_delay_per_flight_min", ".2f"),
        ("Departures >15 min", "dep_del15_rate", ".1%"),
        ("Cancellation rate", "cancel_rate", ".2%"),
        ("Avg departure delay (min)", "dep_delay_avg_min", ".1f"),
        ("Load factor", "load_factor", ".1%"),
    ):
        print(f"  {label:<34} {lax['intensity'][key]:>14{spec}} {sna['intensity'][key]:>14{spec}}")
    print("  -- PHYSICAL --")
    print(f"  {'Runways':<34} {lax['runway_count']:>14} {sna['runway_count']:>14}")
    print(f"  {'Longest runway (ft)':<34} {lax['longest_runway_ft']:>14,} {sna['longest_runway_ft']:>14,}")
    print("  -- SCORES --")
    for a in (lax, sna):
        s = a["scores"]
        print(f"  {a['iata']:<34} TDPI {s['tdpi']['score']:>5.1f}   ACI {s['aci']['score']:>5.1f}   {s['divergence_class']}")

    # ---------------------------------------------------------------
    rule("4. ANC — LONG-HAUL SENSITIVITY")
    lh = e.long_haul("ANC")
    print(f"  Period : {lh.period}   (full production window: {lh.is_full_window})")
    print(f"  Defn   : {lh.definition}")
    print(f"  Unit   : {lh.unit}\n")
    hdr = f"  {'Threshold':>10} " + "".join(f"{s.scope_label.split('(')[0].strip()[:22]:>24}" for s in lh.scopes)
    print(hdr)
    for i, th in enumerate([b.threshold_sm for b in lh.scopes[0].bands]):
        line = f"  {'>= ' + format(th, ',') + ' sm':>10} "
        for s in lh.scopes:
            b = s.bands[i]
            line += f"{b.departures:>13,.0f} ({b.share_pct:>4.1f}%)".rjust(24)
        print(line)
    print()
    for s in lh.scopes:
        print(f"    {s.scope_label:<38} departures={s.total_departures:>9,.0f}  passengers={s.total_passengers:>11,.0f}"
              f"  headline(>=3000sm)={fmt(s.headline_share_pct):>5}%")

    print("\n  Single-month comparison (approved correction C):")
    dec = e.long_haul("ANC", month_start="2025-12", month_end="2025-12")
    dec_all = next(s for s in dec.scopes if s.scope == "all_carriers")
    yr_all = next(s for s in lh.scopes if s.scope == "all_carriers")
    print(f"    {dec.period:<28} >=3,000 sm = {dec_all.headline_share_pct:.1f}%   (research-stage figure)")
    print(f"    {lh.period:<28} >=3,000 sm = {yr_all.headline_share_pct:.1f}%   (PRODUCTION figure)")

    # ---------------------------------------------------------------
    rule("5. SFO — UNMET DEMAND EVIDENCE")
    u = e.unmet_demand("SFO")
    print(f"  Window: {u.window}")
    print(f"  Band  : {u.evidence_band}  ({u.triggered_count} of {u.available_count} evaluable indicators triggered; {u.total_count} defined)\n")
    for i in u.indicators:
        if i.available:
            state = "TRIGGERED" if i.triggered else "not triggered"
            print(f"    {i.id} {i.label:<44} {str(i.value_display):>36}")
            print(f"        threshold {i.threshold_display}  ->  {state}")
        else:
            print(f"    {i.id} {i.label:<44} {'UNAVAILABLE':>36}")
            print(f"        reason: {i.unavailable_reason}")
    print(f"\n  CAVEAT: {u.caveat}")
    print(f"  Numeric magnitude field present in payload: "
          f"{any(k in u.to_dict() for k in ('unmet_passengers', 'unmet_flights', 'magnitude'))}")

    e.close()


if __name__ == "__main__":
    main()
