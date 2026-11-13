"""Joint error bars (DESIGN.md 4.9, eval E11): covariances across factors, from the jump windows to fusion and E11."""

from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from redkeep import factordata, joint
from redkeep.engine.covariance import moves_corr, nearest_corr, precision, shrink_corr
from redkeep.engine.fusion import DeltaReading, fuse_delta, size_error
from redkeep.evidence import windows
from redkeep.factors import NF, index

EQ, VIX, CREDIT = index("eq"), index("vix"), index("credit")


@pytest.fixture
def joint_on(monkeypatch):
    """The engine using all of the errors' correlations, whatever E11 last set."""
    from redkeep.engine import covariance

    monkeypatch.setattr(covariance, "JOINT_WEIGHT", 1.0)


def corr_world_R() -> np.ndarray:
    R = np.eye(NF)
    for (a, b), r in {(EQ, VIX): -0.8, (EQ, CREDIT): -0.5, (CREDIT, VIX): 0.45}.items():
        R[a, b] = R[b, a] = r
    return R


def test_nearest_corr_mends_an_invalid_matrix():
    R = np.array([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]])  # not positive definite
    C = nearest_corr(R)
    assert np.allclose(np.diag(C), 1) and np.allclose(C, C.T)
    assert np.linalg.eigvalsh(C).min() > 0


def test_shrinkage_leans_on_the_target_with_few_days_and_on_the_data_with_many():
    rng = np.random.default_rng(0)
    R = corr_world_R()
    L = np.linalg.cholesky(R)
    few, lam_few = shrink_corr(rng.normal(size=(4, NF)) @ L.T, np.eye(NF))
    assert lam_few > 0.5
    many, lam_many = shrink_corr(rng.normal(size=(4000, NF)) @ L.T, np.eye(NF))
    assert lam_many < 0.05 and abs(many[EQ, VIX] - R[EQ, VIX]) < 0.05
    same, _ = shrink_corr(rng.normal(size=(2, NF)), R)  # under three days: the target alone
    assert np.allclose(same, nearest_corr(R))


def test_precision_says_nothing_where_a_reading_says_nothing():
    C = np.diag(np.arange(1.0, NF + 1))
    C[0, 1] = C[1, 0] = 0.5
    ok = np.ones(NF, bool)
    ok[[7, 8, 9]] = False
    P = precision(C, ok)
    assert np.all(P[7:] == 0) and np.all(P[:, 7:] == 0)
    assert np.allclose(P[:7, :7] @ C[:7, :7], np.eye(7))


def test_moves_corr_reads_pairs_over_the_days_both_have():
    rng = np.random.default_rng(1)
    Z = rng.normal(size=(500, NF)) @ np.linalg.cholesky(corr_world_R()).T
    Z[:200, 9] = np.nan
    R = moves_corr(Z)
    assert abs(R[EQ, VIX] + 0.8) < 0.06 and np.isfinite(R).all() and np.allclose(np.diag(R), 1)


def window_world(rng, *, T=240, sd=0.35):
    """Two events whose odds jump on set days, factor moves driven by the jumps plus noise correlated across factors."""
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(T)]
    true = {"a": np.array([-2.0, 0.5, 0, 0, 1.0, 0, 1.5, 0, 0, 0]), "b": np.array([0.5, -1.0, 0, 0, 0, 0, 0, 0, 0, 0.8])}
    q = {"a": 0.3 + np.zeros(T), "b": 0.5 + np.zeros(T)}
    sched = np.random.default_rng(7)
    for e, days in (("a", [20, 60, 61, 120, 150, 180, 33, 77, 200]), ("b", [30, 90, 100, 140, 170, 61, 120, 210])):
        for t in days:
            q[e][t:] += sched.choice([-1, 1]) * sched.uniform(0.06, 0.12)
    dq = {e: np.diff(np.concatenate([[q[e][0]], q[e]])) for e in q}
    L = np.linalg.cholesky(corr_world_R() * sd ** 2)
    X = rng.normal(size=(T, NF)) @ L.T + sum(np.outer(dq[e], true[e]) for e in q)
    fd = factordata.FactorData(levels={}, dates=dates, X=X, typical=np.ones(NF))
    return {e: dict(zip(dates, q[e].tolist())) for e in q}, fd, true


