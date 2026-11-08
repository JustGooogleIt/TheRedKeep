"""Reading the odds from a prediction market (DESIGN.md 6.4).

* Use the midpoint of the best bid and ask, not the last trade.
* The error grows with the spread and shrinks with volume:
  ``σ² = (spread / √12)² + (0.15 / √(1 + volume / $1,000))² + 0.005²``.
  The half-spread term treats the true price as anywhere inside the quote; the
  volume term gives a $1k market about ±10 points and a $1M market about ±0.5.
  These constants are placeholders until the calibration study (E2) fits them.
* The favorite-longshot correction maps market odds to calibrated odds ``p̂``.
  Until E2 is run on resolved markets, the table is the identity, so ``p̂ = q``.
"""

from __future__ import annotations

import math
from typing import Sequence

from ..engine.fusion import OddsReading
from ..markets import Market

VENUE_NAMES = {"polymarket": "Polymarket", "kalshi": "Kalshi"}


def market_sigma(spread: float | None, volume: float) -> float:
    s = 0.02 if spread is None else max(spread, 0.0)
    return math.sqrt((s / math.sqrt(12)) ** 2 + (0.15 / math.sqrt(1 + max(volume, 0.0) / 1000)) ** 2 + 0.005**2)


def event_side(m: Market, polarity: str) -> float | None:
    """The market's probability for the side that corresponds to the event."""
    q = m.mid
    if q is None:
        return None
    return 1.0 - q if polarity == "no" else q


def reading(m: Market, polarity: str = "yes", *, detail: str = "") -> OddsReading | None:
    q = event_side(m, polarity)
    if q is None:
        return None
    return OddsReading(
        source=VENUE_NAMES[m.venue], q=min(max(q, 0.0), 1.0), sigma=market_sigma(m.spread, m.volume),
        kind="market", detail=detail or describe_liquidity(m),
    )


def describe_liquidity(m: Market) -> str:
    vol = f"${m.volume / 1e6:.1f}M" if m.volume >= 1e6 else f"${m.volume / 1e3:.0f}k"
    spread = "no quote" if m.spread is None else f"{m.spread * 100:.0f}¢ spread"
    return f"{vol} volume · {spread}"


def liquid(markets: Sequence[Market], *, volume: float, spread: float) -> bool:
    return any(m.volume >= volume and (m.spread is not None and m.spread <= spread) for m in markets)


def calibrate(q: float, table: Sequence[tuple[float, float]] | None = None) -> float:
    """Favorite-longshot correction: piecewise-linear in logit space through (q, p̂) points."""
    if not table:
        return q
    pts = sorted(table)
    lg = lambda p: math.log(max(p, 1e-6) / max(1 - p, 1e-6))  # noqa: E731
    x = lg(q)
    xs, ys = [lg(a) for a, _ in pts], [lg(b) for _, b in pts]
    if x <= xs[0]:
        y = ys[0] + (x - xs[0])
    elif x >= xs[-1]:
        y = ys[-1] + (x - xs[-1])
    else:
        i = next(i for i in range(len(xs) - 1) if xs[i] <= x <= xs[i + 1])
        t = (x - xs[i]) / (xs[i + 1] - xs[i])
        y = ys[i] + t * (ys[i + 1] - ys[i])
    return 1 / (1 + math.exp(-y))
