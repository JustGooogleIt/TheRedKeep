"""Fusion v1 and the market quorum (DESIGN.md 4.6 and 6.5).

Every source is a noisy reading of the odds ``q`` or the jump vector ``δ``.
For each quantity we take a precision-weighted average, ``x̂ = Σ w_s x_s / Σ w_s``
with ``w_s = 1 / σ_s²``, and run a consistency check,
``χ² = Σ (x_s − x̂)² / σ_s²``, over the *market* readings, because the quorum is
about independent markets agreeing with each other.

A second check sets the market's jump against the other evidence (past events
and the AI), factor by factor. A market window can't tell an event from other
news that moved its odds and the markets the same day: good economic news can
raise the odds of a rate hike and lift stocks together, so a hike seems to lift
them. Where the two point opposite ways, further apart than their error bars
allow, the jump is contested, and on those factors it rests on the other
evidence alone. The market's reading is the one with a known bias there, and on
resolved markets (E3) daily windows called the sign no better than chance. Each
factor is tested at ``ALPHA``, corrected for the number of factors tested, so
ten factors don't make a false alarm ten times as likely.

Tiers:

* A, Confirmed: odds from a well-matched, liquid market, and the jump backed by
  two or more precise market readings that agree.
* B, Contested: market readings of the odds or the jump disagree beyond their
  error bars, or the market's jump points the opposite way from the past events
  and the AI on some factor, beyond their error bars. The disagreement is the
  insight.
* C, Provisional: odds observed, but the jump has fewer than two precise market
  readings, or the match is weak.
* D, Unpriced: no prediction market fits; odds and jump come from past events
  and the AI only.

A market reading is precise when it could confirm the jump on its own. Projected
onto the fused jump, its error bar on the jump's size is ``1 / sqrt(Σ_f δ̂_f² / σ_f²)``
of the jump (the same projection as the app's jump chart). At ``QUORUM_SE`` or less,
the reading alone tells the jump from no jump at two standard errors. A vaguer
reading still enters the average and the χ² check, so it can contest a jump, but
it can't help confirm one: two vague readings agree with anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

import numpy as np
from scipy.stats import chi2 as chi2_dist
from scipy.stats import norm

from ..factors import BY_KEY, KEYS, NF
from .covariance import precision, weigh

DeltaKind = Literal["etf", "hl", "opt", "past", "ai"]
MARKET_DELTA_KINDS = {"etf", "hl", "opt"}
ALPHA = 0.05  # significance level for "disagree beyond the error bars"
QUORUM_SE = 0.5  # largest error bar on the jump's size, as a share of it, for a reading to count in the quorum


@dataclass
class DeltaReading:
    kind: DeltaKind
    name: str
    detail: str
    delta: np.ndarray  # typical moves per factor
    sigma: np.ndarray  # standard error per factor; inf where the reading says nothing
    n: int = 0  # windows or analogs behind the reading
    cov: np.ndarray | None = None  # the errors' covariance across factors (its diagonal is sigma²); None: independent

    @property
    def market(self) -> bool:
        return self.kind in MARKET_DELTA_KINDS

    def informative(self) -> np.ndarray:
        return np.isfinite(self.sigma) & (self.sigma > 0)

    def covariance(self) -> np.ndarray:
        """The errors' covariance over the factors the reading informs, zero elsewhere, with its correlations at the
        weight E11 set (``covariance.JOINT_WEIGHT``)."""
        ok = self.informative()
        s = np.where(ok, self.sigma, 0.0)
        C = np.diag(s ** 2) if self.cov is None else np.where(np.outer(ok, ok), np.nan_to_num(self.cov), 0.0)
        return weigh((C + C.T) / 2)


@dataclass
class Conflict:
    """A factor on which the market's jump and the other evidence point opposite ways, beyond their error bars."""
    factor: str  # the factor's key
    market: float  # the market readings' jump on it, fused, in typical moves
    other: float  # the past events' and the AI's
    z: float  # their difference over its standard error
    market_names: list[str]
    other_names: list[str]
    factors: list[str] = field(default_factory=list)  # every contested factor, furthest apart first


