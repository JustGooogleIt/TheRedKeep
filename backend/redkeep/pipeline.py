"""End to end: the shared library refresh and the per-portfolio analysis (DESIGN.md section 5).

``refresh`` builds the event library: market catalogue, matching, odds,
evidence for the jumps, fusion and tiers, and the link checks. It writes a
dated snapshot. ``analyze`` is the fast per-portfolio layer: exposures, then
vector math over the snapshot, then the simulation.
"""

from __future__ import annotations

import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import exposure as exposure_mod
from . import factordata
from .config import Settings
from .data.fred import Fred
from .data.kalshi import Kalshi
from .data.massive import Massive
from .data.polymarket import Polymarket
from .engine.fusion import (DeltaReading, FusedDelta, FusedOdds, OddsReading, Tier, assign_tier, fuse_delta,
                            fuse_odds)
from .engine.impact import EngineEvent, Link, build_state
from .engine.simulate import simulate
from .events import EventSpec, Library, load_library
from .evidence import analogs, odds as odds_mod, prior as prior_mod, windows
from .factors import FACTORS, KEYS
from .http import Http, HttpError
from .markets import Market, dedupe
from .matching.decide import Candidate, Decision, decide
from .matching.judge import Judge
from .matching.retrieve import Retriever

log = logging.getLogger(__name__)
Progress = Callable[[str], None]


@dataclass
class Clients:
    polymarket: Polymarket
    kalshi: Kalshi
    fred: Fred
    massive: Massive
    judge: Judge | None
    prior: prior_mod.AIPrior | None
    model: str | None = None


def pick_model(client: Any, wanted: str | None) -> str:
    """REDKEEP_MODEL if set; otherwise the newest active Opus model this key can call.

    The Models API lists the most recently released models first.
    """
    if wanted:
        return wanted
    ids = [m.id for m in client.models.list(lifecycle=["active"], limit=100)]
    best = next((i for i in ids if "opus" in i), ids[0] if ids else None)
    if not best:
        raise RuntimeError("This Anthropic key can't list any models; set REDKEEP_MODEL")
    return best


def make_clients(s: Settings, *, claude: bool = True) -> Clients:
    """Data clients, plus Claude for matching and the prior when ``claude`` is on and a key is set."""
    http = Http(cache_dir=s.cache_dir / "http", min_interval=s.request_spacing)
    judge = prior = model = None
    if claude and s.anthropic_api_key:
        import anthropic

        client = anthropic.Anthropic(api_key=s.anthropic_api_key)
        model = pick_model(client, s.model)
        judge = Judge(client, model=model, effort=s.effort, log_dir=s.cache_dir / "ai")
        prior = prior_mod.AIPrior(client, model=model, effort=s.effort, log_dir=s.cache_dir / "ai")
    return Clients(Polymarket(http), Kalshi(http), Fred(http), Massive(http, s.massive_api_key), judge, prior, model)


# -- JSON helpers -------------------------------------------------------------------


def finite(x: Any) -> Any:
    """Finite floats only: inf and NaN become null."""
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(x) else None
    if isinstance(x, np.ndarray):
        return [finite(v) for v in x.tolist()]
    if isinstance(x, (list, tuple)):
        return [finite(v) for v in x]
    if isinstance(x, dict):
        return {k: finite(v) for k, v in x.items()}
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def _market_json(c: Candidate) -> dict:
    m, v = c.market, c.verdict
    return {
        "venue": m.venue, "id": m.id, "key": m.key, "title": m.title, "outcome": m.outcome, "group": m.event_title,
        "url": m.url, "closes": m.close_time.date().isoformat() if m.close_time else None,
        "bid": m.bid, "ask": m.ask, "mid": m.mid, "spread": m.spread, "volume": m.volume, "event_side_q": c.q,
        "relation": v.relation, "polarity": v.polarity, "score": v.score, "quoted_rule": v.quoted_rule, "reason": v.reason,
        "market_threshold": v.market_threshold, "market_deadline": v.market_deadline,
        "checks": {"same_entity": v.same_entity, "threshold": v.threshold, "deadline": v.deadline,
                   "resolution_source": v.resolution_source},
        "rules": m.rules[:1200],
    }


