"""Jump windows (DESIGN.md 4.5 and 6.4), on daily data for this slice.

A window is a trading day on which at least one monitored market's odds moved
sharply (``move``, 5 points by default). In those windows we regress each
factor's move (in typical moves) on *all* the odds changes that day, so an
event's jump is separated from the moves of events that shifted with it:

    r_f,w = Σ_k δ_k,f · Δq_k,w + noise

Odds are read at 4 p.m. New York time, when the US stock market closes, so they
line up with daily closes. The design calls for 30-minute windows on intraday
data; daily windows are a coarser first version whose wider error bars carry
through fusion. Each event's reading counts as one ETF-window market reading
for the quorum.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np

from ..engine.fusion import DeltaReading
from ..factordata import FactorData
from ..factors import NF

NY = ZoneInfo("America/New_York")


def at_close(points: list[tuple[datetime, float]], hour: int = 16) -> dict[date, float]:
    """The last odds at or before the stock market close, each New York day."""
    out: dict[date, tuple[datetime, float]] = {}
    for t, q in points:
        local = t.astimezone(NY)
        if local.hour < hour or (local.hour == hour and local.minute == 0):
            d = local.date()
            if d not in out or t > out[d][0]:
                out[d] = (t, q)
    return {d: q for d, (_, q) in out.items()}


@dataclass
class WindowFit:
    readings: dict[str, DeltaReading]
    windows: list[date]
    moves: dict[str, int]  # sharp moves per event
    left_out: int = 0  # windows some factor left to another reading (``skip``)


def jump_windows(odds: dict[str, dict[date, float]], fd: FactorData, *, move: float = 0.05, min_moves: int = 3,
                 skip: dict[int, set[date]] | None = None, left_to: str = "another reading") -> WindowFit:
    """``skip`` leaves days out of one factor's regression. The pipeline passes the days whose window spans a weekend
    that Hyperliquid's perps measured (``left_to`` names them), so the weekend reading and this one rest on separate
    data and count as independent readings for the quorum."""
    dates = fd.dates
    prev = {d: dates[i - 1] for i, d in enumerate(dates) if i}
    ids = sorted(odds)
    dQ = np.zeros((len(dates), len(ids)))
    seen = np.zeros_like(dQ, dtype=bool)
    for j, e in enumerate(ids):
        s = odds[e]
        for t, d in enumerate(dates):
            p = prev.get(d)
            if p in s and d in s:
                dQ[t, j] = s[d] - s[p]
                seen[t, j] = True
    sharp = (np.abs(dQ) >= move) & seen
    rows = np.flatnonzero(sharp.any(axis=1))
    moves = {e: int(sharp[:, j].sum()) for j, e in enumerate(ids)}
    cols = [j for j, e in enumerate(ids) if moves[e] >= min_moves]
    readings: dict[str, DeltaReading] = {}
    if not cols or len(rows) < len(cols) + 2:
        return WindowFit(readings, [dates[i] for i in rows], moves)

    Z = fd.Z
    # Ordinary day-to-day noise of each factor, from days without sharp moves.
    calm = np.setdiff1d(np.arange(len(dates)), rows)
    noise = np.full(NF, 1 / np.sqrt(5))  # a typical move is weekly, so a calm day is about 1/sqrt(5) of one
    for f in range(NF):
        x = Z[calm, f]
        x = x[np.isfinite(x)]
        if len(x) > 30:  # a factor with no data keeps the default; it gets no reading anyway
            noise[f] = x.std(ddof=1)
    delta = np.full((len(ids), NF), np.nan)
    sigma = np.full((len(ids), NF), np.inf)
    skip = skip or {}
    left = {i for i in rows if any(dates[i] in days for days in skip.values())}
    for f in range(NF):
        r = np.array([i for i in rows if dates[i] not in skip.get(f, ())], dtype=int)
        y = Z[r, f]
        if not np.isfinite(y).all():
            continue
        # Events with enough sharp moves among this factor's windows: all of ``cols`` unless some were skipped.
        cf = [j for j in cols if sharp[r, j].sum() >= min_moves]
        if not cf or len(r) < len(cf) + 2:
            continue
        A = dQ[np.ix_(r, cf)]
        AtA_inv = np.linalg.pinv(A.T @ A)
        coef = AtA_inv @ (A.T @ y)
        dof = len(r) - len(cf)
        resid = y - A @ coef
        s2 = float(resid @ resid) / dof if dof >= 3 else noise[f] ** 2
        s2 = max(s2, noise[f] ** 2)  # never claim less noise than an ordinary day
        delta[cf, f] = coef
        sigma[cf, f] = np.sqrt(np.clip(np.diag(AtA_inv) * s2, 0, None))
    kept = f"; {len(left)} that span a weekend are left to {left_to}" if left else ""
    for j in cols:
        e = ids[j]
        readings[e] = DeltaReading(
            kind="etf", name="Jump windows",
            detail=f"{moves[e]} days with odds moves of {move * 100:.0f}+ points, out of {len(rows)} windows in {len(dates)} trading days{kept}",
            delta=delta[j], sigma=sigma[j], n=moves[e],
        )
    return WindowFit(readings, [dates[i] for i in rows], moves, len(left))
