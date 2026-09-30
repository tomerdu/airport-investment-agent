# Data Source Research — Airport Investment Intelligence Agent

**Date checked: 2026-09-27.** Every source below was probed live from this machine. Status labels mean:

- **VERIFIED** — I called it, got a 200, and inspected the actual payload/schema.
- **VERIFIED + COMPUTED** — I additionally ran a real calculation on the returned data and the number is in this doc.
- **UI ONLY** — the site works but I could not drive it programmatically in a reasonable time.
- **BROKEN** — the documented access path is currently failing on the provider's side.

Nothing in this document is an endpoint I assumed, remembered, or inferred. Where I failed, I say so.

---

## 1. Summary table

| # | Source | Type | Status | Freshness (as of 2026-09-27) | Auth | Use in MVP |
|---|--------|------|--------|------------------------------|------|------------|
| 1 | BTS T-100 Segment Summary **by Origin Airport** (Socrata `r495-tyji`) | Live JSON API | **VERIFIED + COMPUTED** | through **2026-04** | none | **Primary** — demand, seats, load factor, intl split |
| 2 | BTS On-Time Performance (TranStats PREZIP) | Bulk ZIP/CSV | **VERIFIED + COMPUTED** | through **2026-07** | none | **Primary** — airside congestion/delay |
| 3 | BTS T-100 **Segment (All Carriers)** (TranStats form) | Scripted POST → ZIP | **VERIFIED + COMPUTED** | through **2026** (2025-12 pulled) | none | **Primary** — per-segment distance ⇒ long-haul % |
| 4 | FAA Passenger Boarding (Enplanements) | XLSX | **VERIFIED** | **CY2025 preliminary** | none | **Primary** — enplanements, hub class, YoY |
| 5 | OurAirports airports/runways | Bulk CSV | **VERIFIED + COMPUTED** | continuous | none | **Primary** — runway count/length reference |
| 6 | BTS Consumer Airfare Report (Socrata) | Live JSON API | **VERIFIED** | updated **2026-09-03** | none | Secondary — fare premium (unmet-demand proxy) |
| 7 | FAA NAS Status (`nasstatus.faa.gov`) | Live XML | **VERIFIED** | real-time | none | Optional — live GDP/closure colour |
| 8 | OpenSky Network `/states/all` | Live JSON | **VERIFIED** | real-time | anon OK | **Not used** — see §3.7 |
| 9 | BTS International Passengers/Freight (`udzf-9fvh`) | Live JSON API | **VERIFIED** | updated **2026-08-07** | none | Secondary — intl market detail |
| 10 | FAA Airport Capacity Profiles | PDF | **VERIFIED (stale)** | **2014–2019** | none | Optional — curated constants only |
| 11 | FAA ATADS / OPSNET (tower ops counts) | Legacy ASP UI | **UI ONLY** | current | page loads anon | **Dropped** — see §3.10 |
| 12 | FAA ASPM | Web app | **Login required** | current | **MyAccess account** | **Dropped** |
| 13 | FAA Terminal Area Forecast (TAF) | Bulk download | **BROKEN** | n/a | none | **Dropped** — see §3.12 |

---

## 2. The sources we will actually build on

### 2.1 BTS T-100 Segment Summary by Origin Airport — `r495-tyji` ⭐ primary

- **Endpoint:** `https://datahub.transportation.gov/resource/r495-tyji.json` (Socrata SODA v2, full SoQL)
- **Metadata:** `https://datahub.transportation.gov/api/views/r495-tyji.json`
- **License:** `USGOV_WORKS` — *Public Domain U.S. Government* (confirmed in metadata). No attribution obligation, redistribution of a cached snapshot is fine.
- **Rows:** 131,739. **Coverage:** `2014-01-01` → `2026-04-01`, **monthly, per origin airport**.
- **Granularity:** airport × month, with a three-way split — **domestic / outbound international / inbound international**.
- **Rate limits:** unthrottled anonymously in testing; Socrata's documented practice is to rate-limit by IP and lift it with a free app token (`X-App-Token`). Add a token for the demo to be safe.

Key fields (display names confirmed from live metadata, because the API field names are badly truncated):

