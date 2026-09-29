"""The six analytics tools exposed to the model.

Every tool is a thin wrapper over the Phase 2 deterministic engine. No tool
computes anything itself, and the model computes nothing at all — its only
numeric inputs are these return values.

Two schemas are shaped specifically to prevent misreporting:

* `long_haul_breakdown` returns a band table and no scalar "the answer" field,
  so a single percentage cannot be quoted without its threshold.
* `unmet_demand_evidence` has no magnitude field anywhere, so there is nowhere
  to put a fabricated "unmet passengers" figure.
"""

from __future__ import annotations

import json

from typing import Any, Callable

from app.analytics import AnalyticsEngine
from app.analytics.definitions import LONG_HAUL_THRESHOLDS_SM
from app.analytics.resolve import resolve as resolve_airport

# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "resolve_airports",
        "description": (
            "Turn free-text place references into US airport IATA codes. ALWAYS "
            "call this first when the user names a place rather than a 3-letter "
            "code — including 'LA', 'Santa Ana', 'New England', a city, a state "
            "or an airport name. It reports how it interpreted the query and "
            "whether the reading was ambiguous; if `ambiguous` is true you must "
            "state the assumption you are proceeding on in your answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Place references exactly as the user wrote them.",
                }
            },
            "required": ["queries"],
        },
    },
    {
        "name": "get_airport_profile",
        "description": (
            "Full deterministic profile for one airport: traffic and delay "
            "metrics, TDPI (Terminal Demand Pressure Index), ACI (Airside "
            "Congestion Index), the divergence class, and every score's "
            "component-level derivation. Also returns 'aci_temporal', which "
            "describes WHEN within the window the ACI score occurred — how many "
            "months were measured, whether pressure was sustained or "
            "concentrated in a few months, and any coverage caveats. Use for "
            "'tell me about X', 'why is X ranked there', or any single-airport "
            "question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "iata": {"type": "string", "description": "3-letter IATA code."},
                "hub_class_cohort": {
                    "type": "boolean",
                    "description": (
                        "Normalise against same-hub-class peers instead of all "
                        "US primary airports. Default false."
                    ),
                },
            },
            "required": ["iata"],
        },
    },
    {
        "name": "compare_airports",
        "description": (
            "Compare two or more airports side by side. Returns VOLUME (total "
            "departures, passengers) and PER-FLIGHT INTENSITY (taxi-out, NAS "
            "delay, delay rate, cancellations) as SEPARATE blocks, plus runway "
            "geometry and both indices. Use for any 'compare X and Y' question, "
            "especially congestion — a bigger airport is not automatically more "
            "congested, and you must report volume and intensity distinctly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "iatas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Two or more IATA codes.",
                },
            },
            "required": ["iatas"],
        },
    },
    {
        "name": "rank_airports",
        "description": (
            "Rank a set of airports by TDPI or ACI, with full score breakdowns, "
            "divergence classes and absolute scale beside each relative score. "
            "Use for 'which airports in <region> are candidates for X'. Airports "
            "whose score is suppressed are returned separately in `unscored` "
            "with a reason — never treat those as low-scoring. The analyst's "
            "panel renders this whole ranking table, so summarise the top few "
            "and the shape of the result rather than retyping every row."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "iatas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "IATA codes to rank (from resolve_airports).",
                },
                "index": {
                    "type": "string",
                    "enum": ["TDPI", "ACI"],
                    "description": "Which index to rank by. Default TDPI.",
                },
                "min_passengers": {
                    "type": "number",
                    "description": (
                        "Optional minimum 12-month passengers, to exclude very "
                        "small airports whose growth-driven scores are volatile."
                    ),
                },
            },
            "required": ["iatas"],
        },
    },
    {
        "name": "long_haul_breakdown",
        "description": (
            "Distance distribution of departures from an airport, returned as a "
            "SENSITIVITY TABLE across thresholds (1500/2000/2500/3000/6000 "
            "statute miles), split by T-100 aircraft configuration: all "
            "carriers, passenger, all-cargo, combi (passengers AND freight on "
            "one main deck) and amphibious. Passenger and all-cargo are NOT "
            "exhaustive — use the `reconciliation` block instead of implying "
            "they are. There is deliberately no single 'long-haul percentage' "
            "field: the share depends heavily on the threshold and the scope, "
            "so state both. The analyst's panel already renders this entire "
            "grid, so quote the headline figures and point them to the panel "
            "rather than retyping the table. Report `period` exactly as returned."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "iata": {"type": "string", "description": "3-letter IATA code."},
                "month_start": {
                    "type": "string",
                    "description": "Optional 'YYYY-MM'. Defaults to the full analysis window.",
                },
                "month_end": {
                    "type": "string",
                    "description": "Optional 'YYYY-MM'. Defaults to the full analysis window.",
                },
            },
            "required": ["iata"],
        },
    },
    {
        "name": "unmet_demand_evidence",
        "description": (
            "Observable indicators consistent with constrained supply at an "
            "airport: load factor, frequency suppression, upgauging, airside "
            "throughput ceiling, fare premium. Returns each indicator's value, "
            "threshold and trigger status plus a qualitative evidence band. "
            "Unmet demand CANNOT be measured — there is no numeric estimate in "
            "this result and you must not produce one. Indicators without data "
            "are returned as unavailable; report them that way."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "iata": {"type": "string", "description": "3-letter IATA code."},
            },
            "required": ["iata"],
        },
    },
]

