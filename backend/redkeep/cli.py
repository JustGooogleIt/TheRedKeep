"""Command line: ``redkeep check | refresh | analyze | calibrate | jumps | replay | record | store | app | serve``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import ANTHROPIC_KEY_NAMES, Settings
from .portfolio import parse_holdings, samples


def _pct(x, signed=True):
    if x is None:
        return "   n/a"
    return f"{x:+6.2f}%" if signed else f"{x:6.1f}%"


def cmd_check(s: Settings, args) -> int:
    """Can we reach every source? (DESIGN.md milestone M0)"""
    from dataclasses import replace

    from .pipeline import make_clients

    claude_error: Exception | None = None
    try:
        c = make_clients(s)
    except Exception as e:  # the Claude model lookup failed: report it below and check the rest
        claude_error = e
        c = make_clients(replace(s, anthropic_api_key=None))

    def claude() -> str:
        if claude_error:
            raise claude_error
        return f"model {c.model} available"

    def hyperliquid() -> str:
        """The perps the weekend windows look for, as trade[XYZ] lists them, and a day of candles for the first."""
        from .evidence.weekend import PERPS

        listed = c.hyperliquid.coins("xyz")
        found = {p.name: next((x for x in p.coins if x in listed), None) for p in PERPS}
        if not any(found.values()):
            raise RuntimeError(f"none of {[x for p in PERPS for x in p.coins]} among {len(listed)} listed perps")
        coin = next(x for x in found.values() if x)
        now = datetime.now(timezone.utc)
        n = len(c.hyperliquid.candles(coin, now - timedelta(days=1), now))
        return (", ".join(f"{x} {listed[x].get('maxLeverage')}x" if x else f"no {name} perp" for name, x in found.items())
                + f"; {n} hourly {coin} candles in the last day")

    today = date.today()
    checks = [
        ("Polymarket (Gamma)", lambda: f"{len(c.polymarket.search('recession', limit=3))} events for 'recession'"),
        ("Kalshi", lambda: f"{len(c.kalshi.http.get_json('https://api.elections.kalshi.com/trade-api/v2/events', {'limit': 5, 'status': 'open'}).get('events', []))} open events"),
        ("FRED", lambda: f"DGS10 has {len(c.fred.series(['DGS10'], today - timedelta(days=20), today).get('DGS10', {}))} recent days"),
        ("Hyperliquid (trade[XYZ])", hyperliquid),
    ]
    if c.massive.available:
        checks.append(("Massive (prices)", lambda: f"SPY has {len(c.massive.closes('SPY', today - timedelta(days=20), today))} recent closes"))
        checks.append(("Massive (ETF holdings)", lambda: (lambda r: f"{len(r[1])} QQQ holdings on {r[0]}" if r else "not included in this key")(c.massive.constituents('QQQ', today))))
    if s.anthropic_api_key:
        checks.append(("Claude API", claude))
    ok = True
    for name, fn in checks:
        try:
            print(f"  ok    {name:<24} {fn()}")
        except Exception as e:  # report every failure, keep going
            ok = False
            print(f"  FAIL  {name:<24} {type(e).__name__}: {str(e)[:160]}")
    keys = (
        (s.anthropic_key_name or " or ".join(ANTHROPIC_KEY_NAMES), bool(s.anthropic_api_key)),
        ("MASSIVE_API_KEY", bool(s.massive_api_key)),
    )
    for name, present in keys:
        print(f"  {'set ' if present else 'MISSING'}  {name}")
    return 0 if ok else 1


def print_library(snap: dict) -> None:
    print(f"\nEvent library as of {snap['as_of']}  ({snap['catalogue']['polymarket']} Polymarket and "
          f"{snap['catalogue']['kalshi']} Kalshi markets searched)\n")
    print(f"  {'event':<20} {'tier':<4} {'odds':>6}  {'from':<28} match")
    for e in snap["events"]:
        o = e["odds"]
        src = ", ".join(r["source"] for r in o["readings"])[:28]
        print(f"  {e['short']:<20} {e['tier']['tier']:<4} {o['q'] * 100:5.1f}%  {src:<28} {e['match']['kind']}: {e['match']['note'][:90]}")
    print()
    for l in snap["links"]:
        extra = f" (slope {l['w_data']:+.2f}, t={l['t']:+.1f}, {l['n']} days)" if l.get("w_data") is not None else ""
        print(f"  link {l['parent']} -> {l['child']}: {l['status']}{extra}")
    for w in snap.get("warnings", []):
        print(f"  warning: {w}")


def cmd_refresh(s: Settings, args) -> int:
    from .pipeline import make_clients, refresh, save

    snap = refresh(s, make_clients(s), today=date.fromisoformat(args.today) if args.today else None)
    path = save(snap, s.out_dir, "library")
    print_library(snap)
    print(f"\nSaved {path}")
    return 0


def print_analysis(a: dict) -> None:
    p = a["portfolio"]
    fit = f"{p['fit'] * 100:.0f}%" if p["fit"] is not None else "n/a"
    print(f"\n{p['name']}: ${p['value']:,.0f}, fit {fit} (share of daily moves the factors explain, last 6 months)\n")
    print(f"  {'event':<20} {'tier':<4} {'odds':>6} {'jump':>8} {'if yes':>8} {'if no':>8} {'swing':>8} {'swing $':>10}")
    for e in a["events"]:
        flag = "" if e["in_horizon"] else "  (after the horizon)"
        print(f"  {e['short']:<20} {e['tier']:<4} {e['q'] * 100:5.1f}% {_pct(e['total'])} {_pct(e['if_yes'])} "
              f"{_pct(e['if_no'])} {_pct(e['swing_pct'], False)} {e['usd']['swing']:>10,.0f}{flag}")
    sim = a["simulation"]
    print(f"\n  Simulated {a['horizon_months']:.0f}-month outcomes from these events: median {_pct(sim['p50'])}, "
          f"5th percentile {_pct(sim['p5'])}, 95th {_pct(sim['p95'])}, worst-5% average {_pct(sim['es5'])}")


def cmd_analyze(s: Settings, args) -> int:
    from .pipeline import analyze, load_latest, make_clients, save

    if args.sample:
        sm = samples()[args.sample]
        holdings, name = sm.holdings, sm.name
    elif args.holdings:
        holdings, name = parse_holdings(args.holdings), "Portfolio"
    else:
        print("give --sample or --holdings", file=sys.stderr)
        return 2
    snap = json.loads(Path(args.snapshot).read_text()) if args.snapshot else load_latest(s.out_dir, "library")
    what_if = dict(kv.split("=", 1) for kv in args.what_if or [])
    a = analyze(s, make_clients(s, claude=False), snap, holdings, name=name, horizon_months=args.horizon, what_if=what_if or None)
    a["sample"] = args.sample
    path = save(a, s.out_dir, f"analysis-{args.sample or 'custom'}")
    print_analysis(a)
    print(f"\nSaved {path}")
    return 0


def print_calibration(rep: dict) -> None:
    venues = ", ".join(f"{n:,} {v.title()}" for v, n in sorted(rep["markets"].items()))
    print(f"\nCalibration: {venues} resolved markets ({rep['events']:,} events, {rep['observations']:,} prices), "
          f"closed {rep['closed'][0]} to {rep['closed'][1]}. Fitted on markets that closed before {rep['split']}, "
          f"scored on the rest.\n")
    print(f"  {'days':>4} {'fit':>5} {'test':>5}  {'slope':<11} {'log loss raw':>12} {'corrected':>9}  {'gain, 95% range':<26} use")
    for r in rep["horizons"]:
        if "raw" not in r:
            print(f"  {r['days']:>4} {r['train']:>5} {r['test']:>5}  {r.get('note', '')}")
            continue
        g = r["gain"]
        use = f"b = {r['b']:.2f}" if r["used"] else "raw odds"
        print(f"  {r['days']:>4} {r['train']:>5} {r['test']:>5}  {r['fit_b']:4.2f} ± {r['fit_se']:4.2f}"
              f" {r['raw']['log_loss']:>12.4f} {r['corrected']['log_loss']:>9.4f}  "
              f"{g[0]:+.4f} ({g[1]:+.4f} to {g[2]:+.4f})  {use}")
    print("\n  A slope above 1 means long shots were overpriced and favorites underpriced. A horizon's slope is used "
          "only if it\n  beat raw odds on the later markets across the whole 95% range of its gain; it is then refitted "
          "on all of them.")


def cmd_calibrate(s: Settings, args) -> int:
    """The long-shot study (DESIGN.md 6.4, eval E2) on resolved Polymarket and Kalshi markets."""
    from .evidence import calibration, resolved
    from .pipeline import finite, make_clients, save

    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = now.strftime("%Y%m%dT%H%M%S")
    warnings: list[str] = []
    if args.from_file:
        obs = calibration.read_obs(Path(args.from_file))
        sources: dict = {"observations": str(args.from_file)}
        print(f"Read {len(obs):,} prices from {args.from_file}")
    else:
        start = date.fromisoformat(args.since)
        end = date.fromisoformat(args.until) if args.until else now.date() - timedelta(days=1)
        print(f"Collecting resolved markets that closed from {start} to {end} (up to {args.markets:,} a venue)")
        c = make_clients(s, claude=False)
        obs, stats, warnings = resolved.collect(c.polymarket, c.kalshi, start, end, total=args.markets)
        s.out_dir.mkdir(parents=True, exist_ok=True)
        obs_path = s.out_dir / f"calibration-obs-{stamp}.jsonl.gz"
        calibration.write_obs(obs, obs_path)
        sources = {"since": start.isoformat(), "until": end.isoformat(), "venues": stats, "observations": obs_path.name}
    for w in warnings:
        print(f"  warning: {w}")
    try:
        report = calibration.study(obs)
    except ValueError as e:
        print(f"No study: {e}", file=sys.stderr)
        return 1
    out = {"kind": "redkeep.calibration-study", "as_of": now.isoformat(), "sources": sources, "warnings": warnings, **report}
    path = save(finite(out), s.out_dir, "calibration")
    print_calibration(out)
    print(f"\nSaved {path}" + ("" if args.from_file else f" and the prices behind it, {sources['observations']}"))
    if args.write:
        s.calibration_file.write_text(json.dumps(calibration.to_json(report, now.date().isoformat()), indent=1,
                                                 ensure_ascii=False) + "\n")
        used = any(r["used"] for r in report["horizons"])
        print(f"Wrote {s.calibration_file}: " + ("the next refresh gives calibrated odds from these slopes" if used else
                                                "no slope beat raw odds reliably, so calibrated odds stay at market odds"))
    return 0


def cmd_jumps(s: Settings, args) -> int:
    """Jump accuracy on resolved markets (DESIGN.md section 10, evals E3 to E5)."""
    from . import jumps
    from .pipeline import finite, make_clients, save

    if args.from_file:
        old = json.loads(Path(args.from_file).read_text())
        if old.get("kind") != "redkeep.jumps":
            print(f"{args.from_file} is not a saved jumps study", file=sys.stderr)
            return 1
        print(f"Read {len(old['rows']):,} markets from {args.from_file}")
        rep = jumps.report(jumps.cases_from(old), {**old["sources"], "rerun_of": str(args.from_file)}, old["warnings"],
                           typical=old.get("typical"))
    else:
        start = date.fromisoformat(args.since)
        end = date.fromisoformat(args.until) if args.until else datetime.now(timezone.utc).date() - timedelta(days=1)
        print(f"Resolved markets that closed from {start} to {end} (up to {args.markets:,} a venue)")
        rep = jumps.run(make_clients(s, claude=False), start, end, per_venue=args.markets)
    print_jumps(rep)
    path = save(finite(rep), s.out_dir, "jumps")
    print(f"\nSaved {path}")
    return 0


def _share(x) -> str:
    return "  n/a" if x is None else f"{100 * x:+5.0f}%"


def _range(r, signed: bool = True) -> str:
    if not r:
        return ""
    fmt = (lambda v: f"{100 * v:+.0f}%") if signed else (lambda v: f"{100 * v:.0f}%")
    return f" (95% range {fmt(r[0])} to {fmt(r[1])})"


def print_jumps(rep: dict) -> None:
    from .factors import FACTORS

    for w in rep["warnings"]:
        print(f"  warning: {w}")
    for v, st in rep["sources"].get("venues", {}).items():
        if isinstance(st, dict):
            why = ", ".join(f"{n} {k}" for k, n in sorted(st.get("left_out", {}).items(), key=lambda x: -x[1]))
            print(f"  {v.title()}: {st.get('sampled', 0)} read, {st.get('cases', 0)} with a reading the evening before"
                  + (f"; left out: {why}" if why else ""))
    e3, e4, e5 = rep["e3"], rep["e4"], rep["e5"]
    print(f"\nJumps: {rep['cases']} resolved markets, one per event ({e3['days']} resolution days, {e3['pairs']} "
          f"market-factors with a reading). Granularity: "
          + ", ".join(f"{n} {g}" for g, n in rep["granularity"].items()))
    if not e3["pairs"]:
        print("  Nothing to score.")
        return
    print(f"\n  E3  resolution-day moves explained: {_share(e3['explained_share']).strip()}{_range(e3['explained_range'])}")
    print(f"      with no move where a reading can't resolve the factor ({e3['resolved_pairs']} of {e3['pairs']} "
          f"resolved): {_share(e3['explained_share_resolved']).strip()}{_range(e3['explained_resolved_range'])}")
    print(f"      the most any jump could explain, given each factor's day-to-day noise: "
          f"{_share(e3['explainable']).strip()}")
    print(f"      signs on clear moves: {e3['clear_signs_right']} of {e3['clear_moves']} right"
          + _range(e3["clear_signs_range"], signed=False)
          + (f"; rank correlation {e3['rank_corr']:+.2f}" if e3["rank_corr"] is not None else ""))
    print(f"\n  {'factor':<24} {'pairs':>5} {'explained':>9} {'signs':>9} {'rank':>6} {'80% cover':>9}")
    names = {f.key: f.name for f in FACTORS}
    for k, v in e3["by_factor"].items():
        sg = f"{v['clear_signs_right']}/{v['clear_moves']}" if v["clear_moves"] else "-"
        rk = f"{v['rank_corr']:+.2f}" if v["rank_corr"] is not None else "-"
        cv = f"{100 * v['coverage_80']:.0f}%" if v["coverage_80"] is not None else "-"
        print(f"  {names.get(k, k):<24} {v['pairs']:>5} {_share(v['explained_share']):>9} {sg:>9} {rk:>6} {cv:>9}")
    sc = e4["scale"]
    print(f"\n  E4  realized jumps inside the 80% interval: {100 * e4['coverage_80']:.0f}%")
    if sc["scale"]:
        print(f"      a scale of {sc['scale']:.2f} on the error bars gives 80% on days before {sc['split_day']}; on the "
              f"{sc['later_pairs']} later pairs it covers {100 * sc['later_coverage_scaled']:.0f}% "
              f"(unscaled {100 * sc['later_coverage']:.0f}%)")
    for name in ("precise", "vague"):
        x = e5[name]
        cover = f"{100 * x['coverage_80']:.0f}%" if x["coverage_80"] is not None else "n/a"
        print(f"  E5  {name:<7} readings: {x['markets']:>4} markets, explained {_share(x['explained_share']).strip()}, "
              f"signs {x['clear_signs_right']} of {x['clear_moves']}, 80% cover {cover}")
    print(f"  E6  {rep['e6']}")


def cmd_replay(s: Settings, args) -> int:
    """Replay a past episode day by day on the markets' archived prices (DESIGN.md section 10, eval E8)."""
    from .pipeline import make_clients, save
    from .replay import load_episode, run, save as save_replay

    ep = load_episode(args.episode)
    rep = run(s, make_clients(s), ep)
    print_replay(rep)
    path = save(rep, s.out_dir, f"replay-{ep.name}")
    print(f"\nSaved {path}")
    if args.write:
        print(f"Wrote {save_replay(rep)}: `redkeep app` puts this replay in the app")
    return 0


