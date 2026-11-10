"""Historical replay (DESIGN.md section 10, eval E8): would the engine have helped on real days?

An episode (``data/episodes/<name>.toml``) names a stretch of trading days, the events that mattered then, when each
one resolved, past events like them and proposed links. The replay rebuilds what the engine could have known each
evening, at the 4 p.m. New York close:

* **The markets.** The episode's events are matched by the same retrieval, judge and rules as a refresh, among the
  Polymarket and Kalshi markets listed at some point during the episode, closed ones included. The judge is told
  the episode's first evening as today's date. A market counts only from the first close after it was listed, and
  for an event that happened during the episode, only markets listed before it happened are candidates.
* **The odds.** Each matched market's archived prices, read at every close: the last price at or before 4 p.m.,
  if it is under four days old. Hourly prices are used where the venue still serves them, daily ones otherwise
  (stamped after the close, so they lag by most of a day). An event resolves at the first close after the moment
  the episode file says it happened, and its odds are 1 from then on.
* **The jumps.** Jump windows over the ``lookback`` days of odds and factor moves before each evening, re-estimated
  every evening on the data up to that close, plus the episode's past events, fused as in a refresh. The AI prior
  is left out: the model knows how the episode ended, so its estimates would carry hindsight. Hyperliquid's perps
  didn't trade yet, and options wait for M6, so the jump windows are the only market reading.
* **Typical moves** are measured once, on the two years before the episode, so every evening's jumps share a unit.

Then, each day, it compares the engine's view the evening before with the day's factor moves, all in typical moves:

* **Calls.** For an event that resolved that day, the move the engine expected if it happened: ``(1 − q) · δ``,
  with ``δ`` its total jump (its own plus the knock-on effects through confirmed links to events still open).
* **Explained moves.** Every day, the jumps times the day's odds changes: ``Σ_e δ_e · Δq_e``, each event's own jump
  only, since the odds of the events it moves are in the sum too. The difference from the day's move is what the
  engine's events don't account for.

The metrics are fixed here, before any run (``metrics``):

* calls: on each factor the engine had a reading for, whether the expected move has the right sign, and on stocks
  the ratio of the day's move to the expected one;
* explained moves, pooled over days and factors: the share of the moves' sum of squares they account for (one minus
  the sum of squared misses over the sum of squared moves, so a model that explains nothing scores 0 and one that
  makes things worse scores below), their correlation with the moves, and how often they have the right sign on days
  that moved a typical move or more;
* the ranking: at the first evening, the events by swing on stocks, ``|δ_stocks| · √(q (1 − q))``, beside the
  stock move on the day each one resolved.

With Massive's prices, the sample portfolios are scored too (E8's portfolio metric, ``portfolio_moves``). Each is
taken as it stood at the first evening: its holdings valued at that close, and its arrow ``b`` (% per typical move)
fitted by the exposure engine on the two years of prices up to it, with ETF holdings as ETF Global had them by then.
Each day then has the portfolio's own move (its buy-and-hold return from the previous close), the events' move
``b · Σ_e δ_e Δq_e``, and the factors' move ``b · z``, the arrow times the day's actual factor moves: all the events
could explain through the factors, since the rest is the portfolio's own noise. The metrics, fixed before the first
run: the share of the portfolio's daily moves that the events explain, as above, with the correlation and the days
of the right sign; the same share for the factors' move, the ceiling; and each call, ``(1 − q) · b · δ_total``,
against the portfolio's move that day.

The AI-only baseline the design names for E8 can't be run on a past episode for the same reason the prior is left
out; it runs prospectively instead, on events that resolve after the model's training (E9).
"""

from __future__ import annotations

import logging
import math
import tomllib
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from . import exposure, factordata
from .config import DATA_DIR, Settings
from .data.massive import MassiveUnavailable
from .engine.fusion import DeltaReading, FusedDelta, fuse_delta
from .events import EventSpec, LinkSpec, parse_event, parse_links
from .evidence import analogs, windows
from .evidence.odds import VENUE_NAMES
from .evidence.resolved import KS_CATEGORIES, keep_series
from .factordata import FactorData
from .factors import FACTORS, NF, index
from .http import HttpError
from .markets import Market, dedupe, from_kalshi_market, from_polymarket_event, is_sports_polymarket
from .matching.decide import Candidate, Decision, decide
from .matching.retrieve import BM25, Retriever, tokenize
from .pipeline import Clients, _market_json, check_link, finite, history_legs
from .portfolio import Sample, samples

log = logging.getLogger(__name__)
Progress = Callable[[str], None]

