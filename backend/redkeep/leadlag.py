"""Eval E10, lead and lag (DESIGN.md 4.8 and section 10): the data, the report and its decisions.

The statistics, and the rules fixed before the first run, are in ``granger.py``. This module gathers the data for its
parts and applies the rules:

1. **Factors against factors**, on daily moves since 2008, as the engine measures them (the dollar carried to the
   4 p.m. close), as it measured them before (the dollar at its noon fixing), and from 4 p.m. fund closes alone
   ("stand-ins": a lead that survives on one clock but not the other is a clock, not a lead). The whole span and
   three stretches of it, since links that come and go are no use to the engine. The rule: carry an event's jump
   past its first day only if the pooled out-of-sample score's 95% range is above zero.
2. **Clocks.** Each factor whose series isn't a 4 p.m. close, against a fund that closes then. Added after part 1's
   first run found every link into the dollar, so it is a check on that finding, not a test fixed in advance. Then
   what the dollar's clock costs a fund's exposures: the exposure engine's fit score (``exposure.fit_line``) for 25
   funds, on windows ending every quarter, with the dollar carried to the close and at its noon fixing.
3. **Propagation on the April 2025 replay**: the saved replay's explained moves, carried forward through the VAR
   fitted on the two years before it, against the moves that happened.
4. **Odds against factors**, on the funnel's material markets (one per group), with the sign tests fixed in
   ``granger.py``. Beside them, a pooled index of all those markets' odds changes, signed by the labels, which has
   the power the per-market tests lack. It was added after the first run, so it is exploratory and changes nothing.
5. **Odds against odds**: every pair of those markets from different families, for late reactions and for how well a
   same-day slope holds over longer spans and on later data.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from . import exposure, factordata
from . import granger as G
from .evidence.windows import at_close
from .factors import BY_KEY, FACTORS, KEYS, NF
from .funnel.labels import read_rows
from .http import HttpError

log = logging.getLogger(__name__)
Progress = Callable[[str], None]

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
LABELS = DATA_DIR / "funnel" / "labels.jsonl"
REPLAY = DATA_DIR / "replays" / "tariffs-2025.json"

FROM = date(2007, 6, 1)  # the dollar fund's noon prices begin in 2007, so every sample can carry the dollar
STRETCHES = [("2008 to 2012", date(2008, 1, 1), date(2012, 12, 31)), ("2013 to 2019", date(2013, 1, 1), date(2019, 12, 31)),
             ("2020 on", date(2020, 1, 1), None)]
ODDS_DAYS = 200  # days of hourly odds for parts 4 and 5
POLYMARKET_MAX = 60  # Polymarket's prices come 14 days a call, so its markets are sampled (by a hash of their ids)
CLEAR_PAIR_T = 3.0  # a pair of markets is linked on the day when its same-day slope is this many standard errors out
# Funds whose exposure fit part 2 compares with the dollar carried and at noon: the sample portfolios' kinds of holdings,
# and the currency funds the dollar matters most to
FUNDS = ("SPY", "QQQ", "EFA", "EEM", "VEA", "VWO", "EWJ", "EWW", "EWZ", "FXI", "GLD", "SLV", "GDX", "TLT", "IEF", "LQD",
         "HYG", "TIP", "VNQ", "XLE", "DBC", "FXE", "FXY", "FXB", "FXC")

# 4 p.m. closes standing in for each factor (the VIX's close is at 4:15): (how, tickers)
STAND_INS: dict[str, tuple[str, tuple[str, ...]]] = {
    "eq": ("ret", ("SPY",)), "rates": ("down", ("IEF",)), "infl": ("spread", ("TIP", "IEF")), "usd": ("ret", ("UUP",)),
    "oil": ("ret", ("USO",)), "credit": ("down-spread", ("HYG", "IEF")), "vix": ("diff", ("VIXCLS",)),
    "china": ("ret", ("FXI",)), "chips": ("spread", ("SMH", "SPY")), "gold": ("ret", ("GLD",)),
}


# -- data ---------------------------------------------------------------------------------------------------------


def stand_in_moves(levels: dict[str, dict[date, float]], dates: list[date]) -> np.ndarray:
    """Each factor's daily move from 4 p.m. fund closes, in percent (points for the VIX), on ``dates``."""
    prev = {d: dates[i - 1] for i, d in enumerate(dates) if i}
    out = np.full((len(dates), NF), np.nan)

    def ret(t: str, a: date, b: date) -> float:
        s = levels.get(t) or {}
        return (s[b] / s[a] - 1) * 100 if a in s and b in s and s[a] > 0 else np.nan

    for k, f in enumerate(FACTORS):
        how, ts = STAND_INS[f.key]
        for i, d in enumerate(dates):
            a = prev.get(d)
            if a is None:
                continue
            if how == "ret":
                out[i, k] = ret(ts[0], a, d)
            elif how == "down":  # a bond fund falls when yields rise
                out[i, k] = -ret(ts[0], a, d)
            elif how == "spread":
                out[i, k] = ret(ts[0], a, d) - ret(ts[1], a, d)
            elif how == "down-spread":  # high-yield bonds lagging Treasuries: spreads wider
                out[i, k] = ret(ts[1], a, d) - ret(ts[0], a, d)
            else:
                s = levels.get(ts[0]) or {}
                out[i, k] = s[d] - s[a] if a in s and d in s else np.nan
    return out


