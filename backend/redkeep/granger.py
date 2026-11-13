"""Lead and lag (DESIGN.md 4.8, eval E10): does one series' past help predict another's next move?

Granger (1969) called a series ``x`` causal for ``y`` when ``x``'s past improves the forecast of ``y`` beyond what
``y``'s own past, and everything else known, already gives. That is predictability, not cause, and the review this
study starts from (Shojaie and Fox 2022) keeps the name "Granger causality" for exactly that reason. The engine asks
it in three places, all on daily data at the 4 p.m. New York close, in typical moves (DESIGN.md 4.1):

* **Factors against factors.** If yesterday's move in one factor predicted today's move in another, an event's jump
  would keep travelling after the day it happened, and its direction across the ten factors would turn as it did.
  A vector autoregression (VAR) answers this: each factor's move regressed on every factor's moves over the last
  ``p`` days. Factor ``j`` Granger-causes factor ``i`` when the coefficients on ``j``'s past in ``i``'s equation
  are not all zero, with the other eight factors in the regression too, so a third factor that drives both can't
  pass for a link ("network" or conditional Granger causality; the two-factor version is easily fooled, as the
  review's Figure 1 shows).
* **Odds against factors.** The jump windows (DESIGN.md 4.5) regress a day's factor moves on the same day's odds
  changes. If the factors kept moving the day after an odds jump, or had already moved the day before, the same-day
  regression would miss part of the jump, and miss more of it on some factors than others, turning its direction.
* **Odds against odds.** A knock-on link (DESIGN.md 4.4) is the same-day slope of the child's odds changes on the
  parent's. A market that reacts a day late would make the same-day slope too small.

How each part is tested, fixed here before the first run:

* **Lag order** by the Bayesian information criterion (BIC) up to ``PMAX`` days, beside Akaike's (AIC), which
  penalises extra lags less.
* **Links** from a VAR(1) (yesterday only, the lag order that gives a link its best chance when BIC picks none),
  by a Wald test of each source's coefficients in each target's equation. The test uses White's (1980)
  heteroskedasticity-robust covariance, because daily moves come in calm and wild spells, and the ``n·(n−1)``
  tests are controlled for false discoveries at ``FDR`` (Benjamini and Hochberg 1995). Each link's size is its
  partial R²: the share of the target's remaining variance that the source's past explains.
* **Forecasts out of sample.** A VAR refitted every ``REFIT`` days on the trailing ``TRAIN`` days (two years,
  like the typical moves) forecasts each next day. The score is ``1 − Σ (y − ŷ)² / Σ y²`` per factor and pooled:
  the forecast "no move" scores 0, so a positive score is predictability that holds on data the model didn't
  see. Its 95% range resamples blocks of ``BLOCK`` days, since spells of high volatility hang together.
* **The rule.** The engine carries a jump past its first day only if the pooled out-of-sample score's whole 95%
  range is above zero. Otherwise an event's jump is its first day's move, as it was.
* **Odds against factors** on markets the funnel's labels call material (DESIGN.md 6.2), one per group of
  near-duplicates: each factor's move regressed on the day before's, the same day's and the next day's odds change.
  Where the same-day coefficient is clear (``|t| ≥ CLEAR_T``), the other two are compared with it: their median
  share of the same-day move, and how often they point the same way. With no lead or lag they point the same way
  half the time and their shares scatter around zero. A factor's windows would take in the next day only if its
  after-day coefficients point the same way as the same-day ones in significantly more than half the markets
  (a sign test at ``FDR`` across the factors).
* **Odds against odds** for each proposed link with enough history: the same-day slope, the slope of the child's
  next-day change on the parent's (a late child), and Granger tests both ways.

What the tests can't do is order the same-day relations. Granger called those "instantaneous causality" and
showed that predictability can't orient them: when two series move together on the day, either could be driving
the other. The engine never needed them oriented, because it measures each event's same-day jump directly, in
windows where the event's odds moved sharply. That is the identification the structural VAR literature reaches
for when it uses outside shocks as instruments (Stock and Watson 2018) or market surprises in narrow windows
(Gürkaynak, Sack and Swanson 2005; Nakamura and Steinsson 2018), and the VAR adds only what happens after the
first day. The research note ``docs/research/granger-causality.md`` reviews the methods and their pitfalls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

import numpy as np
from scipy import stats

PMAX = 5  # longest lag the order selection considers, in trading days
FDR = 0.05  # false discovery rate for the link tests (Benjamini–Hochberg)
TRAIN = 520  # trailing days each out-of-sample forecast is fitted on: two years, like the typical moves
REFIT = 21  # refit the out-of-sample VAR about monthly
BLOCK = 20  # days per block when resampling the out-of-sample score
BOOT = 1000
CLEAR_T = 2.5  # a same-day coefficient this many standard errors from zero is clear enough to compare lags with
MIN_DAYS = 60  # days of odds and factor moves a market needs for the odds-against-factors test


# -- a vector autoregression, fitted by least squares ---------------------------------------------------------------


def design(Z: np.ndarray, p: int, *, start: int | None = None,
           rows: Sequence[int] | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Regressors ``[1, z_{t−1}, …, z_{t−p}]`` and targets ``z_t`` for every day whose window ``t−p … t`` has no
    missing value, from ``start`` on (default ``p``), or for the given ``rows`` (days whose windows are complete)."""
    T, n = Z.shape
    if rows is None:
        rows = [t for t in range(start if start is not None else p, T) if np.isfinite(Z[t - p:t + 1]).all()]
    X = np.ones((len(rows), 1 + n * p))
    for c, t in enumerate(rows):
        if p:
            X[c, 1:] = Z[t - p:t][::-1].ravel()
    return X, Z[list(rows)], np.array(rows, dtype=int)


