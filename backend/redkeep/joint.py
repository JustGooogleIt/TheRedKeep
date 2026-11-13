"""Joint error bars on resolved markets (DESIGN.md section 10, eval E11), with the method fixed here, before any run.

Each jump comes with error bars on every factor, and E4 checks them one factor at a time. A portfolio feels them all
at once, ``b · δ``, and how far that is off depends on how the factors' errors move together: a jump window that
overstates the fall in stocks most likely overstates the rise in the VIX too. E11 asks whether the jump's covariance
across factors (its uncertainty ellipsoid) describes what happened on resolved markets better than the same error
bars taken as independent, on the markets of E3 and E4. The design is the research note's
(``docs/research/uncertainty-ellipsoids.md``), fixed before E11's first run.

* **The unit: a resolution day.** Markets that resolve on one day share its moves, so each day is one forecast of
  that day's factor moves, in typical moves, over the factors where the move and every market's reading exist:
  mean ``m = Σ Δq_i δ̂_i`` and covariance ``S = Σ Δq_i² Σ_i + N``. ``N`` is the day's ordinary noise, each factor's
  calm-day spread with the factors' calm-day correlations: it is how the move is measured, the same in every
  candidate. With one market a day this is the market's own jump forecast, scaled by its odds change.
* **The candidates.** ``Σ_i`` is the reading's error: its covariance across factors (*joint*), its error bars alone
  (*independent*, the engine before E11), or halfway (*half*, the correlations at half weight). A reference,
  *E4's way*, also takes the noise factor by factor.
* **The primary test.** The Dawid–Sebastiani score, ``log det S + (z − m)'S⁻¹(z − m)``, a proper score for a
  forecast's mean and covariance (lower is better; for normal errors it is minus twice the log likelihood, so the
  paired difference is the most powerful test between two normal forecasts). Each day's gain is the independent
  score less the joint one. Over the K days, ``t = mean / (sd / √K)`` against Student's t with K − 1 degrees of
  freedom (Diebold and Mariano's test with Harvey, Leybourne and Newbold's small-sample form), one-sided at 5% each
  way, and a sign-flip randomization p-value must agree. ``exp(mean / 2)`` is how many times as likely, on a typical
  day, the joint forecast made what happened.
* **The design's power,** from the forecasts alone, before any outcome: the share of simulated runs, each day's miss
  drawn from its joint forecast, in which the test finds the joint error bars better.
* **Two guards.** (ii) The hedges the joint error bars believe in must not be too thin. Along the portfolio whose
  error they shrink most against the independent ones (the smallest eigenvector of the markets' average error
  correlation, each factor scaled by one over its average error bar), the spread of ``t = b·(z − m) / √(b'Sb)``
  over the days, the bias statistic risk models are tested with, stays within ``1 + √(2/K)``. (iii) The joint
  forecasts' squared Mahalanobis distance, ``(z − m)'S⁻¹(z − m)``, is not significantly larger than the
  independent ones' (paired, one-sided at 5%).
* **Also reported, not deciding.** The half and E4's-way candidates; the share of days inside each forecast's 80%
  region (χ² with as many degrees of freedom as factors); the spread along the largest eigenvector, where the joint
  error bars widen most; the 80% coverage and spread along the six sample portfolios of the April 2025 replay (its
  saved file) and along 500 random directions (seed 0), and the score along the sample portfolios; an energy score
  (10,000 draws, seed 0); and, for the primary test, days whose largest odds move was under 0.2 left out, the
  noise's covariance 1.5 times as large, each day left out in turn, both candidates rescaled so the independent
  one's distance averages its degrees of freedom (E4 found the error bars a little wide), and a Student-t score with
  5 degrees of freedom in place of the normal one.

The decision, fixed before the first run, sets how much of the correlations the engine uses
(``covariance.JOINT_WEIGHT``):

* **All (1)** if the primary test favours the joint error bars and they pass both guards.
* **Half (0.5)** if it favours the joint ones but they fail a guard, while the half candidate beats the independent
  one on the primary test and passes both guards.
* **None (0)** if the primary test favours the independent error bars.
* Otherwise it is inconclusive, and the default was set in advance by the design's power: at 0.6 or more, none (the
  test should have seen the gain the joint error bars promise, and didn't), with portfolio ranges marked as not yet
  validated; under 0.6, half, on theory (regressions on the same days have errors that move together as their
  residuals do), since the sample can't tell.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
from scipy.stats import chi2
from scipy.stats import t as student

from .config import DATA_DIR
from .engine.covariance import corr_from_cov, nearest_corr
from .factors import NF

Z80 = 1.2815515655446004
ALPHA = 0.05  # each one-sided test
BOOT = 1000
FLIPS = 10000
SIMS = 2000  # simulated runs for the design's power
DRAWS = 10000  # draws from each forecast for the energy score
RANDOM_DIRECTIONS = 500
POWER_FOR_NONE = 0.6  # at this power or more, an inconclusive result keeps the error bars independent
BIG_MOVE = 0.2  # sensitivity: days whose largest odds move was smaller are left out
NOISE_SCALE = 1.5  # sensitivity: the noise's covariance this many times as large
T_DOF = 5  # sensitivity: a Student-t score with this many degrees of freedom
WAYS = ("joint", "independent", "half", "e4")
WEIGHTS = {"all": 1.0, "half": 0.5, "none": 0.0}


@dataclass
class Day:
    """One resolution day: its forecast of the factor moves, each candidate's spread, and the miss."""
    day: str
    idx: np.ndarray  # the factors scored
    e: np.ndarray  # the day's moves less the forecast ``Σ Δq δ̂``, typical moves, over ``idx``
    jump: np.ndarray  # ``Σ Δq² Σ_i``, the readings' covariance
    jump_diag: np.ndarray  # ``Σ Δq² diag(σ_i²)``, their error bars alone
    noise: np.ndarray  # the day's noise covariance
    markets: int
    dq: float  # the day's largest odds move

    def S(self, way: str, noise_scale: float = 1.0) -> np.ndarray:
        N = self.noise * noise_scale
        if way == "joint":
            return self.jump + N
        if way == "half":
            return (self.jump + self.jump_diag) / 2 + N
        if way == "independent":
            return self.jump_diag + N
        return self.jump_diag + np.diag(np.diag(N))  # E4's way: the noise factor by factor too