def in_typical(X: np.ndarray) -> np.ndarray:
    """Moves in units of their own weekly standard deviation, so series of different sizes pool fairly."""
    sd = np.nanstd(X, axis=0) * np.sqrt(5)
    return X / np.where(sd > 0, sd, np.nan)


def material_markets(path: Path = LABELS) -> list:
    """The funnel's material markets (a label moves some factor a full typical move), one per group: the one its labels
    move most, then the first key."""
    best: dict[str, object] = {}
    for r in read_rows(path):
        size = sum(abs(m) for m in r.moves)
        if max(abs(m) for m in r.moves) < 1:
            continue
        old = best.get(r.group)
        if old is None or (-size, r.key) < (-sum(abs(m) for m in old.moves), old.key):
            best[r.group] = r
    return sorted(best.values(), key=lambda r: r.key)


def family(key: str, group: str) -> str:
    """Markets of one Kalshi series or one Polymarket event move together by construction (a ladder's rungs, an
    outcome set), so pairs within a family say nothing about knock-on effects."""
    venue, gid = group.split(":", 1)
    return f"{venue}:{gid.split('-')[0]}" if venue == "kalshi" else group


def odds_at_close(c, rows: list, start: datetime, end: datetime, progress: Progress) -> tuple[dict, dict, list[str]]:
    """Each market's odds at the 4 p.m. close from hourly prices: Kalshi's all, Polymarket's a hashed sample."""
    from .data.polymarket import GAMMA

    poly = sorted((r for r in rows if r.venue == "polymarket"),
                  key=lambda r: hashlib.sha256(r.key.encode()).hexdigest())[:POLYMARKET_MAX]
    pick = [r for r in rows if r.venue == "kalshi"] + poly
    series, meta, warnings = {}, {}, []
    for i, r in enumerate(pick):
        try:
            if r.venue == "kalshi":
                tick = r.key.split(":", 1)[1]
                pts = c.kalshi.history(r.group.split(":", 1)[1].split("-")[0], tick, start, end, period_min=60)
            else:
                res = c.polymarket.http.get_json(f"{GAMMA}/markets", {"id": r.key.split(":", 1)[1]}, ttl=None)
                m = res[0] if isinstance(res, list) and res else None
                toks = (m or {}).get("clobTokenIds")
                toks = json.loads(toks) if isinstance(toks, str) else toks
                if not toks:
                    continue
                pts = c.polymarket.history(toks[0], start, end, fidelity_min=60)
        except (HttpError, ValueError, KeyError) as e:
            warnings.append(f"{r.key}: no hourly odds ({type(e).__name__})")
            continue
        closes = at_close([(t, q) for t, q in pts if start <= t <= end])
        if len(closes) >= G.MIN_DAYS:
            series[r.key] = closes
            meta[r.key] = {"title": r.title, "venue": r.venue, "group": r.group, "moves": list(r.moves)}
        if (i + 1) % 25 == 0:
            progress(f"  odds for {i + 1} of {len(pick)} markets, {len(series)} with {G.MIN_DAYS}+ days")
    return series, meta, warnings


