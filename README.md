<!--
  Co-branding: add Castle's logo at docs/assets/castle-logo.svg, then uncomment the line below.
  <p><img src="docs/assets/castle-logo.svg" alt="Built for Castle" height="28"></p>
-->

![A red dragon breathes fire onto a walled castle on a sea cliff](docs/assets/hero.jpg)

# The Red Keep

### Hedging against dragons.

**Scenario Analysis Engine. Ashwin's Take-home for Castle**

*What could move your portfolio, net of what's already priced in.*

> Independent take-home project built for Castle Technologies. Not affiliated with or endorsed by Castle.

The first Red Keep had thick walls and no plan for dragons. This one starts with the dragons.

**Start here**

- [Try it live](https://red-keep.vercel.app): pick a famous portfolio and see the events that could move it.
- [Read the write-up](docs/WRITEUP.md): what was built, what the evals found on live data, and what comes next.
- [Follow the ten-minute demo](docs/WRITEUP.md#a-ten-minute-demo).

---

## The idea in one example

You hold $1M of QQQ. Prediction markets give "US tariffs on China of 25% or more" a 40% chance.

- If tariffs happen, QQQ's total jump is about **−8.5%**: −6% directly, plus the hit from recession odds rising 25 points.
- 40% of that jump is already in today's price.
- So the news itself would cost you **−5.1% (−$51K)** if tariffs happen, and you'd gain **+3.4% (+$34K)** if they don't.

Events are ranked by that two-way **swing**, not by how scary the headline sounds. All numbers here are illustrative.

## How it works

1. **See what you really own.** Look through ETFs to the stocks inside, then measure how the portfolio responds to ten macro factors (stocks, rates, inflation, the dollar, oil, credit, fear, China, chips, gold).
2. **Find the events that matter.** Screen every live Polymarket and Kalshi market with a small model taught by Claude's labels (a words model today; Laya, an open-source decision model, is the planned upgrade), and keep the ~100 that could swing this portfolio the most. Today the screen finds the material markets, and the library is ten hand-picked events; turning the screen's finds into events comes next.
3. **Match events to markets.** Check each market's wording, threshold and deadline against the event, and say so when nothing fits.
4. **Let markets check each other.** Odds and impacts come from prediction markets, ETF prices in market hours and Hyperliquid's perps on weekends and holidays, with options next (M6). A number is "confirmed" only when at least two independent markets agree, and disagreement is shown, not averaged away.
5. **Show it and prove it.** A portfolio builder, a 3D risk map, live analysis and a historical replay, backed by evals against a "just ask an AI" baseline.

![Architecture](docs/diagrams/architecture.svg)

## Try the prototype

![The prototype's live analysis for the tech-heavy sample portfolio](docs/assets/prototype.png)

A playable prototype, built with the engine's latest live snapshot: real odds, measured exposures with ETF look-through, and the April 2025 replay as the engine ran it. Under the hero image, a short story about the engine lights up word by word over a dragon drawn in crimson numbers, and its fire grows as you scroll. Start from a famous portfolio (Ray Dalio's All Weather, Warren Buffett's 90/10, Harry Browne's Permanent Portfolio, a Yale-style mix) or build your own, see the events that could move it, open any event to see its evidence and a hedge size, and replay the April 2025 tariff shock one day at a time. The Methodology tab explains the engine in a few minutes of plain reading.

- **Open it.** It's live at [red-keep.vercel.app](https://red-keep.vercel.app), rebuilt from `main` on every merge ([`prototype/vercel.json`](prototype/vercel.json)). The page is static and holds no keys.
- **Run it.** Clone the repo and open [`prototype/index.html`](prototype/index.html) in a browser. It loads React and fonts from public CDNs, so there is nothing to install.
- **Change it.** Edit the sources in [`prototype/src/`](prototype/src/), then rebuild with `python3 prototype/build.py`.
- **Check it.** `node prototype/check.js` confirms the prototype reproduces the worked example above and the identities in the design doc.

The prototype's own numbers are illustrative. A build that includes a backend snapshot shows the engine's numbers instead, with a switch back, and the banner always says which you're looking at.

## Run the engine

[`backend/`](backend/README.md) is a working vertical slice of the design: ten events matched to Polymarket and Kalshi markets by Claude (or marked "nothing fits", with the near-misses and why they were rejected), odds with cross-checks, jumps and tiers, exposures with ETF look-through, and the impacts and simulation.

```bash
cd backend
uv sync && uv run pytest                  # offline tests: no keys, no network
uv run redkeep refresh                    # builds the event library from live markets (needs an Anthropic key)
uv run redkeep analyze --sample allweather  # needs MASSIVE_API_KEY for prices and holdings
uv run redkeep analyze --holdings "VTI:300000, TLT:400000, NVDA:120sh"   # any portfolio, in dollars or shares
```

`redkeep app` turns a snapshot, and any analyses, into data for the prototype, and `node prototype/check.js` then confirms the prototype's model gives the engine's numbers. Without analyses (so without the Massive key), the prototype shows the snapshot's events against its own illustrative portfolios, and says so.

## Docs

- [Write-up](docs/WRITEUP.md): what was built, what the evals found on live data, what that changes, and a ten-minute demo.
- [System design](docs/DESIGN.md): the math, every stage with its rejected alternatives, evals, edge cases, decisions and checked references.
- [Backend](backend/README.md): setup, keys, commands, and what the slice does and doesn't do yet.
- [Research: Hyperliquid's non-crypto perps](docs/research/hyperliquid-noncrypto-perps.md): which markets are usable for weekend jump windows, and how to measure them.
- [Research: Granger causality](docs/research/granger-causality.md): what lead and lag can and can't tell the engine, a literature review, and what eval E10 found on real data (an event's jump is its first day's move; the dollar was on a noon clock, now fixed).
- [Research: uncertainty ellipsoids](docs/research/uncertainty-ellipsoids.md): how a jump's errors move together across the ten factors, how to estimate, fuse, test and draw them, and what eval E11 found on resolved markets (the measured correlations were too strong for now, so each factor's error counts on its own).
- [Diagrams](docs/diagrams/): Mermaid sources and rendered figures.

## Status

Design doc v0.3, the playable prototype, and the engine, which runs on live data. Its evals run on live data too, and the [write-up](docs/WRITEUP.md) says what they found. Exposures run on Massive's paid key, with look-through to ETF Global's holdings, and the April 2025 replay scores each sample portfolio as it stood the evening before. Next: options (M6), whose chains the same key includes.