def print_replay(rep: dict) -> None:
    eq = next(f for f in rep["factors"] if f["key"] == "eq")
    typ = eq["typical"] or float("nan")
    days = rep["days"]
    print(f"\n{rep['title']}: {len(days)} trading days from {days[0]['d']} to {days[-1]['d']}, first evening {rep['base']}")
    for w in rep["warnings"]:
        print(f"  warning: {w}")
    print("\nThe events and their markets:")
    for e in rep["events"]:
        src = e["odds"]["from"] or "no market"
        gran = "/".join(e["odds"]["granularity"]) if e["odds"]["granularity"] else ""
        print(f"  {e['s']:<20} {e['match']['kind']:<6} listed {e['listed'] or 'never':<10}  {src}" + (f" ({gran})" if gran else ""))

    def pct(z):
        return "    n/a" if z is None else f"{z * typ:+6.2f}%"

    print("\nStocks (S&P 500) day by day: the day's move, and the engine's events times their odds changes")
    for d in days:
        print(f"  {d['d']}  {pct(d['z'][eq_i(rep)])} {pct(d['explained'][eq_i(rep)])}   {d['note'][:70]}")
    print("\nCalls, from the evening before each event resolved (stocks):")
    names = {e["id"]: e["s"] for e in rep["events"]}
    for c in rep["calls"]:
        if c["why"]:
            print(f"  {names[c['event']]:<20} {c['date']}  {c['why']}")
            continue
        print(f"  {names[c['event']]:<20} {c['date']}  odds {c['q'] * 100:4.0f}%  expected {pct(c['pred'][eq_i(rep)])}  "
              f"the day {pct(c['real'][eq_i(rep)])}")
    m = rep["metrics"]
    cl, ex = m["calls"], m["explained"]
    share = "n/a" if ex["explained_share"] is None else f"{ex['explained_share'] * 100:.0f}%"
    corr = "n/a" if ex["corr"] is None else f"{ex['corr']:.2f}"
    print(f"\nE8: calls had the right sign on {cl['factor_signs_right']} of {cl['factor_signs']} factor moves. "
          f"Explained moves account for {share} of the moves' sum of squares over {ex['cells']} day-factors "
          f"(correlation {corr}), with the right sign on {ex['big_days_right']} of {ex['big_days']} big moves.")