def test_jump_window_covariance_matches_the_spread_of_the_estimates():
    est, covs = [], []
    hist, fd, true = window_world(np.random.default_rng(0))
    for rep in range(150):
        _, fd_r, _ = window_world(np.random.default_rng(100 + rep))
        r = windows.jump_windows(hist, fd_r, move=0.05).readings["a"]
        assert np.allclose(np.diag(r.cov), r.sigma ** 2)  # each factor's own error bar is unchanged
        est.append(r.delta)
        covs.append(r.cov)
    E = np.cov(np.array(est).T)
    C = np.mean(covs, axis=0)
    corr = lambda M, i, j: M[i, j] / np.sqrt(M[i, i] * M[j, j])  # noqa: E731
    assert abs(corr(C, EQ, VIX) - corr(E, EQ, VIX)) < 0.12
    assert corr(C, EQ, VIX) < -0.6
    b = np.zeros(NF)
    b[EQ] = b[VIX] = 1.0  # stocks and the VIX together: their errors largely cancel
    err = (np.array(est) - true["a"]) @ b
    assert np.mean([b @ c @ b for c in covs]) < 0.5 * np.mean([b ** 2 @ np.diag(c) for c in covs])
    assert np.var(err) < 0.5 * np.mean([b ** 2 @ np.diag(c) for c in covs])


def test_a_factor_with_fewer_windows_keeps_a_valid_covariance(joint_on):
    hist, fd, _ = window_world(np.random.default_rng(3))
    X = fd.X.copy()
    X[:120, index("gold")] = np.nan
    wf = windows.jump_windows(hist, factordata.FactorData(fd.levels, fd.dates, X, fd.typical), move=0.05)
    for r in wf.readings.values():
        ok = r.informative()
        C = r.covariance()[np.ix_(ok, ok)]
        assert np.linalg.eigvalsh(C).min() > -1e-9 and np.allclose(np.diag(C), r.sigma[ok] ** 2)


def reading(kind, delta, cov, name=None):
    cov = np.asarray(cov, float)
    sd = np.sqrt(np.diag(cov))
    return DeltaReading(kind, name or kind, "", np.asarray(delta, float), np.where(sd > 0, sd, np.inf), cov=cov)


def test_fusion_with_independent_errors_is_the_per_factor_average():
    a = reading("etf", np.full(NF, 1.0), np.diag(np.full(NF, 1.0)))
    b = reading("hl", np.full(NF, 3.0), np.diag(np.full(NF, 4.0)))
    f = fuse_delta([a, b])
    assert np.allclose(f.delta, (1 / 1 + 3 / 4) / (1 + 1 / 4))
    assert np.allclose(f.sigma, 1 / np.sqrt(1 + 1 / 4))
    assert f.dof == NF and f.chi2 == pytest.approx(NF * ((1.0 - f.delta[0]) ** 2 + (3.0 - f.delta[0]) ** 2 / 4))


def test_fusion_borrows_across_correlated_factors(joint_on):
    """A second reading of stocks alone moves the fused VIX through the first reading's stock–VIX correlation."""
    C = np.eye(NF)
    C[EQ, VIX] = C[VIX, EQ] = -0.8
    a = reading("etf", np.zeros(NF), C)
    cov_b = np.zeros((NF, NF))
    cov_b[EQ, EQ] = 0.25
    b = reading("hl", np.where(np.arange(NF) == EQ, -1.0, 0.0), cov_b)
    f = fuse_delta([a, b])
    W = np.linalg.inv(C)
    Wb = np.zeros((NF, NF))
    Wb[EQ, EQ] = 4.0
    want = np.linalg.solve(W + Wb, Wb @ b.delta)
    assert np.allclose(f.delta, want)
    assert f.delta[EQ] < 0 and f.delta[VIX] > 0  # stocks lower than the windows said, so the VIX higher
    assert f.dof == 1 and np.allclose(f.cov, np.linalg.inv(W + Wb))


def test_a_contested_factor_rests_on_the_other_evidence_alone(joint_on):
    """The market and the past events point opposite ways on stocks, and agree on the VIX. The fused stocks are the
    past events' alone: the market's VIX can't reach them through the past events' own stock–VIX correlation."""
    C = np.zeros((NF, NF))
    C[EQ, EQ] = C[VIX, VIX] = 0.09
    C[EQ, VIX] = C[VIX, EQ] = -0.8 * 0.09
    on = lambda eq, vix: np.where(np.arange(NF) == EQ, eq, np.where(np.arange(NF) == VIX, vix, 0.0))  # noqa: E731
    f = fuse_delta([reading("etf", on(2.0, -1.0), C), reading("past", on(-2.0, -0.2), C)])
    assert f.conflict is not None and f.conflict.factors == ["eq"]
    assert f.delta[EQ] == pytest.approx(-2.0) and f.sigma[EQ] == pytest.approx(0.3)
    assert f.cov[EQ, VIX] == pytest.approx(0.0)  # stocks' error now apart from the VIX's
    assert -1.0 < f.delta[VIX] < -0.2  # both readings still count on the VIX


