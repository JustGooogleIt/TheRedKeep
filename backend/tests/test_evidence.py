"""Exposures, jump windows, past events, the AI prior and fusion, on synthetic data."""

from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from redkeep import factordata
from redkeep.engine.fusion import (QUORUM_SE, DeltaReading, FusedOdds, OddsReading, assign_tier, fuse_delta,
                                    fuse_odds, size_error)
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


def window_world():
    """Two events whose odds jump on set days, and factor moves driven by those jumps plus noise."""
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
    return {e: dict(zip(dates, q[e].tolist())) for e in q}, fd, true


def test_jump_windows_recover_the_jump():
    hist, fd, true = window_world()
    wf = windows.jump_windows(hist, fd, move=0.05)
    assert set(wf.readings) == {"a", "b"} and wf.left_out == 0
    for e in hist:
        r = wf.readings[e]
        assert r.kind == "etf" and r.market
        assert np.all(np.abs(r.delta - true[e]) < 4 * r.sigma + 0.1)


def test_days_left_to_the_weekend_perps_drop_out_of_that_factor_only():
    hist, fd, true = window_world()
    base = windows.jump_windows(hist, fd, move=0.05)
    left = {fd.dates[t] for t in (20, 60, 120, 30, 90, 100)}  # three of a's six sharp days, three of b's five
    wf = windows.jump_windows(hist, fd, move=0.05, skip={index("eq"): left}, left_to="the S&P 500 perp")
    assert wf.left_out == 6 and wf.windows == base.windows
    for e in hist:
        r, r0 = wf.readings[e], base.readings[e]
        assert np.array_equal(r.delta[1:], r0.delta[1:]) and np.array_equal(r.sigma[1:], r0.sigma[1:])  # other factors
        assert r.detail == r0.detail + "; 6 that span a weekend are left to the S&P 500 perp"
    a, b = wf.readings["a"], wf.readings["b"]
    assert base.readings["a"].sigma[0] < a.sigma[0] < np.inf  # three windows left: a wider error bar
    assert abs(a.delta[0] - true["a"][0]) < 4 * a.sigma[0] + 0.1
    assert np.isinf(b.sigma[0])  # two sharp moves left on this factor, below the three a reading needs


def test_a_factor_missing_some_days_keeps_its_reading_from_the_rest():
    hist, fd, true = window_world()
    base = windows.jump_windows(hist, fd, move=0.05)
    g = index("gold")
    X = fd.X.copy()
    X[:100, g] = np.nan  # gold's prices start on day 100: a's last three sharp days and b's last three count
    wf = windows.jump_windows(hist, replace(fd, X=X), move=0.05)
    assert wf.windows == base.windows
    others = np.arange(NF) != g
    for e in hist:
        r, r0 = wf.readings[e], base.readings[e]
        assert np.array_equal(r.delta[others], r0.delta[others]) and np.array_equal(r.sigma[others], r0.sigma[others])
        assert np.isfinite(r.sigma[g]) and abs(r.delta[g] - true[e][g]) < 4 * r.sigma[g] + 0.1


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
    # One past event can't pin the jump's size: its error is at least half the move it measured.
    assert r.sigma[index("eq")] > 0.5 * abs(r.delta[index("eq")]) > 1.5


def test_market_odds_error_grows_with_spread_and_shrinks_with_volume():
    assert odds.market_sigma(0.01, 5e6) < odds.market_sigma(0.01, 5e4) < odds.market_sigma(0.01, 1e3)
    assert odds.market_sigma(0.10, 5e6) > odds.market_sigma(0.01, 5e6)


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


def test_a_market_jump_that_points_against_the_other_evidence_is_contested():
    kalshi = fuse_odds([OddsReading("Kalshi", 0.40, 0.01, "market")])
    eq, one = index("eq"), np.ones(NF)

    def on_stocks(x, sigma, kind="ai", name="AI prior"):
        return DeltaReading(kind, name, "", np.where(np.arange(NF) == eq, x, 0.2), one * sigma)

    windows_ = on_stocks(3.6, 1.0, "etf", "Jump windows")
    f = fuse_delta([windows_, on_stocks(-1.0, 0.9)])
    assert f.conflict.factor == "eq" and f.conflict.factors == ["eq"]
    assert f.conflict.z == pytest.approx(4.6 / np.hypot(1.0, 0.9))  # 3.4 > 2.8
    # On stocks the jump rests on the AI alone; elsewhere both readings still count.
    assert f.delta[eq] == pytest.approx(-1.0) and f.sigma[eq] == pytest.approx(0.9) and f.weights["Jump windows"][eq] == 0
    assert np.allclose(np.delete(f.delta, eq), 0.2) and f.weights["Jump windows"][index("rates")] == pytest.approx(0.81 / 1.81)
    tier = assign_tier(odds=kalshi, delta=f, match_good=True, liquid=True)
    assert tier.tier == "B"
    assert tier.reason.startswith("US stocks: the jump windows say +3.6 typical moves and the AI prior -1.0, opposite ways")
    assert tier.reason.endswith("so on US stocks the jump rests on the AI prior alone.")
    # The same side at a different size isn't a contest, nor are opposite sides within the error bars (2.1 < 2.8).
    assert fuse_delta([windows_, on_stocks(8.2, 0.9)]).conflict is None
    near = fuse_delta([windows_, on_stocks(-1.0, 2.0)])
    assert near.conflict is None and assign_tier(odds=kalshi, delta=near, match_good=True, liquid=True).tier == "C"