def _reading_json(r: DeltaReading) -> dict:
    return {"kind": r.kind, "name": r.name, "detail": r.detail, "market": r.market, "n": r.n,
            "delta": finite(r.delta), "sigma": finite(r.sigma)}


def _odds_json(o: OddsReading) -> dict:
    return {"source": o.source, "q": o.q, "sigma": o.sigma, "kind": o.kind, "detail": o.detail, "lo": o.lo, "hi": o.hi}


# -- refresh ---------------------------------------------------------------------------


def catalogue(lib: Library, c: Clients, today: date, progress: Progress) -> list[Market]:
    queries = sorted({q for e in lib.events for q in e.search})
    pm = c.polymarket.markets(since=today, queries=queries)
    progress(f"Polymarket: {len(pm)} open yes/no markets")
    ks = c.kalshi.markets(since=today)
    progress(f"Kalshi: {len(ks)} open markets outside sports, entertainment and weather")
    return dedupe(pm + ks)


def history_legs(d: Decision) -> list[tuple[Candidate, float]]:
    """Markets whose odds history stands in for the event's, with weights."""
    if d.kind == "match":
        sums = [c for c in d.used if c.verdict.relation == "part_of"]
        if sums:
            best_group = max({c.market.event_id for c in sums}, key=lambda g: sum(c.market.volume for c in sums if c.market.event_id == g))
            return [(c, 1.0) for c in sums if c.market.event_id == best_group]
        best = max(d.used, key=lambda c: c.market.volume)
        return [(best, 1.0)]
    if d.kind == "ladder":
        legs = d.used[:2]
        return [(c, 1 / len(legs)) for c in legs]
    return []


def history_from(d: Decision) -> str | None:
    """Where an event's odds history comes from, in words."""
    legs = history_legs(d)
    if not legs:
        return None
    venue = odds_mod.VENUE_NAMES.get(legs[0][0].market.venue, legs[0][0].market.venue)
    if d.kind == "ladder":
        return f"Average of the {len(legs)} nearest rungs of the {venue} ladder"
    if len(legs) > 1:
        return f"Sum of {len(legs)} outcomes on {venue}"
    return f"{venue}: {legs[0][0].market.title}"


def fetch_history(c: Clients, cand: Candidate, start: datetime, end: datetime) -> dict[date, float]:
    m = cand.market
    if m.venue == "polymarket" and m.token_yes:
        pts = c.polymarket.history(m.token_yes, start, end)
    elif m.venue == "kalshi" and m.series:
        pts = c.kalshi.history(m.series, m.id, start, end)
    else:
        return {}
    daily = windows.at_close(pts)
    return {d: (1 - q if cand.verdict.polarity == "no" else q) for d, q in daily.items()}


def event_history(c: Clients, d: Decision, start: datetime, end: datetime) -> dict[date, float]:
    legs = history_legs(d)
    if not legs:
        return {}
    series = []
    for cand, w in legs:
        try:
            series.append((fetch_history(c, cand, start, end), w))
        except HttpError as e:
            log.warning("no history for %s: %s", cand.market.key, e)
            return {}
    common = set.intersection(*(set(s) for s, _ in series)) if series else set()
    return {day: min(1.0, sum(w * s[day] for s, w in series)) for day in sorted(common)}