def changes_on(dates: list[date], s: dict[date, float]) -> np.ndarray:
    """Odds changes on the factor calendar: each day's from the close before, where both are known."""
    out = np.full(len(dates), np.nan)
    for t in range(1, len(dates)):
        a, b = dates[t - 1], dates[t]
        if a in s and b in s:
            out[t] = s[b] - s[a]
    return out


# -- the parts ------------------------------------------------------------------------------------------------------


def _study_json(st: G.FactorStudy) -> dict:
    return {
        "label": st.label, "start": st.start.isoformat(), "end": st.end.isoformat(), "series": st.names, "days": st.days,
        "lag_order": {"bic": st.order["bic"], "aic": st.order["aic"],
                      "scores": {str(p): v for p, v in st.order["scores"].items()}},
        "tests": len(st.links), "links": [asdict(ln) for ln in st.links if ln.significant],
        "own": st.own, "oos": asdict(st.oos), "notes": st.notes,
    }


def factor_part(fd: factordata.FactorData, fd_noon: factordata.FactorData, standin: np.ndarray,
                progress: Progress) -> dict:
    measures = {"engine": fd.Z, "engine, dollar at its noon fixing": fd_noon.Z, "4 p.m. stand-ins": in_typical(standin)}
    dates = fd.dates
    studies = []
    for label, lo, hi in [("2008 on", date(2008, 1, 1), None), *STRETCHES]:
        idx = [i for i, d in enumerate(dates) if d >= lo and (hi is None or d <= hi)]
        for name, Z in measures.items():
            st = G.factor_study(Z[idx], [dates[i] for i in idx], list(KEYS), f"{label}: {name}")
            studies.append({"stretch": label, "measure": name, **_study_json(st)})
            progress(f"  {label:<13} {name:<34} lag order {st.order['bic']} (BIC); "
                     f"{sum(ln.significant for ln in st.links)} links of {len(st.links)}; "
                     f"out of sample {st.oos.pooled:+.3f}")
    main = next(s for s in studies if s["stretch"] == "2008 on" and s["measure"] == "engine")
    lo95 = (main["oos"]["pooled_range"] or [None])[0]
    propagate = lo95 is not None and lo95 > 0
    return {"studies": studies, "decision": {
        "propagate": propagate,
        "why": (f"The pooled out-of-sample score since 2008 is {main['oos']['pooled']:+.3f} (95% range "
                f"{main['oos']['pooled_range'][0]:+.3f} to {main['oos']['pooled_range'][1]:+.3f}): yesterday's factor "
                "moves forecast today's worse than no move at all, so an event's jump stays its first day's move."
                if not propagate else "The pooled out-of-sample score's whole range is above zero."),
    }}


def clock_part(fd: factordata.FactorData, fd_noon: factordata.FactorData, standin: np.ndarray) -> dict:
    out = {}
    for k, f in enumerate(FACTORS):
        how, ts = STAND_INS[f.key]
        if all(t in f.massive for t in ts) or ts[0] == f.fred:
            continue  # the factor is already measured at the close (or by the same series)
        row = {"series": fd.sources.get(f.key, ""), "stand_in": " vs ".join(ts)}
        for name, X in (("engine", fd.X), ("before", fd_noon.X)):
            if name == "before" and not f.carry:
                continue
            ck = G.clock(X[:, k], standin[:, k])
            row[name] = asdict(ck) if ck else None
        out[f.key] = row
    return out


