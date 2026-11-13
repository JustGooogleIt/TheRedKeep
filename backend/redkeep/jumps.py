"""Jump accuracy on resolved markets (DESIGN.md section 10, evals E3 to E5), with the method and metrics fixed here,
before any run.

When a market resolves, its odds jump to 0 or 1 and the factors move with them, so the day's factor move divided by
the day's odds change is the event's realized jump (section 10, "Ground truth for jumps"). The question is whether
the jump the engine measured the evening before, from data up to that close, matched it.

* **The sample.** Resolved yes/no markets of both venues that closed in the study's dates, listed as for the
  calibration study (E2): the busiest closed Polymarket events of each month under the engine's tags, and settled
  Kalshi markets of series in its categories, without sports, crypto, short-dated series or thin markets. They are
  sampled evenly by a hash of their ids. Each market's prices are read at every 4 p.m. New York close: hourly
  prices where the venue serves them, daily ones otherwise (stamped after the close, so they lag).
* **The resolution day.** The day the odds made their final move: from that close on they stay within ``DONE`` of
  the outcome, and at the close before they were at least ``SURPRISE`` away. A resolution everyone expected says
  nothing about the jump, because the factors had nothing left to price. One market counts per event (the one whose
  odds moved most that day), so a ladder that resolves at once counts once.
* **The prediction.** Jump windows on the market's own odds over the ``LOOKBACK`` days up to the close before, as in
  a refresh, in typical moves fixed before the study's first day. A market whose odds moved 5 points or more on
  fewer than three of those days gets no reading and is left out, and counted.

The metrics, over every (market, factor) with a reading and a factor move that day, all in typical moves:

* **E3, accuracy.** The share of the resolution-day factor moves that the predicted jumps explain,
  ``1 − Σ (z − Δq·δ̂)² / Σ z²``, where ``z`` is the day's move and ``Δq`` the day's odds change. It weights each
  market by its surprise, and no move at all (``δ̂ = 0``) scores 0. Next to it, two references: the same share
  when the factors a reading can't resolve (``|δ̂| < 2σ``) are set to no move, which is what a quorum-minded
  reader would act on, and the most any jump could explain, ``1 − Σ noise² / Σ z²``, since a day's move holds the
  factor's ordinary noise too. Then the sign of ``δ̂`` against the realized jump ``z / Δq`` where the move was
  clear (at least twice the factor's calm-day noise), and the rank correlation of ``δ̂`` with the realized jump.
  Ranges come from resampling whole resolution days, since markets that resolve on one day share its move.
* **E4, error bars.** The share of realized jumps inside the 80% interval, widened by the realized jump's own
  noise: ``|z/Δq − δ̂| ≤ 1.28 · √(σ² + (noise/Δq)²)``. Also the scale on ``σ`` that gives 80% on the earlier 70%
  of resolution days, and the coverage it gives on the rest.
* **E5, quorum value.** E3 and E4 for precise readings (an error bar on the jump's size of at most half of it, the
  rule for counting toward the quorum) against vague ones. Tiers proper need a second market reading, which past
  markets don't have yet.
* **E6, source ablation,** needs a second source on past events: no analogs are picked for them, and Hyperliquid's
  weekend windows reach back only about 200 days. The AI prior knows how most of them ended, but not those that
  resolved after its training data; E9 (``baseline.py``) scores the market's reading, the prior's and their fusion
  on those.

The AI-only baseline the design names for E3 runs there too, as E9.
"""

from __future__ import annotations

import hashlib
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Sequence

import numpy as np
from scipy.stats import spearmanr

from . import factordata, joint
from .engine.covariance import moves_corr
from .engine.fusion import QUORUM_SE, size_error
from .evidence import resolved, windows
from .evidence.resolved import Resolved
from .factordata import FactorData
from .factors import FACTORS, NF
from .http import HttpError
from .pipeline import Clients
from .replay import at_closes, window

