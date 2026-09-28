"""Fetch BTS T-100 Segment Summary by Origin Airport (Socrata `r495-tyji`).

This is the traffic backbone: monthly, per origin airport, with a
domestic / outbound-international / inbound-international split.

⚠ Column-name trap. The Socrata field names are truncated and actively
misleading — `outbound_international_1/_2/_3/_4` are NOT variants of one
metric, they are passengers, pax/flight, distance/flight and
distance/passenger respectively. Reading them positionally silently produces
wrong numbers with no error. We therefore resolve every field through the
dataset's *display names* (fetched live from the metadata endpoint) and assert
the mapping before loading anything.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config
from .common import canonical_iata, get_logger, make_session, to_float

log = get_logger("etl.t100")

CACHE_PATH = config.RAW_REFERENCE_DIR / "t100_origin_monthly.json"

# Display name (as published by BTS) -> our column name.
# Resolved against live metadata so a truncated API field is never trusted.
DISPLAY_TO_COL = {
    "Origin Airport Code": "iata",
    "Date": "month_raw",
    "Total Departures": "departures",
    "Total Passengers": "passengers",
    "Total Seats": "seats",
    "Total Distance/Flight (sm)": "avg_distance_sm",
    "Total Freight (lbs)": "freight_lbs",
    "Total Mail (lbs)": "mail_lbs",
    "Domestic Departures": "dom_departures",
    "Domestic Passengers": "dom_passengers",
    "Domestic Seats": "dom_seats",
    "Outbound International Departures": "intl_out_departures",
    "Outbound International Passengers": "intl_out_passengers",
    "Outbound International Seats": "intl_out_seats",
}


def resolve_field_map(session) -> dict[str, str]:
    """Map our column names -> Socrata API field names, via display names."""
    r = session.get(config.SOCRATA_T100_ORIGIN_META, timeout=120)
    r.raise_for_status()
    meta = r.json()

    by_display = {c["name"].strip(): c["fieldName"] for c in meta.get("columns", [])}
    field_map: dict[str, str] = {}
    missing: list[str] = []
    for display, col in DISPLAY_TO_COL.items():
        api_field = by_display.get(display)
        if api_field is None:
            missing.append(display)
        else:
            field_map[col] = api_field

    if missing:
        raise RuntimeError(
            "T-100 dataset schema changed; these display names are gone: "
            + ", ".join(missing)
        )

    # Guard the specific trap: these must resolve to *different* API fields.
    trap = {field_map["intl_out_departures"], field_map["intl_out_passengers"], field_map["intl_out_seats"]}
    if len(trap) != 3:
        raise RuntimeError("Outbound-international fields collapsed to the same API field")

    log.info("resolved %d T-100 fields via display names", len(field_map))
    log.debug("field map: %s", field_map)
    return field_map


def fetch(force: bool = False) -> list[dict]:
    """Pull the analysis window plus the prior-year window (for YoY growth)."""
    if CACHE_PATH.exists() and not force:
        rows = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        log.info("skip (cached): %d T-100 rows from %s", len(rows), CACHE_PATH.name)
        return rows

    session = make_session()
    if config.SOCRATA_APP_TOKEN:
        session.headers["X-App-Token"] = config.SOCRATA_APP_TOKEN
        log.info("using Socrata app token from environment")

    field_map = resolve_field_map(session)

    # Widest span we need: prior window start .. analysis window end.
    start = config.PRIOR_WINDOW_MONTHS[0] + "-01T00:00:00.000"
    end_y, end_m = (int(p) for p in config.WINDOW_END.split("-"))
    end_m += 1
    if end_m > 12:
        end_m, end_y = 1, end_y + 1
    end = f"{end_y:04d}-{end_m:02d}-01T00:00:00.000"

    date_field = field_map["month_raw"]
    select = ",".join(f"{api} AS {col}" for col, api in field_map.items())
    where = f"{date_field} >= '{start}' AND {date_field} < '{end}'"

    rows: list[dict] = []
    limit, offset = 50000, 0
    while True:
        params = {
            "$select": select,
            "$where": where,
            "$order": f"{date_field}, {field_map['iata']}",
            "$limit": limit,
            "$offset": offset,
        }
        r = session.get(config.SOCRATA_T100_ORIGIN_URL, params=params, timeout=300)
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        log.info("fetched %d rows (offset %d)", len(batch), offset)
        if len(batch) < limit:
            break
        offset += limit

    CACHE_PATH.write_text(json.dumps(rows), encoding="utf-8")
    log.info("cached %d T-100 rows -> %s", len(rows), CACHE_PATH.name)
    return rows


def normalize(rows: list[dict]) -> list[dict]:
    """Convert raw Socrata rows into warehouse records."""
    out: list[dict] = []
    for row in rows:
        iata = canonical_iata(row.get("iata"))
        if not iata:
            continue
        raw_month = (row.get("month_raw") or "")[:7]  # 'YYYY-MM'
        if len(raw_month) != 7:
            continue
        out.append(
            {
                "iata": iata,
                "month": raw_month,
                "departures": to_float(row.get("departures")),
                "passengers": to_float(row.get("passengers")),
                "seats": to_float(row.get("seats")),
                "freight_lbs": to_float(row.get("freight_lbs")),
                "mail_lbs": to_float(row.get("mail_lbs")),
                "dom_departures": to_float(row.get("dom_departures")),
                "dom_passengers": to_float(row.get("dom_passengers")),
                "dom_seats": to_float(row.get("dom_seats")),
                "intl_out_departures": to_float(row.get("intl_out_departures")),
                "intl_out_passengers": to_float(row.get("intl_out_passengers")),
                "intl_out_seats": to_float(row.get("intl_out_seats")),
                "avg_distance_sm": to_float(row.get("avg_distance_sm")),
            }
        )
    # Aliasing could in principle collapse two source codes onto one
    # (iata, month). That would mean BTS reported the same airport under both
    # its old and new code in the same month — anomalous, and silently
    # resolved by the insert's ON CONFLICT. Surface it instead.
    seen: set[tuple[str, str]] = set()
    collisions = [
        (r["iata"], r["month"]) for r in out
        if (r["iata"], r["month"]) in seen or seen.add((r["iata"], r["month"]))  # type: ignore[func-returns-value]
    ]
    if collisions:
        log.error("alias collision on (iata, month): %s", sorted(set(collisions))[:10])

    log.info("normalized %d T-100 airport-month records", len(out))
    return out


if __name__ == "__main__":
    recs = normalize(fetch())
    months = sorted({r["month"] for r in recs})
    log.info("months: %s .. %s (%d)", months[0], months[-1], len(months))
    for code in ("LAX", "SFO", "BOS", "SNA", "ANC"):
        sel = [r for r in recs if r["iata"] == code and r["month"] in config.WINDOW_MONTHS]
        dep = sum(r["departures"] or 0 for r in sel)
        pax = sum(r["passengers"] or 0 for r in sel)
        seats = sum(r["seats"] or 0 for r in sel)
        lf = 100 * pax / seats if seats else 0
        log.info("%s months=%2d dep=%9.0f pax=%11.0f seats=%11.0f LF=%.1f%%", code, len(sel), dep, pax, seats, lf)
