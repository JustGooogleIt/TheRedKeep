"""The engine against DESIGN.md sections 3 and 4 (mirrors prototype/check.js)."""

from datetime import date, timedelta

import numpy as np
import pytest

from redkeep.engine.impact import EngineEvent, Link, build_state
from redkeep.engine.simulate import simulate

TODAY = date(2026, 11, 8)

# The worked example in DESIGN.md section 3: $1M of QQQ, tariffs at 40%.
QQQ = np.array([2.4, -0.25, -0.1, -0.15, -0.1, -0.05, -0.05, 0.15, 0.55, 0])
TARIFF = np.array([-2.0753, -0.3, 0.8, 0.4, -0.4, 1.0, 1.5, -2.2, -1.0, 0.6])
RECESSION = np.array([-4.1167, -1.6, -0.6, -0.2, -1.4, 2.2, 2.2, -0.6, -0.8, 0.6])


def example_state(**kw):
    events = [
        EngineEvent("tariff", 0.40, date(2027, 3, 31), TARIFF),
        EngineEvent("recession", 0.28, date(2027, 4, 29), RECESSION),
    ]
    return build_state(QQQ, events, [Link("tariff", "recession", 0.25)], today=TODAY, **kw)


def test_section_3_worked_example():
    e = example_state().events["tariff"]
    assert e.direct == pytest.approx(-6.0, abs=0.05)
    assert e.total == pytest.approx(-8.5, abs=0.05)
    assert e.priced == pytest.approx(-3.4, abs=0.05)
    assert e.if_yes == pytest.approx(-5.1, abs=0.05)
    assert e.if_no == pytest.approx(3.4, abs=0.05)
    assert 0.40 * e.if_yes + 0.60 * e.if_no == pytest.approx(0.0, abs=1e-12)


def test_what_if_moves_children_odds():
    s = example_state(what_if={"tariff": "yes"})
    assert s.events["tariff"].resolved == "yes"
    assert s.events["recession"].q == pytest.approx(0.28 + 0.25 * 0.60)
    # A resolved child no longer passes on a knock-on.
    s2 = example_state(what_if={"recession": "no"})
    assert s2.events["tariff"].total == pytest.approx(s2.events["tariff"].direct)


def test_links_must_run_forward_in_time():
    events = [EngineEvent("a", 0.3, date(2027, 1, 1), TARIFF), EngineEvent("b", 0.3, date(2026, 12, 1), RECESSION)]
    s = build_state(QQQ, events, [Link("a", "b", 0.2)], today=TODAY)
    assert not s.links and s.dropped_links


def random_library(seed, m=12):
    rng = np.random.default_rng(seed)
    events = [
        EngineEvent(f"e{i}", float(rng.uniform(0.02, 0.9)), TODAY + timedelta(days=int(rng.integers(20, 300))),
                    rng.normal(0, 1, 10), p_hat=float(rng.uniform(0.02, 0.9)), order=i)
        for i in range(m)
    ]
    srt = sorted(events, key=lambda e: (e.date, e.order))
    links = []
    for _ in range(m):
        i, j = sorted(rng.choice(m, 2, replace=False))
        links.append(Link(srt[i].id, srt[j].id, float(rng.uniform(-0.3, 0.3))))
    return rng.normal(0, 1.5, 10), events, links


@pytest.mark.parametrize("seed", range(6))
def test_identities_hold_on_random_libraries(seed):
    b, events, links = random_library(seed)
    s = build_state(b, events, links, today=TODAY, horizon_days=200)
    for e in s.events.values():
        # 4.2: the priced-in moves net to zero at market odds.
        assert abs(e.q * e.if_yes + (1 - e.q) * e.if_no) < 1e-9
        # 4.4: knock-on jumps add up through the graph.
        via = sum(ln.w * s.events[ln.child].total for ln in e.children)
        assert abs(e.total - e.direct - via) < 1e-9
        # 4.3: the swing is q(1 - q) times the jump squared.
        assert abs(e.swing_pct**2 - e.swing) < 1e-9


@pytest.mark.parametrize("seed", range(4))
def test_simulation_is_centred_and_repeatable(seed):
    b, events, links = random_library(seed)
    s = build_state(b, events, links, today=TODAY, horizon_days=200)
    a, c = simulate(s, n=20_000), simulate(s, n=20_000)
    assert abs(a.mean) <= 4 * a.sd / np.sqrt(a.n)
    assert (a.mean, a.p5, a.es5) == (c.mean, c.p5, c.es5)
    assert a.p5 <= a.p50 <= a.p95 and a.es5 <= a.p5


def test_simulation_keeps_each_event_at_its_odds():
    # The logistic link's intercept is solved so a child's overall probability matches its odds.
    s = example_state()
    r = simulate(s, n=200_000)
    rec = next(d for d in r.drivers if d.id == "recession")
    assert rec.base == pytest.approx(0.28, abs=0.005)
    tar = next(d for d in r.drivers if d.id == "tariff")
    assert tar.tail > tar.base  # tariffs drive the left tail for QQQ
