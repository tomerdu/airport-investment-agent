"""Central configuration for the ETL pipeline.

Every tunable that affects a produced number lives here, so the analysis window
and source URLs are stated in exactly one place and can be echoed into
`source_registry` rather than retyped.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent

RAW_DIR = PROJECT_DIR / "data" / "raw"
RAW_OTP_DIR = RAW_DIR / "otp"
RAW_SEGMENT_DIR = RAW_DIR / "t100_segment"
RAW_REFERENCE_DIR = RAW_DIR / "reference"

WAREHOUSE_PATH = BACKEND_DIR / "app" / "data" / "warehouse.db"

for _d in (RAW_OTP_DIR, RAW_SEGMENT_DIR, RAW_REFERENCE_DIR, WAREHOUSE_PATH.parent):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# Analysis window  (approved decision D7)
# --------------------------------------------------------------------------
# Pinned to 12 months ending 2026-04 because T-100 (the traffic backbone) ends
# there. OTP runs to 2026-07 but we deliberately truncate it to the same window
# so no response ever mixes a July delay figure with an April traffic figure.

WINDOW_START = "2025-05"
WINDOW_END = "2026-04"
WINDOW_LABEL = f"{WINDOW_START}..{WINDOW_END}"


def window_months() -> list[str]:
    """The 12 'YYYY-MM' strings in the analysis window, inclusive."""
    y, m = (int(p) for p in WINDOW_START.split("-"))
    ey, em = (int(p) for p in WINDOW_END.split("-"))
    out: list[str] = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


WINDOW_MONTHS = window_months()

# Prior 12 months, used for year-over-year growth components.
PRIOR_WINDOW_START = "2024-05"
PRIOR_WINDOW_END = "2025-04"


def prior_window_months() -> list[str]:
    y, m = (int(p) for p in PRIOR_WINDOW_START.split("-"))
    ey, em = (int(p) for p in PRIOR_WINDOW_END.split("-"))
    out: list[str] = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


PRIOR_WINDOW_MONTHS = prior_window_months()

# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# faa.gov returns 403 to non-browser user agents. Verified 2026-09-27.

SOCRATA_DOMAIN = "datahub.transportation.gov"
SOCRATA_T100_ORIGIN_ID = "r495-tyji"  # AFF - T100 Segment Summary By Origin Airport
SOCRATA_T100_ORIGIN_URL = f"https://{SOCRATA_DOMAIN}/resource/{SOCRATA_T100_ORIGIN_ID}.json"
SOCRATA_T100_ORIGIN_META = f"https://{SOCRATA_DOMAIN}/api/views/{SOCRATA_T100_ORIGIN_ID}.json"
# Optional free app token raises Socrata's per-IP throttle. Never hard-coded.
SOCRATA_APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN", "").strip()

OTP_BASE_URL = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present"
)

T100_SEGMENT_FORM_URL = (
    "https://www.transtats.bts.gov/DL_SelectFields.aspx"
    "?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4%20Pn44vr45"
)  # FMG = T-100 Segment (All Carriers), includes international.

FAA_ENPLANEMENTS_BASE = (
    "https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger"
)
FAA_ENPLANEMENTS_FILES = {
    2025: f"{FAA_ENPLANEMENTS_BASE}/arp-cy2025-all-enplanements-preliminary.xlsx",
    2024: f"{FAA_ENPLANEMENTS_BASE}/arp-cy2024-all-enplanements.xlsx",
}
FAA_PRELIMINARY_YEARS = {2025}  # restated later by FAA; surfaced in the UI

OURAIRPORTS_AIRPORTS_URL = "https://ourairports.com/data/airports.csv"
OURAIRPORTS_RUNWAYS_URL = "https://ourairports.com/data/runways.csv"

# --------------------------------------------------------------------------
# Scoring-adjacent thresholds that the ETL must respect
# --------------------------------------------------------------------------

# ACI is suppressed below this many OTP flights in the window: delay rates
# computed on a few hundred observations are noise, and a confident score on
# noise is worse than no score.
MIN_OTP_FLIGHTS_FOR_ACI = 1000

LONG_HAUL_THRESHOLDS_SM = [1500, 2000, 2500, 3000, 6000]
LONG_HAUL_DEFAULT_SM = 3000  # approved decision D3

# --------------------------------------------------------------------------
# Airport universe (approved decision D8)
# --------------------------------------------------------------------------
# Defined authoritatively as FAA primary commercial service airports
# (service level 'P' in the FAA enplanement workbook), ~380 airports, rather
# than an arbitrary hand-picked list.

PRIMARY_SERVICE_LEVEL = "P"

# --------------------------------------------------------------------------
# Identifier drift: BTS code -> current FAA/OurAirports code
# --------------------------------------------------------------------------
# Airports occasionally get renamed and recoded, and the sources adopt the new
# code at different times. BTS (T-100, OTP) lags the FAA workbook, so a
# renamed airport appears under the old code in traffic data and the new code
# in the universe — and silently drops out of every ranking.
#
# Found in build validation 2026-09-27: Palm Beach International was recoded
# PBI -> DJT between FAA CY2024 and CY2025. BTS still reports PBI for all 24
# months (DJT appears zero times), so a MEDIUM HUB with 4.26M annual
# enplanements had no traffic or delay data at all.
#
# Keep this map small and explicit — never fuzzy-match airport names. The
# `material_dropped_traffic` validation check fails the build if any unmapped
# BTS code exceeds the materiality threshold, so future renames surface
# automatically instead of quietly deleting an airport.
BTS_IATA_ALIASES: dict[str, str] = {
    "PBI": "DJT",  # Palm Beach Intl -> President Donald J Trump Intl (FAA CY2025)
}

# A dropped BTS code carrying more than this many passengers in the window
# fails the build rather than warning.
MAX_UNMAPPED_DROPPED_PASSENGERS = 500_000

# Must be present in the final warehouse or the build fails loudly.
REQUIRED_AIRPORTS = ["LAX", "SNA", "SFO", "ANC", "BOS", "BDL", "PVD", "MHT", "PWM", "BTV"]

NEW_ENGLAND_STATES = ["MA", "CT", "RI", "NH", "ME", "VT"]
