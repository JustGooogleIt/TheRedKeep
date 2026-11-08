"""The point-in-time store (DESIGN.md milestone M1): versions, the two as-of views, compaction, the data clients
that write to it, and ``redkeep record``."""

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from redkeep import cli
from redkeep.config import Settings
from redkeep.data.fred import Fred
from redkeep.data.hyperliquid import Candle, Hyperliquid
from redkeep.data.kalshi import Kalshi
from redkeep.data.polymarket import Polymarket
from redkeep.markets import from_kalshi_event, from_polymarket_event
from redkeep.record import record
from redkeep.store import Store, last_close_day

from .test_markets import stub
from .test_weekend import FakeHttp, raw

UTC = timezone.utc
T0 = datetime(2026, 11, 2, 12, tzinfo=UTC)


def h(n: float) -> datetime:
    return T0 + timedelta(hours=n)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "store")


# -- versions and the two as-of views --------------------------------------------------------------


def test_recording_the_same_prices_again_adds_nothing_and_a_revision_adds_a_version(store):
    pts = [(h(i), 0.40 + i / 100) for i in range(5)]
    store.add_odds("polymarket", "tok", 60, pts, seen_at=h(5))
    assert store.flush() == {"odds": 5}
    store.add_odds("polymarket", "tok", 60, pts, seen_at=h(6))
    assert store.flush() == {"odds": 0}  # nothing new: no segment written
    store.add_odds("polymarket", "tok", 60, [(h(0), 0.35)], seen_at=h(7))
    assert store.flush() == {"odds": 1}
    assert len(store.segments("odds")) == 2
    assert store.odds("polymarket", "tok", h(0), h(9))[0] == (h(0), 0.35)  # the latest version
    assert store.odds("polymarket", "tok", h(0), h(9), as_of=h(6), known=True)[0] == (h(0), 0.40)  # as seen then


def test_as_of_never_returns_later_times_and_known_only_what_had_been_seen(store):
    # Backfilled: the store saw these prices a day after they printed, as a replay of an old day would.
    store.add_odds("kalshi", "KX-1", 60, [(h(i), 0.5) for i in range(6)], seen_at=h(30))
    got = store.odds("kalshi", "KX-1", h(0), h(9), as_of=h(2))
    assert [t for t, _ in got] == [h(0), h(1), h(2)]  # nothing after the as-of time, even though it's stored
    assert store.odds("kalshi", "KX-1", h(0), h(9), as_of=h(2), known=True) == []  # not seen by then
    assert len(store.odds("kalshi", "KX-1", h(0), h(9), as_of=h(30), known=True)) == 6


def test_a_revision_back_to_an_earlier_value_is_kept(store):
    for seen, v in ((1, 4.10), (2, 4.15), (3, 4.10)):  # restated, then the restatement withdrawn
        store.add_closes("fred", "DGS10", {date(2026, 10, 30): v}, seen_at=h(seen))
        store.flush()
    assert store.sql("SELECT count(*) FROM closes")[0][0] == 3
    day = date(2026, 10, 30)
    assert store.closes("fred", "DGS10", day, day) == {day: 4.10}
    assert store.closes("fred", "DGS10", day, day, as_of=h(2.5), known=True) == {day: 4.15}


def test_an_open_candle_is_a_version_until_it_closes(store):
    def candle(close):
        return Candle(h(0), h(1) - timedelta(milliseconds=1), 100, max(100, close), min(100, close), close, 5, 3)

    store.add_candles("xyz:SP500", "1h", [candle(101)], seen_at=h(0.25))  # the hour still trading
    store.add_candles("xyz:SP500", "1h", [candle(103)], seen_at=h(1.1))  # final
    assert store.flush() == {"candles": 2}
    close = 5  # (start, end, open, high, low, close, ...)
    assert store.candles("xyz:SP500", "1h", h(0), h(0), as_of=h(0.5)) == []  # unfinished by then
    [partial] = store.candles("xyz:SP500", "1h", h(0), h(0), as_of=h(0.5), known=True)
    assert partial[close] == 101  # what a run at 12:30 saw
    assert store.candles("xyz:SP500", "1h", h(0), h(0), as_of=h(2))[0][close] == 103
    assert store.last_time("candles", coin="xyz:SP500", interval="1h") == h(0)


