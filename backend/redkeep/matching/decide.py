"""From verdicts to odds, or to "nothing fits" (DESIGN.md 6.3 steps 3 to 6).

In order of preference, per venue:

1. **Match**: a "same question" market scoring at least ``match_min``, or a
   complete set of mutually exclusive outcomes whose union is the event (their
   odds are summed).
2. **Ladder**: rungs of one market group that differ from the event only by
   threshold or deadline, interpolated to the event's exact terms.
3. **Bound**: stricter markets give a lower bound and looser ones an upper
   bound; usable when the range is at most ``max_bound_width`` wide.
4. **Nothing fits**: the event has no market and becomes tier D. The closest
   near-misses are kept with the judge's reason for rejecting them.

Along the way, the related prices are checked for coherence (``engine/coherence.py``):
a ladder's rungs must rise or fall in order, and a lower bound can't sit above an
upper one. The pipeline adds the checks that need the fused odds or the whole
catalogue.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from ..config import Settings
from ..engine import coherence
from ..engine.fusion import OddsReading
from ..events import EventSpec
from ..evidence.odds import VENUE_NAMES, describe_liquidity, event_side, market_sigma
from ..markets import Market
from . import ladders
from .judge import Verdict

EPOCH = date(2000, 1, 1)


@dataclass
class Candidate:
    market: Market
    verdict: Verdict
    retrieval: float = 0.0

    @property
    def q(self) -> float | None:
        return event_side(self.market, self.verdict.polarity)

    @property
    def sigma(self) -> float:
        return market_sigma(self.market.spread, self.market.volume)


@dataclass
class Decision:
    kind: Literal["match", "ladder", "bound", "none"]
    readings: list[OddsReading]
    used: list[Candidate]
    near_misses: list[Candidate]
    note: str
    good: bool  # well matched: eligible for tier A
    lo: float | None = None
    hi: float | None = None
    ladders: list[dict] = field(default_factory=list)
    coherence: list[coherence.Finding] = field(default_factory=list)  # related prices that break the rules
    checks: int = 0  # how many comparisons the coherence checks made


@dataclass
class Ladder:
    result: ladders.LadderResult
    legs: list[Candidate]  # the rungs the estimate rests on
    rungs: list[tuple[float, coherence.Price]]  # every rung, for the coherence check
    increasing: bool


def price(c: Candidate) -> coherence.Price:
    """A candidate's event-side odds, as the coherence checks read them."""
    m = c.market
    label = ("No: " if c.verdict.polarity == "no" else "") + (m.outcome or m.title)
    return coherence.Price(m.key, label, c.q, coherence.half_spread(m.bid, m.ask))


def related(cands: list[Candidate], s: Settings) -> tuple[list[Candidate], list[Candidate]]:
    """Markets that imply the event (lower bounds) and markets the event implies (upper bounds)."""
    lo = [c for c in cands if c.verdict.relation == "implies" and c.verdict.score >= s.leg_min]
    hi = [c for c in cands if c.verdict.relation == "implied_by" and c.verdict.score >= s.leg_min]
    return lo, hi


def _venue(v: str) -> str:
    return VENUE_NAMES.get(v, v)


def _pct(q: float) -> str:
    return f"{q * 100:.0f}%" if q >= 0.01 else f"{q * 100:.1f}%"


def _near_misses(cands: list[Candidate], used: list[Candidate], n: int = 2) -> list[Candidate]:
    used_groups = {(c.market.venue, c.market.event_id) for c in used}
    rest = [c for c in cands if (c.market.venue, c.market.event_id) not in used_groups]
    rest.sort(key=lambda c: (c.verdict.same_entity, c.verdict.relation != "unrelated", c.verdict.score), reverse=True)
    out, groups = [], set()
    for c in rest:  # one per market group, so a ladder doesn't fill the list
        g = (c.market.venue, c.market.event_id)
        if g in groups:
            continue
        groups.add(g)
        out.append(c)
        if len(out) == n:
            break
    return out


def _direct(cands: list[Candidate], s: Settings) -> tuple[list[OddsReading], list[Candidate]]:
    readings, used = [], []
    for venue in ("kalshi", "polymarket"):
        vc = [c for c in cands if c.market.venue == venue]
        same = [c for c in vc if c.verdict.relation == "same_question" and c.verdict.score >= s.match_min]
        if same:
            c = max(same, key=lambda c: (c.verdict.score, c.market.volume))
            readings.append(OddsReading(_venue(venue), c.q, c.sigma, "market", describe_liquidity(c.market)))
            used.append(c)
            continue
        groups: dict[str, list[Candidate]] = defaultdict(list)
        for c in vc:
            if c.verdict.relation == "part_of" and c.market.exclusive:
                groups[c.market.event_id].append(c)
        full = [g for g in groups.values() if min(c.verdict.score for c in g) >= s.match_min]
        if full:
            g = max(full, key=lambda g: sum(c.market.volume for c in g))
            q = min(1.0, sum(c.q for c in g))
            sigma = math.sqrt(sum(c.sigma**2 for c in g))
            names = " + ".join(c.market.outcome or c.market.title for c in g)
            readings.append(OddsReading(f"{_venue(venue)} (sum)", q, sigma, "sum", f"{names}: {_pct(q)}"))
            used.extend(g)
    return readings, used


