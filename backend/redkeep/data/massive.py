"""Massive (formerly Polygon.io): daily bars and ETF holdings.

* Daily bars: ``GET /v2/aggs/ticker/{t}/range/1/day/{from}/{to}``, split-adjusted,
  results ``[{"t": ms, "c": close, ...}]``, paged by ``next_url``.
* ETF holdings: ``GET /etf-global/v1/constituents`` (the ETF Global partner
  dataset), filtered by ``composite_ticker``, ``effective_date`` (the day the
  holdings were true) and ``processed_date`` (the day ETF Global had them, which
  for some issuers is weeks later); ``weight`` is a fraction (0.0789 = 7.89%).
  It sorts only by ``composite_ticker``, ``constituent_ticker`` or
  ``processed_date``, so the latest effective date is found by asking for a
  later one until there is none. This dataset is a separate entitlement; when
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
CONSTITUENTS = f"{BASE}/etf-global/v1/constituents"
DATE_STEPS = 8
MAX_PAGES = 100  # 5,000 holdings or 50,000 bars a page: far more than any fund or price history needs


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

    def _pages(self, first: dict, ttl=None):
        """``first`` and every page after it, by ``next_url``. A page that comes round again stops it with an error:
        reading on would repeat the same rows without end."""
        res, seen = first, set()
        while True:
            yield res
            nxt = res.get("next_url")
            if not nxt:
                return
            if nxt in seen or len(seen) >= MAX_PAGES:
                raise HttpError(nxt, 0, "Massive's pages came round again (or ran past the most expected); stopped "
                                        "rather than read the same rows again")
            seen.add(nxt)
            res = self._get(nxt, ttl=ttl)

    def closes(self, ticker: str, start: date, end: date) -> dict[date, float]:
        ttl = None if end < date.today() - timedelta(days=7) else self.ttl
        res = self._get(f"{BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
                        {"adjusted": "true", "sort": "asc", "limit": 50000}, ttl=ttl)
        out: dict[date, float] = {}
        for page in self._pages(res, ttl):
            for bar in page.get("results") or []:
                out[datetime.fromtimestamp(bar["t"] / 1000, timezone.utc).date()] = float(bar["c"])
        if self.store is not None:
            self.store.add_closes("massive", ticker, out)
        return out

    def details(self, ticker: str) -> dict:
        """Name and security type ("CS" for a common stock, "ETF", ...) from the reference data."""
        try:
            return self._get(f"{BASE}/v3/reference/tickers/{ticker}", ttl=7 * 86400).get("results") or {}
        except HttpError as e:
            if e.status in (403, 404):
                return {}
            raise

    def constituents(self, etf: str, as_of: date) -> tuple[date, list[dict]] | None:
        """The fund's holdings on the latest date on or before ``as_of``, as ETF Global had them by ``as_of``
        (nothing processed later is used), largest first; None if the key doesn't include holdings."""
        if not self._holdings:
            return None
        known = {"composite_ticker": etf, "effective_date.lte": as_of.isoformat(), "processed_date.lte": as_of.isoformat()}
        eff: date | None = None
        processed = ""
        for _ in range(DATE_STEPS):  # each step finds a later effective date, until one finds none
            later = {"effective_date.gt": eff.isoformat()} if eff else {}
            try:
                head = self._get(CONSTITUENTS, {**known, **later, "sort": "processed_date.desc", "limit": 1}, ttl=self.ttl)
            except HttpError as e:
                if e.status in (400, 401, 403):  # not entitled, or a request it refuses: every other fund would cost a call too
                    self._holdings = False
                    if e.status == 400:
                        raise
                    return None
                raise
            rows = head.get("results") or []
            if not rows:
                break
            eff, processed = date.fromisoformat(rows[0]["effective_date"]), rows[0].get("processed_date") or ""
        if eff is None:
            return None
        params = {"composite_ticker": etf, "effective_date": eff.isoformat(), "sort": "constituent_ticker.asc", "limit": 5000}
        if processed:  # the latest processing known by as_of: a date processed again would repeat every holding
            params["processed_date"] = processed
        out = [r for page in self._pages(self._get(CONSTITUENTS, params, ttl=None)) for r in page.get("results") or []]
        out.sort(key=lambda r: -float(r.get("weight") or 0))
        if self.store is not None:
            self.store.add_holdings(etf, eff, [(r.get("constituent_ticker") or r.get("constituent_name") or "?",
                                                r.get("constituent_name") or "", float(r.get("weight") or 0))
                                               for r in out])
        return eff, out