def _scored(c, f: int) -> bool:
    return (c.pred[f] is not None and c.sigma[f] is not None and c.z[f] is not None and c.noise[f] is not None
            and c.cov[f][f] is not None)


def days(cases: Sequence) -> list[Day]:
    """The resolution days of the markets with a reading's covariance (runs from before E11 have none), over the
    factors every market of the day can be scored on, at least two."""
    by: dict[str, list] = {}
    for c in cases:
        if c.cov is not None and c.noise_corr is not None:
            by.setdefault(c.day, []).append(c)
    out = []
    for day in sorted(by):
        cs = by[day]
        idx = np.array([f for f in range(NF) if all(_scored(c, f) for c in cs)], dtype=int)
        if len(idx) < 2:
            continue
        z = np.array([cs[0].z[f] for f in idx], dtype=float)
        m = sum(c.dq * np.array([c.pred[f] for f in idx], dtype=float) for c in cs)
        cov = [np.array([[0.0 if c.cov[f][g] is None else c.cov[f][g] for g in idx] for f in idx], dtype=float)
               for c in cs]
        jump = sum(c.dq ** 2 * (C + C.T) / 2 for c, C in zip(cs, cov))
        diag = sum(c.dq ** 2 * np.diag([c.sigma[f] ** 2 for f in idx]) for c in cs)
        nz = np.mean([[c.noise[f] for f in idx] for c in cs], axis=0)
        R = np.mean([[[c.noise_corr[f][g] for g in idx] for f in idx] for c in cs], axis=0)
        out.append(Day(day, idx, z - m, jump, diag, R * np.outer(nz, nz), len(cs), max(abs(c.dq) for c in cs)))
    return out


