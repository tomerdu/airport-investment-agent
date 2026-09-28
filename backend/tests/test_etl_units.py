"""Unit tests for ETL primitives — no network, no warehouse required."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from etl import config, crosswalk, parse_otp
from etl.common import to_float, to_int


# ---------------------------------------------------------------------------
# Value parsing — the "never fabricate" contract starts here
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1234", 1234.0),
        ("1,234.5", 1234.5),
        (" 42 ", 42.0),
        (7, 7.0),
        (3.5, 3.5),
    ],
)
def test_to_float_parses_real_values(raw, expected):
    assert to_float(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", None, "NA", "N/A", "NULL", "-", "--", "abc"])
def test_to_float_returns_none_not_zero_for_missing(raw):
    """Missing must be None, never 0.0.

    'zero departures' and 'not reported' are different facts; collapsing them
    would let a gap masquerade as a measurement.
    """
    assert to_float(raw) is None


def test_to_int_preserves_none():
    assert to_int("") is None
    assert to_int("12.6") == 13


# ---------------------------------------------------------------------------
# Analysis window (approved decision D7)
# ---------------------------------------------------------------------------


def test_window_is_twelve_months_ending_april_2026():
    months = config.WINDOW_MONTHS
    assert len(months) == 12
    assert months[0] == "2025-05"
    assert months[-1] == "2026-04"


def test_window_crosses_year_boundary_correctly():
    assert "2025-12" in config.WINDOW_MONTHS
    assert "2026-01" in config.WINDOW_MONTHS


def test_prior_window_is_the_preceding_twelve_months():
    prior = config.PRIOR_WINDOW_MONTHS
    assert len(prior) == 12
    assert prior[0] == "2024-05"
    assert prior[-1] == "2025-04"
    assert not set(prior) & set(config.WINDOW_MONTHS)


def test_long_haul_default_is_3000_sm():
    assert config.LONG_HAUL_DEFAULT_SM == 3000
    assert 3000 in config.LONG_HAUL_THRESHOLDS_SM


# ---------------------------------------------------------------------------
# Crosswalk
# ---------------------------------------------------------------------------


def _oa(ident, iata, local, state="AK", name="X"):
    return {
        "ident": ident, "icao": ident, "iata": iata, "local_code": local,
        "name": name, "city": "C", "state": state, "lat": 1.0, "lon": 2.0, "type": "large_airport",
    }


def test_crosswalk_matches_on_local_code():
    faa = [{"faa_locid": "ANC", "name": "Ted Stevens", "city": "Anchorage",
            "state": "AK", "hub_class": "M", "service_level": "P"}]
    oa = {"PANC": _oa("PANC", "ANC", "ANC")}
    rows, report = crosswalk.build(faa, oa, {"PANC": {"runway_count": 3, "longest_runway_ft": 12400}})
    assert len(rows) == 1
    assert rows[0]["iata"] == "ANC"
    assert rows[0]["icao"] == "PANC"       # NOT KANC — the Alaska prefix trap
    assert rows[0]["runway_count"] == 3
    assert report["matched_by_local_code"] == 1


def test_crosswalk_falls_back_to_iata_when_local_code_absent():
    faa = [{"faa_locid": "XYZ", "name": "N", "city": "C", "state": "TX",
            "hub_class": "N", "service_level": "P"}]
    oa = {"KXYZ": _oa("KXYZ", "XYZ", None, state="TX")}
    rows, report = crosswalk.build(faa, oa, {})
    assert len(rows) == 1
    assert report["matched_by_iata_code"] == 1


def test_crosswalk_reports_unmatched_rather_than_guessing():
    faa = [{"faa_locid": "ZZZ", "name": "Nowhere", "city": "C", "state": "TX",
            "hub_class": "N", "service_level": "P"}]
    rows, report = crosswalk.build(faa, {}, {})
    assert rows == []
    assert report["unmatched"] == 1
    assert "ZZZ" in report["unmatched_locids"]


def test_crosswalk_marks_only_primary_airports_in_universe():
    faa = [
        {"faa_locid": "AAA", "name": "A", "city": "C", "state": "MA", "hub_class": "S", "service_level": "P"},
        {"faa_locid": "BBB", "name": "B", "city": "C", "state": "MA", "hub_class": "N", "service_level": "CS"},
    ]
    oa = {"KAAA": _oa("KAAA", "AAA", "AAA", state="MA"), "KBBB": _oa("KBBB", "BBB", "BBB", state="MA")}
    rows, _ = crosswalk.build(faa, oa, {})
    universe = {r["iata"]: r["in_universe"] for r in rows}
    assert universe["AAA"] == 1
    assert universe["BBB"] == 0


def test_crosswalk_deduplicates_conflicting_iata():
    """Two FAA rows resolving to one IATA must not silently overwrite."""
    faa = [
        {"faa_locid": "AAA", "name": "First", "city": "C", "state": "MA", "hub_class": "S", "service_level": "P"},
        {"faa_locid": "A1A", "name": "Second", "city": "C", "state": "MA", "hub_class": "S", "service_level": "P"},
    ]
    oa = {"KAAA": _oa("KAAA", "AAA", "AAA", state="MA")}
    oa["KAAA"]["local_code"] = "AAA"
    # Both FAA rows will resolve via iata fallback to the same OurAirports record.
    oa_by_iata = dict(oa)
    oa_by_iata["KA1A"] = _oa("KAAA", "AAA", "A1A", state="MA")
    rows, _ = crosswalk.build(faa, oa_by_iata, {})
    assert len({r["iata"] for r in rows}) == len(rows)


def test_us_territories_are_in_scope():
    """Regression guard for a real bug caught by build validation.

    OurAirports assigns US territories their own ISO country codes (PR, VI,
    GU, MP, AS), not 'US'. Filtering on iso_country == 'US' alone silently
    dropped all 12 FAA-primary territory airports — including SJU at ~6.7M
    annual enplanements — with no error anywhere.
    """
    from etl.fetch_reference import US_ISO_COUNTRIES

    for code in ("US", "PR", "VI", "GU", "MP", "AS"):
        assert code in US_ISO_COUNTRIES, f"{code} must be treated as US-domestic"
    assert "CA" not in US_ISO_COUNTRIES
    assert "MX" not in US_ISO_COUNTRIES


def test_canonical_iata_applies_rename_aliases():
    """Regression guard for a real bug caught by build validation.

    Palm Beach Intl was recoded PBI -> DJT between FAA CY2024 and CY2025, but
    BTS still reports PBI for all 24 months. Without the alias, a medium hub
    with 4.26M annual enplanements had zero traffic and zero delay data, and
    nothing raised an error.
    """
    from etl.common import canonical_iata

    assert canonical_iata("PBI") == "DJT"
    assert canonical_iata("pbi") == "DJT"
    assert canonical_iata("LAX") == "LAX"   # unaliased codes pass through
    assert canonical_iata("") is None
    assert canonical_iata(None) is None
    assert canonical_iata("TOOLONG") is None


def test_alias_map_has_no_chains_or_self_loops():
    """An alias whose target is itself an alias would resolve inconsistently
    depending on application order."""
    aliases = config.BTS_IATA_ALIASES
    for src, dst in aliases.items():
        assert src != dst, f"self-loop alias {src}"
        assert dst not in aliases, f"chained alias {src}->{dst}->{aliases[dst]}"


def test_crosswalk_flags_unmatched_primary_separately_from_ga():
    """An unmatched primary airport is a missing airport; an unmatched GA
    field with no IATA code is out of scope. They must not be averaged into
    one match-rate number that hides the former."""
    faa = [
        {"faa_locid": "SJU", "name": "Luis Munoz Marin", "city": "San Juan",
         "state": "PR", "hub_class": "M", "service_level": "P"},
        {"faa_locid": "XX1", "name": "Tiny GA", "city": "C",
         "state": "TX", "hub_class": None, "service_level": "GA"},
    ]
    rows, report = crosswalk.build(faa, {}, {})
    assert rows == []
    assert report["unmatched"] == 2
    assert report["unmatched_primary"] == ["SJU"]
    assert report["faa_primary_count"] == 1


def test_new_england_region_assignment():
    for state in ("MA", "CT", "RI", "NH", "ME", "VT"):
        assert crosswalk.assign_region(state) == "new_england"
    for state in ("NY", "CA", "AK", None):
        assert crosswalk.assign_region(state) is None


# ---------------------------------------------------------------------------
# OTP aggregation — sums and counts, never averages
# ---------------------------------------------------------------------------


_OTP_HEADER = (
    "Year,Month,FlightDate,Origin,Dest,DepDelayMinutes,DepDel15,TaxiOut,"
    "Cancelled,Diverted,NASDelay,Distance\n"
)


def _make_otp_zip(tmp_path: Path, rows: list[str], name: str = "otp_2025_05.zip") -> Path:
    csv_text = _OTP_HEADER + "".join(rows)
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.html", "<html>ignore me</html>")
        zf.writestr("On_Time_Test.csv", csv_text)
    return path


def test_otp_aggregation_sums_and_counts(tmp_path):
    rows = [
        '2025,5,2025-05-01,BOS,JFK,10.00,0.00,15.00,0.00,0.00,2.00,187\n',
        '2025,5,2025-05-02,BOS,JFK,30.00,1.00,25.00,0.00,0.00,8.00,187\n',
        '2025,5,2025-05-03,BOS,JFK,,,,1.00,0.00,,187\n',   # cancelled
    ]
    agg = parse_otp.aggregate_zip(_make_otp_zip(tmp_path, rows))
    rec = agg[("BOS", "2025-05")]

    assert rec["flights"] == 3          # cancelled flights still count as flights
    assert rec["cancelled"] == 1
    assert rec["taxi_out_sum"] == 40.0  # cancelled row contributes nothing
    assert rec["taxi_out_n"] == 2
    assert rec["dep_del15"] == 1
    assert rec["dep_del15_n"] == 2
    assert rec["nas_delay_sum"] == 10.0


def test_otp_cancelled_flights_excluded_from_delay_denominators(tmp_path):
    """A cancelled flight has no taxi time; including it would dilute the mean."""
    rows = ['2025,5,2025-05-01,PVD,BOS,,,,1.00,0.00,,50\n'] * 5
    agg = parse_otp.aggregate_zip(_make_otp_zip(tmp_path, rows))
    rec = agg[("PVD", "2025-05")]
    assert rec["flights"] == 5
    assert rec["cancelled"] == 5
    assert rec["taxi_out_n"] == 0
    assert rec["nas_delay_n"] == 0


def test_otp_blank_nas_delay_counts_as_zero_minutes(tmp_path):
    """NASDelay is blank on undelayed flights — a real zero, not missing data."""
    rows = [
        '2025,5,2025-05-01,SFO,LAX,0.00,0.00,20.00,0.00,0.00,,337\n',
        '2025,5,2025-05-02,SFO,LAX,60.00,1.00,30.00,0.00,0.00,45.00,337\n',
    ]
    agg = parse_otp.aggregate_zip(_make_otp_zip(tmp_path, rows))
    rec = agg[("SFO", "2025-05")]
    assert rec["nas_delay_sum"] == 45.0
    assert rec["nas_delay_n"] == 2          # both flights in the denominator
    assert rec["nas_delay_sum"] / rec["nas_delay_n"] == 22.5


def test_otp_skips_malformed_rows(tmp_path):
    rows = [
        '2025,5,2025-05-01,BOS,JFK,10.00,0.00,15.00,0.00,0.00,0.00,187\n',
        '2025,5,,BOS,JFK,10.00,0.00,15.00,0.00,0.00,0.00,187\n',   # no date
        '2025,5,2025-05-01,,JFK,10.00,0.00,15.00,0.00,0.00,0.00,187\n',  # no origin
        '2025,5,2025-05-01,TOOLONG,JFK,10.00,0.00,15.00,0.00,0.00,0.00,187\n',
    ]
    agg = parse_otp.aggregate_zip(_make_otp_zip(tmp_path, rows))
    assert list(agg) == [("BOS", "2025-05")]
    assert agg[("BOS", "2025-05")]["flights"] == 1


def test_otp_windowed_average_differs_from_mean_of_monthly_means(tmp_path):
    """Regression guard for the weighting bug the schema exists to prevent."""
    big = ['2025,5,2025-05-01,LAX,JFK,0,0.00,20.00,0.00,0.00,0.00,2475\n'] * 100
    small = ['2025,6,2025-06-01,LAX,JFK,0,0.00,40.00,0.00,0.00,0.00,2475\n'] * 1

    a = parse_otp.aggregate_zip(_make_otp_zip(tmp_path, big, "otp_2025_05.zip"))
    b = parse_otp.aggregate_zip(_make_otp_zip(tmp_path, small, "otp_2025_06.zip"))

    total_sum = a[("LAX", "2025-05")]["taxi_out_sum"] + b[("LAX", "2025-06")]["taxi_out_sum"]
    total_n = a[("LAX", "2025-05")]["taxi_out_n"] + b[("LAX", "2025-06")]["taxi_out_n"]
    weighted = total_sum / total_n
    naive = (20.0 + 40.0) / 2

    assert round(weighted, 3) == round(2040 / 101, 3) == 20.198
    assert naive == 30.0
    assert abs(weighted - naive) > 9   # the bug would have been ~10 minutes wide
