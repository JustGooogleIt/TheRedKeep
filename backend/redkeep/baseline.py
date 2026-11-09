"""Eval E9, "just ask an AI": is the engine better than Claude with no tools? (DESIGN.md section 10)

The baseline is the engine's own AI prior (``evidence/prior.py``): one structured call with a question, a date and
the factors' typical moves, and nothing else. No prices, no search, no data. It is the strongest no-tools answer
the engine knows how to ask for, and it is one of the engine's own sources, so the comparison also says what the
market evidence adds to it.

**The model mustn't know how a market ended.** A model knows how the events in its training data turned out, which
is why E3 and E8 leave the prior out. E9 therefore scores only markets that resolved from ``AFTER`` on, a margin
after the training data of the model the engine uses. On those, it answers as a forecaster would have, from what it
knew before. A check asks the same about markets that closed well before its training data ends: if it scores far
better on those, it remembers them, and the cutoff matters.

Two parts, each with the metrics of an earlier eval, fixed here before any run:

* **Jumps (E3's metrics, and E6's ablation).** The jump study's markets (``jumps.py``) that resolved from ``AFTER``
  on. Each has its own jump-window reading from the evening before; Claude estimates the jump as of that evening;
  and the engine fuses the two by precision (6.5), as a refresh would. All three are scored on the same (market,
  factor) pairs against the resolution day's moves: the share explained, the same with no move where the reading
  can't resolve the factor, signs on clear moves, rank correlation and 80% coverage. The differences between their
  shares get 95% ranges from resampling whole resolution days. Also: the scale on the prior's error bars that would
  cover 80% of the realized jumps, which is the first measurement of ``τ_AI``.
* **Odds (E2's metrics).** Resolved markets from the calibration study's saved prices, ``HORIZON`` days before their
  deadline: Claude's probability as of that day against the market's price then, by Brier score and log loss, with
  95% ranges on the differences from resampling whole events. At most two markets of an event are asked about.

E8's metric, portfolio moves, needs exposures, which wait for the Massive key.
"""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Sequence

import numpy as np

from .engine.fusion import DeltaReading, fuse_delta
from .evidence.calibration import Obs, losses, reliability, scores
from .evidence.prior import AIPrior, Prior, delta_reading
from .factors import NF
from .jumps import BOOT, Case, Pairs, explained, fit_scale, pairs, summary
from .matching.judge import JudgeRefused

log = logging.getLogger(__name__)
Progress = Callable[[str], None]

AFTER = date(2026, 8, 1)  # markets that resolved from this day on: a margin after the model's training data
LEAK_GAP = timedelta(days=92)  # the check reads markets that closed this long before AFTER, well inside it
HORIZON = 30  # days before the deadline at which the odds part asks
MOST = 250  # markets the odds part asks about after the cutoff
LEAK = 100  # and before it, as the check
PER_EVENT = 2
CLIP = 0.001  # probabilities are scored inside [0.1%, 99.9%], the market's and Claude's alike
WORKERS = 8


# -- asking -------------------------------------------------------------------------------------------------


def brief(key: str, title: str, category: str, deadline: date | None = None) -> dict:
    """What Claude sees about a market: its question, its category and, where known, its deadline. Never when it
    closed or how."""
    d = {"id": key, "question": " ".join(title.split()), "category": category}
    if deadline is not None:
        d["deadline"] = deadline.isoformat()
    return d


def jump_name(c: Case) -> str:
    return f"jump:{c.key}"


def odds_name(o: Obs) -> str:
    return f"odds:{o.market}@{o.h}"


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def pick_obs(obs: Sequence[Obs], keep: Callable[[Obs], bool], horizon: int, most: int) -> list[Obs]:
    """An even sample by hash of the titled observations at ``horizon`` that ``keep`` accepts, at most
    ``PER_EVENT`` per event."""
    pool = sorted((o for o in obs if o.h == horizon and o.title and o.deadline and keep(o)),
                  key=lambda o: _hash(f"{o.market}|{o.h}"))
    seen: dict[str, int] = {}
    out = []
    for o in pool:
        if len(out) >= most:
            break
        if seen.get(o.event, 0) < PER_EVENT:
            seen[o.event] = seen.get(o.event, 0) + 1
            out.append(o)
    return out


