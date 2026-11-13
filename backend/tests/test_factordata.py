"""Factor data when the series cover different stretches, as on Massive's free plan. Synthetic data only."""

from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

from redkeep import factordata
from redkeep.events import Analog
from redkeep.evidence import analogs
from redkeep.exposure import fit_line, returns_on
from redkeep.factors import NF, index

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
    """A typical move by hand: one series' non-overlapping five-close changes, the variance averaged over the five
    days the weeks can start on."""
    ds = sorted(d for d in s if lo <= d <= hi)
    var = [np.var([pct(s, ds[t - 5], ds[t]) for t in range(5 + k, len(ds), 5)], ddof=1) for k in range(5)]
    return np.sqrt(np.mean(var))


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


def test_the_typical_move_does_not_depend_on_the_day_the_weeks_start(lv):
    # A 15% drop undone the next day: a week that holds both days nets it out, so one grid of weeks gives a typical
    # move that swings with the day the weeks start (by 38% here). Averaged over the five start days, it barely moves.
    days = DAYS[-530:]
    sp = {d: lv["SP500"][d] for d in days}
    for d in days[300:]:
        sp[d] *= 0.85
    for d in days[301:]:
        sp[d] /= 0.85
    typical = [factordata.build({"SP500": {d: v for d, v in sp.items() if d <= days[-1 - k]}}).typical[EQ] for k in range(5)]
    assert max(typical) / min(typical) < 1.03


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


# -- a series fixed before the close (the Fed's dollar index, at noon) ---------------------------------------------------


def carry_world(seed: int = 3, beta: float = 0.6, n: int = 400):
    """A dollar fund whose day splits at noon, and a broad index that moves ``beta`` times the fund plus its own basket
    moves (which come in the morning), fixed at noon. Its true 4 p.m. level is known."""
    rng = np.random.default_rng(seed)
    ds = business_days(date(2024, 1, 1), date(2026, 12, 31))[:n]
    morning, afternoon, basket = rng.normal(0, 0.003, n), rng.normal(0, 0.002, n), rng.normal(0, 0.002, n)
    f_noon = np.log(28) + np.cumsum(morning) + np.concatenate([[0], np.cumsum(afternoon)[:-1]])
    f_close = f_noon + afternoon
    e = np.cumsum(basket)
    index_noon = {d: float(np.exp(np.log(120) + beta * fn + b)) for d, fn, b in zip(ds, f_noon, e)}
    index_close = {d: float(np.exp(np.log(120) + beta * fc + b)) for d, fc, b in zip(ds, f_close, e)}
    fund_close = {d: float(np.exp(v)) for d, v in zip(ds, f_close)}
    fund_noon = {d: float(np.exp(v)) for d, v in zip(ds, f_noon)}
    return ds, index_noon, index_close, fund_close, fund_noon


def test_carrying_a_noon_fixing_to_the_close_recovers_the_close():
    ds, index_noon, index_close, fund_close, fund_noon = carry_world()
    carried, beta = factordata.carry_to_close(index_noon, fund_close, fund_noon)
    assert beta == pytest.approx(0.6, abs=0.05)  # measured from noon to noon, where the two share a clock
    true = np.diff(np.log([index_close[d] for d in ds]))
    got = np.diff(np.log([carried[d] for d in ds]))
    noon = np.diff(np.log([index_noon[d] for d in ds]))
    assert np.corrcoef(true, got)[0, 1] > 0.99
    assert np.corrcoef(true, noon)[0, 1] < 0.95  # the fixing alone misses the afternoon


def test_carrying_keeps_the_history_before_the_fund_and_drops_its_gaps_after():
    ds, index_noon, _, fund_close, fund_noon = carry_world()
    early = {d: 100.0 for d in business_days(date(2023, 6, 1), date(2023, 12, 29))}
    gap = ds[200]
    fixing = {d: v for d, v in fund_noon.items() if d != gap}
    carried, _ = factordata.carry_to_close({**early, **index_noon}, fund_close, fixing)
    assert all(carried[d] == 100.0 for d in early)  # before the fund's noon prices begin: as fixed
    assert gap not in carried  # after: a day without them is left out rather than mixing the clocks
    assert factordata.carry_to_close(index_noon, fund_close, dict(list(fund_noon.items())[:50])) is None  # too few days


