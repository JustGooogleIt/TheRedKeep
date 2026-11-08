"""Kalshi's public trade API v2: events with nested markets, and candlesticks.

No key is needed for market data. Fields checked against live responses on
2026-11-08: prices are dollar strings (``yes_bid_dollars``), sizes fixed-point
strings (``volume_fp``); candlesticks carry ``yes_bid`` and ``yes_ask`` blocks
with ``close_dollars``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ..http import Http
from ..markets import SKIP_KALSHI_CATEGORIES, Market, from_kalshi_event
from . import chunks

BASE = "https://api.elections.kalshi.com/trade-api/v2"


def _dollars(block: dict | None, name: str = "close") -> float | None:
    if not block:
        return None
    v = block.get(f"{name}_dollars")
    if v is None and block.get(name) is not None:
        return float(block[name]) / 100
    return None if v in (None, "") else float(v)


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
                bid, ask = _dollars(c.get("yes_bid")), _dollars(c.get("yes_ask"))
                if bid is not None and ask is not None and 0 < ask and bid <= ask:
                    mid = (bid + ask) / 2
                else:
                    mid = _dollars(c.get("price")) or _dollars(c.get("price"), "previous")
                if mid is not None:
                    out.append((datetime.fromtimestamp(c["end_period_ts"], timezone.utc), mid))
        return sorted({(t, q) for t, q in out if start <= t <= end})