# -- scores ----------------------------------------------------------------------------------------------------


def ds(e: np.ndarray, S: np.ndarray) -> tuple[float, float]:
    """Dawid and Sebastiani's score, ``log det S + e'S⁻¹e``, and the squared Mahalanobis distance ``e'S⁻¹e``."""
    sign, logdet = np.linalg.slogdet(S)
    m2 = float(e @ np.linalg.solve(S, e))
    return (float(logdet) + m2 if sign > 0 else math.inf), m2


def t_score(e: np.ndarray, S: np.ndarray, nu: int = T_DOF) -> float:
    """Minus twice the log density of a multivariate Student t with ``nu`` degrees of freedom and covariance ``S``."""
    d = len(e)
    psi = S * (nu - 2) / nu
    _, logdet = np.linalg.slogdet(psi)
    q = float(e @ np.linalg.solve(psi, e))
    logf = (math.lgamma((nu + d) / 2) - math.lgamma(nu / 2) - d / 2 * math.log(nu * math.pi) - logdet / 2
            - (nu + d) / 2 * math.log1p(q / nu))
    return -2 * logf


def _root(S: np.ndarray) -> np.ndarray:
    w, V = np.linalg.eigh((S + S.T) / 2)
    return V * np.sqrt(np.clip(w, 0, None))


def energy(e: np.ndarray, S: np.ndarray, W: np.ndarray) -> float:
    """The energy score ``E‖X − e‖ − ½ E‖X − X'‖`` for ``X`` drawn from the forecast's miss, ``N(0, S)``, using the
    standard normal draws ``W`` (draws by factors), the same for every candidate."""
    X = W[:, :len(e)] @ _root(S).T
    h = len(X) // 2
    return float(np.linalg.norm(X - e, axis=1).mean() - 0.5 * np.linalg.norm(X[:h] - X[h:2 * h], axis=1).mean())


# -- tests -----------------------------------------------------------------------------------------------------


def paired(x: np.ndarray, *, seed: int = 0) -> dict:
    """Day-level differences, positive for the joint error bars: their mean, Student's t over the days with its
    one-sided p-values, sign-flip p-values each way, and a 90% range from resampling days."""
    x = np.asarray(x, dtype=float)
    K = len(x)
    mean = float(x.mean())
    sd = float(x.std(ddof=1)) if K > 1 else 0.0
    t = mean / (sd / math.sqrt(K)) if sd > 0 else math.copysign(math.inf, mean) if mean else 0.0
    rng = np.random.default_rng(seed)
    flips = (rng.choice([-1.0, 1.0], size=(FLIPS, K)) * x).mean(axis=1)
    boot = x[rng.integers(0, K, size=(BOOT, K))].mean(axis=1)
    return {"mean": mean, "t": float(t), "days": K,
            "p_joint": float(student.sf(t, K - 1)), "p_independent": float(student.cdf(t, K - 1)),
            "flip_joint": float((1 + (flips >= mean).sum()) / (FLIPS + 1)),
            "flip_independent": float((1 + (flips <= mean).sum()) / (FLIPS + 1)),
            "range90": [float(np.percentile(boot, 5)), float(np.percentile(boot, 95))]}


def favours(p: dict) -> str:
    """"joint" or "independent" when the paired test favours one at 5% and the sign flips agree, else "neither"."""
    crit = float(student.ppf(1 - ALPHA, p["days"] - 1))
    if p["t"] > crit and p["flip_joint"] < ALPHA:
        return "joint"
    if p["t"] < -crit and p["flip_independent"] < ALPHA:
        return "independent"
    return "neither"