def test_a_close_counts_from_four_in_new_york():
    # 4 p.m. New York is 20:00 UTC in summer time and 21:00 UTC in winter.
    assert last_close_day(datetime(2025, 4, 2, 19, 59, tzinfo=UTC)) == date(2025, 4, 1)
    assert last_close_day(datetime(2025, 4, 2, 20, 0, tzinfo=UTC)) == date(2025, 4, 2)
    assert last_close_day(datetime(2025, 3, 7, 20, 30, tzinfo=UTC)) == date(2025, 3, 6)
    assert last_close_day(datetime(2025, 3, 7, 21, 0, tzinfo=UTC)) == date(2025, 3, 7)
    assert last_close_day(None) == date.max


def test_the_catalogue_as_of_a_time_is_each_venues_last_read_before_it(store, gamma_events, kalshi_events):
    pm = from_polymarket_event(gamma_events[0])
    ks = from_kalshi_event(kalshi_events[0])
    store.add_markets(pm, seen_at=h(1))
    store.add_markets(ks, seen_at=h(1.5))
    moved = [replace(m, bid=0.70, ask=0.72) if i == 0 else m for i, m in enumerate(pm)]
    store.add_markets(moved, seen_at=h(2))
    store.add_markets([replace(moved[0], title="Renamed")], seen_at=h(3))  # the next read lists only one market
    store.flush()
    assert store.sql("SELECT count(*) FROM markets")[0][0] == len(pm) + len(ks) + 1  # details only when they change

    at = {m.key: m for m in store.catalogue(as_of=h(2.5))}
    assert set(at) == {m.key for m in pm + ks}  # Polymarket's read at 2:00 and Kalshi's at 1:30
    first = at[pm[0].key]
    assert (first.bid, first.ask, first.title) == (0.70, 0.72, pm[0].title)
    assert first.token_yes == pm[0].token_yes and first.exclusive and first.close_time == pm[0].close_time
    assert at[ks[0].key].series == ks[0].series and at[ks[0].key].strike == ks[0].strike
    assert {m.key for m in store.catalogue(as_of=h(1.2))} == {m.key for m in pm}  # Kalshi not read yet
    now = store.catalogue(venue="polymarket")
    assert [m.title for m in now] == ["Renamed"]  # the other two are no longer listed
    assert store.catalogue(as_of=h(0)) == []
    # Quotes are stored only when they move: the same read again adds nothing, and an empty read delists nothing.
    store.add_markets(ks, seen_at=h(4))
    store.add_markets([], seen_at=h(5))
    assert store.flush() == {"markets": 0, "quotes": 0}
    assert store.sql("SELECT count(*) FROM quotes")[0][0] == len(pm) + len(ks) + 1 + 2  # moved, renamed, 2 delisted


def test_a_value_that_goes_blank_is_a_new_version_and_stays_blank(store, gamma_events):
    m = from_polymarket_event(gamma_events[0])[0]
    store.add_markets([replace(m, bid=0.001)], seen_at=h(1))
    store.add_markets([replace(m, bid=None)], seen_at=h(2))  # the book emptied
    store.add_markets([replace(m, bid=None)], seen_at=h(3))  # and stayed empty: nothing new
    store.flush()
    assert store.sql("SELECT count(*) FROM quotes")[0][0] == 2
    assert store.catalogue()[0].bid is None and store.catalogue(as_of=h(1.5))[0].bid == 0.001


