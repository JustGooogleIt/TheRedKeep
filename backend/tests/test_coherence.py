"""Coherence checks (DESIGN.md 6.6, step 6): the rules themselves, then the decisions and the snapshot that use them."""

import itertools
import random
from dataclasses import replace

import pytest

from redkeep.config import Settings
from redkeep.engine import coherence
from redkeep.engine.coherence import Finding, Price
from redkeep.engine.fusion import Tier
from redkeep.engine.impact import Q_MAX, Q_MIN
from redkeep.events import LinkSpec, load_library
from redkeep.matching.decide import decide
from redkeep.pipeline import CONTESTED, contest, link_record, market_coherence

from .test_matching import mk, v

S = Settings()
SPEC = load_library().by_id()


def px(key: str, q: float, half: float = 0.01) -> Price:
    return Price(key, key, q, half)


# -- the rules ------------------------------------------------------------------------


def test_a_stricter_rung_priced_above_a_looser_one_is_flagged():
    # Above 3.0%, 3.2% and 3.4%: the odds can only fall as the threshold rises.
    [f] = coherence.ladder([(3.0, px("a", 0.40)), (3.2, px("b", 0.45)), (3.4, px("c", 0.10))], increasing=False)
    assert f.kind == "ladder" and f.keys == ("b", "a")
    assert f.gap == pytest.approx(0.05) and f.allowed == pytest.approx(0.03)  # two half-spreads and a point
    assert f.note.startswith("“b” is priced at 45%, above “a” at 40%, though it can't be likelier")
    assert f.note.endswith("5 points out of line where the quotes allow 3 points.")
    # Within the quotes it passes: the true prices could still be in order.
    assert coherence.ladder([(3.0, px("a", 0.40)), (3.2, px("b", 0.42))], increasing=False) == []


def test_an_earlier_deadline_cant_be_likelier_and_the_worst_pair_is_reported():
    rungs = [(0, px("dec", 0.50)), (365, px("jun", 0.30)), (730, px("dec27", 0.20))]  # in days
    [f] = coherence.ladder(rungs, increasing=True)
    assert f.keys == ("dec", "dec27") and f.gap == pytest.approx(0.30)
    assert f.note.endswith("2 other pairs of rungs are out of line too.")
    assert coherence.ladder(rungs, increasing=False) == []  # the same falling odds are right on a ladder that must fall


def test_worst_says_how_many_more():
    fs = [Finding("ladder", g, 0.03, (), "Off.") for g in (0.05, 0.20, 0.10)]
    [top] = coherence.worst(fs, "pair of rungs", "pairs of rungs")
    assert top.gap == 0.20 and top.note == "Off. 2 other pairs of rungs are out of line too."
    assert coherence.worst(fs[:2], "pair of rungs", "pairs of rungs")[0].note == "Off. 1 other pair of rungs is out of line too."
    assert coherence.worst(fs[:1], "x", "xs")[0].note == "Off."
    assert coherence.worst([], "x", "xs") == []


def test_related_markets_bound_the_event():
    # The event at 30%, its own quotes good to a point either way.
    [f] = coherence.bounds(0.30, 0.01, below=[px("strict", 0.40), px("fine", 0.31)], above=[])
    assert f.kind == "bound" and f.keys == ("strict",) and f.gap == pytest.approx(0.10)
    assert "can only happen if the event does" in f.note
    [g] = coherence.bounds(0.30, 0.01, below=[], above=[px("loose", 0.20)])
    assert g.keys == ("loose",) and "the event can only happen if that does" in g.note
    assert coherence.bounds(0.30, 0.01, below=[px("a", 0.32)], above=[px("b", 0.28)]) == []



def test_a_note_never_rounds_a_violation_into_the_allowance():
    # A real case (Bank of Japan, 2026-11-08): 3.44 points out of line where 3.30 are allowed. In whole points both
    # are 3, which reads as within the quotes, so the note switches to one decimal, prices included.
    [f] = coherence.bounds(0.749, 0.013, below=[], above=[px("hold-no", 0.715)])
    assert f.note == ("The event's 74.9% is above “hold-no” at 71.5%, though the event can only happen if that does: "
                      "3.4 points out of line where the quotes allow 3.3 points.")
    # Two decimals when one isn't enough.
    [f] = coherence.bounds(0.7484, 0.013, below=[], above=[px("hold-no", 0.715)])
    assert f.note == ("The event's 74.84% is above “hold-no” at 71.50%, though the event can only happen if that does: "
                      "3.34 points out of line where the quotes allow 3.30 points.")
    # Whole points whenever they show the excess, as before.
    f = coherence.exclusive_sum([px("a", 0.6), px("b", 0.5)], group="g")
    assert f.note.endswith("add up to 110%, though only one can happen: 10 points over 100% where the quotes allow "
                           "3 points.")

def test_a_lower_bound_cant_sit_above_an_upper_one():
    f = coherence.crossed(px("lo", 0.30), px("hi", 0.20))
    assert f.kind == "bound" and f.keys == ("lo", "hi") and f.gap == pytest.approx(0.10)
    assert coherence.crossed(px("lo", 0.20), px("hi", 0.30)) is None
    assert coherence.crossed(px("lo", 0.22), px("hi", 0.20)) is None  # within the quotes