def power(ds_: Sequence[Day], *, sims: int = SIMS, seed: int = 0) -> float:
    """The design's power, from the forecasts alone: the share of simulated runs, each day's miss drawn from its
    joint forecast, in which Student's t finds the joint error bars better (the sign flips are left out for speed)."""
    K = len(ds_)
    if K < 3:
        return 0.0
    rng = np.random.default_rng(seed)
    crit = float(student.ppf(1 - ALPHA, K - 1))
    D = np.zeros((sims, K))
    for k, d in enumerate(ds_):
        Sj, Si = d.S("joint"), d.S("independent")
        E = rng.standard_normal((sims, len(d.idx))) @ _root(Sj).T
        ld = np.linalg.slogdet(Si)[1] - np.linalg.slogdet(Sj)[1]
        D[:, k] = (ld + np.einsum("si,ij,sj->s", E, np.linalg.inv(Si), E)
                   - np.einsum("si,ij,sj->s", E, np.linalg.inv(Sj), E))
    t = D.mean(axis=1) / (D.std(axis=1, ddof=1) / math.sqrt(K))
    return float((t > crit).mean())


# -- portfolios ------------------------------------------------------------------------------------------------


def sample_directions(path: Path | None = None) -> dict[str, np.ndarray]:
    """The sample portfolios' arrows, ``b`` in % per typical move, from the saved April 2025 replay."""
    path = path or DATA_DIR / "replays" / "tariffs-2025.json"
    if not path.exists():
        return {}
    rep = json.loads(path.read_text())
    return {p["name"]: np.array(p["b"], dtype=float) for p in rep.get("portfolios") or [] if p.get("b")}


def random_directions(n: int = RANDOM_DIRECTIONS, seed: int = 0) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=(n, NF))
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def hedges(cases: Sequence) -> tuple[np.ndarray, np.ndarray] | None:
    """The two portfolios along which the joint error bars differ most from the independent ones, from the forecasts
    alone: the smallest and largest eigenvectors of the markets' average error correlation, each factor scaled by
    one over its average error bar. Along the first the joint error bars are narrowest against the independent
    ones (the hedge they believe in most), along the second widest."""
    num, cnt = np.zeros((NF, NF)), np.zeros((NF, NF))
    ssum, scnt = np.zeros(NF), np.zeros(NF)
    for c in cases:
        if c.cov is None:
            continue
        ok = np.array([c.sigma[f] is not None and c.cov[f][f] is not None and c.cov[f][f] > 0 for f in range(NF)])
        C = np.array([[c.cov[f][g] if ok[f] and ok[g] and c.cov[f][g] is not None else 0.0 for g in range(NF)]
                      for f in range(NF)], dtype=float)
        both = np.outer(ok, ok)
        num += np.where(both, corr_from_cov(C), 0.0)
        cnt += both
        ssum += np.array([c.sigma[f] if ok[f] else 0.0 for f in range(NF)])
        scnt += ok
    have = np.diag(cnt) > 0
    if have.sum() < 2:
        return None
    R = nearest_corr((num / np.maximum(cnt, 1))[np.ix_(have, have)])
    _, V = np.linalg.eigh(R)
    sbar = (ssum / np.maximum(scnt, 1))[have]
    out = []
    for v in (V[:, 0], V[:, -1]):
        b = np.zeros(NF)
        b[have] = v / sbar
        out.append(b / np.linalg.norm(b))
    return out[0], out[1]


def along(ds_: Sequence[Day], b: np.ndarray, way: str) -> np.ndarray:
    """``t = b·(z − m) / √(b'Sb)`` on each day the direction touches a scored factor."""
    out = []
    for d in ds_:
        bb = b[d.idx]
        v = float(bb @ d.S(way) @ bb)
        if np.any(bb) and v > 0:
            out.append(float(bb @ d.e) / math.sqrt(v))
    return np.array(out)


def _spread(t: np.ndarray) -> float | None:
    return float(np.sqrt(np.mean(t ** 2))) if len(t) else None


def _coverage(t: np.ndarray) -> float | None:
    return float((np.abs(t) <= Z80).mean()) if len(t) else None