NY = ZoneInfo("America/New_York")
EPISODES = DATA_DIR / "episodes"
REPLAYS = DATA_DIR / "replays"
MAX_AGE = timedelta(days=4)  # covers a long weekend; an older last price doesn't count as the odds at a close
PM_TAGS = ("tariffs", "trade-war", "trump", "economy", "fed", "fed-rates", "recession", "china", "economic-policy",
           "politics", "business", "finance", "geopolitics")
SERIES_PER_EVENT = 20  # Kalshi series read per event, best first by their titles (about a hundred mention tariffs)
EQ = index("eq")
BIG = 1.0  # a day that moved a factor a typical move or more


# -- the episode ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodeEvent:
    spec: EventSpec
    happened: datetime | None = None  # when it resolved yes, if it did during the episode


@dataclass(frozen=True)
class Episode:
    name: str
    title: str
    sub: str
    start: date
    end: date
    lookback: int
    events: tuple[EpisodeEvent, ...]
    links: tuple[LinkSpec, ...]
    notes: dict[date, str]


def _utc(x) -> datetime | None:
    if x is None:
        return None
    if not isinstance(x, datetime) or x.tzinfo is None:
        raise ValueError(f"`happened` needs a time with its offset, such as 2025-04-02T20:10:00Z (got {x!r})")
    return x.astimezone(timezone.utc)


def load_episode(name: str | Path) -> Episode:
    path = Path(name) if str(name).endswith(".toml") else EPISODES / f"{name}.toml"
    raw = tomllib.loads(path.read_text())
    events = tuple(EpisodeEvent(parse_event(e, i), _utc(e.get("happened"))) for i, e in enumerate(raw.get("event", [])))
    ids = {e.spec.id for e in events}
    if len(ids) != len(events):
        raise ValueError(f"{path.name}: duplicate event ids")
    start, end = raw["start"], raw["end"]
    for e in events:
        if e.happened and not (close_time(start - timedelta(days=4)) < e.happened <= close_time(end)):
            raise ValueError(f"{path.name}: {e.spec.id} happened outside the episode")
    notes = {n["day"]: n["note"] for n in raw.get("notes", [])}
    return Episode(path.stem, raw["title"], raw.get("sub", ""), start, end, int(raw.get("lookback_days", 180)), events,
                   tuple(parse_links(raw.get("link", []), ids)), notes)


def close_time(d: date) -> datetime:
    """4 p.m. New York time on ``d``, in UTC."""
    return datetime.combine(d, time(16), NY).astimezone(timezone.utc)


def at_closes(points: Sequence[tuple[datetime, float]], days: Sequence[date], *, listed: datetime | None = None,
              max_age: timedelta = MAX_AGE) -> dict[date, float]:
    """The last price at or before each day's close, once the market was listed and while the price is under
    ``max_age``. Unlike ``windows.at_close`` it carries a price forward, so it also reads a venue's daily points."""
    pts = sorted(points)
    times = [t for t, _ in pts]
    out = {}
    for d in days:
        c = close_time(d)
        if listed is not None and listed > c:
            continue
        i = bisect_right(times, c) - 1
        if i >= 0 and c - times[i] <= max_age:
            out[d] = pts[i][1]
    return out


def resolution_day(happened: datetime | None, days: Sequence[date]) -> int | None:
    """The index of the first day whose close comes after ``happened``: the day whose move contains it."""
    if happened is None:
        return None
    return next((i for i, d in enumerate(days) if close_time(d) >= happened), None)


# -- markets as they were listed --------------------------------------------------------------------------


def pick_series(series: dict[str, dict], specs: Sequence[EventSpec], per_event: int = SERIES_PER_EVENT) -> list[str]:
    """Kalshi series whose titles best match each event's terms (there are thousands)."""
    tickers = sorted(series)
    if not tickers:
        return []
    bm = BM25([tokenize(" ".join([str(series[t].get("title") or ""), *map(str, series[t].get("tags") or []), t]))
               for t in tickers])
    out: set[str] = set()
    for spec in specs:
        s = bm.scores(tokenize(" ".join([spec.title, *spec.search])))
        out.update(tickers[i] for i in np.argsort(-s)[:per_event] if s[i] > 0)
    return sorted(out)


@dataclass
class Catalogue:
    markets: list[Market]
    archived: set[str]  # Kalshi tickers read from the archive
    counts: dict[str, int]
    warnings: list[str]