def ask(prior: AIPrior, items: Sequence[tuple[str, dict, date, np.ndarray]], *,
        progress: Progress = print) -> tuple[dict[str, dict], dict[str, int]]:
    """Claude's answer to each ``(name, brief, today, typical)``, as plain dicts. A question it declines, or that
    fails, is counted and left out."""
    def one(item):
        name, b, today, typical = item
        try:
            return name, prior.ask(name, b, today, typical).model_dump(), None
        except JudgeRefused:
            return name, None, "declined"
        except Exception as e:  # count it and keep going; a run where every call fails says so
            log.warning("no answer for %s: %s", name, e)
            return name, None, type(e).__name__

    answers: dict[str, dict] = {}
    missing: dict[str, int] = {}
    with ThreadPoolExecutor(WORKERS) as ex:
        for i, (name, got, why) in enumerate(ex.map(one, items), 1):
            if got is not None:
                answers[name] = got
            else:
                missing[why] = missing.get(why, 0) + 1
            if i % 25 == 0 or i == len(items):
                progress(f"  {i} of {len(items)} asked, {len(answers)} answered")
    if items and not answers:
        raise RuntimeError(f"Claude answered none of the {len(items)} questions: {missing}")
    return answers, missing


# -- jumps ------------------------------------------------------------------------------------------------


def day_ranges(day: np.ndarray, stat: Callable[[np.ndarray], float | None], *, n: int = BOOT,
               seed: int = 0) -> list[float] | None:
    """The 95% range of ``stat`` (a function of row indices) over resamples of whole resolution days."""
    days = np.unique(day)
    if len(days) < 5:
        return None
    idx = {d: np.flatnonzero(day == d) for d in days}
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        v = stat(np.concatenate([idx[d] for d in rng.choice(days, len(days))]))
        if v is not None:
            vals.append(v)
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))] if vals else None


def gap(a: Pairs, b: Pairs) -> dict:
    """``a``'s explained share minus ``b``'s, on the same pairs, with its 95% range over resolution days."""
    def d(m: np.ndarray) -> float | None:
        x, y = explained(a.take(m)), explained(b.take(m))
        return None if x is None or y is None else x - y

    return {"share": d(np.arange(len(a.z))), "range": day_ranges(a.day, d)}


