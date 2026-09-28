"""Aggregate BTS On-Time Performance monthly ZIPs into airport-month delay rows.

Two constraints shape this module:

1. **Memory.** Each monthly CSV is ~273 MB uncompressed (631,970 rows for
   2026-07). We stream the zip entry and aggregate on the fly; the file is
   never materialised.

2. **Sums, not averages.** We emit sums and counts, never per-month means.
   Averaging monthly averages across a window would weight BTV's 594-flight
   month equally with LAX's 17,454-flight month. Callers divide
   `taxi_out_sum / taxi_out_n` over whatever window they need.

Coverage caveat carried downstream: OTP is domestic, reporting-carrier only,
and excludes all-cargo carriers entirely.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from . import config
from .common import canonical_iata, get_logger, to_float

log = get_logger("etl.otp")

# Columns we need; anything else in the 110-column file is ignored.
_NEEDED = (
    "Origin", "FlightDate", "Cancelled", "Diverted", "DepDel15",
    "TaxiOut", "NASDelay", "DepDelayMinutes",
)


def _blank_row(iata: str, month: str) -> dict:
    return {
        "iata": iata, "month": month,
        "flights": 0, "cancelled": 0, "diverted": 0,
        "dep_del15": 0, "dep_del15_n": 0,
        "taxi_out_sum": 0.0, "taxi_out_n": 0,
        "nas_delay_sum": 0.0, "nas_delay_n": 0,
        "dep_delay_sum": 0.0, "dep_delay_n": 0,
    }


def aggregate_zip(path: Path) -> dict[tuple[str, str], dict]:
    """Aggregate one monthly OTP zip to {(iata, month): row}."""
    agg: dict[tuple[str, str], dict] = {}
    rows_read = 0

    with zipfile.ZipFile(path) as zf:
        names = [
            n for n in zf.namelist()
            if n.lower().endswith(".csv") and "readme" not in n.lower()
        ]
        if not names:
            log.error("%s: no CSV entry found", path.name)
            return agg
        # The data file is by far the largest entry.
        name = max(names, key=lambda n: zf.getinfo(n).file_size)

        with zf.open(name) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")
            reader = csv.DictReader(text)

            missing = [c for c in _NEEDED if c not in (reader.fieldnames or [])]
            if missing:
                log.error("%s: missing expected columns %s", path.name, missing)
                return agg

            for row in reader:
                rows_read += 1
                origin = canonical_iata(row.get("Origin"))
                if not origin:
                    continue
                flight_date = (row.get("FlightDate") or "").strip()
                if len(flight_date) < 7:
                    continue
                month = flight_date[:7]  # 'YYYY-MM'

                key = (origin, month)
                rec = agg.get(key)
                if rec is None:
                    rec = agg[key] = _blank_row(origin, month)

                rec["flights"] += 1

                cancelled = to_float(row.get("Cancelled")) or 0.0
                if cancelled >= 1.0:
                    rec["cancelled"] += 1
                    # A cancelled flight has no taxi/delay observations; skip.
                    continue

                if (to_float(row.get("Diverted")) or 0.0) >= 1.0:
                    rec["diverted"] += 1

                d15 = to_float(row.get("DepDel15"))
                if d15 is not None:
                    rec["dep_del15_n"] += 1
                    if d15 >= 1.0:
                        rec["dep_del15"] += 1

                taxi = to_float(row.get("TaxiOut"))
                if taxi is not None:
                    rec["taxi_out_sum"] += taxi
                    rec["taxi_out_n"] += 1

                # NASDelay is populated only for delayed flights; a blank means
                # "no NAS delay attributed", which is a real zero for the
                # per-flight average. Count every non-cancelled flight in the
                # denominator so the metric is delay-minutes per flight.
                nas = to_float(row.get("NASDelay"))
                rec["nas_delay_sum"] += nas if nas is not None else 0.0
                rec["nas_delay_n"] += 1

                dep_delay = to_float(row.get("DepDelayMinutes"))
                if dep_delay is not None:
                    rec["dep_delay_sum"] += dep_delay
                    rec["dep_delay_n"] += 1

    log.info("%s: %d rows -> %d airport-months", path.name, rows_read, len(agg))
    return agg


def aggregate_window(otp_dir: Path | None = None) -> list[dict]:
    """Aggregate every cached OTP zip, keeping only the analysis window."""
    otp_dir = otp_dir or config.RAW_OTP_DIR
    zips = sorted(otp_dir.glob("otp_*.zip"))
    if not zips:
        log.warning("no OTP zips found in %s", otp_dir)
        return []

    merged: dict[tuple[str, str], dict] = {}
    for z in zips:
        for key, rec in aggregate_zip(z).items():
            if key[1] not in config.WINDOW_MONTHS:
                continue
            existing = merged.get(key)
            if existing is None:
                merged[key] = rec
            else:
                for field, value in rec.items():
                    if field in ("iata", "month"):
                        continue
                    existing[field] += value

    out = sorted(merged.values(), key=lambda r: (r["iata"], r["month"]))
    months = sorted({r["month"] for r in out})
    log.info(
        "aggregated %d airport-month delay rows over %d months (%s..%s)",
        len(out), len(months), months[0] if months else "-", months[-1] if months else "-",
    )
    return out


if __name__ == "__main__":
    rows = aggregate_window()
    for code in ("LAX", "SFO", "BOS", "SNA", "ANC"):
        sel = [r for r in rows if r["iata"] == code]
        if not sel:
            continue
        flights = sum(r["flights"] for r in sel)
        cancelled = sum(r["cancelled"] for r in sel)
        taxi_sum = sum(r["taxi_out_sum"] for r in sel)
        taxi_n = sum(r["taxi_out_n"] for r in sel)
        d15 = sum(r["dep_del15"] for r in sel)
        d15n = sum(r["dep_del15_n"] for r in sel)
        log.info(
            "%s months=%2d flights=%7d cxl=%.2f%% taxi_out=%.1f dep>15=%.1f%%",
            code, len(sel), flights,
            100 * cancelled / max(flights, 1),
            taxi_sum / max(taxi_n, 1),
            100 * d15 / max(d15n, 1),
        )
