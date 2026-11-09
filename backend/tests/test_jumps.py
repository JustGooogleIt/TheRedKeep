"""Jump accuracy on resolved markets (evals E3 to E5), offline: synthetic markets whose odds jumps move the factors by
planted amounts, served the way Polymarket and Kalshi serve closed markets. Nothing here is market data."""

import json
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pytest

from redkeep import cli, factordata, jumps
from redkeep.config import Settings
from redkeep.evidence.resolved import Resolved
from redkeep.factors import FACTORS, NF, index
from redkeep.pipeline import Clients

from .synth import business_days, factor_levels

DAYS = business_days(date(2017, 12, 1), date(2026, 3, 31))
START, END = date(2025, 1, 1), date(2025, 12, 31)
PROXY = {"eq": "SP500", "rates": "DGS10", "infl": "T10YIE", "usd": "DTWEXBGS", "oil": "DCOILWTICO",
         "credit": "BAMLH0A0HYM2", "vix": "VIXCLS"}
# The planted jumps, in typical moves: even markets move the factors, odd ones don't.
PLANTED = [{"eq": -3.0, "oil": -1.5}, {}, {"rates": 2.5, "usd": 1.0}, {}]
N = 16


def plant(levels, typical, day, delta, dq):
    for key, d in delta.items():
        f = FACTORS[index(key)]
        move = d * dq * typical[index(key)]
        s = levels[PROXY[key]]
        for t in s:
            if t >= day:
                s[t] = s[t] + move / f.scale if f.transform == "diff" else s[t] * (1 + move / 100)


def build_world(seed: int = 7):
    """Factor levels, and for each market its odds at each trading day, its resolution day and outcome."""
    levels = factor_levels(DAYS, seed=seed)
    typical = factordata.build({k: {d: v for d, v in s.items() if d < START} for k, s in levels.items()}).typical
    rng = np.random.default_rng(seed)
    t25 = [d for d in DAYS if date(2025, 1, 2) <= d <= END]
    res_days = [d for d in t25 if d >= date(2025, 7, 15)][::6][:N]
    used = set(res_days)
    markets = []
    for i, R in enumerate(res_days):
        opened = R - timedelta(days=200)
        life = [d for d in t25 if opened <= d < R]
        pool = [d for d in life if d not in used and d >= R - timedelta(days=175)]
        jdays = sorted(rng.choice(len(pool), 8, replace=False))
        jdays = [pool[j] for j in jdays]
        used.update(jdays)
        q, path, sign = 0.5, {}, 1
        for d in life:
            q += rng.normal(0, 0.003)
            if d in jdays:
                q += sign * rng.uniform(0.12, 0.2)
                sign = -sign
            q = float(np.clip(q, 0.3, 0.7))
            path[d] = q
        y = int(path[life[-1]] < 0.5)  # a surprise either way: at least 30 points
        delta = PLANTED[i % 4]
        prev = None
        for d in life:
            if prev is not None and d in jdays:
                plant(levels, typical, d, delta, path[d] - path[prev])
            prev = d
        plant(levels, typical, R, delta, y - path[life[-1]])
        path[R] = 0.99 if y else 0.01
        for d in [d for d in DAYS if R < d <= R + timedelta(days=3)]:
            path[d] = path[R]
        markets.append({"i": i, "venue": "kalshi" if i % 3 == 0 else "polymarket", "R": R, "y": y, "opened": opened,
                        "path": path, "delta": delta})
    # A second market of market 1's event follows its odds halfway, so it resolves with a smaller surprise.
    m1 = markets[1]
    markets.append({**m1, "i": 100, "path": {d: 0.5 + 0.5 * (q - 0.5) for d, q in m1["path"].items()}, "twin": True})
    return levels, markets


