"""Lead and lag (eval E10, DESIGN.md 4.8) on synthetic series with known answers. None of this is market data."""

from datetime import date, timedelta

import numpy as np
import pytest

from redkeep import granger as G


def var1(A: np.ndarray, T: int, seed: int, noise: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    Z = np.zeros((T, A.shape[0]))
    for t in range(1, T):
        Z[t] = A @ Z[t - 1] + rng.normal(0, noise, A.shape[0])
    return Z


def days(n: int) -> list[date]:
    return [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]


# -- the VAR and its tests ------------------------------------------------------------------------------------------


def test_a_planted_link_is_found_and_nothing_else_is():
    A = np.zeros((4, 4))
    A[2, 0] = 0.3  # a's move yesterday adds 0.3 of it to c's today
    links = G.granger(G.fit_var(var1(A, 3000, seed=1), 1, list("abcd")))
    found = {(ln.target, ln.source) for ln in links if ln.significant}
    assert ("c", "a") in found and len(found) <= 2  # the control allows the odd false discovery, not many
    ln = next(ln for ln in links if (ln.target, ln.source) == ("c", "a"))
    assert ln.coef[0] == pytest.approx(0.3, abs=0.05)
    assert ln.partial_r2 == pytest.approx(0.09 / 1.09, abs=0.02)  # its share of what c's own past leaves


def test_independent_series_give_no_links_once_false_discoveries_are_controlled():
    Z = np.random.default_rng(2).normal(size=(2000, 10))  # 90 tests
    links = G.granger(G.fit_var(Z, 1, [str(i) for i in range(10)]))
    assert len(links) == 90
    assert not any(ln.significant for ln in links)
    assert sum(ln.p_value < 0.05 for ln in links) > 0  # unadjusted, some always look "significant"


def test_a_third_series_that_drives_two_others_is_not_mistaken_for_a_link_between_them():
    """The review's Figure 1: x drives y after one day and z after two, so on their own y seems to lead z. With x in
    the regression the y-to-z link goes away."""
    rng = np.random.default_rng(5)
    T = 4000
    x = rng.normal(size=T)
    y, z = rng.normal(size=T) * 0.5, rng.normal(size=T) * 0.5
    y[1:] += 0.8 * x[:-1]
    z[2:] += 0.8 * x[:-2]
    pair = {(ln.target, ln.source) for ln in G.granger(G.fit_var(np.column_stack([y, z]), 1, ["y", "z"])) if ln.significant}
    assert ("z", "y") in pair
    full = G.granger(G.fit_var(np.column_stack([x, y, z]), 2, ["x", "y", "z"]))
    assert ("z", "y") not in {(ln.target, ln.source) for ln in full if ln.significant}
    assert ("z", "x") in {(ln.target, ln.source) for ln in full if ln.significant}


def test_the_lag_order_is_zero_for_noise_and_one_for_a_var1_on_the_same_days():
    rng = np.random.default_rng(3)
    noise = rng.normal(size=(1500, 3))
    noise[100, 1] = np.nan  # a missing day: every order is still scored on the same days
    out = G.lag_order(noise)
    assert out["bic"] == 0
    assert len({s["days"] for s in out["scores"].values()}) == 1
    A = np.array([[0.5, 0.2, 0.0], [0.0, 0.4, 0.0], [0.3, 0.0, -0.3]])
    assert G.lag_order(var1(A, 1500, seed=4))["bic"] == 1


def test_benjamini_hochberg_on_a_worked_example():
    assert G.bh([0.01, 0.04, 0.03, 0.005]).tolist() == [True, True, True, True]
    assert G.bh([0.01, 0.02, 0.03, 0.5]).tolist() == [True, True, True, False]
    assert G.bh([0.2, 0.3]).tolist() == [False, False]
    assert G.bh([]).tolist() == []


def test_forecasts_out_of_sample_beat_no_move_only_when_there_is_something_to_forecast():
    rng = np.random.default_rng(6)
    noise = G.out_of_sample(rng.normal(size=(1400, 3)), list("abc"), train=400, draws=300)
    assert noise.pooled < 0 and noise.pooled_range[0] < 0
    A = np.array([[0.5, 0.0, 0.0], [0.4, 0.0, 0.0], [0.0, 0.0, 0.3]])
    signal = G.out_of_sample(var1(A, 1400, seed=7), list("abc"), train=400, draws=300)
    assert signal.pooled > 0.1 and signal.pooled_range[0] > 0
    assert signal.r2["b"] > 0.1  # b is forecast from a's yesterday


def test_impulse_responses_follow_the_recursion():
    A1 = np.array([[0.5, 0.1], [0.2, 0.3]])
    psi = G.ma_coefficients([A1], 3)
    assert np.allclose(psi[0], np.eye(2)) and np.allclose(psi[3], np.linalg.matrix_power(A1, 3))
    A2 = np.array([[0.1, 0.0], [0.0, -0.2]])
    psi2 = G.ma_coefficients([A1, A2], 2)
    assert np.allclose(psi2[2], A1 @ A1 + A2)


def test_propagating_a_first_day_move_adds_what_follows_it():
    daily = np.zeros((4, 2))
    daily[0] = [1.0, 0.0]
    assert np.allclose(G.propagate(daily, [np.zeros((2, 2))]), daily)  # nothing follows: unchanged
    A = np.array([[0.0, 0.0], [0.5, 0.0]])  # the first series' move carries half into the second's next day
    out = G.propagate(daily, [A], h=3)
    assert np.allclose(out[1], [0.0, 0.5]) and np.allclose(out[2], [0.0, 0.0])


# -- clocks -------------------------------------------------------------------------------------------------------------


def test_a_series_fixed_at_noon_shows_the_afternoon_a_day_late():
    """A day's news: three quarters before noon, a quarter after. The close sees all of today's; the noon fixing sees
    yesterday afternoon's and this morning's."""
    rng = np.random.default_rng(8)
    T = 3000
    morning, afternoon = rng.normal(0, np.sqrt(0.75), T), rng.normal(0, np.sqrt(0.25), T)
    close = morning + afternoon
    noon = morning.copy()
    noon[1:] += afternoon[:-1]
    ck = G.clock(noon, close)
    assert ck.late_share == pytest.approx(0.25, abs=0.04)
    assert ck.next_corr == pytest.approx(0.25, abs=0.04) and ck.same_corr == pytest.approx(0.75, abs=0.04)
    assert abs(G.clock(close, close).late_share) < 1e-9
    # and the clock alone makes the close look as if it leads the noon series
    links = G.granger(G.fit_var(np.column_stack([close, noon]), 1, ["close", "noon"]))
    assert {(ln.target, ln.source) for ln in links if ln.significant} == {("noon", "close")}


# -- odds against factors ---------------------------------------------------------------------------------------------


def test_cross_lag_recovers_a_factor_that_keeps_moving_the_next_day():
    rng = np.random.default_rng(9)
    dq = rng.normal(0, 0.03, 400)
    z = np.full(400, np.nan)
    z[1:] = 20 * dq[1:] + 5 * dq[:-1] + rng.normal(0, 0.3, 399)
    cl = G.cross_lag(dq, z)
    assert cl.same == pytest.approx(20, rel=0.1)
    assert cl.after == pytest.approx(5, abs=2)
    assert abs(cl.before) < 2
    assert G.cross_lag(dq[:40], z[:40]) is None  # too few days


def test_the_sign_test_flags_a_factor_only_when_markets_agree_it_moves_late():
    def cl(after: float) -> G.CrossLag:
        return G.CrossLag(100, 1.0, after, 0.01, (0.1, 0.1, 0.1))

    late = {f"m{i}": {"oil": cl(0.3), "gold": cl(0.3 if i % 2 else -0.3)} for i in range(20)}
    out = {s.factor: s for s in G.timing(late, ["oil", "gold", "vix"])}
    assert out["oil"].after_significant and out["oil"].after_same_sign == 20
    assert not out["gold"].after_significant and out["gold"].after_same_sign == 10
    assert out["vix"].pairs == 0


def test_the_pooled_index_signs_each_market_by_its_label():
    rng = np.random.default_rng(10)
    T = 300
    a, b = rng.normal(0, 0.02, T), rng.normal(0, 0.02, T)
    z = 10 * a - 10 * b + rng.normal(0, 0.2, T)  # b's label says it moves the factor down
    ix = G.index_timing({"a": a, "b": b, "c": rng.normal(0, 0.02, T)}, {"a": 1, "b": -1, "c": 0}, z, "eq")
    assert ix.markets == 2 and ix.same == pytest.approx(10, rel=0.1) and ix.t[0] > 10
    assert abs(ix.t[1]) < 3 and abs(ix.t[2]) < 3


# -- odds against odds --------------------------------------------------------------------------------------------------


def test_link_timing_finds_a_child_that_reacts_a_day_late():
    rng = np.random.default_rng(11)
    T = 300
    dp = rng.normal(0, 0.02, T)
    dc = rng.normal(0, 0.01, T)
    dc[1:] += 0.5 * dp[:-1]
    ds = days(T + 1)
    parent = dict(zip(ds, 0.5 + np.concatenate([[0], np.cumsum(dp)])))
    child = dict(zip(ds, 0.3 + np.concatenate([[0], np.cumsum(dc)])))
    lt = G.link_timing(parent, child)
    assert lt.late == pytest.approx(0.5, abs=0.1) and abs(lt.same) < 0.1
    assert lt.parent_leads_p < 1e-6 and lt.child_leads_p > 0.01


def test_pair_study_keeps_a_lasting_link_and_skips_pairs_in_one_family():
    rng = np.random.default_rng(12)
    T = 240
    ds = days(T)
    common = np.cumsum(rng.normal(0, 0.02, T))
    series = {
        "kalshi:A-1": dict(zip(ds, 0.5 + common)),
        "kalshi:B-1": dict(zip(ds, 0.4 + 0.6 * common + np.cumsum(rng.normal(0, 0.005, T)))),  # a lasting link
        "kalshi:B-2": dict(zip(ds, 0.3 + 0.6 * common + np.cumsum(rng.normal(0, 0.005, T)))),  # same family as B-1
        "polymarket:9": dict(zip(ds, 0.5 + np.cumsum(rng.normal(0, 0.02, T)))),  # unrelated
    }
    fam = {"kalshi:A-1": "kalshi:A", "kalshi:B-1": "kalshi:B", "kalshi:B-2": "kalshi:B", "polymarket:9": "polymarket:9"}
    ps = G.pair_study(series, fam)
    assert ps.pairs == 10  # 12 directed pairs less the two within family B
    assert ps.clear == 4  # A with each B, both ways
    assert ps.persistence[5] == pytest.approx(1.0, abs=0.15)  # the slope holds over longer spans
    assert ps.split_error < 0.7  # and on the later half of the data


def test_a_kday_slope_shrinks_when_the_child_undoes_its_move():
    rng = np.random.default_rng(13)
    T = 400
    dp = rng.normal(0, 0.02, T)
    dc = 0.5 * dp.copy()
    dc[1:] -= 0.5 * dp[:-1]  # the child's reaction is gone the next day
    qp, qc = np.cumsum(dp), np.cumsum(dc)
    assert G.kday_slope(qp, qc, 1) == pytest.approx(0.5, abs=0.02)
    assert abs(G.kday_slope(qp, qc, 5)) < 0.15