@dataclass
class VarFit:
    """``z_t = c + A_1 z_{t−1} + … + A_p z_{t−p} + u_t``, one least-squares regression per factor."""

    names: list[str]
    p: int
    coef: np.ndarray  # (1 + n·p, n): the intercept, then lag 1's block of n rows, then lag 2's, ...
    X: np.ndarray
    Y: np.ndarray
    rows: np.ndarray

    @property
    def resid(self) -> np.ndarray:
        return self.Y - self.X @ self.coef

    @property
    def A(self) -> list[np.ndarray]:
        """``A[l][i, j]``: how much factor ``j``'s move ``l + 1`` days ago adds to factor ``i``'s move today."""
        n = len(self.names)
        return [self.coef[1 + l * n:1 + (l + 1) * n, :].T for l in range(self.p)]

    @property
    def sigma(self) -> np.ndarray:
        """The covariance of the same-day surprises ``u_t``: the instantaneous links a VAR leaves unordered."""
        u = self.resid
        return u.T @ u / max(len(u) - self.X.shape[1], 1)


def fit_var(Z: np.ndarray, p: int, names: Sequence[str], *, start: int | None = None) -> VarFit:
    X, Y, rows = design(Z, p, start=start)
    if len(rows) <= X.shape[1] + 2:
        raise ValueError(f"too few complete days ({len(rows)}) for a VAR({p}) on {len(names)} series")
    coef, *_ = np.linalg.lstsq(X, Y, rcond=None)
    return VarFit(list(names), p, coef, X, Y, rows)


def lag_order(Z: np.ndarray, pmax: int = PMAX) -> dict:
    """BIC and AIC for VAR(0) … VAR(pmax), all fitted on the same days (those with ``pmax`` complete days before
    them), and the order each picks."""
    n = Z.shape[1]
    rows = [t for t in range(pmax, Z.shape[0]) if np.isfinite(Z[t - pmax:t + 1]).all()]
    scores = {}
    for p in range(pmax + 1):
        X, Y, _ = design(Z, p, rows=rows)
        coef, *_ = np.linalg.lstsq(X, Y, rcond=None)
        U = Y - X @ coef
        T = len(U)
        logdet = float(np.linalg.slogdet(U.T @ U / T)[1])
        k = X.shape[1] * n
        scores[p] = {"bic": logdet + np.log(T) * k / T, "aic": logdet + 2 * k / T, "days": T}
    return {"scores": scores, "bic": min(scores, key=lambda p: scores[p]["bic"]),
            "aic": min(scores, key=lambda p: scores[p]["aic"])}


# -- Granger tests ------------------------------------------------------------------------------------------------


