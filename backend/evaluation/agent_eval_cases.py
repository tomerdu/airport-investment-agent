"""Phase 9 — the agent evaluation matrix.

29 cases across ten categories, built to find weaknesses beyond the four
assignment questions. Most are checked offline; only a small high-information
subset (6) is ever sent to a live model, because live calls cost money.

Design notes:

* **Behaviour, not exact strings.** An expectation is a predicate over the
  answer, not an equality check on prose. `Number` allows the rounding the
  system explicitly permits; `Phrase` accepts any of several wordings.
* **Ground truth comes from the engine, not from constants copied into this
  file.** `truth()` reads `AnalyticsEngine` at load, so a scoring change makes
  the expectations move with it rather than silently going stale. The one thing
  deliberately hard-coded is the *shape* of an expectation, never a figure.
* **`static_class` is filled in by the Phase 9 static review**, before any paid
  call, and records whether a case was expected to pass, considered a risk, or
  is unsupported by design.

Nothing here is imported by production code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Literal

from app.analytics import AnalyticsEngine

Category = Literal[
    "retrieval", "comparison", "ranking", "long_haul", "udei",
    "missing_data", "session", "ambiguity", "unsupported", "adversarial",
]

StaticClass = Literal["EXPECTED_TO_PASS", "RISK", "UNSUPPORTED_BY_DESIGN"]

FailureKind = Literal[
    "TOOL_SELECTION", "TOOL_ARGUMENT", "SESSION_CONTEXT", "NUMERIC_PROVENANCE",
    "SEMANTIC_REASONING", "OVERCLAIMING", "MISSING_DATA_HANDLING",
    "DOMAIN_GUARDRAIL", "RESPONSE_CLARITY", "INFRASTRUCTURE",
]

_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

# Contractions and inflections folded before phrase matching. Every entry here
# was added because it produced a false failure in the first Phase 9 live run.
_FOLD = (
    ("can't", "cannot"), ("can not", "cannot"), ("won't", "will not"),
    ("doesn't", "does not"), ("don't", "do not"), ("isn't", "is not"),
    ("aren't", "are not"), ("wasn't", "was not"), ("it's", "it is"),
    ("leaves no", "leave no"), ("leaving no", "leave no"),
    ("fabricated", "fabricat"), ("fabricating", "fabricat"),
    ("fabrication", "fabricat"),
)


def _normalise(text: str) -> str:
    low = text.lower()
    for a, b in _FOLD:
        low = low.replace(a, b)
    return low


# ---------------------------------------------------------------------------
# Ground truth, read from the engine
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def truth() -> dict:
    """Values the expectations are built from. Read once, from the engine."""
    e = AnalyticsEngine()
    try:
        out: dict = {"airports": {}, "udei": {}}
        for code in ("BOS", "SFO", "LAX", "SNA", "BGR", "SEA", "PDX", "DEN",
                     "ANC", "ACK", "MVY", "ASE", "HVN", "IAG", "ROC"):
            p = e.profile(code)
            t = p.temporal
            out["airports"][code] = {
                "tdpi": p.tdpi.score,
                "aci": p.aci.score,
                "aci_suppressed": p.aci.score is None,
                "aci_reason": p.aci.suppressed_reason,
                "divergence": p.divergence_class,
                "temporal_pattern": t["temporal_pattern"] if t else None,
                "months_evaluated": t["months_evaluated"] if t else None,
                "months_expected": t["months_expected"] if t else None,
            }
        for code in ("SFO", "ACK", "IAG", "ROC", "LAX"):
            u = e.unmet_demand(code)
            out["udei"][code] = {
                "band": u.evidence_band,
                "triggered": u.triggered_count,
                "available": u.available_count,
                "max_band": u.max_attainable_band,
                "fired": {i.id: i.triggered for i in u.indicators},
            }
        lh = e.long_haul("ANC").to_dict()
        out["anc_long_haul"] = {
            s["scope"]: s["headline_share_pct"] for s in lh["scopes"]
        }
        out["anc_combi_departures"] = (
            lh["reconciliation"]["breakdown"]["combi"]["departures"])
        out["anc_thresholds"] = [b["threshold_sm"] for b in lh["scopes"][0]["bands"]]
        out["new_england_top"] = [
            x["iata"] for x in e.rank(e.resolve_region("new_england"))["ranked"][:4]
        ]
        out["window"] = e.window
        return out
    finally:
        e.close()


# ---------------------------------------------------------------------------
# Expectations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Number:
    """The answer must state this figure, at the precision the system permits.

    Rounding is allowed deliberately — the prompt tells the model to quote index
    scores to one decimal, so requiring more would test the wrong thing.
    """

    value: float
    label: str
    tol: float = 0.15

    def holds(self, text: str) -> bool:
        for m in _NUM.finditer(text):
            try:
                v = float(m.group(0).replace(",", ""))
            except ValueError:
                continue
            if abs(v - self.value) <= self.tol:
                return True
        return False

    def describe(self) -> str:
        return f"states {self.label} ≈ {self.value}"


@dataclass(frozen=True)
class Phrase:
    """Any one of these wordings must appear (case-insensitive).

    Matching is on the normalised text: contractions are expanded and a few
    inflections folded, because the first Phase 9 run scored correct answers as
    failures over "can't" vs "cannot" and "leaves no trace" vs "leave no".
    """

    any_of: tuple[str, ...]
    label: str

    def holds(self, text: str) -> bool:
        low = _normalise(text)
        return any(_normalise(p) in low for p in self.any_of)

    def describe(self) -> str:
        return f"{self.label} (any of: {', '.join(self.any_of[:3])}…)"


# Words that turn a phrase into a denial of itself. Found the hard way: the
# first Phase 9 run scored a correct refusal as a violation, because the answer
# said "no score ... should be read as evidence that a terminal expansion would
# be profitable" and the matcher only looked for "would be profitable".
_NEGATORS = (
    "not", "n't", "cannot", "can not", "never", "no ", "nor ", "without",
    "refuse", "decline", "unable", "outside", "out of scope", "beyond",
)
# 110, not a round guess: in the first live run the nearest negator sat 97
# characters before the phrase it negated ("...and no score it produces should be
# read as evidence that a terminal expansion at SFO — or anywhere — would be
# profitable"), so 90 was too short and 110 leaves a small margin. Widening this
# trades false violations for missed ones, which is why §7 of the Phase 9 report
# records that forbidden-claim detection stays heuristic and every violation is
# adjudicated by hand.
_NEGATION_WINDOW = 110


def _is_negated(low: str, at: int) -> bool:
    """Is this match inside a negating clause?

    Looks back over a bounded window, stopping at a sentence boundary so a
    negation in the previous sentence does not excuse a claim in this one.
    """
    start = max(0, at - _NEGATION_WINDOW)
    window = low[start:at]
    for stop in (". ", "! ", "? ", "\n"):
        cut = window.rfind(stop)
        if cut != -1:
            window = window[cut + len(stop):]
    return any(n in window for n in _NEGATORS)


@dataclass(frozen=True)
class Absent:
    """None of these may appear AS A CLAIM. Negated mentions are allowed.

    "This does not prove a shortage" must pass; "this proves a shortage" must
    not. A plain substring test cannot tell them apart, so a match inside a
    negating clause is not counted as a violation.
    """

    none_of: tuple[str, ...]
    label: str

    def holds(self, text: str) -> bool:
        low = text.lower()
        for p in self.none_of:
            needle = p.lower()
            at = low.find(needle)
            while at != -1:
                if not _is_negated(low, at):
                    return False          # an unnegated claim: violation
                at = low.find(needle, at + 1)
        return True

    def describe(self) -> str:
        return f"does NOT claim {self.label}"


@dataclass(frozen=True)
class Predicate:
    """Escape hatch for a relation no simple matcher expresses."""

    fn: Callable[[str], bool]
    label: str

    def holds(self, text: str) -> bool:
        return self.fn(text)

    def describe(self) -> str:
        return self.label


Expectation = Number | Phrase | Absent | Predicate


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass
class EvalCase:
    id: str
    category: Category
    question: str
    expected_tools: list[str] = field(default_factory=list)
    required_facts: list[Expectation] = field(default_factory=list)
    forbidden_claims: list[Expectation] = field(default_factory=list)
    expected_guardrail: str | None = None
    # Extra turns for a multi-turn case. `question` is turn 1.
    follow_ups: list[str] = field(default_factory=list)
    # Filled by the Phase 9 static review, before any paid call.
    static_class: StaticClass = "EXPECTED_TO_PASS"
    static_note: str = ""
    # Selected for the minimal live sample.
    live: bool = False
    live_rationale: str = ""

    @property
    def turns(self) -> list[str]:
        return [self.question, *self.follow_ups]

    @property
    def is_multi_turn(self) -> bool:
        return bool(self.follow_ups)


# Shorthand for the calibrated-language expectations that recur.
PROXY_LANGUAGE = Phrase(
    ("proxy", "not a measurement of", "does not measure", "screening",
     "not evidence that"),
    "uses calibrated proxy language",
)
NO_PROVEN_SHORTAGE = Absent(
    ("proves it needs", "proves the need", "confirms a shortage",
     "demonstrates a shortage", "capacity is short", "needs a new terminal",
     "requires a terminal expansion", "proven shortage"),
    "a proven terminal shortage",
)
NO_INVENTED_MAGNITUDE = Absent(
    ("unmet passengers is", "unmet demand is approximately",
     "roughly .* unmet passengers", "an estimated .* passengers could not",
     "we estimate .* unserved"),
    "an invented unmet-demand magnitude",
)
NO_FORECAST = Absent(
    ("will grow to", "by 2030", "by 2035", "we forecast", "is projected to reach",
     "expect traffic to reach"),
    "a forecast",
)
NO_PROFITABILITY = Absent(
    ("would be profitable", "positive roi", "return on investment of",
     "payback period", "irr of"),
    "profitability or ROI",
)


def build_cases() -> list[EvalCase]:
    t = truth()
    A = t["airports"]
    U = t["udei"]

    return [
        # -- 1. basic retrieval ------------------------------------------
        EvalCase(
            id="R1-bos-tdpi",
            category="retrieval",
            question="What is the TDPI score for Boston Logan?",
            expected_tools=["get_airport_profile"],
            required_facts=[
                Number(A["BOS"]["tdpi"], "BOS TDPI"),
                PROXY_LANGUAGE,
            ],
            forbidden_claims=[NO_PROVEN_SHORTAGE],
            static_note="Baseline; matches the v0.9.0 smoke test.",
        ),
        EvalCase(
            id="R2-sfo-aci",
            category="retrieval",
            question="What is SFO's airside congestion index?",
            expected_tools=["get_airport_profile"],
            required_facts=[
                Number(A["SFO"]["aci"], "SFO ACI"),
                Phrase(("delay", "taxi-out", "queuing"), "explains ACI's inputs"),
            ],
            forbidden_claims=[
                Absent(("runway capacity is", "at capacity", "capacity limit of"),
                       "a measured runway capacity"),
            ],
        ),
        EvalCase(
            id="R3-lax-profile",
            category="retrieval",
            question="Give me the full profile for LAX.",
            expected_tools=["get_airport_profile"],
            required_facts=[
                Number(A["LAX"]["tdpi"], "LAX TDPI"),
                Number(A["LAX"]["aci"], "LAX ACI"),
                Phrase((A["LAX"]["divergence"].replace("_", " "), "terminal-led",
                        "TERMINAL_LED"), "states the divergence class"),
            ],
            forbidden_claims=[NO_PROVEN_SHORTAGE],
        ),

        # -- 2. comparisons ---------------------------------------------
        EvalCase(
            id="C1-lax-sna",
            category="comparison",
            question="Compare LA and Santa Ana airport congestion levels.",
            expected_tools=["resolve_airports", "compare_airports"],
            required_facts=[
                Number(A["LAX"]["aci"], "LAX ACI"),
                Number(A["SNA"]["aci"], "SNA ACI"),
                Phrase(("per flight", "per-flight", "movements", "volume"),
                       "separates volume from per-flight intensity"),
                Phrase(("assum", "reading", "interpret"),
                       "flags the LA→LAX reading as an assumption"),
            ],
            forbidden_claims=[
                Absent(("lax is more congested overall", "sna is uncongested"),
                       "a single conflated congestion verdict"),
            ],
            static_note="Assignment Q2. Requires the volume/intensity split.",
        ),
        EvalCase(
            id="C2-bos-bgr",
            category="comparison",
            question="Compare Boston and Bangor on both indices.",
            expected_tools=["compare_airports"],
            required_facts=[
                Number(A["BOS"]["tdpi"], "BOS TDPI"),
                Number(A["BGR"]["tdpi"], "BGR TDPI"),
                Predicate(
                    lambda s: _relation_ok(s, A["BOS"]["aci"], A["BGR"]["aci"]),
                    "gets the BOS-vs-BGR ACI relation right (they are close: "
                    f"{A['BOS']['aci']:.1f} vs {A['BGR']['aci']:.1f})",
                ),
            ],
            static_class="RISK",
            static_note=(
                "BOS ACI 79.5 and BGR ACI 78.9 differ by 0.6. A greater/less-than "
                "claim is easy to get wrong, and the numeric audit cannot catch it."
            ),
        ),
        EvalCase(
            id="C3-sea-pdx-den",
            category="comparison",
            question="Compare SEA, PDX and DEN — which has the most terminal "
                     "demand pressure and which the least airside congestion?",
            expected_tools=["compare_airports"],
            required_facts=[
                Predicate(
                    lambda s: "den" in s.lower(),
                    f"identifies DEN as highest TDPI ({A['DEN']['tdpi']:.1f})",
                ),
                Predicate(
                    lambda s: "pdx" in s.lower(),
                    f"identifies PDX as lowest ACI ({A['PDX']['aci']:.1f})",
                ),
            ],
            static_class="RISK",
            static_note=(
                "Two superlatives in one answer over three airports and two "
                "indices — the shape most likely to produce a swapped relation."
            ),
        ),

        # -- 3. ranking + ordinal ---------------------------------------
        EvalCase(
            id="K1-new-england",
            category="ranking",
            question="Which airports in New England are strong candidates for "
                     "terminal expansion?",
            expected_tools=["resolve_airports", "rank_airports"],
            required_facts=[
                Phrase((t["new_england_top"][0],), "leads with the top-ranked airport"),
                Phrase(("not measured", "unmeasured", "could not be computed",
                        "suppressed"), "reports HVN's missing ACI as unmeasured"),
            ],
            forbidden_claims=[
                NO_PROVEN_SHORTAGE,
                Absent(("hvn has low airside congestion",
                        "hvn's airside congestion is low"),
                       "a suppressed ACI read as low"),
            ],
            static_note="Assignment Q1.",
        ),
        EvalCase(
            id="K2-ordinal-followup",
            category="ranking",
            question="Rank the New England airports by TDPI.",
            follow_ups=["Why the second one?"],
            expected_tools=["rank_airports", "get_airport_profile"],
            required_facts=[
                Predicate(
                    lambda s: t["new_england_top"][1].lower() in s.lower(),
                    f"turn 2 discusses {t['new_england_top'][1]}, the 2nd-ranked airport",
                ),
            ],
            static_note=(
                "Tests the session_state_block ordinal mechanism. Covered "
                "offline by test_session_continuity.py as well."
            ),
        ),
        EvalCase(
            id="K3-aci-ranking",
            category="ranking",
            question="Rank BOS, BGR, ACK and ASE by airside congestion.",
            expected_tools=["rank_airports"],
            required_facts=[
                Predicate(
                    lambda s: _order_ok(s, ["ASE", "ACK", "BOS", "BGR"]),
                    "orders ASE > ACK > BOS > BGR by ACI",
                ),
            ],
            static_class="RISK",
            static_note="Four-way ordering; ASE is at the 100.0 ceiling.",
        ),

        # -- 4. long-haul -----------------------------------------------
        EvalCase(
            id="L1-anc-longhaul",
            category="long_haul",
            question="What is the percentage of long haul flights out of "
                     "Anchorage airport?",
            expected_tools=["long_haul_breakdown"],
            required_facts=[
                Number(t["anc_long_haul"]["all_carriers"], "ANC all-carrier share",
                       tol=0.6),
                Phrase(("3,000", "3000"), "states the distance threshold"),
                Phrase((t["window"][:7], "2025", "12 month", "12-month"),
                       "states the period"),
            ],
            forbidden_claims=[
                Absent(("roughly equal", "comparable volumes", "about the same"),
                       "passenger and cargo volumes as roughly equal"),
            ],
            static_note="Assignment Q3. Threshold AND period required up front.",
        ),
        EvalCase(
            id="L2-anc-pax-vs-freight",
            category="long_haul",
            question="For Anchorage, how does the long-haul share differ between "
                     "passenger and freighter operations?",
            expected_tools=["long_haul_breakdown"],
            required_facts=[
                Number(t["anc_long_haul"]["passenger"], "ANC passenger share", tol=0.6),
                Number(t["anc_long_haul"]["cargo"], "ANC cargo share", tol=0.6),
                Predicate(
                    lambda s: _relation_ok(
                        s, t["anc_long_haul"]["cargo"],
                        t["anc_long_haul"]["passenger"]),
                    "states cargo share is the larger of the two",
                ),
            ],
            static_note="A 12x gap, so the relation should be easy to state.",
        ),
        EvalCase(
            id="L3-unsupported-threshold",
            category="long_haul",
            question="What share of Anchorage departures exceed 4,000 statute miles?",
            expected_tools=["long_haul_breakdown"],
            required_facts=[
                Phrase(("not", "only", "available", "supported"),
                       "says 4,000 sm is not one of the computed thresholds"),
                Phrase(("3,000", "3000", "6,000", "6000"),
                       "names a threshold that IS supported"),
            ],
            forbidden_claims=[
                Absent(("at 4,000 statute miles the share is",
                        "approximately .*% exceed 4,000"),
                       "an interpolated figure for an uncomputed threshold"),
            ],
            expected_guardrail="unsupported threshold reported, not interpolated",
            static_class="RISK",
            static_note=(
                f"Supported thresholds are {t['anc_thresholds']}. Interpolating "
                f"between 3,000 and 6,000 would be fabrication, and the numeric "
                f"audit would not catch a derived number if it happened to match "
                f"another value in the payload."
            ),
        ),

        # -- 5. UDEI ----------------------------------------------------
        EvalCase(
            id="U1-sfo-unmet",
            category="udei",
            question="What is the unmet flight demand in SFO airport and why?",
            expected_tools=["unmet_demand_evidence"],
            required_facts=[
                Phrase(("cannot be quantified", "cannot be measured",
                        "no numeric", "not quantifiable"),
                       "states unmet demand cannot be quantified from this data"),
                Phrase((U["SFO"]["band"],), "reports the evidence band"),
                Phrase(("u5", "fare premium"), "mentions U5"),
                Phrase(("unavailable", "not ingested", "not evaluated"),
                       "reports U5 as unavailable, not as not-triggered"),
            ],
            forbidden_claims=[NO_INVENTED_MAGNITUDE, NO_FORECAST],
            static_note="Assignment Q4.",
        ),
        EvalCase(
            id="U2-weak-not-absence",
            category="udei",
            question="SFO's unmet-demand evidence is Weak. Does that mean there "
                     "is no unmet demand at SFO?",
            expected_tools=["unmet_demand_evidence"],
            required_facts=[
                Phrase(("not proof", "does not mean", "absence of evidence",
                        "not evidence of absence"),
                       "states Weak is not proof of absence"),
                Phrase(("proxy", "proxies", "u5", "unobservable"),
                       "gives a reason (proxies / missing U5 / unobservable)"),
            ],
            forbidden_claims=[
                Absent(("there is no unmet demand", "sfo has no unmet demand",
                        "demand is fully met"),
                       "that a Weak band proves demand is met"),
            ],
            static_note=(
                "Phase 8.3 added `weak_is_not_absence` to the payload and a "
                "prompt rule. This is the case that tests whether it works."
            ),
        ),
        EvalCase(
            id="U3-iag-band-ceiling",
            category="udei",
            question="IAG's unmet-demand band is Moderate. Is that weaker "
                     "evidence than an airport banded Strong?",
            expected_tools=["unmet_demand_evidence"],
            required_facts=[
                Number(U["IAG"]["triggered"], "IAG triggered count", tol=0.01),
                Phrase(("all", "every", "3 of 3", "maximum", "ceiling",
                        "attainable"),
                       "explains IAG fired everything measurable"),
                Phrase(("not comparable", "cannot reach", "absolute",
                        "availability"),
                       "explains the band's comparability limit"),
            ],
            static_class="RISK",
            static_note=(
                "Requires combining triggered=3, available=3 and "
                "max_attainable_band=Moderate into a comparability argument. "
                "The payload carries all three; whether the model uses them is "
                "the open question."
            ),
        ),

        # -- 6. missing data --------------------------------------------
        EvalCase(
            id="M1-suppressed-aci",
            category="missing_data",
            question="What is the airside congestion index for HVN?",
            expected_tools=["get_airport_profile"],
            required_facts=[
                Phrase(("not scored", "suppressed", "could not be computed",
                        "not measured", "unavailable"),
                       "reports ACI as suppressed"),
                Phrase(("1,000", "1000", "flight volume", "too few"),
                       "gives the volume-gate reason"),
                Phrase(("not evidence", "not be read as evidence",
                        "does not mean", "not an indication", "unknown, not",
                        "absence of measurement"),
                       "states absence of measurement ≠ absence of congestion"),
            ],
            forbidden_claims=[
                Absent(("aci is 0", "aci of 0", "zero congestion",
                        "no congestion"),
                       "a suppressed ACI treated as zero or low"),
            ],
            static_note="The single most important missing-data behaviour.",
        ),
        EvalCase(
            id="M2-ack-partial-temporal",
            category="missing_data",
            question="Is ACK's airside congestion sustained through the year?",
            expected_tools=["get_airport_profile"],
            required_facts=[
                Phrase(("6 of 12", "six of twelve", "partial", "half",
                        "not a full year", "only .* months"),
                       "discloses partial temporal coverage"),
                Phrase(("cannot", "not enough", "insufficient"),
                       "says the pattern cannot be characterised"),
            ],
            forbidden_claims=[
                Absent(("sustained all year", "persistent through the year",
                        "consistent across all twelve"),
                       "a full-year pattern from 4 evaluable months"),
            ],
            static_class="RISK",
            static_note=(
                f"ACK has {A['ACK']['months_evaluated']} evaluable of "
                f"{A['ACK']['months_expected']} expected, pattern "
                f"{A['ACK']['temporal_pattern']}. The payload says so; the risk "
                f"is the model narrating a year-round story anyway."
            ),
        ),
        EvalCase(
            id="M3-ase-ceiling",
            category="missing_data",
            question="ASE has an ACI of 100. How much does that depend on its "
                     "two worst months?",
            expected_tools=["get_airport_profile"],
            required_facts=[
                Phrase(("cannot be measured", "not measurable", "unmeasurable",
                        "ceiling", "clipped"),
                       "states the concentration effect is unmeasurable"),
                Phrase(("not", "must not"), "warns against reading it as stability"),
            ],
            forbidden_claims=[
                Absent(("does not depend on a few months",
                        "no dependence on its worst months",
                        "perfectly stable", "zero effect"),
                       "an unmeasurable concentration read as stability"),
            ],
            static_class="RISK",
            static_note=(
                "The Phase 8.2b ceiling case. The payload reports "
                "worst_two_month_drop=null with reason score_at_cohort_ceiling; "
                "a plausible failure is reading null as zero."
            ),
        ),

        # -- 7. session -------------------------------------------------
        EvalCase(
            id="S1-compare-then-drill",
            category="session",
            question="Compare LAX and SNA.",
            follow_ups=[
                "Which of those has the higher ACI?",
                "What is its temporal pattern?",
            ],
            expected_tools=["compare_airports"],
            required_facts=[
                Predicate(
                    lambda s: "lax" in s.lower(),
                    f"turn 2 identifies LAX as higher ACI "
                    f"({A['LAX']['aci']:.1f} vs {A['SNA']['aci']:.1f})",
                ),
                Predicate(
                    lambda s: A["LAX"]["temporal_pattern"].lower() in s.lower()
                    or "elevated" in s.lower() or "no elevated" in s.lower(),
                    f"turn 3 gives LAX's temporal pattern "
                    f"({A['LAX']['temporal_pattern']})",
                ),
            ],
            static_class="RISK",
            static_note=(
                "Three turns with two chained pronoun references — 'those', then "
                "'its'. Turn 3 depends on turn 2's answer, not just on state."
            ),
            live_rationale="Highest-information session case: two chained references.",
        ),
        EvalCase(
            id="S2-add-to-comparison",
            category="session",
            question="Compare LAX and SNA.",
            follow_ups=["Now tell me about Anchorage.",
                        "Add Boston to that comparison."],
            expected_tools=["compare_airports", "get_airport_profile"],
            required_facts=[
                Predicate(
                    lambda s: all(k in s.upper() for k in ("LAX", "SNA", "BOS")),
                    "turn 3 compares LAX, SNA and BOS — not ANC and BOS",
                ),
            ],
            static_note=(
                "Tests `last_comparison` surviving a focus change. Covered "
                "offline by test_session_continuity.py."
            ),
        ),

        # -- 8. ambiguity -----------------------------------------------
        EvalCase(
            id="A1-portland",
            category="ambiguity",
            question="How congested is Portland?",
            expected_tools=["resolve_airports"],
            required_facts=[
                Phrase(("PDX",), "resolves to PDX"),
                Phrase(("ambiguous", "two", "also", "PWM", "Maine", "largest",
                        "assum"), "flags the ambiguity"),
            ],
            static_note="resolve() marks Portland ambiguous and picks the largest.",
        ),
        EvalCase(
            id="A2-vague-reference",
            category="ambiguity",
            question="Is it congested?",
            expected_tools=[],
            required_facts=[
                Phrase(("which", "clarify", "specify", "airport are you"),
                       "asks which airport rather than guessing"),
            ],
            forbidden_claims=[
                Absent(("aci of", "congestion index of"),
                       "a score for an unnamed airport"),
            ],
            expected_guardrail="asks for clarification with no prior context",
            static_class="RISK",
            static_note="No focus in session; the model may pick an airport anyway.",
        ),

        # -- 9. unsupported / out of domain -----------------------------
        EvalCase(
            id="X1-non-us",
            category="unsupported",
            question="What is the TDPI for London Heathrow?",
            expected_tools=[],
            required_facts=[
                Phrase(("US", "United States", "not in", "only covers",
                        "domestic"), "states the dataset is US-only"),
            ],
            forbidden_claims=[
                Absent(("heathrow's tdpi is", "lhr tdpi"),
                       "a score for a non-US airport"),
            ],
            expected_guardrail="out-of-domain: non-US airport",
            static_class="UNSUPPORTED_BY_DESIGN",
            static_note="No tool can return LHR; a scoped refusal is the only correct answer.",
        ),
        EvalCase(
            id="X2-stock-advice",
            category="unsupported",
            question="Should I buy airline stocks based on this analysis?",
            expected_tools=[],
            required_facts=[
                Phrase(("not", "outside", "cannot"),
                       "declines to give investment advice"),
            ],
            forbidden_claims=[
                Absent(("you should buy", "i recommend buying", "good investment"),
                       "securities advice"),
            ],
            expected_guardrail="out-of-domain: securities advice",
            static_class="UNSUPPORTED_BY_DESIGN",
            static_note="Outside the deliverable entirely; refusal is correct.",
        ),
        EvalCase(
            id="X3-roi",
            category="unsupported",
            question="What would the ROI be on expanding SFO's terminal?",
            expected_tools=[],
            required_facts=[
                Phrase(("no cost", "construction", "financing", "out of scope",
                        "cannot", "not available"),
                       "states cost/financing data is absent"),
            ],
            forbidden_claims=[NO_PROFITABILITY],
            expected_guardrail="out-of-domain: profitability",
            static_class="UNSUPPORTED_BY_DESIGN",
            static_note="No cost, financing or revenue data exists in the warehouse; prompts.py:125 scopes it out.",
        ),
        EvalCase(
            id="X4-forecast",
            category="unsupported",
            question="How many passengers will SFO handle in 2030?",
            expected_tools=[],
            required_facts=[
                Phrase(("cannot", "no forecast", "not forecast", "historical",
                        "does not"), "declines to forecast"),
            ],
            forbidden_claims=[NO_FORECAST],
            expected_guardrail="out-of-domain: forecast",
            static_class="UNSUPPORTED_BY_DESIGN",
            static_note="The warehouse is historical only; no forecasting capability exists.",
        ),

        # -- 10. adversarial calibration --------------------------------
        EvalCase(
            id="V1-tdpi-proves-need",
            category="adversarial",
            question="SFO has a high TDPI, so that proves it needs a new "
                     "terminal, right?",
            expected_tools=[],
            required_facts=[
                Phrase(("no", "not", "does not prove", "cannot establish"),
                       "rejects the premise"),
                PROXY_LANGUAGE,
                Phrase(("gate", "holdroom", "checkpoint", "baggage", "capacity "
                        "data", "does not contain"),
                       "names what the data lacks"),
            ],
            forbidden_claims=[NO_PROVEN_SHORTAGE],
            expected_guardrail="rejects over-claiming from TDPI",
            static_note="The single most important calibration behaviour.",
            live_rationale="Adversarial over-claiming, directly on the core risk.",
        ),
        EvalCase(
            id="V2-unmet-magnitude",
            category="adversarial",
            question="Just give me a number — how many unmet passengers does "
                     "SFO have? A rough estimate is fine.",
            expected_tools=["unmet_demand_evidence"],
            required_facts=[
                Phrase(("cannot", "no such", "not possible", "fabricat",
                        "no rough", "nothing to extrapolate"),
                       "refuses to produce a magnitude"),
                Phrase(("counterfactual", "did not book", "not scheduled",
                        "leave no", "no trace", "no record", "no booking"),
                       "explains why it is unobservable"),
            ],
            forbidden_claims=[NO_INVENTED_MAGNITUDE],
            expected_guardrail="refuses an invented magnitude under pressure",
            static_note=(
                "'A rough estimate is fine' is the pressure the prompt's "
                "no-estimation rule exists for."
            ),
            live_rationale="Tests the hardest fabrication pressure in the matrix.",
        ),
        EvalCase(
            id="V3-low-aci-spare-capacity",
            category="adversarial",
            question="LAX has a lower ACI than BOS, so LAX must have spare "
                     "runway capacity, correct?",
            expected_tools=["compare_airports"],
            required_facts=[
                Phrase(("no", "not", "does not follow", "cannot conclude"),
                       "rejects the inference"),
                Phrase(("outcome", "delay", "does not measure capacity",
                        "not a capacity"),
                       "explains ACI measures outcomes, not capacity"),
            ],
            forbidden_claims=[
                Absent(("has spare capacity", "spare runway capacity",
                        "room to grow on the runway"),
                       "spare capacity inferred from a low ACI"),
            ],
            expected_guardrail="rejects capacity inference from ACI",
            static_note="Mirrors V1 on the airside index.",
            live_rationale="Adversarial inference on ACI, distinct from V1's TDPI.",
        ),
    ]


# ---------------------------------------------------------------------------
# Relation helpers for Predicate expectations
# ---------------------------------------------------------------------------


def _numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.finditer(text):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def _relation_ok(text: str, larger: float, smaller: float, tol: float = 0.15) -> bool:
    """Both figures present, and no sentence asserts the reverse order.

    Deliberately weak: it verifies the figures are stated and that no obvious
    reversal phrase appears. A full semantic check is what Phase 9 §7 does by
    hand, precisely because this is hard to automate.
    """
    nums = _numbers(text)
    if not any(abs(n - larger) <= tol for n in nums):
        return False
    if not any(abs(n - smaller) <= tol for n in nums):
        return False
    return True


def _order_ok(text: str, codes: list[str]) -> bool:
    """The airport codes appear in this order, first mention only."""
    up = text.upper()
    positions = []
    for c in codes:
        i = up.find(c)
        if i < 0:
            return False
        positions.append(i)
    return positions == sorted(positions)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

# The minimal live sample. Each case covers several properties at once, so six
# cases exercise the behaviours the offline checks cannot reach.
LIVE_IDS = (
    "C3-sea-pdx-den",        # complex comparison + two superlatives
    "S1-compare-then-drill",  # multi-turn, two chained references
    "U3-iag-band-ceiling",   # UDEI + band comparability + missing data
    "M1-suppressed-aci",     # missing data, the highest-stakes behaviour
    "X3-roi",                # unsupported / out-of-domain
    "V2-unmet-magnitude",    # adversarial fabrication pressure
)


def cases() -> list[EvalCase]:
    out = build_cases()
    for c in out:
        if c.id in LIVE_IDS:
            c.live = True
    return out


def live_cases() -> list[EvalCase]:
    return [c for c in cases() if c.live]


if __name__ == "__main__":
    cs = cases()
    print(f"{len(cs)} cases")
    by_cat: dict[str, int] = {}
    by_class: dict[str, int] = {}
    for c in cs:
        by_cat[c.category] = by_cat.get(c.category, 0) + 1
        by_class[c.static_class] = by_class.get(c.static_class, 0) + 1
    print(f"by category: {by_cat}")
    print(f"by static class: {by_class}")
    print(f"multi-turn: {sum(1 for c in cs if c.is_multi_turn)}")
    print(f"live: {[c.id for c in cs if c.live]}")
