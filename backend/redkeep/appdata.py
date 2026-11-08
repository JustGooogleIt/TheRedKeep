"""A library snapshot and portfolio analyses, in the prototype app's data shapes (``redkeep app``).

The prototype (``prototype/src/data.js``) was built on illustrative data in the
shapes this engine produces. ``export`` writes ``live.js`` in those shapes; the
prototype's build inlines it when it exists, and the app then switches between
the illustrative data and this snapshot.

The analyses are optional. Without any, the export carries the events alone
(``"exposures": "illustrative"``): the app pairs them with its own illustrative
portfolios, rescaled to the snapshot's typical moves, and says so. That needs
only a snapshot, so only the Anthropic key; exposures need Massive's prices.

Two conversions are worth knowing:

* An event's jump readings are vectors, one number per factor, and the app
  shows one number per reading. Each reading becomes a multiple of the fused
  jump, ``k``, estimated by weighted least squares along the fused direction
  (``k = 1``: the same size and direction as the fused jump), with its error
  bar. The verdict on whether market readings agree comes from the snapshot's
  χ² over the full vectors, not from these projections.
* A fund's exposure is its own regression, and the app's portfolio arrow is the
  value-weighted sum of its lines, the same arithmetic as the engine. Each
  analysis's own results ride along so the app's numbers can be checked against
  the engine's (``prototype/check.js`` does).
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import date

import numpy as np
from scipy.stats import chi2

from .engine.impact import validate_links
from .factors import FACTORS
from .pipeline import engine_inputs, finite
from .portfolio import samples

VENUES = {"polymarket": "Polymarket", "kalshi": "Kalshi"}
SERIES = {
    "SP500": "S&P 500", "DGS10": "10-yr yield", "T10YIE": "10-yr breakeven", "DTWEXBGS": "Broad dollar index",
    "DCOILWTICO": "WTI crude", "BAMLH0A0HYM2": "High-yield spread", "VIXCLS": "VIX",
}
# The app's check marks: 2 the same, 1 close, 0 different.
THRESHOLD = {"equal": (2, "Same threshold"), "stricter": (1, "Stricter"), "looser": (1, "Looser"),
             "different_measure": (0, "Measures something else")}
DEADLINE = {"same": 2, "earlier": 1, "later": 1}
SOURCE = {"same": (2, "Same source"), "compatible": (1, "Compatible source"), "unclear": (1, "Unclear"),
          "different": (0, "Different source")}


def _day(s: str | None) -> str:
    if not s:
        return "n/a"
    d = date.fromisoformat(s[:10])
    return f"{d:%b} {d.day}, {d.year}"


def _proxy(source: str | None, fallback: str) -> str:
    """'FRED DGS10' -> '10-yr yield'; 'Massive SMH vs Massive SPY' -> 'SMH vs SPY'."""
    if not source:
        return fallback
    return " vs ".join(SERIES.get(part.split()[-1], part.split()[-1]) for part in source.split(" vs "))


def _title(c: dict) -> str:
    t, o = c["title"], c.get("outcome")
    return f"{t} ({o})" if o and o.lower() not in t.lower() else t


def _liquidity(c: dict) -> str:
    v = c.get("volume") or 0.0
    vol = f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.0f}k"
    spread = "no quote" if c.get("spread") is None else f"{c['spread'] * 100:.0f}¢ spread"
    return f"{vol} volume · {spread}"


def _threshold(x: float | None, terms: dict) -> str:
    if x is None:
        return "None"
    unit = terms.get("unit") or ""
    word = {"above": "at least", "below": "at most"}.get(terms.get("direction") or "", "")
    num = f"{x:g}{unit}" if unit.startswith("%") else f"{x:g} {unit}".strip()
    text = f"{word} {num}".strip()
    return text[:1].upper() + text[1:]


def _cmp(e: dict, lead: dict) -> list[list]:
    terms, ck = e["terms"], lead["checks"]
    rows = [["What", e["short"], lead.get("outcome") or lead["title"], 2 if ck["same_entity"] else 0]]
    if ck["threshold"] != "not_applicable":
        mark, word = THRESHOLD.get(ck["threshold"], (1, ck["threshold"]))
        market = _threshold(lead["market_threshold"], terms) if lead.get("market_threshold") is not None else word
        rows.append(["Threshold", _threshold(terms.get("threshold"), terms), market, mark])
    market_deadline = lead.get("market_deadline")
    rows.append(["Deadline", _day(terms["deadline"]),
                 _day(market_deadline) if market_deadline else ("Same" if ck["deadline"] == "same" else _day(lead.get("closes"))),
                 DEADLINE.get(ck["deadline"], 1)])
    mark, word = SOURCE.get(ck["resolution_source"], (1, ck["resolution_source"]))
    rows.append(["Source", terms["source"], word, mark])
    return rows


def _market(e: dict) -> dict | None:
    mt = e["match"]
    if mt["kind"] == "none" or not mt["used"]:
        return None
    used = mt["used"]
    lead = max(used, key=lambda c: c.get("volume") or 0.0)
    parts = [c for c in used if c["relation"] == "part_of" and c["venue"] == lead["venue"] and c["group"] == lead["group"]]
    rel = {"match": "sum" if parts else "same", "ladder": "ladder", "bound": "bound"}[mt["kind"]]
    extra = " · ".join(f"{r['source']}: {r['detail']}" for r in e["odds"]["readings"]
                       if r["kind"] in ("ladder", "sum", "bound") and r.get("detail"))
    return {
        "venue": VENUES.get(lead["venue"], lead["venue"]),
        "title": lead["group"] if rel == "sum" and lead.get("group") else _title(lead),
        "rule": lead.get("quoted_rule") or (lead.get("rules") or "")[:300],
        "rel": rel,
        "score": min(c["score"] for c in parts) if parts else lead["score"],
        "liq": _liquidity(lead),
        "cmp": _cmp(e, lead),
        "ladder": extra or None,
        "note": mt["note"],
        "url": lead.get("url"),
    }


def _vec(xs: list, missing: float) -> np.ndarray:
    return np.array([missing if v is None else float(v) for v in xs])


def _project(r: dict, fused: np.ndarray) -> list | None:
    """A jump reading as a multiple of the fused jump: k with its error bar, by weighted least squares."""
    d, s = _vec(r["delta"], math.nan), _vec(r["sigma"], math.inf)
    ok = np.isfinite(d) & np.isfinite(s) & (s > 0) & (fused != 0)
    info = float(np.sum(fused[ok] ** 2 / s[ok] ** 2))
    if info <= 0:
        return None
    k = float(np.sum(d[ok] * fused[ok] / s[ok] ** 2)) / info
    return [r["kind"], r["name"], r["detail"], round(k, 4), round(1 / math.sqrt(info), 4)]


def _coherence(e: dict, caps: dict[str, str]) -> dict | None:
    """The checks on an event's related prices (DESIGN.md 6.6), and the notes on its knock-on links that the engine
    capped, by the event at the other end. Snapshots from before the checks carry neither."""
    c = e.get("coherence")
    if c is None:
        return None
    return {"n": c["checks"], "contested": c["contested"], "flags": [f["note"] for f in c["findings"]], "caps": caps}


def _event(e: dict, today: str, caps: dict[str, str]) -> dict:
    odds, dl = e["odds"], e["delta"]
    fused = _vec(dl["value"], 0.0)
    dof = dl.get("dof") or 0
    out = {
        "id": e["id"], "t": e["title"], "s": e["short"], "cat": e["category"], "date": e["resolves"],
        "q": odds["q"], "ph": odds["p_hat"], "tier": e["tier"]["tier"], "qErr": odds["sigma"],
        "d": [round(float(x), 6) for x in fused],
        "m": _market(e),
        "miss": [[VENUES.get(c["venue"], c["venue"]), _title(c), c["reason"]] for c in e["match"]["near_misses"]] or None,
        "odds": [[r["source"], r["q"], r["sigma"]] for r in odds["readings"]],
        "src": [p for p in (_project(r, fused) for r in dl["readings"]) if p],
        "fusion": {"nMarket": dl["n_market"], "chi2": dl["chi2"], "dof": dof,
                   "crit": round(float(chi2.ppf(0.95, dof)), 2) if dof else None, "agree": bool(dl["agree"])},
        "tierWhy": e["tier"]["reason"],
        "why": e["why"],
        "matchNote": e["match"]["note"],
        "coh": _coherence(e, caps),
        "hist": None,
    }
    past = [(d, round(q, 4)) for d, q in e["history"] if d < today]  # closes before today; today's odds are q
    if len(past) >= 2:
        out.update(hist={"h0": past[0][1], "y": past[-1][1]}, series=[q for _, q in past],
                   seriesFrom=past[0][0], seriesNote=e.get("history_from"))
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "portfolio"


def build(snap: dict, analyses: list[dict]) -> dict:
    if snap.get("kind") != "redkeep.library":
        raise ValueError("not a library snapshot")
    for a in analyses:
        if a.get("kind") != "redkeep.analysis":
            raise ValueError("not a portfolio analysis")
        if a["library_as_of"] != snap["as_of"]:
            raise ValueError(f"{a['portfolio']['name']} was analyzed against the snapshot of {a['library_as_of']}, "
                             f"not this one ({snap['as_of']}); rerun `redkeep analyze`")

    tickers: dict[str, dict] = {}
    names: dict[str, str] = {}
    sample_rows, results, seen = [], [], set()
    meta = samples()
    for a in analyses:
        p = a["portfolio"]
        for ln in p["lines"]:
            hold = ln.get("holdings") or []
            names.update({tk: nm for tk, nm, _ in hold if nm})
            t = {"n": ln["name"] or ln["ticker"], "k": "Stock" if ln["kind"] == "CS" else (ln["kind"] or "Fund"),
                 "px": ln["price"], "b": [round(float(x), 6) for x in ln["b"]], "r2": ln["r2_oos"]}
            if hold:
                t["h"] = [[tk, round(100 * w, 4)] for tk, _, w in hold]
                more = (ln.get("n_holdings") or 0) - len(hold)
                t["rest"] = f"{more} more" if more > 0 else ""
                t["asOf"] = ln.get("holdings_date")
            tickers[ln["ticker"]] = t
        sm = meta.get(a.get("sample") or "")
        sid = sm.id if sm else _slug(p["name"])
        while sid in seen:
            sid += "-2"
        seen.add(sid)
        sample_rows.append({
            "id": sid, "name": sm.name if sm else p["name"], "who": sm.who if sm else "Custom",
            "note": sm.note if sm else "",
            "h": [[t, "sh" if u == "shares" else "usd", amt] for t, amt, u in p["holdings"]],
        })
        results.append({
            "sample": sid, "value": p["value"], "b": p["b"], "fit": p["fit"], "horizon": a["horizon_months"],
            "whatIf": a["what_if"],
            "events": [{k: e[k] for k in ("id", "q", "direct", "total", "if_yes", "if_no", "swing_pct")} for e in a["events"]],
            "sim": {k: a["simulation"][k] for k in ("n", "mean", "sd", "p5", "p50", "p95", "es5")},
        })

    evs, used = engine_inputs(snap)
    caps: dict[str, dict[str, str]] = defaultdict(dict)  # links capped at what today's odds allow, from either end
    for ln in snap["links"]:
        if ln.get("capped"):
            caps[ln["parent"]][ln["child"]] = caps[ln["child"]][ln["parent"]] = ln["capped"]
    links, _ = validate_links({e.id: e for e in evs}, used)  # the links the engine itself applies
    by_key = {f["key"]: f for f in snap["factors"]}
    data = {
        "kind": "redkeep.app", "version": 1, "asOf": snap["as_of"], "today": snap["today"],
        "exposures": "backend" if analyses else "illustrative",
        "catalogue": snap["catalogue"], "windows": snap["windows"], "warnings": snap["warnings"],
        "factors": [{"k": f.key, "typical": by_key.get(f.key, {}).get("typical"),
                     "proxy": _proxy(by_key.get(f.key, {}).get("source"), f.name)} for f in FACTORS],
        "events": [_event(e, snap["today"], caps[e["id"]]) for e in snap["events"]],
        "links": [[ln.parent, ln.child, ln.w] for ln in links],
        "linkChecks": [{k: ln.get(k) for k in ("parent", "child", "status", "w_proposed", "w_data", "w", "t", "n", "note", "capped")}
                       for ln in snap["links"]],
        "tickers": tickers, "names": names, "samples": sample_rows, "results": results,
    }
    return finite(data)


def export(snap: dict, analyses: list[dict]) -> str:
    """``live.js`` for the prototype: one ``RK_LIVE`` constant."""
    text = json.dumps(build(snap, analyses), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    text = text.replace("<", "\\u003c")  # keeps "</script" out of the page it is inlined into
    return f"/* Generated by `redkeep app` from the library snapshot of {snap['as_of']}. Do not edit. */\nconst RK_LIVE = {text};\n"
