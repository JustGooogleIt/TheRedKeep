"""Scenario simulation over the event graph (DESIGN.md 4.7).

Events are sampled in resolution order, parents first. A child's probability
given its parents uses a logistic link whose slopes match the edge weights to
first order and whose intercept is solved by bisection so the child's overall
probability equals its odds. In each simulated future the portfolio moves
``Σ_e Δ_e_direct · (1[e happens] − q_e)``; knock-on effects arrive through the
sampled children. Events that resolve after the horizon don't happen or fail:
their odds just move with their parents.

At market odds the mean move is zero by construction. The run is seeded, so the
same inputs always give the same numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np

from .impact import State


@dataclass
class Driver:
    id: str
    base: float  # how often it happens overall (or its mean odds, past the horizon)
    tail: float  # how often it happens in the worst 5% of futures
    direct: float


@dataclass
class SimResult:
    n: int
    mean: float
    sd: float
    p5: float
    p50: float
    p95: float
    es5: float  # expected shortfall: the average of the worst 5%
    drivers: list[Driver]
    hist_lo: float
    hist_hi: float
    bins: list[int]


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def _solve_intercept(p: float, betas: list[float], parents: list[tuple[float, float]]) -> float:
    """α such that Σ_configs P(config) σ(α + Σ β_t (x_t − q_t)) = p.

    ``parents`` holds (p_parent, q_parent); parents are treated as independent,
    which is exact for a single parent and a first-order approximation otherwise.
    """
    confs = []
    for xs in product((0, 1), repeat=len(betas)):
        pr, z = 1.0, 0.0
        for x, beta, (pp, qp) in zip(xs, betas, parents):
            pr *= pp if x else 1 - pp
            z += beta * (x - qp)
        confs.append((pr, z))
    lo, hi = -30.0, 30.0
    for _ in range(60):
        mid = (lo + hi) / 2
        f = sum(pr * _sigmoid(mid + z) for pr, z in confs)
        if f > p:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def simulate(
    state: State,
    *,
    n: int = 20_000,
    seed: int = 7,
    calibrated: bool = False,
    noise_sd: float = 0.0,
    bins: int = 38,
) -> SimResult:
    """Simulate portfolio outcomes, in % of the portfolio, over the horizon.

    ``noise_sd`` optionally adds ordinary day-to-day noise over the horizon
    (one standard deviation, in %), for example ``sqrt(b' Σ b × weeks)``.
    """
    evs = [state.events[i] for i in state.order if not state.events[i].resolved]
    idx = {e.id: j for j, e in enumerate(evs)}
    m = len(evs)
    q = np.array([e.q for e in evs])
    p = np.array([e.p_hat if calibrated else e.q for e in evs])
    direct = np.array([e.direct for e in evs])
    parents = [[(idx[ln.parent], ln.w) for ln in e.parents if ln.parent in idx] for e in evs]

    alpha = np.zeros(m)
    betas: list[list[float]] = [[] for _ in range(m)]
    for j, e in enumerate(evs):
        if not e.in_horizon or not parents[j]:
            continue
        pv = max(p[j] * (1 - p[j]), 0.02)
        betas[j] = [w / pv for _, w in parents[j]]
        alpha[j] = _solve_intercept(p[j], betas[j], [(p[k], q[k]) for k, _ in parents[j]])

    rng = np.random.default_rng(seed)
    X = np.zeros((n, m))
    pnl = np.zeros(n)
    for j, e in enumerate(evs):
        if e.in_horizon:
            if parents[j]:
                z = alpha[j] + sum(beta * (X[:, k] - q[k]) for beta, (k, _) in zip(betas[j], parents[j]))
                pj = _sigmoid(z)
            else:
                pj = p[j]
            X[:, j] = (rng.random(n) < pj).astype(float)
        else:
            sh = sum(w * (X[:, k] - q[k]) for k, w in parents[j]) if parents[j] else 0.0
            X[:, j] = np.clip(q[j] + sh, 0.001, 0.999)
        pnl += direct[j] * (X[:, j] - q[j])
    if noise_sd:
        pnl += noise_sd * rng.standard_normal(n)

    order = np.argsort(pnl, kind="stable")
    srt = pnl[order]
    at = lambda f: float(srt[min(n - 1, max(0, int(np.floor(f * (n - 1)))))])  # noqa: E731
    k5 = max(1, int(np.floor(0.05 * n)))
    tail = order[:k5]
    drivers = [
        Driver(id=e.id, base=float(X[:, j].mean()), tail=float(X[tail, j].mean()), direct=float(direct[j]))
        for j, e in enumerate(evs)
        if e.in_horizon
    ]
    lo, hi = at(0.002), at(0.998)
    if hi - lo < 1e-6:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.04
    lo, hi = lo - pad, hi + pad
    counts, _ = np.histogram(pnl, bins=bins, range=(lo, hi))
    return SimResult(
        n=n, mean=float(pnl.mean()), sd=float(pnl.std()), p5=at(0.05), p50=at(0.5), p95=at(0.95),
        es5=float(srt[:k5].mean()), drivers=drivers, hist_lo=lo, hist_hi=hi, bins=[int(c) for c in counts],
    )