@dataclass
class FusedDelta:
    delta: np.ndarray
    sigma: np.ndarray
    weights: dict[str, np.ndarray]  # each reading's share of the precision, per factor
    n_market: int  # precise market readings: the ones that count toward the quorum
    chi2: float
    dof: int
    p_value: float | None
    agree: bool | None  # over all market readings; None when no two of them share a factor
    n_market_all: int = 0  # market readings of any precision
    conflict: Conflict | None = None  # the market against the past events and the AI
    cov: np.ndarray | None = None  # the fused jump's covariance across factors; zero on factors with no reading
    borrowed: np.ndarray | None = None  # how far the errors' correlations moved each factor, against fusing factor by factor


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
        return FusedDelta(z, np.full(NF, np.inf), {}, 0, 0.0, 0, None, None, cov=np.zeros((NF, NF)))
    X = np.nan_to_num(np.array([r.delta for r in readings], dtype=float))
    C = [r.covariance() for r in readings]
    ok = [r.informative() for r in readings]
    P = [precision(c, m) for c, m in zip(C, ok)]
    mk = [i for i, r in enumerate(readings) if r.market]
    conflict = _conflict(readings, P, X)
    W = list(P)  # the precision each reading gets in the average: none for the market on a contested factor
    if conflict:
        cut = np.isin(np.array(KEYS), conflict.factors)
        for i in mk:
            W[i] = precision(C[i], ok[i] & ~cut)
    mean, cov = _gls(W, X)
    have = np.diag(sum(W)) > 0
    # What the joint error bars moved: the same readings averaged factor by factor, as if their errors were independent.
    alone, _ = _gls([np.diag(1 / np.where(np.diag(w) > 0, np.diag(np.linalg.pinv(w, hermitian=True)), np.inf)) for w in W], X)
    sigma = np.where(have, np.sqrt(np.clip(np.diag(cov), 0, None)), np.inf)
    Wsum_inv = np.linalg.pinv(sum(W), hermitian=True)
    weights = {r.name: np.where(have, np.diag(Wsum_inv @ W[i]), 0.0) for i, r in enumerate(readings)}

    # Consistency among market readings only, over the factors two or more of them inform. Every market reading
    # takes part, vague or not. With correlated errors a reading's miss on one factor tells on the others, so the
    # statistic is each reading's squared distance from the market readings' own average, in its precision.
    chi2, dof = 0.0, 0
    if len(mk) >= 2:
        m_mean, _ = _gls([P[i] for i in mk], X[mk])
        chi2 = float(sum((X[i] - m_mean) @ P[i] @ (X[i] - m_mean) for i in mk))
        dof = int(sum(_rank(P[i]) for i in mk) - _rank(sum(P[i] for i in mk)))
    n_all = sum(1 for i in mk if np.trace(P[i]) > 0)
    n_market = sum(1 for i in mk if size_error(P[i], mean) <= QUORUM_SE)
    p_value = float(chi2_dist.sf(chi2, dof)) if dof else None
    agree = None if p_value is None else p_value >= ALPHA
    return FusedDelta(mean, sigma, weights, n_market, chi2, dof, p_value, agree, n_all, conflict, cov,
                      np.where(have, mean - alone, 0.0))


def _rank(P: np.ndarray) -> int:
    return int(np.linalg.matrix_rank(P, hermitian=True)) if np.any(P) else 0