def catalogue(c: Clients, ep: Episode, base: date, progress: Progress) -> Catalogue:
    """The markets on both venues that were listed at some point between the first evening and the last close,
    closed ones included."""
    warnings: list[str] = []
    events: dict[str, dict] = {}

    def keep(evs):
        for ev in evs:
            events.setdefault(str(ev.get("id")), ev)

    for tag in PM_TAGS:
        for closed in (True, False):
            try:
                keep(c.polymarket.listed_events(tag, base, ep.end, closed=closed))
            except HttpError as e:
                warnings.append(f"Polymarket tag {tag}: {e}")
    for q in sorted({q for e in ep.events for q in e.spec.search}):
        for search in (c.polymarket.search_closed, c.polymarket.search):
            try:
                keep(search(q))
            except HttpError as e:
                warnings.append(f"Polymarket search {q!r}: {e}")
    pm = [m for ev in events.values() if not is_sports_polymarket(ev) for m in from_polymarket_event(ev, closed=True)]

    series: dict[str, dict] = {}
    for cat in KS_CATEGORIES:
        try:
            series.update({s["ticker"]: s for s in c.kalshi.series(cat) if keep_series(s)})
        except HttpError as e:
            warnings.append(f"Kalshi category {cat}: {e}")
    ks: dict[str, Market] = {}
    archived: set[str] = set()
    for t in pick_series(series, [e.spec for e in ep.events]):
        for arch in (True, False):  # archived first: a market listed in both is read from the archive
            try:
                rows = c.kalshi.settled(t, archived=arch)
            except HttpError as e:
                warnings.append(f"Kalshi series {t}: {e}")
                continue
            for row in rows:
                m = from_kalshi_market(row, series=t, event_title=str(series[t].get("title") or ""),
                                       category=series[t].get("category"))
                if m is not None and m.id not in ks:
                    ks[m.id] = m
                    if arch:
                        archived.add(m.id)
    lo, hi = close_time(base), close_time(ep.end)
    during = [m for m in dedupe(pm + list(ks.values())) if (m.opened is None or m.opened <= hi) and (m.close_time is None or m.close_time >= lo)]
    counts = {"polymarket": sum(m.venue == "polymarket" for m in during), "kalshi": sum(m.venue == "kalshi" for m in during),
              "kalshi_series": len(series)}
    progress(f"Markets listed during the episode: {counts['polymarket']:,} on Polymarket, {counts['kalshi']:,} on Kalshi")
    return Catalogue(during, archived, counts, warnings)


# -- prices ----------------------------------------------------------------------------------------------


@dataclass
class Prices:
    points: list[tuple[datetime, float]]  # the market's Yes price, oldest first
    granularity: str  # "hourly" or "daily"


def prices(c: Clients, m: Market, start: datetime, end: datetime, *, archived: bool, hourly: bool) -> Prices:
    """A market's archived Yes prices: hourly where the venue still serves them (``hourly``), else daily."""
    tries: list[tuple[str, Callable[[], list]]] = []
    if m.venue == "polymarket" and m.token_yes:
        tok = m.token_yes
        if hourly:
            tries += [("hourly", lambda: c.polymarket.history(tok, start, end)),
                      ("hourly", lambda: c.polymarket.lifetime(tok, fidelity_min=60))]
        tries.append(("daily", lambda: c.polymarket.lifetime(tok, fidelity_min=1440)))
    elif m.venue == "kalshi" and m.series:
        if hourly:
            tries.append(("hourly", lambda: c.kalshi.history(m.series, m.id, start, end, archived=archived)))
        tries.append(("daily", lambda: c.kalshi.daily(m.series, m.id, start, end, archived=archived)))
    for gran, fetch in tries:
        try:
            pts = [(t, q) for t, q in fetch() if start <= t <= end]
        except HttpError as e:
            log.info("%s %s prices unavailable: %s", gran, m.key, e)
            continue
        if pts:
            return Prices(sorted(pts), gran)
    return Prices([], "none")


# -- each evening's evidence --------------------------------------------------------------------------------


def window(fd: FactorData, until: date, days: int) -> FactorData:
    """The factor data of the ``days`` calendar days up to and including ``until``."""
    keep = [i for i, d in enumerate(fd.dates) if until - timedelta(days=days) < d <= until]
    return replace(fd, dates=[fd.dates[i] for i in keep], X=fd.X[keep])


def total_jump(eid: str, direct: dict[str, np.ndarray], links: dict[tuple[str, str], float], open_: set[str],
               seen: frozenset = frozenset()) -> np.ndarray:
    """``δ_total(A) = δ(A) + Σ_B w_AB δ_total(B)`` over confirmed links to events still open (DESIGN.md 4.4)."""
    out = np.nan_to_num(direct[eid]).copy()
    for (p, ch), w in links.items():
        if p == eid and ch in open_ and ch in direct and ch not in seen:
            out += w * total_jump(ch, direct, links, open_, seen | {eid})
    return out


