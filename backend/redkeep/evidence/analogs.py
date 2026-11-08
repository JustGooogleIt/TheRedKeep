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

import numpy as np

from ..engine.fusion import DeltaReading
from ..events import EventSpec
from ..factordata import FactorData, window_bounds
from ..factors import NF

MODEL_FLOOR = 0.3
MODEL_SHARE = 0.5  # of the measured move: a past event's move could be half again as large or small


def analog_reading(spec: EventSpec, fd: FactorData) -> DeltaReading | None:
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
    return DeltaReading(kind="past", name="Past events", detail=detail, delta=np.nan_to_num(mean), sigma=sigma, n=len(used))