TOOL_NAMES = [t["name"] for t in TOOL_SCHEMAS]


def _r(v: Any, places: int = 4) -> Any:
    """Round floats for the model view.

    Full precision is preserved in the payload the frontend receives; the model
    only ever quotes a rounded figure anyway, and `0.8259876543` costs several
    times what `0.826` does across a conversation.
    """
    if isinstance(v, bool) or not isinstance(v, float):
        return v
    return round(v, places)


def _r_score(v: Any) -> Any:
    """Round a 0–100 index-scale value to one decimal, for narrative use.

    Index scores, normalised component values and percentiles are cohort-relative
    positions on a 0–100 scale; a second decimal is noise and a fourth
    ("ACI 79.4545") reads as false precision in prose. Ratios and rates keep
    `_r`'s four places, because a cancellation rate of 0.0105 needs them.

    This affects the MODEL VIEW only. The frontend payload and every
    deterministic calculation keep full precision.
    """
    if isinstance(v, bool) or not isinstance(v, float):
        return v
    return round(v, 1)


def _r_minutes(v: Any) -> Any:
    """Minutes to two decimals: 18.52, not 18.5234."""
    if isinstance(v, bool) or not isinstance(v, float):
        return v
    return round(v, 2)


# --------------------------------------------------------------------------
# Implementations
# --------------------------------------------------------------------------


class ToolError(Exception):
    """Recoverable tool failure, reported back to the model as an error result."""