def test_asking_for_the_last_time_doesnt_write_a_segment(store):
    for k, coin in enumerate(("xyz:SP500", "xyz:GOLD", "xyz:CL")):
        assert store.last_time("candles", coin=coin, interval="1h") is None
        store.add_candles(coin, "1h", [Candle(h(k), h(k + 1) - timedelta(milliseconds=1), 1, 1, 1, 1, 1, 1)], seen_at=h(5))
        assert store.last_time("candles", coin=coin, interval="1h") == h(k)  # queued rows count
    assert store.segments("candles") == []
    assert store.flush() == {"candles": 3} and len(store.segments("candles")) == 1
    assert store.last_time("candles", coin="xyz:GOLD", interval="1h") == h(1)


def test_holdings_as_of_take_the_fund_s_latest_file_by_then(store):
    store.add_holdings("QQQ", date(2026, 10, 30), [("NVDA", "NVIDIA", 0.09), ("AAPL", "Apple", 0.08)], seen_at=h(0))
    store.add_holdings("QQQ", date(2026, 11, 2), [("NVDA", "NVIDIA", 0.10), ("MSFT", "Microsoft", 0.08)], seen_at=h(9))
    eff, rows = store.holdings("QQQ")
    assert eff == date(2026, 11, 2) and rows[0] == ("NVDA", "NVIDIA", 0.10)
    eff, rows = store.holdings("QQQ", as_of=datetime(2026, 10, 31, 22, tzinfo=UTC))
    assert eff == date(2026, 10, 30) and [t for t, _, _ in rows] == ["NVDA", "AAPL"]
    assert store.holdings("SPY") is None


# -- segments ------------------------------------------------------------------------------------------


def test_compaction_merges_segments_and_answers_the_same(store):
    for k in range(6):
        store.add_odds("polymarket", "tok", 60, [(h(i), 0.5 + k / 100 * (i == 0)) for i in range(4)], seen_at=h(10 + k))
        store.flush()
    store.add_closes("fred", "VIXCLS", {date(2026, 11, 2): 18.0})
    store.flush()
    before = store.odds("polymarket", "tok", h(0), h(9), as_of=h(12.5), known=True)
    assert len(store.segments("odds")) == 6
    assert store.compact()["odds"] == (6, 1)
    assert store.odds("polymarket", "tok", h(0), h(9), as_of=h(12.5), known=True) == before
    assert store.sql("SELECT count(*) FROM odds")[0][0] == 4 + 5  # every version kept
    assert not list(store.root.rglob("*.tmp"))


def test_two_writers_can_share_a_store(tmp_path):
    a, b = Store(tmp_path / "s"), Store(tmp_path / "s")
    a.add_closes("fred", "DGS10", {date(2026, 11, 2): 4.1}, seen_at=h(0))
    b.add_closes("fred", "DGS10", {date(2026, 11, 2): 4.1}, seen_at=h(0))  # the same row, written twice
    a.add_closes("fred", "DGS10", {date(2026, 11, 3): 4.2}, seen_at=h(1))
    a.flush(), b.flush()
    assert b.closes("fred", "DGS10", date(2026, 11, 1), date(2026, 11, 9)) == {date(2026, 11, 2): 4.1, date(2026, 11, 3): 4.2}
    a.compact()
    assert a.sql("SELECT count(*) FROM closes")[0][0] == 2  # the duplicate went at compaction


# -- the data clients write what they read ----------------------------------------------------------------


def test_hyperliquid_returns_candles_the_api_has_dropped(store):
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    old = [Candle(now - timedelta(days=300, hours=i), now - timedelta(days=300, hours=i - 1), 1, 1, 1, 1, 1, 1)
           for i in range(3)]
    store.add_candles("xyz:SP500", "1h", old, seen_at=now - timedelta(days=299))

    def answer(body):  # the API now has only the last day
        a, b = body["req"]["startTime"], body["req"]["endTime"]
        return [raw(now - timedelta(hours=k), 6000, 6001, 5999, 6000) for k in range(1, 25)
                if a <= int((now - timedelta(hours=k)).timestamp() * 1000) <= b]

    hl = Hyperliquid(FakeHttp(answer), store=store)
    got = hl.candles("xyz:SP500", now - timedelta(days=301), now)
    assert len(got) == 3 + 24 and got[0].start == old[-1].start and isinstance(got[0], Candle)
    assert hl.candles("xyz:SP500", now - timedelta(days=301), now) == got  # the same, with the queue read twice
    assert store.segments("candles") == []  # reading back wrote nothing yet
    assert store.sql("SELECT count(*) FROM candles")[0][0] == 27  # the API's candles are recorded too
    assert len(store.segments("candles")) == 1