def eq_i(rep: dict) -> int:
    return [f["key"] for f in rep["factors"]].index("eq")


def cmd_record(s: Settings, args) -> int:
    """Bring the point-in-time store up to date (DESIGN.md milestone M1)."""
    from .pipeline import make_clients
    from .record import record

    c = make_clients(s, claude=False)
    rec = record(s, c, intervals=tuple(args.interval or ["1h"]), engine_perps=args.engine_perps, skip=tuple(args.skip))
    for w in rec.warnings:
        print(f"  warning: {w}")
    added = ", ".join(f"{n:,} {t}" for t, n in rec.added.items() if n) or "nothing new"
    print(f"\nRecorded {rec.started:%Y-%m-%d %H:%M} UTC into {s.store_dir}: {added}")
    if args.compact:
        print_compaction(c.store.compact())
    return 0


def print_compaction(done: dict[str, tuple[int, int]]) -> None:
    merged = [f"{t} {a} to {b}" for t, (a, b) in done.items() if a != b]
    print("Compacted segments: " + (", ".join(merged) if merged else "nothing to merge"))


def cmd_store(s: Settings, args) -> int:
    """What the point-in-time store holds."""
    from .store import Store

    st = Store(s.store_dir)
    if args.compact:
        print_compaction(st.compact())
    rows = st.status()
    size = sum(r["bytes"] for r in rows)
    print(f"\nPoint-in-time store at {s.store_dir}: {sum(r['segments'] for r in rows)} segments, {size / 1e6:.1f} MB\n")
    print(f"  {'table':<9} {'rows':>11} {'segments':>8}  {'about':<33} seen")
    for r in rows:
        a, b = (str(x)[:16] if x else "" for x in r["about"])
        s0, s1 = (f"{x:%Y-%m-%d %H:%M}" if x else "" for x in r["seen"])
        about = f"{a} to {b}" if a else ""
        seen = f"{s0} to {s1}" if s0 else ""
        print(f"  {r['table']:<9} {r['rows']:>11,} {r['segments']:>8}  {about:<33} {seen}")
    return 0