@dataclass
class Evening:
    """The engine's view at one close: each event's odds and jump, and the links the data confirmed."""
    day: date
    q: dict[str, float]  # listed events; 1.0 once resolved
    delta: dict[str, np.ndarray]  # events with any jump reading
    sigma: dict[str, np.ndarray]
    readings: dict[str, list[str]]
    links: dict[tuple[str, str], float]  # confirmed links, with the weight used
    link_status: dict[tuple[str, str], str]


def evening(T: date, ep: Episode, odds: dict[str, dict[date, float]], fd: FactorData,
            past: dict[str, DeltaReading | None], s: Settings) -> Evening:
    lo = T - timedelta(days=ep.lookback)
    hist = {e: {d: q for d, q in h.items() if lo < d <= T} for e, h in odds.items()}
    wf = windows.jump_windows({e: h for e, h in hist.items() if h}, window(fd, T, ep.lookback), move=s.window_move)
    q, delta, sigma, names = {}, {}, {}, {}
    for e in ep.events:
        eid = e.spec.id
        if T in hist.get(eid, {}):
            q[eid] = hist[eid][T]
        rs = [r for r in (wf.readings.get(eid), past.get(eid)) if r is not None]
        if rs:
            f: FusedDelta = fuse_delta(rs)
            delta[eid], sigma[eid] = np.asarray(f.delta, float), np.asarray(f.sigma, float)
            names[eid] = [f"{r.name} ({r.n})" for r in rs]
    links, status = {}, {}
    for ln in ep.links:
        chk = check_link(hist.get(ln.parent, {}), hist.get(ln.child, {}))
        key = (ln.parent, ln.child)
        if chk is None:
            status[key] = "untestable"
        elif chk["t"] * math.copysign(1, ln.w) < 2:
            status[key] = "unconfirmed"
        else:
            status[key] = "confirmed"
            links[key] = max(-1.0, min(1.0, chk["w_data"]))
    return Evening(T, q, delta, sigma, names, links, status)


# -- the replay ----------------------------------------------------------------------------------------


@dataclass
class Matched:
    event: EpisodeEvent
    decision: Decision
    candidates: int


def listed_before(markets: Sequence[Market], t: datetime | None) -> list[Market]:
    """The markets listed before ``t``. A market listed after an event happened can't have priced it, and its rules
    often say what happened."""
    return list(markets) if t is None else [m for m in markets if m.opened is None or m.opened < t]


def _match(c: Clients, s: Settings, ep: Episode, cat: Catalogue, base: date, closes: list[date], start: datetime,
           end: datetime, progress: Progress) -> list[Matched]:
    """Match every event among the markets listed during the episode. Each candidate's price for the decision is its
    price at the first close it was listed for; its odds each evening come from its own history later."""
    got = {e.spec.id: Retriever(listed_before(cat.markets, e.happened)).candidates(e.spec, k=s.candidates_per_event)
           for e in ep.events}
    priced: dict[str, Market] = {}
    for m in dedupe(m for g in got.values() for m, _ in g):
        p = prices(c, m, start, end, archived=m.id in cat.archived, hourly=False)
        q = next(iter(at_closes(p.points, closes, listed=m.opened).values()), None)
        priced[m.key] = replace(m, bid=None, ask=None, last=q)  # archived prices carry no quotes

    def one(e: EpisodeEvent) -> Matched:
        ms = [priced[m.key] for m, _ in got[e.spec.id]]
        verdicts = c.judge.judge(e.spec, ms, base)
        cands = [Candidate(m, v, r) for m, (_, r), v in zip(ms, got[e.spec.id], verdicts)]
        return Matched(e, decide(e.spec, cands, s), len(cands))

    with ThreadPoolExecutor(max_workers=4) as pool:
        out = list(pool.map(one, ep.events))
    for mt in out:
        progress(f"  {mt.event.spec.short:<18} {mt.decision.kind:<6} {mt.decision.note}")
    return out


