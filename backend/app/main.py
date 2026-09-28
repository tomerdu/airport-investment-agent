"""FastAPI backend.

Two surfaces:

* `/chat` — the conversational agent (needs an API key)
* `/analytics/*` — the deterministic engine directly, with no model in the
  path. These keep working if the LLM is unavailable, which is the documented
  degradation path: a narrower true answer rather than a wider guess.

    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.agent import config as agent_config
from app.agent.orchestrator import Orchestrator
from app.agent.session import SessionStore
from app.agent.usage import TRACKER
from app.analytics import AnalyticsEngine
from app.analytics.resolve import resolve as resolve_airport

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api")

app = FastAPI(
    title="Airport Investment Intelligence Agent",
    description=(
        "Deterministic airport screening analytics with a conversational "
        "interface. All numbers come from the analytics engine; the language "
        "model narrates but never calculates."
    ),
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = AnalyticsEngine()
store = SessionStore()
orchestrator = Orchestrator(engine=engine, store=store)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = None


class ResolveRequest(BaseModel):
    queries: list[str]


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    cohort = engine.cohort()
    return {
        "status": "ok",
        "window": engine.window,
        "airports": len(engine.metrics),
        "cohort_size": cohort.size,
        "aci_eligible": cohort.aci_eligible_size,
        "llm_configured": agent_config.has_api_key(),
        "model": agent_config.MODEL,
        "active_sessions": len(store),
    }


@app.get("/sources")
def sources() -> dict:
    return {"window": engine.window, "sources": engine.sources()}


@app.get("/config")
def configuration() -> dict:
    """Credential/model status. Never includes the key itself."""
    return agent_config.describe_credentials()


@app.get("/usage")
def usage() -> dict:
    """Cumulative API token usage and estimated cost for this process.

    Token counts come from Anthropic's `response.usage`; the cost is a local
    estimate. No prompts, completions or credentials are recorded.
    """
    return {
        "totals": TRACKER.totals(),
        "requests": [r.to_dict() for r in TRACKER.requests[-50:]],
    }


@app.post("/usage/reset")
def reset_usage() -> dict:
    TRACKER.reset()
    return {"status": "reset", "totals": TRACKER.totals()}


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


@app.post("/chat")
def chat(req: ChatRequest) -> dict:
    if not agent_config.has_api_key():
        raise HTTPException(
            status_code=503,
            detail=(
                "ANTHROPIC_API_KEY is not configured. The /analytics endpoints "
                "still work without it."
            ),
        )
    return orchestrator.chat(req.message, session_id=req.session_id).to_dict()


@app.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session")
    return session.snapshot()


@app.delete("/sessions/{session_id}")
def reset_session(session_id: str) -> dict:
    return store.reset(session_id).snapshot()


# ---------------------------------------------------------------------------
# Analytics — no LLM in the path
# ---------------------------------------------------------------------------


@app.post("/analytics/resolve")
def analytics_resolve(req: ResolveRequest) -> dict:
    return {
        "window": engine.window,
        "resolutions": [resolve_airport(engine, q).to_dict() for q in req.queries],
    }


@app.get("/analytics/airports")
def analytics_airports(region: str | None = None, hub_class: str | None = None) -> dict:
    rows = [
        {
            "iata": m.iata, "name": m.name, "city": m.city, "state": m.state,
            "region": m.region, "hub_class": m.hub_class,
            "passengers": m.passengers, "runway_count": m.runway_count,
        }
        for m in engine.metrics.values()
        if m.has_traffic
        and (region is None or m.region == region)
        and (hub_class is None or m.hub_class == hub_class)
    ]
    rows.sort(key=lambda r: -(r["passengers"] or 0))
    return {"window": engine.window, "count": len(rows), "airports": rows}


@app.get("/analytics/profile/{iata}")
def analytics_profile(iata: str) -> dict:
    scores = engine.profile(iata.upper())
    if scores is None:
        raise HTTPException(status_code=404, detail=f"Unknown airport '{iata}'")
    return scores.to_dict()


@app.get("/analytics/compare")
def analytics_compare(iatas: str) -> dict:
    codes = [c.strip().upper() for c in iatas.split(",") if c.strip()]
    if len(codes) < 2:
        raise HTTPException(status_code=400, detail="Provide at least two IATA codes")
    return engine.compare(codes)


@app.get("/analytics/rank")
def analytics_rank(
    region: str | None = None,
    iatas: str | None = None,
    index: str = "TDPI",
    min_passengers: float | None = None,
) -> dict:
    if region:
        codes = engine.resolve_region(region)
    elif iatas:
        codes = [c.strip().upper() for c in iatas.split(",") if c.strip()]
    else:
        raise HTTPException(status_code=400, detail="Provide `region` or `iatas`")
    if not codes:
        raise HTTPException(status_code=404, detail="No airports matched")
    return engine.rank(codes, index=index.upper(), min_passengers=min_passengers)


@app.get("/analytics/long-haul/{iata}")
def analytics_long_haul(
    iata: str, month_start: str | None = None, month_end: str | None = None
) -> dict:
    kwargs = {}
    if month_start:
        kwargs["month_start"] = month_start
    if month_end:
        kwargs["month_end"] = month_end
    if engine.get_metrics(iata.upper()) is None:
        raise HTTPException(status_code=404, detail=f"Unknown airport '{iata}'")
    try:
        return engine.long_haul(iata.upper(), **kwargs).to_dict()
    except Exception as exc:  # noqa: BLE001
        # Previously this collapsed every failure into a bare 404, which hid a
        # real cross-thread SQLite fault behind "not found".
        log.exception("long_haul failed for %s", iata)
        raise HTTPException(
            status_code=500, detail=f"Long-haul computation failed: {exc}"
        ) from exc


@app.get("/analytics/unmet-demand/{iata}")
def analytics_unmet(iata: str) -> dict:
    result = engine.unmet_demand(iata.upper())
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown airport '{iata}'")
    return result.to_dict()