def portfolio_score(d: Day, bs: Sequence[np.ndarray], way: str) -> float | None:
    """The day's score along the portfolios: the mean of ``log v + r² / v``, ``r = b·(z − m)``, ``v = b'Sb``."""
    S = d.S(way)
    vals = []
    for b in bs:
        bb = b[d.idx]
        v = float(bb @ S @ bb)
        if np.any(bb) and v > 0:
            vals.append(math.log(v) + float(bb @ d.e) ** 2 / v)
    return float(np.mean(vals)) if vals else None


# -- the study -------------------------------------------------------------------------------------------------


def _primary(ds_: Sequence[Day], score: Callable[[Day, str], float]) -> dict:
    gain = np.array([score(d, "independent") - score(d, "joint") for d in ds_])
    p = paired(gain)
    return {"days": len(ds_), "gain": p["mean"], "t": p["t"], "favours": favours(p)}


def sensitivities(ds_: Sequence[Day], first: str) -> dict:
    """The primary test under the pre-listed changes; ``flips``: whether leaving out some day changes its verdict."""
    big = [d for d in ds_ if d.dq >= BIG_MOVE]
    m2i = np.array([ds(d.e, d.S("independent"))[1] / len(d.idx) for d in ds_])
    c = float(m2i.mean())
    out = {
        "big_moves": _primary(big, lambda d, w: ds(d.e, d.S(w))[0]) if len(big) >= 3 else None,
        "noise_x1.5": _primary(ds_, lambda d, w: ds(d.e, d.S(w, NOISE_SCALE))[0]),
        "rescaled": {"scale": c, **_primary(ds_, lambda d, w: ds(d.e, c * d.S(w))[0])},
        "student_t": _primary(ds_, lambda d, w: t_score(d.e, d.S(w))),
    }
    gain = np.array([ds(d.e, d.S("independent"))[0] - ds(d.e, d.S("joint"))[0] for d in ds_])
    loo = [paired(np.delete(gain, k)) for k in range(len(ds_))] if len(ds_) > 3 else []
    out["leave_one_out"] = None if not loo else {
        "t_min": min(p["t"] for p in loo), "t_max": max(p["t"] for p in loo),
        "gain_min": min(p["mean"] for p in loo), "gain_max": max(p["mean"] for p in loo),
        "flips": any(favours(p) != first for p in loo)}
    return out


def decide(first: str, hedge_ok: bool, m2_ok: bool, half_ok: bool, pw: float) -> tuple[str, str]:
    """The rule fixed before the first run: how much of the correlations the engine uses, and why."""
    if first == "joint" and hedge_ok and m2_ok:
        return "all", "the joint error bars scored better and passed both guards"
    if first == "joint" and half_ok:
        return "half", "the joint error bars scored better but failed a guard; halfway scored better and passed both"
    if first == "independent":
        return "none", "the independent error bars scored better"
    if pw >= POWER_FOR_NONE:
        return "none", (f"inconclusive, though the design had the power ({pw:.2f}) to see the gain the joint error "
                        "bars promise: kept independent, not yet validated")
    return "half", (f"inconclusive, as the design's power ({pw:.2f}) said it might be: halfway, on theory, since "
                    "regressions on the same days have errors that move together")