log = logging.getLogger(__name__)
Progress = Callable[[str], None]

SURPRISE = 0.15  # the odds were at least this far from the outcome at the close before the resolution day
DONE = 0.05  # and within this of it from the resolution day's close on
LOOKBACK = 180  # days of odds and factor moves behind each prediction, as in a refresh
Z80 = 1.2815515655446004  # the 90th percentile of the standard normal: an 80% interval
CLEAR = 2.0  # a factor move counts as clear at this many calm-day standard deviations
RESOLVE = 2.0  # a reading resolves a factor's jump at this many standard errors from no move
SPLIT = 0.7  # the earlier share of resolution days that sets E4's scale
BOOT = 1000
PER_VENUE = 600  # markets read per venue
HOURLY = timedelta(hours=2)  # prices this close together, or closer, count as hourly
CALM_DEFAULT = 1 / math.sqrt(5)  # a typical move is weekly, so a calm day is about 1/sqrt(5) of one (as in windows.py)


@dataclass
class Case:
    """One resolved market, as the engine saw it the evening before it resolved, and what happened."""
    key: str
    venue: str
    event: str
    title: str
    category: str
    y: int
    day: str  # the resolution day (ISO)
    before: str  # the close before it
    q: float  # odds at the close before
    dq: float  # the day's odds change
    granularity: str
    n: int  # days of 5-point odds moves behind the reading
    windows: int
    pred: list  # δ̂ per factor, typical moves; None where the reading says nothing
    sigma: list
    z: list  # the day's factor moves, typical moves; None where there is no data
    noise: list  # each factor's calm-day standard deviation over the lookback
    cov: list | None = None  # the reading's covariance across factors (E11); None in runs from before it
    noise_corr: list | None = None  # the factors' correlation on calm days over the lookback (E11)

    @property
    def real(self) -> list:
        return [None if v is None else v / self.dq for v in self.z]


# -- the resolution day ----------------------------------------------------------------------------------


def resolution(closes: dict[date, float], y: int, calendar: Sequence[date]) -> tuple[date, date, float, float] | None:
    """``(before, day, q, dq)``: the day the odds made their final move to the outcome ``y``, the trading day
    before it, the odds at that close and the day's change; None if there was no such day."""
    days = [d for d in calendar if d in closes]
    if not days:
        return None
    k = len(days)
    while k > 0 and abs(closes[days[k - 1]] - y) <= DONE:
        k -= 1
    if k == len(days) or k == 0:
        return None  # never settled near the outcome, or was there from the start
    day = days[k]
    i = calendar.index(day)
    before = calendar[i - 1] if i > 0 else None
    if before is None or before not in closes or abs(closes[before] - y) < SURPRISE:
        return None
    return before, day, closes[before], closes[day] - closes[before]


def calm_days(fd: FactorData, busy: set[date]) -> np.ndarray:
    """The factor moves (typical moves) on days without a sharp odds move."""
    return fd.Z[[i for i, d in enumerate(fd.dates) if d not in busy]]


def calm_noise(fd: FactorData, busy: set[date]) -> np.ndarray:
    """Each factor's standard deviation on days without a sharp odds move, in typical moves."""
    keep = [i for i, d in enumerate(fd.dates) if d not in busy]
    Z = fd.Z[keep]
    out = np.full(NF, CALM_DEFAULT)
    for f in range(NF):
        x = Z[:, f]
        x = x[np.isfinite(x)]
        if len(x) > 30:
            out[f] = x.std(ddof=1)
    return out