@dataclass
class Link:
    """Does ``source``'s past help predict ``target``, given every other series' past?"""

    target: str
    source: str
    F: float
    p_value: float
    partial_r2: float  # share of the target's remaining variance that the source's past explains
    coef: list[float]  # the source's coefficients in the target's equation, lag 1 first
    significant: bool = False  # after the false-discovery control


def bh(p_values: Sequence[float], q: float = FDR) -> np.ndarray:
    """Benjamini–Hochberg: which hypotheses to reject so that, on average, at most a share ``q`` of the rejections
    are false."""
    p = np.asarray(p_values, dtype=float)
    m = len(p)
    if not m:
        return np.zeros(0, dtype=bool)
    order = np.argsort(p)
    passed = p[order] <= q * np.arange(1, m + 1) / m
    out = np.zeros(m, dtype=bool)
    if passed.any():
        out[order[:int(np.max(np.nonzero(passed)[0])) + 1]] = True
    return out


def robust_cov(X: np.ndarray, u: np.ndarray) -> np.ndarray:
    """White's heteroskedasticity-robust covariance of least-squares coefficients, with the small-sample factor
    ``T / (T − k)`` (HC1)."""
    T, k = X.shape
    bread = np.linalg.pinv(X.T @ X)
    meat = (X * (u ** 2)[:, None]).T @ X
    return bread @ meat @ bread * T / max(T - k, 1)


def granger(fit: VarFit, *, q: float = FDR, robust: bool = True) -> list[Link]:
    """Every directed pair's Wald test, with the false-discovery control over all of them."""
    n, p = len(fit.names), fit.p
    if p == 0:
        return []
    T, k = fit.X.shape
    U = fit.resid
    out: list[Link] = []
    for i in range(n):
        u = U[:, i]
        V = robust_cov(fit.X, u) if robust else np.linalg.pinv(fit.X.T @ fit.X) * float(u @ u) / (T - k)
        for j in range(n):
            if i == j:
                continue
            idx = [1 + l * n + j for l in range(p)]
            b = fit.coef[idx, i]
            W = float(b @ np.linalg.solve(V[np.ix_(idx, idx)], b))
            keep = [c for c in range(k) if c not in idx]
            br, *_ = np.linalg.lstsq(fit.X[:, keep], fit.Y[:, i], rcond=None)
            ur = fit.Y[:, i] - fit.X[:, keep] @ br
            out.append(Link(fit.names[i], fit.names[j], W / p, float(stats.f.sf(W / p, p, T - k)),
                            1 - float(u @ u) / float(ur @ ur), [float(x) for x in b]))
    for ln, rej in zip(out, bh([ln.p_value for ln in out], q)):
        ln.significant = bool(rej)
    return out


# -- forecasts on data the model didn't see -----------------------------------------------------------------------


@dataclass
class OutOfSample:
    r2: dict[str, float | None]  # per series: 1 − Σ(y − ŷ)²/Σy²; the forecast "no move" scores 0
    pooled: float | None
    pooled_range: list[float] | None  # 95%, resampling blocks of days
    days: int