def _legs(c: Clients, d: Decision, cat: Catalogue, start: datetime, end: datetime) -> tuple[list[tuple[Candidate, float, Prices]], str | None]:
    """The markets whose prices stand in for the event's odds, with their weights. A refresh takes the busiest
    market that asks the same question; the replay prefers hourly prices, which line up with the closes."""
    legs = history_legs(d)
    if not legs:
        return [], None
    fetched = {cand.market.key: prices(c, cand.market, start, end, archived=cand.market.id in cat.archived, hourly=True)
               for cand in d.used}
    if d.kind == "match" and len(legs) == 1:
        best = max(d.used, key=lambda x: (fetched[x.market.key].granularity == "hourly", len(fetched[x.market.key].points),
                                          x.market.volume))
        legs = [(best, 1.0)]
    out = [(cand, w, fetched[cand.market.key]) for cand, w in legs]
    m = out[0][0].market
    venue = VENUE_NAMES.get(m.venue, m.venue)
    what = (f"Average of {len(out)} rungs of the {venue} ladder" if d.kind == "ladder"
            else f"Sum of {len(out)} outcomes on {venue}" if len(out) > 1 else f"{venue}: {m.title}")
    return out, what


def odds_path(legs: list[tuple[Candidate, float, Prices]], days: Sequence[date], happened: datetime | None) -> dict[date, float]:
    """The event's odds at each close: the legs' weighted prices on the event's side, on the closes where every leg
    has one, and 1 from the first close after the event happened."""
    series = []
    for cand, w, p in legs:
        side = [(t, 1 - x if cand.verdict.polarity == "no" else x) for t, x in p.points]
        series.append((at_closes(side, days, listed=cand.market.opened), w))
    common = set.intersection(*(set(s) for s, _ in series)) if series else set()
    out = {d: min(1.0, max(0.0, sum(w * s[d] for s, w in series))) for d in sorted(common)}
    if happened is not None:
        out.update({d: 1.0 for d in days if close_time(d) >= happened})
    return dict(sorted(out.items()))


def run(s: Settings, c: Clients, ep: Episode, *, progress: Progress = print) -> dict:
    if c.judge is None:
        raise RuntimeError("No Anthropic key (set REDKEEP_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY): "
                           "the replay matches its events with the same judge as a refresh")
    warnings: list[str] = []

    # Factor data: the episode, the lookback before it, two years more for typical moves, and the analogs.
    first = min([a.date for e in ep.events for a in e.spec.analogs] + [ep.start - timedelta(days=3 * 365 + ep.lookback)])
    fd = factordata.load(c.fred, c.massive, first - timedelta(days=14), ep.end)
    cal = fd.calendar()
    base = max(d for d in cal if d < ep.start)
    days = [d for d in cal if ep.start <= d <= ep.end]
    typical = factordata.build({k: {d: v for d, v in lv.items() if d <= base} for k, lv in fd.levels.items()}).typical
    fd = replace(fd, typical=typical)  # every evening's jumps are in the typical moves known before the episode
    missing = [f.name for f, t in zip(FACTORS, typical) if not np.isfinite(t)]
    if missing:
        warnings.append(f"No price data for {', '.join(missing)}, so the replay leaves those factors out.")
    progress(f"Factors: {NF - len(missing)} of {NF}; {len(days)} trading days from {days[0]} to {days[-1]}, first evening {base}")

    closes = [d for d in cal if base - timedelta(days=ep.lookback) < d <= ep.end]
    start = close_time(base) - timedelta(days=ep.lookback + 1)
    end = close_time(ep.end) + timedelta(hours=1)

    cat = catalogue(c, ep, base, progress)
    warnings += cat.warnings
    matched = _match(c, s, ep, cat, base, [d for d in closes if d >= base], start, end, progress)

    odds: dict[str, dict[date, float]] = {}
    sources: dict[str, dict] = {}
    for mt in matched:
        eid = mt.event.spec.id
        legs, what = _legs(c, mt.decision, cat, start, end)
        odds[eid] = odds_path(legs, closes, mt.event.happened) if legs else {}
        sources[eid] = {"from": what, "granularity": sorted({p.granularity for _, _, p in legs}),
                        "markets": [{"key": cand.market.key, "title": cand.market.title, "url": cand.market.url, "weight": w,
                                     "polarity": cand.verdict.polarity, "granularity": p.granularity, "points": len(p.points),
                                     "listed": cand.market.opened.isoformat() if cand.market.opened else None}
                                    for cand, w, p in legs]}
        if legs and all(p.granularity == "daily" for _, _, p in legs):
            warnings.append(f"{eid}: only daily prices, stamped after the close, so its odds lag the closes by most of a day")
        if mt.decision.kind == "bound":
            warnings.append(f"{eid}: only bounded by related markets ({mt.decision.note.split(': ', 1)[-1].rstrip('.')}), "
                            "and a bound has no odds path, so the replay has no odds for it")
        elif mt.decision.kind != "none" and not odds[eid]:
            warnings.append(f"{eid}: matched, but its market has no prices in the episode")

    past = {e.spec.id: analogs.analog_reading(e.spec, fd) for e in ep.events}
    evenings = [evening(T, ep, odds, fd, past, s) for T in [base] + days]
    progress(f"Evenings: {len(evenings)}, from {base} to {days[-1]}")
    rep = assemble(ep, s, c, base, days, fd, typical, matched, odds, sources, evenings, warnings, cat.counts)
    rep["portfolios"], more = portfolio_moves(s, c, fd, rep)
    rep["warnings"] += more
    if rep["portfolios"]:
        progress(f"Portfolios: {len(rep['portfolios'])}, as they stood at the close of {base}")
    return finite(rep)


