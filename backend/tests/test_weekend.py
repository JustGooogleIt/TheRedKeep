"""Hyperliquid weekend windows: the client, the weekend clock, boundary prices, discovery bounds, the regression."""

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from redkeep.data.hyperliquid import INFO, Candle, Hyperliquid, parse_candle
from redkeep.engine.fusion import DeltaReading, FusedOdds, assign_tier, fuse_delta
from redkeep.evidence import weekend
from redkeep.evidence.weekend import PERPS, PerpData, at_bound, odds_at, price_at, weekend_windows, weekends
from redkeep.factors import FACTORS, NF, index

UTC = timezone.utc
TYPICAL = np.array([f.typical_default for f in FACTORS])


class FakeHttp:
    """Answers POST /info like Hyperliquid, from a function of the request body."""

    def __init__(self, answer):
        self.answer, self.bodies = answer, []

    def post_json(self, url, body, *, headers=None, ttl=0):
        assert url == INFO
        self.bodies.append(body)
        return self.answer(body)


def raw(t: datetime, o, h, l, c, n=12):
    ms = int(t.timestamp() * 1000)
    return {"t": ms, "T": ms + 3_600_000 - 1, "s": "xyz:SP500", "i": "1h", "o": str(o), "h": str(h), "l": str(l),
            "c": str(c), "v": "1.5", "n": n}


def test_candles_parse_strings_and_read_in_chunks():
    t0 = datetime(2026, 9, 1, tzinfo=UTC)

    def answer(body):
        if body["type"] == "meta":
            return {"universe": [{"name": "xyz:SP500", "maxLeverage": 50}, {"name": "GOLD", "maxLeverage": 25}]}
        req = body["req"]
        a, b = req["startTime"], req["endTime"]
        hours = range(0, 24 * 60)
        return [raw(t0 + timedelta(hours=h), 6000, 6001, 5999, 6000.5) for h in hours
                if a <= int((t0 + timedelta(hours=h)).timestamp() * 1000) <= b]

    http = FakeHttp(answer)
    hl = Hyperliquid(http)
    assert set(hl.coins("xyz")) == {"xyz:SP500", "xyz:GOLD"}  # a bare name gets the dex prefix
    cs = hl.candles("xyz:SP500", t0 + timedelta(days=3), t0 + timedelta(days=45))
    assert len(cs) == 42 * 24 + 1 and cs[0].start == t0 + timedelta(days=3)
    assert cs[0].close == 6000.5 and cs[0].trades == 12 and cs[0].end == cs[0].start + timedelta(hours=1, milliseconds=-1)
    reqs = [b["req"] for b in http.bodies if b["type"] == "candleSnapshot"]
    assert all(r["coin"] == "xyz:SP500" and r["interval"] == "1h" for r in reqs)
    assert all(r["endTime"] - r["startTime"] <= 20 * 86_400_000 for r in reqs)  # at most 480 candles a response
    assert parse_candle(raw(t0, "1", "2", "0.5", "1.5")).high == 2.0


def test_weekends_follow_new_york_time_through_the_clock_change():
    got = weekends(datetime(2026, 10, 28, tzinfo=UTC), datetime(2026, 11, 9, tzinfo=UTC))
    # US clocks went back on Sunday Nov 1, 2026: 5 p.m. EDT is 21:00 UTC, 6 p.m. EST is 23:00 UTC.
    assert got == [(datetime(2026, 10, 30, 21, tzinfo=UTC), datetime(2026, 11, 1, 23, tzinfo=UTC)),
                   (datetime(2026, 11, 6, 22, tzinfo=UTC), datetime(2026, 11, 8, 23, tzinfo=UTC))]
    # A weekend still under way at the end of the span is left for the next run.
    assert weekends(datetime(2026, 11, 6, tzinfo=UTC), datetime(2026, 11, 8, 20, tzinfo=UTC)) == []


