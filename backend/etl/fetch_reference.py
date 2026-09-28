"""Fetch OurAirports reference data (airport identifiers + runway inventory).

OurAirports is the crosswalk backbone: it is the only verified source carrying
ICAO ident, IATA code and FAA local code together, which is what lets BTS
(IATA-style) join to the FAA enplanement workbook (Locid).
"""

from __future__ import annotations

import csv
from pathlib import Path

from . import config
from .common import download_file, get_logger, make_session, to_int

log = get_logger("etl.reference")

AIRPORTS_CSV = config.RAW_REFERENCE_DIR / "ourairports_airports.csv"
RUNWAYS_CSV = config.RAW_REFERENCE_DIR / "ourairports_runways.csv"

# Surfaces (OurAirports `surface` is free text) that count as a hard runway.
_HARD_SURFACE_TOKENS = ("ASP", "CON", "PEM", "ASPH", "CONC", "BIT", "TAR")

# OurAirports assigns US territories their own ISO country codes, NOT 'US'.
# Filtering on iso_country == 'US' alone silently drops every territory
# airport, including SJU (~6.7M annual enplanements) — a large, FAA-primary
# airport vanishing with no error. Caught by the crosswalk validation.
US_ISO_COUNTRIES = frozenset({
    "US",  # 50 states + DC
    "PR",  # Puerto Rico
    "VI",  # U.S. Virgin Islands
    "GU",  # Guam
    "MP",  # Northern Mariana Islands
    "AS",  # American Samoa
    "UM",  # U.S. Minor Outlying Islands
})


def fetch(force: bool = False) -> tuple[Path, Path]:
    session = make_session()
    a = download_file(session, config.OURAIRPORTS_AIRPORTS_URL, AIRPORTS_CSV, force=force)
    r = download_file(session, config.OURAIRPORTS_RUNWAYS_URL, RUNWAYS_CSV, force=force)
    return a, r


def load_airports() -> dict[str, dict]:
    """US (incl. territory) airports keyed by ICAO ident.

    Keeps records in `US_ISO_COUNTRIES` that carry an IATA code, which is the
    join key every BTS table uses.
    """
    out: dict[str, dict] = {}
    with open(AIRPORTS_CSV, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if (row.get("iso_country") or "").strip().upper() not in US_ISO_COUNTRIES:
                continue
            iata = (row.get("iata_code") or "").strip().upper()
            if len(iata) != 3:
                continue
            ident = (row.get("ident") or "").strip().upper()
            # iso_region looks like 'US-CA'; take the state part.
            region = (row.get("iso_region") or "").strip().upper()
            state = region.split("-", 1)[1] if "-" in region else None
            out[ident] = {
                "icao": (row.get("icao_code") or ident).strip().upper() or ident,
                "ident": ident,
                "iata": iata,
                "local_code": (row.get("local_code") or "").strip().upper() or None,
                "name": (row.get("name") or "").strip(),
                "city": (row.get("municipality") or "").strip() or None,
                "state": state,
                "lat": _f(row.get("latitude_deg")),
                "lon": _f(row.get("longitude_deg")),
                "type": (row.get("type") or "").strip(),
            }
    log.info("loaded %d US airports with IATA codes", len(out))
    return out


def load_runways() -> dict[str, dict]:
    """Open-runway count and longest length, keyed by airport ICAO ident.

    Closed runways are excluded — counting a decommissioned runway would
    inflate the physical-scale proxy used in TDPI component T4.
    """
    agg: dict[str, dict] = {}
    with open(RUNWAYS_CSV, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if (row.get("closed") or "").strip() == "1":
                continue
            ident = (row.get("airport_ident") or "").strip().upper()
            if not ident:
                continue
            length = to_int(row.get("length_ft")) or 0
            surface = (row.get("surface") or "").strip().upper()
            hard = any(tok in surface for tok in _HARD_SURFACE_TOKENS)
            rec = agg.setdefault(ident, {"runway_count": 0, "longest_runway_ft": 0, "hard_count": 0})
            rec["runway_count"] += 1
            if hard:
                rec["hard_count"] += 1
            if length > rec["longest_runway_ft"]:
                rec["longest_runway_ft"] = length
    log.info("loaded runway inventory for %d airports", len(agg))
    return agg


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    fetch()
    airports = load_airports()
    runways = load_runways()
    for ident in ("KLAX", "KSNA", "KSFO", "PANC", "KBOS"):
        a = airports.get(ident, {})
        r = runways.get(ident, {})
        log.info(
            "%s  iata=%s local=%s runways=%s longest=%s",
            ident, a.get("iata"), a.get("local_code"),
            r.get("runway_count"), r.get("longest_runway_ft"),
        )
