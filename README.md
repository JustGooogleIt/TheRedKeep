<!--
  Co-branding: add Castle's logo at docs/assets/castle-logo.svg, then uncomment the line below.
  <p><img src="docs/assets/castle-logo.svg" alt="Built for Castle" height="28"></p>
-->

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
2. **Find the events that matter.** Scan every live Polymarket and Kalshi market and keep the ~100 that could swing this portfolio the most.
3. **Match events to markets.** Check each market's wording, threshold and deadline against the event, and say so when nothing fits.
4. **Let markets check each other.** Odds and impacts come from prediction markets, options and 24/7 traded prices. A number is "confirmed" only when at least two independent markets agree, and disagreement is shown, not averaged away.
5. **Show it and prove it.** A portfolio builder, a 3D risk map, live analysis and a historical replay, backed by evals against a "just ask an AI" baseline.

![Architecture](docs/diagrams/architecture.svg)

## Docs

- [System design](docs/DESIGN.md): the math, every stage with its rejected alternatives, hedging with event contracts, evals, edge cases and checked references.
- [Diagrams](docs/diagrams/): Mermaid sources and rendered figures.

## Status

Design phase. See the [build plan](docs/DESIGN.md#15-build-plan).
