"""The long-shot study (E2): the fit, the out-of-sample guard, the market readers, and the command, offline."""

import hashlib
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from redkeep import cli
from redkeep.config import Settings
from redkeep.data.kalshi import Kalshi, candle_price
from redkeep.data.polymarket import Polymarket
from redkeep.evidence import calibration as cal
from redkeep.evidence import resolved as rs
from redkeep.http import Http

from .conftest import FIX

UTC = timezone.utc


@pytest.fixture(scope="module")
def fx():
    return json.loads((FIX / "resolved.json").read_text())


def stub(handler) -> Http:
    return Http(transport=httpx.MockTransport(handler), sleep=lambda s: None)


def biased(n: int, b: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Prices and outcomes where a price q comes true with probability expit(b · logit(q))."""
    rng = np.random.default_rng(seed)
    q = cal.expit(rng.normal(0, 1.5, n))
    y = (rng.random(n) < cal.expit(b * cal.logit(q, 1e-9))).astype(int)
    return q, y


def obs_at(h: int, q, y, *, first: date, days: int = 600, tag: str = "") -> list[cal.Obs]:
    """One observation per market, closing evenly over ``days`` days from ``first``; each market is its own event."""
    return [cal.Obs("kalshi", f"kalshi:{tag}{h}-{i}", f"kalshi:{tag}{h}-{i}", "Economics", first + timedelta(days=i * days // len(q)),
                    h, float(qi), int(yi)) for i, (qi, yi) in enumerate(zip(q, y))]


# -- the model ---------------------------------------------------------------------------------------


def test_the_fit_recovers_a_planted_long_shot_bias():
    q, y = biased(20_000, 1.3, seed=1)
    line = cal.fit_line(q, y)
    assert line.a == 0.0 and abs(line.b - 1.3) < 3 * line.se_b and line.se_b < 0.03
    two = cal.fit_line(q, y, intercept=True)  # no bias toward yes was planted, so the intercept is near zero
    assert abs(two.a) < 3 * two.se_a and abs(two.b - 1.3) < 3 * two.se_b
    # A sample a line separates perfectly still gives a finite slope, pulled toward raw prices.
    sep = cal.fit_line([0.2, 0.3, 0.7, 0.8], [0, 0, 1, 1])
    assert 1.0 < sep.b < 10 and math.isfinite(sep.se_b)


def test_calibrated_odds_treat_yes_and_no_alike():
    c = cal.Calibration({1: 1.0, 7: 1.2, 30: 1.5, 90: 1.5, 180: 1.0})
    assert c.slope(math.sqrt(7 * 30)) == pytest.approx(1.35)  # halfway between 7 and 30 days, in log days
    assert c.slope(0) == 1.0 and c.slope(400) == 1.0  # flat beyond the ends
    assert c.p_hat(0.05, 30) < 0.05 and c.p_hat(0.95, 30) > 0.95  # b > 1 shades long shots down, favorites up
    for q in (0.03, 0.2, 0.5, 0.8):
        assert c.p_hat(1 - q, 45) == pytest.approx(1 - c.p_hat(q, 45))
    assert c.p_hat(0.5, 30) == pytest.approx(0.5)
    assert c.p_hat(0.137, 1) == 0.137 and cal.IDENTITY.p_hat(0.137, 30) == 0.137  # slope 1 returns the price itself
    assert c.p_hat(0.0, 30) == 0.0 and c.p_hat(1.0, 30) == 1.0


def test_the_study_uses_a_slope_only_where_it_reliably_beats_raw_odds_out_of_sample():
    first = date(2025, 1, 1)
    obs = obs_at(30, *biased(1500, 1.5, seed=2), first=first, days=1000)  # a steady bias: the slope helps later on too
    obs += obs_at(7, *biased(1000, 1.8, seed=3), first=first, days=700, tag="a")  # a bias that reverses after the split
    obs += obs_at(7, *biased(500, 0.6, seed=4), first=first + timedelta(days=700), days=300, tag="b")
    obs += obs_at(90, *biased(60, 1.5, seed=5), first=first, days=60)  # too few
    rep = cal.study(obs, boot=300)
    by = {r["days"]: r for r in rep["horizons"]}
    r30 = by[30]
    assert r30["used"] and abs(r30["b"] - 1.5) < 3 * r30["se"] and 0 < r30["gain"][1] < r30["gain"][0] < r30["gain"][2]
    assert r30["corrected"]["log_loss"] < r30["raw"]["log_loss"]
    assert {x["n"] for x in r30["reliability"]["raw"]} and sum(x["n"] for x in r30["reliability"]["raw"]) == r30["test"]
    r7 = by[7]
    assert r7["train"] >= 1000 and r7["test"] >= 450  # the 70% split falls just after the reversal
    assert not r7["used"] and r7["b"] == 1.0 and r7["note"].startswith("raw prices scored as well") and r7["fit_b"] > 1.3
    assert not by[90]["used"] and by[90]["note"].startswith("too few markets")
    assert not by[1]["used"] and by[1]["train"] == 0
    assert rep["observations"] == len(obs) and rep["markets"] == {"kalshi": len({o.market for o in obs})}
    # A gain whose 95% range includes zero isn't enough either: a mild bias in a small sample stays raw.
    unsure = cal.study(obs_at(180, *biased(800, 1.1, seed=9), first=first, days=1000, tag="u"), boot=300)
    r180 = next(r for r in unsure["horizons"] if r["days"] == 180)
    assert r180["gain"][1] < 0 < r180["gain"][0] and not r180["used"] and r180["b"] == 1.0 and "includes zero" in r180["note"]

    # The file the engine reads keeps the slopes in use; raw odds elsewhere.
    doc = cal.to_json(rep, "2026-11-08")
    assert doc["kind"] == "redkeep.calibration" and [h["days"] for h in doc["horizons"]] == list(cal.HORIZONS)
    assert "se" not in next(h for h in doc["horizons"] if h["days"] == 1)


def test_the_calibration_file_round_trips(tmp_path):
    assert cal.load(tmp_path / "missing.json") is cal.IDENTITY
    rep = cal.study(obs_at(30, *biased(1500, 1.5, seed=2), first=date(2025, 1, 1)), boot=100)
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(cal.to_json(rep, "2026-11-08")))
    c = cal.load(path)
    assert c.slopes[30] == pytest.approx(next(r["b"] for r in rep["horizons"] if r["days"] == 30))
    assert c.slopes[30] > 1.2 and c.slopes[1] == c.slopes[7] == c.slopes[90] == c.slopes[180] == 1.0
    assert c.summary() == {"fitted": "2026-11-08", "markets": 1500, "slopes": {"1": 1.0, "7": 1.0, "30": round(c.slopes[30], 4),
                                                                                "90": 1.0, "180": 1.0}}
    path.write_text(json.dumps({"kind": "something else"}))
    with pytest.raises(ValueError, match="not a calibration file"):
        cal.load(path)
    obs = obs_at(7, *biased(20, 1.0, seed=9), first=date(2025, 3, 1))
    cal.write_obs(obs, tmp_path / "obs.jsonl.gz")
    assert cal.read_obs(tmp_path / "obs.jsonl.gz") == obs


# -- which markets, and their prices --------------------------------------------------------------------


def test_polymarket_resolved_markets(fx):
    got = [m for ev in fx["polymarket_closed_events"] for m in rs.from_polymarket(ev)]
    assert [(m.id, m.y) for m in got] == [("530001", 0), ("530002", 1)]  # not 0.0005/0.9995, a proposal, two names or sports
    m = got[0]
    assert m.category == "Economics" and m.group == "polymarket:Economics:2025-06" and m.event == "polymarket:910001"
    assert m.deadline == datetime(2025, 6, 18, 12, tzinfo=UTC) and m.closed == datetime(2025, 6, 18, 18, 31, 7, tzinfo=UTC)
    assert m.opened == datetime(2025, 3, 2, 17, 5, tzinfo=UTC) and m.token.endswith("01") and m.volume == 900_000

    prices = Polymarket(stub(lambda req: httpx.Response(200, json=fx["polymarket_daily"]))).daily(m.token)
    o = rs.observations(m, prices)
    # 1 day before: the price stamped that midnight. 7 days: the last price, 2.5 days old. 30 and 90: nothing yet.
    # 180: not listed yet.
    assert [(x.h, x.q) for x in o] == [(1, 0.01), (7, 0.035)]
    assert o[0] == cal.Obs("polymarket", "polymarket:530001", "polymarket:910001", "Economics", date(2025, 6, 18), 1, 0.01, 0,
                           m.title, date(2025, 6, 18))


def test_polymarket_reads_closed_events_by_tag_and_month(fx):
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json=fx["polymarket_closed_events"][:1])

    pm = Polymarket(stub(handler))
    assert [e["id"] for e in pm.closed_events("economy", date(2025, 6, 1), date(2025, 7, 1))] == ["910001"]
    assert seen == [{"closed": "true", "tag_slug": "economy", "order": "volume", "ascending": "false", "limit": "20",
                     "end_date_min": "2025-06-01", "end_date_max": "2025-07-01"}]


def test_kalshi_settled_markets_and_their_candles(fx):
    assert [s["ticker"] for s in fx["kalshi_series"]["series"] if rs.keep_series(s)] == ["KXCPIYOY", "KXRECSSNBER"]
    old = [rs.from_kalshi(m, series="KXCPIYOY", category="Economics", archived=True) for m in fx["kalshi_settled_historical"]["markets"]]
    assert old[2] is None  # not settled yes or no
    m = old[0]
    assert (m.y, m.volume, m.event, m.group, m.archived) == (0, 152_000, "kalshi:KXCPIYOY-25JUN", "kalshi:KXCPIYOY", True)
    assert m.deadline == datetime(2025, 7, 15, 14, tzinfo=UTC) and m.closed == datetime(2025, 7, 15, 12, 29, tzinfo=UTC)
    assert old[1].y == 1 and old[1].volume == 3100
    assert rs.from_kalshi(fx["kalshi_settled_historical"]["markets"][0], series="KXCPI", category="Economics", archived=True) is None
    assert rs.open_at(m) == [1, 7, 30]  # listed June 11, so not yet trading 90 or 180 days before
    assert rs.window(m) == (datetime(2025, 6, 11, 14, tzinfo=UTC), datetime(2025, 7, 14, 15, tzinfo=UTC))

    # Archived candles name prices without "_dollars" and still give dollars; live ones carry "_dollars".
    hist, live = fx["kalshi_candles_historical"]["candlesticks"], fx["kalshi_candles_live"]["candlesticks"]
    assert candle_price(hist[0]) == pytest.approx(0.06) and candle_price(hist[1]) == pytest.approx(0.04)
    assert candle_price(live[0]) == pytest.approx(0.42) and candle_price(live[1]) == pytest.approx(0.42)  # no quotes: the last trade
    assert candle_price({"yes_bid": {"close": 41}, "yes_ask": {"close": 43}}) == pytest.approx(0.42)  # old whole cents

    calls = []

    def handler(req):
        calls.append((req.url.path, dict(req.url.params)))
        if req.url.path.endswith("/candlesticks"):
            return httpx.Response(200, json=fx["kalshi_candles_historical" if "/historical/" in req.url.path else "kalshi_candles_live"])
        if req.url.path.endswith("/series"):
            return httpx.Response(200, json=fx["kalshi_series"])
        if req.url.path.endswith("/historical/markets"):
            return httpx.Response(200, json=fx["kalshi_settled_historical"])
        page = {**fx["kalshi_settled_live"], "cursor": "" if req.url.params.get("cursor") else "next"}
        return httpx.Response(200, json=page)

    ks = Kalshi(stub(handler))
    assert len(ks.series("Economics")) == 5 and calls[-1] == ("/trade-api/v2/series", {"category": "Economics"})
    assert len(ks.settled("KXCPIYOY", archived=True)) == 3
    assert calls[-1] == ("/trade-api/v2/historical/markets", {"series_ticker": "KXCPIYOY", "limit": "200"})
    assert len(ks.settled("KXCPIYOY", archived=False)) == 2  # two pages, through the cursor
    assert calls[-1] == ("/trade-api/v2/markets", {"series_ticker": "KXCPIYOY", "limit": "200", "cursor": "next", "status": "settled"})
    got = ks.daily("KXCPIYOY", m.id, *rs.window(m), archived=True)
    assert calls[-1][0] == "/trade-api/v2/historical/markets/KXCPIYOY-25JUN-T2.8/candlesticks"
    assert calls[-1][1] == {"start_ts": str(int(rs.window(m)[0].timestamp())), "end_ts": str(int(rs.window(m)[1].timestamp())),
                            "period_interval": "1440"}
    assert got == [(datetime(2025, 7, 6, 4, tzinfo=UTC), pytest.approx(0.06)), (datetime(2025, 7, 7, 4, tzinfo=UTC), pytest.approx(0.04))]
    # 7 days before the deadline (July 8, 14:00) the last price is July 7's; a day before, it is a week old.
    assert [(o.h, o.q) for o in rs.observations(m, got)] == [(7, pytest.approx(0.04))]
    ks.daily("KXCPIYOY", "KXCPIYOY-25SEP-T3.0", datetime(2025, 10, 1, tzinfo=UTC), datetime(2025, 10, 23, tzinfo=UTC), archived=False)
    assert calls[-1][0] == "/trade-api/v2/series/KXCPIYOY/markets/KXCPIYOY-25SEP-T3.0/candlesticks"


def test_observations_only_while_the_market_traded():
    deadline = datetime(2025, 9, 30, 20, tzinfo=UTC)
    r = rs.Resolved("kalshi", "X-1", "kalshi:X", "kalshi:X", "Economics", "", opened=deadline - timedelta(days=60),
                    deadline=deadline, closed=deadline - timedelta(days=3), y=1, volume=1e5, series="X")
    assert rs.open_at(r) == [7, 30]  # closed 3 days early: no price 1 day before; listed 60 days before: none at 90 or 180
    days = [datetime.combine(deadline.date() - timedelta(days=d), time(4), UTC) for d in range(70, 2, -1)]
    o = rs.observations(r, [(t, 0.3) for t in days])
    assert [x.h for x in o] == [7, 30] and {x.closed for x in o} == {date(2025, 9, 27)}
    # A price stamped up to an hour after the moment counts; later ones don't, and one over three days old doesn't either.
    t7 = deadline - timedelta(days=7)
    assert rs.observations(r, [(t7 + timedelta(minutes=30), 0.4)], [7])[0].q == 0.4
    assert rs.observations(r, [(t7 + timedelta(hours=2), 0.4)], [7]) == []
    assert rs.observations(r, [(t7 - timedelta(days=3, hours=1), 0.4)], [7]) == []


def test_the_sample_is_even_and_repeatable():
    def mk(i, event, group):
        t = datetime(2025, 5, 1, tzinfo=UTC)
        return rs.Resolved("kalshi", f"M{i}", event, group, "Economics", "", None, t, t, 0, 1e4)

    ladder = [mk(i, "kalshi:E0", "kalshi:S0") for i in range(30)]
    series = [mk(100 + i, f"kalshi:E{1 + i}", "kalshi:S1") for i in range(80)]
    spread = [mk(1000 + i, f"kalshi:F{i}", f"kalshi:T{i}") for i in range(500)]
    every = ladder + series + spread
    s = rs.sample(every, total=1000)
    count = lambda key, v: sum(getattr(m, key) == v for m in s)  # noqa: E731
    assert count("event", "kalshi:E0") == 3 and count("group", "kalshi:S1") == 40 and len(s) == 3 + 40 + 500
    assert rs.sample(list(reversed(every)) + every[:50], total=1000) == s  # order and duplicates don't matter
    assert rs.sample(every, total=100) == s[:100]


# -- collection and the command ------------------------------------------------------------------------


SLOPE = 1.6  # the planted bias: a price q comes true with probability expit(1.6 · logit(q))


def planted(key: str) -> tuple[float, int]:
    rng = np.random.default_rng(int(hashlib.sha256(key.encode()).hexdigest()[:12], 16))
    q = float(cal.expit(rng.normal(0, 1.5)))
    return round(q, 3), int(rng.random() < cal.expit(SLOPE * cal.logit(q, 1e-9)))


def daily_points(start: datetime, end: datetime, q: float, hour: int) -> list[tuple[datetime, float]]:
    d, out = start.date(), []
    while d <= end.date():
        t = datetime.combine(d, time(hour), UTC)
        if start <= t <= end:
            out.append((t, q))
        d += timedelta(days=1)
    return out


class FakePolymarket:
    """Twenty closed economy events a month, three markets each, listed 120 days before their mid-month deadline."""

    def __init__(self):
        self.prices: dict[str, tuple[float, datetime, datetime]] = {}

    def closed_events(self, tag, start, end, *, limit=20):
        if tag != "economy":
            return []
        deadline = datetime.combine(start + timedelta(days=14), time(12), UTC)
        events = []
        for j in range(limit):
            eid = f"{start:%Y%m}{j:02d}"
            markets = []
            for k in range(3):
                mid = f"{eid}{k}"
                q, y = planted(f"polymarket:{mid}")
                opened, closed = deadline - timedelta(days=120), deadline + timedelta(hours=6)
                self.prices[f"{mid}-yes"] = (q, opened, closed)
                markets.append({"id": mid, "question": f"Market {mid}?", "endDate": deadline.isoformat(),
                                "acceptingOrdersTimestamp": opened.isoformat(), "closedTime": closed.isoformat(),
                                "outcomes": '["Yes", "No"]', "outcomePrices": '["1", "0"]' if y else '["0", "1"]',
                                "clobTokenIds": json.dumps([f"{mid}-yes", f"{mid}-no"]), "volumeNum": 50_000,
                                "closed": True, "umaResolutionStatus": "resolved"})
            events.append({"id": eid, "title": f"Event {eid}", "endDate": deadline.isoformat(), "tags": [{"slug": "economy"}],
                           "markets": markets})
        return events

    def daily(self, token):
        q, opened, closed = self.prices[token]
        return daily_points(opened, closed - timedelta(hours=1), q, 0)


class FakeKalshi:
    """Thirty monthly series, each with a settled four-strike event a month; plus a daily series and combinations."""

    CUTOFF = datetime(2025, 9, 7, tzinfo=UTC)

    def __init__(self, n_series=30):
        self.n = n_series
        self.markets: dict[str, dict] = {}
        self.read: list[str] = []

    def series(self, category):
        if category != "Economics":
            return [{"ticker": "KXNBAGAME", "frequency": "custom", "category": "Sports"}]
        return ([{"ticker": f"KXS{i:02d}", "frequency": "monthly", "category": "Economics"} for i in range(self.n)]
                + [{"ticker": "KXDAILY", "frequency": "daily", "category": "Economics"},
                   {"ticker": "KXMVECOMBO", "frequency": "custom", "category": "Economics"}])

    def settled(self, series, *, archived):
        assert series.startswith("KXS")
        if not archived:
            self.read.append(series)
        out = []
        for month in range(1, 13):
            deadline = datetime(2025, month, 20, 14, tzinfo=UTC)
            if (deadline < self.CUTOFF) != archived:
                continue
            ev = f"{series}-25M{month:02d}"
            for k in range(4):
                tk = f"{ev}-T{k}"
                q, y = planted(f"kalshi:{tk}")
                m = {"ticker": tk, "event_ticker": ev, "market_type": "binary", "title": f"{tk}?", "status": "finalized",
                     "result": "yes" if y else "no", "open_time": (deadline - timedelta(days=100)).isoformat(),
                     "close_time": (deadline - timedelta(hours=1)).isoformat(),
                     "expected_expiration_time": deadline.isoformat(), "volume_fp": "10000.00"}
                self.markets[tk] = {**m, "q": q, "archived": archived}
                out.append(m)
        return out

    def daily(self, series, ticker, start, end, *, archived):
        m = self.markets[ticker]
        assert archived == m["archived"] and ticker.startswith(series)
        return daily_points(start, end, m["q"], 4)


def test_collect_reads_both_venues_and_keeps_going_when_one_fails():
    obs, stats, warnings = rs.collect(FakePolymarket(), FakeKalshi(), date(2025, 1, 1), date(2025, 12, 31), progress=lambda _: None)
    pm, ks = stats["polymarket"], stats["kalshi"]
    assert warnings == []
    assert pm["sampled"] == 12 * 40 and pm["priced"] == pm["sampled"]  # 40 a month: the cap on a tag and month
    assert ks["series"] == 30 and ks["series_read"] == 30 and ks["sampled"] == 30 * 12 * 3  # three of each event's four
    assert ks["priced"] == ks["sampled"] and ks["failed"] == 0
    by_h = {h: sum(o.h == h for o in obs) for h in cal.HORIZONS}
    assert by_h[180] == 0 and by_h[90] == pm["sampled"] + ks["sampled"]  # listed 120 and 100 days before their deadlines
    assert {o.venue for o in obs} == {"polymarket", "kalshi"}

    class Down(FakeKalshi):
        def series(self, category):
            raise RuntimeError("Kalshi is down")

    obs2, stats2, warnings2 = rs.collect(FakePolymarket(), Down(), date(2025, 1, 1), date(2025, 12, 31), progress=lambda _: None)
    assert list(stats2) == ["polymarket"] and warnings2 == ["kalshi: RuntimeError: Kalshi is down"]
    assert len(obs2) == sum(o.venue == "polymarket" for o in obs)


def test_kalshi_stops_reading_series_once_the_sample_is_full():
    ks = FakeKalshi()
    _, st = rs.collect_kalshi(ks, date(2025, 1, 1), date(2025, 12, 31), total=100, min_series=10, progress=lambda _: None)
    assert st["series_read"] == 25 and st["sampled"] == 100 and len(ks.read) == 25


def test_the_calibrate_command(tmp_path, monkeypatch, capsys):
    s = Settings(cache_dir=tmp_path / ".cache", out_dir=tmp_path / "out", calibration_file=tmp_path / "calibration.json")
    monkeypatch.setattr(cli.Settings, "from_env", classmethod(lambda cls: s))
    monkeypatch.setattr("redkeep.pipeline.make_clients",
                        lambda s, claude=True: SimpleNamespace(polymarket=FakePolymarket(), kalshi=FakeKalshi()))
    assert cli.main(["calibrate", "--since", "2025-01-01", "--until", "2025-12-31", "--write"]) == 0
    out = capsys.readouterr().out
    rep = json.loads((s.out_dir / "calibration-latest.json").read_text())
    assert rep["kind"] == "redkeep.calibration-study" and rep["sources"]["venues"]["kalshi"]["sampled"] == 1080
    assert rep["markets"] == {"kalshi": 1080, "polymarket": 480}
    by = {r["days"]: r for r in rep["horizons"]}
    for h in (1, 7, 30, 90):
        assert by[h]["used"] and abs(by[h]["b"] - SLOPE) < 3 * by[h]["se"], by[h]
    assert not by[180]["used"] and by[180]["note"].startswith("too few")
    assert "Calibration: 1,080 Kalshi, 480 Polymarket resolved markets" in out and "too few markets" in out

    c = cal.load(s.calibration_file)
    assert c.slopes[30] == pytest.approx(by[30]["b"]) and c.slopes[180] == 1.0 and c.markets == 1560
    obs_file = s.out_dir / rep["sources"]["observations"]
    assert obs_file.exists() and len(cal.read_obs(obs_file)) == rep["observations"]

    # A rerun on the saved prices gives the same study without fetching anything.
    monkeypatch.setattr("redkeep.pipeline.make_clients", lambda *a, **k: pytest.fail("fetched"))
    assert cli.main(["calibrate", "--from", str(obs_file)]) == 0
    again = json.loads((s.out_dir / "calibration-latest.json").read_text())
    assert [r["b"] for r in again["horizons"]] == [r["b"] for r in rep["horizons"]]

    empty = tmp_path / "empty.jsonl.gz"
    cal.write_obs([], empty)
    assert cli.main(["calibrate", "--from", str(empty)]) == 1
    assert "no resolved markets" in capsys.readouterr().err
