"""Impact, priced-in moves, swing and knock-on effects (DESIGN.md 4.1 to 4.4).

Everything here is portfolio math over precomputed event data, so it is pure
and fast: no I/O, no randomness.

* ``Δ_e = b · δ_e``: the event's direct jump on the portfolio, in %.
* ``Δ_A_total = Δ_A + Σ_B w_AB · Δ_B_total`` over the event graph, computed
  in reverse resolution order (links only run from earlier to later events).
* At odds ``q``: if the event happens the portfolio moves ``(1 − q) Δ``, if it
  doesn't it moves ``−q Δ``, and ``q Δ`` is already priced in.
* ``swing = q (1 − q) Δ²``, shown as ``|Δ| √(q (1 − q))``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Literal, Mapping, Sequence

import numpy as np

from ..factors import FACTORS, NF

Q_MIN, Q_MAX = 0.005, 0.995


@dataclass(frozen=True)
class EngineEvent:
    """What the engine needs to know about one library event."""

    id: str
    q: float  # the odds the market is pricing
    date: date  # when it resolves
    delta: np.ndarray  # jump vector, in typical moves of each factor
    p_hat: float | None = None  # calibrated odds; defaults to q
    order: int = 0  # tie-break for events resolving on the same day


@dataclass(frozen=True)
class Link:
    """A knock-on edge: w = P(child | parent) − P(child | not parent)."""

    parent: str
    child: str
    w: float


@dataclass
class Contribution:
    factor: str
    name: str
    move: float  # typical moves
    exposure: float  # % per typical move
    pct: float  # exposure × move, in % of the portfolio


@dataclass
class EventState:
    id: str
    q0: float
    q: float  # after what-ifs propagate through the graph
    p_hat: float
    shift: float
    resolved: Literal["yes", "no"] | None
    direct: float  # Δ_direct, %
    contributions: list[Contribution]
    knock: list[tuple[str, float, float]]  # (child, w, w × Δ_child_total)
    total: float  # Δ_total, %
    priced: float
    if_yes: float
    if_no: float
    swing: float  # q (1 − q) Δ², in %²
    swing_pct: float  # |Δ| √(q (1 − q)), in %
    edge: float  # (p̂ − q) Δ: our expected P&L where we think the market is wrong
    days: int
    in_horizon: bool
    parents: list[Link] = field(default_factory=list)
    children: list[Link] = field(default_factory=list)


@dataclass
class State:
    events: dict[str, EventState]
    order: list[str]  # resolution order, earliest first
    links: list[Link]
    dropped_links: list[tuple[Link, str]]
    what_if_move: float  # immediate portfolio move from the what-ifs, in %

    def ranked(self, in_horizon_only: bool = True) -> list[EventState]:
        evs = [e for e in self.events.values() if not e.resolved and (e.in_horizon or not in_horizon_only)]
        return sorted(evs, key=lambda e: e.swing, reverse=True)


def clamp(x: float, lo: float = Q_MIN, hi: float = Q_MAX) -> float:
    return max(lo, min(hi, x))


def contributions(b: np.ndarray, delta: np.ndarray, threshold: float = 0.004) -> list[Contribution]:
    out = [
        Contribution(f.key, f.name, float(delta[k]), float(b[k]), float(b[k] * delta[k]))
        for k, f in enumerate(FACTORS)
        if abs(b[k] * delta[k]) > threshold
    ]
    return sorted(out, key=lambda c: abs(c.pct), reverse=True)


def outcomes(q: float, total: float) -> dict[str, float]:
    return {
        "priced": q * total,
        "if_yes": (1 - q) * total,
        "if_no": -q * total,
        "swing": q * (1 - q) * total * total,
        "swing_pct": abs(total) * (q * (1 - q)) ** 0.5,
    }


def resolution_order(events: Iterable[EngineEvent]) -> list[EngineEvent]:
    return sorted(events, key=lambda e: (e.date, e.order))


def validate_links(events: Mapping[str, EngineEvent], links: Sequence[Link]) -> tuple[list[Link], list[tuple[Link, str]]]:
    """Keep links that point from an earlier event to a later one (DESIGN.md 6.6)."""
    pos = {e.id: i for i, e in enumerate(resolution_order(events.values()))}
    kept, dropped = [], []
    for ln in links:
        if ln.parent not in events or ln.child not in events:
            dropped.append((ln, "unknown event"))
        elif pos[ln.parent] >= pos[ln.child]:
            dropped.append((ln, "would run from a later event to an earlier one"))
        else:
            kept.append(ln)
    return kept, dropped


def build_state(
    b: np.ndarray,
    events: Sequence[EngineEvent],
    links: Sequence[Link] = (),
    *,
    today: date,
    horizon_days: float = 6 * 30.44,
    what_if: Mapping[str, Literal["yes", "no"]] | None = None,
) -> State:
    """Odds, jumps, knock-ons, priced-in moves and swing for one portfolio."""
    b = np.asarray(b, dtype=float)
    if b.shape != (NF,):
        raise ValueError(f"exposure must have {NF} factors, got {b.shape}")
    by_id = {e.id: e for e in events}
    if len(by_id) != len(events):
        raise ValueError("duplicate event ids")
    kept, dropped = validate_links(by_id, links)
    order = [e.id for e in resolution_order(events)]
    parents: dict[str, list[Link]] = {i: [] for i in by_id}
    children: dict[str, list[Link]] = {i: [] for i in by_id}
    for ln in kept:
        parents[ln.child].append(ln)
        children[ln.parent].append(ln)
    what_if = dict(what_if or {})

    # Odds: what-ifs resolve events, and their news moves the children's odds.
    q0, q_eff, shift, resolved = {}, {}, {}, {}
    for i in order:
        e = by_id[i]
        q0[i] = clamp(e.q)
        if i in what_if:
            resolved[i] = what_if[i]
            q_eff[i] = 1.0 if what_if[i] == "yes" else 0.0
        else:
            resolved[i] = None
            s = sum(ln.w * (q_eff[ln.parent] - q0[ln.parent]) for ln in parents[i])
            q_eff[i] = clamp(q0[i] + s)
        shift[i] = q_eff[i] - q0[i]

    # Jumps: direct, then totals in reverse order so children are done first.
    direct = {i: float(b @ by_id[i].delta) for i in order}
    total: dict[str, float] = {}
    knock: dict[str, list[tuple[str, float, float]]] = {}
    for i in reversed(order):
        knock[i] = [(ln.child, ln.w, 0.0 if resolved[ln.child] else ln.w * total[ln.child]) for ln in children[i]]
        total[i] = direct[i] + sum(k[2] for k in knock[i])

    out: dict[str, EventState] = {}
    for i in order:
        e = by_id[i]
        q = q_eff[i]
        p_hat0 = clamp(e.p_hat if e.p_hat is not None else e.q)
        p_hat = q if resolved[i] else clamp(p_hat0 + shift[i])
        days = (e.date - today).days
        o = outcomes(q, total[i])
        out[i] = EventState(
            id=i, q0=q0[i], q=q, p_hat=p_hat, shift=shift[i], resolved=resolved[i],
            direct=direct[i], contributions=contributions(b, e.delta), knock=knock[i], total=total[i],
            priced=o["priced"], if_yes=o["if_yes"], if_no=o["if_no"], swing=o["swing"], swing_pct=o["swing_pct"],
            edge=(p_hat - q) * total[i], days=days, in_horizon=days <= horizon_days,
            parents=parents[i], children=children[i],
        )
    what_if_move = sum(direct[i] * (q_eff[i] - q0[i]) for i in order)
    return State(events=out, order=order, links=kept, dropped_links=dropped, what_if_move=what_if_move)
