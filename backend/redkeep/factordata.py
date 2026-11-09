"""Factor moves from their proxies (DESIGN.md 4.1 and 6.1).

Levels come from FRED (the S&P 500, yields, breakevens, the dollar index, oil,
the high-yield spread, the VIX) and Massive (SPY, FXI, SMH, GLD). Daily changes
are taken on the stock market's trading days, so every factor's change on a day
spans the same two closes. A factor whose series has no level on either close
is missing (NaN) that day, and everything downstream treats it as "no
information". So a series that starts late (Massive's free plan keeps two years
of prices) or ends early (FRED publishes the dollar index about a week behind)
costs its own factor those days, not every factor. Where a factor has two
proxies (stocks: SPY, then the S&P 500 index), each day uses the first that has
both closes.
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


CALENDAR = ("SPY", "SP500")  # the stock market's proxies: their trading days are the calendar
COVER = 0.9  # typical moves come from the preferred proxy if it covers this share of the window
MAX_WEEK = 14  # calendar days: five closes further apart than two weeks span a gap in the series, not a week


def _options(f: Factor, have: set[str]) -> list[list[str]]:
    """The series that can measure this factor, in order of preference: Massive's tickers, then FRED's series."""
    if f.transform == "spread":
        return [list(f.massive)] if all(t in have for t in f.massive) else []
    return [[k] for k in (*f.massive[:1], *([f.fred] if f.fred else [])) if k in have]


def _describe(f: Factor, keys: list[str]) -> str:
    return " vs ".join(("Massive " if key in f.massive else "FRED ") + key for key in keys)


def _at(levels: dict[str, Series], keys: list[str], d: date) -> dict[str, float] | None:
    """The levels of ``keys`` on ``d``, if every one has a close that day."""
    try:
        return {k: levels[k][d] for k in keys}
    except KeyError:
        return None


def _trading_days(levels: dict[str, Series]) -> list[date]:
    """Every day either stock-market proxy has a close."""
    return sorted(set().union(*(levels.get(k) or () for k in CALENDAR)))


def _typical(f: Factor, opts: list[list[str]], levels: dict[str, Series], window: list[date]) -> float:
    """One standard deviation of the factor's weekly change over ``window``, from one proxy: the preferred one if it
    covers most of the window, else the one that covers the most (the S&P 500 index, for a window that ends soon
    after SPY's prices begin on Massive's free plan).

    Weeks don't overlap, and the variance is averaged over the five days a week can start on: two years hold only
    about a hundred weeks, so one start day can lump a few big days together or split them up, and moved stocks'
    typical move by a fifth on real data."""
    if not window:
        return float("nan")
    lo, hi = window[0], window[-1]
    cover = [sorted(d for d in set.intersection(*(set(levels[k]) for k in keys)) if lo <= d <= hi) for keys in opts]
    pick = 0 if len(cover[0]) >= COVER * len(window) else max(range(len(opts)), key=lambda j: len(cover[j]))
    keys, ds = opts[pick], cover[pick]
    var = []
    for start in range(5):
        weekly = [change(f, _at(levels, keys, ds[t - 5]), _at(levels, keys, ds[t]))
                  for t in range(5 + start, len(ds), 5) if (ds[t] - ds[t - 5]).days <= MAX_WEEK]
        weekly = [w for w in weekly if np.isfinite(w)]
        if len(weekly) >= 10:
            var.append(np.var(weekly, ddof=1))
    return float(np.sqrt(np.mean(var))) if var else float("nan")


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
        """Factors with a typical move and at least one day's change; an available factor can still miss days."""
        return np.isfinite(self.typical) & np.isfinite(self.X).any(axis=0)

    def level_on_or_before(self, key: str, d: date) -> tuple[date, float] | None:
        s = self.levels.get(key)
        if not s:
            return None
        ds = sorted(s)
        i = bisect_right(ds, d) - 1
        return (ds[i], s[ds[i]]) if i >= 0 else None

    def move(self, start: date, end: date) -> np.ndarray:
        """Each factor's change from the close on ``start`` to the close on ``end``, in typical moves.

        Uses the last level on or before each date, per series, from the first of the factor's proxies that has
        levels near both dates (before SPY's history begins, stocks are the S&P 500 index)."""
        out = np.full(NF, np.nan)
        have = {k for k, s in self.levels.items() if s}
        for k, f in enumerate(FACTORS):
            for keys in _options(f, have):
                a, b = {}, {}
                for key in keys:
                    la, lb = self.level_on_or_before(key, start), self.level_on_or_before(key, end)
                    if not la or not lb or la[0] >= lb[0] or (start - la[0]).days > 5 or (end - lb[0]).days > 5:
                        break
                    a[key], b[key] = la[1], lb[1]
                else:
                    out[k] = change(f, a, b) / self.typical[k]
                    break
        return out

    def calendar(self) -> list[date]:
        """The stock market's trading days, from either of its proxies, for event windows."""
        return _trading_days(self.levels) or list(self.dates)


def build(levels: dict[str, Series], *, typical_window: int = 520) -> FactorData:
    """Daily changes on the stock market's trading days, and typical moves over the last ``typical_window`` of them.

    Without either stock-market proxy, the days are the ones every series shares."""
    have = {k for k, s in levels.items() if s}
    plan = {f.key: _options(f, have) for f in FACTORS}
    used = sorted({k for opts in plan.values() for keys in opts for k in keys})
    if not used:
        raise ValueError("no factor data loaded")
    days = _trading_days(levels) or sorted(set.intersection(*(set(levels[k]) for k in used)))
    X = np.full((max(len(days) - 1, 0), NF), np.nan)
    typical = np.full(NF, np.nan)
    sources = {}
    for k, f in enumerate(FACTORS):
        opts = plan[f.key]
        if not opts:
            continue
        sources[f.key] = _describe(f, opts[0])
        for t in range(1, len(days)):
            for keys in opts:
                a, b = _at(levels, keys, days[t - 1]), _at(levels, keys, days[t])
                if a and b:
                    X[t - 1, k] = change(f, a, b)
                    break
        typical[k] = _typical(f, opts, levels, days[-typical_window:])
    return FactorData(levels=levels, dates=days[1:], X=X, typical=typical, sources=sources)


def load(fred: Fred, massive: Massive | None, start: date, end: date) -> FactorData:
    levels: dict[str, Series] = {}
    for i in (f.fred for f in FACTORS if f.fred):  # one at a time, so one bad series doesn't cost the rest
        try:
            levels.update(fred.series([i], start, end))
        except (HttpError, ValueError) as e:
            log.warning("FRED %s unavailable: %s", i, e)
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
