"""Hyperliquid's public info API (DESIGN.md 6.4 and 11): candles for trade[XYZ]'s non-crypto perps.

Everything goes through ``POST https://api.hyperliquid.xyz/info``. HIP-3 coins carry their dex's prefix
(``xyz:SP500``). Only the latest 5,000 candles of each interval are kept, so hourly candles reach back about
208 days. They are read in chunks of 480 candles (inside the 500 a response may hold; 20 days of hourly ones) on a
fixed grid, so finished chunks come from the response cache. With a point-in-time store, every candle read is
recorded there, and reads return the store's candles too, so history the API has dropped is still there.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..http import Http
from ..store import Store
from . import chunks

INFO = "https://api.hyperliquid.xyz/info"
CHUNK = 480  # candles a request asks for
KEPT = 5000  # candles of each interval the API keeps
INTERVALS = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "8h": 480, "12h": 720,
             "1d": 1440}  # minutes; the API also has 3d, 1w and 1M


def interval_length(interval: str) -> timedelta:
    return timedelta(minutes=INTERVALS[interval])


@dataclass(frozen=True)
class Candle:
    start: datetime  # open time
    end: datetime  # close time
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def _utc(ms: Any) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc)


def parse_candle(r: dict) -> Candle:
    """One candle as the API sends it: times in epoch milliseconds, prices and volume as strings."""
    return Candle(start=_utc(r["t"]), end=_utc(r["T"]), open=float(r["o"]), high=float(r["h"]), low=float(r["l"]),
                  close=float(r["c"]), volume=float(r.get("v") or 0), trades=int(r.get("n") or 0))


class Hyperliquid:
    def __init__(self, http: Http, *, ttl: float = 300, store: Store | None = None):
        self.http, self.ttl, self.store = http, ttl, store

    def coins(self, dex: str = "xyz") -> dict[str, dict]:
        """The dex's perps by coin name (with the dex prefix), each with its listing (max leverage and so on)."""
        res = self.http.post_json(INFO, {"type": "meta", "dex": dex}, ttl=self.ttl)
        out = {}
        for a in res.get("universe") or []:
            name = a.get("name") or ""
            if name:
                out[name if ":" in name else f"{dex}:{name}"] = a
        return out

    def candles(self, coin: str, start: datetime, end: datetime, *, interval: str = "1h") -> list[Candle]:
        """Candles that open between ``start`` and ``end``, oldest first: the API's, and with a store, also those
        it has dropped."""
        now = datetime.now(timezone.utc)
        step = interval_length(interval)
        got: dict[datetime, Candle] = {}
        for t, u in chunks(start, end, CHUNK * step):
            body = {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": _ms(t), "endTime": _ms(u)}}
            rows = self.http.post_json(INFO, body, ttl=None if u < now - 2 * step else self.ttl)
            for r in rows or []:
                c = parse_candle(r)
                got[c.start] = c
        if self.store is None:
            return [got[k] for k in sorted(got) if start <= k <= end]
        self.store.add_candles(coin, interval, got.values())
        return [Candle(*r) for r in self.store.candles(coin, interval, start, end)]