def hourly(path):
    """Prices every hour from 14:00 to 20:00 UTC, before the 4 p.m. close all year."""
    return [(datetime.combine(d, time(h), timezone.utc), q) for d, q in sorted(path.items()) for h in range(14, 21)]


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def closed_at(m) -> datetime:
    return datetime.combine(m["R"] + timedelta(days=1), time(1), timezone.utc)


class PastPolymarket:
    def __init__(self, markets):
        self.ms = {f"tok-{m['i']}": m for m in markets if m["venue"] == "polymarket"}

    def closed_events(self, tag, a, b, *, limit=20):
        if tag != "economy":
            return []
        out = {}
        for tok, m in self.ms.items():
            end = closed_at(m)
            if not a <= end.date() < b:
                continue
            ev = out.setdefault(m["i"] if not m.get("twin") else 1, {
                "id": f"ev{m['i'] if not m.get('twin') else 1}", "title": f"Synthetic event {m['i']}",
                "tags": [{"slug": "economy"}], "endDate": iso(end), "markets": []})
            ev["markets"].append({
                "id": f"pm{m['i']}", "question": f"Synthetic market {m['i']}?", "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]' if m["y"] else '["0", "1"]', "clobTokenIds": json.dumps([tok, tok + "-no"]),
                "endDate": iso(end), "closedTime": iso(end), "closed": True, "volumeNum": 50_000,
                "startDate": iso(datetime.combine(m["opened"], time(12), timezone.utc))})
        return list(out.values())

    def lifetime(self, token, *, fidelity_min=1440):
        m = self.ms[token]
        pts = hourly(m["path"])
        if fidelity_min == 60:
            return pts
        last = {}
        for t, q in pts:
            last[t.date()] = q
        return [(datetime.combine(d + timedelta(days=1), time(0), timezone.utc), q) for d, q in sorted(last.items())]


class PastKalshi:
    def __init__(self, markets):
        self.ms = {f"KXSYN-{m['i']}": m for m in markets if m["venue"] == "kalshi"}

    def series(self, category):
        return [{"ticker": "KXSYN", "title": "Synthetic", "category": "Economics", "frequency": "custom"}] \
            if category == "Economics" else []

    def settled(self, series, *, archived, pages=25):
        if series != "KXSYN" or not archived:
            return []
        return [{"ticker": t, "event_ticker": f"KXSYN-E{m['i']}", "market_type": "binary", "title": f"Synthetic {m['i']}?",
                 "result": "yes" if m["y"] else "no", "close_time": iso(closed_at(m)),
                 "open_time": iso(datetime.combine(m["opened"], time(12), timezone.utc)), "volume": 90_000}
                for t, m in self.ms.items()]

    def history(self, series, ticker, start, end, *, period_min=60, archived=False):
        assert archived and period_min == 60
        return [p for p in hourly(self.ms[ticker]["path"]) if start <= p[0] <= end]

    def daily(self, series, ticker, start, end, *, archived):
        raise AssertionError("hourly prices are there")


class Fred:
    def __init__(self, levels):
        self.levels = levels

    def series(self, ids, start, end):
        return {i: {d: v for d, v in self.levels[i].items() if start <= d <= end} for i in ids if i in self.levels}


class NoMassive:
    available = False


def clients(levels, markets):
    return Clients(PastPolymarket(markets), PastKalshi(markets), Fred(levels), NoMassive(), None, None, model="test")


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def rep(world):
    return jumps.run(clients(*world), START, END, progress=lambda _: None)


