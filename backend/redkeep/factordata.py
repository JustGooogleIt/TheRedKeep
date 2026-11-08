"""Factor moves from their proxies (DESIGN.md 4.1 and 6.1).

Levels come from FRED (yields, breakevens, the dollar index, oil, the
high-yield spread, the VIX) and Massive (SPY, FXI, SMH, GLD). All series are
aligned on the dates they share, so every daily change spans the same two
closes. A factor whose proxy can't be loaded stays in the vector as missing
(NaN), and everything downstream treats it as "no information".
"""

from __future__ import annotations

import logging
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from .data.fred import Fred
from .data.massive import Massive
from .factors import FACTORS, NF, Factor
from .http import HttpError

log = logging.getLogger(__name__)
Series = dict[date, float]


def change(f: Factor, a: dict[str, float], b: dict[str, float]) -> float:
    """The factor's change from levels ``a`` to levels ``b``, in its natural unit."""
    if f.transform == "diff":
        return (b[f.fred] - a[f.fred]) * f.scale
    if f.transform == "ret":
        k = f.massive[0] if f.massive and f.massive[0] in a else f.fred
        return (b[k] / a[k] - 1) * 100 if a[k] > 0 else float("nan")
    x, y = f.massive
    return ((b[x] / a[x]) - (b[y] / a[y])) * 100


def _needs(f: Factor, have: set[str]) -> list[str] | None:
    """Which series measure this factor, preferring Massive tickers; None if unavailable."""
    if f.transform == "spread":
        return list(f.massive) if all(t in have for t in f.massive) else None
    if f.massive and f.massive[0] in have:
        return [f.massive[0]]
    if f.fred and f.fred in have:
        return [f.fred]
    return None


@dataclass
class FactorData:
    levels: dict[str, Series]  # raw proxy levels, by FRED id or ticker
    dates: list[date]  # dates of the daily changes (each from the previous date)
    X: np.ndarray  # (T, NF) daily changes in natural units; NaN where unavailable
    typical: np.ndarray  # (NF,) one standard deviation of the weekly change
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def Z(self) -> np.ndarray:
        """Daily changes in typical moves."""
        return self.X / self.typical

    @property
    def available(self) -> np.ndarray:
        return np.isfinite(self.X).all(axis=0) & np.isfinite(self.typical)

    def level_on_or_before(self, key: str, d: date) -> tuple[date, float] | None:
        s = self.levels.get(key)
        if not s:
            return None
        ds = sorted(s)
        i = bisect_right(ds, d) - 1
        return (ds[i], s[ds[i]]) if i >= 0 else None

    def move(self, start: date, end: date) -> np.ndarray:
        """Each factor's change from the close on ``start`` to the close on ``end``, in typical moves.

        Uses the last level on or before each date, per series."""
        out = np.full(NF, np.nan)
        for k, f in enumerate(FACTORS):
            keys = _needs(f, set(self.levels))
            if not keys:
                continue
            a, b = {}, {}
            for key in keys:
                la, lb = self.level_on_or_before(key, start), self.level_on_or_before(key, end)
                if not la or not lb or la[0] >= lb[0] or (start - la[0]).days > 5 or (end - lb[0]).days > 5:
                    break
                a[key], b[key] = la[1], lb[1]
            else:
                out[k] = change(f, a, b) / self.typical[k]
        return out

    def calendar(self) -> list[date]:
        """Trading days of the stock-market proxy, for event windows."""
        for key in ("SPY", "SP500"):
            if self.levels.get(key):
                return sorted(self.levels[key])
        return list(self.dates)


def build(levels: dict[str, Series], *, typical_window: int = 520) -> FactorData:
    """Align the series and compute daily changes and typical moves."""
    have = {k for k, s in levels.items() if s}
    plan = {f.key: _needs(f, have) for f in FACTORS}
    used = sorted({k for keys in plan.values() if keys for k in keys})
    if not used:
        raise ValueError("no factor data loaded")
    common = sorted(set.intersection(*(set(levels[k]) for k in used)))
    T = len(common) - 1
    X = np.full((max(T, 0), NF), np.nan)
    typical = np.full(NF, np.nan)
    sources = {}
    for k, f in enumerate(FACTORS):
        keys = plan[f.key]
        if not keys:
            continue
        sources[f.key] = " vs ".join(("Massive " if key in f.massive else "FRED ") + key for key in keys)
        lv = [{key: levels[key][d] for key in keys} for d in common]
        X[:, k] = [change(f, lv[t - 1], lv[t]) for t in range(1, len(common))]
        # Weekly changes, non-overlapping, over the most recent window.
        recent = lv[-typical_window:]
        weekly = [change(f, recent[t - 5], recent[t]) for t in range(5, len(recent), 5)]
        if len(weekly) >= 10:
            typical[k] = float(np.nanstd(weekly, ddof=1))
    return FactorData(levels=levels, dates=common[1:], X=X, typical=typical, sources=sources)


def load(fred: Fred, massive: Massive | None, start: date, end: date) -> FactorData:
    levels: dict[str, Series] = {}
    ids = [f.fred for f in FACTORS if f.fred]
    try:
        levels.update(fred.series(ids, start, end))
    except HttpError as e:
        log.warning("FRED unavailable: %s", e)
    if massive is not None and massive.available:
        for t in sorted({t for f in FACTORS for t in f.massive}):
            try:
                levels[t] = massive.closes(t, start, end)
            except HttpError as e:  # for example, history beyond the plan's limit
                log.warning("Massive %s unavailable from %s: %s", t, start, e)
    return build(levels)


def window_bounds(dates: list[date], day: date, days: int) -> tuple[date, date] | None:
    """The close before ``day`` and the close ``days`` trading days later (inclusive of ``day``)."""
    i = bisect_left(dates, day)
    if i == 0 or i + days - 1 >= len(dates):
        return None
    return dates[i - 1], dates[i + days - 1]
