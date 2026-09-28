"""Shared ETL plumbing: HTTP, logging, and source provenance."""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from . import config

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)-22s %(message)s",
    datefmt="%H:%M:%S",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


log = get_logger("etl.common")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def make_session() -> requests.Session:
    """Session with a browser UA (faa.gov 403s otherwise) and retries."""
    s = requests.Session()
    s.headers.update({"User-Agent": config.USER_AGENT})
    adapter = requests.adapters.HTTPAdapter(
        max_retries=requests.adapters.Retry(
            total=4,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def download_file(
    session: requests.Session,
    url: str,
    dest: Path,
    *,
    force: bool = False,
    min_bytes: int = 1024,
) -> Path:
    """Download `url` to `dest`, skipping if already present.

    Writes to a `.part` file and moves on success, so an interrupted run never
    leaves a truncated file that a later run would mistake for complete.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force and dest.stat().st_size >= min_bytes:
        log.info("skip (cached): %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
        return dest

    tmp = dest.with_suffix(dest.suffix + ".part")
    t0 = time.time()
    with session.get(url, stream=True, timeout=1800) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    fh.write(chunk)
    tmp.replace(dest)
    mb = dest.stat().st_size / 1e6
    dt = max(time.time() - t0, 0.001)
    log.info("downloaded %s  %.1f MB in %.0fs (%.0f KB/s)", dest.name, mb, dt, mb * 1000 / dt)
    return dest


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Source registry
# --------------------------------------------------------------------------
# Every table records where it came from, when it was pulled, what period it
# covers and its licence. Citations and the UI freshness panel read from here,
# so provenance is generated from data instead of hand-maintained strings.


def register_source(
    conn: sqlite3.Connection,
    *,
    dataset: str,
    source_name: str,
    source_url: str,
    license_name: str,
    coverage_start: str | None,
    coverage_end: str | None,
    row_count: int,
    notes: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO source_registry
            (dataset, source_name, source_url, license, coverage_start,
             coverage_end, retrieved_at, row_count, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dataset) DO UPDATE SET
            source_name   = excluded.source_name,
            source_url    = excluded.source_url,
            license       = excluded.license,
            coverage_start= excluded.coverage_start,
            coverage_end  = excluded.coverage_end,
            retrieved_at  = excluded.retrieved_at,
            row_count     = excluded.row_count,
            notes         = excluded.notes
        """,
        (
            dataset,
            source_name,
            source_url,
            license_name,
            coverage_start,
            coverage_end,
            utc_now_iso(),
            row_count,
            notes,
        ),
    )
    conn.commit()


# --------------------------------------------------------------------------
# SQLite helpers
# --------------------------------------------------------------------------


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.WAREHOUSE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def canonical_iata(code: str | None) -> str | None:
    """Map a BTS airport code onto the warehouse's canonical IATA code.

    Applied to every BTS-sourced code (T-100 summary, T-100 segment, OTP) so a
    source that still uses a superseded code joins correctly instead of being
    dropped as unknown. See `config.BTS_IATA_ALIASES`.
    """
    if not code:
        return None
    c = code.strip().upper()
    if len(c) != 3:
        return None
    return config.BTS_IATA_ALIASES.get(c, c)


def to_float(value: Any) -> float | None:
    """Parse a value to float, returning None rather than guessing.

    The ETL never substitutes 0.0 for a missing measurement: downstream the
    difference between 'zero departures' and 'not reported' changes the score.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if s == "" or s.upper() in {"NA", "N/A", "NULL", "-", "--"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    f = to_float(value)
    return None if f is None else int(round(f))