def study(cases: Sequence, *, replay: Path | None = None) -> dict | None:
    """E11 on the cases of a jump study, or None when too few carry covariances (runs from before E11)."""
    ds_ = days(cases)
    if len(ds_) < 5:
        return None
    K = len(ds_)
    crit = float(student.ppf(1 - ALPHA, K - 1))
    band = 1 + math.sqrt(2 / K)
    sc = {w: np.array([ds(d.e, d.S(w)) for d in ds_]) for w in WAYS}  # days by (score, distance²)
    k = np.array([len(d.idx) for d in ds_])
    pw = power(ds_)

    prim = paired(sc["independent"][:, 0] - sc["joint"][:, 0])
    first = favours(prim)
    half = paired(sc["independent"][:, 0] - sc["half"][:, 0])
    m2 = paired(sc["joint"][:, 1] - sc["independent"][:, 1])  # positive: the joint forecasts' misses look larger
    m2_half = paired(sc["half"][:, 1] - sc["independent"][:, 1])
    hb = hedges(cases)
    lines = {}
    if hb is not None:
        for name, b in (("hedge", hb[0]), ("pile", hb[1])):
            lines[name] = {w: {"spread": _spread(t), "coverage": _coverage(t)}
                           for w in ("joint", "independent", "half") for t in [along(ds_, b, w)]}
    hedge_ok = "hedge" not in lines or (lines["hedge"]["joint"]["spread"] or 0) <= band
    half_hedge_ok = "hedge" not in lines or (lines["hedge"]["half"]["spread"] or 0) <= band
    m2_ok = not m2["t"] > crit
    half_ok = favours(half) == "joint" and half_hedge_ok and not m2_half["t"] > crit
    outcome, why = decide(first, hedge_ok, m2_ok, half_ok, pw)

    rand = random_directions()
    rcov = {w: [_coverage(along(ds_, b, w)) for b in rand] for w in ("joint", "independent", "half")}
    rspread = {w: [_spread(along(ds_, b, w)) for b in rand] for w in ("joint", "independent", "half")}
    samples = sample_directions(replay)
    named = {name: {w: {"coverage": _coverage(t), "spread": _spread(t)}
                    for w in ("joint", "independent") for t in [along(ds_, b, w)]} for name, b in samples.items()}
    pscore = None
    if samples:
        g = [(portfolio_score(d, list(samples.values()), "independent"), portfolio_score(d, list(samples.values()), "joint"))
             for d in ds_]
        g = np.array([a - b for a, b in g if a is not None and b is not None])
        pscore = paired(g) if len(g) >= 3 else None
    W = np.random.default_rng(0).standard_normal((DRAWS, NF))
    es = paired(np.array([energy(d.e, d.S("independent"), W) - energy(d.e, d.S("joint"), W) for d in ds_]))
    share = [float(np.trace(d.jump) / np.trace(d.S("joint"))) for d in ds_]
    corr = [abs(C[i, j]) for d in ds_ for C in [corr_from_cov(d.jump)] for i in range(len(d.idx))
            for j in range(i + 1, len(d.idx))]
    gap = {w: float(np.mean([abs(c - 0.8) for c in rcov[w] if c is not None])) for w in rcov}
    return {
        "markets": sum(d.markets for d in ds_), "days": K, "several": sum(d.markets > 1 for d in ds_),
        "factors": float(k.mean()), "delta_share": float(np.mean(share)),
        "mean_abs_corr": float(np.mean(corr)) if corr else None, "power": pw, "crit": crit, "band": band,
        "primary": {**prim, "likelihood_ratio": math.exp(prim["mean"] / 2), "favours": first},
        "half": {**half, "favours": favours(half)},
        "noise_corr": paired(sc["e4"][:, 0] - sc["independent"][:, 0]),  # what the noise's correlations add
        "candidates": {w: {"score": float(sc[w][:, 0].mean()), "d2_over_k": float((sc[w][:, 1] / k).mean()),
                           "inside_80": float((chi2.cdf(sc[w][:, 1], k) <= 0.8).mean())} for w in WAYS},
        "guards": {"hedge": lines.get("hedge"), "pile": lines.get("pile"), "hedge_ok": hedge_ok,
                   "d2": {"t": m2["t"], "mean": m2["mean"], "ok": m2_ok}, "half_ok": half_ok},
        "random": {"directions": len(rand),
                   "coverage": {w: float(np.mean([c for c in rcov[w] if c is not None])) for w in rcov},
                   "gap_from_80": gap,
                   "spread": {w: float(np.mean([s for s in rspread[w] if s is not None])) for w in rspread}},
        "samples": named or None,
        "portfolio_score": pscore,
        "energy": es,
        "sensitivity": sensitivities(ds_, first),
        "decision": {"outcome": outcome, "weight": WEIGHTS[outcome], "why": why},
    }