def case_for(r: Resolved, points: Sequence[tuple[datetime, float]], granularity: str, fd: FactorData,
             calendar: Sequence[date]) -> Case | str:
    """The market's case, or why it has none."""
    closes = at_closes(points, calendar, listed=r.opened)
    res = resolution(closes, r.y, calendar)
    if res is None:
        return "no clear resolution day"
    before, day, q, dq = res
    at = {d: i for i, d in enumerate(fd.dates)}
    i = at.get(day)
    if i is None or i == 0 or fd.dates[i - 1] != before:
        return "no factor data that day"
    hist = {d: v for d, v in closes.items() if before - timedelta(days=LOOKBACK) < d <= before}
    past = window(fd, before, LOOKBACK)
    wf = windows.jump_windows({r.key: hist}, past)
    rd = wf.readings.get(r.key)
    if rd is None:
        return "too few days of 5-point odds moves"
    z = fd.Z[i]
    noise = calm_noise(past, set(wf.windows))
    ok = np.isfinite(rd.sigma) & np.isfinite(rd.delta)
    C = rd.covariance()
    R = moves_corr(calm_days(past, set(wf.windows)))
    return Case(
        key=r.key, venue=r.venue, event=r.event, title=r.title, category=r.category, y=r.y,
        day=day.isoformat(), before=before.isoformat(), q=float(q), dq=float(dq), granularity=granularity,
        n=rd.n, windows=len(wf.windows),
        pred=[float(v) if k else None for v, k in zip(rd.delta, ok)],
        sigma=[float(v) if k else None for v, k in zip(rd.sigma, ok)],
        z=[float(v) if np.isfinite(v) else None for v in z],
        noise=[float(v) for v in noise],
        cov=[[float(C[f, g]) if ok[f] and ok[g] else None for g in range(NF)] for f in range(NF)],
        noise_corr=[[float(x) for x in row] for row in R],
    )


def one_per_event(cases: Sequence[Case]) -> list[Case]:
    """The case with the biggest surprise in each event (ties by a hash of the market's id)."""
    best: dict[str, Case] = {}
    for c in sorted(cases, key=lambda c: (-abs(c.dq), hashlib.sha256(c.key.encode()).hexdigest())):
        best.setdefault(c.event, c)
    return sorted(best.values(), key=lambda c: (c.day, c.key))


# -- collection --------------------------------------------------------------------------------------------


def fetch(c: Clients, r: Resolved) -> tuple[list[tuple[datetime, float]], str]:
    """The market's prices over the lookback before it closed: hourly where the venue serves them, else daily."""
    a = r.closed - timedelta(days=LOOKBACK + 60)
    if r.opened is not None:
        a = max(a, r.opened - timedelta(days=1))
    b = r.closed + timedelta(days=1)
    tries: list[tuple[str, Callable[[], list]]] = []
    if r.venue == "polymarket" and r.token:
        tries = [("hourly", lambda: c.polymarket.lifetime(r.token, fidelity_min=60)),
                 ("daily", lambda: c.polymarket.lifetime(r.token, fidelity_min=1440))]
    elif r.venue == "kalshi" and r.series:
        tries = [("hourly", lambda: c.kalshi.history(r.series, r.id, a, b, period_min=60, archived=r.archived)),
                 ("daily", lambda: c.kalshi.daily(r.series, r.id, a, b, archived=r.archived))]
    for gran, get in tries:
        try:
            pts = sorted((t, q) for t, q in get() if a <= t <= b)
        except HttpError as e:
            log.info("%s %s prices unavailable: %s", gran, r.key, e)
            continue
        if len(pts) >= 2:
            gaps = np.diff([t.timestamp() for t, _ in pts])
            return pts, ("hourly" if np.median(gaps) <= HOURLY.total_seconds() else "daily")
    return [], "none"