# -- comparing calls with what happened ----------------------------------------------------------------


def day_moves(fd: FactorData, days: Sequence[date], prev: date) -> tuple[np.ndarray, list[str]]:
    """Each day's factor moves in natural units, from the previous close: NaN where a series has no close."""
    out = np.full((len(days), NF), np.nan)
    gaps = []
    at = {d: i for i, d in enumerate(fd.dates)}
    for k, d in enumerate(days):
        p = prev if k == 0 else days[k - 1]
        i = at.get(d)
        if i is not None and i > 0 and fd.dates[i - 1] == p:
            out[k] = fd.X[i]
        else:
            gaps.append(d.isoformat())
    return out, gaps


def explained(ev: Sequence[Evening], days: Sequence[date]) -> np.ndarray:
    """``Σ_e δ_e · Δq_e`` for each day, with each event's jump as of the evening before (typical moves)."""
    out = np.zeros((len(days), NF))
    for i in range(len(days)):
        before, after = ev[i], ev[i + 1]
        for eid, dlt in before.delta.items():
            if eid in before.q and eid in after.q:
                out[i] += np.nan_to_num(dlt) * (after.q[eid] - before.q[eid])
    return out


def swing_stocks(dlt: np.ndarray, q: float) -> float:
    return abs(float(np.nan_to_num(dlt[EQ]))) * math.sqrt(q * (1 - q))


def metrics(calls: list[dict], expl: np.ndarray, real_z: np.ndarray, has: np.ndarray, ranking: list[dict]) -> dict:
    """The E8 metrics, fixed before any run (see the module's docstring)."""
    signs = [(c["pred"][k], c["real"][k]) for c in calls for k in range(NF)
             if c["has"][k] and c["pred"][k] is not None and c["real"][k] is not None and c["pred"][k] != 0]
    right = sum(np.sign(p) == np.sign(r) for p, r in signs)
    stock = [{"event": c["event"], "pred": c["pred"][EQ], "real": c["real"][EQ],
              "ratio": c["real"][EQ] / c["pred"][EQ] if c["pred"][EQ] else None} for c in calls
             if c["pred"][EQ] is not None and c["real"][EQ] is not None]
    ok = has[None, :] & np.isfinite(real_z) & np.isfinite(expl)
    y, x = real_z[ok], expl[ok]
    ss = float(y @ y)
    big = ok & (np.abs(real_z) >= BIG) & (np.abs(expl) > 0)
    per = {}
    for k, f in enumerate(FACTORS):
        col = ok[:, k]
        if col.sum() >= 3:
            yk, xk = real_z[col, k], expl[col, k]
            per[f.key] = {"days": int(col.sum()), "explained_share": 1 - float(((yk - xk) ** 2).sum()) / float(yk @ yk) if yk @ yk else None,
                          "corr": _corr(xk, yk)}
    return {
        "calls": {"n": len(calls), "factor_signs": len(signs), "factor_signs_right": int(right),
                  "stocks": stock},
        "explained": {"cells": int(ok.sum()), "explained_share": 1 - float(((y - x) ** 2).sum()) / ss if ss else None,
                      "corr": _corr(x, y), "big_days": int(big.sum()),
                      "big_days_right": int((np.sign(real_z[big]) == np.sign(expl[big])).sum()), "by_factor": per},
        "ranking": ranking,
    }