def test_the_resolution_day_is_the_final_move():
    cal = business_days(date(2025, 3, 3), date(2025, 3, 14))
    q = dict(zip(cal, [0.3, 0.32, 0.35, 0.6, 0.97, 0.99, 1.0, 1.0, 1.0, 1.0]))
    before, day, q0, dq = jumps.resolution(q, 1, cal)
    assert (before, day) == (cal[3], cal[4]) and q0 == 0.6 and dq == pytest.approx(0.37)
    # No surprise left: the odds were within 15 points of the outcome the evening before.
    assert jumps.resolution({**q, cal[3]: 0.9}, 1, cal) is None
    # Never settled near the outcome, or settled, came back and stayed away.
    assert jumps.resolution(q, 0, cal) is None
    assert jumps.resolution({**q, cal[8]: 0.6, cal[9]: 0.6}, 1, cal) is None
    # Settled, came back, then settled again: the final move counts.
    assert jumps.resolution({**q, cal[8]: 0.6}, 1, cal)[:2] == (cal[8], cal[9])
    # A gap before the move: the day before has no close, so the day's change can't be read.
    gap = {d: v for d, v in q.items() if d != cal[3]}
    assert jumps.resolution(gap, 1, cal) is None


def test_the_study_reads_both_venues_and_keeps_one_market_per_event(rep, world):
    _, markets = world
    v = rep["sources"]["venues"]
    assert v["polymarket"]["sampled"] == sum(m["venue"] == "polymarket" for m in markets)
    assert v["kalshi"]["sampled"] == sum(m["venue"] == "kalshi" for m in markets)
    assert rep["cases"] == N and v["one_per_event"] == N  # the twin of market 1 is left out
    assert {r["key"] for r in rep["rows"]} >= {"polymarket:pm1"} and "polymarket:pm100" not in {r["key"] for r in rep["rows"]}
    assert rep["granularity"] == {"hourly": N}
    assert any("China and trade" in w for w in rep["warnings"])


def test_the_jumps_are_the_planted_ones(rep, world):
    _, markets = world
    rows = {r["key"]: r for r in rep["rows"]}
    delta = {}
    for m in markets[:N]:
        key = f"kalshi:KXSYN-{m['i']}" if m["venue"] == "kalshi" else f"polymarket:pm{m['i']}"
        r, delta[key] = rows[key], m["delta"]
        assert r["day"] == m["R"].isoformat() and r["y"] == m["y"] and abs(r["dq"]) >= 0.25
        for f, d in m["delta"].items():  # recovered within its error bar, give or take
            k = index(f)
            assert abs(r["pred"][k] - d) <= 3 * r["sigma"][k] + 0.3, (m["i"], f)
    # The planted jumps explain most of their factors' moves, and every clear move has the right sign.
    cases = jumps.cases_from(rep)
    p = jumps.pairs(cases)
    planted = np.array([FACTORS[f].key in delta[cases[c].key] for c, f in zip(p.case, p.f)])
    assert jumps.explained(p.take(planted)) > 0.7
    right, clear = jumps.clear_signs(p.take(planted))
    assert right == clear > 0
    # Readings of the factors an event doesn't move are noise, and add to the error: with no move where a reading
    # can't resolve the factor, more is explained. No jump explains the factor's ordinary noise on the day.
    e3 = rep["e3"]
    assert e3["explained_share"] < 0.3 < jumps.explained(p.take(planted))
    assert e3["explained_share"] < e3["explained_share_resolved"] and 0 < e3["resolved_pairs"] < e3["pairs"]
    assert 0 < e3["explainable"] < 1 and e3["rank_corr"] > 0.2
    assert rep["e4"]["coverage_80"] > 0.6
    assert rep["e5"]["precise"]["markets"] + rep["e5"]["vague"]["markets"] == N
    assert "Not run" in rep["e6"]
    json.dumps(rep, allow_nan=True)


def test_no_prediction_sees_the_resolution_day(world):
    levels, markets = world
    m = next(x for x in markets if x["venue"] == "polymarket" and x["delta"])
    r = Resolved("polymarket", f"pm{m['i']}", "polymarket:e", "e", "Economics", "t", None,
                 closed_at(m), closed_at(m), m["y"], 1e5, token="tok")
    fd = jumps.factor_data(clients(levels, markets), START, END)
    a = jumps.case_for(r, hourly(m["path"]), "hourly", fd, fd.calendar())
    # Rewrite every factor move from the resolution day on: the prediction must not change, the day's move must.
    bumped = {k: {d: (v * 1.3 if d >= m["R"] else v) for d, v in s.items()} for k, s in levels.items()}
    fd2 = jumps.factor_data(clients(bumped, markets), START, END)
    b = jumps.case_for(r, hourly(m["path"]), "hourly", fd2, fd2.calendar())
    assert a.pred == b.pred and a.sigma == b.sigma and a.z != b.z


