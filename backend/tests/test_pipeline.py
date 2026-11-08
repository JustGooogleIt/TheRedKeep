"""The whole refresh and analysis, offline, with stand-in clients and synthetic data."""

import json
import shutil
import subprocess
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from redkeep import factordata
from redkeep.appdata import build, export
from redkeep.config import Settings
from redkeep.data.hyperliquid import Candle
from redkeep.evidence import weekend
from redkeep.factors import FACTORS, NF, index
from redkeep.markets import from_kalshi_event, from_polymarket_event
from redkeep.matching.judge import Verdict
from redkeep.evidence import prior as prior_mod
from redkeep.pipeline import Clients, analyze, refresh

from .synth import business_days, factor_levels, fund_closes

CHECK_JS = Path(__file__).resolve().parents[2] / "prototype" / "check.js"
TODAY = date(2026, 11, 8)
DAYS = business_days(date(2017, 12, 1), date(2026, 11, 6))  # long enough for the 2018 and 2019 analogs
LEVELS = factor_levels(DAYS, seed=11)


class FakeVenue:
    def __init__(self, markets, paths):
        self._m, self.paths = markets, paths

    def markets(self, since, queries=()):
        return list(self._m)

    def history(self, *args):
        key = args[0] if len(args) == 3 else args[1]  # (token, start, end) or (series, ticker, start, end)
        path = self.paths.get(key, {})
        return [(datetime.combine(d, time(19, 30), timezone.utc), q) for d, q in path.items()]  # before 4 pm New York, all year


class WeekendVenue(FakeVenue):
    """Odds that keep trading over the weekend: by Sunday evening they already show Monday's."""

    def history(self, *args):
        pts = super().history(*args)
        return sorted(pts + [(t - timedelta(hours=22), q) for t, q in pts if t.weekday() == 0])  # Sunday 21:30 UTC


