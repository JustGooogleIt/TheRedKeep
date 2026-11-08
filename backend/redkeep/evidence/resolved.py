"""Resolved markets for the calibration study (DESIGN.md 6.4, eval E2): which ones, and their prices before the end.

* Polymarket: the busiest closed events of each month under the tags the engine prices (geopolitics, economy,
  finance, tech, politics), and their yes/no markets that resolved to exactly yes or no.
* Kalshi: settled markets of the series filed under the engine's categories (economics, financials, politics,
  world, elections, companies, science and technology), leaving out daily and hourly series and combination
  markets.

Sports, crypto, entertainment and weather are left out, and so are thin markets. Markets are sampled evenly by a
hash of their ids: a few per event, so that a 40-rung ladder or a 30-candidate race can't dominate, and a few dozen
per series or per tag and month. Kalshi's series are read in the same hashed order until the sample is full (and
at least 200 have been read), since there are thousands. Reruns pick the same markets.

Each market is seen at fixed horizons before its deadline (Polymarket's end date, Kalshi's expected expiration),
but only while it was still trading: listed, and not yet closed. Its price then is the last daily price at or
before that moment (or up to an hour after, for prices stamped just past midnight), if it is under three days old.
"""

from __future__ import annotations

import hashlib
import logging
from bisect import bisect_right
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Sequence

from ..http import HttpError
from ..markets import _json_list, _kalshi_count, _num, is_sports_polymarket, parse_time
from .calibration import HORIZONS, Obs

log = logging.getLogger(__name__)

PM_TAGS = (("geopolitics", "World"), ("economy", "Economics"), ("finance", "Financials"), ("tech", "Tech"),
           ("politics", "Politics"))  # the first tag an event carries sets its category
PM_SKIP = {"sports", "esports", "crypto", "crypto-prices", "bitcoin", "ethereum", "solana", "xrp", "dogecoin",
           "pop-culture", "culture", "entertainment", "music", "movies", "awards", "weather", "mentions"}
KS_CATEGORIES = ("Economics", "Financials", "Politics", "World", "Elections", "Companies", "Science and Technology")
KS_SHORT = {"hourly", "daily"}

MAX_AGE = timedelta(days=3)  # an older last price doesn't count as the price at the horizon
SLACK = timedelta(hours=1)  # daily prices are stamped a few seconds past 00:00 UTC


@dataclass(frozen=True)
class Resolved:
    venue: str
    id: str  # Polymarket market id, or Kalshi ticker
    event: str  # venue:event id
    group: str  # what the per-group cap counts: a Kalshi series, or a Polymarket category and month
    category: str
    title: str
    opened: datetime | None
    deadline: datetime
    closed: datetime
    y: int  # 1 if it resolved yes
    volume: float  # dollars on Polymarket, contracts on Kalshi
    token: str | None = None  # Polymarket's Yes token
    series: str | None = None  # Kalshi series
    archived: bool = False  # Kalshi: settled before the archive cutoff, so read from the historical endpoints

    @property
    def key(self) -> str:
        return f"{self.venue}:{self.id}"


# -- Polymarket ---------------------------------------------------------------------------------


def pm_category(ev: dict) -> str | None:
    """The event's category from its tags, or None to leave it out."""
    slugs = {str(t.get("slug") or "").lower() for t in ev.get("tags") or [] if isinstance(t, dict)}
    if is_sports_polymarket(ev) or slugs & PM_SKIP:
        return None
    return next((c for tag, c in PM_TAGS if tag in slugs), None)


def from_polymarket(ev: dict) -> list[Resolved]:
    """The event's yes/no markets that resolved to exactly yes or no."""
    category = pm_category(ev)
    if category is None:
        return []
    out = []
    for m in ev.get("markets") or []:
        if [str(o).lower() for o in _json_list(m.get("outcomes"))] != ["yes", "no"]:
            continue
        prices = [_num(p) for p in _json_list(m.get("outcomePrices"))]
        if prices not in ([1.0, 0.0], [0.0, 1.0]) or m.get("closed") is False:
            continue
        if str(m.get("umaResolutionStatus") or "resolved") != "resolved":
            continue
        tokens = [str(t) for t in _json_list(m.get("clobTokenIds"))]
        deadline = parse_time(m.get("endDate") or ev.get("endDate"))
        closed = parse_time(m.get("closedTime"))
        if not tokens or deadline is None or closed is None:
            continue
        out.append(Resolved(
            venue="polymarket", id=str(m.get("id")), event=f"polymarket:{ev.get('id')}",
            group=f"polymarket:{category}:{deadline:%Y-%m}", category=category,
            title=str(m.get("question") or ev.get("title") or ""),
            opened=parse_time(m.get("acceptingOrdersTimestamp") or m.get("startDate") or m.get("createdAt")),
            deadline=deadline, closed=closed, y=int(prices[0] == 1.0),
            volume=_num(m.get("volumeNum")) or _num(m.get("volume")) or 0.0, token=tokens[0],
        ))
    return out


