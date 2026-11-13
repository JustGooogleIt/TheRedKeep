# Granger causality and The Red Keep

*Research note for eval E10, November 13, 2026. The statistics are in [`backend/redkeep/granger.py`](../../backend/redkeep/granger.py), the data and the report in [`backend/redkeep/leadlag.py`](../../backend/redkeep/leadlag.py), the saved run in [`backend/data/evals/granger-2026-11-13.json`](../../backend/data/evals/granger-2026-11-13.json). `redkeep granger` reruns it.*

## In short

- **Can Granger causality give an event's direction across the ten factors? Not directly.** Granger causality is about timing: does one series' past help forecast another's next move? An event's jump vector is a same-day relation: the factors move on the day the event's odds move. Granger himself showed that timing can't say which way a same-day relation runs. The engine already gets the direction the way that literature recommends, from narrow windows around news.
- **Where it does apply: three timing questions** that decide whether the same-day reading is the whole story. Does a jump keep travelling across the factors after its first day? Are all ten factors measured on the same clock? Do markets, or prediction markets, react a day late? Eval E10 tests each on real data.
- **Jumps don't keep travelling.** On daily moves since 2008, yesterday's factor moves forecast today's worse than "no move" out of sample, on every stretch of the data. An event's direction is set on its first day, as the engine assumed.
- **One factor was on the wrong clock.** The Fed's dollar index is fixed at noon New York time; everything else is read at the 4 p.m. close. Nearly a quarter of each day's dollar move (23%) landed on the next day. That made five other factors look as if they led the dollar, it understated international funds' dollar exposures by a fifth to a quarter, and on the April 2025 replay it scored the tariff pause's dollar move from before the news came out.
- **Markets don't trail the odds; some odds trail the markets.** No factor passed the test fixed in advance for moving the day after an odds change, so the jump windows stay one day long. The other way round, in all ten markets whose odds clearly move with stocks, mostly bets on the S&P 500's or the Nasdaq's own level, stocks had already moved the same way the day before: most likely a stale-price clock on the odds side, and no longer significant when all the stock-linked markets are pooled. Knock-on slopes between markets hold over two to five days, but links found by the data alone fail on later data, which backs the engine's rule that a link needs a mechanism too.
- **What changed.** The engine now carries the dollar from its noon fixing to the 4 p.m. close with a dollar fund that trades then. The day-late share falls from 23% to 6%, four of the five false "leads" into the dollar go away, and 19 of 25 funds' exposures fit held-out days better, the currency and international funds most (the euro fund's from 0.40 to 0.59), with none worse by more than 0.003. E10 reruns as a command as the data grows. Nothing else changed: on the evidence, nothing else should.

## 1. Granger causality on one page

Clive Granger (1969) gave Norbert Wiener's idea a testable form: a series `x` causes `y` if `x`'s past improves the forecast of `y` beyond what `y`'s own past, and everything else known, already gives. In practice it is a regression. Fit `y_t` on its own last `p` values, then add `x`'s last `p` values, and test whether their coefficients are all zero. With many series at once, the regression is a vector autoregression (VAR), each series on every series' recent past, and `x` Granger-causes `y` when `x`'s coefficients in `y`'s equation are not all zero.

Three things it is not:

- **It is not cause.** It is predictability. A third series that moves both `x` and `y`, with `x` first, makes `x` look causal for `y`. Shojaie and Fox (2022), the review this study starts from, keep the name only because it has stuck.
- **It can't order the same day.** Granger split dependence into the lagged part, which a forecast can test, and "instantaneous causality": the correlation of the two series' surprises on the same day. Timing can't orient that. If stocks and the dollar fall together on the day of a tariff announcement, the data alone can't say which pushed which, or whether the announcement pushed both.
- **It depends on the clock.** Granger causality is defined at the frequency you sample. Measuring two series at different times of day, or adding days into weeks, creates and destroys apparent leads (section 2.5).

For The Red Keep the second point is the one that matters. An event's jump `δ`, its push on the ten factors, is a same-day relation by construction: the factors' moves on the days its odds move. So Granger causality can't supply `δ`. What it can do is check the timing assumptions around it, which is what E10 does.

## 2. The literature, briefly

### 2.1 From Granger to VARs and impulse responses