def _ladders(spec: EventSpec, cands: list[Candidate], s: Settings) -> list[Ladder]:
    out = []
    groups: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for c in cands:
        if c.verdict.score >= s.leg_min and c.verdict.relation in ("same_question", "implies", "implied_by"):
            groups[(c.market.venue, c.market.event_id)].append(c)
    for legs in groups.values():
        by_key = {c.market.key: c for c in legs}
        if spec.threshold is not None:
            pts = [ladders.LadderPoint(c.verdict.market_threshold, c.q, c.sigma, c.market.key) for c in legs
                   if c.verdict.market_threshold is not None and c.verdict.deadline == "same"]
            if len(pts) >= 2:
                up = spec.direction == "below"
                r = ladders.estimate(pts, spec.threshold, increasing=up, space="logit")
                out.append(Ladder(r, [by_key[k] for k in r.legs], [(p.x, price(by_key[p.key])) for p in pts], up))
                continue
        pts = []
        for c in legs:
            if c.verdict.market_deadline and c.verdict.threshold in ("equal", "not_applicable"):
                try:
                    d = date.fromisoformat(c.verdict.market_deadline[:10])
                except ValueError:
                    continue
                pts.append(ladders.LadderPoint(float((d - EPOCH).days), c.q, c.sigma, c.market.key))
        if len(pts) >= 2:
            r = ladders.estimate(pts, float((spec.deadline - EPOCH).days), increasing=True, space="hazard")
            out.append(Ladder(r, [by_key[k] for k in r.legs], [(p.x, price(by_key[p.key])) for p in pts], True))
    return out


def _check_ladders(lads: list[Ladder]) -> tuple[list[coherence.Finding], int]:
    found = [f for lad in lads for f in coherence.ladder(lad.rungs, increasing=lad.increasing)]
    return found, sum(len(lad.rungs) * (len(lad.rungs) - 1) // 2 for lad in lads)


def decide(spec: EventSpec, cands: list[Candidate], s: Settings) -> Decision:
    ok = [c for c in cands if c.verdict.same_entity and c.verdict.relation != "unrelated" and c.q is not None]

    readings, used = _direct(ok, s)
    if readings:
        return Decision("match", readings, used, _near_misses(cands, used), "Matched to a market asking the same question.", True)

    lads = _ladders(spec, ok, s)
    inter = [lad for lad in lads if lad.result.kind == "interpolated"]
    if inter:
        readings, used, info, chosen = [], [], [], []
        for venue in ("kalshi", "polymarket"):
            best = [lad for lad in inter if lad.legs[0].market.venue == venue]
            if not best:
                continue
            lad = min(best, key=lambda x: x.result.sigma)
            r, legs = lad.result, lad.legs
            rungs = " · ".join(f"{c.market.outcome or c.market.title} {_pct(c.q)}" for c in legs)
            readings.append(OddsReading(f"{_venue(venue)} ladder", r.q, r.sigma, "ladder", rungs))
            used.extend(legs)
            chosen.append(lad)
            info.append({"venue": venue, "fitted": r.fitted, "note": r.note})
        good = min(c.verdict.score for c in used) >= s.match_min
        found, checks = _check_ladders(chosen)
        return Decision("ladder", readings, used, _near_misses(cands, used), "Interpolated along a ladder of related markets.",
                        good, ladders=info, coherence=found, checks=checks)

    lo_c, hi_c = related(ok, s)
    lo = max([c.q for c in lo_c] + [lad.result.lo for lad in lads], default=0.0)
    hi = min([c.q for c in hi_c] + [lad.result.hi for lad in lads], default=1.0)
    used = lo_c + hi_c + [c for lad in lads for c in lad.legs]
    if (lo > 0 or hi < 1) and abs(hi - lo) <= s.max_bound_width:
        a, b = min(lo, hi), max(lo, hi)
        note = f"Only bounded by related markets: between {_pct(a)} and {_pct(b)}."
        if lo > hi:
            note = f"Related markets contradict each other: a lower bound of {_pct(lo)} sits above an upper bound of {_pct(hi)}."
        r = OddsReading("Bound", (a + b) / 2, max((b - a) / math.sqrt(12), 0.01), "bound", note, lo=a, hi=b)
        found, checks = _check_ladders(lads)
        crossed = [f for x in lo_c for y in hi_c if (f := coherence.crossed(price(x), price(y)))]
        found += coherence.worst(crossed, "pair of bounds", "pairs of bounds")
        checks += len(lo_c) * len(hi_c)
        return Decision("bound", [r], used, _near_misses(cands, used), note, False, lo=a, hi=b,
                        coherence=sorted(found, key=lambda f: f.excess, reverse=True), checks=checks)

    misses = _near_misses(cands, [])
    if lo > 0 or hi < 1:
        note = f"Nothing fits: related markets only say the odds are between {_pct(lo)} and {_pct(hi)}, too wide to use."
    elif misses:
        m = misses[0]
        note = f"Nothing fits. Closest: “{m.market.title}” on {_venue(m.market.venue)}. {m.verdict.reason}"
    else:
        note = "Nothing fits: no market on either venue is about this event."
    return Decision("none", [], [], misses, note, False, lo=lo if lo > 0 else None, hi=hi if hi < 1 else None)
