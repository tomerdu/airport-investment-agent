# Data Pipeline

Builds `backend/app/data/warehouse.db` — a SQLite warehouse of US airport
traffic, delay and reference data for the analysis window **2025-05 … 2026-04**.

## Why the ETL is offline

No external call happens on the request path. Measured BTS throughput is
**70–100 KB/s**, so a 12-month On-Time Performance pull takes 60–90 minutes,
and the T-100 Segment source is a scraped ASP.NET form that can break at any
time. The warehouse is therefore built ahead of time and committed. Every
primary source is U.S. federal public domain or an explicit public-domain
dedication, so redistributing a cached snapshot is clean.

This also *is* the offline fallback: if every upstream source is down, the
application is unaffected.

## Running it

```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt

# Optional: start the slow OTP download first; everything else is fast.
powershell -File etl/prefetch_otp.ps1

python -m etl.build_warehouse            # uses caches where present
python -m etl.build_warehouse --force-fetch   # ignore caches, re-download
python -m pytest                          # unit + warehouse tests
```

Each fetcher is also runnable standalone for debugging, e.g.
`python -m etl.fetch_t100_socrata`.

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | Analysis window, source URLs, thresholds. Single source of truth. |
| `common.py` | HTTP session, caching download, source registry, safe numeric parsing. |
| `fetch_reference.py` | OurAirports airports + runways (identifier + runway inventory). |
| `fetch_faa_enplanements.py` | FAA passenger boarding workbooks; defines the airport universe. |
| `crosswalk.py` | IATA ↔ ICAO ↔ FAA Locid join. |
| `fetch_t100_socrata.py` | BTS T-100 by origin airport (traffic backbone). |
| `fetch_t100_segment.py` | BTS T-100 Segment (per-O&D distance → long-haul). |
| `parse_otp.py` | Streams OTP zips into airport-month delay aggregates. |
| `build_warehouse.py` | Orchestrates the load and runs data validation. |
| `prefetch_otp.ps1` | Toolchain-free parallel OTP downloader. |

## Sources

| Dataset | Source | Access | Coverage |
|---|---|---|---|
| `airport_month` | BTS T-100 Segment Summary by Origin Airport (Socrata `r495-tyji`) | JSON API | 2025-05…2026-04 |
| `airport_delay_month` | BTS On-Time Performance | Bulk ZIP | 2025-05…2026-04 |
| `segments` | BTS T-100 Segment (All Carriers) | Scraped form → ZIP | 2025-05…2026-04 |
| `enplanements` | FAA Passenger Boarding | XLSX | CY2024 final, CY2025 preliminary |
| `airports` | OurAirports + FAA | Bulk CSV + XLSX | current |

## Traps this pipeline defuses

These are real failure modes found during development, not hypotheticals.
Each has a regression test.

1. **US territories are not `iso_country == 'US'`.** OurAirports codes Puerto
   Rico as `PR`, Guam as `GU`, USVI as `VI`, and so on. Filtering on `'US'`
   alone silently dropped all 12 FAA-primary territory airports — including
   **SJU at ~6.7M annual enplanements** — with no error raised anywhere.
   Caught by the crosswalk validation, now fixed in `US_ISO_COUNTRIES`.

2. **Truncated Socrata field names.** In `r495-tyji`,
   `outbound_international_1/_2/_3/_4` are *passengers*, *pax/flight*,
   *distance/flight* and *distance/passenger* — not variants of one metric.
   Reading them positionally produces wrong numbers with no error. We resolve
   every field through the dataset's **display names**, fetched live, and
   assert the mapping before loading.

3. **Averaging monthly averages.** `airport_delay_month` stores sums and
   counts, never means. Averaging monthly means would weight BTV's 594-flight
   month equally with LAX's 17,454-flight month —
   `test_otp_windowed_average_differs_from_mean_of_monthly_means` shows the
   error is ~10 minutes wide on taxi-out.

4. **ICAO prefixes diverge outside the lower 48.** PANC not KANC, PHNL not
   KHNL, TJSJ not KSJU. A wrong prefix attaches another airport's runway
   inventory. Asserted explicitly.

5. **Missing is not zero.** `to_float` returns `None` for blanks and sentinel
   strings; nothing is imputed anywhere in the pipeline.

6. **faa.gov returns 403** to non-browser user agents.

7. **Mixed analysis windows.** OTP publishes two months ahead of T-100, so it
   is deliberately truncated to the T-100 window. Nothing mixes a July delay
   figure with an April traffic figure.

## Validation

`build_warehouse.py` runs 14 checks and writes results to `etl_validation`,
so the app and the checkpoint report read what was actually verified rather
than a claim. A `FAIL` exits non-zero.

The strongest check is `t100_regression_vs_phase1`: five airports' departures,
passengers and seats must match values computed directly from the live API
during research, *before any ETL code existed*. A mismatch means the pipeline
is wrong, not the baseline.
