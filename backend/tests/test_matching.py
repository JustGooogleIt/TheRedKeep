"""Retrieval, the judge (with a stand-in client), ladders, bounds and "nothing fits"."""

from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from redkeep.config import Settings
from redkeep.events import load_library
from redkeep.markets import Market, from_kalshi_event, from_polymarket_event
from redkeep.matching import ladders
from redkeep.matching.decide import Candidate, decide
from redkeep.matching.judge import Judge, JudgeRefused, Judgement, Verdict
from redkeep.matching.retrieve import Retriever, tokenize

S = Settings()
LIB = load_library()
SPEC = LIB.by_id()


def mk(id, title, q, *, venue="kalshi", event="G", volume=500_000, spread=0.02, outcome=None, exclusive=False):
    return Market(venue=venue, id=id, event_id=event, title=title, event_title=title, outcome=outcome, rules=title,
                  close_time=datetime(2027, 4, 30, tzinfo=timezone.utc), bid=q - spread / 2, ask=q + spread / 2,
                  last=q, volume=volume, volume_24h=0, liquidity=0, exclusive=exclusive, url="")


def v(m, relation, score, **kw):
    base = dict(id=m.key, relation=relation, polarity="yes", same_entity=True, threshold="not_applicable",
                deadline="same", resolution_source="same", market_threshold=None, market_deadline=None,
                score=score, quoted_rule="rule", reason="reason")
    base.update(kw)
    return Candidate(m, Verdict(**base))


# -- retrieval -----------------------------------------------------------------


def test_tokenize_keeps_numbers_and_drops_noise():
    assert tokenize("Will core CPI rise 0.4% or more?") == ["core", "cpi", "rise", "0.4"]


def test_retrieval_finds_fed_markets_on_both_venues(gamma_events, kalshi_events):
    markets = [m for e in gamma_events for m in from_polymarket_event(e)] + [m for e in kalshi_events for m in from_kalshi_event(e)]
    got = Retriever(markets).candidates(SPEC["fed-cut-dec"], k=8)
    venues = [m.venue for m, _ in got]
    assert {"polymarket", "kalshi"} <= set(venues)
    assert all("Fed" in m.title or "Fed" in m.event_title for m, _ in got)  # the recession market is cut off


# -- ladders --------------------------------------------------------------------


def test_pav_enforces_monotonicity():
    assert ladders.pav([0.5, 0.6, 0.4], [1, 1, 1], increasing=False) == pytest.approx([0.55, 0.55, 0.4])
    assert ladders.pav([0.1, 0.3, 0.2, 0.6], [1, 1, 1, 1]) == pytest.approx([0.1, 0.25, 0.25, 0.6])


def test_threshold_ladder_interpolates_and_never_extrapolates():
    pts = [ladders.LadderPoint(0.3, 0.45, 0.02, "a"), ladders.LadderPoint(0.5, 0.15, 0.02, "b")]
    r = ladders.estimate(pts, 0.4, increasing=False, space="logit")
    assert r.kind == "interpolated" and 0.15 < r.q < 0.45
    out = ladders.estimate(pts, 0.6, increasing=False, space="logit")
    assert out.kind == "bound" and (out.lo, out.hi) == (0.0, 0.15)
    below = ladders.estimate(pts, 0.2, increasing=False, space="logit")
    assert below.kind == "bound" and (below.lo, below.hi) == (0.45, 1.0)


def test_deadline_ladder_uses_constant_hazard():
    pts = [ladders.LadderPoint(0, 0.10, 0.01, "dec"), ladders.LadderPoint(365, 0.30, 0.01, "next-dec")]
    r = ladders.estimate(pts, 120, increasing=True, space="hazard")
    import math
    h = -math.log(0.9) + 120 / 365 * (-math.log(0.7) + math.log(0.9))
    assert r.q == pytest.approx(1 - math.exp(-h))


# -- decisions ------------------------------------------------------------------


def test_same_question_on_both_venues_gives_two_readings():
    a, b = mk("K1", "Fed cuts in Dec", 0.60), mk("P1", "Fed cuts in Dec", 0.64, venue="polymarket")
    d = decide(SPEC["fed-cut-dec"], [v(a, "same_question", 0.95), v(b, "same_question", 0.92)], S)
    assert d.kind == "match" and d.good
    assert sorted(r.source for r in d.readings) == ["Kalshi", "Polymarket"]