def test_exclusive_outcomes_cant_add_past_one():
    fed = [px("hike", 0.10), px("hold", 0.55), px("cut25", 0.40), px("cut50", 0.05)]
    f = coherence.exclusive_sum(fed, group="Fed decision in December")
    assert f.kind == "sum" and f.gap == pytest.approx(0.10) and f.allowed == pytest.approx(0.05)
    assert f.note.startswith("The 4 outcomes of “Fed decision in December” add up to 110%, though only one can happen")
    assert coherence.exclusive_sum(fed[1:], group="g") is None  # exactly 100%
    assert coherence.exclusive_sum(fed[:2], group="g") is None  # less is fine: the group may not list every outcome


def test_a_sum_is_flagged_exactly_when_selling_every_outcome_at_its_bid_would_pay():
    """Σ mid − 1 > Σ half-spread + slack is Σ bid > 1 + slack: someone could sell every outcome, collect more than
    the one dollar they can owe, and profit whatever happens."""
    rng = random.Random(7)
    flagged = 0
    for _ in range(2000):
        bids = [rng.uniform(0, 0.5) for _ in range(rng.randint(2, 6))]
        asks = [b + rng.uniform(0, 0.1) for b in bids]
        if abs(sum(bids) - 1 - coherence.SLACK) < 1e-9:
            continue
        prices = [Price(str(i), str(i), (b + a) / 2, coherence.half_spread(b, a)) for i, (b, a) in enumerate(zip(bids, asks))]
        hit = coherence.exclusive_sum(prices, group="g") is not None
        assert hit == (sum(bids) > 1 + coherence.SLACK)
        flagged += hit
    assert 200 < flagged < 1800  # both cases were tried


def test_a_link_must_leave_both_conditional_odds_between_0_and_1():
    lo, hi = coherence.link_range(0.30, 0.20)
    assert hi == pytest.approx((0.20 - Q_MIN) / 0.30) and lo == pytest.approx((Q_MIN - 0.20) / 0.70)
    assert coherence.link(0.30, 0.20, 0.50, parent="A", child="B") == (0.50, None)
    w, f = coherence.link(0.30, 0.20, 0.90, parent="strikes resume", child="Hormuz reopens")
    assert w == pytest.approx(hi) and f.kind == "link" and f.gap == pytest.approx(0.90 - hi)
    assert f.note == ("With the measured slope, +0.90, “Hormuz reopens” would sit at 83% if “strikes resume” resolves yes "
                      "and −7% if it resolves no. The engine caps the slope at +0.65, the most today's odds allow.")
    for w in (lo, hi):  # at either cap, P(B | A) and P(B | not A) stay inside the engine's range
        assert Q_MIN - 1e-12 <= 0.20 + 0.70 * w <= Q_MAX + 1e-12
        assert Q_MIN - 1e-12 <= 0.20 - 0.30 * w <= Q_MAX + 1e-12


def test_the_link_range_is_exactly_the_frechet_bounds(monkeypatch):
    """With probabilities allowed all the way to 0 and 1, the caps put P(A and B) = q_A · P(B | A) exactly on the
    Fréchet bounds: max(0, q_A + q_B − 1) at the lower cap, min(q_A, q_B) at the upper."""
    monkeypatch.setattr(coherence, "Q_MIN", 0.0)
    monkeypatch.setattr(coherence, "Q_MAX", 1.0)
    for qa, qb in itertools.product((0.05, 0.3, 0.5, 0.8, 0.97), repeat=2):
        lo, hi = coherence.link_range(qa, qb)
        assert qa * (qb + (1 - qa) * lo) == pytest.approx(max(0.0, qa + qb - 1), abs=1e-12)
        assert qa * (qb + (1 - qa) * hi) == pytest.approx(min(qa, qb), abs=1e-12)


# -- in the decisions -------------------------------------------------------------------


def test_a_ladder_decision_flags_rungs_out_of_order():
    spec = SPEC["cpi-hot"]  # core CPI up 0.4% or more
    r30 = mk("CPI-0.30", "Core CPI above 0.3%", 0.45, event="CPI")
    r35 = mk("CPI-0.35", "Core CPI above 0.35%", 0.50, event="CPI")  # stricter than 0.3%, yet priced higher
    r50 = mk("CPI-0.50", "Core CPI above 0.5%", 0.08, event="CPI")
    d = decide(spec, [v(r30, "implied_by", 0.75, threshold="looser", market_threshold=0.30),
                      v(r35, "implied_by", 0.75, threshold="looser", market_threshold=0.35),
                      v(r50, "implies", 0.75, threshold="stricter", market_threshold=0.50)], S)
    assert d.kind == "ladder" and d.checks == 3
    [f] = d.coherence
    assert f.keys == ("kalshi:CPI-0.35", "kalshi:CPI-0.30") and f.gap == pytest.approx(0.05)