class ToolBox:
    """Dispatches tool calls to the deterministic analytics engine."""

    def __init__(self, engine: AnalyticsEngine) -> None:
        self.engine = engine
        self._handlers: dict[str, Callable[..., dict]] = {
            "resolve_airports": self.resolve_airports,
            "get_airport_profile": self.get_airport_profile,
            "compare_airports": self.compare_airports,
            "rank_airports": self.rank_airports,
            "long_haul_breakdown": self.long_haul_breakdown,
            "unmet_demand_evidence": self.unmet_demand_evidence,
        }

    def call(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            raise ToolError(f"Unknown tool '{name}'. Available: {', '.join(TOOL_NAMES)}")
        return handler(**payload)

    # ------------------------------------------------------------------
    # LLM view vs frontend payload
    # ------------------------------------------------------------------
    # The frontend gets the complete result; the model gets a compact view of
    # the same numbers. Two things are stripped, and neither is a loss of
    # analytical content:
    #
    #   * `sources` and `limitations` are identical on every tool result, so
    #     they live in the cached system prompt instead of being resent with
    #     each call and then re-read on every subsequent turn;
    #   * component `note` prose (the T4 proxy warning and similar) is already
    #     in the system prompt verbatim.
    #
    # Every figure the model may quote is still present, and the numeric audit
    # runs against the FULL payload, so a follow-up can still cite anything the
    # engine returned.

    _DROP_ALWAYS = ("sources", "limitations", "guidance")

    @staticmethod
    def _component(comp: dict, keep_percentile: bool = False) -> dict:
        out = {
            "id": comp.get("id"),
            "label": comp.get("label"),
            # `raw_display` is already formatted by the metric's own formatter,
            # so the raw value needs no rounding policy here.
            "raw": comp.get("raw_display"),
            "norm": _r_score(comp.get("normalized")),
            "weight": comp.get("weight"),
            "contribution": _r_score(comp.get("contribution")),
        }
        if keep_percentile and comp.get("percentile") is not None:
            out["percentile"] = _r_score(comp["percentile"])
        if not comp.get("available"):
            out["available"] = False
        return out

    @classmethod
    def _index(cls, idx: dict, with_components: bool = True) -> dict:
        out: dict[str, Any] = {
            "score": _r_score(idx.get("score")),
            "coverage": _r(idx.get("coverage"), 2),
        }
        if idx.get("suppressed_reason"):
            out["suppressed_reason"] = idx["suppressed_reason"]
        if with_components:
            out["components"] = [
                cls._component(c, keep_percentile=True) for c in idx.get("components", [])
            ]
        return out

    @classmethod
    def _scores(cls, scores: dict, with_components: bool = True) -> dict:
        return {
            "tdpi": cls._index(scores.get("tdpi", {}), with_components),
            "aci": cls._index(scores.get("aci", {}), with_components),
            "class": scores.get("divergence_class"),
            "class_reading": scores.get("divergence_reading"),
        }

    @classmethod
    def _temporal(cls, t: dict | None) -> dict | None:
        """Supporting evidence about WHEN an ACI score occurred.

        The monthly series is deliberately withheld from the model: the panel
        renders it, and 12 rows per airport would ride along in history for the
        rest of the conversation. The model gets the summary figures it needs to
        describe the pattern in words.
        """
        if not t:
            return None
        conc = t.get("concentration") or {}
        out: dict[str, Any] = {
            "temporal_pattern": t.get("temporal_pattern"),
            "pattern_label": t.get("pattern_label"),
            "months_evaluated": t.get("months_evaluated"),
            "months_available": t.get("months_available"),
            "months_expected": t.get("months_expected"),
            "monthly_spread": _r_score(t.get("monthly_spread")),
            "elevated_months": t.get("elevated_months"),
            "description": t.get("description"),
        }
        if t.get("no_elevated_months"):
            # Guards against reading INTERMITTENT as intermittent congestion.
            out["no_elevated_months"] = True
        if conc.get("reliable"):
            out["worst_two_month_drop"] = _r_score(conc.get("worst_two_month_drop"))
        else:
            out["worst_two_month_drop"] = None
            out["concentration_unavailable"] = conc.get("unreliable_reason")
        if t.get("elevated_season"):
            out["elevated_season"] = t["elevated_season"]
        if t.get("uncertainty"):
            out["uncertainty"] = t["uncertainty"]
        return out

    def compact_for_model(self, name: str, result: Any) -> Any:
        """Reduce a tool result to what the model needs to reason and narrate."""
        if not isinstance(result, dict) or "error" in result:
            return result

        r = {k: v for k, v in result.items() if k not in self._DROP_ALWAYS}

        if name == "resolve_airports":
            r["resolutions"] = [
                {
                    "query": x.get("query"),
                    "airports": x.get("airports"),
                    "found": x.get("found"),
                    "ambiguous": x.get("ambiguous"),
                    "interpretation": x.get("interpretation"),
                    **({"alternatives": x["alternatives"]} if x.get("alternatives") else {}),
                    **({"suggestions": [s["iata"] for s in x["suggestions"]]}
                       if x.get("suggestions") else {}),
                }
                for x in result.get("resolutions", [])
            ]

        elif name == "get_airport_profile":
            # Single airport: keep the full derivation — this is what answers
            # "why is it scored that way".
            r["scores"] = self._scores(result.get("scores", {}), with_components=True)
            temporal = self._temporal((result.get("scores") or {}).get("temporal"))
            if temporal:
                r["aci_temporal"] = temporal

        elif name == "compare_airports":
            r["airports"] = [
                {
                    "iata": a.get("iata"), "name": a.get("name"),
                    "hub_class": a.get("hub_class"),
                    "runway_count": a.get("runway_count"),
                    "longest_runway_ft": a.get("longest_runway_ft"),
                    "volume": {k: _r(v) for k, v in (a.get("volume") or {}).items()},
                    # Minutes to 2 dp; rates and ratios keep 4.
                    "intensity": {
                        k: (_r_minutes(v) if k.endswith("_min") else _r(v))
                        for k, v in (a.get("intensity") or {}).items()
                    },
                    "scores": self._scores(a.get("scores") or {}, with_components=False),
                    **({"aci_temporal": t} if (t := self._temporal(
                        (a.get("scores") or {}).get("temporal"))) else {}),
                }
                for a in result.get("airports", [])
            ]

        elif name == "rank_airports":
            # Components for the top 3 only: enough to explain the leaders
            # without shipping 16 full derivations that then ride along in
            # history for the rest of the conversation.
            def row(x, detailed):
                return {
                    "rank": x.get("rank"),
                    "iata": x.get("iata"), "name": x.get("name"),
                    "state": x.get("state"), "hub_class": x.get("hub_class"),
                    "passengers": _r((x.get("scale") or {}).get("passengers")),
                    "scores": self._scores(x, with_components=detailed),
                    **({"unscored_reason": x["unscored_reason"]}
                       if x.get("unscored_reason") else {}),
                }

            ranked = result.get("ranked", [])
            r["ranked"] = [row(x, i < 3) for i, x in enumerate(ranked)]
            r["unscored"] = [row(x, False) for x in result.get("unscored", [])]
            r["note"] = (
                "Component-level derivations are included for the top 3 only. "
                "Call get_airport_profile for any other airport's breakdown."
            )

        elif name == "long_haul_breakdown":
            r["top_destinations"] = [
                {k: d.get(k) for k in ("dest", "departures", "distance_sm", "is_long_haul")}
                for d in result.get("top_destinations", [])[:8]
            ]
            # Keep the reconciliation, minus the per-scope labels the model
            # already has from `scopes`.
            rec = result.get("reconciliation") or {}
            if rec:
                r["reconciliation"] = {
                    "total_departures": _r(rec.get("total_departures")),
                    "residual": _r(rec.get("residual")),
                    "reconciles": rec.get("reconciles"),
                    "breakdown": {
                        k: {"departures": _r(v.get("departures")),
                            "share_pct": _r(v.get("share_pct"), 2)}
                        for k, v in (rec.get("breakdown") or {}).items()
                    },
                    "note": rec.get("note"),
                }

        elif name == "unmet_demand_evidence":
            r["indicators"] = [
                {
                    "id": i.get("id"), "label": i.get("label"),
                    "value": i.get("value_display"),
                    "threshold": i.get("threshold_display"),
                    "triggered": i.get("triggered"),
                    "available": i.get("available"),
                    **({"unavailable_reason": i["unavailable_reason"]}
                       if not i.get("available") else {}),
                }
                for i in result.get("indicators", [])
            ]

        return r

    def serialise_for_model(self, name: str, result: Any) -> str:
        """What actually goes back to the model as the tool result."""
        return json.dumps(
            self.compact_for_model(name, result), default=str, separators=(",", ":")
        )

    # -- helpers ---------------------------------------------------------

    def _require(self, iata: str) -> str:
        code = (iata or "").strip().upper()
        m = self.engine.get_metrics(code)
        if m is None:
            res = resolve_airport(self.engine, iata)
            hint = (
                f" Did you mean: {', '.join(s['iata'] for s in res.suggestions)}?"
                if res.suggestions else ""
            )
            raise ToolError(
                f"'{iata}' is not a US primary commercial service airport in the "
                f"dataset.{hint} Call resolve_airports first."
            )
        if not m.has_traffic:
            raise ToolError(
                f"{code} is in the airport universe but reported no traffic in "
                f"the analysis window, so no metrics can be computed for it."
            )
        return code

    # -- tools -----------------------------------------------------------

    def resolve_airports(self, queries: list[str]) -> dict[str, Any]:
        results = [resolve_airport(self.engine, q).to_dict() for q in queries]
        return {
            "window": self.engine.window,
            "resolutions": results,
            "sources": self.engine.sources(),
            "guidance": (
                "Where a resolution is ambiguous, state the reading you used in "
                "your answer and mention the alternative."
            ),
        }

    def get_airport_profile(
        self, iata: str, hub_class_cohort: bool = False
    ) -> dict[str, Any]:
        code = self._require(iata)
        scores = self.engine.profile(code, hub_class_cohort=hub_class_cohort)
        m = self.engine.get_metrics(code)
        return {
            "window": self.engine.window,
            "airport": {
                "iata": code,
                "name": m.name,
                "city": m.city,
                "state": m.state,
                "hub_class": m.hub_class,
                "runway_count": m.runway_count,
                "longest_runway_ft": m.longest_runway_ft,
            },
            "traffic": {
                "departures": m.departures,
                "passengers": m.passengers,
                "seats": m.seats,
                "load_factor": m.load_factor,
                "seats_per_departure": m.seats_per_departure,
                "passenger_growth_yoy": m.pax_growth,
                "departure_growth_yoy": m.departure_growth,
                "international_departure_share": m.intl_departure_share,
                "enplanements_cy": m.enplanements,
                "months_of_data": m.traffic_months,
            },
            "delay": {
                "otp_flights": m.flights,
                "avg_taxi_out_min": m.taxi_out_avg,
                "nas_delay_per_flight_min": m.nas_delay_per_flight,
                "dep_delayed_over_15min_rate": m.dep_del15_rate,
                "cancellation_rate": m.cancel_rate,
                "avg_departure_delay_min": m.dep_delay_avg,
            },
            "scores": scores.to_dict(),
            # Hoisted to the top level: every tool result must carry its own
            # provenance and caveats, or the API response can silently omit
            # them for single-airport queries.
            "sources": scores.sources,
            "limitations": scores.limitations,
        }

    def compare_airports(self, iatas: list[str]) -> dict[str, Any]:
        codes = [self._require(c) for c in iatas]
        if len(codes) < 2:
            raise ToolError("compare_airports needs at least two airports.")
        return self.engine.compare(codes)

    def rank_airports(
        self,
        iatas: list[str],
        index: str = "TDPI",
        min_passengers: float | None = None,
    ) -> dict[str, Any]:
        if not iatas:
            raise ToolError("rank_airports needs at least one airport.")
        idx = (index or "TDPI").upper()
        if idx not in ("TDPI", "ACI"):
            raise ToolError("index must be 'TDPI' or 'ACI'.")
        result = self.engine.rank(
            [c.strip().upper() for c in iatas],
            index=idx,
            min_passengers=min_passengers,
        )
        result["reporting_requirement"] = (
            "The analyst's panel already shows this full ranking table. "
            "Summarise the leaders and the overall shape; do not retype every "
            "row. Airports in `unscored` are UNKNOWN on that index, not low — "
            "say 'not measured'. If you applied `min_passengers`, say so and "
            "give the threshold, and note that excluded airports were excluded "
            "for scale volatility, not for scoring poorly."
        )
        return result

    def long_haul_breakdown(
        self,
        iata: str,
        month_start: str | None = None,
        month_end: str | None = None,
    ) -> dict[str, Any]:
        code = self._require(iata)
        kwargs: dict[str, Any] = {}
        if month_start:
            kwargs["month_start"] = month_start
        if month_end:
            kwargs["month_end"] = month_end
        result = self.engine.long_haul(code, **kwargs).to_dict()
        result["thresholds_reported_sm"] = LONG_HAUL_THRESHOLDS_SM
        result["reporting_requirement"] = (
            "State the threshold, the unit (departures performed) and the "
            "aircraft-configuration scope alongside any percentage. Report the "
            "`period` field verbatim; never present a single-month figure as an "
            "annual one. The analyst's panel already shows the full sensitivity "
            "grid — quote the headline figures and refer to the panel rather "
            "than retyping the table. Passenger and all-cargo do not sum to the "
            "total; see `reconciliation`."
        )
        return result

    def unmet_demand_evidence(self, iata: str) -> dict[str, Any]:
        code = self._require(iata)
        result = self.engine.unmet_demand(code).to_dict()
        result["reporting_requirement"] = (
            "Report the indicators and the band. Do NOT state or imply a number "
            "of unmet passengers or unmet flights — no such quantity exists in "
            "this data and inventing one would be fabrication."
        )
        return result