Sims (1980) made the VAR the workhorse of empirical macroeconomics: let every series depend on the recent past of every series, and study the system's response to a shock. That response, the impulse response, follows from the VAR's coefficients: in `z_t = A z_{t−1} + u_t` a shock `ε` today moves the series by `A^k ε` after `k` days, and with more lags by the recursion `Ψ_k = Σ_l A_l Ψ_{k−l}`. Geweke (1982) measured how strong a Granger link is, as the share of the target's remaining variance that the source's past removes; E10 reports that partial R² for every link. Jordà (2005) estimated impulse responses directly, by regressing the move `k` days ahead on today's shock ("local projections"), and Plagborg-Møller and Wolf (2021) showed the two estimate the same responses in population, so the choice is about bias and variance, not about what is estimated.

### 2.2 Ordering the same day: identification

A VAR's surprises `u_t` are correlated across series on the same day, so "a shock to stocks" isn't defined until the same-day relations are ordered. The classic fix, ordering the series and taking a Cholesky factor, is an assumption, not a finding. Better: find a shock measured outside the system. Stock and Watson (2018) set out the conditions for such an external instrument. Gürkaynak, Sack and Swanson (2005) and Nakamura and Steinsson (2018) measured monetary-policy shocks as market moves in narrow windows around Fed announcements, where little else happens. Snowberg, Wolfers and Zitzewitz (2007) did the same with prediction markets on election night: the odds move, the markets move with them, and the ratio is the effect of the election.

That is exactly the engine's jump windows (DESIGN.md 4.5): days where an event's odds moved sharply, with the factors' moves regressed on all the odds changes that day. The jump `δ` is the event's measured first-day effect, so a VAR would only ever be needed to carry it forward, never to order it.

### 2.3 Many series at once: network Granger causality

With two series at a time, a common driver fools the test. The review's Figure 1 is the standard example, and E10's tests include it: when `x` moves `y` after one day and `z` after two, a two-series test finds `y` "leading" `z`, and the link disappears once `x` is in the regression. So Granger tests should be conditional, with every candidate driver in the VAR ("network Granger causality"). With many series and few observations the VAR has too many coefficients, and sparse estimators (lasso VARs; Basu and Michailidis 2015) or factor structures take over. In finance, Billio, Getmansky, Lo and Pelizzon (2012) built Granger networks of banks, brokers, insurers and hedge funds and found them denser before crises; Diebold and Yilmaz (2014) measured connectedness by variance decompositions instead. The engine has ten factors and thousands of days, so a full VAR with every factor in it is feasible and E10 uses it, with the false-discovery control below.

### 2.4 Beyond linear

Transfer entropy (Schreiber 2000) measures how much `x`'s past reduces uncertainty about `y`'s next value, with no linear model. Barnett, Barrett and Seth (2009) proved it equals Granger causality (up to a factor of two) when the series are Gaussian, so it adds something only when the dependence is nonlinear. Neural Granger causality (Tank, Covert, Foti, Shojaie and Fox 2022) fits networks with sparsity penalties that pick out which series' pasts matter; PCMCI (Runge et al. 2019) prunes candidate links by conditional-independence tests before testing the rest. VAR-LiNGAM (Hyvärinen, Zhang, Shimizu and Hoyer 2010) goes after the same-day order itself, using non-Gaussian shocks, which daily returns have. None was needed here: the linear tests found almost nothing at the daily frequency (section 4.1), daily returns are close enough to linear in their lagged relations for the question asked, and the same-day order is already fixed by the jump windows. They are the tools to try at intraday frequency (section 6).

### 2.5 The pitfalls, and which bite here

- **Common drivers.** Handled by conditional tests (2.3), and in the engine by tying jumps to named events rather than to one factor.
- **The clock.** Two series sampled at different times of day show leads that are only the gap between their clocks. Scholes and Williams (1977) and Dimson (1979) found that stocks that trade late or rarely have betas biased toward zero, and fixed it by adding the market's next-day or last-day move; Lo and MacKinlay (1990) showed nonsynchronous prices create spurious cross-autocorrelation; Epps (1979) found that correlations measured over short intervals shrink for the same reason. **This one bit:** the dollar (section 4.2).
- **Adding up time.** Summing days into weeks, or sampling less often than the links work, turns lagged links into same-day ones and can reverse their apparent direction (Breitung and Swanson 2002; Tank, Fox and Shojaie 2019). It argues for intraday data when the question is "which moved first", and it is why a daily test that finds nothing doesn't rule out leads of minutes.
- **Calm and wild spells.** Daily moves come in volatility clusters, which make ordinary standard errors too small. E10 uses White's (1980) robust covariance for the VAR tests and Newey and West's (1987) where errors may also hang together over days.
- **Links that come and go.** A lead measured over 2008 to 2026 can be a crisis-year artifact. E10 tests three stretches separately and judges forecasts only out of sample.
- **Many tests.** Ten factors give 90 directed links; at 5% each, four or five would look significant by chance. E10 controls the false discovery rate (Benjamini and Hochberg 1995).
- **In-sample isn't out of sample.** Granger tests are in-sample. A link that can't forecast new data is no use to the engine, which is why the rule for using a link is an out-of-sample forecast, not a p-value.