| API field | Means |
|---|---|
| `total_departures`, `total_passengers`, `total_seats` | totals |
| `total_load_factor` | load factor % |
| `total_seats_flight` | seats per flight (**gauge**) |
| `total_distance_flight_sm` | avg distance per flight (sm) |
| `domestic_*` | domestic subset (same shape) |
| `outbound_international` | outbound intl **departures** |
| `outbound_international_1` | outbound intl **passengers** |
| `outbound_international_3` | outbound intl avg **distance/flight** |
| `total_freight_lbs`, `total_mail_lbs` | cargo |

> **Trap:** `outbound_international_1/_2/_3/_4` are *not* sequential variants of one metric — they are passengers, pax/flight, distance/flight, distance/passenger. Anyone reading the field names alone will misread this. The ETL must map via the display names.

**Live result I ran (rolling 12 months, 2025-05 → 2026-04):**

| Airport | Departures | Passengers | Seats | Load factor | Intl dep share |
|---|---:|---:|---:|---:|---:|
| LAX | 270,853 | 36,590,656 | 44,584,901 | 82.1% | 23.5% |
| SFO | 190,280 | 26,642,605 | 32,273,396 | 82.6% | 20.7% |
| BOS | 189,745 | 20,983,745 | 25,675,310 | 81.7% | 14.5% |
| SNA | 51,609 | 5,590,354 | 6,950,118 | 80.4% | 2.3% |
| BDL | 30,929 | 3,251,263 | 3,996,642 | 81.3% | 1.4% |
| ANC | 86,373 | 2,725,281 | 3,745,847 | 72.8% | 15.4% |
| PVD | 22,002 | 2,095,785 | 2,696,428 | 77.7% | 0.1% |
| PWM | 14,841 | 1,277,351 | 1,543,604 | 82.8% | 0.0% |
| BTV | 9,847 | 702,627 | 846,436 | 83.0% | 0.1% |
| MHT | 10,633 | 683,107 | 905,020 | 75.5% | 0.0% |

Note ANC: **86,373 departures but only 2.7M passengers** — roughly BOS-level movements at one-eighth the passengers. That is the cargo hub showing through, and it is the single most important contextual fact for the Anchorage question.

### 2.2 BTS On-Time Performance — TranStats PREZIP ⭐ primary

- **Directory:** `https://transtats.bts.gov/PREZIP/` (946 files, IIS listing, parseable)
- **File pattern:** `On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{YYYY}_{M}.zip`
- **Latest:** `..._2026_7.zip`, 31.5 MB, published **2026-09-21** ⇒ **~2 month reporting lag**
- **Verified payload:** 1 CSV (273 MB uncompressed, **631,970 rows** for July 2026) + `readme.html`. 110 columns; I confirmed the full header and parsed real rows.

Columns that matter: `Origin`, `Dest`, `FlightDate`, `DepDelayMinutes`, `DepDel15`, `TaxiOut`, `TaxiIn`, `Cancelled`, `CancellationCode`, `Diverted`, `NASDelay`, `WeatherDelay`, `CarrierDelay`, `LateAircraftDelay`, `Distance`, `Flights`.

**Live result I computed** (July 2026, departures by origin):

| Airport | Flights | Cancel % | Dep>15min % | Avg taxi-out (min) | Avg dep delay (min) | NAS delay/flight |
|---|---:|---:|---:|---:|---:|---:|
| LAX | 17,454 | 1.20 | 25.5 | 18.8 | 18.8 | 4.1 |
| SFO | 13,841 | 1.15 | **35.5** | **25.0** | 24.8 | 5.3 |
| BOS | 13,277 | 5.92 | 30.5 | 22.4 | 26.3 | 6.3 |
| SNA | 3,992 | 1.13 | 26.0 | 16.8 | 20.2 | 4.2 |
| ANC | 2,513 | 0.72 | 19.6 | 13.0 | 12.9 | 0.9 |
| BDL | 1,827 | 3.67 | 25.9 | 16.7 | 25.8 | 7.8 |
| PVD | 1,361 | 6.47 | 27.4 | 16.0 | 27.3 | 5.0 |
| PWM | 1,251 | 5.68 | 26.8 | 16.0 | 28.0 | 9.0 |
| BTV | 594 | 4.71 | 30.6 | 19.1 | 31.8 | 5.0 |
| MHT | 529 | 3.78 | 25.7 | 15.7 | 28.9 | 5.4 |