class CarryFred:
    def __init__(self, levels):
        self.levels = levels

    def series(self, ids, start, end):
        return {i: {d: v for d, v in self.levels[i].items() if start <= d <= end} for i in ids if i in self.levels}


class CarryMassive:
    available = True

    def __init__(self, levels, noon, fail=False):
        self.levels, self.noon, self.fail = levels, noon, fail

    def closes(self, t, start, end):
        return {d: v for d, v in self.levels.get(t, {}).items() if start <= d <= end}

    def at_hour(self, t, start, end, *, hour=12):
        if self.fail:
            from redkeep.http import HttpError
            raise HttpError("https://api.massive.com/v2/aggs", 403, "not in the plan")
        return {d: v for d, v in self.noon.items() if start <= d <= end}


def test_load_carries_the_dollar_and_says_so_or_leaves_it_at_noon():
    ds, index_noon, index_close, fund_close, fund_noon = carry_world(n=600)
    lv = factor_levels(ds)
    lv["DTWEXBGS"] = index_noon
    fred = CarryFred({k: v for k, v in lv.items() if k in {"SP500", "DGS10", "T10YIE", "DTWEXBGS", "DCOILWTICO", "BAMLH0A0HYM2", "VIXCLS"}})
    massive = CarryMassive({**{t: lv[t] for t in ("SPY", "FXI", "SMH", "GLD")}, "UUP": fund_close}, fund_noon)
    fd = factordata.load(fred, massive, ds[0], ds[-1])
    usd = index("usd")
    assert "carried from its 12:00 fixing to the 4 p.m. close with Massive UUP" in fd.sources["usd"]
    true = np.diff(np.log([index_close[d] for d in ds]))
    assert np.corrcoef(fd.X[:, usd], true * 100)[0, 1] > 0.99
    plain = factordata.load(fred, CarryMassive(massive.levels, fund_noon, fail=True), ds[0], ds[-1])
    assert plain.sources["usd"] == "FRED DTWEXBGS"
    v = np.array([index_noon[d] for d in ds])
    assert np.allclose(plain.X[:, usd], (v[1:] / v[:-1] - 1) * 100)


def test_two_factors_share_the_noise_of_only_the_past_events_both_read(fd, monkeypatch):
    """Stocks are read on both past events, China only on the later one (FXI's prices start after the first): the
    two averages share that one event's noise, ``Σ_both (1/v_a) / (tot_f · tot_g)``, not their whole error bars."""
    R = np.full((NF, NF), 0.5)
    np.fill_diagonal(R, 1.0)
    monkeypatch.setattr(analogs, "moves_corr", lambda Z: R)
    monkeypatch.setattr(analogs, "MODEL_FLOOR", 0.0)  # leave the analogs' own noise and spread alone
    monkeypatch.setattr(analogs, "MODEL_SHARE", 0.0)
    first, later = Analog(date(2024, 4, 3), 1, 0.5, "before FXI's prices"), Analog(date(2025, 4, 3), 1, 0.8, "after")
    r = analogs.analog_reading(SimpleNamespace(analogs=(first, later)), fd)
    CN = index("china")
    v1, v2 = 0.2 / 0.5 ** 2, 0.2 / 0.8 ** 2  # each one-day window's noise, per unit of surprise
    tot_eq, tot_cn = 1 / v1 + 1 / v2, 1 / v2
    assert r.cov[EQ, CN] == pytest.approx(0.5 * (1 / v2) / (tot_eq * tot_cn))
    assert r.cov[EQ, CN] < 0.5 * np.sqrt((1 / tot_eq) * (1 / tot_cn))  # what taking the error bars whole would give
    ok = np.isfinite(r.sigma)
    assert np.allclose(np.diag(r.cov)[ok], r.sigma[ok] ** 2)