def test_the_snapshot_keeps_each_readings_covariance_and_the_app_weighs_it_as_the_engine_did():
    """A snapshot keeps each reading's covariance as measured, whatever weight the engine used; the app's "k times
    the fused jump" weighs its correlations as the engine's quorum did (the snapshot's ``joint_weight``)."""
    from redkeep import appdata
    from redkeep.engine import covariance
    from redkeep.pipeline import _reading_json

    rng = np.random.default_rng(2)
    C = corr_world_R() * 0.25
    r = reading("etf", rng.normal(size=NF), C)
    assert covariance.JOINT_WEIGHT == 0.0  # E11's first run
    js = _reading_json(r)
    assert js["cov"][EQ][VIX] == pytest.approx(C[EQ, VIX])
    u = rng.normal(size=NF)
    ks = []
    for w in (0.0, 0.5, 1.0):
        P = np.linalg.inv(covariance.weigh(C, w))
        want = float(u @ P @ r.delta) / float(u @ P @ u)
        got = appdata._project(js, u, w)
        assert got[3] == pytest.approx(want, abs=1e-4) and got[4] == pytest.approx(1 / np.sqrt(u @ P @ u), abs=1e-4)
        ks.append(got[3])
    assert len(set(ks)) == 3


def test_size_error_uses_the_whole_precision():
    u = np.zeros(NF)
    u[EQ], u[VIX] = -1.0, 1.0
    C = np.eye(NF)
    C[EQ, VIX] = C[VIX, EQ] = -0.8  # errors that pull stocks and the VIX apart are the likely ones
    P = np.linalg.inv(C)
    assert size_error(P, u) == pytest.approx(1 / np.sqrt(u @ P @ u))
    assert size_error(P, u) > size_error(np.ones(NF), u)  # along this jump the errors are larger than independent


def e11_cases(rng, *, truth: str, n_days: int = 60, pairs: int = 6, noise_sd: float = 0.05,
              noise_corr: np.ndarray | None = None) -> list:
    """Resolved markets whose readings carry the stock–VIX–credit covariance; the misses drawn from it (``truth``
    "joint") or from its error bars alone ("independent"). The first ``pairs`` days have two markets each. The day's
    noise has spread ``noise_sd`` on every factor, independent across factors unless ``noise_corr`` says otherwise."""
    S = corr_world_R() * 0.4
    sig = np.sqrt(np.diag(S))
    L = np.linalg.cholesky(S if truth == "joint" else np.diag(np.diag(S)))
    noise = np.full(NF, noise_sd)
    R = np.eye(NF) if noise_corr is None else noise_corr
    Ln = np.linalg.cholesky(R * np.outer(noise, noise))
    cases = []
    for i in range(n_days):
        day = (date(2025, 1, 1) + timedelta(days=i)).isoformat()
        markets = []
        for _ in range(2 if i < pairs else 1):
            dq = rng.choice([-1, 1]) * rng.uniform(0.3, 0.9)
            pred = rng.normal(size=NF)
            markets.append((dq, pred, dq * (pred + L @ rng.normal(size=NF))))
        z = sum(m[2] for m in markets) + Ln @ rng.normal(size=NF)
        for dq, pred, _ in markets:
            cases.append(SimpleNamespace(day=day, dq=dq, pred=pred.tolist(), sigma=sig.tolist(), z=z.tolist(),
                                         noise=noise.tolist(), cov=S.tolist(), noise_corr=R.tolist()))
    return cases


def test_e11_scores_each_day_once_with_its_markets_summed():
    cases = e11_cases(np.random.default_rng(5), truth="joint", n_days=8, pairs=2)
    ds = joint.days(cases)
    assert len(ds) == 8 and [d.markets for d in ds] == [2, 2, 1, 1, 1, 1, 1, 1]
    a, b = cases[0], cases[1]
    assert np.allclose(ds[0].e, np.array(a.z) - a.dq * np.array(a.pred) - b.dq * np.array(b.pred))
    assert np.allclose(ds[0].jump, (a.dq ** 2 + b.dq ** 2) * np.array(a.cov))
    assert np.allclose(np.diag(ds[0].S("independent")), np.diag(ds[0].S("joint")))  # the same error bars either way
    assert np.allclose(ds[0].S("half") - ds[0].S("independent"), (ds[0].S("joint") - ds[0].S("independent")) / 2)