**Coverage limits (important, and they bite):**
- **Domestic only.** No international legs at all.
- **Reporting carriers only** — carriers at/above the DOT revenue threshold. Regional flying appears only when the reporting carrier files it.
- **No all-cargo carriers.** This is why ANC shows 2,513 flights here but 6,872 departures/month in T-100 Segment: OTP simply cannot see FedEx/UPS/Atlas/Kalitta.

⚠️ **Measured throughput problem.** Two independent samples on 2026-09-27: the 31.5 MB July file took **453 s (71 KB/s)**; the 30.1 MB June file took **313 s (99 KB/s)**. So ~**70–100 KB/s sustained**, i.e. 5–7.5 min per month-file and **60–90 min for a 12-month pull**. This is a stable property of the BTS host, not a transient — it is a real schedule constraint, and it is why the ETL is offline and the warehouse is committed.

### 2.3 BTS T-100 Segment (All Carriers) — scripted download ⭐ primary for long-haul

This is the only verified public path to **per-segment distance**, which is the only way to honestly answer "% of long-haul flights."

- **Form:** `https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4%20Pn44vr45`
- **Mechanism:** ASP.NET WebForms. GET to harvest `__VIEWSTATE` / `__VIEWSTATEGENERATOR` / `__EVENTVALIDATION`, then POST with `cboYear`, `cboPeriod`, `cboGeography=All`, one `on` per desired column, `chkDownloadZip=on`, `btnDownload=Download`.
- **I executed this end to end.** Response: `HTTP 200`, `Content-Type: application/zip`, 462.8 KB, magic bytes `PK`. Contents: `T_T100_SEGMENT_ALL_CARRIER.csv` (2.4 MB, **49,291 rows** for 2025-12) + `Documentation.csv`.
- **Coverage:** year dropdown offers **1990–2026**; geography `All` includes international segments.
- Selectable columns confirmed present: `DEPARTURES_PERFORMED`, `DEPARTURES_SCHEDULED`, `SEATS`, `PASSENGERS`, `DISTANCE`, `ORIGIN`, `DEST`, `ORIGIN_COUNTRY`, `DEST_COUNTRY`, `AIR_TIME`, `RAMP_TO_RAMP`, `CARRIER_GROUP`, `FREIGHT`, `MAIL`, `YEAR`, `MONTH`.

**Live result — ANC, December 2025, all carriers (463 segments, 6,872 departures performed):**

| Long-haul threshold | Departures | Share |
|---|---:|---:|
| ≥ 1,500 sm | 3,620 | **52.7%** |
| ≥ 2,000 sm | 3,477 | **50.6%** |
| ≥ 2,500 sm | 3,213 | **46.8%** |
| ≥ 3,000 sm | 2,355 | **34.3%** |

Top ANC destinations: ENA (818 dep, 59 sm), ORD (674, 2,846), SEA (542, 1,448), SDF (265, 3,122), FAI (256, 261), MIA (253, 4,004), JFK (228, 3,386), BET (223, 399), **HKG (177, 5,081)**, **ICN (176, 3,799)**.

Two things fall out of this and both belong in the product:
1. **The answer moves 18 points (52.7% → 34.3%) purely on threshold choice.** Any agent that returns a single unqualified percentage here is bluffing. Ours must state the definition and show the sensitivity.
2. ANC's profile is bimodal — bush flying to ENA/FAI/BET at one end, transpacific freight to HKG/ICN/SDF at the other. A mean distance would describe neither.

**Risk:** this is screen-scraping a legacy form. `__VIEWSTATE` handling is brittle and the form can change without notice. Mitigation: cache the pulled CSVs in-repo (public domain), never fetch at request time.

### 2.4 FAA Passenger Boarding (Enplanements) ⭐ primary

