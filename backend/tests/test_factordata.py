"""Factor data when the series cover different stretches, as on Massive's free plan. Synthetic data only."""

from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

from redkeep import factordata
from redkeep.events import Analog
from redkeep.evidence import analogs
from redkeep.exposure import fit_line, returns_on
from redkeep.factors import index

from .synth import business_days, factor_levels, fund_closes

DAYS = business_days(date(2023, 1, 2), date(2026, 11, 6))
LATE = date(2024, 12, 2)  # Massive's prices begin: about two years, on the free plan
STALE = date(2026, 10, 30)  # FRED's dollar index ends: it's published about a week behind
HOLIDAY = date(2025, 10, 13)  # the bond market is closed, the stock market isn't
EQ = index("eq")


@pytest.fixture(scope="module")
def lv():
    out = factor_levels(DAYS)
    for t in ("SPY", "FXI", "SMH", "GLD"):
        out[t] = {d: v for d, v in out[t].items() if d >= LATE}
    out["DTWEXBGS"] = {d: v for d, v in out["DTWEXBGS"].items() if d <= STALE}
    for k in ("DGS10", "T10YIE", "BAMLH0A0HYM2"):
        del out[k][HOLIDAY]
    return out


@pytest.fixture(scope="module")
def fd(lv):
    return factordata.build(lv)


def pct(s, a, b):
    return (s[b] / s[a] - 1) * 100


def prev(d):
    return DAYS[DAYS.index(d) - 1]


def weekly_sd(s, lo, hi):
    """A typical move by hand: the standard deviation of one series' non-overlapping five-close changes."""
    ds = sorted(d for d in s if lo <= d <= hi)
    return np.std([pct(s, ds[t - 5], ds[t]) for t in range(5, len(ds), 5)], ddof=1)


def test_a_series_that_starts_late_or_ends_early_does_not_cut_the_calendar(fd):
    assert fd.calendar() == DAYS
    assert fd.dates == DAYS[1:]
    assert fd.available.all()
    assert fd.sources["eq"] == "Massive SPY" and fd.sources["chips"] == "Massive SMH vs Massive SPY"


def test_each_day_uses_the_first_proxy_with_both_closes(fd, lv):
    at = {d: t for t, d in enumerate(fd.dates)}
    # Before SPY's prices, and on their first day (no close the day before), stocks are the S&P 500 index.
    for d in (date(2024, 6, 3), LATE):
        assert fd.X[at[d], EQ] == pytest.approx(pct(lv["SP500"], prev(d), d))
        assert np.isnan(fd.X[at[d], index("china")]) and np.isnan(fd.X[at[d], index("chips")])
    d = date(2025, 6, 2)
    assert fd.X[at[d], EQ] == pytest.approx(pct(lv["SPY"], prev(d), d))
    # After the dollar index's last close, only the dollar is missing.
    after = [at[d] for d in DAYS if d > STALE]
    usd = index("usd")
    assert np.isnan(fd.X[after, usd]).all() and np.isfinite(np.delete(fd.X[after], usd, axis=1)).all()
    # A bond-market holiday costs the bond series that day and the next, and no other factor.
    hit = [at[HOLIDAY], at[HOLIDAY] + 1]
    for k in ("rates", "infl", "credit"):
        assert np.isnan(fd.X[hit, index(k)]).all()
    assert np.isfinite(fd.X[hit, EQ]).all() and np.isfinite(fd.X[hit, usd]).all()


def test_typical_moves_come_from_one_proxy_that_covers_the_window(fd, lv):
    window = DAYS[-520:]
    # SPY covers 97% of the last 520 days, so it measures stocks' typical move, as it would on a paid plan.
    assert fd.typical[EQ] == pytest.approx(weekly_sd(lv["SPY"], window[0], window[-1]))
    # Measured before April 2025 (the replay), SPY covers a sixth of the window, so the S&P 500 index measures it.
    cut = date(2025, 3, 31)
    typical = factordata.build({k: {d: v for d, v in s.items() if d <= cut} for k, s in lv.items()}).typical
    window = [d for d in DAYS if d <= cut][-520:]
    assert typical[EQ] == pytest.approx(weekly_sd(lv["SP500"], window[0], window[-1]))
    # A factor with one proxy uses the stretch it has: FXI's seventeen weeks.
    assert typical[index("china")] == pytest.approx(weekly_sd(lv["FXI"], window[0], window[-1]))
    assert np.isfinite(typical).all()


def test_moves_and_past_events_before_spys_prices_use_the_index(fd, lv):
    a, b = date(2024, 4, 2), date(2024, 4, 5)
    mv = fd.move(a, b)
    assert mv[EQ] == pytest.approx(pct(lv["SP500"], a, b) / fd.typical[EQ])
    assert np.isnan(mv[index("china")]) and np.isfinite(mv[index("rates")])
    a, b = date(2025, 4, 2), date(2025, 4, 4)
    assert fd.move(a, b)[EQ] == pytest.approx(pct(lv["SPY"], a, b) / fd.typical[EQ])
    # A past event from before SPY's prices still reads stocks, and leaves China out.
    d0 = date(2024, 4, 3)
    r = analogs.analog_reading(SimpleNamespace(analogs=(Analog(d0, 1, 0.5, "test"),)), fd)
    assert r is not None and r.n == 1
    assert r.delta[EQ] == pytest.approx(pct(lv["SP500"], prev(d0), d0) / fd.typical[EQ] / 0.5)
    assert np.isinf(r.sigma[index("china")])


def test_exposures_fit_on_the_days_every_factor_has(fd):
    b_true = np.array([2.0, -0.8, 0.3, 0.0, 0.5, -0.4, 0.0, 0.6, 1.2, 0.0])
    y = returns_on(fund_closes(fd, b_true), fd.dates)
    _, b, r2_oos, n, ok = fit_line(y, fd)
    assert n == ok.sum() == np.isfinite(fd.X).all(axis=1).sum() > 400
    assert np.abs(b - b_true).max() < 0.12 and r2_oos > 0.9


def test_without_a_stock_proxy_the_days_are_the_ones_every_series_shares(lv):
    fd = factordata.build({k: s for k, s in lv.items() if k not in ("SPY", "SP500")})
    assert fd.dates[0] == DAYS[DAYS.index(LATE) + 1] and fd.dates[-1] == STALE and HOLIDAY not in fd.dates
    assert np.isfinite(fd.X[:, fd.available]).all()
    assert not fd.available[EQ] and not fd.available[index("chips")]