def _gls(W: Sequence[np.ndarray], X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The readings' average in information form, ``x̂ = (Σ W_s)⁻¹ Σ W_s x_s``, and its covariance ``(Σ W_s)⁻¹``,
    over the factors any reading informs; zero (no move, no error) on the rest, where the caller marks them."""
    tot = sum(W)
    inv = np.linalg.pinv(tot, hermitian=True)
    mean = inv @ sum(w @ x for w, x in zip(W, X))
    have = np.diag(tot) > 0
    return np.where(have, mean, 0.0), inv


def _conflict(readings: Sequence[DeltaReading], P: Sequence[np.ndarray], X: np.ndarray) -> Conflict | None:
    """The factors where the market readings and the other evidence point opposite ways beyond their error bars, if
    any: ``|z| > z_crit`` with ``z_crit`` the two-sided ``ALPHA`` point over the factors tested. Each side is its
    readings' average in information form, and ``z`` sets the two apart factor by factor, on their error bars there.
    The furthest apart is the one named."""
    mk = [i for i, r in enumerate(readings) if r.market]
    other = [i for i, r in enumerate(readings) if not r.market]
    if not mk or not other:
        return None
    P1, P2 = sum(P[i] for i in mk), sum(P[i] for i in other)
    both = (np.diag(P1) > 0) & (np.diag(P2) > 0)
    if not both.any():
        return None
    m1, V1 = _gls([P[i] for i in mk], X[mk])
    m2, V2 = _gls([P[i] for i in other], X[other])
    se = np.sqrt(np.clip(np.diag(V1) + np.diag(V2), 1e-300, None))
    z = np.where(both, (m1 - m2) / se, 0.0)
    hit = both & (m1 * m2 < 0) & (np.abs(z) > norm.ppf(1 - ALPHA / (2 * both.sum())))
    if not hit.any():
        return None
    order = [int(f) for f in np.argsort(-np.abs(z)) if hit[f]]
    f = order[0]
    return Conflict(KEYS[f], float(m1[f]), float(m2[f]), float(z[f]),
                    [readings[i].name for i in mk if P[i][f, f] > 0], [readings[i].name for i in other if P[i][f, f] > 0],
                    [KEYS[k] for k in order])


def conflict_reason(c: Conflict) -> str:
    """'US stocks: the jump windows say +3.6 typical moves and the AI prior -1.0, ...'."""
    mk = {"Jump windows": "the jump windows", "Hyperliquid windows": "the Hyperliquid windows"}
    who = mk.get(c.market_names[0], "the market readings") if len(c.market_names) == 1 else "the market readings"
    other = {("AI prior",): "the AI prior", ("Past events",): "past events"}.get(tuple(c.other_names),
                                                                               "past events and the AI prior")
    names = [BY_KEY[k].name for k in c.factors or [c.factor]]
    where = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return (f"{BY_KEY[c.factor].name}: {who} say {c.market:+.1f} typical moves and {other} {c.other:+.1f}, opposite "
            "ways and further apart than their error bars allow. The same news can move the odds and the markets, "
            f"and a window can't tell them apart, so on {where} the jump rests on {other} alone.")


def size_error(precision: np.ndarray, fused: np.ndarray) -> float:
    """A reading's error bar on the size of the fused jump, as a share of it, from its precision: a matrix (the
    inverse of its covariance), or ``1/σ²`` per factor when its errors are independent.

    Fitting the reading as ``k`` times the fused jump by generalized least squares gives
    ``se(k) = 1/sqrt(δ̂' P δ̂)``, which is ``1/sqrt(Σ_f δ̂_f²/σ_f²)`` with independent errors.
    """
    u = np.asarray(fused, float)
    Pm = np.asarray(precision, float)
    info = float(u @ Pm @ u) if Pm.ndim == 2 else float(np.sum(Pm * u ** 2))
    return 1 / np.sqrt(info) if info > 0 else np.inf


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
    if delta.conflict is not None:
        return Tier("B", conflict_reason(delta.conflict), badges)
    if match_good and liquid and delta.n_market >= 2 and delta.agree:
        return Tier("A", "Odds from a well-matched, liquid market, and two or more precise market readings of the jump agree.", badges)
    vague = delta.n_market_all - delta.n_market
    if not match_good:
        why = "The odds come from a weak match (a ladder edge, a bound or an overlapping question)."
    elif not liquid:
        why = "The matched market is thin, so its odds are less reliable."
    elif delta.n_market == 0 and vague:
        why = "The market readings of the jump are too vague to confirm it: their error bars are over half the jump."
    elif delta.n_market == 0:
        why = "No market reading of the jump yet; it comes from past events and the AI."
    elif delta.n_market == 1 and vague:
        why = "Only one market reading of the jump is precise enough to count; the others' error bars are over half the jump."
    elif delta.n_market == 1:
        why = "Only one market reading of the jump."
    else:
        why = "The market readings of the jump cover different factors, so they can't check each other."
    return Tier("C", why, badges)
