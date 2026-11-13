"""Past similar events: an event study of factor moves around analog dates (DESIGN.md 6.4).

For each analog, the factor moves from the close before the event to the close
``days`` trading days later, in typical moves, divided by ``surprise`` (how much
of the event the market had not expected). The reading averages the analogs,
weighting each by its noise, and its error adds:

* ordinary noise over the window: ``√(days / 5)`` typical moves (a typical move
  is a weekly standard deviation), scaled by ``1 / surprise``;
* the spread between analogs, when there are several;
* a model error, because no past event is quite the same: half the measured move, with a floor of 0.3 typical
  moves, added in quadrature. One analog can't pin the jump's size better than that, however quiet its day was.
  Backtests on resolved events (E3) will measure this error; until then it is set wide.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from ..engine.covariance import moves_corr, size_cov
from ..engine.fusion import DeltaReading
from ..events import EventSpec
from ..factordata import FactorData, window_bounds
from ..factors import NF

MODEL_FLOOR = 0.3
MODEL_SHARE = 0.5  # of the measured move: a past event's move could be half again as large or small
ERA = 365  # days either side of an analog whose moves say how the factors' noise moved together then
NOW = 3 * 365  # days up to the reading whose moves say how they move together now


def analog_reading(spec: EventSpec, fd: FactorData, *, until: date | None = None) -> DeltaReading | None:
    """``until``: the last day whose moves may say how the factors move together (a replay's first evening, so no
    evening sees later prices); the last day of ``fd`` when None."""
    cal = fd.calendar()
    rows, var, used = [], [], []
    for a in spec.analogs:
        b = window_bounds(cal, a.date, a.days)
        if not b:
            continue
        mv = fd.move(*b)
        if not np.isfinite(mv).any():
            continue
        rows.append(mv / a.surprise)
        var.append((a.days / 5) / a.surprise**2)
        used.append(a)
    if not rows:
        return None
    X = np.array(rows)
    v = np.array(var)[:, None] * np.ones((1, NF))
    w = np.where(np.isfinite(X), 1 / v, 0.0)
    tot = w.sum(axis=0)
    safe = np.where(tot > 0, tot, 1)
    mean = np.where(tot > 0, (w * np.nan_to_num(X)).sum(axis=0) / safe, np.nan)
    s2 = np.where(tot > 0, 1 / safe, np.inf)
    spread = np.zeros(NF)
    for f in range(NF):
        col = X[np.isfinite(X[:, f]), f]
        if len(col) >= 2:
            spread[f] = col.var(ddof=1) / len(col)
    sigma = np.sqrt(s2 + spread + MODEL_FLOOR**2 + (MODEL_SHARE * np.nan_to_num(mean)) ** 2)
    sigma = np.where(tot > 0, sigma, np.inf)
    detail = "; ".join(f"{a.note} ({a.date:%b %-d, %Y})" for a in used)
    ok = tot > 0
    m = np.where(ok, np.nan_to_num(mean), 0.0)
    # The same error bars, joint across factors (DESIGN.md 4.6). The windows' noise moved the factors together as
    # their daily moves did around the analogs (stocks and bonds have moved together in some decades and apart in
    # others); the floor, for how today differs from then, moves them as they move now; the size error is half one
    # error for the whole jump and half factor by factor (``covariance.size_cov``); the spread between analogs stays
    # factor by factor. Each factor's own error bar is unchanged.
    last = until or fd.dates[-1]
    upto = np.array([d <= last for d in fd.dates], dtype=bool)
    era = upto & np.array([any(abs((d - a.date).days) <= ERA for a in used) for d in fd.dates], dtype=bool)
    now = upto & np.array([(last - d).days <= NOW for d in fd.dates], dtype=bool)
    R_then = moves_corr(fd.Z[era] if era.sum() >= 60 else fd.Z[upto])
    R_now = moves_corr(fd.Z[now])
    sd_noise = np.sqrt(np.where(ok, s2, 0.0))
    floor = np.where(ok, MODEL_FLOOR, 0.0)
    cov = (R_then * np.outer(sd_noise, sd_noise) + R_now * np.outer(floor, floor)
           + np.diag(np.where(ok, spread, 0.0)) + size_cov(m, MODEL_SHARE))
    return DeltaReading(kind="past", name="Past events", detail=detail, delta=np.nan_to_num(mean), sigma=sigma, n=len(used),
                        cov=cov)
