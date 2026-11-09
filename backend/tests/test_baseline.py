"""Eval E9 ("just ask an AI"), offline: synthetic resolved markets and a stand-in for Claude that knows the true jumps
of some markets and remembers the outcomes of old ones. Nothing here is market data or a real answer."""

import gzip
import json
import random
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from redkeep import baseline, cli, jumps
from redkeep.config import Settings
from redkeep.evidence import calibration, prior
from redkeep.factors import KEYS, NF
from redkeep.jumps import Case
from redkeep.pipeline import Clients, finite

AFTER = baseline.AFTER
TRUE = {"eq": 1.5, "rates": -2.0, "usd": -1.0, "oil": 0.5}  # a Fed cut's jump, in typical moves
DATA = 7  # factors with data: the last three (China, chips, gold) have none, as without Massive


def case(i: int, day: date, macro: bool, rng: random.Random) -> Case:
    dq = rng.choice([0.5, 0.6, -0.6, 0.7])
    true = [TRUE.get(k, 0.0) if macro else 0.0 for k in KEYS]
    z = [dq * t + rng.gauss(0, 0.3) if f < DATA else None for f, t in enumerate(true)]
    pred = [t + rng.gauss(0, 2.0) if f < DATA else None for f, t in enumerate(true)]  # vague market readings
    return Case(key=f"polymarket:{i}", venue="polymarket", event=f"polymarket:e{i}",
                title=f"Will the Fed cut rates at meeting {i}?" if macro else f"Will Aria Vale release album {i}?",
                category="Economics" if macro else "Culture", y=int(dq > 0), day=day.isoformat(),
                before=(day - timedelta(days=1)).isoformat(), q=0.3, dq=dq, granularity="hourly", n=5, windows=40,
                pred=pred, sigma=[2.0 if f < DATA else None for f in range(NF)], z=z,
                noise=[0.3 if f < DATA else 1.0 for f in range(NF)])