def test_only_precise_market_readings_count_toward_the_quorum():
    jump = np.zeros(NF)
    jump[[index("eq"), index("rates"), index("oil")]] = [-2.0, 1.5, 1.0]
    etf = DeltaReading("etf", "ETF windows", "", jump, np.full(NF, 0.4))
    perps = np.full(NF, np.inf)
    perps[[index("eq"), index("oil"), index("gold")]] = 3.0
    vague = DeltaReading("hl", "Weekend windows", "", jump * 0.2, perps)  # sees little, with wide error bars
    odds = FusedOdds(0.6, 0.02, [], from_market=True, cross_checked=False, disagree=False)

    def tier(f):
        return assign_tier(odds=odds, delta=f, match_good=True, liquid=True)

    # A vague reading agrees with anything, so it can't help confirm the jump: one precise reading is tier C.
    f = fuse_delta([etf, vague])
    assert f.agree and f.n_market_all == 2 and f.n_market == 1
    assert tier(f).tier == "C" and tier(f).reason.startswith("Only one market reading of the jump is precise enough")
    # Precise means an error bar on the jump's size of at most half the jump (the app's projection).
    assert size_error(1 / perps ** 2, f.delta) > QUORUM_SE >= size_error(np.full(NF, 1 / 0.4 ** 2), f.delta)
    assert size_error(np.zeros(NF), f.delta) == np.inf
    # Two vague readings confirm nothing.
    f = fuse_delta([DeltaReading("etf", "ETF windows", "", jump, np.full(NF, 3.0)), vague])
    assert f.n_market == 0 and f.n_market_all == 2
    assert tier(f).tier == "C" and tier(f).reason.startswith("The market readings of the jump are too vague")
    # A precise weekend reading that agrees makes two: tier A.
    sharp = DeltaReading("hl", "Weekend windows", "", jump * 1.1, np.where(np.isfinite(perps), 0.5, np.inf))
    f = fuse_delta([etf, sharp])
    assert f.n_market == 2 and f.agree and tier(f).tier == "A"
    # A vague reading can still contest a jump that it rules out.
    against = DeltaReading("hl", "Weekend windows", "", -jump * 4, perps)
    f = fuse_delta([etf, against])
    assert f.n_market == 1 and f.agree is False and tier(f).tier == "B"
    # Precise readings of different factors can't check each other.
    eq_only = DeltaReading("etf", "ETF windows", "", jump, np.where(np.arange(NF) == index("eq"), 0.4, np.inf))
    oil_only = DeltaReading("hl", "Weekend windows", "", jump, np.where(np.arange(NF) == index("oil"), 0.3, np.inf))
    f = fuse_delta([eq_only, oil_only])
    assert f.n_market == 2 and f.agree is None and tier(f).tier == "C" and "different factors" in tier(f).reason


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
    assert d.delta[index("oil")] == 2.0 and d.kind == "ai"
    # Wide errors that grow with the estimated move: 0.8 typical moves and half the move, in quadrature.
    assert d.sigma[index("oil")] == pytest.approx(np.hypot(prior.TAU_DELTA, 1.0))
    assert np.all(d.sigma >= prior.TAU_DELTA) and d.sigma[index("usd")] < d.sigma[index("vix")]
    o = prior.odds_reading(got)
    assert o.kind == "ai" and not o.market and o.q == 0.12


def test_the_prior_is_held_inside_a_range_related_markets_imply():
    p = prior.Prior(probability=0.03, probability_reasoning="Base rate.", move_reasoning="m",
                    moves=prior.FactorMoves(eq=0, rates=0, infl=0, usd=0, oil=0, credit=0, vix=0, china=0, chips=0, gold=0))
    held = prior.odds_reading(p, lo=0.04)
    assert held.q == pytest.approx(0.04) and held.kind == "ai"
    assert held.detail == "Claude said 3%, below the 4% floor that related markets set, so it is held at 4%. Base rate."
    assert prior.odds_reading(p, hi=0.02).q == pytest.approx(0.02)  # a ceiling holds it too
    assert prior.odds_reading(p, lo=0.02, hi=0.5).q == pytest.approx(0.03)  # inside the range: untouched
    assert prior.odds_reading(p, lo=0.5, hi=0.2).q == pytest.approx(0.03)  # ends that contradict each other say nothing
    assert prior.odds_reading(p).detail == "Base rate."