def test_e11_prefers_the_covariance_that_made_the_misses():
    out = joint.study(e11_cases(np.random.default_rng(4), truth="joint"), replay=None)
    assert out["days"] == 60 and out["markets"] == 66 and out["several"] == 6
    assert out["power"] > 0.9 and out["primary"]["favours"] == "joint" and out["primary"]["likelihood_ratio"] > 1
    assert abs(out["candidates"]["joint"]["inside_80"] - 0.8) < 0.12
    assert out["guards"]["hedge_ok"] and out["guards"]["d2"]["ok"]
    hedge = out["guards"]["hedge"]
    assert hedge["independent"]["spread"] < hedge["joint"]["spread"] <= out["band"]  # the independent bars are too wide there
    assert out["random"]["gap_from_80"]["joint"] <= out["random"]["gap_from_80"]["independent"]
    assert out["energy"]["mean"] > 0 and not out["sensitivity"]["leave_one_out"]["flips"]
    assert out["decision"] == {"outcome": "all", "weight": 1.0, "why": "the joint error bars scored better and passed both guards"}


def test_e11_keeps_the_error_bars_independent_when_the_misses_were():
    out = joint.study(e11_cases(np.random.default_rng(6), truth="independent"), replay=None)
    assert out["primary"]["favours"] == "independent" and out["primary"]["mean"] < 0
    assert out["guards"]["hedge"]["joint"]["spread"] > out["band"]  # the joint bars are too thin along their hedge
    assert out["decision"]["outcome"] == "none" and out["decision"]["weight"] == 0.0


def test_e11_settles_an_inconclusive_result_by_the_power_fixed_in_advance():
    assert joint.decide("neither", True, True, False, 0.75)[0] == "none"
    assert joint.decide("neither", True, True, False, 0.4)[0] == "half"
    assert joint.decide("joint", False, True, True, 0.9)[0] == "half"
    assert joint.decide("joint", False, True, False, 0.9)[0] == "none"
    assert joint.decide("joint", True, False, False, 0.4)[0] == "none"  # amendment 2: not halfway, which just failed
    assert joint.decide("independent", True, True, True, 0.2)[0] == "none"


def test_e11_distance_guard_allows_for_what_a_right_joint_forecast_gives():
    """Amendment 1. With day noise whose correlations share the jumps' signs, even a right joint forecast is farther
    off than the independent one, by ``k − tr(S_i⁻¹ S_j)`` a day on average; the guard tests the excess over that,
    and so fails about one run in twenty when the joint forecasts are right, where uncentred it failed most."""
    rng = np.random.default_rng(9)
    ds = joint.days(e11_cases(rng, truth="joint", n_days=40, pairs=0, noise_sd=0.5, noise_corr=corr_world_R()))
    K, sims = len(ds), 3000
    crit = float(joint.student.ppf(1 - joint.ALPHA, K - 1))
    raw, centred = np.zeros((sims, K)), np.zeros((sims, K))
    for k, d in enumerate(ds):
        Sj, Si = d.S("joint"), d.S("independent")
        E = rng.standard_normal((sims, len(d.idx))) @ np.linalg.cholesky(Sj).T
        gap = np.einsum("si,ij,sj->s", E, np.linalg.inv(Sj), E) - np.einsum("si,ij,sj->s", E, np.linalg.inv(Si), E)
        expected = len(d.idx) - np.trace(np.linalg.solve(Si, Sj))
        assert expected > 0.05  # the same sign as the correlations the two share
        raw[:, k], centred[:, k] = gap, gap - expected
    assert abs(centred.mean()) < 0.02 and raw.mean() > 0.1
    fails = lambda D: float(np.mean(D.mean(1) / (D.std(1, ddof=1) / np.sqrt(K)) > crit))  # noqa: E731
    assert 0.02 < fails(centred) < 0.09 and fails(raw) > 0.5


def test_e11_reports_the_distance_guard_both_ways():
    out = joint.study(e11_cases(np.random.default_rng(4), truth="joint", noise_sd=0.5, noise_corr=corr_world_R()),
                      replay=None)
    g = out["guards"]
    assert g["d2"]["raw_mean"] > g["d2"]["mean"] and g["d2"]["raw_t"] > g["d2"]["t"]  # centring takes the bias out
    assert g["d2"]["ok"] == (g["d2"]["t"] <= float(joint.student.ppf(1 - joint.ALPHA, out["days"] - 1)))
    assert set(g["d2_half"]) == {"t", "mean"}


def test_the_engine_uses_the_correlations_at_the_weight_e11_set(monkeypatch):
    from redkeep.engine import covariance

    assert covariance.JOINT_WEIGHT in joint.WEIGHTS.values()
    C = corr_world_R()
    r = reading("etf", np.zeros(NF), C)
    monkeypatch.setattr(covariance, "JOINT_WEIGHT", 1.0)
    assert np.allclose(r.covariance(), C)
    monkeypatch.setattr(covariance, "JOINT_WEIGHT", 0.5)
    half = r.covariance()
    assert np.allclose(np.diag(half), 1) and half[EQ, VIX] == pytest.approx(-0.4)
    monkeypatch.setattr(covariance, "JOINT_WEIGHT", 0.0)
    assert np.allclose(r.covariance(), np.eye(NF))