def exposure_part(fd: factordata.FactorData, fd_noon: factordata.FactorData, levels: dict, *, every: int = 63) -> dict:
    """Each fund's held-out fit (the last ``exposure.TEST_DAYS`` of a window two years longer) and its dollar exposure,
    on windows ending every ``every`` trading days, with the dollar as the engine measures it and at its noon fixing."""
    usd = KEYS.index("usd")
    win = 520 + exposure.TEST_DAYS
    rows = []
    for t in FUNDS:
        if not levels.get(t):
            continue
        y = exposure.returns_on(levels[t], fd.dates)
        fit = {"engine": [], "before": []}
        b = {"engine": [], "before": []}
        for e in range(win, len(fd.dates) + 1, every):
            got = {}
            for name, f in (("engine", fd), ("before", fd_noon)):
                sub = factordata.FactorData(levels={}, dates=f.dates[e - win:e], X=f.X[e - win:e], typical=f.typical,
                                            sources=f.sources)
                _, coef, r2, _, _ = exposure.fit_line(y[e - win:e], sub)
                got[name] = (r2, coef[usd])
            if all(v[0] is not None and np.isfinite(v[0]) for v in got.values()):
                for name, (r2, bu) in got.items():
                    fit[name].append(r2)
                    b[name].append(abs(bu))
        if fit["engine"]:
            gain = np.array(fit["engine"]) - np.array(fit["before"])
            rows.append({"fund": t, "windows": len(gain), "fit_before": float(np.mean(fit["before"])),
                         "fit": float(np.mean(fit["engine"])), "gain": float(gain.mean()), "wins": float((gain > 0).mean()),
                         "usd_before": float(np.mean(b["before"])), "usd": float(np.mean(b["engine"]))})
    gains = [r["gain"] for r in rows]
    return {"funds": rows, "mean_gain": float(np.mean(gains)) if gains else None,
            "gaining": int(sum(g > 0 for g in gains)), "worst": min(gains) if gains else None}


def propagation_part(fd: factordata.FactorData, path: Path = REPLAY) -> dict | None:
    """The saved replay's explained moves, plus what a VAR(1) fitted on the two years before the episode says they go
    on to cause (``granger.propagate``): all of its coefficients, and only the links that pass the tests."""
    if not path.exists():
        return None
    rep = json.loads(path.read_text())
    typ = np.array([np.nan if t is None else t for t in rep["typical"]], float)
    z = np.array([[np.nan if v is None else v for v in d["z"]] for d in rep["days"]], float)
    ex = np.array([[np.nan if v is None else v for v in d["explained"]] for d in rep["days"]], float)
    base = date.fromisoformat(rep["base"])
    idx = [i for i, d in enumerate(fd.dates) if base - timedelta(days=730) < d <= base]
    Z = fd.X[idx] / typ
    use = [k for k in range(NF) if np.isfinite(Z[:, k]).mean() >= 0.8]
    fit = G.fit_var(Z[:, use], 1, [KEYS[k] for k in use])
    links = G.granger(fit)
    A = np.zeros((NF, NF))
    A[np.ix_(use, use)] = fit.A[0]
    sparse = np.zeros((NF, NF))
    for ln in links:
        if ln.significant:
            sparse[KEYS.index(ln.target), KEYS.index(ln.source)] = A[KEYS.index(ln.target), KEYS.index(ln.source)]

    def score(x: np.ndarray) -> dict:
        ok = np.isfinite(z) & np.isfinite(x)
        y, p = z[ok], x[ok]
        return {"explained_share": 1 - float(((y - p) ** 2).sum()) / float(y @ y), "corr": float(np.corrcoef(p, y)[0, 1]),
                "cells": int(ok.sum())}

    return {"episode": rep["episode"], "fitted": [fd.dates[idx[0]].isoformat(), fd.dates[idx[-1]].isoformat()],
            "links": [asdict(ln) for ln in links if ln.significant],
            "first_day_only": score(ex), "all_coefficients": score(G.propagate(ex, [A])),
            "tested_links_only": score(G.propagate(ex, [sparse]))}