def test_mutually_exclusive_parts_are_summed():
    c25 = mk("C25", "Cut 25bps", 0.61, exclusive=True, outcome="Cut 25bps")
    c50 = mk("C26", "Cut >25bps", 0.015, exclusive=True, outcome="Cut >25bps", volume=100_000)
    hold = mk("H0", "Hold", 0.37, exclusive=True, outcome="Hold")
    d = decide(SPEC["fed-cut-dec"], [v(c25, "part_of", 0.9), v(c50, "part_of", 0.9), v(hold, "overlaps", 0.3)], S)
    assert d.kind == "match" and d.readings[0].kind == "sum"
    assert d.readings[0].q == pytest.approx(0.625)
    assert d.near_misses == []  # "Hold" belongs to the group that was used, so it isn't a near miss


def test_polarity_no_flips_the_odds():
    hold = mk("H", "Will the Fed hold?", 0.37)
    d = decide(SPEC["fed-cut-dec"], [v(hold, "same_question", 0.85, polarity="no")], S)
    assert d.readings[0].q == pytest.approx(0.63)


def test_threshold_ladder_decision():
    spec = SPEC["cpi-hot"]  # 0.4% or more
    r3 = mk("CPI-0.3", "Core CPI above 0.3%", 0.45, event="CPI")
    r5 = mk("CPI-0.5", "Core CPI above 0.5%", 0.08, event="CPI")
    d = decide(spec, [v(r3, "implied_by", 0.75, threshold="looser", market_threshold=0.3),
                      v(r5, "implies", 0.75, threshold="stricter", market_threshold=0.5)], S)
    assert d.kind == "ladder" and 0.08 < d.readings[0].q < 0.45
    assert not d.good  # legs scoring under match_min are a weak match


def test_bounds_and_nothing_fits():
    spec = SPEC["taiwan"]
    invade = mk("INV", "China invades Taiwan in 2026?", 0.04, venue="polymarket")
    drills = mk("DR", "China drills around Taiwan in 2026?", 0.70)
    d = decide(spec, [v(invade, "implies", 0.65, deadline="earlier"), v(drills, "overlaps", 0.35)], S)
    # A lower bound of 4% alone leaves a 4%-100% range: too wide, so nothing fits.
    assert d.kind == "none" and d.readings == []
    assert "between 4% and 100%" in d.note
    assert {c.market.id for c in d.near_misses} == {"INV", "DR"}


def test_narrow_bound_is_used():
    spec = SPEC["recession"]
    lo = mk("R26", "Recession by end of 2026", 0.06, venue="polymarket")
    hi = mk("R27", "Recession in 2027", 0.22)
    d = decide(spec, [v(lo, "implies", 0.7, deadline="earlier"), v(hi, "implied_by", 0.7, deadline="later")], S)
    assert d.kind == "bound" and d.lo == pytest.approx(0.06) and d.hi == pytest.approx(0.22)
    assert d.readings[0].q == pytest.approx(0.14)


def test_unrelated_only_is_nothing_fits_with_reason():
    m = mk("X", "Will NVIDIA close below $150?", 0.2)
    d = decide(SPEC["chip-curbs"], [v(m, "unrelated", 0.1, same_entity=False, reason="A stock price is a consequence, not the event.")], S)
    assert d.kind == "none" and "consequence" in d.note


# -- the judge ---------------------------------------------------------------------


class FakeClient:
    def __init__(self, verdicts=None, stop_reason="end_turn"):
        self.calls, self._verdicts, self._stop = [], verdicts, stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kw):
        self.calls.append(kw)
        out = None if self._stop == "refusal" else Judgement(verdicts=self._verdicts)
        return SimpleNamespace(stop_reason=self._stop, parsed_output=out, model=kw["model"], usage=None)


def test_judge_sends_rubric_schema_fallback_and_caches(tmp_path):
    a = mk("K1", "Fed cuts in Dec", 0.6)
    b = mk("K2", "Something else", 0.1)
    client = FakeClient([v(a, "same_question", 1.4).verdict])  # out-of-range score, and K2 left out
    j = Judge(client, model="test-model", log_dir=tmp_path)
    out = j.judge(SPEC["fed-cut-dec"], [a, b], date(2026, 11, 8))
    kw = client.calls[0]
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_format"] is Judgement and kw["thinking"] == {"type": "adaptive"}
    assert "temperature" not in kw
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert out[0].score == 1.0  # clipped to [0, 1]
    assert out[1].relation == "unrelated" and "did not return" in out[1].reason
    # Same request again: served from the log, no new call.
    j.judge(SPEC["fed-cut-dec"], [a, b], date(2026, 11, 8))
    assert len(client.calls) == 1


def test_judge_refusal_raises(tmp_path):
    a = mk("K1", "Fed cuts in Dec", 0.6)
    with pytest.raises(JudgeRefused):
        Judge(FakeClient(stop_reason="refusal"), model="m", log_dir=tmp_path).judge(SPEC["fed-cut-dec"], [a], date(2026, 11, 8))