def cmd_app(s: Settings, args) -> int:
    from .appdata import export
    from .replay import REPLAYS

    snap = json.loads(Path(args.snapshot or s.out_dir / "library-latest.json").read_text())
    analyses = [json.loads(Path(p).read_text()) for p in args.analysis]
    rpath = Path(args.replay) if args.replay else REPLAYS / "tariffs-2025.json"
    rep = None if args.no_replay or not rpath.exists() else json.loads(rpath.read_text())
    out = Path(args.out)
    out.write_text(export(snap, analyses, rep))
    with_what = (f"{len(analyses)} portfolio analys{'is' if len(analyses) == 1 else 'es'}" if analyses else
                 "no portfolio analyses (the app keeps its illustrative portfolios and labels their exposures as such)")
    replay = f"the replay of {rep['title']} from {rpath}" if rep else "the illustrative replay"
    print(f"Wrote {out}: {len(snap['events'])} events from the snapshot of {snap['as_of']}, {with_what}, and {replay}")
    return 0


def cmd_serve(s: Settings, args) -> int:
    import uvicorn

    uvicorn.run("redkeep.api:app", host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="redkeep", description="The Red Keep: scenario analysis engine")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="check that every data source is reachable")
    r = sub.add_parser("refresh", help="rebuild the event library from live data")
    r.add_argument("--today", help="as-of date (YYYY-MM-DD), for reruns")
    a = sub.add_parser("analyze", help="analyze a portfolio against the latest library")
    a.add_argument("--sample", choices=sorted(samples()))
    a.add_argument("--holdings", help='e.g. "VTI:300000, TLT:400000, NVDA:100sh"')
    a.add_argument("--snapshot", help="library snapshot (default: the latest)")
    a.add_argument("--horizon", type=float, help="months (default 6)")
    a.add_argument("--what-if", action="append", metavar="EVENT=yes|no")
    k = sub.add_parser("calibrate", help="measure the long-shot bias on resolved markets (eval E2)")
    k.add_argument("--since", default="2025-01-01", help="first close date (YYYY-MM-DD, default 2025-01-01)")
    k.add_argument("--until", help="last close date (default: yesterday)")
    k.add_argument("--markets", type=int, default=1500, help="markets to read on each venue (default 1500)")
    k.add_argument("--from", dest="from_file", metavar="FILE",
                   help="rerun on the prices an earlier run saved (out/calibration-obs-*.jsonl.gz), without fetching")
    k.add_argument("--write", action="store_true", help="save the slopes to data/calibration.json for the engine to use")
    j = sub.add_parser("jumps", help="score the engine's jumps on resolved markets (evals E3 to E5)")
    j.add_argument("--since", default="2025-01-01", help="first close date (YYYY-MM-DD, default 2025-01-01)")
    j.add_argument("--until", help="last close date (default: yesterday)")
    j.add_argument("--markets", type=int, default=600, help="markets to read on each venue (default 600)")
    j.add_argument("--from", dest="from_file", metavar="FILE",
                   help="rerun the study on the markets an earlier run saved (out/jumps-*.json), without fetching")
    rp = sub.add_parser("replay", help="replay a past episode on archived prices (eval E8)")
    rp.add_argument("--episode", default="tariffs-2025", help="an episode in data/episodes, or a .toml file")
    rp.add_argument("--write", action="store_true", help="save it to data/replays for the app")
    from .data.hyperliquid import INTERVALS

    rc = sub.add_parser("record", help="add what's new to the point-in-time store: candles, quotes, odds, factor series")
    rc.add_argument("--interval", action="append", choices=list(INTERVALS),
                    help="Hyperliquid candle interval (default 1h); repeat for more")
    rc.add_argument("--engine-perps", action="store_true",
                    help="record only the perps the weekend windows use, not every trade[XYZ] perp")
    rc.add_argument("--skip", action="append", default=[], choices=["perps", "markets", "factors"],
                    help="leave out a source; repeat for more")
    rc.add_argument("--compact", action="store_true", help="merge the store's segments afterwards")
    so = sub.add_parser("store", help="show what the point-in-time store holds")
    so.add_argument("--compact", action="store_true", help="first merge each month's segments into one")
    x = sub.add_parser("app", help="export a snapshot, and any portfolio analyses, for the prototype app")
    x.add_argument("--snapshot", help="library snapshot (default: the latest)")
    x.add_argument("--analysis", nargs="*", default=[], metavar="FILE",
                   help="analyses from `redkeep analyze`; without any, the app keeps its illustrative portfolios")
    x.add_argument("--out", required=True, help="where to write live.js")
    x.add_argument("--replay", metavar="FILE", help="a replay from `redkeep replay` (default: data/replays/tariffs-2025.json)")
    x.add_argument("--no-replay", action="store_true", help="leave the replay out; the app keeps its illustrative one")
    v = sub.add_parser("serve", help="run the API")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    s = Settings.from_env()
    return {"check": cmd_check, "refresh": cmd_refresh, "analyze": cmd_analyze, "calibrate": cmd_calibrate,
            "jumps": cmd_jumps, "replay": cmd_replay, "record": cmd_record, "store": cmd_store, "app": cmd_app, "serve": cmd_serve}[args.cmd](s, args)


if __name__ == "__main__":
    sys.exit(main())
