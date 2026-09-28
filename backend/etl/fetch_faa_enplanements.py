"""Fetch FAA passenger boarding (enplanement) workbooks.

This file defines the airport universe: FAA service level 'P' (primary
commercial service) is the authoritative ~380-airport cohort, and the FAA's own
hub classification (L/M/S/N) gives a defensible peer grouping for percentile
normalisation.

Note: faa.gov returns HTTP 403 without a browser User-Agent (verified
2026-09-27); `make_session()` sets one.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from . import config
from .common import download_file, get_logger, make_session, to_float, to_int

log = get_logger("etl.faa")

# Header labels vary slightly year to year ("CY 25 Enplanements" vs
# "CY 2025 Enplanements"), so we match on normalised substrings instead of
# exact strings.
_CANON = {
    "rank": ("rank",),
    "state": ("st",),
    "locid": ("locid",),
    "city": ("city",),
    "name": ("airport name",),
    "service_level": ("s/l",),
    "hub": ("hub",),
}


def _norm(s) -> str:
    return " ".join(str(s or "").strip().lower().split())


def fetch(force: bool = False) -> dict[int, Path]:
    session = make_session()
    paths: dict[int, Path] = {}
    for cy, url in config.FAA_ENPLANEMENTS_FILES.items():
        dest = config.RAW_REFERENCE_DIR / f"faa_enplanements_cy{cy}.xlsx"
        try:
            paths[cy] = download_file(session, url, dest, force=force)
        except Exception as exc:  # noqa: BLE001 - we want the run to continue
            log.error("FAA CY%s download failed: %s", cy, exc)
    return paths


def parse(path: Path, cy: int) -> list[dict]:
    """Parse one FAA enplanement workbook into records.

    Returns [] rather than raising if the header cannot be located, so a
    changed workbook layout degrades this one source instead of failing the
    whole build.
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]

    rows = ws.iter_rows(values_only=True)
    header_idx: dict[str, int] = {}
    enp_cols: list[tuple[int, int]] = []  # (column index, calendar year)

    for raw in rows:
        cells = [_norm(c) for c in raw]
        if "locid" not in cells:
            continue
        for i, c in enumerate(cells):
            for key, tokens in _CANON.items():
                if c in tokens:
                    header_idx[key] = i
            # "cy 25 enplanements" / "cy 2025 enplanements"
            if c.startswith("cy ") and "enplanement" in c:
                digits = "".join(ch for ch in c if ch.isdigit())
                if digits:
                    yr = int(digits)
                    if yr < 100:
                        yr += 2000
                    enp_cols.append((i, yr))
            if "% change" in c or c == "change":
                header_idx["pct_change"] = i
        break

    if "locid" not in header_idx or not enp_cols:
        log.error("CY%s: could not locate header row (found %s)", cy, sorted(header_idx))
        wb.close()
        return []

    enp_cols.sort(key=lambda t: -t[1])
    cur_col, cur_year = enp_cols[0]
    prior_col = enp_cols[1][0] if len(enp_cols) > 1 else None

    out: list[dict] = []
    for raw in rows:
        locid = str(raw[header_idx["locid"]] or "").strip().upper()
        if not locid or len(locid) > 4:
            continue
        enp = to_float(raw[cur_col]) if cur_col < len(raw) else None
        if enp is None:
            continue
        out.append(
            {
                "faa_locid": locid,
                "cy": cur_year,
                "enplanements": enp,
                "prior_year": to_float(raw[prior_col]) if prior_col is not None and prior_col < len(raw) else None,
                "pct_change": to_float(raw[header_idx["pct_change"]]) if "pct_change" in header_idx else None,
                "hub_class": _clean(raw, header_idx.get("hub")),
                "service_level": _clean(raw, header_idx.get("service_level")),
                "rank": to_int(raw[header_idx["rank"]]) if "rank" in header_idx else None,
                "state": _clean(raw, header_idx.get("state")),
                "city": _clean(raw, header_idx.get("city")),
                "name": _clean(raw, header_idx.get("name")),
                "preliminary": 1 if cur_year in config.FAA_PRELIMINARY_YEARS else 0,
            }
        )
    wb.close()
    log.info("CY%s: parsed %d airport records (year column = CY%s)", cy, len(out), cur_year)
    return out


def _clean(raw, idx) -> str | None:
    if idx is None or idx >= len(raw):
        return None
    v = str(raw[idx] or "").strip()
    return v or None


if __name__ == "__main__":
    paths = fetch()
    for cy, p in sorted(paths.items(), reverse=True):
        recs = parse(p, cy)
        primary = [r for r in recs if r["service_level"] == config.PRIMARY_SERVICE_LEVEL]
        log.info("CY%s total=%d primary=%d", cy, len(recs), len(primary))