def test_the_venue_and_fred_clients_record_what_they_read(store, gamma_events, kalshi_events):
    def handler(req):
        if req.url.host == "clob.polymarket.com":
            a = int(req.url.params["startTs"])
            return httpx.Response(200, json={"history": [{"t": a + 3600, "p": 0.4}]})
        if req.url.host == "fred.stlouisfed.org":
            return httpx.Response(200, text="observation_date,DGS10\n2026-11-02,4.10\n2026-11-03,4.12\n")
        if req.url.path.endswith("/events") and req.url.host == "api.elections.kalshi.com":
            return httpx.Response(200, json={"events": kalshi_events[:1], "cursor": ""})
        if req.url.path.endswith("/candlesticks"):
            return httpx.Response(200, json={"candlesticks": [
                {"end_period_ts": int(T0.timestamp()), "yes_bid": {"close_dollars": "0.60"},
                 "yes_ask": {"close_dollars": "0.62"}}]})
        raise AssertionError(req.url)

    http = stub(handler)
    Polymarket(http, store=store).history("tok", T0 - timedelta(days=20), T0)
    Fred(http, store=store).series(["DGS10"], date(2026, 11, 1), date(2026, 11, 5))
    ks = Kalshi(http, store=store)
    listed = ks.markets(since=date(2026, 11, 1))
    ks.history(listed[0].series, listed[0].id, T0 - timedelta(days=1), T0)
    assert len(store.odds("polymarket", "tok", T0 - timedelta(days=20), T0)) == 2  # one point a 14-day chunk
    assert store.closes("fred", "DGS10", date(2026, 11, 1), date(2026, 11, 5)) == {date(2026, 11, 2): 4.10,
                                                                                  date(2026, 11, 3): 4.12}
    assert store.odds("kalshi", listed[0].id, T0 - timedelta(days=1), T0) == [(T0, pytest.approx(0.61))]
    assert {m.key for m in store.catalogue()} == {m.key for m in listed}
    assert Polymarket(http).store is None  # without a store, nothing is recorded


# -- redkeep record ------------------------------------------------------------------------------------------


class World:
    """Stand-in sources for a recording run: two perps, a catalogue, odds histories and FRED."""

    def __init__(self, store, gamma_events, now):
        self.now, self.requests = now, []
        self.markets = from_polymarket_event(gamma_events[0])

        def answer(body):
            if body["type"] == "meta":
                return {"universe": [{"name": "xyz:SP500", "maxLeverage": 50}, {"name": "xyz:NVDA", "maxLeverage": 20},
                                     {"name": "xyz:OLD", "maxLeverage": 10, "isDelisted": True}]}
            req = body["req"]
            self.requests.append(req)
            hours = [now - timedelta(hours=k) for k in range(1, 72)]
            return [raw(t, 1, 1, 1, 1) for t in hours if req["startTime"] <= int(t.timestamp() * 1000) <= req["endTime"]]

        def history(token, start, end):
            pts = [(start + timedelta(hours=k), 0.5) for k in range(int((end - start) / timedelta(hours=1)))]
            store.add_odds("polymarket", token, 60, pts)
            return pts

        def series(ids, a, b):
            store.add_closes("fred", ids[0], {b: 1.0})
            return {ids[0]: {b: 1.0}}

        self.clients = SimpleNamespace(
            store=store, hyperliquid=Hyperliquid(FakeHttp(answer), store=store),
            polymarket=SimpleNamespace(history=history), kalshi=None,
            fred=SimpleNamespace(series=series), massive=SimpleNamespace(available=False))

    def catalogue(self, lib, c, today, progress):
        c.store.add_markets(self.markets)
        return self.markets


