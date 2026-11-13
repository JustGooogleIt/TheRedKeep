"""Jump windows (DESIGN.md 4.5 and 6.4), on daily data for this slice.

A window is a trading day on which at least one monitored market's odds moved
sharply (``move``, 5 points by default). In those windows we regress each
factor's move (in typical moves) on *all* the odds changes that day, so an
event's jump is separated from the moves of events that shifted with it:

    r_f,w = Σ_k δ_k,f · Δq_k,w + noise

Odds are read at 4 p.m. New York time, when the US stock market closes, so they
line up with daily closes. The design calls for 30-minute windows on intraday
data; daily windows are a coarser first version whose wider error bars carry
through fusion. Each event's reading counts as one ETF-window market reading
for the quorum.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np

from ..engine.covariance import moves_corr, shrink_corr
from ..engine.fusion import DeltaReading
from ..factordata import FactorData
from ..factors import NF

NY = ZoneInfo("America/New_York")


def at_close(points: list[tuple[datetime, float]], hour: int = 16) -> dict[date, float]:
    """The last odds at or before the stock market close, each New York day."""
    out: dict[date, tuple[datetime, float]] = {}
    for t, q in points:
        local = t.astimezone(NY)
        if local.hour < hour or (local.hour == hour and local.minute == 0):
            d = local.date()
            if d not in out or t > out[d][0]:
                out[d] = (t, q)
    return {d: q for d, (_, q) in out.items()}


@dataclass
class WindowFit:
    readings: dict[str, DeltaReading]
    windows: list[date]
    moves: dict[str, int]  # sharp moves per event
    left_out: int = 0  # windows some factor left to another reading (``skip``)
    shrink: float | None = None  # how far the residuals' correlations were shrunk toward the calm days' (1: all the way)
    resid_days: int = 0  # windows every fitted factor has, behind those correlations


def jump_windows(odds: dict[str, dict[date, float]], fd: FactorData, *, move: float = 0.05, min_moves: int = 3,
                 skip: dict[int, set[date]] | None = None, left_to: str = "another reading") -> WindowFit:
    """``skip`` leaves days out of one factor's regression. The pipeline passes the days whose window spans a weekend
    that Hyperliquid's perps measured (``left_to`` names them), so the weekend reading and this one rest on separate
    data and count as independent readings for the quorum."""
    dates = fd.dates
    prev = {d: dates[i - 1] for i, d in enumerate(dates) if i}
    ids = sorted(odds)
    dQ = np.zeros((len(dates), len(ids)))
    seen = np.zeros_like(dQ, dtype=bool)
    for j, e in enumerate(ids):
        s = odds[e]
        for t, d in enumerate(dates):
            p = prev.get(d)
            if p in s and d in s:
                dQ[t, j] = s[d] - s[p]
                seen[t, j] = True
    sharp = (np.abs(dQ) >= move) & seen
    rows = np.flatnonzero(sharp.any(axis=1))
    moves = {e: int(sharp[:, j].sum()) for j, e in enumerate(ids)}
    cols = [j for j, e in enumerate(ids) if moves[e] >= min_moves]
    readings: dict[str, DeltaReading] = {}
    if not cols or len(rows) < len(cols) + 2:
        return WindowFit(readings, [dates[i] for i in rows], moves)

    Z = fd.Z
    # Ordinary day-to-day noise of each factor, from days without sharp moves.
    calm = np.setdiff1d(np.arange(len(dates)), rows)
    noise = np.full(NF, 1 / np.sqrt(5))  # a typical move is weekly, so a calm day is about 1/sqrt(5) of one
    for f in range(NF):
        x = Z[calm, f]
        x = x[np.isfinite(x)]
        if len(x) > 30:  # a factor with no data keeps the default; it gets no reading anyway
            noise[f] = x.std(ddof=1)
    delta = np.full((len(ids), NF), np.nan)
    sigma = np.full((len(ids), NF), np.inf)
    skip = skip or {}
    left = {i for i in rows if any(dates[i] in days for days in skip.values())}
    fits: dict[int, Fit] = {}
    for f in range(NF):
        # This factor's windows: the days it has a move (a series can start late or end early), less any skipped.
        r = np.array([i for i in rows if np.isfinite(Z[i, f]) and dates[i] not in skip.get(f, ())], dtype=int)
        y = Z[r, f]
        # Events with enough sharp moves among this factor's windows: all of ``cols`` unless some were left out.
        cf = [j for j in cols if sharp[r, j].sum() >= min_moves]
        if not cf or len(r) < len(cf) + 2:
            continue
        A = dQ[np.ix_(r, cf)]
        AtA_inv = np.linalg.pinv(A.T @ A)
        coef = AtA_inv @ (A.T @ y)
        dof = len(r) - len(cf)
        resid = y - A @ coef
        s2 = float(resid @ resid) / dof if dof >= 3 else noise[f] ** 2
        s2 = max(s2, noise[f] ** 2)  # never claim less noise than an ordinary day
        delta[cf, f] = coef
        sigma[cf, f] = np.sqrt(np.clip(np.diag(AtA_inv) * s2, 0, None))
        fits[f] = Fit(r, cf, A, AtA_inv, resid, float(np.sqrt(s2)), dof >= 3)
    cov, lam, n_resid = covariances(fits, len(ids), Z[calm])
    kept = f"; {len(left)} that span a weekend are left to {left_to}" if left else ""
    for j in cols:
        e = ids[j]
        readings[e] = DeltaReading(
            kind="etf", name="Jump windows",
            detail=f"{moves[e]} days with odds moves of {move * 100:.0f}+ points, out of {len(rows)} windows in {len(dates)} trading days{kept}",
            delta=delta[j], sigma=sigma[j], n=moves[e], cov=cov[j],
        )
    return WindowFit(readings, [dates[i] for i in rows], moves, len(left), lam, n_resid)


@dataclass
class Fit:
    """One factor's regression over its windows."""
    rows: np.ndarray  # its windows, as day indices
    cols: list[int]  # the events it fits, as indices into the events
    A: np.ndarray  # odds changes, windows by events
    AtA_inv: np.ndarray
    resid: np.ndarray
    s: float  # the residuals' standard deviation, at least a calm day's
    resid_ok: bool  # enough windows left over to read the residuals