def collect(c: Clients, fd: FactorData, start: date, end: date, *, per_venue: int = PER_VENUE,
            progress: Progress = print) -> tuple[list[Case], dict, list[str]]:
    """Every sampled market's case, both venues side by side, with the counts of what was left out and why."""
    calendar = fd.calendar()
    in_range = lambda r: start <= r.closed.date() <= end  # noqa: E731

    def venue(name: str) -> tuple[list[Case], dict]:
        if name == "polymarket":
            markets, n_events = resolved.list_polymarket(c.polymarket, start, end, keep=in_range, progress=progress)
            seen = {"events": n_events}
        else:
            markets, seen = resolved.list_kalshi(c.kalshi, start, end, total=per_venue, keep=in_range, progress=progress)
        picked = resolved.sample(markets, total=per_venue)
        label = "Polymarket" if name == "polymarket" else "Kalshi"
        progress(f"  {label}: {len(markets)} resolved markets; reading {len(picked)}")
        out, why = [], {}
        for i, r in enumerate(picked, 1):
            pts, gran = fetch(c, r)
            got = case_for(r, pts, gran, fd, calendar) if pts else "no prices"
            if isinstance(got, Case):
                out.append(got)
            else:
                why[got] = why.get(got, 0) + 1
            if i % 100 == 0 or i == len(picked):
                progress(f"  {label}: {i} of {len(picked)} read, {len(out)} with a reading the evening before")
        return out, {**seen, "markets": len(markets), "sampled": len(picked), "cases": len(out), "left_out": why}

    cases, stats, warnings = [], {}, []
    with ThreadPoolExecutor(2) as ex:  # the venues are different hosts
        futures = {name: ex.submit(venue, name) for name in ("polymarket", "kalshi")}
        for name, f in futures.items():
            try:
                got, st = f.result()
            except Exception as e:  # report it and keep the other venue
                warnings.append(f"{name}: {type(e).__name__}: {str(e)[:200]}")
                continue
            cases += got
            stats[name] = st
    kept = one_per_event(cases)
    stats["one_per_event"] = len(kept)
    return kept, stats, warnings


def factor_data(c: Clients, start: date, end: date) -> FactorData:
    """Factor data from the lookback before ``start`` to ``end``, in typical moves measured before ``start``."""
    fd = factordata.load(c.fred, c.massive, start - timedelta(days=LOOKBACK + 3 * 365 + 30), end + timedelta(days=5))
    typical = factordata.build({k: {d: v for d, v in s.items() if d < start} for k, s in fd.levels.items()}).typical
    return FactorData(fd.levels, fd.dates, fd.X, typical, fd.sources)


# -- the study ------------------------------------------------------------------------------------------------


@dataclass
class Pairs:
    """Every (market, factor) with a reading and a move, as arrays."""
    case: np.ndarray  # index into the cases
    f: np.ndarray
    day: np.ndarray  # resolution day, ISO
    pred: np.ndarray
    sigma: np.ndarray
    z: np.ndarray
    dq: np.ndarray
    noise: np.ndarray
    precise: np.ndarray  # the market's reading counts toward the quorum

    @property
    def real(self) -> np.ndarray:
        return self.z / self.dq

    @property
    def real_sd(self) -> np.ndarray:
        return self.noise / np.abs(self.dq)

    def take(self, m: np.ndarray) -> "Pairs":
        return Pairs(*(getattr(self, k)[m] for k in self.__dataclass_fields__))


def pairs(cases: Sequence[Case]) -> Pairs:
    rows = []
    for i, c in enumerate(cases):
        prec = np.array([0.0 if s is None else 1 / s ** 2 for s in c.sigma])
        precise = size_error(prec, np.array([0.0 if p is None else p for p in c.pred])) <= QUORUM_SE
        for f in range(NF):
            if c.pred[f] is not None and c.sigma[f] is not None and c.z[f] is not None:
                rows.append((i, f, c.day, c.pred[f], c.sigma[f], c.z[f], c.dq, c.noise[f], precise))
    cols = list(zip(*rows)) if rows else [[]] * 9
    return Pairs(np.array(cols[0], int), np.array(cols[1], int), np.array(cols[2], object), np.array(cols[3], float),
                 np.array(cols[4], float), np.array(cols[5], float), np.array(cols[6], float), np.array(cols[7], float),
                 np.array(cols[8], bool))


def explained(p: Pairs) -> float | None:
    ss = float(p.z @ p.z)
    return 1 - float(((p.z - p.dq * p.pred) ** 2).sum()) / ss if ss > 0 else None