### 2.6 Prediction markets

Ng and Peng (2025) studied the 2024 US election on Polymarket, Kalshi, PredictIt and Robinhood and found that Polymarket led Kalshi in price discovery, most of all when it was busier. Kim et al. (2026) looked for Granger leads among Kalshi's economics markets, used them to trade, and found that statistically significant links often fail out of sample. Having a language model re-rank the candidate pairs by whether a real-world mechanism connects them raised the win rate from 51.4% to 54.5% and halved the average losing trade, because it dropped links that were fragile. The engine already works this way: a knock-on link needs a mechanism a model proposes and the odds data's agreement (DESIGN.md 6.6), never the data alone. Section 4.5 finds the same fragility in the funnel's markets.

## 3. How each idea maps onto the engine

| Idea | Where it would sit | Verdict, with E10's evidence |
|---|---|---|
| Granger tests between factors | Carrying a jump past its first day | Not used: no factor forecasts another out of sample (4.1) |
| VAR impulse responses `Ψ_k δ` | The same | Not used: carrying the April 2025 jumps forward made them no better (4.3) |
| Granger tests to set a jump's direction | The jump vector | Not possible: same-day relations can't be oriented by timing (1) |
| Event windows as an outside instrument | The jump windows | Already the design (4.5 of DESIGN.md) |
| Nonsynchronous-trading corrections | Any factor not read at 4 p.m. | **Used for the dollar**, by carrying its noon fixing to the close (4.2) |
| Next-day windows (Dimson-style) | The jump windows | Not used: no factor passed the test fixed in advance (4.4) |
| Day-before windows for odds that trail | The jump windows | Not yet: only markets on an index's own level trail clearly (4.4) |
| Granger tests between markets | Knock-on links | Not used alone, as Kim et al. (2026) advise; same-day slopes hold up (4.5) |
| Transfer entropy, neural Granger, PCMCI, VAR-LiNGAM | Any of the above | Not needed at daily frequency; candidates for intraday windows (6) |

## 4. What E10 found

All on data through November 12, 2026, from the saved run [`granger-2026-11-13.json`](../../backend/data/evals/granger-2026-11-13.json). "Typical moves" are each factor's weekly standard deviation (DESIGN.md 4.1). Credit is left out of the engine's long samples: FRED serves the high-yield spread only from 2023.

### 4.1 Factors against factors

A VAR on the engine's nine factors with long histories, since 2008 and over three stretches, beside the same VAR on 4 p.m. fund closes for all ten (SPY, IEF, TIP against IEF, UUP, USO, HYG against IEF, the VIX, FXI, SMH against SPY, GLD):

| Sample | Measured as | Days | Lag order, BIC / AIC | Links that pass | Out-of-sample score (95% range) |
|---|---|---:|---|---|---|
| 2008 on | Engine, dollar carried to 4 p.m. | 4,430 | 1 / 4 | 3 of 72 | −0.082 (−0.148 to −0.023) |
| | Engine, dollar at noon (before) | 4,430 | 1 / 4 | 7 of 72 | −0.082 (−0.148 to −0.022) |
| | 4 p.m. fund closes | 4,745 | 0 / 4 | 2 of 90 | −0.032 (−0.044 to −0.021) |
| 2008 to 2012 | Engine, dollar carried | 1,135 | 0 / 3 | none | −0.006 (−0.039 to +0.019) |
| | Engine, dollar at noon | 1,135 | 1 / 3 | 2 of 72 | −0.001 (−0.032 to +0.023) |
| | 4 p.m. fund closes | 1,258 | 0 / 4 | none | −0.015 (−0.040 to +0.009) |
| 2013 to 2019 | Engine, dollar carried | 1,628 | 0 / 1 | 1 of 72 | −0.017 (−0.027 to −0.006) |
| | Engine, dollar at noon | 1,628 | 0 / 1 | 2 of 72 | −0.012 (−0.025 to +0.000) |
| | 4 p.m. fund closes | 1,761 | 0 / 1 | 2 of 90 | −0.018 (−0.028 to −0.007) |
| 2020 on | Engine, dollar carried | 1,665 | 0 / 4 | 2 of 72 | −0.041 (−0.067 to −0.021) |
| | Engine, dollar at noon | 1,665 | 0 / 4 | 1 of 72 | −0.038 (−0.064 to −0.019) |
| | 4 p.m. fund closes | 1,724 | 0 / 4 | 1 of 90 | −0.034 (−0.052 to −0.019) |

