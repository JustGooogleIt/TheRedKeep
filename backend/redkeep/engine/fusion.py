"""Fusion v1 and the market quorum (DESIGN.md 4.6 and 6.5).

Every source is a noisy reading of the odds ``q`` or the jump vector ``δ``.
For each quantity we take a precision-weighted average, ``x̂ = Σ w_s x_s / Σ w_s``
with ``w_s = 1 / σ_s²``, and run a consistency check,
``χ² = Σ (x_s − x̂)² / σ_s²``, over the *market* readings, because the quorum is
about independent markets agreeing with each other.

Tiers:

* A, Confirmed: odds from a well-matched, liquid market, and the jump backed by
  two or more market readings that agree.
* B, Contested: market readings of the odds or the jump disagree beyond their
  error bars. The disagreement is the insight.
* C, Provisional: odds observed, but the jump has fewer than two market
  readings, or the match is weak.
* D, Unpriced: no prediction market fits; odds and jump come from past events
  and the AI only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

import numpy as np
from scipy.stats import chi2 as chi2_dist

from ..factors import NF

DeltaKind = Literal["etf", "hl", "opt", "past", "ai"]
MARKET_DELTA_KINDS = {"etf", "hl", "opt"}
ALPHA = 0.05  # significance level for "disagree beyond the error bars"


@dataclass
class DeltaReading:
    kind: DeltaKind
    name: str
    detail: str
    delta: np.ndarray  # typical moves per factor
    sigma: np.ndarray  # standard error per factor; inf where the reading says nothing
    n: int = 0  # windows or analogs behind the reading

    @property
    def market(self) -> bool:
        return self.kind in MARKET_DELTA_KINDS

    def informative(self) -> np.ndarray:
        return np.isfinite(self.sigma) & (self.sigma > 0)


@dataclass
class FusedDelta:
    delta: np.ndarray
    sigma: np.ndarray
    weights: dict[str, np.ndarray]  # each reading's share of the precision, per factor
    n_market: int
    chi2: float
    dof: int
    p_value: float | None
    agree: bool | None  # None when fewer than two market readings overlap


@dataclass
class OddsReading:
    source: str  # "Polymarket", "Kalshi", "Ladder", "Bound", "AI prior"
    q: float
    sigma: float
    kind: Literal["market", "ladder", "sum", "bound", "ai"]
    detail: str = ""
    lo: float | None = None
    hi: float | None = None

    @property
    def market(self) -> bool:
        return self.kind != "ai"


@dataclass
class FusedOdds:
    q: float
    sigma: float
    readings: list[OddsReading]
    from_market: bool
    cross_checked: bool  # two or more market readings that agree
    disagree: bool  # two or more market readings that don't
    z: float | None = None


@dataclass
class Tier:
    tier: Literal["A", "B", "C", "D"]
    reason: str
    badges: list[str] = field(default_factory=list)


def fuse_delta(readings: Sequence[DeltaReading]) -> FusedDelta:
    if not readings:
        z = np.zeros(NF)
        return FusedDelta(z, np.full(NF, np.inf), {}, 0, 0.0, 0, None, None)
    X = np.nan_to_num(np.array([r.delta for r in readings], dtype=float))
    S = np.array([r.sigma for r in readings], dtype=float)
    finite = np.isfinite(S) & (S > 0)
    P = np.zeros_like(S)
    P[finite] = 1.0 / S[finite] ** 2
    tot = P.sum(axis=0)
    safe = np.where(tot > 0, tot, 1.0)
    mean = np.where(tot > 0, (P * X).sum(axis=0) / safe, 0.0)
    sigma = np.where(tot > 0, 1.0 / np.sqrt(safe), np.inf)
    weights = {r.name: np.where(tot > 0, P[i] / safe, 0.0) for i, r in enumerate(readings)}

    # Consistency among market readings only, factor by factor.
    mk = [i for i, r in enumerate(readings) if r.market]
    chi2, dof = 0.0, 0
    for f in range(NF):
        rows = [i for i in mk if P[i, f] > 0]
        if len(rows) < 2:
            continue
        w = P[rows, f]
        m = (w * X[rows, f]).sum() / w.sum()
        chi2 += float((w * (X[rows, f] - m) ** 2).sum())
        dof += len(rows) - 1
    n_market = sum(1 for i in mk if P[i].sum() > 0)
    p_value = float(chi2_dist.sf(chi2, dof)) if dof else None
    agree = None if p_value is None else p_value >= ALPHA
    return FusedDelta(mean, sigma, weights, n_market, chi2, dof, p_value, agree)


def fuse_odds(readings: Sequence[OddsReading]) -> FusedOdds:
    """Market readings decide the odds when there are any; the AI never overrides them."""
    market = [r for r in readings if r.market]
    use = market or list(readings)
    if not use:
        raise ValueError("no odds readings")
    w = np.array([1.0 / max(r.sigma, 1e-4) ** 2 for r in use])
    qs = np.array([r.q for r in use])
    q = float((w * qs).sum() / w.sum())
    sigma = float(1.0 / np.sqrt(w.sum()))
    z, disagree, cross = None, False, False
    if len(market) >= 2:
        c2 = float((w * (qs - q) ** 2).sum())
        dof = len(market) - 1
        disagree = chi2_dist.sf(c2, dof) < ALPHA
        cross = not disagree
        a, b = sorted(market, key=lambda r: r.sigma)[:2]
        z = abs(a.q - b.q) / float(np.hypot(a.sigma, b.sigma))
    return FusedOdds(q, sigma, list(readings), bool(market), cross, disagree, z)


def assign_tier(*, odds: FusedOdds, delta: FusedDelta, match_good: bool, liquid: bool) -> Tier:
    badges = ["odds cross-checked"] if odds.cross_checked else []
    if not odds.from_market:
        return Tier("D", "No prediction market fits, so the odds and the jump come from past events and the AI only.", badges)
    if odds.disagree:
        return Tier("B", "The prediction markets disagree on the odds by more than their spreads allow.", badges)
    if delta.agree is False:
        return Tier("B", "Market readings of the jump disagree beyond their error bars.", badges)
    if match_good and liquid and delta.n_market >= 2 and delta.agree:
        return Tier("A", "Odds from a well-matched, liquid market, and two or more market readings of the jump agree.", badges)
    if not match_good:
        why = "The odds come from a weak match (a ladder edge, a bound or an overlapping question)."
    elif not liquid:
        why = "The matched market is thin, so its odds are less reliable."
    elif delta.n_market == 0:
        why = "No market reading of the jump yet; it comes from past events and the AI."
    else:
        why = "Only one market reading of the jump."
    return Tier("C", why, badges)