def cases(seed: int = 3) -> list[Case]:
    rng = random.Random(seed)
    late = [case(i, AFTER + timedelta(days=3 * (i // 2)), i % 2 == 0, rng) for i in range(40)]
    early = [case(100 + i, date(2026, 3, 2) + timedelta(days=i), i % 2 == 0, rng) for i in range(8)]
    return early + late


def observations() -> list[calibration.Obs]:
    out = []
    for i in range(90):
        late = i < 60
        closed = AFTER + timedelta(days=i) if late else date(2025, 9, 1) + timedelta(days=i)
        y = int(i % 3 == 0)
        event = f"kalshi:EV{i // 3 if i < 30 else i}"  # the first 30 come in threes, to test the per-event cap
        out.append(calibration.Obs("kalshi", f"kalshi:M{i}", event, "Economics", closed, 30, 0.8 if y else 0.2, y,
                                   f"Will indicator {i} beat its forecast?", closed))
    return out


class FakeClaude:
    """Knows the true jump of the Fed markets, and remembers the outcome of markets that closed before 2026."""

    def __init__(self, obs=()):
        self.seen = []
        self.old = {o.market: o.y for o in obs if o.closed.year < 2026}
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    def parse(self, *, model, messages, output_format, **kw):
        assert output_format is prior.Prior and kw["system"][0]["text"] == prior.SYSTEM
        req = json.loads(messages[0]["content"])
        self.seen.append(req)
        ev = req["event"]
        macro = "Fed" in ev["question"]
        p = {1: 0.97, 0: 0.03}.get(self.old.get(ev["id"]), 0.5)
        out = prior.Prior(probability=p, probability_reasoning="Stand-in.", move_reasoning="Stand-in.",
                          moves=prior.FactorMoves(**{k: TRUE.get(k, 0.0) if macro else 0.0 for k in KEYS}))
        return SimpleNamespace(stop_reason="end_turn", parsed_output=out, model=model,
                               usage=SimpleNamespace(input_tokens=600, output_tokens=200))


def test_claude_is_asked_only_about_markets_that_resolved_after_its_training_data():
    cs, obs = cases(), observations()
    fake = FakeClaude(obs)
    rep = baseline.study(prior.AIPrior(fake, model="test"), cases=cs, typical=[2.0] * 7 + [None] * 3, obs=obs,
                         most=20, leak=10, progress=lambda _: None)
    jumps_asked = [r for r in fake.seen if r["event"]["question"].startswith(("Will the Fed", "Will Aria"))]
    assert len(jumps_asked) == 40 and rep["jumps"]["markets"] == 40  # none of the eight from March
    by_key = {c.key: c for c in cs}
    for r in jumps_asked:
        c = by_key[r["event"]["id"]]
        assert date.fromisoformat(c.day) >= AFTER and r["today"] == c.before  # as of the evening before
        assert "deadline" not in r["event"]  # nothing about when or how it closed
    odds_asked = [r for r in fake.seen if r["event"]["question"].startswith("Will indicator")]
    assert len(odds_asked) == 30 and all(r["event"]["deadline"] for r in odds_asked)
    for r in odds_asked:
        assert date.fromisoformat(r["today"]) == date.fromisoformat(r["event"]["deadline"]) - timedelta(days=30)
    picked = rep["rows"]["post"] + rep["rows"]["past"]
    assert max(sum(o["event"] == e for o in picked) for e in {o["event"] for o in picked}) <= baseline.PER_EVENT
    assert rep["usage"] == {"calls": 70, "cached": 0, "tokens_in": 70 * 600, "tokens_out": 70 * 200}


def test_the_jumps_compare_claude_the_market_and_their_fusion_on_the_same_pairs():
    rep = baseline.study(prior.AIPrior(FakeClaude(), model="test"), cases=cases(), typical=None, progress=lambda _: None)
    j = rep["jumps"]
    assert j["market"]["pairs"] == j["ai"]["pairs"] == j["engine"]["pairs"] == 40 * DATA
    # Claude knows the jumps here and the market's readings are vague, so Claude explains most, the market least,
    # and the fusion, which weighs Claude's tighter error bars more, sits between.
    assert j["ai"]["explained_share"] > j["engine"]["explained_share"] > j["market"]["explained_share"]
    assert j["gaps"]["engine_minus_market"]["share"] > 0 > j["gaps"]["engine_minus_ai"]["share"]
    lo, hi = j["gaps"]["ai_minus_market"]["range"]
    assert 0 < lo <= j["gaps"]["ai_minus_market"]["share"] <= hi
    assert 0.5 < j["ai_weight"] < 1
    assert j["ai"]["coverage_80"] > 0.8 and j["ai_error_scale"] < 1  # the prior's wide error bars are too wide here


def test_the_odds_show_what_claude_remembers_and_what_it_can_only_guess():
    obs = observations()
    rep = baseline.study(prior.AIPrior(FakeClaude(obs), model="test"), obs=obs, progress=lambda _: None)
    after, before = rep["odds"]["after"], rep["odds"]["before"]
    assert after["markets"] == 30 + 2 * 10  # 30 single-market events, and two of each of the ten in threes
    assert after["ai"]["brier"] == pytest.approx(0.25) and after["market"]["brier"] == pytest.approx(0.04)
    assert after["brier_gap"]["range"][0] > 0  # Claude did worse, reliably
    assert before["markets"] == 30 and before["brier_gap"]["mean"] < 0  # it remembers how these ended
    assert before["ai_closer"] == 1.0


def test_a_saved_run_scores_the_same_without_asking():
    obs = observations()
    rep = finite(baseline.study(prior.AIPrior(FakeClaude(obs), model="test"), cases=cases(), obs=obs,
                                material={c.key: "Fed" in c.title for c in cases()}, progress=lambda _: None))
    again = baseline.rescore(json.loads(json.dumps(rep)))
    for part in ("jumps", "odds"):
        assert finite(again[part]) == rep[part]
    m = rep["jumps"]["by_material"]
    assert m["material_markets"] == 20 and m["other"]["ai"]["explained_share"] == pytest.approx(0, abs=0.2)


def test_observations_keep_their_title_and_deadline(tmp_path):
    obs = observations()[:3]
    calibration.write_obs(obs, tmp_path / "o.jsonl.gz")
    assert calibration.read_obs(tmp_path / "o.jsonl.gz") == obs
    old = tmp_path / "old.jsonl"  # rows written before titles were kept still load
    with gzip.open(old, "wt") as f:
        f.write(json.dumps({"venue": "kalshi", "market": "kalshi:A", "event": "kalshi:E", "category": "Economics",
                            "closed": "2026-01-01", "h": 30, "q": 0.4, "y": 0}) + "\n")
    [o] = calibration.read_obs(old)
    assert o.title == "" and o.deadline is None


def test_the_baseline_command(tmp_path, monkeypatch, capsys):
    cs, obs = cases(), observations()
    fake = FakeClaude(obs)
    s = Settings(cache_dir=tmp_path / "c", out_dir=tmp_path / "out")
    ai = prior.AIPrior(fake, model="test", log_dir=s.cache_dir / "ai")
    monkeypatch.setattr("redkeep.pipeline.make_clients", lambda s, **kw: Clients(None, None, None, None, None, ai, model="test"))
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: s))
    jf = tmp_path / "jumps.json"
    jf.write_text(json.dumps(finite(jumps.report(cs, {"since": "2026-01-01"}, [], typical=[2.0] * 7 + [None] * 3))))
    of = tmp_path / "obs.jsonl.gz"
    calibration.write_obs(obs, of)

    assert cli.main(["baseline", "--jumps", str(jf), "--odds", str(of), "--most", "20", "--leak", "10"]) == 0
    out = capsys.readouterr().out
    assert "Claude alone" in out and "the engine minus Claude" in out and "a check" in out
    saved = tmp_path / "out" / "baseline-latest.json"
    calls = len(fake.seen)
    assert calls == 70 and json.loads(saved.read_text())["jumps"]["markets"] == 40

    assert cli.main(["baseline", "--from", str(saved)]) == 0 and len(fake.seen) == calls  # rescored, nothing asked
    assert cli.main(["baseline", "--jumps", str(jf), "--odds", str(of), "--most", "20", "--leak", "10"]) == 0
    assert len(fake.seen) == calls and ai.cached == 70  # asked again: every answer comes from the cache

    stale = tmp_path / "stale.jsonl.gz"
    calibration.write_obs([calibration.Obs("kalshi", "kalshi:A", "kalshi:E", "Economics", AFTER, 30, 0.4, 0)], stale)
    assert cli.main(["baseline", "--odds", str(stale)]) == 1
    assert "no market titles" in capsys.readouterr().err
    assert cli.main(["baseline"]) == 1
