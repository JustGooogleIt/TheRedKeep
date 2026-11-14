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
| Exposures (M2) | Built and live: a factor regression on Massive's prices, looked through to ETF Global's holdings since the paid key (November 10) | `redkeep analyze` |
| Vertical slice (M3) | Runs on live data: about 71,000 markets searched, 6 of the 9 open events priced from markets | `redkeep refresh`, merged |
| Funnel (M4) | Built and run live: Claude labeled 2,000 markets, and the screen they taught finds 96% of the material ones while Claude reads 38% (E0) | `redkeep funnel`, PR #13 |
| Calibration and jumps (M5) | Both have run live (E2, E3 to E5) | `redkeep calibrate`, `redkeep jumps`, PR #12 |
| Options (M6) | Not built yet. The paid Massive key includes the option chains, with implied volatility and open interest | |
| Event graph and coherence (M7) | Built and live: knock-on links checked against odds histories, related prices checked against the rules of probability | merged |
| App (M8) | A playable prototype with the live event library and an April 2025 replay screen | [`prototype/`](../prototype/) |
| Evals (M9) | E8 (replay, now also in portfolio terms), E9 ("just ask an AI"), E10 (lead and lag, Granger causality) and E11 (joint error bars) have run live | `redkeep replay`, `redkeep baseline`, `redkeep granger`, `redkeep jumps`, PRs #11 and #14 |

225 offline tests cover the backend with recorded response shapes, synthetic prices and stand-ins for Claude. None of them needs a key or the network.

## What the evals found

Each finding below is from live runs between November 8 and 13, 2026. Where a result is weak, it says so.

**The live library.** The refresh of November 13 searched about 71,000 Polymarket and Kalshi markets and priced 6 of the 9 open events from them (the recession only as a bound). The tenth, a hot October CPI print, was decided on November 10, so the refresh leaves it out.

- Hormuz sits on the quorum's line. Its ETF jump windows and weekend perps are both precise. On November 10 they agreed (a χ² of 7.5 against the 7.8 line), which made it tier A; three days of data later they disagree just past the line (7.9), so it is contested (tier B).
- The December Fed hike is tier B, contested twice over. On fear, its jump windows say the VIX falls 4.4 typical moves while the AI prior says it rises 1.2, further apart than their error bars allow, so on fear and stocks the jump rests on the prior. And Kalshi's December decision ladder sums to the edge of what its quotes allow, which the coherence check flags. On November 8 the same check made the Bank of Japan hike tier B.
- Israel–Iran and a Ukraine ceasefire stay at C: their market readings are too vague to confirm the jump.

**Calibration (E2): market odds are good enough as they are.** On 2,907 resolved markets, long shots did look overpriced (fitted slopes from 1.01 at one day out to 1.33 at 180 days). But no correction beat raw prices reliably on later markets: every gain's 95% range includes zero. So the engine keeps market odds, and reruns will pick up a bias if one firms up. A second run, on 2,929 markets, found the same.

