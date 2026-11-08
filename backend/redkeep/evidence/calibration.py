"""Calibration: do events priced at 70% happen about 70% of the time? (DESIGN.md 6.4, eval E2)

Prediction markets overprice long shots and underprice favorites (Snowberg and Wolfers 2010). Kalshi does too, and its
prices get more accurate as a market nears its close (Bürgi, Deng and Whelan 2025). The engine measures the bias on
resolved Polymarket and Kalshi markets and turns market odds ``q`` into calibrated odds ``p̂``. Priced-in moves keep
using ``q``, since that is what the market has priced; ``p̂`` is the engine's own view, used by the calibrated
simulation and a hedge's expected cost.

The correction is linear in log-odds, with one slope per horizon (days left until the market's deadline):

    logit(p̂) = b_h · logit(q)

b > 1 means long shots are overpriced and favorites underpriced. With no intercept, yes and no are treated alike
(``p̂(1 − q) = 1 − p̂(q)``), so the correction doesn't depend on how a question is worded. The intercept of a
two-parameter fit is reported as a check for a bias toward yes. One parameter per horizon can't chase noise the way
a binned curve can, and the reliability tables show whether the line fits.

Each resolved yes/no market gives one observation per horizon: its price h days before its deadline, if it was still
trading then, and whether it resolved yes. "Still trading" is the right condition, because the engine only prices
open markets.

E2's ground rules: markets are split by the date they closed. The slope is fitted on the earliest 70% and scored on
the latest 30% against raw prices, by Brier score, log loss and a reliability table. A horizon's correction is used
only if it lowers the out-of-sample log loss, and the slopes in use are then refitted on every market. The error
bars on the gain come from resampling whole events, since the outcomes of one event's markets are tied together.
"""

from __future__ import annotations

import gzip
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Sequence

import numpy as np

EPS = 0.01  # the fit reads prices inside [1%, 99%], so a 0.1-cent contract that wins can't decide it
HORIZONS = (1, 7, 30, 90, 180)  # days before the deadline


def logit(p, eps: float = EPS):
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    return np.log(p / (1 - p))


def expit(x):
    return 1 / (1 + np.exp(-np.asarray(x, dtype=float)))


@dataclass(frozen=True)
class Obs:
    """One resolved market seen h days before its deadline."""
    venue: str
    market: str  # venue:id
    event: str  # venue:event id; markets of one event are resampled together
    category: str
    closed: date  # when the market closed, for the time split
    h: int  # days before the deadline
    q: float  # price of yes then
    y: int  # 1 if it resolved yes


@dataclass(frozen=True)
class Line:
    """``logit(p̂) = a + b · logit(q)``, with standard errors."""
    a: float
    b: float
    se_a: float
    se_b: float
    n: int

    def p(self, q):
        return expit(self.a + self.b * logit(q))


def fit_line(q: Sequence[float], y: Sequence[int], *, intercept: bool = False, ridge: float = 1.0) -> Line:
    """Maximum likelihood by Newton's method, with a small pull toward raw prices (a = 0, b = 1), so that a thin
    sample stays near them and a sample a line separates perfectly still gives a finite fit."""
    x = logit(q)
    yy = np.asarray(y, dtype=float)
    X = np.column_stack([np.ones_like(x), x]) if intercept else x[:, None]
    center = np.array([0.0, 1.0]) if intercept else np.array([1.0])
    th = center.copy()

    def hessian(t):
        p = expit(X @ t)
        return (X * (p * (1 - p))[:, None]).T @ X + ridge * np.eye(len(t)), p

    for _ in range(100):
        H, p = hessian(th)
        step = np.linalg.solve(H, X.T @ (yy - p) - ridge * (th - center))
        th = th + step
        if np.abs(step).max() < 1e-10:
            break
    se = np.sqrt(np.diag(np.linalg.inv(hessian(th)[0])))
    if intercept:
        return Line(float(th[0]), float(th[1]), float(se[0]), float(se[1]), len(yy))
    return Line(0.0, float(th[0]), 0.0, float(se[0]), len(yy))


def losses(p, y) -> np.ndarray:
    """Each observation's log loss."""
    p = np.clip(np.asarray(p, dtype=float), 1e-9, 1 - 1e-9)
    yy = np.asarray(y, dtype=float)
    return -(yy * np.log(p) + (1 - yy) * np.log(1 - p))


def scores(p, y) -> dict:
    p, yy = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    return {"n": len(yy), "brier": float(np.mean((p - yy) ** 2)), "log_loss": float(np.mean(losses(p, yy)))}


def reliability(p, y, bins: int = 10) -> list[dict]:
    """Forecasts in equal-width bins: how many, the average forecast, and how often they came true."""
    p, yy = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    out = []
    for i in range(bins):
        m = idx == i
        if m.any():
            out.append({"lo": round(float(edges[i]), 2), "hi": round(float(edges[i + 1]), 2), "n": int(m.sum()),
                        "p": float(p[m].mean()), "freq": float(yy[m].mean())})
    return out


def gain_interval(raw, corrected, y, groups: Sequence[str], *, n: int = 1000, seed: int = 0) -> tuple[float, float, float]:
    """The correction's gain in log loss over raw prices (positive is better), with a 95% interval from resampling
    whole groups (events)."""
    d = losses(raw, y) - losses(corrected, y)
    keys = sorted(set(groups))
    pos = {k: i for i, k in enumerate(keys)}
    g = np.array([pos[k] for k in groups])
    sums, counts = np.bincount(g, weights=d, minlength=len(keys)), np.bincount(g, minlength=len(keys))
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(keys), size=(n, len(keys)))
    boot = sums[draws].sum(axis=1) / np.maximum(counts[draws].sum(axis=1), 1)
    return float(d.mean()), float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))


