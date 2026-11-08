"""Hyperliquid weekend windows (DESIGN.md 4.5 and 6.4): a second market reading of each jump.

trade[XYZ]'s perps on the S&P 500, WTI oil and gold keep trading after their reference markets shut at 5 p.m.
New York time on Friday. Until those reopen at 6 p.m. on Sunday, the perps find their own price, so the weekend
is a window the ETF readings never see. On each weekend where at least one monitored market's odds moved
sharply, every covered factor's move (in typical moves) is regressed on all the odds changes over the same
stretch, as the ETF windows do on weekdays:

    r_f,w = Σ_k δ_k,f · Δq_k,w + noise

How a weekend move is measured (research note: docs/research/hyperliquid-noncrypto-perps.md):

* On trades, through hourly candles, not the oracle, which drifts toward the book with a 30-minute time constant.
* A boundary price is the median of the trade prices around it (the close before, the close at the boundary and
  the next open), so one bad print can't set it. With only two of them, they must agree to within 1%.
* Discovery bounds cap a weekend move: one step is ±1/max leverage around the Friday price, re-anchored a set
  number of times per direction, then a hard cap until the reopen. A move that comes within a quarter step of the
  cap is censored, a lower bound on the jump rather than a reading, so that factor's weekend is left out and
  counted.
* The error bars come from the weekends' own noise, which thin weekend books make wider than a weekday's.
* The ETF windows are daily, so Monday's window (Friday's close to Monday's) spans the weekend and sees the same
  news. For each factor the perps measure, the ETF regression leaves out the trading day after each weekend used
  here (``overlap``). The two readings then rest on separate data, so the quorum can count them as independent.

Left out for now: chips (NVDA is the only liquid chips perp, and one stock isn't the chips factor), the dollar
(EUR and JPY perps) and rates (TLT), which are marginal, and holidays, since CME trades part of most of them.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from ..data.hyperliquid import Candle, Hyperliquid
from ..engine.fusion import DeltaReading
from ..factors import NF, index

NY = ZoneInfo("America/New_York")
Points = list[tuple[datetime, float]]  # sorted by time
Legs = list[tuple[Points, float]]  # an event's odds as a weighted sum of markets


@dataclass(frozen=True)
class Perp:
    """A perp that measures a factor over weekends, with its discovery bounds (trade[XYZ]'s specification
    index, read 2026-11-08)."""
    factor: str
    name: str
    coins: tuple[str, ...]  # names to look for; the first one listed wins
    bound: float  # one discovery-bound step, as a fraction of the reference price
    resets: int  # re-anchors per direction before the hard cap

    @property
    def cap_up(self) -> float:
        return (1 + self.bound) ** (self.resets + 1) - 1

    @property
    def cap_down(self) -> float:
        return 1 - (1 - self.bound) ** (self.resets + 1)


PERPS: tuple[Perp, ...] = (
    Perp("eq", "S&P 500", ("xyz:SP500",), 0.02, 1),  # 50x: caps at +4.0% and -4.0%
    Perp("oil", "WTI", ("xyz:CL", "xyz:WTIOIL"), 0.05, 2),  # 20x: +15.8% and -14.3%
    Perp("gold", "gold", ("xyz:GOLD",), 0.04, 2),  # 25x: +12.5% and -11.5%
)


def weekends(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Each weekend's closed stretch inside ``start`` to ``end``: Friday 5 p.m. to Sunday 6 p.m. New York time, in UTC."""
    out = []
    d = start.astimezone(NY).date()
    d += timedelta(days=(4 - d.weekday()) % 7)  # the first Friday on or after start
    while True:
        a = datetime.combine(d, time(17), tzinfo=NY)
        b = datetime.combine(d + timedelta(days=2), time(18), tzinfo=NY)
        if b > end:
            return out
        if a >= start:
            out.append((a.astimezone(timezone.utc), b.astimezone(timezone.utc)))
        d += timedelta(days=7)


def odds_at(legs: Legs, t: datetime, *, max_age: timedelta = timedelta(hours=6)) -> float | None:
    """The event's odds at ``t``: each leg's last point at or before ``t`` (none older than ``max_age``), weighted."""
    if not legs:
        return None
    total = 0.0
    for pts, w in legs:
        i = bisect_right(pts, (t, float("inf"))) - 1
        if i < 0 or t - pts[i][0] > max_age:
            return None
        total += w * pts[i][1]
    return min(1.0, total)