def test_odds_at_reads_the_last_fresh_point_of_every_leg():
    t = datetime(2026, 11, 6, 22, tzinfo=UTC)
    a = [(t - timedelta(hours=3), 0.30), (t - timedelta(minutes=10), 0.40), (t + timedelta(hours=1), 0.9)]
    b = [(t - timedelta(hours=2), 0.20)]
    assert odds_at([(a, 1.0)], t) == pytest.approx(0.40)
    assert odds_at([(a, 1.0), (b, 1.0)], t) == pytest.approx(0.60)  # a sum of outcomes
    assert odds_at([(a, 0.5), (b, 0.5)], t) == pytest.approx(0.30)  # two ladder rungs
    assert odds_at([(b, 1.0)], t + timedelta(hours=5)) is None  # seven hours stale
    assert odds_at([], t) is None


def hourly(start: datetime, prices: list[float]) -> list[Candle]:
    return [Candle(start + timedelta(hours=i), start + timedelta(hours=i + 1, milliseconds=-1), p, p, p, p, 1.0, 5)
            for i, p in enumerate(prices)]


def test_a_bad_print_cannot_set_the_boundary_price():
    t0 = datetime(2026, 11, 6, 18, tzinfo=UTC)
    cs = hourly(t0, [100, 100, 100, 100, 100])
    ends = [c.end for c in cs]
    assert price_at(cs, ends, t0 + timedelta(hours=3)) == 100
    bad = cs[:2] + [Candle(cs[2].start, cs[2].end, 100, 100, 80, 80, 1.0, 1)] + cs[3:]  # a print 20% low at the close
    assert price_at(bad, ends, t0 + timedelta(hours=3)) == 100
    assert price_at(cs, ends, t0 - timedelta(hours=1)) is None  # nothing traded yet
    assert price_at(cs[:1], ends[:1], t0 + timedelta(hours=1)) is None  # one price alone isn't enough
    # Two prices (the last hour before a gap in trading) must agree, or a bad print can't be told from a move.
    assert price_at(cs[:2], ends[:2], t0 + timedelta(hours=2)) == 100
    assert price_at(bad[:3], ends[:3], t0 + timedelta(hours=3)) is None


def test_moves_near_the_discovery_cap_are_flagged():
    sp, oil = PERPS[0], PERPS[1]
    assert (round(sp.cap_up, 4), round(sp.cap_down, 4)) == (0.0404, 0.0396)
    assert (round(oil.cap_up, 4), round(oil.cap_down, 4)) == (0.1576, 0.1426)  # trade[XYZ]'s own WTI example
    a = datetime(2026, 2, 27, 22, tzinfo=UTC)
    b = a + timedelta(hours=49)
    cs = hourly(a - timedelta(hours=2), [70.0] * 2 + [70.0 * 1.15] * 49)
    starts = [c.start for c in cs]
    assert at_bound(oil, cs, starts, a, b, 70.0)  # +15%: within a quarter step of the +15.8% cap
    assert not at_bound(oil, cs, starts, a, b, 70.0 * 1.10)