def study(obs: Sequence[Obs], *, split: float = 0.7, min_train: int = 100, min_test: int = 50, boot: int = 1000,
          seed: int = 0) -> dict:
    """Fit on the earliest markets, score on the latest, horizon by horizon (E2); then refit what is used on all."""
    closed = sorted({(o.closed, o.market) for o in obs})
    if not closed:
        raise ValueError("no resolved markets to calibrate on")
    cutoff = closed[min(len(closed) - 1, int(len(closed) * split))][0]
    by_venue = Counter(o.venue for o in {o.market: o for o in obs}.values())
    report = {
        "model": "logit(p̂) = b · logit(q), one slope per horizon (days before the deadline)",
        "markets": dict(by_venue), "events": len({o.event for o in obs}), "observations": len(obs),
        "closed": [closed[0][0].isoformat(), closed[-1][0].isoformat()], "split": cutoff.isoformat(),
        "horizons": [],
    }
    for h in HORIZONS:
        at = [o for o in obs if o.h == h]
        tr = [o for o in at if o.closed < cutoff]
        te = [o for o in at if o.closed >= cutoff]
        row: dict = {"days": h, "train": len(tr), "test": len(te), "used": False, "b": 1.0, "se": None}
        report["horizons"].append(row)
        if len(tr) < min_train or len(te) < min_test:
            row["note"] = f"too few markets (needs {min_train} to fit and {min_test} to test)"
            continue
        line = fit_line([o.q for o in tr], [o.y for o in tr])
        q, y = np.array([o.q for o in te]), np.array([o.y for o in te])
        raw, cor = np.clip(q, EPS, 1 - EPS), line.p(q)
        gain = gain_interval(raw, cor, y, [o.event for o in te], n=boot, seed=seed)
        row.update({
            "fit_b": line.b, "fit_se": line.se_b,
            "raw": scores(raw, y), "corrected": scores(cor, y), "gain": gain,
            "reliability": {"raw": reliability(raw, y), "corrected": reliability(cor, y)},
            "by_venue": _groups(te, line, lambda o: o.venue), "by_category": _groups(te, line, lambda o: o.category),
        })
        yes = fit_line([o.q for o in at], [o.y for o in at], intercept=True)
        row["yes_bias"] = {"a": yes.a, "se": yes.se_a}
        if row["corrected"]["log_loss"] < row["raw"]["log_loss"]:
            final = fit_line([o.q for o in at], [o.y for o in at])
            row.update(used=True, b=final.b, se=final.se_b)
        else:
            row["note"] = "raw prices scored as well out of sample, so they stay"
    return report


def _groups(te: Sequence[Obs], line: Line, key) -> dict:
    out = {}
    for k in sorted({key(o) for o in te}):
        g = [o for o in te if key(o) == k]
        q, y = np.array([o.q for o in g]), np.array([o.y for o in g])
        out[k] = {"n": len(g), "raw": scores(np.clip(q, EPS, 1 - EPS), y)["log_loss"], "corrected": scores(line.p(q), y)["log_loss"]}
    return out


@dataclass(frozen=True)
class Calibration:
    """The slopes in use, by horizon; 1 where raw prices stay. Between horizons the slope is interpolated in log days."""
    slopes: dict[int, float]
    fitted: str = ""
    markets: int = 0

    def slope(self, days: float) -> float:
        hs = sorted(self.slopes)
        return float(np.interp(math.log(max(days, 1e-3)), [math.log(h) for h in hs], [self.slopes[h] for h in hs]))

    def p_hat(self, q: float, days: float) -> float:
        """Calibrated odds for market odds ``q`` with ``days`` left. With slope 1 it returns ``q`` itself."""
        b = self.slope(days)
        if b == 1.0 or q <= 0.0 or q >= 1.0:
            return float(q)
        return float(expit(b * math.log(q / (1 - q))))

    def summary(self) -> dict:
        return {"fitted": self.fitted, "markets": self.markets, "slopes": {str(h): round(b, 4) for h, b in sorted(self.slopes.items())}}


IDENTITY = Calibration({h: 1.0 for h in HORIZONS})


def write_obs(obs: Sequence[Obs], path: Path) -> None:
    """The observations behind a study, one JSON line each, so it can be rerun without fetching (``--from``)."""
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for o in obs:
            f.write(json.dumps({**asdict(o), "closed": o.closed.isoformat()}) + "\n")


def read_obs(path: Path) -> list[Obs]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [Obs(**{**d, "closed": date.fromisoformat(d["closed"])}) for d in map(json.loads, f) if d]


def to_json(report: dict, fitted: str) -> dict:
    """The slopes in use, with the evidence behind them, as kept in data/calibration.json."""
    return {
        "kind": "redkeep.calibration", "version": 1, "fitted": fitted, "model": report["model"],
        "markets": report["markets"], "events": report["events"], "closed": report["closed"], "split": report["split"],
        "horizons": [{k: r.get(k) for k in ("days", "used", "b", "se", "train", "test", "raw", "corrected", "gain", "note")
                      if r.get(k) is not None} for r in report["horizons"]],
    }


def load(path: Path) -> Calibration:
    """The fitted slopes, or raw prices (every slope 1) when there is no study yet."""
    if not path.exists():
        return IDENTITY
    d = json.loads(path.read_text())
    if d.get("kind") != "redkeep.calibration":
        raise ValueError(f"{path} is not a calibration file")
    return Calibration({int(r["days"]): float(r["b"]) if r.get("used") else 1.0 for r in d["horizons"]},
                       d.get("fitted", ""), sum(d.get("markets", {}).values()))
