"""One market model for both venues, built from their public API payloads.

Field names follow the live responses of Polymarket's Gamma API and Kalshi's
trade API v2 as of November 2026. Kalshi now quotes prices as dollar strings
(``yes_bid_dollars``) and sizes as fixed-point strings (``volume_fp``); the
older integer-cent fields are still read when present.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

Venue = Literal["polymarket", "kalshi"]

SPORTS_PREFIX = re.compile(
    r"^(nba|nfl|mlb|nhl|wnba|ncaa[a-z]*|cfb|cbb|epl|ucl|uel|laliga|seriea|bundesliga|ligue1|mls|ufc|f1|atp|wta|pga|"
    r"cs2|csgo|lol|dota2?|val|ow|r6|cod|boxing|cricket|ipl|t20|rugby|afl|nrl|tennis|golf|soccer)-"
)
SKIP_KALSHI_CATEGORIES = {"Sports", "Entertainment", "Mentions", "Climate and Weather", "Social"}


@dataclass
class Market:
    venue: Venue
    id: str  # Polymarket market id, or Kalshi market ticker
    event_id: str  # Polymarket event id, or Kalshi event ticker
    title: str
    event_title: str
    outcome: str | None  # the outcome this binary market stands for, inside a group
    rules: str
    close_time: datetime | None
    bid: float | None
    ask: float | None
    last: float | None
    volume: float  # traded, in dollars (approximate for Kalshi)
    volume_24h: float
    liquidity: float  # Polymarket order-book liquidity, or Kalshi open interest in dollars
    exclusive: bool  # one of a set of mutually exclusive outcomes
    url: str
    series: str | None = None  # Kalshi series ticker
    token_yes: str | None = None  # Polymarket CLOB token for "Yes"
    category: str | None = None
    strike: dict[str, Any] = field(default_factory=dict)
    opened: datetime | None = None  # when it was listed, where the venue says (the replay reads markets as of a date)

    @property
    def key(self) -> str:
        return f"{self.venue}:{self.id}"

    @property
    def mid(self) -> float | None:
        if self.bid is not None and self.ask is not None and 0 <= self.bid <= self.ask <= 1:
            return (self.bid + self.ask) / 2
        return self.last

    @property
    def spread(self) -> float | None:
        if self.bid is not None and self.ask is not None and self.ask >= self.bid:
            return self.ask - self.bid
        return None

    def text(self) -> str:
        parts = [self.title, self.outcome or "", self.event_title, self.rules[:600]]
        return " \n".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["close_time"] = self.close_time.isoformat() if self.close_time else None
        d["mid"], d["spread"], d["key"] = self.mid, self.spread, self.key
        return d


# -- helpers ----------------------------------------------------------------


def _num(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _json_list(x: Any) -> list:
    if isinstance(x, list):
        return x
    if isinstance(x, str) and x.strip().startswith("["):
        try:
            return json.loads(x)
        except json.JSONDecodeError:
            return []
    return []


def parse_time(x: Any) -> datetime | None:
    if not x:
        return None
    s = str(x).strip().replace(" ", "T", 1)
    s = re.sub(r"(\+00)(:00)*$", "+00:00", s).replace("Z", "+00:00")
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        try:
            t = datetime.fromisoformat(s[:10])
        except ValueError:
            return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _kalshi_price(m: dict, name: str) -> float | None:
    v = _num(m.get(f"{name}_dollars"))
    if v is None:
        cents = _num(m.get(name))
        v = None if cents is None else cents / 100
    return v


def _kalshi_count(m: dict, name: str) -> float:
    v = _num(m.get(f"{name}_fp"))
    if v is None:
        v = _num(m.get(name))
    return v or 0.0


# -- Polymarket -----------------------------------------------------------------


def is_sports_polymarket(ev: dict) -> bool:
    if SPORTS_PREFIX.match(str(ev.get("slug") or "")):
        return True
    for m in ev.get("markets") or []:
        if m.get("sportsMarketType") or m.get("gameStartTime"):
            return True
    labels = {str(t.get("label", "")).lower() for t in ev.get("tags") or [] if isinstance(t, dict)}
    return bool(labels & {"sports", "esports", "nba", "nfl", "soccer", "mlb", "nhl"})


def from_polymarket_event(ev: dict, *, closed: bool = False) -> list[Market]:
    """The event's yes/no markets that are still trading, or with ``closed`` also those that have closed (for the
    historical replay, which reads markets as they were listed then)."""
    out = []
    exclusive = bool(ev.get("negRisk"))
    for m in ev.get("markets") or []:
        if not closed and (m.get("closed") or m.get("archived") or m.get("active") is False):
            continue
        outcomes = [str(o).lower() for o in _json_list(m.get("outcomes"))]
        if outcomes and outcomes != ["yes", "no"]:
            continue  # head-to-head markets ("Spurs" vs "Kings") are not yes/no questions
        prices = [_num(p) for p in _json_list(m.get("outcomePrices"))]
        tokens = [str(t) for t in _json_list(m.get("clobTokenIds"))]
        last = _num(m.get("lastTradePrice"))
        if last is None and prices:
            last = prices[0]
        out.append(Market(
            venue="polymarket",
            id=str(m.get("id")),
            event_id=str(ev.get("id")),
            title=str(m.get("question") or ev.get("title") or ""),
            event_title=str(ev.get("title") or ""),
            outcome=(m.get("groupItemTitle") or None),
            rules=str(m.get("description") or ev.get("description") or ""),
            close_time=parse_time(m.get("endDate") or ev.get("endDate")),
            bid=_num(m.get("bestBid")),
            ask=_num(m.get("bestAsk")),
            last=last,
            volume=_num(m.get("volumeNum")) or _num(m.get("volume")) or 0.0,
            volume_24h=_num(m.get("volume24hr")) or 0.0,
            liquidity=_num(m.get("liquidityNum")) or _num(m.get("liquidity")) or 0.0,
            exclusive=exclusive or bool(m.get("negRisk")),
            url=f"https://polymarket.com/event/{ev.get('slug')}",
            token_yes=tokens[0] if tokens else None,
            category=None,
            strike={"threshold": _num(m.get("groupItemThreshold"))},
            opened=parse_time(m.get("acceptingOrdersTimestamp") or m.get("startDate") or m.get("createdAt")),
        ))
    return out


# -- Kalshi -----------------------------------------------------------------------


def from_kalshi_market(m: dict, *, series: str | None, event_title: str = "", exclusive: bool = False,
                      category: str | None = None) -> Market | None:
    """One binary market, from an event's nested list or a series' list of settled markets."""
    if m.get("market_type", "binary") != "binary":
        return None
    bid, ask, last = _kalshi_price(m, "yes_bid"), _kalshi_price(m, "yes_ask"), _kalshi_price(m, "last_price")
    if ask == 0:
        ask = None  # no one is selling
    ref = last if last else ((bid or 0) + (ask or 1)) / 2
    rules = "\n".join(s for s in (m.get("rules_primary"), m.get("rules_secondary")) if s)
    return Market(
        venue="kalshi",
        id=str(m.get("ticker")),
        event_id=str(m.get("event_ticker")),
        title=str(m.get("title") or event_title),
        event_title=event_title,
        outcome=m.get("yes_sub_title") or m.get("subtitle") or None,
        rules=rules,
        close_time=parse_time(m.get("close_time")),
        bid=bid,
        ask=ask,
        last=last,
        volume=_kalshi_count(m, "volume") * ref,
        volume_24h=_kalshi_count(m, "volume_24h") * ref,
        liquidity=_kalshi_count(m, "open_interest") * ref,
        exclusive=exclusive,
        url=f"https://kalshi.com/markets/{str(series or '').lower()}",
        series=series,
        category=category,
        strike={k: m.get(k) for k in ("strike_type", "floor_strike", "cap_strike", "custom_strike") if m.get(k) is not None},
        opened=parse_time(m.get("open_time")),
    )


def from_kalshi_event(ev: dict) -> list[Market]:
    out = []
    series = ev.get("series_ticker")
    for m in ev.get("markets") or []:
        if m.get("status") not in (None, "active", "open", "initialized"):
            continue
        mk = from_kalshi_market({**m, "event_ticker": ev.get("event_ticker") or m.get("event_ticker")}, series=series,
                                event_title=str(ev.get("title") or ""), exclusive=bool(ev.get("mutually_exclusive")),
                                category=ev.get("category"))
        if mk is not None:
            out.append(mk)
    return out


def dedupe(markets: Iterable[Market]) -> list[Market]:
    seen: dict[str, Market] = {}
    for m in markets:
        seen.setdefault(m.key, m)
    return list(seen.values())