**Jump accuracy (E3 to E5): honest error bars, vague point estimates.** On 55 resolved markets, each scored on the day it resolved, on all ten factors (the November 10 rerun; the first run, on FRED's seven, found the same):

- The error bars are honest: 84% of realized jumps fall inside the 80% intervals (83% on the November 13 run, with the dollar carried to the 4 p.m. close).
- As point estimates, single-market readings fail: they explain −303% of the resolution-day moves, worse than predicting no move, and their signs are right 25 times in 48. One vague reading, on October's ISM survey, accounts for much of that; without it the share is −249%.
- The sample explains much of it: most were events no factor notices (cabinet confirmations, a rocket launch, what a CEO would say on a call). Claude's funnel label calls 11 of the 55 material. On those, acting only on the factors a reading resolves does about as well as predicting no move (−0.3%), where the rest lose 42%.
- The lesson is the quorum rule the design already had: act on a jump only where the readings resolve it. E9, below, shows that one reading isn't enough.

**The April 2025 replay (E8): the events explain about a quarter of the moves, and the exposures held.** Rerunning the engine evening by evening on archived prices:

- Three of six events matched a market. On the latest run (November 10, on Massive's paid key, so all ten factors reach back to the 2018 analogs), the matched events times their odds changes explain 26% of the daily factor moves (37% for stocks), with the right sign on 25 of 37 big moves. The first run, on FRED's seven factors, explained 28% of fewer day-factors.
- The one call, the tariff pause, had the right sign on 8 of the 9 factors with a reading (gold was the miss), but was far too small: +1.3% for stocks against SPY's 10.5%. Graded on the dollar's 4 p.m. close, which E10 (below) added, the dollar was a miss too: 7 of 9.
- **In portfolio terms.** Each sample portfolio, rebuilt as it stood on March 28, 2025 (holdings valued at that close, exposures fitted only on the two years before), was scored on the ten days. The factors explain 94% to over 99% of every portfolio's daily moves, so the exposures held through the crash and the rebound. The events explain from 26% (the Permanent Portfolio) to 40% (Yale-style). The pause call had the right sign on every portfolio but was 6 to 50 times too small: for $1M of QQQ, +1.8% (+$17,600) expected against +12.0% (+$120,000) that day.
- One idea was tested on that run and dropped: a prior centered on no move, standing in for the AI prior the replay leaves out. It tamed a vague early reading (a recession lifting the Tech-heavy sample 5%, ± 5%), but it also flattened the later, well-measured ones and cut the explained share to 5%, because April 2025 moved further than the engine's jumps. The replay screen shows each jump's error bar instead.
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

**Lead and lag (E10): an event's jump is its first day's move, and the dollar was on the wrong clock.** Granger causality asks whether one series' past helps forecast another's next move. It can't set an event's direction, because a jump is a same-day relation and timing can't orient those (the [research note](research/granger-causality.md) reviews why, and the literature). What it can test is whether the same-day reading is the whole story:

- **Jumps don't keep travelling.** Since 2008, a vector autoregression of the ten factors' daily moves forecasts the next day worse than "no move" out of sample (pooled score −0.082, 95% range −0.148 to −0.023), on every stretch. So a jump stays its first day's move, and carrying the April 2025 jumps forward explains no more of what followed.
- **The dollar was read four hours early.** The Fed's dollar index is fixed at noon; everything else at the 4 p.m. close. 23% of each day's dollar move showed up a day late, which made five factors look as if they led the dollar and understated international funds' dollar exposures by a fifth to a quarter. The engine now carries the index to the close with the dollar fund UUP's move since noon: the late share falls to 6%, and 19 of 25 funds' exposures fit held-out days better (the euro fund's from 0.40 to 0.59), none worse by more than 0.003. On the replay it exposed a call the noon clock had been crediting: the pause's dollar move.
- **No factor trails the odds.** On 133 of the funnel's material markets, no factor kept moving the day after an odds change, so the jump windows stay one day long. Odds on the S&P 500's and the Nasdaq's own levels do trail stocks by a day, most likely a stale price at 4 p.m., but pooled across the stock-linked markets that isn't significant.
- **Knock-on slopes last; links found by the data alone don't.** Same-day slopes between markets keep three quarters of their size over a week, but slopes picked by the data alone predict later data worse than no link, the fragility other work found on Kalshi. That backs the engine's rule that a link needs a mechanism and the data.

**Joint error bars (E11): measured, drawn, and not yet trusted.** A portfolio feels every factor's error at once, so how the errors move together sets how wide its range is: a hedge's errors can cancel, a concentrated bet's pile up. The engine now measures that covariance for every reading (the [research note](research/uncertainty-ellipsoids.md) reviews the methods), can fuse readings with it, and the map draws each event's 80% zone as a bubble. E11, fixed before it ran, asked whether those covariances describe what happened on E3's 55 resolved markets better than the same error bars taken one by one:

- **Better on most days, but not significantly.** Scored day by day, the joint error bars did better on 34 of 47 days, by a likelihood ratio of 1.5 a day on average. A few days where they were confidently wrong kept the gain short of significance (t = 1.53 against the 1.68 needed).
- **Too sure of themselves.** The day's whole move landed inside the joint 80% zone on 30 of the 47 days, 64%, against 77% with the factors one by one. The test had the power to see the gain the joint error bars promise, so the rule fixed in advance keeps each factor's error on its own for now, and the app says so beside each range.
- **Half strength was the best calibrated, after the fact.** With the correlations halved, 79% of the days fell inside the zone, and the score beat the independent one clearly (t = 2.77), though the full correlations scored best on average. So the measured correlations are too strong, but the data can't yet say by how much. Halfway against none becomes the next run's question, on markets that resolve after this one.
- **The noise's own correlations matter most.** Scoring a day with its ordinary noise taken factor by factor, as E4 does for single factors, was far worse (t = 3.57), so any portfolio-level check needs the noise's correlations, whatever the jumps carry.

## What the findings change

In order of what they would improve most:

1. **A second market reading on every factor.** A single market's reading did worse than predicting no move in E3 (−303%) and E9 (−341%), even where it looked resolved, and fusing it with the prior is what kept it harmless (−3%). The quorum already asks two readings to agree before a jump reaches tier A. Options (M6) give a second reading on every factor, so more events can reach it.
2. **Give the prior today's context.** Live, the prior put a December hike at 3% where the markets said 77%, because it reasons from its training data. E9 measured the cost: 0.081 worse than the markets in Brier score. The prior never sets odds where a market exists, but it does for tier D events, which no market fits, and its jump estimate carried 61% of the weight in E9's fusion. Giving it today's news and the related markets' prices is the change to test, against E9's saved run.
3. **E9 in portfolio terms.** Massive's paid key turned on look-through and E8's portfolio metric. Scoring E9's markets the same way, on their resolution days, is what's left.
4. **The matching set (E1)** and Laya, fine-tuned on Claude's labels, once its weights can be fetched. E0 sets the bar it has to clear.
5. **Record on a schedule.** The store holds only what it has seen, so every day it doesn't run is history that can't be recovered.
6. **Intraday jump windows.** E10's one clear lag was a clock: prices read at different times of day. Thirty-minute windows, which the design calls for, remove the clock, and would settle whether bond and oil markets move a day before the related odds, as E10's exploratory index hints.
7. **Joint error bars at half strength.** E11's next run, on markets that resolve after November 7, 2026, should test the halved correlations against none, with that rule fixed before it runs. If they hold, hedged portfolios get narrower ranges and concentrated ones wider.

## A ten-minute demo

1. **The question (1 minute).** The QQQ example above: the jump, what's priced in, the swing.
2. **A portfolio (2 minutes).** Open [red-keep.vercel.app](https://red-keep.vercel.app), or [`prototype/index.html`](../prototype/index.html) from a clone. Scroll the dragon story, then pick All Weather. Events are ranked by swing. Open Hormuz: its two market readings, the ETF jump windows and the weekend perps, disagree just past their error bars, so it is contested (tier B); on November 10 they agreed, for tier A. Hover an event on the map for its error bubble, where its jump could really sit 8 times in 10; its card gives the full move's 80% range for the portfolio. Open the December Fed hike: its readings disagree, so it is contested (tier B). Open Tech-heavy's holdings: NVIDIA held directly and inside QQQ and SMH, looked through to ETF Global's files. Open an event that nothing fits, with the near-misses and why they were rejected.
3. **The replay (2 minutes).** Step through April 2025 one evening at a time: the pause call, what the events explain each day, and the two days no matched market priced, and why. Then pick QQQ and scroll to the portfolio as it stood on March 28, 2025: its exposures, fitted before the crash, explain 98% of its daily moves during it.
4. **The evals (3 minutes).** E0 (the screen keeps 96% of what matters), E2 (market odds stand), E3 (honest error bars, vague point estimates, and the material split), E8 (a quarter of the moves explained, and exposures that held through April 2025), E9 (the markets beat Claude on odds; on jumps the engine ties it, and a single reading alone does far worse), E10 (a jump is its first day's move, and the dollar's noon clock, found and fixed) and E11 (the error bubbles, and a test fixed in advance that said "not yet" to their correlations).
5. **What's next (2 minutes).** Options for the quorum's second reading (the paid Massive key already has the chains), and a prior that knows today's news.

From a terminal, `cd backend && uv run pytest` runs the offline tests in about 15 seconds, and `uv run redkeep baseline --from <run>` rescores a saved E9 run without asking Claude anything.

## Where to look

- [System design](DESIGN.md): the math (section 4), each stage (6), where the AI is used (8), the evals and what they found (10), and the build plan (15).
- [Backend](../backend/README.md): setup, keys, every command, and what doesn't work yet and why.
- [Granger causality and The Red Keep](research/granger-causality.md): the literature review behind E10, how each idea maps onto the engine, and what the run found.
- [Uncertainty ellipsoids and The Red Keep](research/uncertainty-ellipsoids.md): the review behind E11 and the error bubbles, how each idea maps onto the engine, and what the run found.
- Saved runs: `backend/data/funnel/` (Claude's labels and E0), `backend/data/calibration.json` (E2), `backend/data/evals/` (E3 to E5, E9, E10, E11 and the second E2 run), `backend/data/replays/` (E8).