def case(day, dq, pred, sigma, z, noise=0.4, key=None):
    pad = lambda v: [v] + [None] * (NF - 1)  # noqa: E731
    return jumps.Case(key or f"x:{day}", "kalshi", f"e:{day}", "t", "Economics", 1, day, day, 0.5, dq, "hourly", 4, 9,
                      pad(pred), pad(sigma), pad(z), [noise] * NF)


def test_metrics_by_hand():
    cs = [case("2025-07-01", 0.5, -2.0, 0.5, -1.2), case("2025-07-02", -0.4, -1.0, 2.0, 0.1),
          case("2025-07-03", 0.6, 1.0, 0.5, -0.9)]
    p = jumps.pairs(cs)
    # 1 − Σ(z − Δq·δ̂)² / Σz²
    num = (-1.2 + 1.0) ** 2 + (0.1 - 0.4) ** 2 + (-0.9 - 0.6) ** 2
    assert jumps.explained(p) == pytest.approx(1 - num / (1.44 + 0.01 + 0.81))
    # Clear moves: |z| ≥ 2 × 0.4. The first is right (−2 vs −2.4), the third wrong (+1 vs −1.5).
    assert jumps.clear_signs(p) == (1, 2)
    inside = [abs(-1.2 / 0.5 + 2.0) <= jumps.Z80 * np.hypot(0.5, 0.4 / 0.5),
              abs(0.1 / -0.4 + 1.0) <= jumps.Z80 * np.hypot(2.0, 0.4 / 0.4),
              abs(-0.9 / 0.6 - 1.0) <= jumps.Z80 * np.hypot(0.5, 0.4 / 0.6)]
    assert jumps.coverage(p) == pytest.approx(np.mean(inside))
    # The precise readings: a jump's size known to within half of it.
    assert list(p.precise) == [True, False, True]


def test_the_error_bar_scale_puts_80_percent_inside():
    rng = np.random.default_rng(0)
    cs = [case(f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}", 1.0, 0.0, 1.0, float(e), noise=1e-6, key=f"k{i}")
          for i, e in enumerate(rng.normal(0, 2.0, 400))]
    k = jumps.fit_scale(jumps.pairs(cs))
    assert k == pytest.approx(2.0, rel=0.15)
    assert jumps.coverage(jumps.pairs(cs), k) == pytest.approx(0.8, abs=0.01)


def test_the_jumps_command(world, tmp_path, monkeypatch, capsys):
    s = Settings(cache_dir=tmp_path / "c", out_dir=tmp_path / "out")
    monkeypatch.setattr("redkeep.pipeline.make_clients", lambda s, **kw: clients(*world))
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: s))
    assert cli.main(["jumps", "--since", "2025-01-01", "--until", "2025-12-31"]) == 0
    out = capsys.readouterr().out
    assert "E3  resolution-day moves explained" in out and "E6  Not run" in out
    saved = sorted((tmp_path / "out").glob("jumps-*.json"))
    first = json.loads(saved[-1].read_text())
    assert first["kind"] == "redkeep.jumps" and len(first["rows"]) == N
    # A rerun on the saved markets gives the same study without fetching.
    monkeypatch.setattr("redkeep.pipeline.make_clients", lambda s, **kw: pytest.fail("no fetching on a rerun"))
    assert cli.main(["jumps", "--from", str(saved[-1])]) == 0
    again = json.loads(sorted((tmp_path / "out").glob("jumps-*.json"))[-1].read_text())
    assert again["e3"] == first["e3"] and again["e4"] == first["e4"]