The score is `1 − Σ(y − ŷ)² / Σy²` over all factors and days: 0 is the forecast "no move", and below 0 is worse than it. A link passes when its Wald test survives the false-discovery control at 5%.

- **Out of sample, the VAR loses to "no move" everywhere.** Refitted monthly on the two years before each day, it forecasts the next day's factor moves worse than predicting none: the pooled score is below zero on every stretch and every clock, and its 95% range is never above zero. The decision rule fixed before the first run carries a jump past its first day only if the pooled score's whole 95% range is above zero, so an event's jump stays its first day's move.
- **In sample, the criteria disagree, which is itself the answer.** Over the whole span the Bayesian criterion picks one day of lags, but with the dollar carried it picks none within any stretch: the lagged effects are small enough that it takes eighteen years of data to see them at all. Three links pass the false-discovery control over the whole span, each removing between 0.2% and 1.2% of the target's remaining variance. Links that small, which come and go between stretches, can't forecast.
- **On 4 p.m. closes almost nothing is left.** Two links pass over the whole span, stocks into the dollar fund and into chips, each removing 0.5% of the target's variance, and none of the links in the three stretches shows up in a second one. On the engine's measures with the dollar at noon, five of the seven links over the whole span ran into the dollar (from stocks, yields, breakevens, China and gold), which pointed to the clock.

### 4.2 The dollar's clock

The Fed's broad dollar index is computed from noon buying rates in New York (the H.10 release). Its daily change runs from noon to noon, four hours behind the 4 p.m. close that stocks, the funds and the odds are read at. Against UUP, a dollar fund read at 4 p.m.:

| The dollar, measured | Same-day correlation with UUP | Next-day correlation | Late share |
|---|---:|---:|---:|
| Fed index at its noon fixing (before) | 0.66 | 0.18 | 23% |
| Carried to the 4 p.m. close (now) | 0.83 | 0.02 | 6% |

Over 4,765 days since 2007. The late share is `b₁ / (b₀ + b₁)` from `series_t = a + b₀·UUP_t + b₁·UUP_{t−1}`: the part of a 4 p.m. move the series shows a day late. A series on the same clock scores about 0; one fixed four hours before the close shows those four hours' news a day late.

Three consequences, all measured:

