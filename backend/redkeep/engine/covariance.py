"""Joint error bars: how a jump reading's errors move together across the factors (DESIGN.md 4.6).

A reading's error bars say how far each factor's jump could be off. Their covariance says how the misses go
together: a jump window that overstates the fall in stocks on a day of other bad news most likely overstates the rise
in the VIX as well, since the same news moved both. Kept per factor, those errors look independent, and a
portfolio's error bar adds them as if they could cancel or pile up at random.

This module holds the shared pieces:

* correlations of the factors' daily moves, as a target to shrink toward (``moves_corr``);
* the shrinkage of a few days' residual correlations toward that target (``shrink_corr``, Schäfer and Strimmer's
  closed-form intensity, the correlation form of Ledoit and Wolf's);
* a valid correlation matrix from a rough one (``nearest_corr``);
* the precision (information) matrix of a reading that says nothing about some factors (``precision``);
* how much of the correlations the engine uses (``JOINT_WEIGHT``, ``weigh``), which eval E11 sets.
"""

from __future__ import annotations

import numpy as np

MIN_EIG = 1e-6  # smallest eigenvalue kept in a correlation matrix, so every one can be inverted
# How much of the errors' correlations the engine uses: 1 all of them, 0.5 half, 0 none (factor by factor, as before
# E11). Each factor's own error bar is the same at any weight. Set by E11's rule, fixed before its run (joint.py).
JOINT_WEIGHT = 1.0
SIZE_SHARED = 0.5  # of a size error (a share of the estimated move): the part that is one error for the whole jump


def nearest_corr(R: np.ndarray, min_eig: float = MIN_EIG) -> np.ndarray:
    """A correlation matrix near ``R``: symmetric, eigenvalues at least ``min_eig``, ones on the diagonal."""
    R = np.asarray(R, dtype=float)
    if R.size == 0:
        return R.copy()
    R = (R + R.T) / 2
    w, V = np.linalg.eigh(R)
    R = (V * np.clip(w, min_eig, None)) @ V.T
    d = np.sqrt(np.clip(np.diag(R), min_eig, None))
    R = R / np.outer(d, d)
    np.fill_diagonal(R, 1.0)
    return R


def moves_corr(Z: np.ndarray, *, min_obs: int = 30) -> np.ndarray:
    """The correlations of the factors' moves over the rows of ``Z`` (days by factors, NaN where a factor has no
    move), each pair over the days both have. A factor with fewer than ``min_obs`` days is left uncorrelated."""
    Z = np.asarray(Z, dtype=float)
    k = Z.shape[1]
    R = np.eye(k)
    ok = np.isfinite(Z)
    for i in range(k):
        for j in range(i + 1, k):
            m = ok[:, i] & ok[:, j]
            if m.sum() < min_obs:
                continue
            x, y = Z[m, i] - Z[m, i].mean(), Z[m, j] - Z[m, j].mean()
            den = np.sqrt((x @ x) * (y @ y))
            if den > 0:
                R[i, j] = R[j, i] = float(x @ y / den)
    return nearest_corr(R)


def shrink_corr(U: np.ndarray, target: np.ndarray, dof: float | None = None) -> tuple[np.ndarray, float]:
    """Residual correlations from the rows of ``U`` (days by factors, no NaN), shrunk toward ``target``.

    The residuals of a regression without an intercept, so their correlations are taken about zero. The intensity
    is Schäfer and Strimmer's (2005) closed form for a correlation matrix, ``λ = Σ Var(r_ij) / Σ (r_ij − t_ij)²``
    over the pairs ``i ≠ j``, clipped to [0, 1]: the noisier the few days' correlations, and the closer they are to
    the target, the more the target counts. ``dof``: how many of the rows' degrees of freedom the fit left, when it
    used some up (a regression's residuals over n days hold n less the fitted jumps' leverage); the variance of each
    correlation is over those. With fewer than three days, the target alone."""
    U = np.asarray(U, dtype=float)
    T = np.asarray(target, dtype=float)
    n, k = U.shape
    if k < 2:
        return np.eye(k), 1.0
    rms = np.sqrt((U ** 2).mean(axis=0)) if n else np.zeros(k)
    if n < 3 or not (rms > 0).all():
        return nearest_corr(T), 1.0
    X = U / rms
    W = X[:, :, None] * X[:, None, :]  # n by k by k products, each pair's mean is its correlation
    r = W.mean(axis=0)
    eff = n if dof is None else min(max(float(dof), 1.0), n)
    var = ((W - r) ** 2).sum(axis=0) / ((n - 1) * eff)  # the variance of that mean, over the rows' own freedom
    off = ~np.eye(k, dtype=bool)
    gap = float(((r - T)[off] ** 2).sum())
    lam = 1.0 if gap <= 0 else float(np.clip(var[off].sum() / gap, 0.0, 1.0))
    R = lam * T + (1 - lam) * np.clip(r, -1, 1)
    return nearest_corr(R), lam


def precision(cov: np.ndarray, ok: np.ndarray) -> np.ndarray:
    """The information matrix of a reading with covariance ``cov`` on the factors ``ok`` and nothing to say about
    the rest: the inverse of ``cov`` over ``ok``, zero elsewhere."""
    k = len(ok)
    P = np.zeros((k, k))
    idx = np.flatnonzero(ok)
    if idx.size:
        C = np.asarray(cov, dtype=float)[np.ix_(idx, idx)]
        P[np.ix_(idx, idx)] = np.linalg.pinv((C + C.T) / 2, hermitian=True)
    return P


def corr_from_cov(cov: np.ndarray) -> np.ndarray:
    """Correlations from a covariance; zero where a variance is zero or not finite."""
    C = np.asarray(cov, dtype=float)
    d = np.sqrt(np.where(np.isfinite(np.diag(C)) & (np.diag(C) > 0), np.diag(C), np.nan))
    R = C / np.outer(d, d)
    R[~np.isfinite(R)] = 0.0
    np.fill_diagonal(R, 1.0)
    return R


def weigh(C: np.ndarray, w: float | None = None) -> np.ndarray:
    """``C`` with its off-diagonal part at weight ``w`` (``JOINT_WEIGHT`` when None): ``w·C + (1 − w)·diag(C)``, a
    valid covariance at any weight from 0 to 1, with the same variances."""
    w = JOINT_WEIGHT if w is None else w
    C = np.asarray(C, dtype=float)
    if w == 1.0:
        return C
    D = np.diag(np.diag(C))
    return w * C + (1 - w) * D


def size_cov(delta: np.ndarray, share: float) -> np.ndarray:
    """The covariance of a size error of ``share`` of the estimated move: ``SIZE_SHARED`` of it one error for the
    whole jump (``δδ'``, a big estimate is uncertain in size as a whole), the rest factor by factor (``diag(δ²)``,
    since the jump's shape can be off too). Each factor's own variance is ``(share · δ_f)²`` either way."""
    d = np.nan_to_num(np.asarray(delta, dtype=float))
    return share ** 2 * (SIZE_SHARED * np.outer(d, d) + (1 - SIZE_SHARED) * np.diag(d ** 2))
