-- Airport Investment Intelligence Agent — warehouse schema.
--
-- Design notes that matter downstream:
--   * airport_delay_month stores SUMS and COUNTS, never averages. Any window
--     must be re-aggregated as sum/count; averaging monthly averages would
--     weight a 594-flight month at BTV the same as a 17,454-flight month at
--     LAX. This is the single easiest way to produce subtly wrong numbers.
--   * Measurements are nullable. NULL means "not reported"; it is never
--     coerced to 0, because zero departures and unreported departures are
--     different facts.
--   * source_registry carries provenance for every table so citations and
--     freshness are generated from data, not hard-coded.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Reference: the airport universe and its identifier crosswalk
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS airports (
    iata                TEXT PRIMARY KEY,        -- BTS/T-100/OTP code
    icao                TEXT,                    -- OurAirports ident (PANC, not KANC)
    faa_locid           TEXT,                    -- FAA enplanement workbook Locid
    name                TEXT NOT NULL,
    city                TEXT,
    state               TEXT,                    -- 2-letter, US
    region              TEXT,                    -- derived grouping, e.g. 'new_england'
    hub_class           TEXT,                    -- FAA: L / M / S / N
    service_level       TEXT,                    -- FAA: P = primary
    lat                 REAL,
    lon                 REAL,
    runway_count        INTEGER,                 -- open runways (OurAirports)
    longest_runway_ft   INTEGER,
    in_universe         INTEGER NOT NULL DEFAULT 0  -- 1 = FAA primary commercial service
);

CREATE INDEX IF NOT EXISTS idx_airports_state   ON airports(state);
CREATE INDEX IF NOT EXISTS idx_airports_region  ON airports(region);
CREATE INDEX IF NOT EXISTS idx_airports_hub     ON airports(hub_class);
CREATE INDEX IF NOT EXISTS idx_airports_universe ON airports(in_universe);

-- ---------------------------------------------------------------------------
-- Traffic: BTS T-100 Segment Summary by Origin Airport (Socrata r495-tyji)
-- ---------------------------------------------------------------------------
-- Monthly, per origin airport, split domestic / outbound intl / inbound intl.
CREATE TABLE IF NOT EXISTS airport_month (
    iata                    TEXT NOT NULL,
    month                   TEXT NOT NULL,       -- 'YYYY-MM'
    departures              REAL,
    passengers              REAL,
    seats                   REAL,
    freight_lbs             REAL,
    mail_lbs                REAL,
    dom_departures          REAL,
    dom_passengers          REAL,
    dom_seats               REAL,
    intl_out_departures     REAL,
    intl_out_passengers     REAL,
    intl_out_seats          REAL,
    avg_distance_sm         REAL,                -- as reported (avg per flight)
    PRIMARY KEY (iata, month)
);

CREATE INDEX IF NOT EXISTS idx_airport_month_month ON airport_month(month);

-- ---------------------------------------------------------------------------
-- Delay: BTS On-Time Performance, aggregated by origin airport and month
-- ---------------------------------------------------------------------------
-- Domestic, reporting carriers only. Excludes all-cargo carriers entirely:
-- at ANC this sees ~2.5k flights/month where T-100 sees ~6.9k departures.
CREATE TABLE IF NOT EXISTS airport_delay_month (
    iata                TEXT NOT NULL,
    month               TEXT NOT NULL,           -- 'YYYY-MM'
    flights             INTEGER NOT NULL,        -- rows incl. cancelled
    cancelled           INTEGER NOT NULL,
    diverted            INTEGER NOT NULL,
    dep_del15           INTEGER NOT NULL,        -- count with DepDel15 = 1
    dep_del15_n         INTEGER NOT NULL,        -- flights where DepDel15 reported
    taxi_out_sum        REAL NOT NULL,
    taxi_out_n          INTEGER NOT NULL,
    nas_delay_sum       REAL NOT NULL,
    nas_delay_n         INTEGER NOT NULL,
    dep_delay_sum       REAL NOT NULL,           -- DepDelayMinutes
    dep_delay_n         INTEGER NOT NULL,
    PRIMARY KEY (iata, month)
);

CREATE INDEX IF NOT EXISTS idx_delay_month ON airport_delay_month(month);

-- ---------------------------------------------------------------------------
-- Segments: BTS T-100 Segment (All Carriers) — per O&D, carries DISTANCE
-- ---------------------------------------------------------------------------
-- The only verified public source with per-segment distance, which is what
-- makes an honest long-haul share possible. Includes all-cargo carriers.
-- aircraft_config is the cargo/passenger discriminator. Verified empirically
-- against 12 months of ANC data (2025-05..2026-04):
--     '1' passenger config  -> 38,407 dep, 2,727,685 passengers
--     '2' all-cargo config  -> 47,915 dep,         1 passenger
--     '3' seaplane          ->    888 dep,     3,008 passengers
-- i.e. 54.9% of ANC departures are freighters. service_class (F/G/L/P) is
-- retained as a secondary cut: F/L carry passengers, G/P are all-cargo.
CREATE TABLE IF NOT EXISTS segments (
    origin                  TEXT NOT NULL,
    dest                    TEXT NOT NULL,
    month                   TEXT NOT NULL,       -- 'YYYY-MM'
    carrier_group           TEXT,                -- T-100 CARRIER_GROUP_NEW
    aircraft_config         TEXT,                -- '1' pax, '2' cargo, '3' seaplane, '4' helo
    service_class           TEXT,                -- T-100 CLASS: F/G/L/P
    origin_country          TEXT,
    dest_country            TEXT,
    distance_sm             REAL,
    departures_performed    REAL,
    seats                   REAL,
    passengers              REAL
);

CREATE INDEX IF NOT EXISTS idx_segments_config ON segments(origin, aircraft_config);

CREATE INDEX IF NOT EXISTS idx_segments_origin ON segments(origin, month);
CREATE INDEX IF NOT EXISTS idx_segments_dist   ON segments(origin, distance_sm);

-- ---------------------------------------------------------------------------
-- Enplanements: FAA passenger boarding workbooks
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS enplanements (
    faa_locid       TEXT NOT NULL,
    cy              INTEGER NOT NULL,
    enplanements    REAL,
    prior_year      REAL,
    pct_change      REAL,
    hub_class       TEXT,
    service_level   TEXT,
    rank            INTEGER,
    preliminary     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (faa_locid, cy)
);

-- ---------------------------------------------------------------------------
-- Provenance
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_registry (
    dataset         TEXT PRIMARY KEY,
    source_name     TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    license         TEXT,
    coverage_start  TEXT,
    coverage_end    TEXT,
    retrieved_at    TEXT NOT NULL,
    row_count       INTEGER,
    notes           TEXT
);

-- Build-time data quality assertions, kept so the checkpoint report and the
-- app can both show what was validated rather than asserting it was.
CREATE TABLE IF NOT EXISTS etl_validation (
    check_name  TEXT PRIMARY KEY,
    status      TEXT NOT NULL,      -- PASS / WARN / FAIL
    detail      TEXT,
    checked_at  TEXT NOT NULL
);
