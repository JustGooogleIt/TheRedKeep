# The Red Keep: what was built, and what it found

**Scenario Analysis Engine. Ashwin's Take-home for Castle**

*What could move your portfolio, net of what's already priced in.*

> Independent take-home project built for Castle Technologies. Not affiliated with or endorsed by Castle.

This is the short version: the question, the five choices behind the answer, what runs today, what the evals found when they ran on live data, what that changes, and a script for a ten-minute demo. The [system design](DESIGN.md) has the math, every alternative we rejected, and the references. The [backend README](../backend/README.md) has the commands.

---

## The question

You hold a portfolio. Thousands of prediction markets price what might happen next: tariffs, a Fed move, a war widening or ending. Which of those events could move *your* portfolio, by how much, and how much of that move is already in today's prices?

One example, with illustrative numbers. You hold $1M of QQQ, and the markets give "US tariffs on China of 25% or more" a 40% chance.

- If tariffs happen, QQQ would fall about 8.5% in total: 6% directly, plus the hit from recession odds rising.
- At 40%, the market has already priced 40% of that fall.
- So the news itself would cost you about 5.1% (−$51K) if tariffs happen, and you would gain about 3.4% (+$34K) if they don't.

The engine ranks events by that two-way swing, not by how frightening the headline is.

## Five choices, and why

1. **Markets set the numbers; the AI never does when a market exists.** Odds come from Polymarket and Kalshi. How far each factor jumps when an event happens comes from markets too: how ETFs, rates and oil moved on the days the event's odds moved sharply ("jump windows"), and on weekends through Hyperliquid's perps, when the stock market is shut and the odds still move. Claude reads market rules, judges matches and gives a starting estimate (the "prior"), but a market's evidence always wins.
2. **A quorum, not an average.** A jump counts as confirmed (tier A) only when two independent market readings are each precise enough to tell it from no jump, and they agree. When they disagree, the event is shown as contested (tier B) instead of averaged into a number nobody measured.
3. **Exact matching, and "nothing fits" when nothing does.** A market that asks a slightly different question (another threshold, another deadline, "announces" instead of "imposes") is not the event. The judge says how it relates (same question, implies, implied by, overlaps), and only the first three can price it. That costs coverage, and the April 2025 replay shows the cost; it buys numbers that mean what they say.
4. **Nothing sees the future.** Every price the engine reads is stored with when it was seen (the point-in-time store), and the replay of April 2025 matches markets only among those listed before each event happened.
5. **Metrics fixed before the runs, and a baseline that could win.** Each eval's metric is written down before its first run. One eval asks the question every AI product should face: is the system better than just asking Claude?

## What runs today

| Part | State | Where |
|---|---|---|
| Data checks (M0) | Built: reports each source, the perps Hyperliquid lists and what the Massive key includes | `redkeep check` |
| Point-in-time store (M1) | Built and run live by hand; three bugs its first live runs found are fixed. Running it on a schedule waits for your OK | `redkeep record`, PR #10 |
| Exposures (M2) | Built (factor regression with ETF look-through); waits for prices and holdings from Massive | `redkeep analyze` |
| Vertical slice (M3) | Runs on live data: about 66,000 markets searched, 7 of 10 events priced from markets | `redkeep refresh`, merged |
| Funnel (M4) | Built and run live: Claude labeled 2,000 markets, and the screen they taught finds 96% of the material ones while Claude reads 38% (E0) | `redkeep funnel`, PR #13 |
| Calibration and jumps (M5) | Both have run live (E2, E3 to E5) | `redkeep calibrate`, `redkeep jumps`, PR #12 |
| Options (M6) | Waits for the Massive key | |
| Event graph and coherence (M7) | Built and live: knock-on links checked against odds histories, related prices checked against the rules of probability | merged |
| App (M8) | A playable prototype with the live event library and an April 2025 replay screen | [`prototype/`](../prototype/) |
| Evals (M9) | E8 (replay) and E9 ("just ask an AI") have run live | `redkeep replay`, `redkeep baseline`, PRs #11 and #14 |

150 offline tests cover the backend with recorded response shapes, synthetic prices and stand-ins for Claude. None of them needs a key or the network.

## What the evals found

Each finding below is from a live run on November 8, 2026. Where a result is weak, it says so.

**The live library.** The refresh searched about 66,000 Polymarket and Kalshi markets and priced 7 of the 10 events from them (the recession only as a bound).

- Hormuz reached tier A: the ETF jump windows and the weekend perps were both precise, and they agreed.
- The Bank of Japan hike went to tier B, because Kalshi's "hold" market leaves less room for a hike than the fused odds give it. The coherence check caught that.
- Israel–Iran stayed at C: its weekend reading was too vague to confirm the jump.

**Calibration (E2): market odds are good enough as they are.** On 2,907 resolved markets, long shots did look overpriced (fitted slopes from 1.01 at one day out to 1.33 at 180 days). But no correction beat raw prices reliably on later markets: every gain's 95% range includes zero. So the engine keeps market odds, and reruns will pick up a bias if one firms up. A second run, on 2,929 markets, found the same.

**Jump accuracy (E3 to E5): honest error bars, vague point estimates.** On 54 resolved markets, each scored on the day it resolved:

- The error bars are honest: 83% of realized jumps fall inside the 80% intervals.
- As point estimates, single-market readings fail: they explain −198% of the resolution-day moves, worse than predicting no move, and their signs are right 17 times in 33.
- The sample explains much of it: most were events no factor notices (cabinet confirmations, a rocket launch, what a CEO would say on a call). Claude's funnel label calls 10 of the 54 material. On those, acting only on the factors a reading resolves does about as well as predicting no move (−1%), where the rest lose 26%.
- The lesson is the quorum rule the design already had: act on a jump only where the readings resolve it. E9, below, shows that one reading isn't enough.

