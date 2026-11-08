"""Command line: ``redkeep check | refresh | analyze | app | serve``."""

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


def cmd_app(s: Settings, args) -> int:
    from .appdata import export

    snap = json.loads(Path(args.snapshot or s.out_dir / "library-latest.json").read_text())
    analyses = [json.loads(Path(p).read_text()) for p in args.analysis]
    out = Path(args.out)
    out.write_text(export(snap, analyses))
    with_what = (f"{len(analyses)} portfolio analys{'is' if len(analyses) == 1 else 'es'}" if analyses else
                 "no portfolio analyses (the app keeps its illustrative portfolios and labels their exposures as such)")
    print(f"Wrote {out}: {len(snap['events'])} events from the snapshot of {snap['as_of']}, {with_what}")
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
    x = sub.add_parser("app", help="export a snapshot, and any portfolio analyses, for the prototype app")
    x.add_argument("--snapshot", help="library snapshot (default: the latest)")
    x.add_argument("--analysis", nargs="*", default=[], metavar="FILE",
                   help="analyses from `redkeep analyze`; without any, the app keeps its illustrative portfolios")
    x.add_argument("--out", required=True, help="where to write live.js")
    v = sub.add_parser("serve", help="run the API")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    s = Settings.from_env()
    return {"check": cmd_check, "refresh": cmd_refresh, "analyze": cmd_analyze, "app": cmd_app, "serve": cmd_serve}[args.cmd](s, args)


if __name__ == "__main__":
    sys.exit(main())