def covariances(fits: dict[int, Fit], n_events: int, calm: np.ndarray) -> tuple[np.ndarray, float | None, int]:
    """Each event's covariance of its jump across factors, from the regressions' residuals.

    Each factor's jumps are an ordinary least squares fit on its own windows. Two factors' fits share the windows
    both have, and on each of those days the two residuals are correlated as the day's other news moved both
    factors. So the covariance of event ``k``'s jump on factors ``f`` and ``g`` is

        σ_fg · [(A_f'A_f)⁻¹ A_f[C]' A_g[C] (A_g'A_g)⁻¹]_kk

    with ``C`` the shared windows and ``σ_fg`` the residuals' covariance. With the same windows for every factor this
    is ``σ_fg · [(A'A)⁻¹]_kk``, the seemingly unrelated regressions result with the same regressors in every
    equation, where fitting each factor alone loses nothing (Zellner 1962). ``σ_fg`` is ``ρ_fg s_f s_g``: each
    factor's residual spread as in its error bars, and the residuals' correlation over the windows every factor
    has, shrunk toward the factors' correlation on calm days (``covariance.shrink_corr``), with the noise of those
    correlations counted over the residuals' own degrees of freedom (the windows less the fitted jumps)."""
    cov = np.full((n_events, NF, NF), np.nan)
    F = sorted(fits)
    if not F:
        return cov, None, 0
    common = fits[F[0]].rows
    for f in F[1:]:
        common = np.intersect1d(common, fits[f].rows)
    target = moves_corr(calm[:, F])
    if all(fits[f].resid_ok for f in F) and len(common) >= 3:
        at = {f: np.searchsorted(fits[f].rows, common) for f in F}
        U = np.column_stack([fits[f].resid[at[f]] for f in F])
        # The fits used up some of these days' freedom: the fitted jumps' leverage over them, the most of any factor.
        used = max(float(np.einsum("ij,jk,ik->", fits[f].A[at[f]], fits[f].AtA_inv, fits[f].A[at[f]])) for f in F)
        R, lam = shrink_corr(U, target, dof=len(common) - used)
    else:
        R, lam = target, 1.0
    for a, f in enumerate(F):
        for b, g in enumerate(F):
            if b < a:
                continue
            ff, fg = fits[f], fits[g]
            C, i_f, i_g = np.intersect1d(ff.rows, fg.rows, return_indices=True)
            M = ff.AtA_inv @ ff.A[i_f].T @ fg.A[i_g] @ fg.AtA_inv
            s_fg = R[a, b] * ff.s * fg.s
            pos_g = {j: p for p, j in enumerate(fg.cols)}
            for p, j in enumerate(ff.cols):
                q = pos_g.get(j)
                if q is not None:
                    cov[j, f, g] = cov[j, g, f] = s_fg * M[p, q]
    return cov, lam, int(len(common))
