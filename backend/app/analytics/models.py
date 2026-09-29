"""Value objects for the analytics layer.

Every score is returned with its full derivation attached — raw value,
normalised value, weight, contribution, coverage and source — so the
calculation can be audited without re-running it, and so the eventual agent
layer has nothing to invent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# Why a score is absent. Absence is always explained, never silently zero.
SuppressionReason = Literal[
    "insufficient_coverage",
    "insufficient_flight_volume",
    "no_data",
    "degenerate_cohort",
]

DivergenceClass = Literal[
    "TERMINAL_LED",
    "SYSTEMIC",
    "AIRSIDE_LED",
    "NO_NEAR_TERM_CASE",
    "MIXED",
    "UNCLASSIFIED_AIRSIDE_UNKNOWN",
    "UNCLASSIFIED",
]


@dataclass(frozen=True)
class ComponentResult:
    """One weighted input to a score, with its complete derivation."""

    id: str
    label: str
    raw: float | None
    raw_display: str | None          # human-readable, unit-carrying
    normalized: float | None         # 0-100, winsorized min-max within cohort
    percentile: float | None         # informational: % of cohort at or below
    weight: float                    # declared weight
    effective_weight: float | None   # after renormalisation over present components
    contribution: float | None       # effective_weight * normalized
    source: str
    available: bool
    note: str | None = None          # proxy warnings, caveats

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IndexResult:
    """A composite index (TDPI or ACI) plus its components."""

    index: str                       # "TDPI" | "ACI"
    label: str
    score: float | None              # None when suppressed
    coverage: float                  # share of declared weight actually present
    suppressed_reason: SuppressionReason | None
    components: list[ComponentResult]
    cohort_size: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["components"] = [c.to_dict() for c in self.components]
        return d


@dataclass(frozen=True)
class AirportScores:
    """Both indices for one airport, plus the divergence reading."""

    iata: str
    name: str
    state: str | None
    hub_class: str | None
    window: str
    cohort: str
    tdpi: IndexResult
    aci: IndexResult
    divergence_class: DivergenceClass
    divergence_reading: str
    sources: list[dict[str, Any]]
    limitations: list[str]
    # Supplementary ACI temporal diagnostic (Phase 8.2b). Optional and
    # defaulted, so every existing caller and response shape is unaffected:
    # `null` where the engine did not attach one. It explains the DISTRIBUTION
    # of the ACI score across the window and is never a component of any score,
    # ranking or classification.
    temporal: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tdpi"] = self.tdpi.to_dict()
        d["aci"] = self.aci.to_dict()
        return d


@dataclass(frozen=True)
class Indicator:
    """One observable UDEI indicator. Carries no magnitude estimate."""

    id: str
    label: str
    value: float | None
    value_display: str | None
    threshold_display: str
    triggered: bool | None           # None = could not be evaluated
    available: bool
    direction: str                   # what a trigger would be consistent with
    source: str
    unavailable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UnmetDemandEvidence:
    """Evidence-based reading. Deliberately has NO numeric magnitude field.

    There is no place in this structure to put "unmet passengers", because
    unmet demand is counterfactual and unobservable in public data. The
    absence of such a field is the control.
    """

    iata: str
    name: str
    window: str
    indicators: list[Indicator]
    triggered_count: int
    available_count: int
    total_count: int
    evidence_band: Literal["Weak", "Moderate", "Strong", "Indeterminate"]
    caveat: str
    sources: list[dict[str, Any]]
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["indicators"] = [i.to_dict() for i in self.indicators]
        return d


@dataclass(frozen=True)
class LongHaulBand:
    threshold_sm: int
    departures: float
    share_pct: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LongHaulScopeResult:
    scope: str                       # all_carriers | passenger | cargo
    scope_label: str
    total_departures: float
    total_passengers: float
    bands: list[LongHaulBand]
    headline_threshold_sm: int
    headline_share_pct: float | None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["bands"] = [b.to_dict() for b in self.bands]
        return d


@dataclass(frozen=True)
class LongHaulResult:
    """Long-haul breakdown.

    `period` is always explicit and never assumed to be the analysis window —
    a single-month result must never be presented as an annual one.
    """

    iata: str
    name: str
    period: str
    period_months: int
    is_full_window: bool
    definition: str
    unit: str
    scopes: list[LongHaulScopeResult]
    # Proves the exclusive scopes account for every departure. Passenger and
    # all-cargo alone do not — combi and amphibious configurations exist.
    reconciliation: dict[str, Any]
    top_destinations: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["scopes"] = [s.to_dict() for s in self.scopes]
        return d
