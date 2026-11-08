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

---

## The idea in one example

You hold $1M of QQQ. Prediction markets give "US tariffs on China of 25% or more" a 40% chance.

- If tariffs happen, QQQ's total jump is about **−8.5%**: −6% directly, plus the hit from recession odds rising 25 points.
- 40% of that jump is already in today's price.
- So the news itself would cost you **−5.1% (−$51K)** if tariffs happen, and you'd gain **+3.4% (+$34K)** if they don't.

Events are ranked by that two-way **swing**, not by how scary the headline sounds. All numbers here are illustrative.

## How it works

1. **See what you really own.** Look through ETFs to the stocks inside, then measure how the portfolio responds to ten macro factors (stocks, rates, inflation, the dollar, oil, credit, fear, China, chips, gold).
2. **Find the events that matter.** Screen every live Polymarket and Kalshi market with Laya, a small open-source model taught by Claude, and keep the ~100 that could swing this portfolio the most.
3. **Match events to markets.** Check each market's wording, threshold and deadline against the event, and say so when nothing fits.
4. **Let markets check each other.** Odds and impacts come from prediction markets, options, ETF prices in market hours and Hyperliquid's perps on weekends and holidays. A number is "confirmed" only when at least two independent markets agree, and disagreement is shown, not averaged away.
5. **Show it and prove it.** A portfolio builder, a 3D risk map, live analysis and a historical replay, backed by evals against a "just ask an AI" baseline.

![Architecture](docs/diagrams/architecture.svg)

## Try the prototype

![The prototype's live analysis for the tech-heavy sample portfolio](docs/assets/prototype.png)

A playable prototype on illustrative data. Under the hero image, a short story about the engine lights up word by word over a dragon drawn in crimson numbers, and its fire grows as you scroll. Start from a famous portfolio (Ray Dalio's All Weather, Warren Buffett's 90/10, Harry Browne's Permanent Portfolio, a Yale-style mix) or build your own, see the events that could move it, open any event to see its evidence and a hedge size, and replay the April 2025 tariff shock one day at a time.

- **Run it.** Clone the repo and open [`prototype/index.html`](prototype/index.html) in a browser. It loads React and fonts from public CDNs, so there is nothing to install.
- **Change it.** Edit the sources in [`prototype/src/`](prototype/src/), then rebuild with `python3 prototype/build.py`.
- **Check it.** `node prototype/check.js` confirms the prototype reproduces the worked example above and the identities in the design doc.

The prototype's own numbers are illustrative. A build that includes a backend snapshot shows the engine's numbers instead, with a switch back, and the banner always says which you're looking at.

## Run the engine

[`backend/`](backend/README.md) is a working vertical slice of the design: ten events matched to Polymarket and Kalshi markets by Claude (or marked "nothing fits", with the near-misses and why they were rejected), odds with cross-checks, jumps and tiers, exposures with ETF look-through, and the impacts and simulation.

```bash
cd backend
uv sync && uv run pytest                  # offline tests: no keys, no network
uv run redkeep refresh                    # builds the event library from live markets (needs ANTHROPIC_API_KEY)
uv run redkeep analyze --sample allweather  # needs MASSIVE_API_KEY for prices and holdings
```

`redkeep app` turns a snapshot and analyses into data for the prototype, and `node prototype/check.js` then confirms the prototype's model gives the engine's numbers.

## Docs

- [System design](docs/DESIGN.md): the math, every stage with its rejected alternatives, evals, edge cases, decisions and checked references.
- [Backend](backend/README.md): setup, keys, commands, and what the slice does and doesn't do yet.
- [Research: Hyperliquid's non-crypto perps](docs/research/hyperliquid-noncrypto-perps.md): which markets are usable for weekend jump windows, and how to measure them.
- [Diagrams](docs/diagrams/): Mermaid sources and rendered figures.

## Status

Design doc v0.3, the playable prototype, and the engine's vertical slice (M3 in the [build plan](docs/DESIGN.md#15-build-plan)), tested offline. Next: its first run on live data, then options data (M6) for a second market reading of each jump.