- **Index:** `https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger`
- **CY2025 preliminary:** `.../arp-cy2025-all-enplanements-preliminary.xlsx` (148.4 KB, downloaded and opened)
- **CY2024 final:** `.../arp-cy2024-all-enplanements.xlsx`
- **Verified columns:** `Rank, RO, ST, Locid, City, Airport Name, S/L, Hub, CY 25 Enplanements, CY 24 Enplanements, % Change`
- `Hub` ∈ {L, M, S, N} = Large/Medium/Small/Nonhub. `S/L` = service level (P = primary).
- ⚠️ Requires a **browser User-Agent** — faa.gov returns **403** to default clients. (WebFetch failed; `curl`-style with a UA header succeeded.)
- ⚠️ CY2025 is **preliminary** and will be restated. Label it as such in the UI.

This gives the official passenger denominator and the FAA's own hub classification — much better peer-cohort logic than inventing size bands.

### 2.5 OurAirports reference data ⭐ primary

- `https://ourairports.com/data/airports.csv` (12.7 MB, **86,134 rows**)
- `https://ourairports.com/data/runways.csv` (4.0 MB, **48,278 rows**)
- License: public domain dedication by the project. Fields include `length_ft`, `surface`, `closed`, `lighted`, plus IATA/ICAO mapping.

**Live result:**

| ICAO | Airport | Runways | Longest |
|---|---|---:|---:|
| KLAX | Los Angeles Intl | 4 | 12,894 ft |
| KSFO | San Francisco Intl | 4 | 11,870 ft |
| KBOS | Boston Logan Intl | 6 | 10,083 ft |
| **KSNA** | **John Wayne / Orange County** | **2** | **5,700 ft** |
| KBDL | Bradley Intl | 2 | 9,510 ft |
| KPVD | Rhode Island T.F. Green | 2 | 8,700 ft |
| KMHT | Manchester-Boston Regional | 2 | 9,250 ft |
| KPWM | Portland Intl Jetport | 2 | 7,200 ft |
| KBTV | Patrick Leahy Burlington Intl | 2 | 8,319 ft |
| PANC | Ted Stevens Anchorage Intl | 3 | 12,400 ft |

SNA's **5,700 ft** longest runway is decisive for the LAX/SNA question: SNA is physically incapable of most widebody and long-haul operation regardless of demand or terminal size. Its constraint is not congestion — it is airframe/runway geometry (plus the well-known noise curfew and access-agreement caps, which are *not* in any of these datasets and must be flagged as outside-data context).

### 2.6 BTS Consumer Airfare Report — Socrata (secondary)

- `tfrh-tu9e` Table 1a — All U.S. Airport Pair Markets. Updated **2026-09-03**. License `USGOV_WORKS`.
- Fields: `year, quarter, airport_1, airport_2, nsmiles, passengers, fare, carrier_lg, large_ms, fare_lg, carrier_low, lf_ms, fare_low`.
- Also live: `wqw2-rjgd` (Top 1,000 city pairs), `yj5y-b2ir` (Table 6), `d6nc-s8v6` (Table 7 — fare premiums), `bkh6-tj42` (Table 5).
- **Why it matters:** a sustained *fare premium* on routes from a constrained airport is one of the few observable market signals consistent with suppressed supply. It is corroborating evidence for unmet demand — never proof.

### 2.7 FAA NAS Status (optional, live)

- `https://nasstatus.faa.gov/api/airport-status-information` — XML, no auth, ~1.2 KB, real-time.
- Live sample captured at `Sun Sep 27 11:13:40 2026 GMT`: a Ground Delay Program at **LGA** (low visibility, avg 51 min, max 3 h), plus GA-access closure NOTAMs at LAX, SAN, BOS.
- **Honest assessment:** this is a *current-conditions* feed, not a statistic. It shows programs active right now, has no history, and the "Airport Closures" entries above are GA-access NOTAMs, not real closures — trivially misreadable as "LAX is closed." Use it only as a clearly-labelled live sidebar, never as a scoring input.

---

## 3. Sources evaluated and rejected (and why)

### 3.7 OpenSky Network — rejected for scoring
Works anonymously (`HTTP 200` in 0.3 s, `X-Rate-Limit-Remaining: 398`). But it is live ADS-B state vectors: no schedule, no seats, no passengers, coverage gaps from receiver density, and no history at this tier. Building capacity statistics on it would mean inventing a sampling frame. Good for a live map; wrong for investment analytics. **Not used.**

