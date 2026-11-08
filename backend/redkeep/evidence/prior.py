"""The AI prior: Claude's structured starting estimate of ``q`` and ``δ`` (DESIGN.md 4.5 and 8).

It is a starting estimate. Its errors, ``τ_AI``, will come from backtests on
resolved events (E3 and E6); until then they are set wide: for the jump, 0.8
typical moves per factor and half the estimated move, added in quadrature (a
big estimated move is uncertain in size, not only in sign); for the
probability, one unit of log-odds. The prior's probability is only used when
no market fits: the AI never produces a final number when market evidence
exists.

Like the judge, each call is logged and cached by its exact request. Eval E9 asks the same prior about resolved
markets, by their titles (``ask``), to score Claude with no tools against the engine.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from ..engine.fusion import DeltaReading, OddsReading
from ..events import EventSpec
from ..factors import FACTORS, KEYS
from ..matching.judge import FALLBACK_BETA, JudgeRefused

PRIOR_VERSION = "prior-v1"
TAU_DELTA = 0.8
TAU_SHARE = 0.5  # of the estimated move, added in quadrature to TAU_DELTA
TAU_LOGIT = 1.0

SYSTEM = """\
You give a careful starting estimate for a macro or geopolitical event that a portfolio risk engine will later \
check against market data. You estimate two things.

1. probability: the chance the event happens, as defined by its exact terms, as of today's date.
2. moves: how far each of ten market factors would move if the event happens compared with if it doesn't, \
measured at the moment the outcome becomes known. Express each move in typical moves: one typical move is one \
standard deviation of that factor's weekly change, given below in its natural unit. Positive means the factor rises \
(stocks up, yields up, breakevens up, the dollar up, oil up, credit spreads wider, the VIX up, China stocks up, \
chips beating the S&P 500, gold up). Use 0 when the event has no clear effect. Most events move most factors by \
less than one typical move; reserve moves beyond 3 for severe shocks.

Ground your estimates in how markets reacted to comparable past events, and keep each reasoning field to two or \
three plain sentences."""


class FactorMoves(BaseModel):
    eq: float
    rates: float
    infl: float
    usd: float
    oil: float
    credit: float
    vix: float
    china: float
    chips: float
    gold: float


class Prior(BaseModel):
    probability: float = Field(description="0 to 1")
    probability_reasoning: str
    moves: FactorMoves
    move_reasoning: str


class AIPrior:
    def __init__(self, client: Any, *, model: str, effort: str = "medium", log_dir: Path | None = None):
        self.client, self.model, self.effort, self.log_dir = client, model, effort, log_dir
        self.calls = self.cached = self.tokens_in = self.tokens_out = 0  # what the calls cost, for E9
        self._lock = threading.Lock()

    def request(self, brief: dict, today: date, typical: np.ndarray) -> dict[str, Any]:
        factors = [
            {"key": f.key, "name": f.name, "typical_weekly_move": f"{t:.2f} {f.unit}" if math.isfinite(t) else f"{f.typical_default} {f.unit}"}
            for f, t in zip(FACTORS, typical)
        ]
        return {"today": today.isoformat(), "event": brief, "factors": factors}

    def estimate(self, spec: EventSpec, today: date, typical: np.ndarray) -> Prior:
        return self.ask(spec.id, spec.brief(), today, typical)

    def ask(self, name: str, brief: dict, today: date, typical: np.ndarray) -> Prior:
        """The prior for one question as of ``today``: an event of the library, or a market by its title (E9)."""
        req = self.request(brief, today, typical)
        body = json.dumps(req, indent=1, ensure_ascii=False)
        key = hashlib.sha256(f"{PRIOR_VERSION}|{self.model}|{self.effort}|{body}".encode()).hexdigest()[:32]
        path = self.log_dir / "prior" / f"{name.replace(':', '-')}-{key}.json" if self.log_dir else None
        if path and path.exists():
            with self._lock:
                self.cached += 1
            return Prior.model_validate(json.loads(path.read_text())["output"])
        resp = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=8000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": body}],
            output_format=Prior,
        )
        usage = getattr(resp, "usage", None)
        with self._lock:
            self.calls += 1
            self.tokens_in += int(getattr(usage, "input_tokens", 0) or 0)
            self.tokens_out += int(getattr(usage, "output_tokens", 0) or 0)
        if resp.stop_reason == "refusal":
            raise JudgeRefused(f"the prior was declined for {name}")
        if resp.parsed_output is None:
            raise RuntimeError(f"no prior for {name} (stop_reason={resp.stop_reason})")
        out = resp.parsed_output
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "version": PRIOR_VERSION, "model": self.model, "served_by": getattr(resp, "model", None),
                "request": req, "output": out.model_dump(),
            }, indent=1, ensure_ascii=False))
        return out

    def usage(self) -> dict[str, int]:
        return {"calls": self.calls, "cached": self.cached, "tokens_in": self.tokens_in, "tokens_out": self.tokens_out}


def delta_reading(p: Prior) -> DeltaReading:
    m = p.moves.model_dump()
    delta = np.array([float(m[k]) for k in KEYS])
    return DeltaReading(kind="ai", name="AI prior", detail=p.move_reasoning, delta=delta,
                        sigma=np.sqrt(TAU_DELTA**2 + (TAU_SHARE * delta) ** 2))


def odds_reading(p: Prior) -> OddsReading:
    q = min(max(p.probability, 0.001), 0.999)
    return OddsReading("AI prior", q, max(0.02, TAU_LOGIT * q * (1 - q)), "ai", p.probability_reasoning)