def resolved_only(p: Pairs) -> Pairs:
    """The same pairs with no move predicted where the reading can't tell the factor's jump from none."""
    return replace(p, pred=np.where(np.abs(p.pred) >= RESOLVE * p.sigma, p.pred, 0.0))


def explainable(p: Pairs) -> float | None:
    """The most any jump could explain: the day's move holds the factor's calm-day noise too."""
    ss = float(p.z @ p.z)
    return 1 - float(p.noise @ p.noise) / ss if ss > 0 else None


def clear_signs(p: Pairs) -> tuple[int, int]:
    m = (np.abs(p.z) >= CLEAR * p.noise) & (p.pred != 0)
    return int((np.sign(p.pred[m]) == np.sign(p.real[m])).sum()), int(m.sum())


def coverage(p: Pairs, scale: float = 1.0) -> float | None:
    if not len(p.z):
        return None
    half = Z80 * np.sqrt((scale * p.sigma) ** 2 + p.real_sd ** 2)
    return float((np.abs(p.real - p.pred) <= half).mean())


def rank_corr(p: Pairs) -> float | None:
    if len(p.z) < 5 or np.std(p.pred) == 0 or np.std(p.real) == 0:
        return None
    return float(spearmanr(p.pred, p.real).statistic)


def by_day_ranges(p: Pairs, stat: Callable[[Pairs], float | None], *, n: int = BOOT, seed: int = 0) -> list[float] | None:
    """The 95% range of ``stat`` over resamples of whole resolution days."""
    days = np.unique(p.day)
    if len(days) < 5:
        return None
    idx = {d: np.flatnonzero(p.day == d) for d in days}
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        pick = np.concatenate([idx[d] for d in rng.choice(days, len(days))])
        v = stat(p.take(pick))
        if v is not None:
            vals.append(v)
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))] if vals else None


SCALES = (0.05, 50.0)  # the range fit_scale searches; at its floor, the day's noise alone covers the target


def fit_scale(p: Pairs, target: float = 0.8) -> float | None:
    """The scale on ``σ`` that puts ``target`` of the realized jumps inside the 80% interval. ``SCALES[0]`` means
    any width would do: around the predictions, the realized jumps' own noise already covers ``target``."""
    if len(p.z) < 20:
        return None
    lo, hi = SCALES
    if (coverage(p, hi) or 0) < target:
        return None  # the realized jumps' own noise leaves too many outside, whatever the error bars
    if (coverage(p, lo) or 0) >= target:
        return lo
    for _ in range(60):
        mid = math.sqrt(lo * hi)
        if (coverage(p, mid) or 0) >= target:
            hi = mid
        else:
            lo = mid
    return hi


def summary(p: Pairs) -> dict:
    right, n_clear = clear_signs(p)
    return {"pairs": int(len(p.z)), "markets": int(len(np.unique(p.case))), "days": int(len(np.unique(p.day))),
            "explained_share": explained(p), "explained_range": by_day_ranges(p, explained),
            "explained_share_resolved": explained(resolved_only(p)),
            "explained_resolved_range": by_day_ranges(p, lambda x: explained(resolved_only(x))),
            "resolved_pairs": int((np.abs(p.pred) >= RESOLVE * p.sigma).sum()),
            "explainable": explainable(p), "clear_moves": n_clear, "clear_signs_right": right,
            "clear_signs_range": by_day_ranges(p, lambda x: (lambda r, n: r / n if n else None)(*clear_signs(x))),
            "rank_corr": rank_corr(p), "coverage_80": coverage(p)}


def by_material(cases: Sequence[Case], p: Pairs, material: dict[str, bool]) -> dict:
    """E3 apart for the markets Claude's funnel labels call material and for the rest (added after the first run,
    which read mostly markets no factor would notice)."""
    key = np.array([cases[i].key for i in p.case], dtype=object)
    known = np.array([k in material for k in key], bool)
    mat = np.array([material.get(k, False) for k in key], bool)
    return {"labeled": sum(c.key in material for c in cases), "material_markets": sum(material.get(c.key, False) for c in cases),
            "material": summary(p.take(mat)), "other": summary(p.take(known & ~mat))}


