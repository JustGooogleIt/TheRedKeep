"""Kalshi's public trade API v2: events with nested markets, and candlesticks.

No key is needed for market data. Fields checked against live responses on
2026-11-08: prices are dollar strings (``yes_bid_dollars``), sizes fixed-point
strings (``volume_fp``); candlesticks carry ``yes_bid`` and ``yes_ask`` blocks
with ``close_dollars``.

Settled markets (for the calibration study, E2): markets settled before the
archive cutoff (``/historical/cutoff``, two months back on 2026-11-08) live
under ``/historical/markets`` and its candlesticks, which name prices without
the ``_dollars`` suffix but still give dollars (``"0.0500"``). The live
candlestick path refuses archived markets. Daily candles end at 04:00 UTC and
include days without trades, with the quotes carried forward.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ..http import Http
from ..markets import SKIP_KALSHI_CATEGORIES, Market, from_kalshi_event
from . import chunks

BASE = "https://api.elections.kalshi.com/trade-api/v2"


def _dollars(block: dict | None, name: str = "close") -> float | None:
    """A candle price in dollars. Live candles name it ``close_dollars``; archived ones ``close``, also in dollars
    (``"0.0500"``); old live candles gave whole cents (``45``)."""
    if not block:
        return None
    v = block.get(f"{name}_dollars")
    if v is None:
        v = block.get(name)
        if v is None or v == "":
            return None
        if not (isinstance(v, str) and "." in v):
            return float(v) / 100
    return None if v == "" else float(v)


def candle_price(c: dict) -> float | None:
    """The Yes price at the end of a candle: the midpoint of the bid and ask, else the last trade."""
    bid, ask = _dollars(c.get("yes_bid")), _dollars(c.get("yes_ask"))
    if bid is not None and ask is not None and 0 < ask <= 1 and 0 <= bid <= ask:
        return (bid + ask) / 2
    for name in ("close", "previous"):
        p = _dollars(c.get("price"), name)
        if p is not None and 0 <= p <= 1:
            return p
    return None


class Kalshi:
    def __init__(self, http: Http, *, ttl: float | None = 15 * 60):
        self.http, self.ttl = http, ttl

    def events(self, *, pages: int = 80) -> list[dict]:
        out, cursor = [], None
        for _ in range(pages):
            res = self.http.get_json(f"{BASE}/events", {
                "status": "open", "with_nested_markets": "true", "limit": 200, "cursor": cursor,
            }, ttl=self.ttl)
            out.extend(res.get("events") or [])
            cursor = res.get("cursor")
            if not cursor:
                break
        return out

    def markets(self, *, since: date) -> list[Market]:
        out = []
        for e in self.events():
            if e.get("category") in SKIP_KALSHI_CATEGORIES:
                continue
            out.extend(m for m in from_kalshi_event(e) if m.close_time is None or m.close_time.date() >= since)
        return out

    def history(self, series: str, ticker: str, start: datetime, end: datetime, *, period_min: int = 60) -> list[tuple[datetime, float]]:
        """Midpoint of the "Yes" bid and ask at the end of each period, in 60-day chunks on a fixed grid."""
        out: list[tuple[datetime, float]] = []
        now = datetime.now(timezone.utc)
        for t, u in chunks(start, end, timedelta(days=60)):
            res = self.http.get_json(f"{BASE}/series/{series}/markets/{ticker}/candlesticks", {
                "start_ts": int(t.timestamp()), "end_ts": int(u.timestamp()), "period_interval": period_min,
            }, ttl=None if u < now - timedelta(days=1) else self.ttl)
            for c in res.get("candlesticks") or []:
                mid = candle_price(c)
                if mid is not None:
                    out.append((datetime.fromtimestamp(c["end_period_ts"], timezone.utc), mid))
        return sorted({(t, q) for t, q in out if start <= t <= end})

    # -- settled markets, for the calibration study ----------------------------------------------

    def series(self, category: str) -> list[dict]:
        """Every series filed under ``category``, including those that list it second (check ``category``)."""
        return self.http.get_json(f"{BASE}/series", {"category": category}, ttl=24 * 3600).get("series") or []

    def settled(self, series: str, *, archived: bool, pages: int = 25) -> list[dict]:
        """A series' settled markets: from the archive, or the live list for those settled since the cutoff."""
        url = f"{BASE}/historical/markets" if archived else f"{BASE}/markets"
        out, cursor = [], None
        for _ in range(pages):
            params = {"series_ticker": series, "limit": 200, "cursor": cursor, "status": None if archived else "settled"}
            res = self.http.get_json(url, params, ttl=7 * 24 * 3600 if archived else self.ttl)
            out.extend(res.get("markets") or [])
            cursor = res.get("cursor")
            if not cursor:
                break
        return out

    def daily(self, series: str, ticker: str, start: datetime, end: datetime, *, archived: bool) -> list[tuple[datetime, float]]:
        """A settled market's Yes price at the end of each day from ``start`` to ``end``, oldest first."""
        url = (f"{BASE}/historical/markets/{ticker}/candlesticks" if archived
               else f"{BASE}/series/{series}/markets/{ticker}/candlesticks")
        res = self.http.get_json(url, {"start_ts": int(start.timestamp()), "end_ts": int(end.timestamp()),
                                       "period_interval": 1440}, ttl=None)
        out = []
        for c in res.get("candlesticks") or []:
            q = candle_price(c)
            if q is not None:
                out.append((datetime.fromtimestamp(c["end_period_ts"], timezone.utc), q))
        return sorted(out)