def check_link(parent: dict[date, float], child: dict[date, float], *, min_days: int = 20) -> dict | None:
    """Slope of the child's daily odds changes on the parent's (DESIGN.md 4.4), through the origin."""
    days = sorted(set(parent) & set(child))
    if len(days) < min_days + 1:
        return None
    dp = np.diff([parent[d] for d in days])
    dc = np.diff([child[d] for d in days])
    sxx = float(dp @ dp)
    if sxx <= 0:
        return None
    slope = float(dp @ dc) / sxx
    resid = dc - slope * dp
    se = math.sqrt(float(resid @ resid) / max(len(dp) - 1, 1) / sxx)
    return {"w_data": slope, "se": se, "t": slope / se if se > 0 else 0.0, "n": len(dp)}


def refresh(s: Settings, c: Clients, *, today: date | None = None, progress: Progress = print) -> dict:
    if c.judge is None:
        raise RuntimeError("No Anthropic key (set REDKEEP_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY): "
                           "the matcher needs Claude to judge candidates")
    now = datetime.now(timezone.utc)
    today = today or now.date()
    lib = load_library()
    warnings: list[str] = []

    markets = catalogue(lib, c, today, progress)
    retriever = Retriever(markets)

    # Factor data: recent history for typical moves and jump windows; long history for analogs.
    fd = factordata.load(c.fred, c.massive, today - timedelta(days=int(365 * 3)), today)
    progress(f"Factors: {sum(fd.available)} of {len(KEYS)} available ({', '.join(fd.sources.values())})")
    if not c.massive.available:
        warnings.append("No Massive key: China, chips and gold factors are missing, and exposures can't be estimated.")
    first = min((a.date for e in lib.events for a in e.analogs), default=today)
    fd_long = factordata.load(c.fred, c.massive, first - timedelta(days=14), today)
    fd_long.typical = fd.typical  # analogs are measured in today's typical moves

    def match(spec: EventSpec) -> tuple[EventSpec, list[Candidate], Decision]:
        got = retriever.candidates(spec, k=s.candidates_per_event)
        verdicts = c.judge.judge(spec, [m for m, _ in got], today)
        cands = [Candidate(m, v, r) for (m, r), v in zip(got, verdicts)]
        return spec, cands, decide(spec, cands, s)

    with ThreadPoolExecutor(max_workers=4) as pool:
        matched = list(pool.map(match, lib.events))
        priors = list(pool.map(lambda e: c.prior.estimate(e, today, fd.typical), lib.events)) if c.prior else [None] * len(lib.events)
    for spec, _, d in matched:
        progress(f"  {spec.short:<18} {d.kind:<6} {d.note}")

    # Odds histories for the matched events, up to the end of ``today`` (a rerun for an earlier day stops there).
    end = min(now, datetime.combine(today + timedelta(days=1), time(0), timezone.utc))
    hist = {spec.id: event_history(c, d, end - timedelta(days=s.history_days), end) for spec, _, d in matched}
    wf = windows.jump_windows({k: v for k, v in hist.items() if v}, fd, move=s.window_move)
    progress(f"Jump windows: {len(wf.windows)} days with sharp odds moves; readings for {len(wf.readings)} events")

    events = []
    for (spec, cands, d), pr in zip(matched, priors):
        readings: list[DeltaReading] = []
        if spec.id in wf.readings:
            readings.append(wf.readings[spec.id])
        a = analogs.analog_reading(spec, fd_long)
        if a:
            readings.append(a)
        if pr:
            readings.append(prior_mod.delta_reading(pr))
        fused = fuse_delta(readings)
        odds_readings = list(d.readings) or ([prior_mod.odds_reading(pr)] if pr else [])
        if not odds_readings:
            warnings.append(f"{spec.id}: no odds at all (no market fits and no AI prior)")
            continue
        fo = fuse_odds(odds_readings)
        liquid = odds_mod.liquid([c.market for c in d.used], volume=s.liquid_volume, spread=s.liquid_spread)
        tier = assign_tier(odds=fo, delta=fused, match_good=d.good, liquid=liquid)
        events.append({
            "id": spec.id, "title": spec.title, "short": spec.short, "category": spec.category,
            "question": spec.question, "resolves": spec.resolves.isoformat(), "why": spec.why, "order": spec.order,
            "terms": {"deadline": spec.deadline.isoformat(), "threshold": spec.threshold, "unit": spec.threshold_unit,
                      "direction": spec.direction, "source": spec.source},
            "odds": {"q": fo.q, "sigma": fo.sigma, "p_hat": odds_mod.calibrate(fo.q), "from_market": fo.from_market,
                     "cross_checked": fo.cross_checked, "disagree": fo.disagree, "z": fo.z,
                     "readings": [_odds_json(r) for r in odds_readings]},
            "match": {"kind": d.kind, "note": d.note, "good": d.good, "liquid": liquid, "lo": d.lo, "hi": d.hi,
                      "candidates": len(cands), "used": [_market_json(x) for x in d.used],
                      "near_misses": [_market_json(x) for x in d.near_misses], "ladders": d.ladders},
            "delta": {"value": fused.delta, "sigma": fused.sigma, "n_market": fused.n_market, "chi2": fused.chi2,
                      "dof": fused.dof, "p_value": fused.p_value, "agree": fused.agree,
                      "weights": {k: v for k, v in fused.weights.items()},
                      "readings": [_reading_json(r) for r in readings]},
            "tier": {"tier": tier.tier, "reason": tier.reason, "badges": tier.badges},
            "history": [[k.isoformat(), v] for k, v in sorted(hist[spec.id].items())],
            "history_from": history_from(d) if hist[spec.id] else None,
            "prior": pr.model_dump() if pr else None,
        })

    links = []
    for ln in lib.links:
        rec = {"parent": ln.parent, "child": ln.child, "w_proposed": ln.w, "reason": ln.reason}
        chk = check_link(hist.get(ln.parent, {}), hist.get(ln.child, {}))
        if chk is None:
            rec.update(status="untestable", used=False, note="One of the two events has no market history to test the link.")
        elif chk["t"] * math.copysign(1, ln.w) >= 2:
            rec.update(chk, status="confirmed", used=True, w=max(-1.0, min(1.0, chk["w_data"])),
                       note="The odds data agrees with the proposed direction; the engine uses the measured slope.")
        else:
            rec.update(chk, status="unconfirmed", used=False, note="The odds data doesn't support the link, so the engine leaves it out.")
        links.append(rec)

    snap = {
        "kind": "redkeep.library", "version": 1, "as_of": now.isoformat(timespec="seconds"), "today": today.isoformat(),
        "model": c.model, "catalogue": {"polymarket": sum(m.venue == "polymarket" for m in markets),
                                        "kalshi": sum(m.venue == "kalshi" for m in markets)},
        "factors": [{"key": f.key, "name": f.name, "unit": f.unit, "typical": fd.typical[i], "source": fd.sources.get(f.key)}
                    for i, f in enumerate(FACTORS)],
        "windows": {"count": len(wf.windows), "days": len(fd.dates), "move": s.window_move},
        "events": events, "links": links, "warnings": warnings,
    }
    return finite(snap)


