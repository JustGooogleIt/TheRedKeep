"""The exposure engine: from holdings to the portfolio's arrow ``b`` (DESIGN.md 6.1).

For each line the portfolio holds, regress its daily returns on the factors'
daily moves (in typical moves) over about two years, weighting recent days
more (half-life ~6 months) and shrinking noisy coefficients toward zero
(ridge). ``b`` is then in "% gain or loss per typical move" of each factor, and
the portfolio's arrow is the value-weighted sum.

Look-through: funds are expanded into their largest holdings using ETF
Global's constituents (via Massive), so overlaps show up (NVIDIA held directly
and inside QQQ) and concentration is visible. The rest of each fund stays as
one "other holdings" line. When the key doesn't include holdings data, the fund
is shown as one line (DESIGN.md section 12). In this slice a fund's exposure
comes from the fund's own returns; per-constituent regressions are the next
step and give the same arrow for an unchanged fund.

Fit score: the share of the portfolio's daily movement the factors explain
over the last six months, using coefficients fitted only on the earlier data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from .data.massive import Massive
from .factordata import FactorData
from .factors import NF
from .http import HttpError

log = logging.getLogger(__name__)

HALF_LIFE = 126  # trading days
RIDGE = 1.0
TEST_DAYS = 126  # held out for the fit score
MIN_OBS = 120


@dataclass
class Line:
    ticker: str
    value: float
    weight: float
    b: np.ndarray
    alpha: float
    r2_oos: float | None
    n_obs: int
    holdings: list[tuple[str, str, float]] | None = None  # (ticker, name, weight in the fund)
    holdings_date: date | None = None
    name: str = ""
    kind: str = ""  # Massive security type: "CS" for a stock, "ETF", ...
    note: str = ""
    price: float | None = None  # last close, which values a holding given in shares
    price_date: date | None = None
    n_holdings: int = 0  # the fund's full count of holdings, of which ``holdings`` are the largest


@dataclass
class PortfolioExposure:
    value: float
    b: np.ndarray
    fit: float | None
    lines: list[Line]
    look: list[dict]  # look-through, in portfolio weights
    typical: np.ndarray
    as_of: date
    notes: list[str] = field(default_factory=list)


def weights_for(n: int, half_life: float = HALF_LIFE) -> np.ndarray:
    w = 0.5 ** (np.arange(n)[::-1] / half_life)
    return w * n / w.sum()


def ridge_fit(y: np.ndarray, Z: np.ndarray, w: np.ndarray, lam: float = RIDGE) -> tuple[float, np.ndarray]:
    """Weighted ridge with an unpenalised intercept. Returns (alpha, b)."""
    X = np.column_stack([np.ones(len(y)), Z])
    W = w[:, None]
    A = X.T @ (W * X)
    A[1:, 1:] += lam * np.eye(Z.shape[1])
    coef = np.linalg.solve(A, X.T @ (w * y))
    return float(coef[0]), coef[1:]


def r2(y: np.ndarray, pred: np.ndarray) -> float:
    sst = float(((y - y.mean()) ** 2).sum())
    return 1 - float(((y - pred) ** 2).sum()) / sst if sst > 0 else float("nan")


def returns_on(closes: dict[date, float], dates: list[date]) -> np.ndarray:
    """Daily % returns on the factor dates (NaN where a close is missing)."""
    out = np.full(len(dates), np.nan)
    srt = sorted(closes)
    prev_of = {d: srt[i - 1] for i, d in enumerate(srt) if i}
    for t, d in enumerate(dates):
        p = prev_of.get(d)
        if p is not None and closes[p] > 0 and (d - p).days <= 5:
            out[t] = (closes[d] / closes[p] - 1) * 100
    return out


def fit_line(y: np.ndarray, fd: FactorData) -> tuple[float, np.ndarray, float | None, int, np.ndarray]:
    """Coefficients on all usable days, plus the held-out R² and the in-sample mask."""
    avail = fd.available
    Z = fd.Z[:, avail]
    ok = np.isfinite(y) & np.isfinite(Z).all(axis=1)
    n = int(ok.sum())
    b = np.zeros(NF)
    if n < MIN_OBS:
        return 0.0, b, None, n, ok
    idx = np.flatnonzero(ok)
    a, coef = ridge_fit(y[idx], Z[idx], weights_for(len(idx)))
    b[avail] = coef
    r2_oos = None
    if len(idx) > TEST_DAYS + MIN_OBS:
        tr, te = idx[:-TEST_DAYS], idx[-TEST_DAYS:]
        a0, c0 = ridge_fit(y[tr], Z[tr], weights_for(len(tr)))
        r2_oos = r2(y[te], a0 + Z[te] @ c0)
    return a, b, r2_oos, n, ok


def estimate(
    holdings: list[tuple[str, float, str]],
    fd: FactorData,
    massive: Massive,
    *,
    as_of: date,
    top_n: int = 25,
    cover: float = 0.6,
) -> PortfolioExposure:
    """``holdings`` are (ticker, amount, unit) with unit "usd" or "shares"."""
    start = fd.dates[0] - timedelta(days=10)
    notes: list[str] = []
    closes: dict[str, dict[date, float]] = {}
    values: dict[str, float] = {}
    for t, amount, unit in holdings:
        if t not in closes:
            closes[t] = massive.closes(t, start, as_of)
        if not closes[t]:
            raise ValueError(f"no price history for {t}")
        last = closes[t][max(closes[t])]
        values[t] = values.get(t, 0.0) + (amount if unit == "usd" else amount * last)
    V = sum(values.values())

    lines: list[Line] = []
    preds = []
    for t, v in values.items():
        y = returns_on(closes[t], fd.dates)
        a, b, r2_oos, n, ok = fit_line(y, fd)
        info = massive.details(t)
        day = max(closes[t])
        line = Line(t, v, v / V, b, a, r2_oos, n, name=info.get("name") or t, kind=info.get("type") or "",
                    price=closes[t][day], price_date=day)
        if n < MIN_OBS:
            line.note = f"Only {n} days of prices, too few to estimate; treated as having no factor exposure."
            notes.append(f"{t}: {line.note}")
        try:
            got = massive.constituents(t, as_of)
        except HttpError as e:
            got = None
            log.warning("holdings for %s unavailable: %s", t, e)
        if got:
            eff, rows = got
            top, covered = [], 0.0
            for r in sorted(rows, key=lambda r: -(r.get("weight") or 0)):
                w = float(r.get("weight") or 0)
                if w <= 0 or len(top) >= top_n or covered >= cover:
                    break
                top.append((r.get("constituent_ticker") or r.get("constituent_name") or "?", r.get("constituent_name") or "", w))
                covered += w
            line.holdings, line.holdings_date, line.n_holdings = top, eff, len(rows)
        lines.append(line)
        preds.append((line, y))

    b = sum(l.weight * l.b for l in lines)

    # Portfolio fit over the last TEST_DAYS days, out of sample.
    fit = None
    avail = fd.available
    Z = fd.Z[:, avail]
    if len(fd.dates) > TEST_DAYS + MIN_OBS:
        te = np.arange(len(fd.dates) - TEST_DAYS, len(fd.dates))
        tr = np.arange(0, len(fd.dates) - TEST_DAYS)
        yp, pp = np.zeros(TEST_DAYS), np.zeros(TEST_DAYS)
        good = np.isfinite(Z[te]).all(axis=1)
        for line, y in preds:
            ok_tr = tr[np.isfinite(y[tr]) & np.isfinite(Z[tr]).all(axis=1)]
            if len(ok_tr) < MIN_OBS:
                continue
            a0, c0 = ridge_fit(y[ok_tr], Z[ok_tr], weights_for(len(ok_tr)))
            yt = np.nan_to_num(y[te])
            good &= np.isfinite(y[te])
            yp += line.weight * yt
            pp += line.weight * (a0 + np.nan_to_num(Z[te]) @ c0)
        if good.sum() > 30:
            fit = r2(yp[good], pp[good])

    # Look-through, aggregated across lines.
    look: dict[str, dict] = {}

    def add(key, name, w, via, kind):
        o = look.setdefault(key, {"key": key, "name": name, "weight": 0.0, "via": [], "kind": kind})
        o["weight"] += w
        o["via"].append([via, w])

    for l in lines:
        if l.holdings:
            covered = 0.0
            for tk, name, w in l.holdings:
                add(tk, name or tk, l.weight * w, l.ticker, "stock")
                covered += w
            add(f"rest:{l.ticker}", f"Other {l.ticker} holdings", l.weight * max(0.0, 1 - covered), l.ticker, "rest")
        elif l.kind == "CS":
            add(l.ticker, l.name, l.weight, "direct", "stock")
        else:
            add(f"line:{l.ticker}", l.name or l.ticker, l.weight, l.ticker, "line")
    return PortfolioExposure(V, b, fit, lines, sorted(look.values(), key=lambda o: -o["weight"]), fd.typical, as_of, notes)
