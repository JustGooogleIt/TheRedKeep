"""HTTP API (DESIGN.md section 5): the library snapshot and per-portfolio analysis.

Run with ``redkeep serve``. The library is rebuilt by ``redkeep refresh`` on a
schedule; the API serves the latest snapshot and analyzes portfolios against it.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings
from .pipeline import Clients, analyze, load_latest, make_clients

app = FastAPI(title="The Red Keep", version=__version__, description="Scenario analysis engine: events, odds and impacts.")


@lru_cache
def settings() -> Settings:
    return Settings.from_env()


@lru_cache
def clients() -> Clients:
    return make_clients(settings(), claude=False)  # the API only analyzes; refresh runs from the command line


def snapshot(as_of: str | None = None) -> dict:
    s = settings()
    try:
        if not as_of:
            return load_latest(s.out_dir, "library")
        stamp = as_of.replace("-", "").replace(":", "")
        matches = sorted(s.out_dir.glob(f"library-{stamp}*.json"))
        if not matches:
            raise FileNotFoundError(as_of)
        return json.loads(matches[-1].read_text())
    except FileNotFoundError:
        raise HTTPException(404, "No library snapshot yet. Run `redkeep refresh`.") from None


class HoldingIn(BaseModel):
    ticker: str
    amount: float = Field(gt=0)
    unit: Literal["usd", "shares"] = "usd"


class AnalyzeIn(BaseModel):
    holdings: list[HoldingIn] = Field(min_length=1)
    name: str = "Portfolio"
    horizon_months: float | None = Field(default=None, gt=0, le=24)
    what_if: dict[str, Literal["yes", "no"]] | None = None
    as_of: str | None = None


@app.get("/health")
def health() -> dict:
    s = settings()
    return {"ok": True, "version": __version__, "claude": bool(s.anthropic_api_key), "massive": bool(s.massive_api_key)}


@app.get("/events")
def events(as_of: str | None = None) -> dict:
    return snapshot(as_of)


@app.get("/events/{event_id}")
def event(event_id: str, as_of: str | None = None) -> dict:
    for e in snapshot(as_of)["events"]:
        if e["id"] == event_id:
            return e
    raise HTTPException(404, f"No event {event_id!r} in the library.")


@app.post("/portfolio/analyze")
def portfolio_analyze(body: AnalyzeIn) -> dict:
    snap = snapshot(body.as_of)
    unknown = set(body.what_if or {}) - {e["id"] for e in snap["events"]}
    if unknown:
        raise HTTPException(422, f"Unknown events in what_if: {sorted(unknown)}")
    try:
        return analyze(settings(), clients(), snap, [(h.ticker.upper(), h.amount, h.unit) for h in body.holdings],
                       name=body.name, horizon_months=body.horizon_months, what_if=body.what_if)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(422, str(e)) from None
