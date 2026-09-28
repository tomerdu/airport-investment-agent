"""Airport identifier crosswalk: IATA ↔ ICAO ↔ FAA Locid.

Why this module exists and is tested first: the four data sources key on three
different identifier systems.

    BTS T-100 / OTP  ->  IATA-style 3-letter code  ("ANC")
    FAA enplanements ->  FAA Locid                 ("ANC")
    OurAirports      ->  ICAO ident + iata_code    ("PANC" / "ANC")

For most US airports Locid and IATA coincide, but ICAO diverges outside the
lower 48 (PANC not KANC; PHNL not KHNL; TJSJ not KSJU), and a handful of
airports have a Locid that differs from their IATA code. A bad join here does
not raise — it silently drops an airport or attaches the wrong runway count to
the wrong airport, which then flows into a score. So the join is explicit,
audited, and covered by tests.

The airport universe is defined authoritatively as FAA service level 'P'
(primary commercial service), ~380-400 airports (approved decision D8).
"""

from __future__ import annotations

from . import config
from .common import get_logger

log = get_logger("etl.crosswalk")

NEW_ENGLAND = set(config.NEW_ENGLAND_STATES)


def assign_region(state: str | None) -> str | None:
    if not state:
        return None
    if state in NEW_ENGLAND:
        return "new_england"
    return None


def build(
    faa_records: list[dict],
    oa_airports: dict[str, dict],
    oa_runways: dict[str, dict],
) -> tuple[list[dict], dict]:
    """Join the three identifier systems into warehouse `airports` rows.

    Returns (rows, report) where `report` records how each airport was matched
    so the checkpoint can show match quality rather than assert it.
    """
    # Index OurAirports by the two keys we may match on.
    by_local: dict[str, dict] = {}
    by_iata: dict[str, dict] = {}
    for rec in oa_airports.values():
        if rec.get("local_code"):
            by_local.setdefault(rec["local_code"], rec)
        by_iata.setdefault(rec["iata"], rec)

    rows: list[dict] = []
    matched_by = {"local_code": 0, "iata_code": 0, "unmatched": 0}
    unmatched: list[str] = []
    unmatched_primary: list[str] = []
    seen_iata: set[str] = set()
    faa_primary_count = sum(
        1 for fr in faa_records if fr.get("service_level") == config.PRIMARY_SERVICE_LEVEL
    )

    for fr in faa_records:
        locid = fr["faa_locid"]
        is_primary = fr.get("service_level") == config.PRIMARY_SERVICE_LEVEL

        # Prefer FAA local_code, fall back to IATA. Both are exact matches;
        # we never fuzzy-match on name, which would be a silent-error factory.
        oa = by_local.get(locid)
        how = "local_code"
        if oa is None:
            oa = by_iata.get(locid)
            how = "iata_code"

        if oa is None:
            matched_by["unmatched"] += 1
            unmatched.append(locid)
            if is_primary:
                # A primary airport failing to match is a silently missing
                # airport, not an out-of-scope GA field. Surface it loudly.
                unmatched_primary.append(locid)
                log.warning("FAA PRIMARY airport %s (%s) did not match any reference record",
                            locid, fr.get("name"))
            continue

        iata = oa["iata"]
        if iata in seen_iata:
            # Two FAA rows resolving to one IATA code would silently overwrite.
            log.warning("duplicate IATA %s (locid %s) — keeping first", iata, locid)
            continue
        seen_iata.add(iata)
        matched_by[how] += 1

        rw = oa_runways.get(oa["ident"], {})
        state = fr.get("state") or oa.get("state")

        rows.append(
            {
                "iata": iata,
                "icao": oa.get("icao") or oa["ident"],
                "faa_locid": locid,
                "name": fr.get("name") or oa.get("name"),
                "city": fr.get("city") or oa.get("city"),
                "state": state,
                "region": assign_region(state),
                "hub_class": fr.get("hub_class"),
                "service_level": fr.get("service_level"),
                "lat": oa.get("lat"),
                "lon": oa.get("lon"),
                "runway_count": rw.get("runway_count"),
                "longest_runway_ft": rw.get("longest_runway_ft") or None,
                "in_universe": 1 if fr.get("service_level") == config.PRIMARY_SERVICE_LEVEL else 0,
            }
        )

    report = {
        "faa_records": len(faa_records),
        "faa_primary_count": faa_primary_count,
        "matched": len(rows),
        "matched_by_local_code": matched_by["local_code"],
        "matched_by_iata_code": matched_by["iata_code"],
        "unmatched": matched_by["unmatched"],
        "unmatched_locids": sorted(unmatched)[:40],
        "unmatched_primary": sorted(unmatched_primary),
        "in_universe": sum(r["in_universe"] for r in rows),
        "missing_runways": sum(1 for r in rows if not r["runway_count"]),
    }

    log.info(
        "crosswalk: %d/%d matched (local=%d iata=%d unmatched=%d), %d in universe",
        report["matched"], report["faa_records"],
        report["matched_by_local_code"], report["matched_by_iata_code"],
        report["unmatched"], report["in_universe"],
    )
    return rows, report
