"""Exposures, jump windows, past events, the AI prior and fusion, on synthetic data."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from redkeep import factordata
from redkeep.engine.fusion import DeltaReading, OddsReading, assign_tier, fuse_delta, fuse_odds
from redkeep.events import Analog, load_library
from redkeep.evidence import analogs, odds, prior, windows
from redkeep.exposure import fit_line
from redkeep.factors import NF, index

from .synth import business_days, factor_levels, fund_closes

DAYS = business_days(date(2024, 1, 1), date(2026, 11, 6))


@pytest.fixture(scope="module")
def fd():
    return factordata.build(factor_levels(DAYS))


def test_factor_data_aligns_and_measures_typical_moves(fd):
    assert fd.available.all()
    assert fd.X.shape == (len(DAYS) - 1, NF)
    # Weekly typical moves are about sqrt(5) daily moves: SPY's 1% a day is ~2.2% a week.
    assert fd.typical[index("eq")] == pytest.approx(100 * 0.01 * 5**0.5, rel=0.2)
    assert fd.typical[index("rates")] == pytest.approx(100 * 0.06 * 5**0.5, rel=0.2)
    assert "Massive SPY" in fd.sources["eq"] and fd.sources["chips"] == "Massive SMH vs Massive SPY"


def test_exposure_regression_recovers_known_betas(fd):
    b_true = np.array([2.0, -0.8, 0.3, 0.0, 0.5, -0.4, 0.0, 0.6, 1.2, 0.0])
    from redkeep.exposure import returns_on

    y = returns_on(fund_closes(fd, b_true), fd.dates)
    a, b, r2_oos, n, _ = fit_line(y, fd)
    assert n == len(fd.dates)
    assert np.abs(b - b_true).max() < 0.12
    assert r2_oos > 0.9


def test_jump_windows_recover_the_jump():
    rng = np.random.default_rng(5)
    dates = DAYS[-200:]
    T = len(dates)
    true = {"a": np.array([-2.0, 0.5, 0, 0, 1.0, 0, 1.5, 0, 0, 0]), "b": np.array([0.5, -1.0, 0, 0, 0, 0, 0, 0, 0, 0.8])}
    q = {"a": 0.3 + np.zeros(T), "b": 0.5 + np.zeros(T)}
    for e, days in (("a", [20, 60, 61, 120, 150, 180]), ("b", [30, 90, 100, 140, 170])):
        for t in days:
            q[e][t:] += rng.choice([-1, 1]) * rng.uniform(0.06, 0.12)
    dq = {e: np.diff(np.concatenate([[q[e][0]], q[e]])) for e in q}
    X = rng.normal(0, 0.45, (T, NF)) * 0.3 + sum(np.outer(dq[e], true[e]) for e in q)
    fd = factordata.FactorData(levels={}, dates=dates, X=X, typical=np.ones(NF))
    hist = {e: dict(zip(dates, q[e].tolist())) for e in q}
    wf = windows.jump_windows(hist, fd, move=0.05)
    assert set(wf.readings) == {"a", "b"}
    for e in q:
        r = wf.readings[e]
        assert r.kind == "etf" and r.market
        assert np.all(np.abs(r.delta - true[e]) < 4 * r.sigma + 0.1)


def test_odds_read_at_the_new_york_close():
    pts = [(datetime(2026, 11, 5, 20, 30, tzinfo=timezone.utc), 0.40),  # 3:30 pm EST
           (datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc), 0.42),   # 4:00 pm EST
           (datetime(2026, 11, 5, 22, 0, tzinfo=timezone.utc), 0.50)]   # after the close
    assert windows.at_close(pts) == {date(2026, 11, 5): 0.42}


def test_past_events_measure_the_move_on_the_day():
    levels = factor_levels(DAYS, seed=3)
    d0 = date(2025, 4, 3)
    for d in DAYS:  # a 5% drop in the stock proxies from April 3 on
        if d >= d0:
            levels["SPY"][d] *= 0.95
            levels["SP500"][d] *= 0.95
    fd = factordata.build(levels)
    spec = SimpleNamespace(analogs=(Analog(d0, 1, 0.5, "test"),))
    r = analogs.analog_reading(spec, fd)
    expected = (levels["SPY"][d0] / levels["SPY"][date(2025, 4, 2)] - 1) * 100 / fd.typical[index("eq")] / 0.5
    assert r.delta[index("eq")] == pytest.approx(expected, rel=1e-9)
    assert r.kind == "past" and not r.market and r.sigma[index("eq")] > 0.3


def test_market_odds_error_grows_with_spread_and_shrinks_with_volume():
    assert odds.market_sigma(0.01, 5e6) < odds.market_sigma(0.01, 5e4) < odds.market_sigma(0.01, 1e3)
    assert odds.market_sigma(0.10, 5e6) > odds.market_sigma(0.01, 5e6)
    assert odds.calibrate(0.3) == 0.3  # identity until E2 fits the long-shot correction
    assert odds.calibrate(0.05, [(0.05, 0.03), (0.5, 0.5), (0.95, 0.97)]) == pytest.approx(0.03)


def test_fusion_and_tiers():
    one = np.ones(NF)
    a = DeltaReading("etf", "ETF windows", "", one * 1.0, one * 0.2)
    b = DeltaReading("hl", "Weekend windows", "", one * 1.1, one * 0.2)
    c = DeltaReading("ai", "AI prior", "", one * 0.0, one * 0.8)
    f = fuse_delta([a, b, c])
    assert f.n_market == 2 and f.agree
    assert np.allclose(f.delta, (1 / 0.04 + 1.1 / 0.04) / (2 / 0.04 + 1 / 0.64))
    mk = fuse_odds([OddsReading("Kalshi", 0.40, 0.01, "market"), OddsReading("Polymarket", 0.41, 0.012, "market")])
    assert mk.cross_checked and not mk.disagree
    assert assign_tier(odds=mk, delta=f, match_good=True, liquid=True).tier == "A"
    far = DeltaReading("opt", "Options", "", one * 3.0, one * 0.2)
    assert assign_tier(odds=mk, delta=fuse_delta([a, far]), match_good=True, liquid=True).tier == "B"
    split = fuse_odds([OddsReading("Kalshi", 0.30, 0.01, "market"), OddsReading("Polymarket", 0.40, 0.01, "market")])
    assert split.disagree and assign_tier(odds=split, delta=f, match_good=True, liquid=True).tier == "B"
    ai_only = fuse_odds([OddsReading("AI prior", 0.05, 0.03, "ai")])
    assert assign_tier(odds=ai_only, delta=fuse_delta([c]), match_good=False, liquid=False).tier == "D"
    assert assign_tier(odds=mk, delta=fuse_delta([a, c]), match_good=True, liquid=True).tier == "C"
    # The AI never overrides a market's odds.
    assert fuse_odds([OddsReading("Kalshi", 0.40, 0.01, "market"), OddsReading("AI prior", 0.9, 0.01, "ai")]).q == 0.40


def test_ai_prior_request_and_readings(tmp_path):
    out = prior.Prior(probability=0.12, probability_reasoning="r", move_reasoning="m",
                      moves=prior.FactorMoves(eq=-1, rates=-0.5, infl=0.4, usd=0.2, oil=2.0, credit=0.6, vix=1.5, china=-0.3, chips=-0.4, gold=1.0))
    calls = []

    def parse(**kw):
        calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=out, model=kw["model"])

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    ai = prior.AIPrior(client, model="test-model", log_dir=tmp_path)
    spec = load_library().by_id()["hormuz-reopens"]
    got = ai.estimate(spec, date(2026, 11, 8), np.full(NF, 2.0))
    ai.estimate(spec, date(2026, 11, 8), np.full(NF, 2.0))
    assert len(calls) == 1 and calls[0]["fallbacks"] == "default"
    d = prior.delta_reading(got)
    assert d.delta[index("oil")] == 2.0 and d.kind == "ai" and np.all(d.sigma == prior.TAU_DELTA)
    o = prior.odds_reading(got)
    assert o.kind == "ai" and not o.market and o.q == 0.12