- **False leads.** Stocks, yields, breakevens, China and gold seemed to Granger-cause the dollar since 2008, because a move after noon shows in the index the next day. With the dollar carried to 4 p.m., four of those five links disappear, and the fifth, from stocks, shrinks by a third (from 1.8% of the dollar's variance to 1.2%).
- **Shrunken exposures.** The exposure engine regresses each fund's 4 p.m. returns on the factors' same-day moves, so a dollar measured four hours early explains less of them. Carried to the close, 19 of the 25 funds tested fit held-out days better (the exposure engine's own score, `exposure.fit_line`, averaged over 64 windows of two and a half years, the last half year held out, ending every quarter from 2010). The currency funds gain most: the euro fund's held-out R² goes from 0.40 to 0.59, sterling's from 0.29 to 0.40, the Canadian dollar's from 0.40 to 0.49. Developed-market and Mexican stock funds follow (EFA 0.75 to 0.77, VEA 0.78 to 0.80, EWW 0.50 to 0.52). Their dollar exposures had been a fifth to a quarter too small (EFA's 0.47 to 0.64, EWW's 0.80 to 0.98, the euro fund's 0.61 to 0.89). No fund fits worse by more than 0.003 (the gold miners, GDX); the other five that slip, by 0.001 or less, are bond, gold, real-estate and China funds.
- **A call scored on the wrong news.** On April 9, 2025 the US paused most of the new tariffs at 1:18 p.m. The noon fixing that day came before the news, so the replay was scoring the pause against a dollar move that had already happened (−0.28 typical moves, the same sign as the engine's call). At the close the dollar was up (+0.60): the call was wrong. On the replay as a whole, run twice in one process so both runs match the same markets, the dollar's explained share rises from −20% to −13%, the overall share barely moves (40.5% to 40.1%), and the factor signs called right fall from 8 of 9 to 7 of 9, because the noon clock had been crediting the engine with the pause's dollar call. (Both runs priced the May cut from a different Polymarket market than the saved replay does, "no change after the May meeting" rather than the sum of the two cut outcomes, which lifts their overall share from the saved run's 26% to about 40%. The judge's pick between such equivalent markets can change from run to run; what matters here is the difference between the two runs.) The dollar's share stays below zero for a reason the clock doesn't touch: in April 2025 the dollar fell as stocks fell, where in past sell-offs it has usually risen, and the events' jumps, measured on history, mostly called it the other way (the explained and actual dollar moves correlate at −0.43 on the right clock, −0.25 at noon). A replay that grades on the right clock shows that plainly instead of half-hiding it.

**The fix keeps the broad basket.** Replacing the index with UUP would fix the clock but change the basket: UUP tracks six currencies, mostly the euro, while the Fed's index has 26, including the yuan, the peso and the Canadian dollar. In the exploratory runs that helped euro, yen and sterling funds a lot and hurt Mexican, Brazilian, emerging-market and Canadian-dollar funds. So the engine keeps the index and carries it from noon to the close: `level × (UUP's close / UUP's noon price)^β`, with `β` the index's move per unit of UUP's, measured from one noon to the next so the two share a clock (`β` ≈ 0.57). Only the last four hours borrow UUP's move, and they are a small part of the day's.

The other series that aren't 4 p.m. closes show smaller gaps against their fund stand-ins (late shares: the 10-year yield 5% against IEF, breakevens 8% against TIP less IEF, the high-yield spread 11% against HYG less IEF since 2023, oil 0% against USO), and their stand-ins differ from them in more than timing (a bond fund holds a range of maturities; an oil fund holds futures, not spot; a spread of two funds is not an index spread), so they stay as they are. Section 6 keeps them on the list.

### 4.3 Carrying the April 2025 jumps forward

Take the saved replay's explained moves, the matched events' jumps times their odds changes, and add what a VAR fitted on the two years before the episode (March 30, 2023 to March 28, 2025) says those moves go on to cause. Scored as the replay scores itself, over the episode's 100 factor-days, the explained share is 26.0% from the first-day moves alone and 26.0% with the VAR's full coefficients added (correlation 0.545 and 0.549). None of the VAR's links passes the false-discovery control on those two years, so carrying only tested links changes nothing at all. Carrying jumps forward adds nothing the replay can see.

### 4.4 Odds against factors

The funnel's labels mark a market "material" when its event would move some factor by a full typical move (DESIGN.md 6.2). One per group of near-duplicates, 133 of them had hourly odds for at least 60 of the last 200 days (95 Kalshi, 38 Polymarket, the latter a hashed sample of 60, since Polymarket serves 14 days a call). For each market and factor, the factor's daily move is regressed on the market's odds change the day before, the same day and the day after. Where the same-day slope is clear (`|t| ≥ 2.5`), the sign test asks whether the other two point the same way in more than half the markets, with the false-discovery control across the twenty tests:

| Factor | Markets with a clear same-day link | Factor still moving the day after, same way | Factor already moving the day before, same way |
|---|---:|---:|---:|
| Stocks | 10 | 8 (p 0.11) | **10 (p 0.002)** |
| Rates | 9 | 6 (p 0.51) | 6 (p 0.51) |
| Breakevens | 6 | 5 (p 0.22) | 6 (p 0.03) |
| Dollar | 8 | 5 (p 0.73) | 5 (p 0.73) |
| Oil | 16 | 13 (p 0.02) | 13 (p 0.02) |
| Credit | 12 | 8 (p 0.39) | 10 (p 0.04) |
| VIX | 5 | 2 (p 1.00) | 2 (p 1.00) |
| China | 4 | 4 (p 0.13) | 4 (p 0.13) |
| Chips | 12 | 6 (p 1.00) | 11 (p 0.006) |
| Gold | 8 | 4 (p 1.00) | 3 (p 0.73) |

Only the bold result passes the false-discovery control.

- **No factor keeps moving the day after the odds.** The rule fixed in advance would stretch a factor's jump windows to the next day only if the after-day column passed; none does. The windows stay one day long.
- **The other way round, some odds trail the market.** In all ten markets with a clear same-day link to stocks, the stock market had already moved the same way the day before. Six of the ten are bets on the S&P 500's or the Nasdaq-100's own level, and two more on oil and on a recession: their odds follow the markets, and a thinly traded market's price at 4 p.m. can be hours old, so part of the afternoon's move would show up in the next day's odds. That is most likely the dollar's clock problem on the odds side (Lo and MacKinlay 1990). Chips (11 of 12) and credit (10 of 12) lean the same way through largely the same markets, but don't pass the control.
- **Pooled, the stock result belongs to those markets, not to events.** An index of all the odds changes labelled to move a factor, each signed by its label, has power the per-market tests lack. It was added after the first run, so it is exploratory. For stocks the day-before term is no longer significant there (a quarter of the same-day move, t 0.9, over 84 markets). Bonds (0.85 of the same-day move, t 3.2) and oil (0.45, t 2.6) do show one: these markets may move about a day before the related odds, which is a watch item for section 6. Stocks and the VIX give back part of the day's move the next day (0.40 and 0.77 of it, t −2.1 and −2.5), which, if it holds, means a one-day window overstates what lasts; the per-market test points the other way for stocks (8 of 10 still moving the same way, not significant), so it is a question for more data, not a change.

None of this changes the jump windows: the only clear lag is in markets that track a factor's own level, and they are bets on the factor, not events that move it. A window that also takes in the day before, for markets whose odds trail an index, is the change these results would justify if they hold as the history grows.

### 4.5 Odds against odds

A knock-on link (DESIGN.md 4.4) is the same-day slope of a child market's odds changes on its parent's. Across every directed pair of the 133 markets from different families (a Kalshi series' rungs, or a Polymarket event's outcomes, move together by construction), 17,258 pairs had enough shared days, and 350 have a clear same-day slope (`|t| ≥ 3`).

- **Children don't follow a day late.** The child's next-day change points the same way as the same-day slope in only 114 of the 350 pairs (p 6×10⁻¹¹ against half), with a median of −0.16 of the same-day slope: what moves the day after is a small reversal, not a late reaction. After the false-discovery control, no parent's past predicts its child in any of the 350.
- **Same-day slopes hold over a week.** Measured on two-, three- and five-day changes instead of daily ones, the median slope keeps 92%, 84% and 76% of its daily size. A knock-on mostly lasts; about a quarter of it fades within a week.
- **Links found by the data alone fail on later data.** For the 190 pairs clear on the first half of their days, predicting the child's daily changes on the second half from the parent's, with the first half's slope, gives 23% more squared error than predicting no change (15% more on two-day changes). Among 17,000 pairs, many clear slopes are luck. That is the fragility Kim et al. (2026) found in Kalshi's lead-lag pairs, and the reason the engine accepts a link only when a model proposes a mechanism for it and the odds data agree (DESIGN.md 6.6), never from the data alone.

## 5. What changed, and what didn't

- **Changed:** the dollar is carried from its noon fixing to the 4 p.m. close (`factordata.carry_to_close`, used by every command that loads factor data, so exposures, jump windows and the replay all see the dollar at the close). Its source line says so: "FRED DTWEXBGS, carried from its 12:00 fixing to the 4 p.m. close with Massive UUP (β 0.57)". A key without Massive's hourly prices keeps the noon fixing, as before. `redkeep record` stores the noon prices with the closes.
- **Added:** eval E10, `redkeep granger`, which reruns all of the above and saves the report.
- **Kept, now with evidence:** a jump is its first day's move; the jump windows stay one day long; knock-on links stay same-day slopes, used only with a mechanism proposed for them.
- **Saved runs:** the replay and eval files saved before this change used the noon fixing. Their source lines name the index without the carry, and they stay as they were run.

## 6. Open questions

- **Intraday.** The design calls for 30-minute jump windows. At that frequency the question "which moved first, the odds or the market?" has an answer in minutes, and adding time up into days (2.5) no longer hides it. The nonlinear and same-day-ordering tools of 2.4 belong there.
- **Odds that trail.** If bond and oil markets do move a day before the related odds, as the pooled index hints, and index-level markets' odds keep trailing stocks, the jump windows understate those events' jumps. Two hundred days in one oil shock can't settle it; E10's pooled index will, as the odds history grows. The fix it would justify is a window that takes in the day before for those markets, or, better, intraday windows.
- **The other clocks.** The 10-year yield, breakevens, the high-yield spread and oil are FRED series with closes of their own; their late shares against fund stand-ins (0% to 11%) are small and confounded by what the stand-ins hold. With intraday prices for the futures and cash markets behind them, the dollar's treatment would carry over.
- **Correlated errors in the quorum.** A reading's error bar treats the factors' errors as independent. The same-day correlation of the factors' surprises, the instantaneous part of the VAR, says they aren't, which overstates a reading's precision when its errors line up with the factors' usual co-movement. Using that covariance in the quorum's precision test is the next step it points to.

## References

Checked on November 13, 2026 against publisher records, journal indexes (RePEc, JMLR, PubMed Central) or the authors' pages.

1. Barnett, L., Barrett, A. B., and Seth, A. K. (2009). Granger Causality and Transfer Entropy Are Equivalent for Gaussian Variables. *Physical Review Letters*, 103(23), 238701. [doi:10.1103/PhysRevLett.103.238701](https://doi.org/10.1103/PhysRevLett.103.238701)
2. Basu, S., and Michailidis, G. (2015). Regularized Estimation in Sparse High-Dimensional Time Series Models. *Annals of Statistics*, 43(4), 1535–1567. [doi:10.1214/15-AOS1315](https://doi.org/10.1214/15-AOS1315)
3. Benjamini, Y., and Hochberg, Y. (1995). Controlling the False Discovery Rate: A Practical and Powerful Approach to Multiple Testing. *Journal of the Royal Statistical Society B*, 57(1), 289–300. [doi:10.1111/j.2517-6161.1995.tb02031.x](https://doi.org/10.1111/j.2517-6161.1995.tb02031.x)
4. Billio, M., Getmansky, M., Lo, A. W., and Pelizzon, L. (2012). Econometric Measures of Connectedness and Systemic Risk in the Finance and Insurance Sectors. *Journal of Financial Economics*, 104(3), 535–559. [doi:10.1016/j.jfineco.2011.12.010](https://doi.org/10.1016/j.jfineco.2011.12.010)
5. Breitung, J., and Swanson, N. R. (2002). Temporal Aggregation and Spurious Instantaneous Causality in Multiple Time Series Models. *Journal of Time Series Analysis*, 23(6), 651–665. [RePEc](https://ideas.repec.org/a/bla/jtsera/v23y2002i6p651-665.html)
6. Diebold, F. X., and Yilmaz, K. (2014). On the Network Topology of Variance Decompositions: Measuring the Connectedness of Financial Firms. *Journal of Econometrics*, 182(1), 119–134. [doi:10.1016/j.jeconom.2014.04.012](https://doi.org/10.1016/j.jeconom.2014.04.012)
7. Dimson, E. (1979). Risk Measurement When Shares Are Subject to Infrequent Trading. *Journal of Financial Economics*, 7(2), 197–226. [doi:10.1016/0304-405X(79)90013-8](https://doi.org/10.1016/0304-405X(79)90013-8)
8. Epps, T. W. (1979). Comovements in Stock Prices in the Very Short Run. *Journal of the American Statistical Association*, 74(366), 291–298. [doi:10.2307/2286325](https://doi.org/10.2307/2286325)
9. Federal Reserve Board. Foreign Exchange Rates (H.10), About the release: noon buying rates in New York. [federalreserve.gov](https://www.federalreserve.gov/releases/h10/about.htm)
10. Geweke, J. (1982). Measurement of Linear Dependence and Feedback Between Multiple Time Series. *Journal of the American Statistical Association*, 77(378), 304–313. [doi:10.2307/2287238](https://doi.org/10.2307/2287238)
11. Granger, C. W. J. (1969). Investigating Causal Relations by Econometric Models and Cross-spectral Methods. *Econometrica*, 37(3), 424–438. [doi:10.2307/1912791](https://doi.org/10.2307/1912791)
12. Gürkaynak, R. S., Sack, B., and Swanson, E. T. (2005). Do Actions Speak Louder Than Words? The Response of Asset Prices to Monetary Policy Actions and Statements. *International Journal of Central Banking*, 1(1).
13. Hyvärinen, A., Zhang, K., Shimizu, S., and Hoyer, P. O. (2010). Estimation of a Structural Vector Autoregression Model Using Non-Gaussianity. *Journal of Machine Learning Research*, 11, 1709–1731. [jmlr.org](https://www.jmlr.org/papers/v11/hyvarinen10a.html)
14. Jordà, Ò. (2005). Estimation and Inference of Impulse Responses by Local Projections. *American Economic Review*, 95(1), 161–182. [doi:10.1257/0002828053828518](https://doi.org/10.1257/0002828053828518)
15. Kim, S., Kim, M., Kwon, J., et al. (2026). LLM as a Risk Manager: LLM Semantic Filtering for Lead–Lag Trading in Prediction Markets. [arXiv:2602.07048](https://arxiv.org/abs/2602.07048)
16. Lo, A. W., and MacKinlay, A. C. (1990). An Econometric Analysis of Nonsynchronous Trading. *Journal of Econometrics*, 45(1–2), 181–211. [RePEc](https://econpapers.repec.org/RePEc:eee:econom:v:45:y:1990:i:1-2:p:181-211)
17. Nakamura, E., and Steinsson, J. (2018). High-Frequency Identification of Monetary Non-Neutrality: The Information Effect. *Quarterly Journal of Economics*, 133(3), 1283–1330.
18. Newey, W. K., and West, K. D. (1987). A Simple, Positive Semi-Definite, Heteroskedasticity and Autocorrelation Consistent Covariance Matrix. *Econometrica*, 55(3), 703–708. [doi:10.2307/1913610](https://doi.org/10.2307/1913610)
19. Ng, H., and Peng, L. (2025). Price Discovery and Trading in Modern Prediction Markets. [SSRN 5331995](https://papers.ssrn.com/abstract=5331995)
20. Plagborg-Møller, M., and Wolf, C. K. (2021). Local Projections and VARs Estimate the Same Impulse Responses. *Econometrica*, 89(2), 955–980. [doi:10.3982/ECTA17813](https://doi.org/10.3982/ECTA17813)
21. Runge, J., Nowack, P., Kretschmer, M., Flaxman, S., and Sejdinovic, D. (2019). Detecting and Quantifying Causal Associations in Large Nonlinear Time Series Datasets. *Science Advances*, 5(11), eaau4996. [doi:10.1126/sciadv.aau4996](https://doi.org/10.1126/sciadv.aau4996)
22. Scholes, M., and Williams, J. (1977). Estimating Betas from Nonsynchronous Data. *Journal of Financial Economics*, 5(3), 309–327. [doi:10.1016/0304-405X(77)90041-1](https://doi.org/10.1016/0304-405X(77)90041-1)
23. Schreiber, T. (2000). Measuring Information Transfer. *Physical Review Letters*, 85(2), 461–464. [doi:10.1103/PhysRevLett.85.461](https://doi.org/10.1103/PhysRevLett.85.461)
24. Shojaie, A., and Fox, E. B. (2022). Granger Causality: A Review and Recent Advances. *Annual Review of Statistics and Its Application*, 9, 289–319. [doi:10.1146/annurev-statistics-040120-010930](https://doi.org/10.1146/annurev-statistics-040120-010930)
25. Sims, C. A. (1980). Macroeconomics and Reality. *Econometrica*, 48(1), 1–48. [doi:10.2307/1912017](https://doi.org/10.2307/1912017)
26. Snowberg, E., Wolfers, J., and Zitzewitz, E. (2007). Partisan Impacts on the Economy: Evidence from Prediction Markets and Close Elections. *Quarterly Journal of Economics*, 122(2), 807–829.
27. Stock, J. H., and Watson, M. W. (2018). Identification and Estimation of Dynamic Causal Effects in Macroeconomics Using External Instruments. *Economic Journal*, 128(610), 917–948. [doi:10.1111/ecoj.12593](https://doi.org/10.1111/ecoj.12593)
28. Tank, A., Covert, I., Foti, N., Shojaie, A., and Fox, E. B. (2022). Neural Granger Causality. *IEEE Transactions on Pattern Analysis and Machine Intelligence*, 44(8), 4267–4279. [doi:10.1109/TPAMI.2021.3065601](https://doi.org/10.1109/TPAMI.2021.3065601)
29. Tank, A., Fox, E. B., and Shojaie, A. (2019). Identifiability and Estimation of Structural Vector Autoregressive Models for Subsampled and Mixed-Frequency Time Series. *Biometrika*, 106(2), 433–452. [RePEc](https://ideas.repec.org/a/oup/biomet/v106y2019i2p433-452..html)
30. White, H. (1980). A Heteroskedasticity-Consistent Covariance Matrix Estimator and a Direct Test for Heteroskedasticity. *Econometrica*, 48(4), 817–838. [doi:10.2307/1912934](https://doi.org/10.2307/1912934)
