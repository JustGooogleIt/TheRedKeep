"""Synthetic data for offline tests. Seeded random walks: none of this is market data."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from redkeep.factors import FACTORS

FRED_IDS = [f.fred for f in FACTORS if f.fred]
TICKERS = sorted({t for f in FACTORS for t in f.massive})


def business_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def factor_levels(days: list[date], seed: int = 1) -> dict[str, dict[date, float]]:
    """Random-walk levels for every factor proxy."""
    rng = np.random.default_rng(seed)
    n = len(days)
    out = {}
    start = {"SP500": 6000, "DGS10": 4.2, "T10YIE": 2.3, "DTWEXBGS": 120, "DCOILWTICO": 70, "BAMLH0A0HYM2": 3.0,
             "VIXCLS": 16, "SPY": 600, "FXI": 38, "SMH": 300, "GLD": 350}
    vol = {"SP500": 0.01, "DGS10": 0.06, "T10YIE": 0.025, "DTWEXBGS": 0.004, "DCOILWTICO": 0.02, "BAMLH0A0HYM2": 0.05,
           "VIXCLS": 1.0, "SPY": 0.01, "FXI": 0.016, "SMH": 0.018, "GLD": 0.009}
    for k, s0 in start.items():
        steps = rng.normal(0, vol[k], n)
        if k in ("DGS10", "T10YIE", "BAMLH0A0HYM2", "VIXCLS"):
            lv = np.maximum(s0 + np.cumsum(steps), 0.1)
        else:
            lv = s0 * np.exp(np.cumsum(steps))
        out[k] = dict(zip(days, lv.tolist()))
    return out


def fund_closes(fd, b_true: np.ndarray, noise: float = 0.2, seed: int = 2, p0: float = 100.0) -> dict[date, float]:
    """Closes whose daily % returns are Z · b_true + noise, on the factor dates."""
    rng = np.random.default_rng(seed)
    r = np.nan_to_num(fd.Z) @ b_true + rng.normal(0, noise, len(fd.dates))
    first = fd.dates[0] - timedelta(days=1)
    while first.weekday() >= 5:
        first -= timedelta(days=1)
    closes = {first: p0}
    p = p0
    for d, x in zip(fd.dates, r):
        p *= 1 + x / 100
        closes[d] = p
    return closes