# -- analyze ---------------------------------------------------------------------------


def engine_inputs(snap: dict) -> tuple[list[EngineEvent], list[Link]]:
    evs = [EngineEvent(e["id"], e["odds"]["q"], date.fromisoformat(e["resolves"]),
                       np.array([v if v is not None else 0.0 for v in e["delta"]["value"]]),
                       p_hat=e["odds"]["p_hat"], order=e.get("order", i))
           for i, e in enumerate(snap["events"])]
    ids = {e.id for e in evs}
    links = [Link(l["parent"], l["child"], l["w"]) for l in snap["links"]
             if l.get("used") and l["parent"] in ids and l["child"] in ids]
    return evs, links


def analyze(s: Settings, c: Clients, snap: dict, holdings: list[tuple[str, float, str]], *, name: str = "Portfolio",
            horizon_months: float | None = None, what_if: dict[str, str] | None = None, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    horizon = horizon_months or s.horizon_months
    if not c.massive.available:
        raise RuntimeError("MASSIVE_API_KEY is not set: exposures need daily prices for each holding")
    fd = factordata.load(c.fred, c.massive, today - timedelta(days=int(365 * s.exposure_years) + 30), today)
    px = exposure_mod.estimate(holdings, fd, c.massive, as_of=today)
    # Jumps are in the snapshot's typical moves; rescale the arrow if today's typical moves differ.
    snap_typ = np.array([f["typical"] if f["typical"] else np.nan for f in snap["factors"]])
    scale = np.where(np.isfinite(snap_typ) & np.isfinite(px.typical), snap_typ / px.typical, 1.0)
    b = px.b * scale

    evs, links = engine_inputs(snap)
    st = build_state(b, evs, links, today=today, horizon_days=horizon * 30.44, what_if=what_if)
    sim = simulate(st, n=20_000)
    by_id = {e["id"]: e for e in snap["events"]}
    V = px.value
    ranked = []
    for e in st.ranked(in_horizon_only=False):
        meta = by_id[e.id]
        ranked.append({
            "id": e.id, "title": meta["title"], "short": meta["short"], "tier": meta["tier"]["tier"],
            "q": e.q, "p_hat": e.p_hat, "resolves": meta["resolves"], "days": e.days, "in_horizon": e.in_horizon,
            "direct": e.direct, "total": e.total, "priced": e.priced, "if_yes": e.if_yes, "if_no": e.if_no,
            "swing_pct": e.swing_pct, "edge": e.edge,
            "usd": {"if_yes": V * e.if_yes / 100, "if_no": V * e.if_no / 100, "priced": V * e.priced / 100,
                    "swing": V * e.swing_pct / 100},
            "contributions": [c_.__dict__ for c_ in e.contributions],
            "knock": [{"child": ch, "w": w, "pct": pct} for ch, w, pct in e.knock],
        })
    out = {
        "kind": "redkeep.analysis", "version": 1, "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "library_as_of": snap["as_of"], "horizon_months": horizon, "what_if": what_if or {},
        "portfolio": {
            "name": name, "value": V, "b": b, "fit": px.fit, "typical": px.typical, "look": px.look,
            "holdings": [[t, a, u] for t, a, u in holdings],
            "lines": [{"ticker": l.ticker, "name": l.name, "kind": l.kind, "value": l.value, "weight": l.weight, "b": l.b * scale,
                       "r2_oos": l.r2_oos, "n_obs": l.n_obs, "price": l.price, "price_date": l.price_date,
                       "holdings": l.holdings, "n_holdings": l.n_holdings, "holdings_date": l.holdings_date,
                       "note": l.note} for l in px.lines],
            "sources": fd.sources, "notes": px.notes,
        },
        "events": ranked,
        "links_used": [{"parent": l.parent, "child": l.child, "w": l.w} for l in st.links],
        "simulation": {"n": sim.n, "mean": sim.mean, "sd": sim.sd, "p5": sim.p5, "p50": sim.p50, "p95": sim.p95,
                       "es5": sim.es5, "drivers": [d.__dict__ for d in sim.drivers],
                       "hist": {"lo": sim.hist_lo, "hi": sim.hist_hi, "bins": sim.bins}},
    }
    return finite(out)


# -- files -----------------------------------------------------------------------------


def save(obj: dict, out_dir: Path, stem: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = obj["as_of"].replace(":", "").replace("-", "")[:15]
    path = out_dir / f"{stem}-{stamp}.json"
    text = json.dumps(obj, indent=1, ensure_ascii=False)
    path.write_text(text)
    (out_dir / f"{stem}-latest.json").write_text(text)
    return path


def load_latest(out_dir: Path, stem: str) -> dict:
    return json.loads((out_dir / f"{stem}-latest.json").read_text())
