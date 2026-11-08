"""``redkeep record``: bring the point-in-time store up to date (DESIGN.md milestone M1).

Run it on a schedule, hourly for example. Each run adds only what is new since the last one:

* **Hyperliquid candles** for every listed trade[XYZ] perp (or only the perps the weekend windows use), from the last
  candle stored. Delisted perps are skipped. The API keeps only the latest 5,000 candles of each interval, about 208 days of hourly ones, so anything
  older exists only in the store. The first run reads all that the API still has.
* **The market catalogue**: every listed market's details and quotes on both venues, as ``refresh`` reads it.
  Neither venue keeps past quotes, so a spread as of last Tuesday exists only here.
* **Odds** of the markets the latest snapshot used or nearly used, hourly, from the last point stored.
* **Factor series** from FRED, and from Massive with a key (with the sample portfolios' closes and ETF holdings). The
  last 30 days are read again each time, so a revision is stored as a new version.

A source that fails is reported, and the run goes on with the rest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from .config import Settings
from .data.hyperliquid import KEPT, interval_length
from .events import load_library
from .factors import FACTORS
from .portfolio import samples

REVISIONS = timedelta(days=30)  # how far back each run rereads a daily series, for revisions
FIRST = timedelta(days=3 * 365)  # how far back the first read of a daily series goes, as refresh reads it


@dataclass
class Recording:
    started: datetime
    added: dict[str, int] = field(default_factory=dict)  # new rows per table
    read: dict[str, str] = field(default_factory=dict)  # what was read, per source, in words
    warnings: list[str] = field(default_factory=list)


def record(s: Settings, c, *, intervals: tuple[str, ...] = ("1h",), engine_perps: bool = False,
           skip: tuple[str, ...] = (), now: datetime | None = None, progress: Callable[[str], None] = print) -> Recording:
    """One recording run with the clients ``c`` (``pipeline.Clients``, made with a store)."""
    if c.store is None:
        raise RuntimeError("these clients have no store to record into")
    now = now or datetime.now(timezone.utc)
    rec = Recording(now)
    before = dict(c.store.added)  # reads flush along the way, so count from the store's own tally
    if "perps" not in skip:
        _perps(c, rec, intervals, engine_perps, now, progress)
    if "markets" not in skip:
        _markets(s, c, rec, now, progress)
    if "factors" not in skip:
        _factors(c, rec, now.date(), progress)
    c.store.flush()
    rec.added = {t: n - before.get(t, 0) for t, n in c.store.added.items()}
    return rec


def _perps(c, rec: Recording, intervals, engine_perps: bool, now: datetime, progress) -> None:
    from .evidence.weekend import PERPS

    if c.hyperliquid is None:
        return
    try:
        listed = c.hyperliquid.coins("xyz")
    except Exception as e:  # a source that fails is reported; the rest still records
        rec.warnings.append(f"Hyperliquid unavailable: {type(e).__name__}: {e}")
        return
    coins = sorted(x for x, spec in listed.items() if not spec.get("isDelisted"))  # delisted perps print no more candles
    if engine_perps:
        coins = [x for p in PERPS for x in [next((x for x in p.coins if x in listed), None)] if x]
    n = 0
    for coin in coins:
        for iv in intervals:
            step = interval_length(iv)
            last = c.store.last_time("candles", coin=coin, interval=iv)
            start = last - step if last else now - KEPT * step  # the last candle stored may have been open
            try:
                n += len(c.hyperliquid.candles(coin, start, now, interval=iv))
            except Exception as e:
                rec.warnings.append(f"No {iv} candles for {coin}: {type(e).__name__}: {e}")
    rec.read["Hyperliquid"] = (f"{len(coins)} trade[XYZ] perp{'' if len(coins) == 1 else 's'}, "
                               f"{' and '.join(intervals)} candles ({n:,} read)")
    progress(f"Hyperliquid: {rec.read['Hyperliquid']}")


def _markets(s: Settings, c, rec: Recording, now: datetime, progress) -> None:
    from .pipeline import catalogue

    try:
        markets = catalogue(load_library(), c, now.date(), progress)
    except Exception as e:
        rec.warnings.append(f"Market catalogue unavailable: {type(e).__name__}: {e}")
        return
    rec.read["Catalogue"] = f"{len(markets):,} markets' details and quotes"
    # The markets the latest snapshot used or nearly used: their odds history, hourly.
    path = s.out_dir / "library-latest.json"
    if not path.exists():
        rec.read["Odds"] = "none yet: no snapshot says which markets matter (run `redkeep refresh`)"
        return
    snap = json.loads(path.read_text())
    keys = sorted({m["key"] for e in snap["events"] for m in e["match"]["used"] + e["match"]["near_misses"]})
    by_key = {m.key: m for m in markets}
    n = missing = 0
    for key in keys:
        m = by_key.get(key)
        if m is None:  # closed since, or no longer in the catalogue
            missing += 1
            continue
        market = m.token_yes if m.venue == "polymarket" else m.id
        last = c.store.last_time("odds", venue=m.venue, market=market, step=60)
        start = last - timedelta(hours=1) if last else now - timedelta(days=s.history_days)
        try:
            if m.venue == "polymarket" and m.token_yes:
                n += len(c.polymarket.history(m.token_yes, start, now))
            elif m.venue == "kalshi" and m.series:
                n += len(c.kalshi.history(m.series, m.id, start, now))
        except Exception as e:
            rec.warnings.append(f"No odds history for {key}: {type(e).__name__}: {e}")
    rec.read["Odds"] = (f"{len(keys) - missing} markets from the snapshot of {snap['as_of']} ({n:,} hourly points)"
                        + (f"; {missing} no longer listed" if missing else ""))
    progress(f"Odds: {rec.read['Odds']}")


def _factors(c, rec: Recording, today: date, progress) -> None:
    def since(source: str, series: str) -> date:
        last = c.store.last_time("closes", source=source, series=series)
        return last - REVISIONS if last else today - FIRST

    fred = [f.fred for f in FACTORS if f.fred]
    for i in fred:
        try:
            c.fred.series([i], since("fred", i), today)
        except Exception as e:
            rec.warnings.append(f"FRED {i} unavailable: {type(e).__name__}: {e}")
    rec.read["FRED"] = f"{len(fred)} factor series"
    if not c.massive.available:
        rec.read["Massive"] = "skipped: no MASSIVE_API_KEY"
    else:
        tickers = sorted({t for f in FACTORS for t in f.massive} | {t for p in samples().values() for t, _, _ in p.holdings})
        held = 0
        for t in tickers:
            try:
                c.massive.closes(t, since("massive", t), today)
                held += c.massive.constituents(t, today) is not None
            except Exception as e:
                rec.warnings.append(f"Massive {t} unavailable: {type(e).__name__}: {e}")
        rec.read["Massive"] = f"closes for {len(tickers)} tickers, holdings for {held} funds"
    progress(f"Factors: {rec.read['FRED']}; Massive: {rec.read['Massive']}")
