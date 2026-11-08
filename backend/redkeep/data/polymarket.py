"""Polymarket's public APIs: Gamma for the market list, CLOB for price history.

No key is needed. Endpoints and fields checked against live responses on
2026-11-08: ``/events`` and ``/public-search`` return events with nested
markets; ``/prices-history`` returns ``{"history": [{"t": unix_s, "p": price}]}``.
``/events`` pages hold at most 100 events whatever ``limit`` asks for.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ..http import Http
from ..markets import Market, from_polymarket_event, is_sports_polymarket
from . import chunks

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
CHUNK = timedelta(days=14)  # the CLOB refuses longer windows


class Polymarket:
    def __init__(self, http: Http, *, ttl: float | None = 15 * 60):
        self.http, self.ttl = http, ttl

    def events(self, *, since: date, pages: int = 20, page_size: int = 100, volume_min: float = 1_000) -> list[dict]:
        """Active events by 24-hour volume, ending after ``since``.

        Gamma returns at most 100 events a page and refuses offsets past 2,000
        (checked 2026-11-08), so this reads the top 2,000 by 24-hour volume. By
        then an event trades about $150 a day; the targeted searches cover the
        library's terms below that.
        """
        out: list[dict] = []
        for i in range(pages):
            batch = self.http.get_json(f"{GAMMA}/events", {
                "active": "true", "closed": "false", "archived": "false",
                "order": "volume24hr", "ascending": "false",
                "limit": page_size, "offset": i * page_size,
                "volume_min": volume_min, "end_date_min": since.isoformat(),
            }, ttl=self.ttl)
            if not batch:
                break
            out.extend(batch)
            if len(batch) < page_size:
                break
        return out

    def search(self, query: str, *, limit: int = 10) -> list[dict]:
        res = self.http.get_json(f"{GAMMA}/public-search", {
            "q": query, "limit_per_type": limit, "events_status": "active",
        }, ttl=self.ttl)
        return res.get("events") or []

    def markets(self, *, since: date, queries: list[str] = ()) -> list[Market]:
        """The catalogue: top events by volume plus a search for each query."""
        events = {str(e.get("id")): e for e in self.events(since=since)}
        for q in queries:
            for e in self.search(q):
                events.setdefault(str(e.get("id")), e)
        out = []
        for e in events.values():
            if is_sports_polymarket(e):
                continue
            out.extend(m for m in from_polymarket_event(e) if m.close_time is None or m.close_time.date() >= since)
        return out

    def history(self, token_id: str, start: datetime, end: datetime, *, fidelity_min: int = 60) -> list[tuple[datetime, float]]:
        """Price of "Yes" over time.

        The CLOB refuses windows longer than 15 days (checked 2026-11-08), so this
        reads 14-day chunks on a fixed grid: finished chunks are cached for good
        and a rerun only fetches the latest one.
        """
        out: list[tuple[datetime, float]] = []
        now = datetime.now(timezone.utc)
        for t, u in chunks(start, end, CHUNK):
            res = self.http.get_json(f"{CLOB}/prices-history", {
                "market": token_id, "startTs": int(t.timestamp()), "endTs": int(u.timestamp()), "fidelity": fidelity_min,
            }, ttl=None if u < now - timedelta(days=1) else self.ttl)
            out.extend((datetime.fromtimestamp(p["t"], timezone.utc), float(p["p"])) for p in res.get("history") or [])
        return sorted({(t, q) for t, q in out if start <= t <= end})
