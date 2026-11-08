"""Coherence checks (DESIGN.md 6.6, step 6): do related prices obey the rules of probability?

Related markets are tied together by logic, whatever traders think:

* **Ladders.** Of two markets that differ only by threshold, the stricter one can't be likelier (CPI above 3.2% is
  never likelier than CPI above 3.0%). Of two that differ only by deadline, the earlier one can't be likelier.
* **Bounds.** A market whose "yes" implies the event can't be likelier than the event, and one the event implies
  can't be less likely. So the lower bound related markets give can't sit above their upper bound.
* **Exclusive outcomes.** Outcomes of one market group that can't happen together (the Fed's December decision:
  hike, hold, cut 25, cut 50) can't add up to more than one.
* **Links.** A knock-on link of weight ``w = P(B | A) − P(B | not A)`` must leave both conditional odds between 0
  and 1: ``P(B | A) = q_B + (1 − q_A) w`` and ``P(B | not A) = q_B − q_A w``. That is the same as asking that the
  child's odds lie between its two conditional values, and that the joint odds the link implies,
  ``P(A and B) = q_A · P(B | A)``, lie within the Fréchet bounds ``max(0, q_A + q_B − 1) ≤ P(A and B) ≤
  min(q_A, q_B)``.

A quoted price is uncertain by half its bid-ask spread: the true price can be anywhere inside the quote. So a gap
counts only when it is larger than the half-spreads of the prices involved, plus a point for ticks and rounding. A
price that is only a last trade counts as five points either way. Past that, someone could buy one side and sell
the other for a profit whatever happens, so at least one of the prices is probably wrong.

Market violations are flagged as possible mispricings; an event whose own odds are involved becomes tier B, as when
two venues disagree. Links are the exception, since they come from the engine's own regression: a slope outside the
range today's odds allow is capped at the edge of that range, because the engine would otherwise imply odds below 0
or above 1.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Sequence

from .impact import Q_MAX, Q_MIN, clamp

SLACK = 0.01  # a point on top of the half-spreads, for ticks and rounding
NO_QUOTE = 0.05  # how far a price that is only a last trade may be off, either way

Kind = Literal["ladder", "bound", "sum", "link"]


@dataclass(frozen=True)
class Price:
    key: str  # venue:id
    label: str  # how a note names it: the rung, the outcome or the market's title
    q: float
    half_spread: float  # half the bid-ask spread, or NO_QUOTE without a two-sided quote


@dataclass(frozen=True)
class Finding:
    kind: Kind
    gap: float  # how far the prices break the rule, in probability
    allowed: float  # how far the quotes allow them to
    keys: tuple[str, ...]  # the markets involved
    note: str

    @property
    def excess(self) -> float:
        return self.gap - self.allowed

    def json(self) -> dict:
        return {"kind": self.kind, "gap": self.gap, "allowed": self.allowed, "keys": list(self.keys), "note": self.note}


def half_spread(bid: float | None, ask: float | None) -> float:
    if bid is not None and ask is not None and 0 <= bid <= ask <= 1:
        return (ask - bid) / 2
    return NO_QUOTE


def pct(q: float) -> str:
    v = q * 100
    s = f"{abs(v):.0f}%" if abs(v) >= 1 or v == 0 else f"{abs(v):.1f}%"
    return ("−" if v < 0 else "") + s


def slope(w: float) -> str:
    return f"{w:+.2f}".replace("-", "−")


def pts(x: float) -> str:
    return _points(round(x * 100))


def apart(a: float, b: float) -> str:
    """How far ``a`` sits above ``b``, in points rounded the way their percentages show, so a note's numbers add up."""
    return _points(round(a * 100) - round(b * 100)) if min(a, b) >= 0.01 else pts(a - b)


def _points(p: int) -> str:
    return f"{p} point" + ("" if p == 1 else "s")


def worst(found: Sequence[Finding], one: str, many: str) -> list[Finding]:
    """The worst of a check's findings, saying how many more there were. ``one`` and ``many`` name what was checked,
    as in "pair of rungs" and "pairs of rungs"."""
    if not found:
        return []
    top = max(found, key=lambda f: f.excess)
    more = len(found) - 1
    if more:
        top = replace(top, note=f"{top.note} {more} other {one if more == 1 else many} {'is' if more == 1 else 'are'} "
                                f"out of line too.")
    return [top]