### 3.10 FAA ATADS / OPSNET — attempted, dropped
`https://aspm.faa.gov/opsnet/sys/Airport.asp` returns **200 without login** and is the canonical source for *tower operations counts* (the true airside denominator). I tried to drive its backend (`opsnet-server-x.asp`) with a realistic POST and got **`HTTP 200`, zero-length body** — the parameter contract is not what the visible form implies. Reverse-engineering it is an open-ended timebox risk for a 24-hour build. **Dropped.** We substitute T-100 departures ÷ runway count as the airside-intensity denominator and label it as a substitute.

### 3.12 FAA Terminal Area Forecast — BROKEN on FAA's side
`https://taf.faa.gov/Downloads/TAFDownload.aspx` returns a 106-byte page reading, verbatim:

> "We are investigating issues with TAF and will provide updates here as they become available."

The main TAF site loads but the bulk download is out of service as of **2026-09-27**. This removes the obvious source of *forward-looking* enplanement/operations forecasts. **Consequence: the agent must not make forecasts.** We measure trailing growth from T-100 and FAA enplanements and say so. This is the single biggest scope reduction forced by data reality.

### 3.11 FAA ASPM — login wall
`https://aspm.faa.gov/apm/sys/AnalysisCP.asp` redirect-loops to MyAccess auth. ASPM has the best delay/capacity metrics in existence (including hourly called rates) but is not publicly scriptable. **Dropped.**

### 3.10b FAA Airport Capacity Profiles — real but stale
Per-airport PDFs at `https://www.faa.gov/airports/planning_capacity/profiles`, containing declared hourly capacity rates. But the files are dated **2014–2019** (ATL 2018, BOS 2019, DEN 2014, JFK 2014, LAX/SFO similar vintage) and it is a PDF set, not a feed. **Optional use only:** if we want a true "operations vs. declared capacity" ratio we must hand-curate ~30 constants and stamp them with their 2014–2019 vintage. Recommended as a stretch, not MVP.

### Dead ends worth recording
- `api.aviationapi.com` — **DNS does not resolve.** (Frequently cited in blog posts; do not trust it.)
- `catalog.data.gov/api/3/action/package_search` — **404**. The CKAN action API is not serving that path.
- `data.transportation.gov` catalog search — federated and noise-dominated; searching "T-100" returned Colombian geology datasets. **Query `datahub.transportation.gov` directly with `search_context`.**
- `transtats.bts.gov/DownLoad_Table.asp?Table_ID=...` — **404**. The old integer-table-ID download endpoint is gone; only the `gnoyr_VQ` form path works.
- `nasr.faa.gov` — **DNS does not resolve.**
- PREZIP hosts **only** On-Time Performance and the DB1B O&D survey under stable current names. There is **no** stable pre-zipped T-100 URL; the 2015-vintage `896816367_T_T100_*` files are frozen artifacts, not a live feed.

---

## 4. Cross-cutting notes

**Licensing.** Every primary source is U.S. federal public domain (`USGOV_WORKS`) except OurAirports (explicit public-domain dedication). We may cache, redistribute, and ship a snapshot inside the repo. This makes the offline fallback legally clean.

**Freshness ladder** (as of 2026-09-27) — worth surfacing in the UI, since the sources disagree on recency:
- On-Time Performance → **2026-07** (~2 mo lag)
- T-100 by origin airport → **2026-04** (~5 mo lag)
- FAA enplanements → **CY2025 preliminary**
- Consumer Airfare → updated 2026-09-03 (quarterly data, longer lag)

Because the two primary sources have different cutoffs, **any combined score must state one common analysis window** rather than silently mixing a July figure with an April figure.

**The identifier problem.** OTP uses IATA-style `Origin` codes; T-100 uses `ORIGIN` plus `ORIGIN_AIRPORT_ID`; FAA enplanements use `Locid`; OurAirports uses ICAO `ident` plus `iata_code`. `Locid` and IATA diverge for some airports, and ICAO adds the K/P prefix (PANC, not KANC — Alaska). A single crosswalk table built from OurAirports, with ANC/HNL/PR cases tested explicitly, is a genuine prerequisite and not a formality.

**Retrieval provenance.** Every cached table should be written with `source_url`, `retrieved_at`, and `coverage_start/coverage_end`, so citations are generated from data rather than hand-written.