def price_at(candles: list[Candle], ends: list[datetime], t: datetime, *, max_gap: timedelta = timedelta(hours=2),
             agree: float = 0.01) -> float | None:
    """The trade price at ``t``: the median of the close of the last candle to end by ``t``, the close before it and
    the open of the next candle. With all three, one bad print can't set the price; with two, they must agree to
    within ``agree``, or a bad print can't be told from a real move."""
    i = bisect_right(ends, t) - 1
    if i < 0 or t - candles[i].end > max_gap:
        return None
    xs = [candles[i].close]
    if i >= 1 and candles[i].end - candles[i - 1].end <= max_gap:
        xs.append(candles[i - 1].close)
    if i + 1 < len(candles) and candles[i + 1].start - t <= max_gap:
        xs.append(candles[i + 1].open)
    if len(xs) < 2 or (len(xs) == 2 and abs(xs[0] - xs[1]) > agree * min(xs)):
        return None
    return float(np.median(xs))


def at_bound(p: Perp, candles: list[Candle], starts: list[datetime], a: datetime, b: datetime, origin: float) -> bool:
    """Whether trading between ``a`` and ``b`` came within a quarter step of the discovery-bound cap."""
    inside = candles[bisect_left(starts, a):bisect_left(starts, b)]
    if not inside:
        return False
    up = max(c.high for c in inside) / origin - 1
    down = 1 - min(c.low for c in inside) / origin
    return up >= p.cap_up - p.bound / 4 or down >= p.cap_down - p.bound / 4


@dataclass
class PerpData:
    candles: dict[str, list[Candle]]  # by factor key
    coins: dict[str, str]  # by factor key
    warnings: list[str]


def load(hl: Hyperliquid, start: datetime, end: datetime) -> PerpData:
    """Hourly candles for each covered factor's perp, found by name among trade[XYZ]'s listings."""
    # A second reading, not a required one: any failure here is reported and the refresh goes on without it.
    try:
        listed = hl.coins("xyz")
    except Exception as e:
        return PerpData({}, {}, [f"Hyperliquid unavailable, so no weekend windows: {type(e).__name__}: {e}"])
    candles: dict[str, list[Candle]] = {}
    coins: dict[str, str] = {}
    warnings: list[str] = []
    for p in PERPS:
        coin = next((c for c in p.coins if c in listed), None)
        if not coin:
            warnings.append(f"Hyperliquid lists none of {', '.join(p.coins)}, so {p.name} has no weekend windows")
            continue
        lev = listed[coin].get("maxLeverage")
        try:
            step = 1 / float(lev)
        except (TypeError, ValueError, ZeroDivisionError):
            step = None
        if step is None or abs(step - p.bound) > 0.005:
            warnings.append(f"{coin} lists max leverage {lev}, so its discovery bounds may no longer be ±{p.bound:.1%} a step; "
                            f"check trade[XYZ]'s specs")
        try:
            candles[p.factor] = hl.candles(coin, start - timedelta(hours=6), end)
            coins[p.factor] = coin
        except Exception as e:
            warnings.append(f"No {coin} candles from Hyperliquid, so {p.name} has no weekend windows: {type(e).__name__}: {e}")
    return PerpData(candles, coins, warnings)


@dataclass
class WeekendFit:
    readings: dict[str, DeltaReading]
    weekends: int  # weekends in the span
    windows: list[date]  # Fridays of the weekends where some market moved sharply
    moves: dict[str, int]  # sharp weekend moves per event
    censored: list[tuple[date, str]]  # (Friday, factor) windows left out at the discovery bound
    used: dict[str, list[date]]  # Fridays of the weekends each factor's regression used


