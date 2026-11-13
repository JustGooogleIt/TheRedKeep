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

Every change spans two 4 p.m. closes. The Fed's dollar index is fixed at noon,
four hours before the others, and measured there about a fifth of each day's
dollar move lands on the next day's change (eval E10, DESIGN.md 4.8). That
shows up as stocks and yields "leading" the dollar by a day, and it shrinks
every dollar reading that pairs a same-day dollar move with a 4 p.m. price: a
fund's exposure, an event's jump. So ``load`` carries the index from noon to the
close with a dollar fund that trades there (``carry_to_close``).
"""

from __future__ import annotations

import logging
import math
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


CARRY_MIN = 120  # day pairs it takes to measure how far the carrying fund's moves carry a series
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


def carry_to_close(series: Series, close: Series, fixing: Series) -> tuple[Series, float] | None:
    """A series fixed before the 4 p.m. close, carried to the close by a fund that trades there.

    ``series`` is fixed at some hour (the Fed's dollar index, at noon), ``fixing`` is the fund's price at that hour
    and ``close`` its 4 p.m. close. The carried level is ``level × (close / fixing) ** β``: the series as fixed, plus
    the fund's move over the rest of the day scaled by ``β``, the series' move per unit of the fund's, both measured
    from one fixing to the next so they share a clock (a broad dollar index moves about 0.57 for each 1 of a dollar
    fund weighted to the euro). Days before the fund's fixing prices begin keep the series as it was, so a long
    history isn't cut short; after that, a day without them is left out, so no day's change mixes the clocks. None
    when the series and the fund share too few days to measure ``β``."""
    days = sorted(d for d in set(series) & set(close) & set(fixing) if series[d] > 0 and close[d] > 0 and fixing[d] > 0)
    pairs = [(a, b) for a, b in zip(days, days[1:]) if (b - a).days <= 5]
    if len(pairs) < CARRY_MIN:
        return None
    x = np.array([math.log(fixing[b] / fixing[a]) for a, b in pairs])
    y = np.array([math.log(series[b] / series[a]) for a, b in pairs])
    if float(x @ x) <= 0:
        return None
    beta = float(x @ y) / float(x @ x)
    out = {d: v for d, v in series.items() if d < days[0]}
    out.update({d: series[d] * (close[d] / fixing[d]) ** beta for d in days})
    return out, beta


def load(fred: Fred, massive: Massive | None, start: date, end: date) -> FactorData:
    levels: dict[str, Series] = {}
    for i in (f.fred for f in FACTORS if f.fred):  # one at a time, so one bad series doesn't cost the rest
        try:
            levels.update(fred.series([i], start, end))
        except (HttpError, ValueError) as e:
            log.warning("FRED %s unavailable: %s", i, e)
    carried: dict[str, str] = {}
    if massive is not None and massive.available:
        for t in sorted({t for f in FACTORS for t in (*f.massive, *([f.carry] if f.carry else []))}):
            try:
                levels[t] = massive.closes(t, start, end)
            except HttpError as e:  # for example, history beyond the plan's limit
                log.warning("Massive %s unavailable from %s: %s", t, start, e)
        for f in FACTORS:
            if not (f.carry and f.fixed and levels.get(f.fred) and levels.get(f.carry)):
                continue
            try:
                fixing = massive.at_hour(f.carry, start, end, hour=f.fixed)
            except HttpError as e:  # intraday bars can be outside the plan
                log.warning("Massive %s at %d:00 unavailable, so %s stays at its fixing time: %s", f.carry, f.fixed, f.fred, e)
                continue
            got = carry_to_close(levels[f.fred], levels[f.carry], fixing)
            if got is None:
                log.warning("Too few days of %s at %d:00 to carry %s to the close", f.carry, f.fixed, f.fred)
                continue
            levels[f.fred], beta = got
            carried[f.key] = f", carried from its {f.fixed}:00 fixing to the 4 p.m. close with Massive {f.carry} (β {beta:.2f})"
    fd = build(levels)
    for key, note in carried.items():
        fd.sources[key] = fd.sources.get(key, "") + note
    return fd


def window_bounds(dates: list[date], day: date, days: int) -> tuple[date, date] | None:
    """The close before ``day`` and the close ``days`` trading days later (inclusive of ``day``)."""
    i = bisect_left(dates, day)
    if i == 0 or i + days - 1 >= len(dates):
        return None
    return dates[i - 1], dates[i + days - 1]