def synthetic_weekends(true: dict[str, np.ndarray], seed: int = 3, cap_week: int | None = None, rho: float = 0.0):
    """Odds and perp candles over 26 weekends, with perp moves set by ``true`` jumps plus noise; the stock and gold
    perps' noise correlated ``rho`` (the weekend's other news moving both)."""
    rng = np.random.default_rng(seed)
    start, end = datetime(2026, 5, 11, tzinfo=UTC), datetime(2026, 11, 9, tzinfo=UTC)
    W = weekends(start, end)
    ids = sorted(true)
    q = {e: 0.3 + 0.1 * i for i, e in enumerate(ids)}
    legs: dict[str, list] = {e: [] for e in ids}
    jumps = {e: {i * 3 + 1, i * 3 + 9, i * 3 + 15, i * 3 + 19} for i, e in enumerate(ids)}  # weekends with a sharp move
    level = {"eq": 6000.0, "oil": 70.0, "gold": 3000.0}
    candles = {f: [] for f in level}
    for w, (a, b) in enumerate(W):
        dq = {e: (0.08 * (1 if (w // 2) % 2 else -1) if w in jumps[e] else rng.normal(0, 0.01)) for e in ids}
        for e in ids:
            legs[e].append((a - timedelta(minutes=30), q[e]))
            q[e] = float(np.clip(q[e] + dq[e], 0.02, 0.98))
            legs[e].append((b - timedelta(minutes=30), q[e]))
        news = rng.normal(0, 0.04) if rho else 0.0
        for f in level:
            k = index(f)
            eps = rng.normal(0, 0.04)
            if rho and f in ("eq", "gold"):
                eps = (1 if f == "eq" else np.sign(rho)) * np.sqrt(abs(rho)) * news + np.sqrt(1 - abs(rho)) * eps
            z = sum(true[e][k] * dq[e] for e in ids) + eps
            p0 = level[f]
            p1 = p0 * (1 + z * TYPICAL[k] / 100)
            if f == "oil" and w == cap_week:
                p1 = p0 * 1.155  # pinned at the bound
            n = int((b - a).total_seconds() // 3600)
            path = [p0] * 3 + list(np.linspace(p0, p1, n)) + [p1] * 3
            candles[f] += hourly(a - timedelta(hours=3), path)
            level[f] = p1
    coins = {"eq": "xyz:SP500", "oil": "xyz:CL", "gold": "xyz:GOLD"}
    return {e: [(pts, 1.0)] for e, pts in legs.items()}, PerpData(candles, coins, []), start, end


def test_weekend_windows_recover_the_jump_on_the_covered_factors():
    true = {e: np.zeros(NF) for e in ("cpi-hot", "fed-hike-dec", "israel-iran")}
    true["fed-hike-dec"][[index("eq"), index("oil"), index("gold")]] = [-2.0, 0.3, -0.8]
    true["israel-iran"][[index("eq"), index("oil"), index("gold")]] = [-1.0, 3.0, 1.2]
    legs, perps, start, end = synthetic_weekends(true, cap_week=9)
    fit = weekend_windows(legs, perps, TYPICAL, start, end)
    assert fit.weekends == 26 and len(fit.windows) == 11  # weekend 15 is sharp for two events
    assert set(fit.readings) == set(true)
    assert fit.censored == [(date(2026, 7, 17), "oil")]  # the tenth weekend, pinned at the bound
    for e, r in fit.readings.items():
        assert r.kind == "hl" and r.market and r.n == fit.moves[e] == 4
        assert "S&P 500, WTI and gold perps" in r.detail and "1 left out at a discovery bound" in r.detail
        for f in ("eq", "oil", "gold"):
            k = index(f)
            assert np.isfinite(r.sigma[k]) and abs(r.delta[k] - true[e][k]) < 4 * r.sigma[k] + 0.05, (e, f)
        others = [k for k in range(NF) if FACTORS[k].key not in ("eq", "oil", "gold")]
        assert np.isinf(r.sigma[others]).all()
    assert len(fit.used["oil"]) == len(fit.used["eq"]) - 1 and date(2026, 7, 17) in fit.used["eq"]  # out of oil only


def test_the_weekend_errors_lean_on_how_the_factors_move_on_ordinary_days():
    """Half a year holds too few calm weekends for a correlation of their own, so the few windows' residual
    correlations are shrunk toward the one the refresh measures on every day (``target``), not toward none."""
    true = {e: np.zeros(NF) for e in ("cpi-hot", "fed-hike-dec", "israel-iran")}
    true["fed-hike-dec"][[index("eq"), index("oil"), index("gold")]] = [-2.0, 0.3, -0.8]
    legs, perps, start, end = synthetic_weekends(true, rho=-0.8)
    EQ, GOLD = index("eq"), index("gold")
    T = np.eye(NF)
    T[EQ, GOLD] = T[GOLD, EQ] = -0.8  # how stocks and gold move together on ordinary days, here as on weekends

    def corr(fit):
        C = fit.readings["fed-hike-dec"].cov
        return C[EQ, GOLD] / np.sqrt(C[EQ, EQ] * C[GOLD, GOLD])

    alone = corr(weekend_windows(legs, perps, TYPICAL, start, end))
    leaned = corr(weekend_windows(legs, perps, TYPICAL, start, end, target=T))
    assert abs(leaned + 0.8) < abs(alone + 0.8) and leaned < -0.6


def test_two_market_readings_that_agree_make_tier_a():
    true = {e: np.zeros(NF) for e in ("cpi-hot", "fed-hike-dec", "israel-iran")}
    true["fed-hike-dec"][[index("eq"), index("oil"), index("gold")]] = [-2.0, 0.3, -0.8]
    legs, perps, start, end = synthetic_weekends(true)
    hl = weekend_windows(legs, perps, TYPICAL, start, end).readings["fed-hike-dec"]
    etf = DeltaReading("etf", "Jump windows", "", true["fed-hike-dec"] + 0.05, np.full(NF, 0.4))
    fused = fuse_delta([etf, hl])
    assert fused.n_market == 2 and fused.dof == 3 and fused.agree
    odds = FusedOdds(0.76, 0.02, [], from_market=True, cross_checked=True, disagree=False)
    assert assign_tier(odds=odds, delta=fused, match_good=True, liquid=True).tier == "A"
    # A weekend reading that points the other way is a contest, not a confirmation.
    flipped = DeltaReading("hl", "Hyperliquid windows", "", -hl.delta, hl.sigma)
    assert assign_tier(odds=odds, delta=fuse_delta([etf, flipped]), match_good=True, liquid=True).tier == "B"


def test_no_listing_means_no_weekend_windows():
    def answer(body):
        return {"universe": [{"name": "xyz:NVDA", "maxLeverage": 20}, {"name": "xyz:GOLD", "maxLeverage": 10}]}

    t = datetime(2026, 11, 8, tzinfo=UTC)
    got = weekend.load(Hyperliquid(FakeHttp(lambda b: answer(b) if b["type"] == "meta" else [])), t - timedelta(days=30), t)
    assert set(got.coins) == {"gold"} and got.candles["gold"] == []
    assert any("none of xyz:SP500" in w for w in got.warnings) and any("xyz:CL, xyz:WTIOIL" in w for w in got.warnings)
    assert any("xyz:GOLD lists max leverage 10" in w for w in got.warnings)  # so its bounds may have changed too
    fit = weekend_windows({"x": [([(t, 0.5)], 1.0)]}, got, TYPICAL, t - timedelta(days=30), t)
    assert fit.readings == {} and fit.used == {}


def test_check_reports_the_perps_it_found(monkeypatch, capsys):
    from types import SimpleNamespace

    from redkeep import cli, pipeline
    from redkeep.config import Settings

    t = datetime.now(UTC)

    def answer(body):
        if body["type"] == "meta":
            return {"universe": [{"name": "xyz:SP500", "maxLeverage": 50}, {"name": "xyz:GOLD", "maxLeverage": 25}]}
        return [raw(t - timedelta(hours=h), 6000, 6001, 5999, 6000) for h in range(1, 24)]

    c = SimpleNamespace(polymarket=SimpleNamespace(search=lambda q, limit: [{}]),
                        kalshi=SimpleNamespace(http=SimpleNamespace(get_json=lambda url, params: {"events": [{}]})),
                        fred=SimpleNamespace(series=lambda ids, a, b: {"DGS10": {a: 4.1}}),
                        massive=SimpleNamespace(available=False), hyperliquid=Hyperliquid(FakeHttp(answer)), model=None)
    monkeypatch.setattr(pipeline, "make_clients", lambda s, **kw: c)
    assert cli.cmd_check(Settings(), None) == 0
    assert "xyz:SP500 50x, no WTI perp, xyz:GOLD 25x; 23 hourly xyz:SP500 candles in the last day" in capsys.readouterr().out