class FakeHyperliquid:
    """trade[XYZ]'s listings, and hourly perp candles that drift at random over each weekend, plus any planted moves
    (in %, by the weekend's Friday and the coin's factor). Synthetic, not prices."""
    LISTED = {"xyz:SP500": {"maxLeverage": 50}, "xyz:CL": {"maxLeverage": 20}, "xyz:GOLD": {"maxLeverage": 25}}
    FACTOR = {"xyz:SP500": "eq", "xyz:CL": "oil", "xyz:GOLD": "gold"}

    def __init__(self, moves: dict[date, dict[str, float]] | None = None):
        self.moves = moves or {}

    def coins(self, dex="xyz"):
        return dict(self.LISTED)

    def candles(self, coin, start, end, interval="1h"):
        rng = np.random.default_rng(sorted(self.LISTED).index(coin))
        p, out = 100.0, []
        for a, b in weekend.weekends(start, end + timedelta(days=3)):
            planted = self.moves.get(a.date(), {}).get(self.FACTOR[coin], 0.0)
            p1 = p * (1 + rng.normal(0, 0.006) + planted / 100)
            path = [p] * 3 + list(np.linspace(p, p1, int((b - a).total_seconds() // 3600))) + [p1] * 3
            t0 = a - timedelta(hours=3)
            out += [Candle(t0 + timedelta(hours=i), t0 + timedelta(hours=i + 1, milliseconds=-1), x, x, x, x, 1.0, 5)
                    for i, x in enumerate(path)]
            p = p1
        return [c for c in out if start <= c.start <= end]


class FakeFred:
    def __init__(self, levels=LEVELS):
        self.levels = levels

    def series(self, ids, start, end):
        return {i: {d: v for d, v in self.levels[i].items() if start <= d <= end} for i in ids if i in self.levels}


class FakeMassive:
    available = True

    def __init__(self, extra, levels=LEVELS):
        self.extra, self.levels = extra, levels

    def closes(self, t, start, end):
        src = self.levels.get(t) or self.extra[t]
        return {d: v for d, v in src.items() if start <= d <= end}

    def details(self, t):
        return {"name": t, "type": "ETF" if t != "NVDA" else "CS"}

    def constituents(self, etf, as_of):
        if etf != "QQQ":
            return None
        return date(2026, 11, 5), [{"constituent_ticker": "NVDA", "constituent_name": "NVIDIA", "weight": 0.09},
                                   {"constituent_ticker": "MSFT", "constituent_name": "Microsoft", "weight": 0.08}]


class FakeJudge:
    def judge(self, spec, markets, today):
        out = []
        for m in markets:
            v = dict(id=m.key, relation="unrelated", polarity="yes", same_entity=False, threshold="not_applicable",
                     deadline="same", resolution_source="unclear", market_threshold=None, market_deadline=None,
                     score=0.1, quoted_rule="", reason="About something else.")
            if spec.id == "fed-hike-dec" and m.exclusive:
                hike = any(w in (m.outcome or "").lower() for w in ("increase", "hike"))
                v.update(relation="part_of" if hike else "overlaps", same_entity=True, score=0.92 if hike else 0.3,
                         resolution_source="same", quoted_rule=m.rules[:60], reason="One of the hike outcomes." if hike else "A hold is not a hike.")
            if spec.id == "recession" and "recession" in m.title.lower():
                v.update(relation="implies", same_entity=True, deadline="earlier", score=0.7, market_deadline="2026-12-31",
                         reason="Closes Dec 31, 2026, four months before the event's deadline.")
            out.append(Verdict(**v))
        return out


class FakePrior:
    def estimate(self, spec, today, typical):
        return prior_mod.Prior(probability=0.08, probability_reasoning="Base rate.", move_reasoning="Risk-off.",
                               moves=prior_mod.FactorMoves(eq=-1.0, rates=-0.4, infl=0.1, usd=0.2, oil=0.5, credit=0.8,
                                                           vix=1.2, china=-0.5, chips=-0.6, gold=0.6))


def odds_jumps(q0, jumps, size=0.07):
    """Jumps on the given days (indexes into the last 170 business days), alternating away from and back toward q0."""
    away = 1 if q0 < 0.5 else -1
    return {t: away * (-1) ** i * size for i, t in enumerate(jumps)}


def odds_path(seed, q0, jumps, size=0.07):
    """A quiet random walk with those jumps."""
    rng = np.random.default_rng(seed)
    days = DAYS[-170:]
    q = np.full(len(days), q0) + rng.normal(0, 0.004, len(days)).cumsum() * 0.2
    for t, x in odds_jumps(q0, jumps, size).items():
        q[t:] += x
    return dict(zip(days, np.clip(q, 0.01, 0.99).tolist()))


@pytest.fixture(scope="module")
def world(gamma_events_mod, kalshi_events_mod):
    pm = [m for e in gamma_events_mod for m in from_polymarket_event(e)]
    ks = [m for e in kalshi_events_mod for m in from_kalshi_event(e)]
    # Each market jumps on its own days, so summed outcomes never cancel each other's jumps.
    paths = {m.token_yes: odds_path(i, m.mid, [30 + 3 * i, 80 + 3 * i, 120 + 3 * i]) for i, m in enumerate(pm) if m.token_yes}
    paths.update({m.id: odds_path(50 + i, m.mid, [40 + 3 * i, 90 + 3 * i, 140 + 3 * i]) for i, m in enumerate(ks)})
    fd = factordata.build(LEVELS)
    qqq = fund_closes(fd, np.array([2.4, -0.25, -0.1, -0.15, -0.1, -0.05, -0.05, 0.15, 0.55, 0]), seed=4)
    nvda = fund_closes(fd, np.array([3.0, -0.3, 0, 0, 0, 0, 0, 0.2, 1.5, 0]), noise=1.0, seed=6, p0=180.0)
    return Clients(FakeVenue(pm, paths), FakeVenue(ks, paths), FakeFred(), FakeMassive({"QQQ": qqq, "NVDA": nvda}),
                   FakeJudge(), FakePrior())


@pytest.fixture(scope="module")
def gamma_events_mod():
    from .conftest import FIX
    return json.loads((FIX / "gamma_events.json").read_text())


@pytest.fixture(scope="module")
def kalshi_events_mod():
    from .conftest import FIX
    return json.loads((FIX / "kalshi_events.json").read_text())["events"]


@pytest.fixture(scope="module")
def snap(world, tmp_path_factory):
    s = Settings(cache_dir=tmp_path_factory.mktemp("cache"))
    return refresh(s, world, today=TODAY, progress=lambda _: None)


def test_refresh_builds_a_complete_library(snap):
    json.dumps(snap, allow_nan=False)  # no NaN or infinity leaks into the snapshot
    assert len(snap["events"]) == 10
    ev = {e["id"]: e for e in snap["events"]}
    fed = ev["fed-hike-dec"]
    assert fed["match"]["kind"] == "match"
    assert {r["kind"] for r in fed["odds"]["readings"]} == {"sum"}
    assert {r["source"] for r in fed["odds"]["readings"]} == {"Kalshi (sum)", "Polymarket (sum)"}
    assert fed["odds"]["cross_checked"]
    assert fed["tier"]["tier"] in ("A", "B", "C")
    # Each venue's outcomes add up to at most one, within their quotes.
    assert fed["coherence"] == {"checks": 2, "contested": False, "findings": []}
    assert len(fed["history"]) > 100
    assert any(r["kind"] == "etf" for r in fed["delta"]["readings"])  # jump windows found the sharp moves
    # A lower bound alone is too wide to use: nothing fits, with the near-miss and its reason.
    rec = ev["recession"]
    assert rec["match"]["kind"] == "none" and rec["tier"]["tier"] == "D"
    assert "between 6% and 100%" in rec["match"]["note"]
    assert rec["match"]["near_misses"][0]["reason"].startswith("Closes Dec 31, 2026")
    # With no market, the AI prior sets the odds and the jump comes from past events and the AI only.
    assert rec["odds"]["readings"][0]["kind"] == "ai"
    taiwan = ev["taiwan"]
    assert taiwan["tier"]["tier"] == "D" and taiwan["odds"]["q"] == pytest.approx(0.08)
    assert [r["kind"] for r in ev["tariffs-china"]["delta"]["readings"]] == ["past", "ai"]
    st = {(l["parent"], l["child"]): l["status"] for l in snap["links"]}
    assert st[("cpi-hot", "fed-hike-dec")] == "untestable"


def test_analysis_ranks_events_for_a_portfolio(world, snap, tmp_path):
    s = Settings(cache_dir=tmp_path)
    a = analyze(s, world, snap, [("QQQ", 1_000_000, "usd")], name="QQQ", today=TODAY)
    json.dumps(a, allow_nan=False)
    p = a["portfolio"]
    assert p["value"] == pytest.approx(1_000_000)
    assert p["b"][0] == pytest.approx(2.4, abs=0.25)  # the regression finds QQQ's market exposure
    assert p["fit"] > 0.8
    look = {o["key"]: o for o in p["look"]}
    assert look["NVDA"]["weight"] == pytest.approx(0.09) and look["rest:QQQ"]["weight"] == pytest.approx(0.83)
    swings = [e["swing_pct"] for e in a["events"]]
    assert swings == sorted(swings, reverse=True)
    for e in a["events"]:
        assert e["q"] * e["if_yes"] + (1 - e["q"]) * e["if_no"] == pytest.approx(0, abs=1e-9)
    sim = a["simulation"]
    assert abs(sim["mean"]) < 4 * sim["sd"] / np.sqrt(sim["n"]) + 1e-9


def test_what_if_resolves_an_event(world, snap, tmp_path):
    a = analyze(Settings(cache_dir=tmp_path), world, snap, [("QQQ", 1_000_000, "usd")], today=TODAY, what_if={"taiwan": "no"})
    assert "taiwan" not in [d["id"] for d in a["simulation"]["drivers"]]


def test_the_export_carries_the_coherence_checks(keyless):
    """Each event brings its checks, and a capped link's note shows from either end. Older snapshots have neither."""
    _, snap = keyless
    pair = ("israel-iran", "hormuz-reopens")
    links = [dict(l, status="confirmed", used=True, w=-0.1, capped="Capped.") if (l["parent"], l["child"]) == pair else l
             for l in snap["links"]]
    data = build({**snap, "links": links}, [])
    ev = {e["id"]: e for e in data["events"]}
    assert ev["israel-iran"]["coh"]["caps"] == {"hormuz-reopens": "Capped."}
    assert ev["hormuz-reopens"]["coh"]["caps"] == {"israel-iran": "Capped."}
    assert ev["fed-hike-dec"]["coh"]["n"] == 2 and ev["taiwan"]["coh"]["n"] == 0
    assert [l["capped"] for l in data["linkChecks"] if l["capped"]] == ["Capped."]
    old = {**snap, "events": [{k: v for k, v in e.items() if k != "coherence"} for e in snap["events"]]}
    assert all(e["coh"] is None for e in build(old, [])["events"])


def test_app_export_reproduces_the_engine(world, snap, tmp_path):
    s = Settings(cache_dir=tmp_path)
    holdings = [("QQQ", 1_000_000, "usd"), ("NVDA", 500, "shares")]
    runs = [analyze(s, world, snap, holdings, name="QQQ and NVIDIA", today=TODAY),
            analyze(s, world, snap, holdings, name="QQQ and NVIDIA", today=TODAY, what_if={"taiwan": "no", "cpi-hot": "yes"})]
    data = build(snap, runs)
    ev = {e["id"]: e for e in data["events"]}
    fed = ev["fed-hike-dec"]
    assert fed["m"]["rel"] == "sum" and fed["m"]["score"] == pytest.approx(0.92)
    assert fed["seriesNote"].startswith("Sum of 2 outcomes") and len(fed["series"]) > 100 and fed["hist"]["y"] == fed["series"][-1]
    # Prices here ignore the odds, so the ETF reading is noise: too vague to count, and with nothing to check against.
    assert [r[0] for r in fed["src"]][0] == "etf"
    assert fed["fusion"]["nAll"] == 1 and fed["fusion"]["nMarket"] == 0 and fed["fusion"]["agree"] is None
    assert fed["tier"] == "C" and fed["tierWhy"].startswith("The market readings of the jump are too vague")
    assert ev["taiwan"]["m"] is None and ev["taiwan"]["hist"] is None
    assert fed["coh"] == {"n": 2, "contested": False, "flags": [], "caps": {}}
    assert ev["recession"]["miss"][0][2].startswith("Closes Dec 31, 2026")
    # The AI prior is the only reading for Taiwan, so it is exactly the fused jump.
    assert ev["taiwan"]["src"] == [["ai", "AI prior", ev["taiwan"]["src"][0][2], 1.0, pytest.approx(ev["taiwan"]["src"][0][4])]]
    t = data["tickers"]
    assert t["NVDA"]["k"] == "Stock" and t["QQQ"]["h"][0] == ["NVDA", 9.0] and t["QQQ"]["rest"] == ""
    assert [x["sample"] for x in data["results"]] == ["qqq-and-nvidia", "qqq-and-nvidia-2"]
    text = export(snap, runs)
    assert text.startswith("/* Generated by `redkeep app`") and "</" not in text

    with pytest.raises(ValueError, match="analyzed against the snapshot"):
        build({**snap, "as_of": "2020-01-01T00:00:00+00:00"}, runs)

    # The app's own model, run in Node on this export, must give the engine's numbers.
    node = shutil.which("node")
    if not node:
        pytest.skip("Node isn't installed")
    live = tmp_path / "live.js"
    live.write_text(text)
    out = subprocess.run([node, str(CHECK_JS), str(live)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.count("matches the engine") == 4 and "moves match the engine" in out.stdout


@pytest.fixture(scope="module")
def keyless(world, tmp_path_factory):
    """The clients without a Massive key, and the library they build."""
    class NoMassive(FakeMassive):
        available = False

    c = Clients(world.polymarket, world.kalshi, world.fred, NoMassive({}), world.judge, world.prior)
    return c, refresh(Settings(cache_dir=tmp_path_factory.mktemp("keyless")), c, today=TODAY, progress=lambda _: None)


def test_without_massive_the_library_still_builds(keyless, tmp_path):
    c, snap = keyless
    assert any("No Massive key" in w for w in snap["warnings"])
    have = {f["key"] for f in snap["factors"] if f["source"]}
    assert have == {"eq", "rates", "infl", "usd", "oil", "credit", "vix"}  # the FRED seven; the S&P 500 stands in for SPY
    assert len(snap["events"]) == 10
    with pytest.raises(RuntimeError, match="MASSIVE_API_KEY"):
        analyze(Settings(cache_dir=tmp_path), c, snap, [("QQQ", 1_000_000, "usd")], today=TODAY)


def test_the_app_takes_the_events_alone(keyless, tmp_path):
    """Without analyses the export carries the events, and the app keeps its illustrative portfolios."""
    _, snap = keyless
    data = build(snap, [])
    assert data["exposures"] == "illustrative"
    assert data["tickers"] == {} and data["samples"] == [] and data["results"] == []
    assert len(data["events"]) == 10 and data["warnings"] == snap["warnings"]
    measured = [f["k"] for f in data["factors"] if f["typical"]]
    assert measured == ["eq", "rates", "infl", "usd", "oil", "credit", "vix"]
    for e, x in zip(snap["events"], data["events"]):  # with no prices, only the AI prior moves China, chips and gold
        ai = [r for r in e["delta"]["readings"] if r["kind"] == "ai"]
        assert all(r["sigma"][k] is None for r in e["delta"]["readings"] if r["kind"] != "ai" for k in (7, 8, 9))  # no information
        assert x["d"][7:] == pytest.approx(ai[0]["delta"][7:] if ai else [0.0] * 3, abs=1e-6)

    node = shutil.which("node")
    if not node:
        pytest.skip("Node isn't installed")
    live = tmp_path / "live.js"
    live.write_text(export(snap, []))
    out = subprocess.run([node, str(CHECK_JS), str(live)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.count("keep their size per unit of each factor") == 6
    assert out.stdout.count("the April 2025 replay is unchanged") == 6
    assert out.stdout.count("snapshot · ") == 6 * 5 and "matches the engine" not in out.stdout



# The Fed hike's planted jump, in typical moves, and the series that carry it. SMH moves with SPY, so chips don't.
HIKE = {"eq": -3.0, "rates": 3.0, "usd": 2.0, "oil": -1.5, "gold": -2.5}
PROXIES = {"eq": ("SPY", "SP500", "SMH"), "rates": ("DGS10",), "usd": ("DTWEXBGS",), "oil": ("DCOILWTICO",), "gold": ("GLD",)}


def planted(levels, jumps, typical):
    """The levels with the hike's jump added from each jump day on: δ_f times the odds change, in typical moves."""
    out = {k: dict(v) for k, v in levels.items()}
    for day, dq in jumps.items():
        for key, d in HIKE.items():
            f = FACTORS[index(key)]
            move = d * dq * typical[index(key)]  # in the factor's unit: % or bp
            for name in PROXIES[key]:
                s = out[name]
                for t in s:
                    if t >= day:
                        s[t] = s[t] + move / f.scale if f.transform == "diff" else s[t] * (1 + move / 100)
    return out


@pytest.fixture(scope="module")
def weekend_world(world):
    """The same world, but odds trade over weekends, Hyperliquid lists its perps, and the Fed hike outcomes jump
    15 points on Mondays (so over the weekend) and midweek. Prices carry the planted jump: ETFs on the day, the
    perps over the weekend."""
    pm, ks = world.polymarket, world.kalshi
    paths, jumps = {**pm.paths}, {}
    hikes = [m for m in pm._m + ks._m if any(w in (m.outcome or "").lower() for w in ("increase", "hike"))]
    for j, m in enumerate(hikes):  # index i of the last 170 business days is a Monday when i % 5 == 0
        days = [50, 70, 90, 110, 130] if j % 2 == 0 else [62, 83, 104, 126, 147]  # the same days on both venues
        paths[m.token_yes or m.id] = odds_path(200 + j, m.mid, days, size=0.15)
        if m.venue == "polymarket":
            jumps.update({DAYS[-170:][t]: x for t, x in odds_jumps(m.mid, days, size=0.15).items()})
    typical = factordata.build(LEVELS).typical
    levels = planted(LEVELS, jumps, typical)
    weekends = {d - timedelta(days=3): {k: HIKE[k] * dq * typical[index(k)] for k in ("eq", "oil", "gold")}
                for d, dq in jumps.items() if d.weekday() == 0}
    return replace(world, polymarket=WeekendVenue(pm._m, paths), kalshi=WeekendVenue(ks._m, paths),
                   fred=FakeFred(levels), massive=FakeMassive(world.massive.extra, levels),
                   hyperliquid=FakeHyperliquid(weekends))


def test_weekend_windows_add_a_second_market_reading(weekend_world, tmp_path):
    with_hl = refresh(Settings(cache_dir=tmp_path / "a"), weekend_world, today=TODAY, progress=lambda _: None)
    without = refresh(Settings(cache_dir=tmp_path / "b"), replace(weekend_world, hyperliquid=None), today=TODAY,
                      progress=lambda _: None)
    json.dumps(with_hl, allow_nan=False)
    assert without["windows"]["weekends"] is None and without["windows"]["left_out"] == 0
    wk = with_hl["windows"]["weekends"]
    assert wk["coins"] == {"eq": "xyz:SP500", "oil": "xyz:CL", "gold": "xyz:GOLD"} and wk["censored"] == []
    assert set(wk["used"]) == {"eq", "oil", "gold"} and wk["readings"] == 1 and with_hl["windows"]["left_out"] >= 5
    fed, fed0 = (next(e for e in snap["events"] if e["id"] == "fed-hike-dec") for snap in (with_hl, without))
    rs, rs0 = ({r["kind"]: r for r in e["delta"]["readings"]} for e in (fed, fed0))
    covered = [index("eq"), index("oil"), index("gold")]
    others = [k for k in range(NF) if k not in covered]
    assert [k for k, x in enumerate(rs["hl"]["sigma"]) if x is not None] == covered
    assert rs["hl"]["detail"].startswith("S&P 500, WTI and gold perps: 5 weekends with odds moves of 5+ points")
    # Monday's ETF window spans the weekend: on stocks, oil and gold it is left to the perps, elsewhere it stays.
    assert rs["etf"]["detail"].endswith("that span a weekend are left to the S&P 500, WTI and gold perps")
    assert [rs["etf"]["delta"][k] for k in others] == [rs0["etf"]["delta"][k] for k in others]
    assert all(rs["etf"]["sigma"][k] > rs0["etf"]["sigma"][k] for k in covered)
    # Each reading finds the planted jump on the factors it measures.
    for key, d in HIKE.items():
        k = index(key)
        assert abs(rs["etf"]["delta"][k] - d) < 4 * rs["etf"]["sigma"][k], key
        if key in ("eq", "oil", "gold"):
            assert abs(rs["hl"]["delta"][k] - d) < 4 * rs["hl"]["sigma"][k], key
    # One precise reading alone leaves the event at tier C; two that agree on the jump make it tier A.
    assert fed0["delta"]["n_market"] == fed0["delta"]["n_market_all"] == 1 and fed0["tier"]["tier"] == "C"
    assert fed0["tier"]["reason"] == "Only one market reading of the jump."
    assert fed["delta"]["n_market"] == 2 and fed["delta"]["agree"] and fed["tier"]["tier"] == "A"
    ev = {e["id"]: e for e in build(with_hl, [])["events"]}
    assert [r[0] for r in ev["fed-hike-dec"]["src"]][:2] == ["etf", "hl"]
    assert ev["fed-hike-dec"]["fusion"] | {"chi2": 0, "crit": 0} == {"nMarket": 2, "nAll": 2, "chi2": 0, "dof": 3, "crit": 0, "agree": True}