def weekend_windows(legs: dict[str, Legs], perps: PerpData, typical: np.ndarray, start: datetime, end: datetime, *,
                    move: float = 0.05, min_moves: int = 2) -> WeekendFit:
    W = weekends(start, end)
    ids = sorted(legs)
    dQ = np.zeros((len(W), len(ids)))
    seen = np.zeros_like(dQ, dtype=bool)
    for j, e in enumerate(ids):
        for w, (a, b) in enumerate(W):
            qa, qb = odds_at(legs[e], a), odds_at(legs[e], b)
            if qa is not None and qb is not None:
                dQ[w, j], seen[w, j] = qb - qa, True
    sharp = (np.abs(dQ) >= move) & seen
    hot = sharp.any(axis=1)
    rows, calm = np.flatnonzero(hot), np.flatnonzero(~hot)
    moves = {e: int(sharp[:, j].sum()) for j, e in enumerate(ids)}
    fridays = [a.astimezone(NY).date() for a, _ in W]

    # Each covered factor's move over each weekend, in typical moves; NaN without prices or at the bound.
    Y = np.full((len(W), NF), np.nan)
    censored: list[tuple[date, str]] = []
    for p in PERPS:
        cs = perps.candles.get(p.factor) or []
        if not cs:
            continue
        k = index(p.factor)
        ends, starts = [c.end for c in cs], [c.start for c in cs]
        for w, (a, b) in enumerate(W):
            pa, pb = price_at(cs, ends, a), price_at(cs, ends, b)
            if not pa or not pb:
                continue
            if at_bound(p, cs, starts, a, b, pa):
                if hot[w]:
                    censored.append((fridays[w], p.factor))
                continue
            Y[w, k] = (pb / pa - 1) * 100 / typical[k]

    delta = {e: np.full(NF, np.nan) for e in ids}
    sigma = {e: np.full(NF, np.inf) for e in ids}
    used: dict[str, list[date]] = {}
    for p in PERPS:
        k = index(p.factor)
        r = rows[np.isfinite(Y[rows, k])]
        # Events with enough sharp moves among this factor's usable weekends.
        cols = [j for j in range(len(ids)) if sharp[r, j].sum() >= min_moves]
        if not cols or len(r) < len(cols) + 2:
            continue
        x = Y[calm, k]
        x = x[np.isfinite(x)]
        noise = x.std(ddof=1) if len(x) >= 8 else 1 / np.sqrt(5)  # a calm stretch, about a day's worth of a weekly typical move
        A = dQ[np.ix_(r, cols)]
        AtA_inv = np.linalg.pinv(A.T @ A)
        y = Y[r, k]
        coef = AtA_inv @ (A.T @ y)
        dof = len(r) - len(cols)
        resid = y - A @ coef
        s2 = float(resid @ resid) / dof if dof >= 3 else noise ** 2
        s2 = max(s2, noise ** 2)  # never claim less noise than a calm weekend
        var = np.diag(AtA_inv) * s2
        for c, j in enumerate(cols):
            if var[c] > 0:
                delta[ids[j]][k], sigma[ids[j]][k] = coef[c], float(np.sqrt(var[c]))
        used[p.factor] = [fridays[w] for w in r]

    names = perp_names(used)
    cut = f"; {len(censored)} left out at a discovery bound" if censored else ""
    readings = {}
    for e in ids:
        if np.isfinite(sigma[e]).any():
            readings[e] = DeltaReading(
                kind="hl", name="Hyperliquid windows",
                detail=f"{names} perps: {moves[e]} weekends with odds moves of {move * 100:.0f}+ points, "
                       f"out of {len(rows)} windows in {len(W)} weekends{cut}",
                delta=delta[e], sigma=sigma[e], n=moves[e],
            )
    return WeekendFit(readings, len(W), [fridays[w] for w in rows], moves, censored, used)


def perp_names(factors) -> str:
    """The perps of the given factor keys, in PERPS order: "S&P 500, WTI and gold"."""
    xs = [p.name for p in PERPS if p.factor in factors]
    return ", ".join(xs[:-1]) + " and " + xs[-1] if len(xs) > 1 else "".join(xs)


def overlap(fit: WeekendFit, dates: list[date]) -> dict[int, set[date]]:
    """For each factor the perps measured, by factor index, the trading days whose daily window spans one of the
    weekends its regression used: the first trading day after each of those Fridays."""
    out: dict[int, set[date]] = {}
    for f, fridays in fit.used.items():
        days = {dates[i] for i in (bisect_right(dates, d) for d in fridays) if i < len(dates)}
        if days:
            out[index(f)] = days
    return out