def _corr(x: np.ndarray, y: np.ndarray) -> float | None:
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def assemble(ep: Episode, s: Settings, c: Clients, base: date, days: list[date], fd: FactorData, typical: np.ndarray,
             matched: list[Matched], odds: dict[str, dict[date, float]], sources: dict[str, dict],
             ev: list[Evening], warnings: list[str], counts: dict) -> dict:
    nat, gaps = day_moves(fd, days, base)
    if gaps:
        warnings.append(f"Some factor series have no close on {', '.join(gaps)}; those days' moves are left out.")
    real_z = nat / typical
    has = np.isfinite(typical)
    expl = explained(ev, days)
    expl[:, ~has] = np.nan

    events, calls = [], []
    for mt in matched:
        spec, eid = mt.event.spec, mt.event.spec.id
        r = resolution_day(mt.event.happened, days)
        q_days = [None if r is not None and i >= r else ev[i + 1].q.get(eid) for i in range(len(days))]
        events.append({
            "id": eid, "t": spec.title, "s": spec.short, "question": spec.question, "why": spec.why,
            "happened": mt.event.happened.isoformat() if mt.event.happened else None,
            "res": r, "out": 1 if r is not None else 0,
            "q0": ev[0].q.get(eid), "q": q_days,
            "d": [x.delta.get(eid) for x in ev], "sd": [x.sigma.get(eid) for x in ev],
            "readings": [x.readings.get(eid, []) for x in ev],
            "listed": next((d.isoformat() for d, _ in sorted(odds[eid].items()) if d >= base), None) if odds[eid] else None,
            "odds": sources[eid],
            "match": {"kind": mt.decision.kind, "note": mt.decision.note, "good": mt.decision.good,
                      "candidates": mt.candidates, "used": [_market_json(x) for x in mt.decision.used],
                      "near_misses": [_market_json(x) for x in mt.decision.near_misses]},
            "analogs": [{"date": a.date.isoformat(), "days": a.days, "surprise": a.surprise, "note": a.note}
                        for a in spec.analogs],
        })
        if r is None:
            continue
        before = ev[r]
        if eid not in before.q or eid not in before.delta:
            calls.append({"event": eid, "day": r, "date": days[r].isoformat(), "q": before.q.get(eid), "pred": [None] * NF,
                          "real": real_z[r], "has": [False] * NF,
                          "why": "No market priced it the evening before." if eid not in before.q
                          else "No reading of its jump the evening before."})
            continue
        open_ = {e for e, q in before.q.items() if q < 1 and e != eid}
        tot = total_jump(eid, before.delta, before.links, open_)
        q = before.q[eid]
        calls.append({"event": eid, "day": r, "date": days[r].isoformat(), "q": q, "total": tot, "pred": (1 - q) * tot,
                      "real": real_z[r], "has": (np.isfinite(before.sigma[eid]) & has).tolist(), "why": None})
    for cl in calls:
        cl["pred"] = [None if v is None or not np.isfinite(v) or not cl["has"][k] else float(v) for k, v in enumerate(cl["pred"])]
        cl["real"] = [None if not np.isfinite(v) else float(v) for v in cl["real"]]

    first = ev[0]
    ranking = sorted(({"event": e, "q": first.q[e], "swing": swing_stocks(first.delta[e], first.q[e]),
                       "resolved_on": next((cl["date"] for cl in calls if cl["event"] == e), None),
                       "stock_move_that_day": next((cl["real"][EQ] for cl in calls if cl["event"] == e), None)}
                      for e in first.delta if e in first.q and first.q[e] < 1), key=lambda x: -x["swing"])

    links = [{"parent": ln.parent, "child": ln.child, "w_proposed": ln.w, "reason": ln.reason,
              "w": [x.links.get((ln.parent, ln.child)) for x in ev],
              "status": [x.link_status[(ln.parent, ln.child)] for x in ev]} for ln in ep.links]
    return {
        "kind": "redkeep.replay", "version": 1, "episode": ep.name, "title": ep.title, "sub": ep.sub,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": c.model,
        "base": base.isoformat(), "lookback_days": ep.lookback, "window_move": s.window_move,
        "catalogue": counts,
        "factors": [{"key": f.key, "name": f.name, "unit": f.unit, "typical": typical[k], "source": fd.sources.get(f.key)}
                    for k, f in enumerate(FACTORS)],
        "typical": typical,
        "days": [{"d": d.isoformat(), "f": nat[i], "z": real_z[i], "explained": expl[i], "note": ep.notes.get(d, "")}
                 for i, d in enumerate(days)],
        "events": events, "links": links, "calls": calls,
        "metrics": metrics(calls, expl, real_z, has, ranking),
        "left_out": [
            "The AI prior: the model knows how the episode ended, so its estimates would carry hindsight.",
            "Hyperliquid's weekend windows: its stock, oil and gold perps didn't trade yet.",
            "Options: event-day volatility and the two-world fit aren't built yet (M6).",
        ],
        "warnings": warnings,
    }


# -- the sample portfolios (E8's portfolio metric) ---------------------------------------------------------------


def _share(y: np.ndarray, x: np.ndarray) -> float | None:
    """``1 − Σ(y − x)² / Σ y²``: the share of the moves ``y`` that ``x`` accounts for."""
    ss = float(y @ y)
    return 1 - float(((y - x) ** 2).sum()) / ss if ss else None