def test_record_adds_only_what_is_new(tmp_path, monkeypatch, gamma_events):
    now = datetime.now(UTC).replace(minute=30, second=0, microsecond=0)
    s = Settings(out_dir=tmp_path / "out", store_dir=tmp_path / "store")
    store = Store(s.store_dir)
    w = World(store, gamma_events, now)
    monkeypatch.setattr("redkeep.pipeline.catalogue", w.catalogue)
    s.out_dir.mkdir()
    used = w.markets[0]
    (s.out_dir / "library-latest.json").write_text(json.dumps({"as_of": "2026-11-08T12:12:00+00:00", "events": [
        {"match": {"used": [{"key": used.key}], "near_misses": [{"key": "kalshi:GONE"}]}}]}))

    rec = record(s, w.clients, now=now, progress=lambda m: None)
    assert rec.warnings == []
    assert rec.added["candles"] == 2 * 71 and rec.added["markets"] == rec.added["quotes"] == len(w.markets)
    assert rec.added["odds"] == 180 * 24 and rec.added["closes"] == 7
    assert "1 no longer listed" in rec.read["Odds"] and rec.read["Massive"] == "skipped: no MASSIVE_API_KEY"

    def ms(t):
        return int(t.timestamp() * 1000)

    assert min(r["startTime"] for r in w.requests) <= ms(now - timedelta(hours=5000))  # all the API keeps

    w.requests.clear()
    later = now + timedelta(hours=1)
    rec = record(s, w.clients, now=later, progress=lambda m: None)
    assert rec.added["candles"] == rec.added["markets"] == rec.added["quotes"] == 0  # nothing moved: nothing new
    assert rec.added["odds"] == 1  # from the last point stored: only the newest hour is new
    # Candles from the last one stored (the one still open then), so one chunk or two per perp, not the whole history.
    assert len(w.requests) <= 4 and all(r["startTime"] <= ms(now - timedelta(hours=2)) for r in w.requests)
    assert all(r["endTime"] == ms(later) for r in w.requests)

    rec = record(s, w.clients, now=now, skip=("perps", "markets", "factors"), progress=lambda m: None)
    assert not any(rec.added.values()) and rec.read == {}


def test_the_record_and_store_commands(tmp_path, monkeypatch, capsys, gamma_events):
    now = datetime.now(UTC)
    s = Settings(out_dir=tmp_path / "out", store_dir=tmp_path / "store")
    w = World(Store(s.store_dir), gamma_events, now)
    monkeypatch.setattr("redkeep.pipeline.catalogue", w.catalogue)
    made = []
    monkeypatch.setattr("redkeep.pipeline.make_clients", lambda s, **kw: made.append(kw) or w.clients)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: s))
    assert cli.main(["record", "--engine-perps", "--skip", "factors", "--compact"]) == 0
    assert made == [{"claude": False, "fresh": True}]  # a recording never comes from the response cache
    out = capsys.readouterr().out
    assert "Hyperliquid: 1 trade[XYZ] perp, 1h candles (71 read)" in out  # S&P 500 only: no WTI or gold perp listed
    assert "71 candles, 3 markets, 3 quotes" in out and "Compacted segments" in out
    assert cli.main(["store"]) == 0
    out = capsys.readouterr().out
    assert "candles" in out and "quotes" in out and "Point-in-time store at" in out


def test_fresh_clients_skip_the_response_cache(tmp_path):
    from redkeep.pipeline import make_clients

    def ttls(c):
        return [x.ttl for x in (c.polymarket, c.kalshi, c.fred, c.massive, c.hyperliquid)]

    s = Settings(cache_dir=tmp_path / "c", store_dir=tmp_path / "store")
    assert ttls(make_clients(s, claude=False, fresh=True)) == [0] * 5
    assert 0 not in ttls(make_clients(s, claude=False))
