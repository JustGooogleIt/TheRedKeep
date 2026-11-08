"""The whole refresh and analysis, offline, with stand-in clients and synthetic data."""

import json
import shutil
import subprocess
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from redkeep import factordata
from redkeep.appdata import build, export
from redkeep.config import Settings
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


class FakeFred:
    def series(self, ids, start, end):
        return {i: {d: v for d, v in LEVELS[i].items() if start <= d <= end} for i in ids if i in LEVELS}


class FakeMassive:
    available = True

    def __init__(self, extra):
        self.extra = extra

    def closes(self, t, start, end):
        src = LEVELS.get(t) or self.extra[t]
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
            if spec.id == "fed-cut-dec" and m.exclusive:
                cut = any(w in (m.outcome or "").lower() for w in ("decrease", "cut"))
                v.update(relation="part_of" if cut else "overlaps", same_entity=True, score=0.92 if cut else 0.3,
                         resolution_source="same", quoted_rule=m.rules[:60], reason="One of the cut outcomes." if cut else "A hold is not a cut.")
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


def odds_path(seed, q0, jumps):
    """A quiet random walk with 7-point jumps on the given days, alternating away from and back toward q0."""
    rng = np.random.default_rng(seed)
    days = DAYS[-170:]
    q = np.full(len(days), q0) + rng.normal(0, 0.004, len(days)).cumsum() * 0.2
    away = 1 if q0 < 0.5 else -1
    for i, t in enumerate(jumps):
        q[t:] += away * (-1) ** i * 0.07
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
    fed = ev["fed-cut-dec"]
    assert fed["match"]["kind"] == "match"
    assert {r["kind"] for r in fed["odds"]["readings"]} == {"sum"}
    assert {r["source"] for r in fed["odds"]["readings"]} == {"Kalshi (sum)", "Polymarket (sum)"}
    assert fed["odds"]["cross_checked"]
    assert fed["tier"]["tier"] in ("A", "B", "C")
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
    assert st[("cpi-hot", "fed-cut-dec")] == "untestable"


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


def test_app_export_reproduces_the_engine(world, snap, tmp_path):
    s = Settings(cache_dir=tmp_path)
    holdings = [("QQQ", 1_000_000, "usd"), ("NVDA", 500, "shares")]
    runs = [analyze(s, world, snap, holdings, name="QQQ and NVIDIA", today=TODAY),
            analyze(s, world, snap, holdings, name="QQQ and NVIDIA", today=TODAY, what_if={"taiwan": "no", "cpi-hot": "yes"})]
    data = build(snap, runs)
    ev = {e["id"]: e for e in data["events"]}
    fed = ev["fed-cut-dec"]
    assert fed["m"]["rel"] == "sum" and fed["m"]["score"] == pytest.approx(0.92)
    assert fed["seriesNote"].startswith("Sum of 2 outcomes") and len(fed["series"]) > 100 and fed["hist"]["y"] == fed["series"][-1]
    assert [r[0] for r in fed["src"]][0] == "etf" and fed["fusion"]["nMarket"] == 1
    assert ev["taiwan"]["m"] is None and ev["taiwan"]["hist"] is None
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