def odds_factor_part(fd: factordata.FactorData, series: dict, meta: dict) -> dict:
    dates = fd.dates
    dq = {k: changes_on(dates, s) for k, s in series.items()}
    Z = fd.Z
    res = {k: {f: cl for i, f in enumerate(KEYS) if (cl := G.cross_lag(dq[k], Z[:, i])) is not None} for k in dq}
    summary = G.timing(res, KEYS)
    index = [ix for i, f in enumerate(KEYS)
             if (ix := G.index_timing(dq, {k: meta[k]["moves"][i] for k in dq}, Z[:, i], f)) is not None]
    later = [s.factor for s in summary if s.after_significant]
    return {
        "markets": len(dq), "by_venue": {v: sum(m["venue"] == v for m in meta.values()) for v in ("kalshi", "polymarket")},
        "timing": [asdict(s) for s in summary], "index": [asdict(ix) for ix in index],
        "decision": {"next_day": later, "why": (
            "No factor's moves the day after an odds change point the same way as the same-day ones in significantly "
            "more than half the markets, so the jump windows stay one day long." if not later else
            f"These factors keep moving the day after: {', '.join(later)}.")},
    }


def odds_odds_part(series: dict, meta: dict) -> dict:
    fam = {k: family(k, m["group"]) for k, m in meta.items()}
    return asdict(G.pair_study(series, fam, clear_t=CLEAR_PAIR_T))


# -- the run ----------------------------------------------------------------------------------------------------------


def run(c, *, today: date | None = None, odds_days: int = ODDS_DAYS, odds: bool = True,
        progress: Progress = print) -> dict:
    today = today or datetime.now(timezone.utc).date()
    warnings: list[str] = []
    progress(f"Factor data from {FROM} to {today}")
    fd = factordata.load(c.fred, c.massive, FROM, today)
    raw = c.fred.series(["DTWEXBGS"], FROM, today).get("DTWEXBGS") or {}
    fd_noon = factordata.build({**fd.levels, "DTWEXBGS": raw})
    extra = sorted({t for how, ts in STAND_INS.values() for t in ts if t not in fd.levels and t != "VIXCLS"}
                   | {t for t in FUNDS if t not in fd.levels})
    levels = dict(fd.levels)
    for t in extra:
        try:
            levels[t] = c.massive.closes(t, FROM, today)
        except HttpError as e:
            warnings.append(f"Massive {t}: {e}")
    standin = stand_in_moves(levels, fd.dates)
    if "carried" not in fd.sources.get("usd", ""):
        warnings.append("The dollar isn't carried to the close (no hourly prices for its fund), so part 1 measures it "
                        "at its noon fixing on both counts")
    progress("Part 1, factors against factors (VAR, Granger tests, forecasts out of sample):")
    rep = {
        "kind": "redkeep.leadlag", "version": 1, "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": {"pmax": G.PMAX, "fdr": G.FDR, "train": G.TRAIN, "refit": G.REFIT, "block": G.BLOCK, "draws": G.BOOT,
                   "clear_t": G.CLEAR_T, "clear_pair_t": CLEAR_PAIR_T, "min_days": G.MIN_DAYS, "odds_days": odds_days,
                   "polymarket_max": POLYMARKET_MAX},
        "sources": {**fd.sources, "stand_ins": {k: " vs ".join(ts) for k, (how, ts) in STAND_INS.items()}},
        "factors": factor_part(fd, fd_noon, standin, progress),
        "clocks": clock_part(fd, fd_noon, standin),
        "exposures": exposure_part(fd, fd_noon, levels),
        "propagation": propagation_part(fd),
    }
    if odds:
        end = datetime.combine(today, datetime.min.time(), timezone.utc)
        rows = material_markets()
        progress(f"Parts 4 and 5: hourly odds for the funnel's material markets ({len(rows)} groups), {odds_days} days")
        series, meta, warn = odds_at_close(c, rows, end - timedelta(days=odds_days), end, progress)
        warnings += warn
        rep["odds_markets"] = meta
        rep["odds_factors"] = odds_factor_part(fd, series, meta)
        rep["odds_odds"] = odds_odds_part(series, meta)
    rep["warnings"] = warnings
    return rep


