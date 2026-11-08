# The Red Keep: backend

The engine behind the prototype, as a vertical slice (milestone M3 in the [build plan](../docs/DESIGN.md#15-build-plan)): ten events and a handful of portfolios, end to end on public data.

```
markets (Polymarket, Kalshi) ──► match each event (Claude) ──► odds, or "nothing fits"
factor data (FRED, Massive) ──► jump windows, past events, AI prior ──► fused jump and tier
holdings (Massive) ──► look-through and exposures ──► impacts, priced-in moves, swing, simulation
```

`refresh` builds the shared event library and saves a dated snapshot. `analyze` runs a portfolio against the latest snapshot in a few seconds. `app` feeds a snapshot, and any analyses, into the prototype, which then offers a switch between its illustrative data and the backend's. With no analyses the prototype shows the snapshot's events against its own illustrative portfolios and labels their exposures as illustrative: that needs only the Anthropic key.

## Setup

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/):

```bash
cd backend
uv sync                      # creates .venv with the locked dependencies
uv run pytest                # the offline tests, a few seconds
```

The tests need no keys and no network: they use market responses copied from the live APIs (with made-up values) and seeded synthetic prices. One test runs the prototype's own model in Node on an export and checks it gives the engine's numbers; it skips itself when Node isn't installed.

## Keys and settings

Set these in the environment. They are read at start-up and never written to disk or logs.

| Variable | Needed for | Without it |
|---|---|---|
| `ANTHROPIC_API_KEY` | `refresh`: Claude judges every candidate market and writes the AI prior | `refresh` stops with a message |
| `MASSIVE_API_KEY` | Prices for exposures and the China, chips and gold factors; ETF holdings for look-through | `refresh` runs on the seven FRED factors with a warning; `analyze` stops with a message |
| `REDKEEP_MODEL` | Optional: the Claude model to use | The newest Opus model the key can call |
| `REDKEEP_EFFORT` | Optional: `low`, `medium` (default) or `high` | |
| `REDKEEP_HOME` | Optional: where `.cache/` and `out/` live | `backend/` |

Polymarket, Kalshi and FRED need no keys. The machine needs outbound access to `gamma-api.polymarket.com`, `clob.polymarket.com`, `api.elections.kalshi.com`, `fred.stlouisfed.org`, `api.massive.com` and `api.anthropic.com`.

## Commands

```bash
uv run redkeep check                                   # can we reach every source, and what does the Massive key include?
uv run redkeep refresh                                 # build the event library; saves out/library-<time>.json and library-latest.json
uv run redkeep analyze --sample allweather             # a sample portfolio: allweather, buffett, permanent, qqq, swensen, tech
uv run redkeep analyze --holdings "VTI:300000, TLT:400000, NVDA:120sh"
uv run redkeep analyze --sample qqq --what-if tariffs-china=yes --horizon 12
uv run redkeep app --analysis out/analysis-allweather-latest.json out/analysis-qqq-latest.json \
                   --out ../prototype/src/live.js      # then: python3 ../prototype/build.py
uv run redkeep app --out ../prototype/src/live.js      # events only: the prototype keeps its illustrative portfolios
uv run redkeep serve                                   # the API on http://127.0.0.1:8000 (docs at /docs)
```

The API serves the latest snapshot (`GET /events`, `GET /events/{id}`, either with `?as_of=` for an older one) and analyzes portfolios against it (`POST /portfolio/analyze` with holdings, an optional horizon and what-ifs).

`refresh` takes a few minutes the first time and caches every HTTP response and every Claude call under `.cache/`, so a rerun is quick and returns the same matches. Each Claude call is saved with its request and response under `.cache/ai/`, so any match or prior can be traced to the call that produced it.

## What runs, stage by stage

| Stage | In this slice | Design doc |
|---|---|---|
| Market catalogue | Yes/no markets in the 2,000 Polymarket events with the most trading in the last day (as deep as its API pages), plus targeted searches for the library's terms; every open Kalshi market outside sports, entertainment and weather | 6.2 |
| Matching | BM25 retrieval (contracts that close within a week of the event's deadline get a boost), then Claude with a fixed rubric and structured output: same question, implies, implied by, part of (mutually exclusive outcomes that are summed, like "cut 25 bps" and "cut 50+ bps"), overlaps or unrelated, with a score, the quoted rule and a reason. Then, in order: a match, a ladder interpolated along thresholds or deadlines (isotonic fit, never extrapolated), a bound, or "nothing fits" with the near-misses and why they were rejected | 6.3 |
| Odds | Mid of bid and ask, with an error from the spread and the volume; markets on both venues are cross-checked, and a disagreement beyond their errors makes the event tier B. The AI never overrides a market | 4.5, 6.4 |
| Jump | Daily jump windows (odds read at the 4 p.m. New York close; factor moves regressed on all odds changes on days with a 5-point move), past similar events (an event study around analog dates) and the AI prior, fused by precision with a χ² check across market readings | 4.5, 4.6, 6.4 |
| Tiers | A: a well-matched liquid market and two market readings of the jump that agree. B: readings disagree. C: priced, but one market reading or a weak match. D: no market fits | 6.5 |
| Links | Proposed knock-on links are used only when the two events' odds histories agree in direction (t ≥ 2), with the measured slope | 4.4, 6.6 |
| Exposures | Weighted ridge regression of each holding's daily returns on the ten factors over two years (recent days count more), fit tested out of sample on the last six months; look-through via ETF Global holdings | 6.1 |
| Impact | `Δ = b · δ`, knock-ons through the graph, priced-in moves `(1 − q)Δ` and `−qΔ`, swing `|Δ|√(q(1 − q))`, and a seeded 20,000-run simulation over the event graph | 4.1 to 4.4, 4.7 |

## Not yet, and why it matters

- **Only one market reading of each jump.** Daily ETF windows are the only market reading in this slice, so no event can reach tier A yet: tier A needs two that agree. Options (event-day volatility and the two-world fit, milestone M6) and Hyperliquid's weekend windows add the second and third.
- **Daily windows, not 30-minute ones.** A day holds other news, so the error bars are wider than the design's intraday windows would give. They carry through fusion honestly.
- **Ten hand-picked events.** The funnel that screens every market (Laya taught by Claude, M4) replaces the hand-written library.
- **No calibration yet.** Calibrated odds equal market odds until the long-shot study (E2) fits the correction.
- **Fund-level exposures.** A fund's arrow comes from its own returns; per-holding regressions come next and give the same arrow for an unchanged fund.
- **Point-in-time storage.** Responses are cached, not yet stored as a queryable history (DuckDB, M1).

## Layout

```
backend/
├── data/              events.toml (the event library), portfolios.toml (sample portfolios)
├── redkeep/
│   ├── data/          polymarket.py, kalshi.py, fred.py, massive.py
│   ├── matching/      retrieve.py, judge.py, ladders.py, decide.py
│   ├── evidence/      odds.py, windows.py, analogs.py, prior.py
│   ├── engine/        fusion.py, impact.py, simulate.py
│   ├── factors.py, factordata.py, exposure.py
│   ├── pipeline.py    refresh and analyze
│   ├── appdata.py     the export for the prototype
│   └── api.py, cli.py
└── tests/             fixtures copied from live responses, synthetic prices, and the offline end-to-end run
```