# -- Kalshi ---------------------------------------------------------------------------------------


def keep_series(s: dict) -> bool:
    """A series filed first under one of the engine's categories, that isn't daily, hourly or a combination."""
    t, freq = str(s.get("ticker") or ""), str(s.get("frequency") or "").lower()
    short = freq in KS_SHORT or "min" in freq.split("_") or "minute" in freq
    return bool(t) and not t.startswith("KXMVE") and s.get("category") in KS_CATEGORIES and not short


def from_kalshi(m: dict, *, series: str, category: str, archived: bool) -> Resolved | None:
    """A settled yes/no market of ``series``. Event tickers start with their series' ticker, so a market of another
    series (were a filter ignored) is left out rather than counted under the wrong one."""
    if m.get("market_type", "binary") != "binary" or not str(m.get("event_ticker") or "").startswith(f"{series}-"):
        return None
    result = str(m.get("result") or "").lower()
    closed = parse_time(m.get("close_time"))
    deadline = parse_time(m.get("expected_expiration_time")) or closed
    if result not in ("yes", "no") or closed is None or deadline is None:
        return None
    return Resolved(
        venue="kalshi", id=str(m.get("ticker")), event=f"kalshi:{m.get('event_ticker')}", group=f"kalshi:{series}",
        category=category, title=str(m.get("title") or ""), opened=parse_time(m.get("open_time")),
        deadline=deadline, closed=closed, y=int(result == "yes"), volume=_kalshi_count(m, "volume"),
        series=series, archived=archived,
    )


# -- sampling and observations -----------------------------------------------------------------------


def _rank(m: Resolved) -> str:
    return hashlib.sha256(m.key.encode()).hexdigest()


def sample(markets: Sequence[Resolved], *, per_event: int = 3, per_group: int = 40, total: int = 1500) -> list[Resolved]:
    """An even, repeatable sample: at most ``per_event`` markets of an event and ``per_group`` of a group, then at
    most ``total`` in all, each chosen by a hash of the market's id."""
    ranked = sorted({m.key: m for m in markets}.values(), key=_rank)
    for cap, key in ((per_event, lambda m: m.event), (per_group, lambda m: m.group)):
        seen: dict[str, int] = defaultdict(int)
        kept = []
        for m in ranked:
            if seen[key(m)] < cap:
                seen[key(m)] += 1
                kept.append(m)
        ranked = kept
    return ranked[:total]


def open_at(r: Resolved, horizons: Sequence[int] = HORIZONS) -> list[int]:
    """The horizons at which the market was listed and not yet closed."""
    out = []
    for h in horizons:
        t = r.deadline - timedelta(days=h)
        if t < r.closed and (r.opened is None or t >= r.opened):
            out.append(h)
    return out


def observations(r: Resolved, prices: Sequence[tuple[datetime, float]], horizons: Sequence[int] = HORIZONS) -> list[Obs]:
    """The market's price at each horizon before its deadline, while it was still trading."""
    times = [t for t, _ in prices]
    out = []
    for h in open_at(r, horizons):
        t = r.deadline - timedelta(days=h)
        i = bisect_right(times, t + SLACK) - 1
        if i >= 0 and t - times[i] <= MAX_AGE:
            out.append(Obs(r.venue, r.key, r.event, r.category, r.closed.date(), h, float(prices[i][1]), r.y))
    return out


def window(r: Resolved, horizons: Sequence[int] = HORIZONS) -> tuple[datetime, datetime] | None:
    """The stretch of daily prices the horizons need, or None if the market wasn't trading at any of them."""
    hs = open_at(r, horizons)
    if not hs:
        return None
    start = r.deadline - timedelta(days=max(hs)) - MAX_AGE - timedelta(days=1)
    if r.opened is not None:
        start = max(start, r.opened)
    return start, min(r.closed, r.deadline - timedelta(days=min(hs)) + SLACK)


# -- collection ----------------------------------------------------------------------------------------


Progress = Callable[[str], None]


def months(start: date, end: date) -> list[tuple[date, date]]:
    out, a = [], date(start.year, start.month, 1)
    while a <= end:
        b = date(a.year + (a.month == 12), a.month % 12 + 1, 1)
        out.append((a, b))
        a = b
    return out