def out_of_sample(Z: np.ndarray, names: Sequence[str], *, p: int = 1, train: int = TRAIN, refit: int = REFIT,
                  block: int = BLOCK, draws: int = BOOT, seed: int = 0) -> OutOfSample:
    """Each day's move forecast by a VAR(p) fitted on the ``train`` days before it, refitted every ``refit`` days."""
    T, n = Z.shape
    pred = np.full((T, n), np.nan)
    coef, fitted_at = None, None
    for t in range(train + p, T):
        if coef is None or t - fitted_at >= refit:
            try:
                coef, fitted_at = fit_var(Z[t - train:t], p, names).coef, t
            except ValueError:
                continue
        x = Z[t - p:t][::-1].ravel()
        if np.isfinite(x).all():
            pred[t] = np.concatenate([[1.0], x]) @ coef
    ok = np.isfinite(pred) & np.isfinite(Z)
    r2 = {}
    for i, name in enumerate(names):
        y, f = Z[ok[:, i], i], pred[ok[:, i], i]
        r2[name] = 1 - float(((y - f) ** 2).sum()) / float(y @ y) if len(y) > 30 and y @ y > 0 else None
    days = np.flatnonzero(ok.any(axis=1))
    if len(days) < 2 * block:
        return OutOfSample(r2, None, None, int(len(days)))
    err = np.where(ok, (Z - pred) ** 2, 0.0).sum(axis=1)[days]
    tot = np.where(ok, Z ** 2, 0.0).sum(axis=1)[days]
    pooled = 1 - err.sum() / tot.sum()
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(len(days) / block))
    starts = rng.integers(0, len(days) - block + 1, size=(draws, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(draws, -1)[:, :len(days)]
    boot = 1 - err[idx].sum(axis=1) / tot[idx].sum(axis=1)
    return OutOfSample(r2, float(pooled), [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
                       int(len(days)))


# -- clocks: a series fixed before the close -------------------------------------------------------------------------


@dataclass
class Clock:
    """A daily series against a 4 p.m. price of the same thing: does part of the 4 p.m. move land on the series'
    next day?"""

    days: int
    same_corr: float  # correlation of the two on the same day
    next_corr: float  # the 4 p.m. move today against the series' move tomorrow
    late_share: float  # of the series' response to a 4 p.m. move, the share that shows up a day late


def clock(series: np.ndarray, at_close: np.ndarray) -> Clock | None:
    """``series_t = a + b₀·close_t + b₁·close_{t−1} + e_t``: a series fixed at the close has ``b₁ ≈ 0``; one fixed
    at noon shows the afternoon's share of a day's news a day late, in ``b₁ / (b₀ + b₁)``."""
    y, x0, x1 = series[1:], at_close[1:], at_close[:-1]
    ok = np.isfinite(y) & np.isfinite(x0) & np.isfinite(x1)
    if ok.sum() < MIN_DAYS:
        return None
    X = np.column_stack([np.ones(ok.sum()), x0[ok], x1[ok]])
    b, *_ = np.linalg.lstsq(X, y[ok], rcond=None)
    same = np.isfinite(series) & np.isfinite(at_close)
    return Clock(int(ok.sum()), float(np.corrcoef(series[same], at_close[same])[0, 1]),
                 float(np.corrcoef(x1[ok], y[ok])[0, 1]), float(b[2] / (b[1] + b[2])) if b[1] + b[2] else float("nan"))


# -- how a first-day jump would travel ----------------------------------------------------------------------------


def ma_coefficients(A: Sequence[np.ndarray], h: int) -> list[np.ndarray]:
    """``Ψ_0 = I`` and ``Ψ_k = Σ_l A_l Ψ_{k−l}``: the move ``k`` days after a first-day move ``δ`` is ``Ψ_k δ``.

    The first-day move is the event's measured jump, so nothing here orders the same-day relations: the jump
    windows identify the first day, the VAR only carries it forward, as with an outside instrument."""
    n = A[0].shape[0] if A else 0
    psi = [np.eye(n)]
    for k in range(1, h + 1):
        psi.append(sum((A[l - 1] @ psi[k - l] for l in range(1, min(k, len(A)) + 1)), np.zeros((n, n))))
    return psi


def propagate(daily: np.ndarray, A: Sequence[np.ndarray], h: int = 5) -> np.ndarray:
    """Moves explained day by day (rows), plus what the VAR says each of them goes on to cause on the next ``h``
    days: ``Σ_k Ψ_k x_{t−k}``."""
    psi = ma_coefficients(A, h)
    x = np.nan_to_num(daily)
    out = np.array(daily, dtype=float, copy=True)
    for t in range(len(x)):
        add = sum((psi[k] @ x[t - k] for k in range(1, min(h, t) + 1)), np.zeros(x.shape[1]))
        out[t] = np.where(np.isfinite(out[t]), out[t] + add, out[t])
    return out


# -- odds against factors -----------------------------------------------------------------------------------------


@dataclass
class CrossLag:
    """A factor's move regressed on one market's odds changes the day before, the same day and the day after."""

    days: int
    same: float  # factor move per unit of same-day odds change
    after: float  # the factor's move the day after an odds change: a factor that reacts late
    before: float  # the factor's move the day before an odds change: odds that react late
    se: tuple[float, float, float]  # standard errors of (same, after, before), heteroskedasticity-robust


def cross_lag(dq: np.ndarray, z: np.ndarray) -> CrossLag | None:
    """``z_t = a + same·Δq_t + after·Δq_{t−1} + before·Δq_{t+1} + e_t`` over the days where all four are known.

    ``dq`` and ``z`` are aligned on the same calendar of closes."""
    T = len(dq)
    rows = [t for t in range(1, T - 1) if np.isfinite(z[t]) and np.isfinite(dq[t - 1:t + 2]).all()]
    if len(rows) < MIN_DAYS:
        return None
    r = np.array(rows)
    X = np.column_stack([np.ones(len(r)), dq[r], dq[r - 1], dq[r + 1]])
    if np.linalg.matrix_rank(X) < 4:
        return None
    y = z[r]
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    V = robust_cov(X, y - X @ b)
    se = np.sqrt(np.clip(np.diag(V), 0, None))
    return CrossLag(len(r), float(b[1]), float(b[2]), float(b[3]), (float(se[1]), float(se[2]), float(se[3])))


@dataclass
class TimingSummary:
    """One factor's lead and lag against many markets' odds."""

    factor: str
    pairs: int  # markets with a clear same-day relation to this factor
    after_same_sign: int  # of those, how many have the day-after coefficient pointing the same way
    before_same_sign: int
    after_share: float | None  # median of after / same
    before_share: float | None
    after_p: float | None  # two-sided sign test against one half
    before_p: float | None
    after_significant: bool = False  # after the false-discovery control across factors
    before_significant: bool = False


def timing(results: dict[str, dict[str, CrossLag]], factors: Sequence[str], *, clear_t: float = CLEAR_T,
           q: float = FDR) -> list[TimingSummary]:
    """``results[market][factor]``: sign tests across markets, factor by factor, with the false-discovery control
    over every factor's two tests."""
    out = []
    for f in factors:
        cl = [r[f] for r in results.values() if f in r and r[f] is not None and r[f].se[0] > 0
              and abs(r[f].same / r[f].se[0]) >= clear_t]
        n = len(cl)
        if not n:
            out.append(TimingSummary(f, 0, 0, 0, None, None, None, None))
            continue
        a = sum(np.sign(c.after) == np.sign(c.same) for c in cl)
        b = sum(np.sign(c.before) == np.sign(c.same) for c in cl)
        out.append(TimingSummary(
            f, n, int(a), int(b),
            float(np.median([c.after / c.same for c in cl])), float(np.median([c.before / c.same for c in cl])),
            float(stats.binomtest(int(a), n, 0.5).pvalue), float(stats.binomtest(int(b), n, 0.5).pvalue)))
    ps = [(i, k, getattr(s, k)) for i, s in enumerate(out) for k in ("after_p", "before_p") if getattr(s, k) is not None]
    for (i, k, _), rej in zip(ps, bh([x[2] for x in ps], q)):
        setattr(out[i], k.replace("_p", "_significant"), bool(rej))
    return out


def hac_cov(X: np.ndarray, u: np.ndarray, lags: int = 5) -> np.ndarray:
    """Newey and West's (1987) covariance, robust to calm and wild spells and to errors that hang together for a
    few days, with Bartlett weights."""
    bread = np.linalg.pinv(X.T @ X)
    Xu = X * u[:, None]
    S = Xu.T @ Xu
    for l in range(1, lags + 1):
        G = Xu[l:].T @ Xu[:-l]
        S += (1 - l / (lags + 1)) * (G + G.T)
    return bread @ S @ bread


@dataclass
class IndexTiming:
    """One factor against an index of many markets' odds changes, each signed by the way the funnel's labels say the
    market moves the factor."""

    factor: str
    markets: int
    days: int
    same: float
    after: float
    before: float
    t: tuple[float, float, float]  # of (same, after, before), Newey–West


def index_timing(dq: dict[str, np.ndarray], signs: dict[str, int], z: np.ndarray, factor: str, *,
                 lags: int = 5) -> IndexTiming | None:
    """``z_t = a + same·x_t + after·x_{t−1} + before·x_{t+1}`` with ``x_t = Σ sign_m · Δq_m,t`` over the markets
    the labels give a direction for this factor (a market missing a day adds nothing to it). The signs come from the
    labels, fixed before these data, so no market is picked or signed by how it moved."""
    used = [m for m in dq if signs.get(m)]
    if not used:
        return None
    x = sum(np.sign(signs[m]) * np.nan_to_num(dq[m]) for m in used)
    seen = np.any([np.isfinite(dq[m]) for m in used], axis=0)
    r = np.array([t for t in range(1, len(z) - 1) if np.isfinite(z[t]) and seen[t - 1:t + 2].all()], dtype=int)
    if len(r) < MIN_DAYS:
        return None
    X = np.column_stack([np.ones(len(r)), x[r], x[r - 1], x[r + 1]])
    b, *_ = np.linalg.lstsq(X, z[r], rcond=None)
    se = np.sqrt(np.clip(np.diag(hac_cov(X, z[r] - X @ b, lags)), 1e-300, None))
    return IndexTiming(factor, len(used), len(r), float(b[1]), float(b[2]), float(b[3]),
                       (float(b[1] / se[1]), float(b[2] / se[2]), float(b[3] / se[3])))


# -- odds against odds: knock-on links --------------------------------------------------------------------------


@dataclass
class LinkTiming:
    parent: str
    child: str
    days: int
    same: float  # the child's same-day odds change per unit of the parent's (the engine's link weight)
    same_t: float
    late: float  # the child's next-day change per unit of the parent's change today
    late_t: float
    parent_leads_p: float  # Granger test: the parent's past predicts the child, given the child's own past
    child_leads_p: float


def link_timing(parent: dict[date, float], child: dict[date, float], *, min_days: int = 20) -> LinkTiming | None:
    """The same-day slope (through the origin, as ``pipeline.check_link``), the late slope, and Granger tests both
    ways from a VAR(1) on the two markets' daily odds changes."""
    days = sorted(set(parent) & set(child))
    if len(days) < min_days + 2:
        return None
    dp = np.diff([parent[d] for d in days])
    dc = np.diff([child[d] for d in days])

    def slope(y: np.ndarray, x: np.ndarray) -> tuple[float, float]:
        sxx = float(x @ x)
        if sxx <= 0:
            return 0.0, 0.0
        b = float(x @ y) / sxx
        r = y - b * x
        se = np.sqrt(float(r @ r) / max(len(y) - 1, 1) / sxx)
        return b, b / se if se > 0 else 0.0

    same, same_t = slope(dc, dp)
    late, late_t = slope(dc[1:], dp[:-1])
    try:
        fit = fit_var(np.column_stack([dp, dc]), 1, ["parent", "child"])
    except ValueError:
        return None
    tests = {(ln.target, ln.source): ln.p_value for ln in granger(fit, q=1.0)}
    return LinkTiming("", "", len(dp), same, same_t, late, late_t, tests[("child", "parent")], tests[("parent", "child")])


def kday_slope(qp: np.ndarray, qc: np.ndarray, k: int) -> float | None:
    """The child's ``k``-day odds changes on the parent's, through the origin, over every overlapping window: a
    lasting link keeps its slope as ``k`` grows, while a same-day co-move that undoes itself loses it."""
    if len(qp) <= k + 1:
        return None
    x, y = qp[k:] - qp[:-k], qc[k:] - qc[:-k]
    sxx = float(x @ x)
    return float(x @ y) / sxx if sxx > 0 else None


@dataclass
class PairStudy:
    """Knock-on timing across many pairs of markets: every directed pair from different families (a venue's series
    or event), and the pairs with a clear same-day slope among them."""

    markets: int
    pairs: int
    clear: int  # pairs whose same-day slope is at least ``clear_t`` standard errors from zero
    late_same_sign: int  # of the clear pairs, how many have a late slope pointing the same way
    late_p: float | None  # sign test against one half
    late_share: float | None  # median of late / same
    late_clear: int  # clear pairs whose late slope is itself clear (|t| ≥ 2) and points the same way
    granger_significant: int  # clear pairs where the parent's past predicts the child, after the false-discovery control
    persistence: dict[int, float]  # k: median over clear pairs of the k-day slope over the same-day slope
    split_pairs: int  # pairs clear on the first half of their days
    split_error: float | None  # on the second half: the pooled squared error of each child's daily changes predicted
    #                            from the parent's with the first half's slope, over that of predicting no change
    split_error_kday: float | None  # the same for two-day changes, with the first half's two-day slope


def pair_study(series: dict[str, dict[date, float]], family: dict[str, str], *, min_days: int = MIN_DAYS,
               clear_t: float = 3.0, q: float = FDR) -> PairStudy:
    keys = sorted(series)
    rows: list[tuple[LinkTiming, np.ndarray, np.ndarray]] = []
    split_err, split_err2, split_zero, split_zero2, split_n = 0.0, 0.0, 0.0, 0.0, 0
    for p in keys:
        for c in keys:
            if p == c or family.get(p) == family.get(c):
                continue
            lt = link_timing(series[p], series[c], min_days=min_days)
            if lt is None:
                continue
            days = sorted(set(series[p]) & set(series[c]))
            qp, qc = np.array([series[p][d] for d in days]), np.array([series[c][d] for d in days])
            rows.append((lt, qp, qc))
            half = len(qp) // 2
            if half >= min_days:
                first = link_timing(dict(zip(days[:half], qp[:half])), dict(zip(days[:half], qc[:half])),
                                    min_days=min_days // 2)
                w2 = kday_slope(qp[:half], qc[:half], 2)
                if first is not None and abs(first.same_t) >= clear_t and w2 is not None:
                    x, y = np.diff(qp[half - 1:]), np.diff(qc[half - 1:])
                    x2, y2 = qp[half + 1:] - qp[half - 1:-2], qc[half + 1:] - qc[half - 1:-2]
                    split_err += float(((y - first.same * x) ** 2).sum())
                    split_zero += float(y @ y)
                    split_err2 += float(((y2 - w2 * x2) ** 2).sum())
                    split_zero2 += float(y2 @ y2)
                    split_n += 1
    clear = [(lt, qp, qc) for lt, qp, qc in rows if abs(lt.same_t) >= clear_t]
    same_sign = sum(np.sign(lt.late) == np.sign(lt.same) for lt, _, _ in clear)
    persistence = {}
    for k in (2, 3, 5):
        r = [v / lt.same for lt, qp, qc in clear if lt.same and (v := kday_slope(qp, qc, k)) is not None]
        if r:
            persistence[k] = float(np.median(r))
    sig = bh([lt.parent_leads_p for lt, _, _ in clear], q) if clear else np.zeros(0, dtype=bool)
    return PairStudy(
        len(keys), len(rows), len(clear), int(same_sign),
        float(stats.binomtest(int(same_sign), len(clear), 0.5).pvalue) if clear else None,
        float(np.median([lt.late / lt.same for lt, _, _ in clear if lt.same])) if clear else None,
        int(sum(abs(lt.late_t) >= 2 and np.sign(lt.late) == np.sign(lt.same) for lt, _, _ in clear)),
        int(sig.sum()), persistence, split_n,
        split_err / split_zero if split_zero else None, split_err2 / split_zero2 if split_zero2 else None)


# -- helpers for reports ------------------------------------------------------------------------------------------


@dataclass
class FactorStudy:
    """E10's first part on one sample of days: lag order, links, and forecasts out of sample."""

    label: str
    start: date
    end: date
    names: list[str]
    days: int
    order: dict
    links: list[Link]
    own: dict[str, float]  # each series' coefficient on its own yesterday
    oos: OutOfSample
    notes: list[str] = field(default_factory=list)


def factor_study(Z: np.ndarray, dates: Sequence[date], names: Sequence[str], label: str, *,
                 min_cover: float = 0.8) -> FactorStudy:
    """The VAR tests on the series that cover at least ``min_cover`` of the days (a factor whose proxy starts late
    would otherwise cut the sample to its own history)."""
    keep = [i for i in range(Z.shape[1]) if np.isfinite(Z[:, i]).mean() >= min_cover]
    left = [names[i] for i in range(Z.shape[1]) if i not in keep]
    Zk, nk = Z[:, keep], [names[i] for i in keep]
    order = lag_order(Zk)
    fit = fit_var(Zk, 1, nk)
    links = granger(fit)
    own = {nk[i]: float(fit.A[0][i, i]) for i in range(len(nk))}
    oos = out_of_sample(Zk, nk)
    notes = [f"Left out for covering under {min_cover:.0%} of the days: {', '.join(left)}"] if left else []
    return FactorStudy(label, dates[0], dates[-1], nk, len(fit.rows), order, links, own, oos, notes)