def _close_on_or_before(closes: dict[date, float], d: date) -> float | None:
    ds = sorted(x for x in closes if x <= d)
    return closes[ds[-1]] if ds and (d - ds[-1]).days <= 5 else None


def portfolio_moves(s: Settings, c: Clients, fd: FactorData, rep: dict, *,
                    picks: dict[str, Sample] | None = None) -> tuple[list[dict], list[str]]:
    """Each sample portfolio as it stood at the first evening, scored on the episode's days (see the module's
    docstring). ``rep`` is the assembled replay, before its numbers are made JSON-safe."""
    if not getattr(c.massive, "available", False):
        return [], ["No Massive key, so the replay scores the factors and not the sample portfolios."]
    base = date.fromisoformat(rep["base"])
    days = [date.fromisoformat(x["d"]) for x in rep["days"]]
    prev = [base] + days[:-1]
    expl = np.nan_to_num(np.array([x["explained"] for x in rep["days"]], float))
    real_z = np.nan_to_num(np.array([x["z"] for x in rep["days"]], float))
    fdw = window(fd, base, int(365 * s.exposure_years) + 30)  # what `analyze` would have fitted on that evening
    out, warns = [], []
    for p in (picks or samples()).values():
        try:
            px = exposure.estimate(p.holdings, fdw, c.massive, as_of=base)
            path = {ln.ticker: c.massive.closes(ln.ticker, base - timedelta(days=10), days[-1]) for ln in px.lines}
        except (ValueError, HttpError, MassiveUnavailable) as e:
            warns.append(f"{p.name}: no portfolio moves ({e})")
            continue
        def value(d: date) -> float:
            """The buy-and-hold value at the close of ``d``: each line's shares at the first evening, at that day's price."""
            total = 0.0
            for ln in px.lines:
                price = ln.price if d == base else _close_on_or_before(path[ln.ticker], d)
                if not ln.price or price is None:
                    return float("nan")
                total += ln.value / ln.price * price
            return total

        v = {d: value(d) for d in [base] + days}
        real = np.array([(v[d] / v[a] - 1) * 100 for d, a in zip(days, prev)])
        ev_move, f_move = expl @ px.b, real_z @ px.b
        ok = np.isfinite(real)
        y, e, f = real[ok], ev_move[ok], f_move[ok]
        moved = ok & (ev_move != 0)
        calls = []
        for cl in rep["calls"]:
            pred = cl["pred"] or []
            if all(x is None for x in pred):
                continue
            pp = float(sum(px.b[k] * x for k, x in enumerate(pred) if x is not None))
            r = real[cl["day"]]
            calls.append({"event": cl["event"], "date": cl["date"], "q": cl["q"], "pred": pp,
                          "real": float(r) if np.isfinite(r) else None,
                          "usd": {"pred": px.value * pp / 100, "real": px.value * r / 100 if np.isfinite(r) else None}})
        funds = {ln.ticker: ln.holdings_date.isoformat() for ln in px.lines if ln.holdings_date}
        out.append({
            "id": p.id, "name": p.name, "who": p.who, "value": px.value, "b": px.b, "fit": px.fit,
            "lines": [{"ticker": ln.ticker, "weight": ln.weight, "r2_oos": ln.r2_oos, "n_obs": ln.n_obs} for ln in px.lines],
            "holdings_as_of": funds,
            "days": [{"d": d.isoformat(), "real": real[i], "events": ev_move[i], "factors": f_move[i]}
                     for i, d in enumerate(days)],
            "calls": calls,
            "metrics": {
                "days": int(ok.sum()),
                "events": {"explained_share": _share(y, e), "corr": _corr(e, y), "days_moved": int(moved.sum()),
                           "right_sign": int((np.sign(real[moved]) == np.sign(ev_move[moved])).sum())},
                "factors": {"explained_share": _share(y, f), "corr": _corr(f, y)},
            },
        })
    return out, warns


# -- files -----------------------------------------------------------------------------------------------


def save(rep: dict, path: Path | None = None) -> Path:
    """The replay for the app, under ``data/replays/``. That folder is committed, so the file leaves out which model
    judged the matches; the run's own copy in ``out/`` and the call logs under ``.cache/ai/`` keep it."""
    import json

    path = path or REPLAYS / f"{rep['episode']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: v for k, v in rep.items() if k != "model"}, indent=1, ensure_ascii=False))
    return path


def load_saved(name: str = "tariffs-2025") -> dict | None:
    import json

    path = REPLAYS / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None
