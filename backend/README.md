# The Red Keep: backend

The engine behind the prototype, as a vertical slice (milestone M3 in the [build plan](../docs/DESIGN.md#15-build-plan)): ten events and a handful of portfolios, end to end on public data.

```
markets (Polymarket, Kalshi) ──► match each event (Claude) ──► odds, or "nothing fits"
factor data (FRED, Massive), weekend perps (Hyperliquid) ──► jump windows, past events, AI prior ──► fused jump and tier
holdings (Massive) ──► look-through and exposures ──► impacts, priced-in moves, swing, simulation
every read ──► the point-in-time store (DuckDB over Parquet), kept current by `redkeep record`
```

`refresh` builds the shared event library and saves a dated snapshot; it leaves out events past their deadlines, which have been decided, and warns so the library can take the next one. `analyze` runs a portfolio against the latest snapshot in a few seconds. `app` feeds a snapshot, and any analyses, into the prototype, which then offers a switch between its illustrative data and the backend's. With no analyses the prototype shows the snapshot's events against its own illustrative portfolios and labels their exposures as illustrative: that needs only the Anthropic key.

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
| `REDKEEP_ANTHROPIC_API_KEY` or `ANTHROPIC_API_KEY` | `refresh`: Claude judges every candidate market and writes the AI prior. The first name wins; use it where an agent host keeps `ANTHROPIC_API_KEY` for itself (a Claude Code cloud session did) | `refresh` stops with a message |
| `MASSIVE_API_KEY` | Prices for exposures and the China, chips and gold factors; hourly prices for the dollar fund UUP, which carry the Fed's noon dollar index to the 4 p.m. close (a key without them keeps the noon fixing); ETF Global's holdings for look-through; option chains for options (M6), not read yet. The project's key is on a paid plan (prices back to 2003, no call limit, holdings, option chains with implied volatility). A free key gives two years of end-of-day prices and no holdings: before those two years, stocks come from FRED's S&P 500 index and China, chips and gold are missing, and a fund's exposures come from its own returns | `refresh` runs on the seven FRED factors with a warning; `analyze` and the replay's portfolio scores stop or are left out with a message |
| `MASSIVE_CALLS_PER_MINUTE` | Optional: Massive's call limit. Calls are spaced to fit it, so a first run on the free plan waits about 13 seconds between calls, and the response cache spares later runs. Set `0` on a paid plan, which has no limit | 5, the free plan's limit |
| `REDKEEP_MODEL` | Optional: the Claude model to use | The newest Opus model the key can call |
| `REDKEEP_EFFORT` | Optional: `low`, `medium` (default) or `high` | |
| `REDKEEP_HOME` | Optional: where `.cache/`, `out/` and `store/` live | `backend/` |

Polymarket, Kalshi, FRED and Hyperliquid need no keys. The machine needs outbound access to `gamma-api.polymarket.com`, `clob.polymarket.com`, `api.elections.kalshi.com`, `fred.stlouisfed.org`, `api.hyperliquid.xyz`, `api.massive.com` and `api.anthropic.com`.

## Commands

```bash
uv run redkeep check                                   # can we reach every source, which perps does Hyperliquid list, what does the Massive key include?
uv run redkeep refresh                                 # build the event library; saves out/library-<time>.json and library-latest.json
uv run redkeep analyze --sample allweather             # a sample portfolio: allweather, buffett, permanent, qqq, swensen, tech
uv run redkeep analyze --holdings "VTI:300000, TLT:400000, NVDA:120sh"
uv run redkeep analyze --sample qqq --what-if tariffs-china=yes --horizon 12
uv run redkeep calibrate --write                       # the long-shot study on resolved markets (E2); --write saves data/calibration.json
uv run redkeep calibrate --from out/calibration-obs-<time>.jsonl.gz   # rerun the study on saved prices, without fetching
uv run redkeep replay --write                          # the April 2025 replay on archived prices (E8); --write saves data/replays/tariffs-2025.json
uv run redkeep jumps                                   # jump accuracy (E3 to E5) and joint error bars (E11) on resolved markets; --from out/jumps-<time>.json reruns it, --label scores material markets apart
uv run redkeep baseline --jumps out/jumps-<time>.json --odds out/calibration-obs-<time>.jsonl.gz
                                                       # Claude with no tools against the engine, on markets that resolved after its training data (E9)
uv run redkeep granger                                 # lead and lag: VARs, Granger tests and clocks (E10); --no-odds skips the prediction-market parts
uv run redkeep funnel label --n 2000                  # Claude labels an even sample of the catalogue (M4); adds to data/funnel/labels.jsonl
uv run redkeep funnel e0 --write                       # how well the screen those labels teach keeps what matters (E0)
uv run redkeep funnel screen                           # screen every listed market; the unsure band goes to Claude
uv run redkeep record                                  # add what's new to the point-in-time store; run it hourly
uv run redkeep record --interval 1h --interval 15m     # finer candles too (the API keeps 5,000 of each: 52 days at 15m)
uv run redkeep store                                   # what the store holds; --compact merges its segments
uv run redkeep app --analysis out/analysis-allweather-latest.json out/analysis-qqq-latest.json \
                   --out ../prototype/src/live.js      # then: python3 ../prototype/build.py
uv run redkeep app --out ../prototype/src/live.js      # events only: the prototype keeps its illustrative portfolios
                                                       # app adds data/replays/tariffs-2025.json when it exists (--replay FILE, --no-replay)
uv run redkeep serve                                   # the API on http://127.0.0.1:8000 (docs at /docs)
```

The API serves the latest snapshot (`GET /events`, `GET /events/{id}`, either with `?as_of=` for an older one) and analyzes portfolios against it (`POST /portfolio/analyze` with holdings, an optional horizon and what-ifs).

`calibrate` saves the full report (`out/calibration-<time>.json`: slopes, scores, and reliability tables overall, by venue and by category) and the prices behind it (`out/calibration-obs-<time>.jsonl.gz`). Resolved markets and their price histories don't change, so they stay cached and a rerun is quick.

`replay` reruns the engine on a past episode as it stood each evening (`data/episodes/tariffs-2025.toml`: the days, the events with the time each happened, their analogs and proposed links). It finds the markets listed then (closed and still-open Polymarket events under the episode's tags, settled Kalshi markets from the archive), has the judge match them as of the evening before the first day (an event that happened during the episode only among markets listed before it happened), reads their archived prices at each 4 p.m. New York close, and re-estimates every jump each evening from data up to that close. It prints each day's stock move against what the events explain, each call against its day, and the metrics fixed before the first run (section 10 of the design), and saves the whole run to `out/replay-<episode>-<time>.json`. With a Massive key it also scores each sample portfolio as it stood the evening before the first day (holdings valued at that close, exposures fitted on the two years before it, ETF holdings as ETF Global had them then): the share of its daily moves the events explain, the share the factors explain, and each call against the portfolio's move that day. With `--write` it also saves `data/replays/<episode>.json`, which `app` passes to the prototype's replay screen in place of the illustrative one; `check.js` then confirms that the app's own math gives the engine's numbers, on stocks and on each portfolio's arrow.

`jumps` asks whether the jump the engine measured the evening before a market resolved matched the factor moves on the day it resolved. It reads resolved yes/no markets of both venues (listed as for `calibrate`), finds each one's resolution day (the odds' final move to the outcome, from at least 15 points away), keeps one market per event, measures the jump from the 180 days of jump windows up to the close before, and scores it against the day's moves. It prints the share of those moves the jumps explain (beside the same share with no move where a reading can't resolve a factor, and the most any jump could explain, given each factor's day-to-day noise), the signs on clear moves, a table by factor, the 80% interval coverage with the scale that would fix it, and precise against vague readings, then saves `out/jumps-<time>.json` with every market's reading, so `--from` reruns the study without fetching.

The same run scores eval E11, the readings' joint error bars: whether each jump's covariance across factors, the error bubble the app draws, describes the resolution days better than the same error bars taken one by one. Each day is one forecast of its factor moves, scored by the Dawid–Sebastiani score with the design's power, two guards and a rule fixed in `redkeep/joint.py` before the first run; the rule sets how much of the correlations the engine uses (`engine/covariance.py`, `JOINT_WEIGHT`). The first run (November 13, 2026, `data/evals/jumps-2026-11-13.json`) was inconclusive with enough power, so the engine counts each factor's error on its own for now.

`baseline` is eval E9: is the engine better than just asking Claude? The baseline is the engine's own AI prior, one structured call with the question, a date and the factors' typical moves, and no data. A model knows how the events in its training data ended, so only markets that resolved after it are scored (`--after`, default August 1, 2026). From a saved jump study, Claude estimates each market's jump as of the evening before it resolved; the market's own jump-window reading and the engine's fusion of the two are scored beside it, on the same pairs and E3's metrics (the share explained, signs, rank correlation, 80% coverage), which also gives the source ablation of E6 on these markets. From saved calibration prices, Claude gives each market's probability 30 days before its deadline, scored against the market's price then by Brier score and log loss (E2's metrics). A check asks the same of markets that closed well before its training data ends: on those it should look far better, because it remembers them. Every answer is saved with the run, so `--from` scores it again without asking; the first run is `data/evals/baseline-2026-11-08.json`.

`granger` is eval E10: is an event's jump its first day's move, with every factor on one clock? It needs no Claude calls. It fits vector autoregressions to the factors' daily moves since 2008 (the whole span and three stretches; as the engine measures them, with the dollar at its noon fixing, and from 4 p.m. fund closes) and reports the lag order, the Granger links that survive a false-discovery control, and the out-of-sample forecast score against "no move", which decides whether jumps would be carried past their first day. It then measures each non-4 p.m. series' late share against a fund that closes at 4 p.m., compares 25 funds' exposure fit with the dollar carried and at noon, carries the April 2025 replay's explained moves forward through a VAR, and, from hourly odds over the last 200 days (`--odds-days`), tests the funnel's material markets for odds and factors that move a day late and for knock-on slopes that hold over longer spans and on later data. It prints the report and saves `out/granger-<time>.json`; the first run is `data/evals/granger-2026-11-13.json`, and [the research note](../docs/research/granger-causality.md) explains it.

`funnel` is milestone M4. `label` reads the live catalogue, applies the cheap filters and samples it evenly (at most two markets per group). Claude labels the sample in batches of 40: macro or not, and how far each factor would move if the market resolved yes rather than no, from −2 to +2 typical moves. `e0` splits the labels by group, sets the screen's lines on the training side, scores the test side, and writes a sheet of test markets for checking Claude's labels by hand. `screen` refits the screen on every label and reads the whole catalogue. It drops what the screen is sure doesn't matter, keeps what it is sure does, and sends up to 400 unsure markets to Claude, whose labels join the training set. It saves `out/funnel-<time>.json` with the material groups, each with a rough factor vector and a reason. The first run's labels are in `data/funnel/labels.jsonl`, so `e0` and `screen` run on them without labeling again, and its screen of the catalogue is `data/funnel/screen-2026-11-08.json`.

`refresh` takes a few minutes the first time and caches every HTTP response and every Claude call under `.cache/`, so a rerun is quick and returns the same matches. Each Claude call is saved with its request and response under `.cache/ai/`, so any match or prior can be traced to the call that produced it.

## The point-in-time store

`store/` keeps every price the backend reads with two clocks: the time the price is about and the time the store first saw it (`redkeep/store.py`). The data clients write to it as they read, so `refresh`, `analyze`, `calibrate` and `check` all add to it. `redkeep record` brings it up to date between runs, reading every source fresh rather than from the response cache (settled markets and old candles, which can't change, stay cached):

- **Hyperliquid candles** for every trade[XYZ] perp, from the last one stored. The API keeps only the latest 5,000 candles of each interval, so older ones exist only here. The first run reads all that the API still has.
- **Both catalogues**: every listed market's details and quotes, and which markets are no longer listed. Neither venue keeps past quotes.
- **Odds** of the markets the latest snapshot used or nearly used, hourly, from the last point stored.
- **Factor series**: FRED, and Massive closes and ETF holdings with a key. The last 30 days are read again each time, so a revision is stored as a new version.

| Table | One row is | Versioned by |
|---|---|---|
| `odds` | a market's Yes price at a time (`step` 60 for hourly, 1440 for daily) | venue, market, step, time |
| `candles` | a Hyperliquid candle | coin, interval, open time |
| `closes` | a daily level from FRED or Massive | source, series, day |
| `holdings` | one holding in an ETF's file | fund, effective date, ticker |
| `markets` | a listed market's details (title, rules, deadline, tokens) | venue, market |
| `quotes` | a market's bid, ask, last price and volume, and whether it is still listed | venue, market |

A write adds a row only when it differs from the latest version stored, so recording the same data again adds nothing, and an hourly read of some 66,000 markets stores only the quotes that moved. Reads take `as_of=T` (nothing about times after T, latest version) and `known=True` (only what the store had seen by T). `Store.catalogue(as_of=T)` rebuilds the market list as it stood at T.

Run `record` from cron on a machine that stays up, hourly, with a daily `redkeep store --compact` to merge the segments. The store records only from the day it is first scheduled.

## What runs, stage by stage

| Stage | In this slice | Design doc |
|---|---|---|
| Market catalogue | Yes/no markets in the 2,000 Polymarket events with the most trading in the last day (as deep as its API pages), plus targeted searches for the library's terms; every open Kalshi market outside sports, entertainment and weather | 6.2 |
| Matching | BM25 retrieval (contracts that close within a week of the event's deadline get a boost, and the best market of each of the top twelve groups gets a slot before any group's other rungs, so big ladders can't crowd out a lone market), then Claude with a fixed rubric and structured output: same question, implies, implied by, part of (mutually exclusive outcomes that are summed, like "cut 25 bps" and "cut 50+ bps"), overlaps or unrelated, with a score, the quoted rule and a reason. Then, in order: a match, a ladder interpolated along thresholds or deadlines (isotonic fit, never extrapolated), a bound, or "nothing fits" with the near-misses and why they were rejected | 6.3 |
| Odds | Mid of bid and ask, with an error from the spread and the volume; markets on both venues are cross-checked, and a disagreement beyond their errors makes the event tier B. The AI never overrides a market: when nothing fits, its estimate is held inside whatever range related markets imply, however wide | 4.5, 6.4 |
| Jump | Daily jump windows (odds read at the 4 p.m. New York close; factor moves regressed on all odds changes on days with a 5-point move); weekend windows on Hyperliquid's S&P 500, WTI and gold perps (Friday 5 p.m. to Sunday 6 p.m. New York time, while the reference markets are shut); past similar events (an event study around analog dates); and the AI prior. Until backtests measure them, the last two carry wide errors that grow with the move: half the move, in quadrature with a floor of 0.3 typical moves (past events) or 0.8 (the AI prior). Fused by precision, with a χ² check across market readings | 4.5, 4.6, 6.4 |
| Weekend windows | The same regression on weekends with a 5-point odds move. Prices come from hourly trade candles, not the oracle, and each boundary price is the median of the closes around it and the next open, so one bad print can't set it. A move that comes within a quarter step of trade[XYZ]'s discovery-bound cap is censored and left out. Error bars come from calm weekends. Monday's daily ETF window spans the weekend, so on these three factors it is left to the perps: the two readings rest on separate data and count as independent | 6.4 |
| Tiers | A: a well-matched liquid market and two precise market readings of the jump that agree (a reading is precise when its error bar on the jump's size is at most half the jump). B: readings disagree. C: priced, but fewer than two precise market readings or a weak match. D: no market fits | 4.6, 6.5 |
| Links | Proposed knock-on links are used only when the two events' odds histories agree in direction (t ≥ 2), with the measured slope | 4.4, 6.6 |
| Coherence | `engine/coherence.py`: each event's related prices are checked against the rules of probability. Ladders must rise or fall in order, markets that imply the event (or that it implies) bound its odds, and mutually exclusive outcomes add up to at most 100%, each within the quotes' half-spreads plus a point. A contradiction the event's odds rest on makes it tier B. A confirmed link's slope is capped where it would push either conditional probability below 0 or above 1 | 6.6 |
| Exposures | Weighted ridge regression of each holding's daily returns on the ten factors over two years (recent days count more), fit tested out of sample on the last six months; look-through via ETF Global holdings, to companies only (a bond, gold or commodity fund stays one line) | 6.1 |
| Impact | `Δ = b · δ`, knock-ons through the graph, priced-in moves `(1 − q)Δ` and `−qΔ`, swing `|Δ|√(q(1 − q))`, and a seeded 20,000-run simulation over the event graph | 4.1 to 4.4, 4.7 |
| Historical replay | `redkeep replay`: one episode, evening by evening, on archived prices. An event counts only once a market priced it; jumps come from jump windows on the 180 days before each close and the episode's analogs; links need the data's agreement on those days. Calls are `(1 − q)·δ_total` from the evening before; each day's explained move is `Σ δ·Δq`. Left out: the AI prior (it knows how April 2025 ended), Hyperliquid (its perps didn't trade yet) and options (M6). Metrics: the call's sign on every factor with a reading, stocks' move as a multiple of the call, the share of daily factor moves explained (`1 − Σ(real − explained)² / Σ real²`) with the correlation and the big days' signs, and the first evening's ranking by swing; with Massive, each sample portfolio as it stood the evening before: the events' and the factors' shares of its daily moves, and the calls against its moves | 10, E8 |
| Funnel | `redkeep funnel`: cheap filters (volume under $5,000, quotes wider than 20 points, odds within a point of 0 or 1), Claude's labels of an even sample under a fixed rubric, and a words model taught by them (TF-IDF over words and word pairs; logistic regression for "material", ridge for each factor's rough move) that screens every market. The screen drops a market below the line that kept 97% of the material ones in cross-validation, keeps it above the line where 90% were material, and sends the band between to Claude. E0 scores it on markets it never saw | 6.2, E0 |
| Jump accuracy | `redkeep jumps`: resolved markets of both venues, one per event, each scored on its resolution day (the odds' final move to the outcome, from at least 15 points away). The prediction is the jump windows' reading from the 180 days up to the close before, in typical moves fixed before the study's first day. E3: the share of the day's factor moves explained, `1 − Σ(z − Δq·δ̂)² / Σ z²`, signs on clear moves and the rank correlation, with ranges from resampling whole days. E4: 80% interval coverage, widened by the day's own noise, and the scale on the error bars that gives 80% on the earlier 70% of days. E5: precise readings against vague ones. E6 runs in `redkeep baseline` (E9), on markets that resolved after the model's training data | 10, E3 to E6 |
| Joint error bars | `redkeep jumps`: on the same markets, one forecast per resolution day, `Σ Δq δ̂` with covariance `Σ Δq² Σ_δ` plus the day's noise with its correlations. The paired Dawid–Sebastiani score of joint against independent error bars, with Student's t and sign flips, the design's power from the forecasts alone, a bias-statistic guard along the hedge the errors imply, and coverage along the sample portfolios and 500 random ones | 4.9, 10, E11 |
| AI baseline | `redkeep baseline`: the engine's AI prior alone (no tools, no data) against the market's own reading and the engine's fusion of both, on resolved markets the model can't know the outcome of. Jumps by E3's metrics, odds 30 days out by E2's, with 95% ranges from resampling whole days or events, and a check on older markets that the model does remember | 10, E9 |
| Lead and lag | `redkeep granger`: VARs of the factors' daily moves with Granger tests (White's robust covariance, Benjamini–Hochberg at 5%) and out-of-sample scores refitted monthly on two years, with block-bootstrap ranges; late shares against 4 p.m. funds; sign tests on the material markets' next-day and day-before moves; knock-on slopes over two to five days and on later data. The dollar index, fixed at noon, is carried to the 4 p.m. close by `level × (UUP close / UUP noon)^β`, with `β` from noon-to-noon changes (`factordata.carry_to_close`) | 4.8, 10, E10 |
| Calibration | `redkeep calibrate`: resolved yes/no markets since January 2025 from both venues (the busiest closed Polymarket events each month under five tags; settled Kalshi markets of series in the matching categories; no sports, crypto, daily series or thin markets), sampled evenly by a hash of their ids and priced 1, 7, 30, 90 and 180 days before their deadlines while still trading. One slope in log-odds per horizon, fitted on the earliest 70% by close date and kept only if it lowers the log loss on the rest across the whole 95% range of the gain; calibrated odds `p̂` sit beside market odds in every snapshot | 6.4, E2 |

## Not yet, and why it matters

- **A second market reading on three factors only.** Hyperliquid's weekend windows give a second reading on stocks, oil and gold, so an event can reach tier A once its odds have moved sharply on at least two weekends in the last 180 days and the two readings agree. Options (event-day volatility and the two-world fit, milestone M6) add a reading on every factor. The NVIDIA perp (chips), the dollar and rates perps, and holidays are left out for now.
- **Hourly candles go back about 208 days.** Hyperliquid keeps only the latest 5,000 candles of each interval. `redkeep record` keeps them for good, but only from the day it is first scheduled.
- **Daily windows, not 30-minute ones.** A day holds other news, so the error bars are wider than the design's intraday windows would give. They carry through fusion honestly. E10 (`data/evals/granger-2026-11-13.json`) found the one clear lag in the daily data to be a clock, prices read at different times of day, which intraday windows remove.
- **Only the dollar is carried to the close.** The 10-year yield, breakevens, the high-yield spread and oil are FRED series with closes of their own. E10 found their late shares against 4 p.m. funds small (0% to 11%) and confounded by what the funds hold, so only the dollar, 23% late at its noon fixing, is carried. Before UUP's hourly prices begin (2007) the dollar keeps its noon fixing.
- **One replayed episode, and credit only from 2023.** With Massive's paid key the replay has all ten factors back to the 2018 and 2019 analogs except credit: FRED's copy of the ICE high-yield spread starts in November 2023, so the analogs carry no credit reading and the exposures as of March 2025 are fitted on 16 months rather than two years. Without a Massive key the replay leaves out China, chips and gold, and the sample portfolios, and says so. Polymarket's daily prices are stamped at midnight UTC, after the close, so an event with daily prices only reads its odds most of a day late; the replay warns where that happens and prefers hourly prices.
- **Jump accuracy from one source, mostly.** On resolved markets only the market's own jump windows can be scored: no analogs are picked for them, and Hyperliquid's weekend windows reach back about 200 days. The AI prior knows how most of them ended, except those that resolved after its training data, which is where E9 scores it beside the market's reading and their fusion. So the tiers proper wait, and E5 compares precise readings with vague ones instead.
- **E9 has few jumps to score.** E9 can only use markets that resolved after the model's training data. The first run (November 8, 2026, `data/evals/baseline-2026-11-08.json`) found market odds far better than Claude's (Brier 0.110 against 0.191 on 250 markets), but only 41 markets with a jump reading, 7 of them material, where Claude and the engine both came out near no move and the market's reading alone did far worse. Months of newly resolved markets will give it more. Its portfolio metric (E8's, which the replay now runs on April 2025) isn't run on these markets yet.
- **Single-market jumps don't predict yet.** The first study (November 8, 2026, 54 resolved markets, `data/evals/jumps-2026-11-08.json`) and its rerun on all ten factors (November 10, 55 markets, `data/evals/jumps-2026-11-10.json`) found the readings honest about their error (83% and 84% inside the 80% intervals) but too vague to predict a resolution day's moves: as point estimates they did worse than predicting no move. Most of those markets were events no factor notices. Scored apart, the ones Claude's funnel label calls material lose almost nothing (−1%, then −0.3%) when only the factors a reading resolves are acted on, against −26% and −42% for the rest, so acting only on resolved jumps looked like the engine change it points to. E9's markets say otherwise: there, the readings that resolved a factor still did far worse than no move (−173%), and in both studies about as many readings resolved a factor as chance alone would give. So a jump should count only once two readings agree, the quorum, which options (M6) make possible on every factor.
- **The jumps' errors are counted one factor at a time.** Every reading carries its covariance across factors, and fusion, the portfolio range and the map's error bubbles can use it, but E11's first run found the measured correlations too strong: the day's move landed inside the joint 80% zone on 64% of 47 days, against 77% one by one, and the gain in score wasn't significant. By the rule fixed before the run, `JOINT_WEIGHT` is 0 until a larger sample backs them; halfway did best after the fact, and is the next run's question.
- **Ten hand-picked events, and a words model for a screen.** The funnel finds the material markets, but turning its groups into library events (step 4's cross-venue grouping, and an event's terms for the judge) comes next. Laya, the screen the design names, needs its weights from Hugging Face, which the build environment can't reach yet, and Jev is a paid early-access API. So a words model taught by the same labels stands in. On the first E0 run (November 8, 2026, `data/funnel/e0.json`) it found 96% of the material test markets while sending 38% of them to Claude; the four it dropped were one step on one factor. Laya and Jev have to beat that on the same split. The matching set for E1 isn't built yet.
- **No reliable long-shot bias yet.** The first study (November 8, 2026, on 2,907 resolved markets) fitted slopes from 1.01 to 1.33, but none beat raw prices reliably on later markets, so `data/calibration.json` keeps every horizon at market odds. A second run on 2,929 markets that closed from June 2025 (`data/evals/calibration-2026-11-08.json`) found the same. Rerunning `redkeep calibrate --write` as markets resolve will pick up a bias once the data supports it. The study scores each venue and category separately as a check, but one slope per horizon serves them all for now.
- **Fund-level exposures.** A fund's arrow comes from its own returns; per-holding regressions come next and give the same arrow for an unchanged fund.
- **The store starts on its first run.** Quotes and catalogue snapshots exist only from then on. Earlier days rest on the venues' own odds histories and FRED's current data, recorded as seen later, which the strict view (`known=True`) leaves out.
- **Coherence within each event's markets.** The checks compare the markets matched to one event. Checking across events, such as a market on two events happening together against the bounds its parts allow, needs joint markets in the catalogue.

## Layout

```
backend/
├── data/              events.toml (the event library), portfolios.toml (sample portfolios), episodes/ and replays/ (E8), funnel/ (Claude's labels, E0, the first screen), evals/
├── redkeep/
│   ├── data/          polymarket.py, kalshi.py, fred.py, massive.py, hyperliquid.py
│   ├── matching/      retrieve.py, judge.py, ladders.py, decide.py
│   ├── evidence/      odds.py, windows.py, weekend.py, analogs.py, prior.py, calibration.py and resolved.py (E2)
│   ├── engine/        fusion.py, covariance.py (joint error bars), impact.py, simulate.py, coherence.py
│   ├── store.py       the point-in-time store; record.py keeps it current
│   ├── factors.py, factordata.py, exposure.py
│   ├── pipeline.py    refresh and analyze
│   ├── funnel/        labels.py, screen.py, e0.py, run.py: the funnel (M4) and its eval (E0)
│   ├── baseline.py    Claude with no tools against the engine (E9)
│   ├── granger.py     lead and lag: VARs, Granger tests, clocks (E10); leadlag.py runs the study
│   ├── replay.py      the historical replay (E8)
│   ├── jumps.py       jump accuracy on resolved markets (E3 to E5); joint.py scores their joint error bars (E11)
│   ├── appdata.py     the export for the prototype
│   └── api.py, cli.py
└── tests/             fixtures copied from live responses, synthetic prices, and the offline end-to-end run
```
