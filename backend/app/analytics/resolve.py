"""Deterministic airport resolution from free text.

This is where a naive build silently answers a *different question* than the
one asked. "Compare LA and Santa Ana" contains two traps: "LA" may mean LAX
alone or the whole LA basin, and "Santa Ana" is SNA — a code that shares no
letters with the words. Both are resolved here in Python, never by the model,
and the resolution reports its own interpretation so the agent can state the
assumption out loud rather than bury it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# Metro shorthands. `default` is what a bare mention resolves to; `members`
# is the full metro set offered as the alternative reading.
METRO_ALIASES: dict[str, dict[str, Any]] = {
    "la": {"default": ["LAX"], "members": ["LAX", "BUR", "LGB", "ONT", "SNA"],
           "label": "Los Angeles"},
    "los angeles": {"default": ["LAX"], "members": ["LAX", "BUR", "LGB", "ONT", "SNA"],
                    "label": "Los Angeles"},
    "nyc": {"default": ["JFK", "LGA", "EWR"], "members": ["JFK", "LGA", "EWR"],
            "label": "New York City"},
    "new york": {"default": ["JFK", "LGA", "EWR"], "members": ["JFK", "LGA", "EWR"],
                 "label": "New York City"},
    "bay area": {"default": ["SFO", "OAK", "SJC"], "members": ["SFO", "OAK", "SJC"],
                 "label": "San Francisco Bay Area"},
    "washington": {"default": ["DCA", "IAD"], "members": ["DCA", "IAD", "BWI"],
                   "label": "Washington DC"},
    "dc": {"default": ["DCA", "IAD"], "members": ["DCA", "IAD", "BWI"],
           "label": "Washington DC"},
    "chicago": {"default": ["ORD"], "members": ["ORD", "MDW"], "label": "Chicago"},
    "dallas": {"default": ["DFW"], "members": ["DFW", "DAL"], "label": "Dallas-Fort Worth"},
    "houston": {"default": ["IAH"], "members": ["IAH", "HOU"], "label": "Houston"},
    "miami": {"default": ["MIA"], "members": ["MIA", "FLL", "PBI"], "label": "Miami"},
}

REGION_ALIASES: dict[str, str] = {
    "new england": "new_england",
    "newengland": "new_england",
}

STATE_NAMES: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington state": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR",
}

# Common spoken names that are not the airport's official name.
NAME_HINTS: dict[str, str] = {
    "santa ana": "SNA",
    "john wayne": "SNA",
    "orange county": "SNA",
    "logan": "BOS",
    "boston": "BOS",
    "sfo": "SFO",
    "san francisco": "SFO",
    "anchorage": "ANC",
    "ted stevens": "ANC",
    "o'hare": "ORD",
    "ohare": "ORD",
    "midway": "MDW",
    "newark": "EWR",
    "laguardia": "LGA",
    "la guardia": "LGA",
    "jfk": "JFK",
    "kennedy": "JFK",
    "dulles": "IAD",
    "reagan": "DCA",
    "national": "DCA",
    "hartsfield": "ATL",
    "atlanta": "ATL",
    "sea-tac": "SEA",
    "seatac": "SEA",
    "bradley": "BDL",
    "hartford": "BDL",
    "t.f. green": "PVD",
    "tf green": "PVD",
    "providence": "PVD",
    "manchester": "MHT",
    "portland jetport": "PWM",
    "burlington": "BTV",
    "bangor": "BGR",
    "tweed": "HVN",
    "new haven": "HVN",
    "palm beach": "DJT",
}


@dataclass
class Resolution:
    query: str
    airports: list[str] = field(default_factory=list)
    method: str = "none"
    interpretation: str = ""
    ambiguous: bool = False
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[dict[str, str]] = field(default_factory=list)
    found: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def resolve(engine, query: str) -> Resolution:
    """Resolve one free-text location reference to IATA codes."""
    raw = (query or "").strip()
    q = _norm(raw)
    if not q:
        return Resolution(query=raw, interpretation="Empty query.")

    known = {c for c, m in engine.metrics.items() if m.has_traffic}

    # 1. Explicit IATA code.
    upper = raw.strip().upper()
    if len(upper) == 3 and upper in known:
        m = engine.get_metrics(upper)
        return Resolution(
            query=raw, airports=[upper], method="iata_code", found=True,
            interpretation=f"{upper} — {m.name}, {m.city}, {m.state}.",
        )

    # 2. Region.
    for alias, region in REGION_ALIASES.items():
        if alias in q:
            codes = engine.resolve_region(region)
            if codes:
                return Resolution(
                    query=raw, airports=codes, method="region", found=True,
                    interpretation=(
                        f"New England = {len(codes)} FAA primary commercial "
                        f"service airports across MA, CT, RI, NH, ME and VT."
                    ),
                )

    # 3. Metro shorthand. This is where "LA" gets decided — and declared.
    for alias, spec in METRO_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", q):
            default = [c for c in spec["default"] if c in known]
            members = [c for c in spec["members"] if c in known]
            if not default:
                continue
            wider = [c for c in members if c not in default]
            return Resolution(
                query=raw, airports=default, method="metro_alias", found=True,
                ambiguous=bool(wider),
                interpretation=(
                    f"Reading '{raw}' as {', '.join(default)}"
                    + (
                        f" (the primary {spec['label']} airport"
                        f"{'s' if len(default) > 1 else ''})."
                        if wider else f" ({spec['label']})."
                    )
                ),
                alternatives=(
                    [{
                        "label": f"All {spec['label']} airports",
                        "airports": members,
                    }] if wider else []
                ),
            )

    # 4. Spoken-name hints (Santa Ana -> SNA).
    for hint, code in NAME_HINTS.items():
        if hint in q and code in known:
            m = engine.get_metrics(code)
            return Resolution(
                query=raw, airports=[code], method="name_hint", found=True,
                interpretation=f"'{raw}' resolved to {code} — {m.name}.",
            )

    # 5. US state.
    state = STATE_NAMES.get(q) or (upper if len(upper) == 2 else None)
    if state:
        codes = engine.resolve_states([state])
        if codes:
            return Resolution(
                query=raw, airports=codes, method="state", found=True,
                interpretation=f"{len(codes)} primary airports in {state}.",
            )

    # 6. Substring match on airport or city name.
    hits = []
    for code, m in engine.metrics.items():
        if code not in known:
            continue
        hay = _norm(f"{m.name} {m.city or ''}")
        if q in hay:
            hits.append((code, m))
    if len(hits) == 1:
        code, m = hits[0]
        return Resolution(
            query=raw, airports=[code], method="name_match", found=True,
            interpretation=f"'{raw}' resolved to {code} — {m.name}.",
        )
    if len(hits) > 1:
        hits.sort(key=lambda t: -(t[1].passengers or 0))
        top = hits[:6]
        return Resolution(
            query=raw, airports=[top[0][0]], method="name_match", found=True,
            ambiguous=True,
            interpretation=(
                f"'{raw}' matched {len(hits)} airports; using the largest, "
                f"{top[0][0]} ({top[0][1].name})."
            ),
            alternatives=[{
                "label": "Other matches",
                "airports": [c for c, _ in top[1:]],
            }],
            suggestions=[
                {"iata": c, "name": m.name, "city": m.city or ""} for c, m in top
            ],
        )

    # 7. Not found — offer near matches rather than inventing one.
    near = []
    for code, m in engine.metrics.items():
        if code not in known:
            continue
        hay = _norm(f"{m.name} {m.city or ''}")
        if any(tok and tok in hay for tok in q.split()):
            near.append((code, m))
    near.sort(key=lambda t: -(t[1].passengers or 0))
    return Resolution(
        query=raw, found=False, method="not_found",
        interpretation=(
            f"No US primary commercial service airport matched '{raw}'. "
            f"This tool covers US airports only."
        ),
        suggestions=[
            {"iata": c, "name": m.name, "city": m.city or ""} for c, m in near[:5]
        ],
    )


def resolve_many(engine, queries: list[str]) -> list[Resolution]:
    return [resolve(engine, q) for q in queries]