def ladder(rungs: Sequence[tuple[float, Price]], *, increasing: bool) -> list[Finding]:
    """Rungs of one ladder by threshold or deadline. With ``increasing`` the odds can only rise along the ladder (a
    later deadline, or a higher "below" threshold); otherwise they can only fall. Reports the worst pair out of order
    by more than the quotes allow."""
    pts_ = sorted(rungs, key=lambda r: r[0])
    found = []
    for i, (_, a) in enumerate(pts_):
        for _, b in pts_[i + 1:]:
            hi, lo = (a, b) if increasing else (b, a)  # hi can't be likelier than lo
            gap, allowed = hi.q - lo.q, hi.half_spread + lo.half_spread + SLACK
            if gap > allowed:
                found.append(Finding("ladder", gap, allowed, (hi.key, lo.key),
                                     f"“{hi.label}” is priced at {pct(hi.q)}, above “{lo.label}” at {pct(lo.q)}, though it "
                                     f"can't be likelier: {apart(hi.q, lo.q)} out of line where the quotes allow {pts(allowed)}."))
    return worst(found, "pair of rungs", "pairs of rungs")


def bounds(q: float, q_allowed: float, *, below: Sequence[Price], above: Sequence[Price]) -> list[Finding]:
    """The event's odds ``q`` (good to ``q_allowed``, its own quotes' half-spreads) against markets that imply the
    event (``below``: none can be likelier than the event) and markets the event implies (``above``: none can be less
    likely). Reports the worst market out of line by more than the quotes allow."""
    found = []
    for m in below:
        gap, allowed = m.q - q, m.half_spread + q_allowed + SLACK
        if gap > allowed:
            found.append(Finding("bound", gap, allowed, (m.key,),
                                 f"“{m.label}” is priced at {pct(m.q)}, above the event's {pct(q)}, though it can only "
                                 f"happen if the event does: {apart(m.q, q)} out of line where the quotes allow {pts(allowed)}."))
    for m in above:
        gap, allowed = q - m.q, m.half_spread + q_allowed + SLACK
        if gap > allowed:
            found.append(Finding("bound", gap, allowed, (m.key,),
                                 f"The event's {pct(q)} is above “{m.label}” at {pct(m.q)}, though the event can only "
                                 f"happen if that does: {apart(q, m.q)} out of line where the quotes allow {pts(allowed)}."))
    return worst(found, "related market", "related markets")


def crossed(lo: Price, hi: Price) -> Finding | None:
    """A lower bound from one related market above the upper bound from another."""
    gap, allowed = lo.q - hi.q, lo.half_spread + hi.half_spread + SLACK
    if gap <= allowed:
        return None
    return Finding("bound", gap, allowed, (lo.key, hi.key),
                   f"“{lo.label}” at {pct(lo.q)} implies the event, and the event implies “{hi.label}” at {pct(hi.q)}, so "
                   f"the first can't be likelier than the second: {apart(lo.q, hi.q)} out of line where the quotes allow "
                   f"{pts(allowed)}.")


def exclusive_sum(prices: Sequence[Price], *, group: str) -> Finding | None:
    """Outcomes of one group that can't happen together can't add up to more than one. (They can add up to less: the
    group may not list every outcome.)"""
    total = sum(p.q for p in prices)
    allowed = sum(p.half_spread for p in prices) + SLACK
    if len(prices) < 2 or total - 1 <= allowed:
        return None
    return Finding("sum", total - 1, allowed, tuple(p.key for p in prices),
                   f"The {len(prices)} outcomes of “{group}” add up to {pct(total)}, though only one can happen: "
                   f"{apart(total, 1.0)} over 100% where the quotes allow {pts(allowed)}.")


def link_range(q_parent: float, q_child: float) -> tuple[float, float]:
    """The weights a link can have while both of the child's conditional odds stay inside [Q_MIN, Q_MAX], the range
    the engine keeps every probability in."""
    qa, qb = clamp(q_parent), clamp(q_child)
    hi = min((Q_MAX - qb) / (1 - qa), (qb - Q_MIN) / qa)
    lo = max((Q_MIN - qb) / (1 - qa), (qb - Q_MAX) / qa)
    return lo, hi


def link(q_parent: float, q_child: float, w: float, *, parent: str, child: str) -> tuple[float, Finding | None]:
    """The link's weight, capped to what the odds allow, and a finding if it had to be."""
    lo, hi = link_range(q_parent, q_child)
    if lo <= w <= hi:
        return w, None
    capped = min(max(w, lo), hi)
    qa, qb = clamp(q_parent), clamp(q_child)
    if_yes, if_no = qb + (1 - qa) * w, qb - qa * w
    note = (f"With the measured slope, {slope(w)}, “{child}” would sit at {pct(if_yes)} if “{parent}” resolves yes and "
            f"{pct(if_no)} if it resolves no. The engine caps the slope at {slope(capped)}, the most today's odds allow.")
    return capped, Finding("link", abs(w - capped), 0.0, (), note)