def test_a_bound_decision_flags_crossed_bounds():
    spec = SPEC["recession"]
    ok = decide(spec, [v(mk("R26", "Recession by end of 2026", 0.06, venue="polymarket"), "implies", 0.7, deadline="earlier"),
                       v(mk("R27", "Recession in 2027", 0.22), "implied_by", 0.7, deadline="later")], S)
    assert ok.kind == "bound" and ok.coherence == [] and ok.checks == 1
    bad = decide(spec, [v(mk("R26", "Recession by end of 2026", 0.30, venue="polymarket"), "implies", 0.7, deadline="earlier"),
                        v(mk("R27", "Recession in 2027", 0.20), "implied_by", 0.7, deadline="later")], S)
    assert bad.kind == "bound" and "contradict each other" in bad.note
    [f] = bad.coherence
    assert f.keys == ("polymarket:R26", "kalshi:R27") and f.gap == pytest.approx(0.10)


# -- in the snapshot ---------------------------------------------------------------------


def fed_group(hold: float):
    """Kalshi's December decision as a group of exclusive outcomes, two of which make up the event."""
    outs = [mk("H25", "Hike 25bps", 0.60, outcome="Hike 25bps", exclusive=True),
            mk("H50", "Hike >25bps", 0.02, outcome="Hike >25bps", exclusive=True, volume=100_000),
            mk("H0", "Hold", hold, outcome="Hold", exclusive=True)]
    outs = [replace(m, event_title="Fed decision in December") for m in outs]
    cands = [v(outs[0], "part_of", 0.9), v(outs[1], "part_of", 0.9), v(outs[2], "overlaps", 0.3)]
    return cands, {("kalshi", "G"): outs}


def test_outcomes_adding_past_one_contest_the_events_odds():
    cands, groups = fed_group(hold=0.48)
    d = decide(SPEC["fed-hike-dec"], cands, S)
    found, checks = market_coherence(d, cands, d.readings[0].q, groups, S)
    assert d.kind == "match" and checks == 1
    [f] = found
    assert f.kind == "sum" and f.gap == pytest.approx(0.10) and f.allowed == pytest.approx(0.04)
    assert "The 3 outcomes of “Fed decision in December” add up to 110%" in f.note
    # The event's own odds rest on the sum, so a tier A or C event becomes B, keeping its badges.
    tier, mine = contest(Tier("A", "Well matched.", ["odds cross-checked"]), d, found)
    assert mine and tier == Tier("B", CONTESTED, ["odds cross-checked"])
    assert contest(Tier("C", "Thin."), d, found)[0].tier == "B"
    assert contest(Tier("B", "Venues disagree."), d, found)[0].reason == "Venues disagree."
    # A ladder pair the odds don't rest on is only a possible mispricing.
    far = Finding("ladder", 0.10, 0.03, ("kalshi:X", "kalshi:Y"), "Off.")
    assert contest(Tier("A", "Well matched."), d, [far]) == (Tier("A", "Well matched."), False)

    cands, groups = fed_group(hold=0.38)  # 100%
    d = decide(SPEC["fed-hike-dec"], cands, S)
    assert market_coherence(d, cands, d.readings[0].q, groups, S) == ([], 1)


def test_a_related_market_priced_above_the_event_it_implies_is_flagged():
    same = mk("FED", "Fed hikes in December", 0.30)
    bigger = mk("FED50", "Fed hikes 50bps in December", 0.40, event="G2")  # implies the event
    cands = [v(same, "same_question", 0.95), v(bigger, "implies", 0.8)]
    d = decide(SPEC["fed-hike-dec"], cands, S)
    found, checks = market_coherence(d, cands, 0.30, {}, S)
    assert d.kind == "match" and checks == 1
    [f] = found
    assert f.kind == "bound" and f.keys == ("kalshi:FED50",) and f.allowed == pytest.approx(0.03)
    assert contest(Tier("A", "Well matched."), d, found)[1]


def test_a_confirmed_link_is_capped_at_what_todays_odds_allow():
    ln = LinkSpec("israel-iran", "hormuz-reopens", -0.05, "Renewed strikes push a reopening out.")
    odds = {"israel-iran": (0.30, "Strikes resume"), "hormuz-reopens": (0.20, "Hormuz reopens")}
    chk = {"w_data": -0.90, "se": 0.1, "t": -9.0, "n": 120}
    rec = link_record(ln, chk, odds)
    lo, _ = coherence.link_range(0.30, 0.20)
    assert rec["status"] == "confirmed" and rec["used"] and rec["w"] == pytest.approx(lo)
    assert rec["w_data"] == -0.90 and "capped at what today's odds allow" in rec["note"]
    assert rec["capped"].startswith("With the measured slope, −0.90, “Hormuz reopens” would sit at −43% if “Strikes resume” "
                                    "resolves yes and 47% if it resolves no.")
    fits = link_record(ln, {**chk, "w_data": -0.20, "t": -4.0}, odds)
    assert fits["w"] == -0.20 and "capped" not in fits
    assert link_record(ln, None, odds)["status"] == "untestable"
    assert link_record(ln, {**chk, "t": 1.0}, odds)["used"] is False  # the data points the other way