def jump_readings(c: Case, answer: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Claude's jump and error bars; the engine's fusion of them with the market's own reading; and Claude's share
    of the fused precision, per factor."""
    ai = delta_reading(Prior.model_validate(answer))
    market = DeltaReading("etf", "Jump windows", "", np.array([np.nan if v is None else v for v in c.pred], float),
                          np.array([np.inf if v is None else v for v in c.sigma], float))
    f = fuse_delta([market, ai])
    return ai.delta, ai.sigma, f.delta, f.sigma, f.weights.get(ai.name, np.zeros(NF))


def jumps_part(cases: Sequence[Case], answers: dict[str, dict], material: dict[str, bool] | None = None) -> dict:
    asked = [c for c in cases if jump_name(c) in answers]
    if not asked:
        return {"markets": 0}
    A, AS, F, FS, W = (np.zeros((len(asked), NF)) for _ in range(5))
    for i, c in enumerate(asked):
        A[i], AS[i], F[i], FS[i], W[i] = jump_readings(c, answers[jump_name(c)])
    p = pairs(asked)
    by = {"market": p,
          "ai": replace(p, pred=A[p.case, p.f], sigma=AS[p.case, p.f]),
          "engine": replace(p, pred=F[p.case, p.f], sigma=FS[p.case, p.f])}
    out = {
        "markets": len(asked), "days": int(len(np.unique(p.day))), "pairs": int(len(p.z)),
        **{k: summary(v) for k, v in by.items()},
        "gaps": {"engine_minus_ai": gap(by["engine"], by["ai"]), "engine_minus_market": gap(by["engine"], by["market"]),
                 "ai_minus_market": gap(by["ai"], by["market"])},
        "ai_weight": float(W[p.case, p.f].mean()) if len(p.z) else None,
        "ai_error_scale": fit_scale(by["ai"]),
    }
    if material:
        key = np.array([asked[i].key for i in p.case], dtype=object)
        known = np.array([k in material for k in key], bool)
        mat = np.array([material.get(k, False) for k in key], bool)
        out["by_material"] = {
            "material_markets": sum(material.get(c.key, False) for c in asked),
            "material": {k: summary(v.take(mat)) for k, v in by.items()},
            "other": {k: summary(v.take(known & ~mat)) for k, v in by.items()},
        }
    return out


# -- odds -------------------------------------------------------------------------------------------------------


def paired(d: np.ndarray, groups: Sequence[str], *, n: int = BOOT, seed: int = 0) -> dict:
    """The mean of ``d`` with a 95% range from resampling whole groups (events)."""
    keys = sorted(set(groups))
    pos = {k: i for i, k in enumerate(keys)}
    g = np.array([pos[k] for k in groups])
    sums, counts = np.bincount(g, weights=d, minlength=len(keys)), np.bincount(g, minlength=len(keys))
    draws = np.random.default_rng(seed).integers(0, len(keys), size=(n, len(keys)))
    boot = sums[draws].sum(axis=1) / np.maximum(counts[draws].sum(axis=1), 1)
    return {"mean": float(d.mean()), "range": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))]}


def odds_part(obs: Sequence[Obs], answers: dict[str, dict]) -> dict:
    rows = [(o, answers[odds_name(o)]) for o in obs if odds_name(o) in answers]
    if not rows:
        return {"markets": 0}
    q = np.clip([o.q for o, _ in rows], CLIP, 1 - CLIP)
    p = np.clip([float(a["probability"]) for _, a in rows], CLIP, 1 - CLIP)
    y = np.array([o.y for o, _ in rows], float)
    g = [o.event for o, _ in rows]
    return {
        "markets": len(rows), "events": len(set(g)), "yes": int(y.sum()),
        "closed": [min(o.closed for o, _ in rows).isoformat(), max(o.closed for o, _ in rows).isoformat()],
        "market": scores(q, y), "ai": scores(p, y),
        "brier_gap": paired((p - y) ** 2 - (q - y) ** 2, g),  # Claude minus the market: above zero, Claude did worse
        "log_loss_gap": paired(losses(p, y) - losses(q, y), g),
        "ai_closer": float(np.mean(np.abs(p - y) < np.abs(q - y))),
        "reliability": {"market": reliability(q, y, 5), "ai": reliability(p, y, 5)},
    }


# -- the study -----------------------------------------------------------------------------------------------


def study(prior: AIPrior, *, cases: Sequence[Case] = (), typical: Sequence[float | None] | None = None,
          material: dict[str, bool] | None = None, obs: Sequence[Obs] = (), after: date = AFTER,
          horizon: int = HORIZON, most: int = MOST, leak: int = LEAK, sources: dict | None = None,
          progress: Progress = print) -> dict:
    late = [c for c in cases if date.fromisoformat(c.day) >= after]
    post = pick_obs(obs, lambda o: o.closed >= after, horizon, most)
    past = pick_obs(obs, lambda o: o.closed < after - LEAK_GAP, horizon, leak) if leak else []
    t = np.array([np.nan if v is None else float(v) for v in typical]) if typical is not None else np.full(NF, np.nan)
    items = [(jump_name(c), brief(c.key, c.title, c.category), date.fromisoformat(c.before), t) for c in late]
    items += [(odds_name(o), brief(o.market, o.title, o.category, o.deadline), o.deadline - timedelta(days=o.h),
               np.full(NF, np.nan)) for o in post + past]
    progress(f"Claude, with no tools, answers {len(items)} questions: {len(late)} jumps, "
             f"{len(post)} odds after its training data and {len(past)} from before it, as a check")
    answers, missing = ask(prior, items, progress=progress)
    return report(late, post, past, answers, after=after, horizon=horizon, typical=typical, material=material,
                  sources=sources or {}, usage=prior.usage(), missing=missing)


def report(cases: Sequence[Case], post: Sequence[Obs], past: Sequence[Obs], answers: dict[str, dict], *,
           after: date, horizon: int, typical=None, material: dict[str, bool] | None = None, sources: dict,
           usage: dict | None = None, missing: dict | None = None) -> dict:
    return {
        "kind": "redkeep.baseline", "version": 1, "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": sources,
        "method": {"after": after.isoformat(), "leak_before": (after - LEAK_GAP).isoformat(), "horizon": horizon,
                   "per_event": PER_EVENT, "clip": CLIP, "boot": BOOT},
        "usage": usage, "not_answered": missing or {},
        "jumps": jumps_part(cases, answers, material),
        "odds": {"after": odds_part(post, answers), "before": odds_part(past, answers)},
        "portfolios": "Not run: portfolio moves need exposures, which wait for the Massive key.",
        "rows": {"cases": [asdict(c) for c in cases],
                 "post": [_obs_json(o) for o in post], "past": [_obs_json(o) for o in past],
                 "answers": answers, "material": material, "typical": typical},
    }


def rescore(rep: dict) -> dict:
    """A saved run scored again from its rows, without asking."""
    rows, m = rep["rows"], rep["method"]
    return report([Case(**c) for c in rows["cases"]], [_obs(o) for o in rows["post"]], [_obs(o) for o in rows["past"]],
                  rows["answers"], after=date.fromisoformat(m["after"]), horizon=m["horizon"],
                  typical=rows.get("typical"), material=rows.get("material"),
                  sources={**rep["sources"], "rescore_of": rep["as_of"]}, usage=rep.get("usage"),
                  missing=rep.get("not_answered"))


def _obs_json(o: Obs) -> dict:
    return {**asdict(o), "closed": o.closed.isoformat(), "deadline": o.deadline.isoformat() if o.deadline else None}


def _obs(d: dict) -> Obs:
    return Obs(**{**d, "closed": date.fromisoformat(d["closed"]),
                  "deadline": date.fromisoformat(d["deadline"]) if d.get("deadline") else None})
