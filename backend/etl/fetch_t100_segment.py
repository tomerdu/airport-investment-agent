"""Fetch BTS T-100 Segment (All Carriers) — per-segment, carries DISTANCE.

This is the only verified public source with distance at segment level, which
is what makes an honest long-haul share possible. It includes international
segments and all-cargo carriers; both matter enormously at ANC, where the
defining activity is transpacific freight.

⚠ Access is a scripted ASP.NET WebForms POST, not an API: harvest
__VIEWSTATE / __VIEWSTATEGENERATOR / __EVENTVALIDATION from the form page,
then POST the field selection. It is inherently brittle, so every month is
cached to disk on first success and never re-fetched.

`AIRCRAFT_CONFIG` and `CLASS` are pulled so passenger and cargo activity can
be reported separately (approved decision D4).
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

from . import config
from .common import canonical_iata, get_logger, make_session, to_float

log = get_logger("etl.t100seg")

# Columns requested from the form (each becomes a checkbox named exactly this).
REQUESTED_FIELDS = [
    "DEPARTURES_PERFORMED",
    "SEATS",
    "PASSENGERS",
    "FREIGHT",
    "MAIL",
    "DISTANCE",
    "ORIGIN",
    "DEST",
    "ORIGIN_COUNTRY",
    "DEST_COUNTRY",
    "CARRIER_GROUP_NEW",
    "AIRCRAFT_CONFIG",
    "CLASS",
    "YEAR",
    "MONTH",
]

_HIDDEN_RE = {
    name: re.compile(rf'id="{name}"[^>]*value="([^"]*)"', re.I | re.S)
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")
}


def _hidden(html: str, name: str) -> str:
    m = _HIDDEN_RE[name].search(html)
    if m:
        return m.group(1)
    m = re.search(rf'name="{name}"[^>]*value="([^"]*)"', html, re.I | re.S)
    return m.group(1) if m else ""


def fetch_month(year: int, month: int, *, force: bool = False) -> Path | None:
    """Download one month of T-100 Segment data. Returns the cached zip path."""
    dest = config.RAW_SEGMENT_DIR / f"t100_segment_{year}_{month:02d}.zip"
    if dest.exists() and not force and dest.stat().st_size > 10_000:
        log.info("skip (cached): %s (%.0f KB)", dest.name, dest.stat().st_size / 1e3)
        return dest

    session = make_session()
    page = session.get(config.T100_SEGMENT_FORM_URL, timeout=180)
    page.raise_for_status()
    html = page.text

    body: dict[str, str] = {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__LASTFOCUS": "",
        "__VIEWSTATE": _hidden(html, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _hidden(html, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _hidden(html, "__EVENTVALIDATION"),
        "cboGeography": "All",   # includes international segments
        "cboYear": str(year),
        "cboPeriod": str(month),
        "chkDownloadZip": "on",
        "btnDownload": "Download",
    }
    for field in REQUESTED_FIELDS:
        body[field] = "on"

    resp = session.post(
        config.T100_SEGMENT_FORM_URL,
        data=body,
        timeout=900,
        headers={"Referer": config.T100_SEGMENT_FORM_URL},
    )
    resp.raise_for_status()

    content = resp.content
    if not content.startswith(b"PK"):
        log.error(
            "%s-%02d: expected a zip, got %s (%d bytes) — form contract may have changed",
            year, month, resp.headers.get("Content-Type"), len(content),
        )
        return None

    dest.write_bytes(content)
    log.info("downloaded %s (%.0f KB)", dest.name, len(content) / 1e3)
    return dest


def parse_month(path: Path) -> list[dict]:
    """Parse one cached T-100 Segment zip into segment records."""
    out: list[dict] = []
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.upper().startswith("T_T100") and n.lower().endswith(".csv")]
        if not names:
            log.error("%s: no T_T100*.csv entry (found %s)", path.name, zf.namelist())
            return out
        with zf.open(names[0]) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")
            for row in csv.DictReader(text):
                dep = to_float(row.get("DEPARTURES_PERFORMED"))
                if not dep:  # drop zero/blank-departure rows outright
                    continue
                origin = canonical_iata(row.get("ORIGIN"))
                dest_code = canonical_iata(row.get("DEST"))
                if not origin or not dest_code:
                    continue
                year = (row.get("YEAR") or "").strip()
                month = (row.get("MONTH") or "").strip()
                if not year or not month:
                    continue
                out.append(
                    {
                        "origin": origin,
                        "dest": dest_code,
                        "month": f"{int(year):04d}-{int(month):02d}",
                        "carrier_group": (row.get("CARRIER_GROUP_NEW") or "").strip() or None,
                        "aircraft_config": (row.get("AIRCRAFT_CONFIG") or "").strip() or None,
                        "service_class": (row.get("CLASS") or "").strip() or None,
                        "origin_country": (row.get("ORIGIN_COUNTRY") or "").strip() or None,
                        "dest_country": (row.get("DEST_COUNTRY") or "").strip() or None,
                        "distance_sm": to_float(row.get("DISTANCE")),
                        "departures_performed": dep,
                        "seats": to_float(row.get("SEATS")),
                        "passengers": to_float(row.get("PASSENGERS")),
                    }
                )
    return out


def fetch_window(force: bool = False) -> list[Path]:
    """Fetch every month in the analysis window."""
    paths: list[Path] = []
    for ym in config.WINDOW_MONTHS:
        year, month = (int(p) for p in ym.split("-"))
        try:
            p = fetch_month(year, month, force=force)
            if p:
                paths.append(p)
        except Exception as exc:  # noqa: BLE001
            log.error("%s: fetch failed: %s", ym, exc)
    log.info("segment months available: %d / %d", len(paths), len(config.WINDOW_MONTHS))
    return paths


if __name__ == "__main__":
    paths = fetch_window()
    total = 0
    anc_rows: list[dict] = []
    for p in paths:
        recs = parse_month(p)
        total += len(recs)
        anc_rows.extend(r for r in recs if r["origin"] == "ANC")
    log.info("parsed %d segment rows across %d months", total, len(paths))

    if anc_rows:
        dep = sum(r["departures_performed"] for r in anc_rows)
        log.info("ANC: %d segment rows, %.0f departures performed", len(anc_rows), dep)
        log.info("ANC AIRCRAFT_CONFIG values: %s", sorted({r["aircraft_config"] for r in anc_rows}))
        log.info("ANC CLASS values: %s", sorted({r["service_class"] for r in anc_rows}))
        for th in config.LONG_HAUL_THRESHOLDS_SM:
            lh = sum(r["departures_performed"] for r in anc_rows if (r["distance_sm"] or 0) >= th)
            log.info("  >= %5d sm : %8.0f dep = %5.1f%%", th, lh, 100 * lh / dep)