def study(cases: Sequence[Case], material: dict[str, bool] | None = None) -> dict:
    p = pairs(cases)
    by_factor = {}
    for k, f in enumerate(FACTORS):
        q = p.take(p.f == k)
        if len(q.z):
            right, n_clear = clear_signs(q)
            by_factor[f.key] = {"pairs": int(len(q.z)), "explained_share": explained(q), "clear_moves": n_clear,
                                "clear_signs_right": right, "rank_corr": rank_corr(q), "coverage_80": coverage(q)}
    days = sorted(np.unique(p.day))
    cut = days[int(len(days) * SPLIT)] if len(days) >= 10 else None
    early, late = (p.take(p.day < cut), p.take(p.day >= cut)) if cut else (p, p.take(np.zeros(len(p.z), bool)))
    k = fit_scale(early) if cut else None
    return {
        "cases": len(cases), "by_venue": {v: sum(c.venue == v for c in cases) for v in ("polymarket", "kalshi")},
        "granularity": {g: sum(c.granularity == g for c in cases) for g in sorted({c.granularity for c in cases})},
        "e3": {**summary(p), "by_factor": by_factor},
        "e4": {"coverage_80": coverage(p), "by_factor": {key: v["coverage_80"] for key, v in by_factor.items()},
               "scale": {"split_day": cut, "fitted_on_pairs": int(len(early.z)), "scale": k,
                         "later_pairs": int(len(late.z)), "later_coverage": coverage(late),
                         "later_coverage_scaled": coverage(late, k) if k else None}},
        "e5": {"precise": summary(p.take(p.precise)), "vague": summary(p.take(~p.precise))},
        "e6": ("Not run here: it needs a second source of jump readings on past events. No analogs are picked for "
               "resolved markets, and Hyperliquid's weekend windows reach back only about 200 days. The AI prior "
               "knows how most of them ended; `redkeep baseline` (E9) scores it beside the market's reading on those "
               "that resolved after its training data."),
        **({"e3_by_material": by_material(cases, p, material)} if material else {}),
        "e11": joint.study(cases),
    }


def run(c: Clients, start: date, end: date, *, per_venue: int = PER_VENUE, progress: Progress = print) -> dict:
    fd = factor_data(c, start, end)
    missing = [f.name for f, t in zip(FACTORS, fd.typical) if not np.isfinite(t)]
    warnings = [f"No price data for {', '.join(missing)}, so the study leaves those factors out."] if missing else []
    progress(f"Factors: {NF - len(missing)} of {NF}, typical moves as of {start - timedelta(days=1)}")
    cases, stats, w = collect(c, fd, start, end, per_venue=per_venue, progress=progress)
    return report(cases, {"since": start.isoformat(), "until": end.isoformat(), "venues": stats},
                  warnings + w, typical=fd.typical)


def report(cases: Sequence[Case], sources: dict, warnings: list[str], *, typical=None,
           material: dict[str, bool] | None = None) -> dict:
    return {
        "kind": "redkeep.jumps", "version": 1, "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": sources,
        "method": {"surprise": SURPRISE, "done": DONE, "lookback_days": LOOKBACK, "clear": CLEAR, "resolve": RESOLVE,
                   "split": SPLIT, "interval": 0.8},
        "typical": None if typical is None else [None if v is None else float(v) for v in typical],
        **study(cases, material),
        "warnings": warnings,
        "rows": [asdict(c) for c in cases],
        **({"material": material} if material else {}),
    }


def cases_from(rep: dict) -> list[Case]:
    """The cases of a saved run, to rerun the study without fetching."""
    return [Case(**row) for row in rep["rows"]]
