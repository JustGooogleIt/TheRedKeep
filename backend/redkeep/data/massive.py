"""Massive (formerly Polygon.io): daily bars and ETF holdings.

* Daily bars: ``GET /v2/aggs/ticker/{t}/range/1/day/{from}/{to}``, split-adjusted,
  results ``[{"t": ms, "c": close, ...}]``, paged by ``next_url``.
* ETF holdings: ``GET /etf-global/v1/constituents`` (the ETF Global partner
  dataset), filtered by ``composite_ticker`` and ``effective_date``; ``weight``
  is a fraction (0.0789 = 7.89%). This dataset is a separate entitlement; when
  the key doesn't include it the exposure engine falls back to the fund's own
  returns (DESIGN.md section 12).

Massive's free plan covers the daily bars (two years of them, end of day) at
5 calls a minute, which the HTTP client's spacing keeps to
(``MASSIVE_CALLS_PER_MINUTE``); holdings are a paid add-on.

The key is sent as a bearer token, never in the URL, so it can't leak into the
cache or logs.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ..http import Http, HttpError
from ..store import Store

BASE = "https://api.massive.com"


class MassiveUnavailable(RuntimeError):
    pass


class Massive:
    """With a ``store``, every close and holdings file read is also recorded there, point in time."""

    def __init__(self, http: Http, api_key: str | None, *, ttl: float | None = 6 * 3600, store: Store | None = None):
        self.http, self.ttl, self.store = http, ttl, store
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._holdings = True  # False once the key turns out not to include holdings: asking again only spends calls

    @property
    def available(self) -> bool:
        return self._headers is not None

    def _get(self, url: str, params=None, ttl=None):
        if not self._headers:
            raise MassiveUnavailable("MASSIVE_API_KEY is not set")
        return self.http.get_json(url, params, headers=self._headers, ttl=ttl)

    def closes(self, ticker: str, start: date, end: date) -> dict[date, float]:
        ttl = None if end < date.today() - timedelta(days=7) else self.ttl
        res = self._get(f"{BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
                        {"adjusted": "true", "sort": "asc", "limit": 50000}, ttl=ttl)
        out: dict[date, float] = {}
        while True:
            for bar in res.get("results") or []:
                out[datetime.fromtimestamp(bar["t"] / 1000, timezone.utc).date()] = float(bar["c"])
            nxt = res.get("next_url")
            if not nxt:
                if self.store is not None:
                    self.store.add_closes("massive", ticker, out)
                return out
            res = self._get(nxt, ttl=ttl)

    def details(self, ticker: str) -> dict:
        """Name and security type ("CS" for a common stock, "ETF", ...) from the reference data."""
        try:
            return self._get(f"{BASE}/v3/reference/tickers/{ticker}", ttl=7 * 86400).get("results") or {}
        except HttpError as e:
            if e.status in (403, 404):
                return {}
            raise

    def constituents(self, etf: str, as_of: date) -> tuple[date, list[dict]] | None:
        """The fund's holdings on the latest date on or before ``as_of``; None if not entitled."""
        if not self._holdings:
            return None
        try:
            head = self._get(f"{BASE}/etf-global/v1/constituents", {
                "composite_ticker": etf, "effective_date.lte": as_of.isoformat(),
                "sort": "effective_date.desc", "limit": 1,
            }, ttl=self.ttl)
        except HttpError as e:
            if e.status in (401, 403):
                self._holdings = False
                return None
            raise
        rows = head.get("results") or []
        if not rows:
            return None
        eff = date.fromisoformat(rows[0]["effective_date"])
        res = self._get(f"{BASE}/etf-global/v1/constituents", {
            "composite_ticker": etf, "effective_date": eff.isoformat(), "sort": "weight.desc", "limit": 5000,
        }, ttl=None)
        out = list(res.get("results") or [])
        while res.get("next_url"):
            res = self._get(res["next_url"], ttl=None)
            out.extend(res.get("results") or [])
        if self.store is not None:
            self.store.add_holdings(etf, eff, [(r.get("constituent_ticker") or r.get("constituent_name") or "?",
                                                r.get("constituent_name") or "", float(r.get("weight") or 0))
                                               for r in out])
        return eff, out
