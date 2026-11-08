"""Ladders and bounds (DESIGN.md 6.3 steps 3 and 4).

Markets that differ only by threshold or deadline are fitted together under a
monotonic constraint: a higher "above" threshold is never more likely, and a
later deadline is never less likely. The fit is weighted isotonic regression
(pool adjacent violators). We interpolate to the event's exact terms and never
extrapolate past the ladder; outside it we report a bound.

Interpolation space:

* thresholds: linear in log-odds between the two neighbouring strikes;
* deadlines: linear in cumulative hazard, ``H = −ln(1 − q)``, which is exact if
  the event's hazard rate is constant between the two deadlines.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

EPS = 1e-4


@dataclass(frozen=True)
class LadderPoint:
    x: float  # threshold value, or days since an epoch for deadlines
    q: float
    sigma: float
    key: str


@dataclass
class LadderResult:
    kind: Literal["interpolated", "bound"]
    q: float | None
    lo: float
    hi: float
    sigma: float
    fitted: list[tuple[float, float]]
    legs: list[str]
    note: str


def pav(y: Sequence[float], w: Sequence[float], increasing: bool = True) -> list[float]:
    """Weighted isotonic regression (pool adjacent violators)."""
    sign = 1.0 if increasing else -1.0
    blocks: list[list[float]] = []  # [mean, weight, count]
    for yi, wi in zip(y, w):
        blocks.append([sign * yi, wi, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            m2, w2, c2 = blocks.pop()
            m1, w1, c1 = blocks.pop()
            blocks.append([(m1 * w1 + m2 * w2) / (w1 + w2), w1 + w2, c1 + c2])
    out: list[float] = []
    for m, _, c in blocks:
        out.extend([sign * m] * int(c))
    return out


def _to(q: float, space: str) -> float:
    q = min(max(q, EPS), 1 - EPS)
    return math.log(q / (1 - q)) if space == "logit" else -math.log(1 - q)


def _from(z: float, space: str) -> float:
    return 1 / (1 + math.exp(-z)) if space == "logit" else 1 - math.exp(-z)


def estimate(points: Sequence[LadderPoint], x_star: float, *, increasing: bool, space: Literal["logit", "hazard"]) -> LadderResult | None:
    pts = sorted(points, key=lambda p: p.x)
    if not pts:
        return None
    fit = pav([p.q for p in pts], [1 / max(p.sigma, 1e-3) ** 2 for p in pts], increasing)
    fitted = [(p.x, f) for p, f in zip(pts, fit)]
    legs = [p.key for p in pts]
    xs = [p.x for p in pts]
    for p, f in zip(pts, fit):
        if abs(p.x - x_star) < 1e-9:
            return LadderResult("interpolated", f, f, f, p.sigma, fitted, [p.key], "An exact rung of the ladder.")
    if xs[0] < x_star < xs[-1]:
        i = max(j for j in range(len(xs)) if xs[j] < x_star)
        a, b = pts[i], pts[i + 1]
        t = (x_star - a.x) / (b.x - a.x)
        z = (1 - t) * _to(fit[i], space) + t * _to(fit[i + 1], space)
        q = _from(z, space)
        sigma = math.hypot((1 - t) * a.sigma + t * b.sigma, 0.25 * abs(fit[i + 1] - fit[i]))
        return LadderResult("interpolated", q, min(fit[i], fit[i + 1]), max(fit[i], fit[i + 1]), sigma, fitted,
                            [a.key, b.key], "Interpolated between the two nearest rungs, never past them.")
    # Outside the ladder: a bound only.
    below = x_star < xs[0]
    edge = fit[0] if below else fit[-1]
    # P rises with x (increasing): below the ladder the edge is an upper bound; above it, a lower bound.
    if increasing == below:
        lo, hi = 0.0, edge
    else:
        lo, hi = edge, 1.0
    key = pts[0].key if below else pts[-1].key
    return LadderResult("bound", None, lo, hi, (hi - lo) / math.sqrt(12), fitted, [key],
                        "Outside the ladder, so only a bound: we never extrapolate.")