**The April 2025 replay (E8): the events explain 28% of the factor moves.** Rerunning the engine evening by evening on archived prices:

- Three of six events matched a market. Over ten days, the matched events times their odds changes explain 28% of the daily factor moves (35% for stocks), with the right sign on 22 of 30 big moves.
- The one call, the tariff pause, had the right sign on all six factors with a reading, but was far too small: +1.2% for stocks against a 9.5% rise.
- The misses are April 3 and 4: the tariff announcement and China's answer. One market could have priced the announcement, Polymarket's general-tariff market at 50% the evening before. The judge called it an overlap, not a match, because it counted only an April 2 announcement and set no 10% minimum. That is the price of exact matching. An overlapping market could still inform an event as a flagged, lower-tier reading; the matching set (E1) is where to test whether that helps more than it misleads.

**The funnel (E0): a cheap screen keeps 96% of what matters.** About 4,000 of the 66,000 listed markets pass the cheap filters. Claude labeled an even sample of 2,000 of them, and a words model taught by those labels was tested on 589 markets it never saw:

- It found 99 of the 103 material markets while sending 38% of the markets to Claude, and 32 of the 33 it kept on its own were material.
- The four it dropped were the weakest material calls, each one step on one factor, such as NVIDIA staying the world's largest company (chips).
- So Claude's work per refresh falls by three fifths. Laya and Jev, the screens the design names, have to beat that.

**"Just ask an AI" (E9): the markets beat Claude on odds, and on jumps the engine ties it.** Claude with no tools was asked about markets that resolved from August 1, 2026, after its training data, so it couldn't know how they ended:

- **Odds.** On 250 markets, 30 days before each deadline, the market's Brier score was 0.110 and Claude's 0.191 (lower is better; the gap's 95% range is 0.052 to 0.111). When Claude said 60% or more, the event happened half the time (16 of 32); when the market did, 82% of the time (40 of 49). That is choice 1 in numbers.
- **The check.** On 100 markets that closed inside its training data, Claude still didn't beat the market (0.100 against 0.079), as it would if it remembered how they ended.
- **Jumps.** 41 markets had a jump reading the evening before they resolved. Claude mostly predicts no move, and the engine, which gives Claude's estimate 61% of the weight, ties it: they explain −2% and −3% of the resolution-day moves. The market's reading alone explains −341%, and −173% even where it resolves a factor. At two standard errors, chance alone would make about as many of the 287 readings look resolved as did (13 against 14). So a reading that looks sure isn't enough on its own; the quorum asks for a second, independent one that agrees, which chance rarely gives.
- **Too few to settle.** On the 7 material markets the engine explained +11% (95% range −45% to +38%). Claude's own error bars can't be sized on these markets, so they stay as wide as set.

## What the findings change

In order of what they would improve most:

1. **A second market reading on every factor.** A single market's reading did worse than predicting no move in E3 (−198%) and E9 (−341%), even where it looked resolved, and fusing it with the prior is what kept it harmless (−3%). The quorum already asks two readings to agree before a jump reaches tier A. Options (M6) give a second reading on every factor, so more events can reach it.
2. **Give the prior today's context.** Live, the prior put a December hike at 3% where the markets said 77%, because it reasons from its training data. E9 measured the cost: 0.081 worse than the markets in Brier score. The prior never sets odds where a market exists, but it does for tier D events, which no market fits, and its jump estimate carried 61% of the weight in E9's fusion. Giving it today's news and the related markets' prices is the change to test, against E9's saved run.
3. **Massive's prices and holdings.** They turn on exposures (M2), E7, and the portfolio metric of E8 and E9.
4. **The matching set (E1)** and Laya, fine-tuned on Claude's labels, once its weights can be fetched. E0 sets the bar it has to clear.
5. **Record on a schedule.** The store holds only what it has seen, so every day it doesn't run is history that can't be recovered.

## A ten-minute demo

1. **The question (1 minute).** The QQQ example above: the jump, what's priced in, the swing.
2. **A portfolio (2 minutes).** Open [red-keep.vercel.app](https://red-keep.vercel.app), or [`prototype/index.html`](../prototype/index.html) from a clone. Scroll the dragon story, then pick All Weather. Events are ranked by swing. Open Hormuz: two market readings that agree make it tier A. Open the Bank of Japan hike: the coherence check made it tier B. Open an event that nothing fits, with the near-misses and why they were rejected.
3. **The replay (2 minutes).** Step through April 2025 one evening at a time: the pause call, what the events explain each day, and the two days no matched market priced, and why.
4. **The evals (3 minutes).** E0 (the screen keeps 96% of what matters), E2 (market odds stand), E3 (honest error bars, vague point estimates, and the material split), E8 (28% explained) and E9 (the markets beat Claude on odds; on jumps the engine ties it, and a single reading alone does far worse).
5. **What's next (2 minutes).** Options for the quorum's second reading, Massive, and a prior that knows today's news.

From a terminal, `cd backend && uv run pytest` runs the offline tests in about 15 seconds, and `uv run redkeep baseline --from <run>` rescores a saved E9 run without asking Claude anything.

## Where to look

- [System design](DESIGN.md): the math (section 4), each stage (6), where the AI is used (8), the evals and what they found (10), and the build plan (15).
- [Backend](../backend/README.md): setup, keys, every command, and what doesn't work yet and why.
- Saved runs: `backend/data/funnel/` (Claude's labels and E0), `backend/data/calibration.json` (E2), `backend/data/evals/` (E3 to E5, E9 and the second E2 run), `backend/data/replays/` (E8).