def _prices(fetch, picked: Sequence[Resolved], name: str, progress: Progress) -> tuple[list[Obs], int, int]:
    obs, priced, failed = [], 0, 0
    for i, r in enumerate(picked, 1):
        try:
            got = observations(r, fetch(r))
        except HttpError as e:
            failed += 1
            log.warning("%s %s: no prices (%s)", name, r.id, e)
            got = []
        priced += bool(got)
        obs += got
        if i % 100 == 0 or i == len(picked):
            progress(f"  {name}: prices for {i} of {len(picked)} markets, {len(obs)} observations")
    return obs, priced, failed


def collect_polymarket(pm, start: date, end: date, *, events_per_month: int = 20, min_volume: float = 10_000,
                       total: int = 1500, progress: Progress = print) -> tuple[list[Obs], dict]:
    events: dict[str, dict] = {}
    for a, b in months(start, end):
        for tag, _ in PM_TAGS:
            for ev in pm.closed_events(tag, a, b, limit=events_per_month):
                events.setdefault(str(ev.get("id")), ev)
    progress(f"  Polymarket: {len(events)} closed events")
    markets = [r for ev in events.values() for r in from_polymarket(ev)
               if r.volume >= min_volume and start <= r.closed.date() <= end and open_at(r)]
    picked = sample(markets, total=total)
    progress(f"  Polymarket: {len(markets)} resolved markets with ${min_volume:,.0f}+ volume; reading {len(picked)}")
    obs, priced, failed = _prices(lambda r: pm.daily(r.token), picked, "Polymarket", progress)
    return obs, {"events": len(events), "markets": len(markets), "sampled": len(picked), "priced": priced,
                 "failed": failed, "observations": len(obs)}


def collect_kalshi(ks, start: date, end: date, *, min_volume: float = 5_000, total: int = 1500, min_series: int = 200,
                   progress: Progress = print) -> tuple[list[Obs], dict]:
    series: dict[str, dict] = {}
    for cat in KS_CATEGORIES:
        for s in ks.series(cat):
            if keep_series(s):
                series.setdefault(s["ticker"], s)
    order = sorted(series, key=lambda t: hashlib.sha256(t.encode()).hexdigest())
    progress(f"  Kalshi: {len(series)} series in {len(KS_CATEGORIES)} categories")
    found: dict[str, Resolved] = {}
    read = 0
    for t in order:
        for archived in (True, False):  # archived first: a market listed in both is read from the archive
            try:
                rows = ks.settled(t, archived=archived)
            except HttpError as e:
                log.warning("Kalshi %s: no settled markets (%s)", t, e)
                continue
            for m in rows:
                r = from_kalshi(m, series=t, category=series[t]["category"], archived=archived)
                if r and r.volume >= min_volume and start <= r.closed.date() <= end and open_at(r):
                    found.setdefault(r.id, r)
        read += 1
        if read % 25 == 0 or read == len(order):
            full = len(sample(list(found.values()), total=total)) >= total
            progress(f"  Kalshi: settled markets of {read} of {len(order)} series, {len(found)} kept")
            if full and read >= min_series:
                break
    picked = sample(list(found.values()), total=total)
    progress(f"  Kalshi: {len(found)} settled markets with {min_volume:,.0f}+ contracts traded in {read} series; "
             f"reading {len(picked)}")
    obs, priced, failed = _prices(lambda r: ks.daily(r.series, r.id, *window(r), archived=r.archived), picked, "Kalshi", progress)
    return obs, {"series": len(series), "series_read": read, "markets": len(found), "sampled": len(picked),
                 "priced": priced, "failed": failed, "observations": len(obs)}


def collect(pm, ks, start: date, end: date, *, total: int = 1500, progress: Progress = print) -> tuple[list[Obs], dict, list[str]]:
    """Observations from both venues, read side by side (they are different hosts). A venue that fails is left out
    with a warning, and the study runs on the other."""
    jobs = {"polymarket": lambda: collect_polymarket(pm, start, end, total=total, progress=progress),
            "kalshi": lambda: collect_kalshi(ks, start, end, total=total, progress=progress)}
    obs, stats, warnings = [], {}, []
    with ThreadPoolExecutor(len(jobs)) as ex:
        futures = {name: ex.submit(fn) for name, fn in jobs.items()}
        for name, f in futures.items():
            try:
                o, st = f.result()
            except Exception as e:  # report it and keep the other venue
                warnings.append(f"{name}: {type(e).__name__}: {str(e)[:200]}")
                continue
            obs += o
            stats[name] = st
    return obs, stats, warnings