def _f(x, spec: str) -> str:
    return "n/a" if x is None else format(x, spec)


def print_report(rep: dict, out: Progress = print) -> None:
    fp = rep["factors"]
    out("\nE10, lead and lag")
    out("Factors against factors (pooled out-of-sample score: 0 is the forecast 'no move'):")
    for s in fp["studies"]:
        o = s["oos"]
        rng = f"[{o['pooled_range'][0]:+.3f}, {o['pooled_range'][1]:+.3f}]" if o["pooled_range"] else ""
        links = ", ".join(f"{ln['target']}←{ln['source']} ({ln['partial_r2']:.1%})" for ln in s["links"]) or "none"
        out(f"  {s['stretch']:<13} {s['measure']:<34} lag {s['lag_order']['bic']} | oos {o['pooled']:+.3f} {rng} | {links}")
    out(f"  Decision: {fp['decision']['why']}")
    out("Clocks (late share: the part of a 4 p.m. move the series shows a day late):")
    for k, row in rep["clocks"].items():
        parts = [f"{name} {row[name]['late_share']:+.0%} (same-day corr {row[name]['same_corr']:.2f})"
                 for name in ("before", "engine") if row.get(name)]
        out(f"  {BY_KEY[k].name:<24} vs {row['stand_in']:<10} " + "; ".join(parts))
    ex = rep.get("exposures")
    if ex and ex["funds"]:
        out(f"Exposure fit with the dollar carried to the close, against at noon (held-out R², {ex['funds'][0]['windows']} windows):")
        for r in ex["funds"]:
            out(f"  {r['fund']:<5} {r['fit_before']:.3f} → {r['fit']:.3f} ({r['gain']:+.4f}, better in {r['wins']:.0%}); "
                f"dollar exposure {r['usd_before']:.2f} → {r['usd']:.2f}")
        out(f"  {ex['gaining']} of {len(ex['funds'])} funds fit better; mean gain {ex['mean_gain']:+.4f}, worst {ex['worst']:+.4f}")
    p = rep.get("propagation")
    if p:
        out(f"April 2025 replay, share of factor moves explained: first day only {p['first_day_only']['explained_share']:+.3f}; "
            f"carried by the VAR {p['all_coefficients']['explained_share']:+.3f}; "
            f"by its tested links only {p['tested_links_only']['explained_share']:+.3f}")
    if "odds_factors" in rep:
        of = rep["odds_factors"]
        out(f"Odds against factors on {of['markets']} markets ({of['by_venue']}):")
        for s in of["timing"]:
            if s["pairs"]:
                out(f"  {s['factor']:<7} {s['pairs']:>3} clear | after same sign {s['after_same_sign']:>3} "
                    f"(p {_f(s['after_p'], '.3f')}) | before same sign {s['before_same_sign']:>3} (p {_f(s['before_p'], '.3f')})")
        out("  Pooled, signed by the labels (exploratory): same / after / before, with t")
        for ix in of["index"]:
            out(f"  {ix['factor']:<7} {ix['markets']:>3} mkts {ix['same']:+.3f} ({ix['t'][0]:+.1f}) "
                f"{ix['after']:+.3f} ({ix['t'][1]:+.1f}) {ix['before']:+.3f} ({ix['t'][2]:+.1f})")
        out(f"  Decision: {of['decision']['why']}")
        oo = rep["odds_odds"]
        out(f"Odds against odds: {oo['pairs']:,} pairs, {oo['clear']} linked on the day; late slope the same way in "
            f"{oo['late_same_sign']} (p {_f(oo['late_p'], '.2g')}), median late/same {_f(oo['late_share'], '+.2f')}; "
            f"slope kept at 2, 3, 5 days: {', '.join(f'{v:.2f}' for v in oo['persistence'].values())}; "
            f"on later data ({oo['split_pairs']} pairs), error vs no link {_f(oo['split_error'], '.3f')} (daily), "
            f"{_f(oo['split_error_kday'], '.3f')} (two-day)")
    for w in rep.get("warnings", [])[:10]:
        out(f"  warning: {w}")
