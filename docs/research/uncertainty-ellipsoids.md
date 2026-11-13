# Uncertainty ellipsoids and The Red Keep

*Research note for eval E11, November 13, 2026. The shared pieces are in [`backend/redkeep/engine/covariance.py`](../../backend/redkeep/engine/covariance.py), fusion in [`engine/fusion.py`](../../backend/redkeep/engine/fusion.py), each reading's covariance in [`evidence/windows.py`](../../backend/redkeep/evidence/windows.py), `weekend.py`, `analogs.py` and `prior.py`, the eval in [`backend/redkeep/joint.py`](../../backend/redkeep/joint.py), and the saved run in [`backend/data/evals/jumps-2026-11-13.json`](../../backend/data/evals/jumps-2026-11-13.json). `redkeep jumps` reruns it.*

## In short

- **Does an ellipsoid make sense in place of ten separate error bars? In principle, yes.** A portfolio feels every factor's error at once: its impact `b·δ` is off by `b·(δ̂ − δ)`, whose spread is `√(b'Σ_δ b)`. Ten separate error bars give only the diagonal of `Σ_δ`, which treats a hedge's errors and a concentrated bet's alike. The covariance is the uncertainty ellipsoid, and it says which way the errors lean.
- **The engine can compute it exactly for its main reading.** Each factor's jumps are a regression on the same days' odds changes, a system of seemingly unrelated regressions with the same regressors in every equation. Fitting each factor alone loses nothing there, and the jumps' errors are correlated exactly as the residuals are (Zellner 1962). The residuals' correlation over a few dozen days is noisy, so it is shrunk toward calm-day correlations.
- **Fusing with covariances is a small change of form.** The precision-weighted average becomes its matrix version, the information filter. A reading of one factor can then move another through the correlations, the consistency check becomes a Mahalanobis distance, and the quorum's test projects each reading along the fused jump with its whole precision matrix.
- **Testing needs care, and may not be decisive yet.** The mean of the usual χ² check can't see a missing correlation, and coverage along one portfolio has little power at 55 markets. The research protocol therefore makes the resolution day the unit, scores mean and covariance together with the Dawid–Sebastiani score, computes the design's power from the forecasts before looking at outcomes, and guards against an ellipsoid that is too thin along the hedges it implies.
- **Drawing it.** A 3D ellipsoid seen through a camera is an ellipse on screen (EWA splatting), so each event can carry a faint 80% bubble on the map. People misread error bars and bounded zones, so it is worded in frequencies ("8 times in 10"), one probability level is used everywhere, size is said to mean uncertainty rather than impact, and the portfolio's own range is computed, not read off the picture.
- **What E11 found.** On 55 resolved markets over 47 days, the joint error bars scored better on 34 days and made what happened 1.5 times as likely on a typical day, but not significantly (t = 1.53 against 1.68), and they were too sure of themselves: the day's move landed inside their 80% zone on 64% of the days, against 77% with the factors one by one. The test had the power to see the gain the joint error bars promise, so by the rule fixed in advance the engine counts each factor's error on its own for now. After the fact, the correlations at half strength did best (79% inside, and a clear win over independent, t = 2.77): the next run's question.
- **What changed.** Every reading carries its covariance; fusion, the quorum and the portfolio range can use it at the weight E11 sets (zero today); the map draws each event's 80% bubble and each event card the full move's 80% range, marked as counting each factor's error on its own. E11 reruns with every jump study.

## 1. Joint error bars on one page

An event's jump `δ` has ten numbers, one per factor, and each reading of it comes with an error bar per factor, `σ_f`. Those say how far each number could be off. What they leave out is how the misses go together. If a jump window overstates the fall in stocks on a day of other bad news, it most likely overstates the rise in the VIX and in credit spreads too, because the same news moved all three. The errors are correlated.

That matters as soon as factors are added up, which is what a portfolio does. For an exposure arrow `b`, the impact error is `b·(δ̂ − δ)`, and

    Var(b·δ̂) = Σ_f b_f² σ_f² + 2 Σ_{f<g} b_f b_g ρ_fg σ_f σ_g.

The first sum is all that ten separate error bars give. The second has the sign of `b_f b_g ρ_fg` for each pair. For a book long stocks and long the VIX (a hedge), the stock and VIX errors are negatively correlated in the way that cancels, and the true range is narrower than the separate bars say. For a book long stocks, high yield and China (one bet three ways), the errors pile up, and the true range is wider. On an illustrative correlation matrix shaped like the factors' daily moves, the separate bars run from 0.65 of the true width, for the most concentrated portfolio, to 2.3 times it, for the most hedged, so a nominal 80% interval really covers anything from about 60% to over 99%. The famous allocations sit near 1 (0.86 to 1.09), because their stock leg dominates. A stock book hedged with the VIX (1.7, so about 97%) and one bet made three ways (about 0.85, so about 72%) are where it matters.

The covariance `Σ_δ` is the jump's uncertainty ellipsoid: the region `(δ − δ̂)'Σ_δ⁻¹(δ − δ̂) ≤ χ²₁₀(0.8)` holds the true jump 8 times in 10. Projected onto any direction `b`, it gives that portfolio's interval, and projected onto the map's three axes, it gives the bubble the app draws.

## 2. The literature, briefly

### 2.1 Regressions with the same regressors

The jump windows regress each factor's daily move on every event's odds change that day: ten regressions with the same right-hand side. Zellner (1962) named this a system of seemingly unrelated regressions (SUR) and showed when estimating it jointly gains nothing over equation by equation: when the errors are uncorrelated across equations, or when every equation has the same regressors. The second is the engine's case, so the coefficients don't change; what the joint view adds is their covariance, `Cov(δ̂_kf, δ̂_kg) = σ_fg [(A'A)⁻¹]_kk`, with `σ_fg` the residuals' covariance and `A` the odds changes. When a factor's series starts late, its windows are a subset of the others', and the cross term runs over the shared windows; the result is still a valid covariance.

### 2.2 Shrinking a few days' correlations

The residual covariance comes from a few dozen windows, and a 10 × 10 correlation matrix from that many days is noisy. Linear shrinkage pulls it toward a structured target, with the intensity chosen from the data. Ledoit and Wolf (2004) did this for covariances; Schäfer and Strimmer (2005) gave the closed form for correlations while keeping each variance, which is what the engine needs, since each factor's error bar is already set (and floored at a calm day's noise). The target here is the factors' correlation on calm days, and the intensity counts the residuals' own degrees of freedom, the windows less the fitted jumps. A better target, suggested by the research but not yet built, would be the residual correlations of past jump days, weighted toward recent ones.

### 2.3 Priors and views

The AI prior and the past events are not regressions, so their covariances are modelling choices. Black–Litterman models face the same question for an investor's views and usually answer it by making a view's uncertainty proportional to the market's own covariance (He and Litterman 1999): a view about stocks and the VIX is uncertain in the way stocks and the VIX move together. The engine does the same for the prior's base error, and splits the size error (a big estimate is uncertain in size as a whole) into a part shared along the jump and a part factor by factor. For the past events, the noise of each analog's window moved the factors as they moved around the analog's date: stocks and bonds have moved together in some decades and apart in others.

### 2.4 Fusing correlated readings

With covariances, the precision-weighted average becomes the information form of the Kalman filter: add the readings' precision matrices `P_s`, and `x̂ = (Σ P_s)⁻¹ Σ P_s x_s`. Two things follow that the per-factor version never shows. A reading that only sees stocks moves the fused VIX, through another reading's stock–VIX correlation ("borrowing strength"); and the fused value on a factor can fall outside the readings' range there. The consistency check becomes a Mahalanobis distance with degrees of freedom from the matrices' ranks. The research flagged one trap: two readings that share information (the AI prior has read about the same analogs) are not independent, and fusing them as if they were overstates the precision. Covariance intersection (Julier and Uhlmann 1997) fuses two readings without knowing their correlation, at the cost of some precision; it is listed as an open question.

### 2.5 Testing a covariance forecast

The proper scores for a forecast's mean and covariance are the log score of a normal forecast and its close relative, the Dawid–Sebastiani score `log det S + e'S⁻¹e` (Dawid and Sebastiani 1999; Gneiting and Raftery 2007). For two normal forecasts their paired difference is twice the log likelihood ratio, the statistic of the most powerful test between the two (Neyman–Pearson). Two cheaper checks are weaker than they look. With the error bars taken one by one, the mean of the squared Mahalanobis distance equals the number of factors whenever each factor's own variance is right, whatever the true correlations, so it can't see a missing one. And coverage along one fixed portfolio, with 55 markets, catches a 10-point miss only 38% to 52% of the time. Risk-model vendors test covariance forecasts with bias statistics, the spread of standardized returns against the band `1 ± √(2/K)`, and stress them along the hedged directions where sample covariances understate risk most (Menchero, Wang and Orr 2012). Day-level score differences are compared with Diebold and Mariano's (1995) test, using Student's t with `K − 1` degrees of freedom for a small sample (Harvey, Leybourne and Newbold 1997).

Two facts shaped E11's design. Markets that resolve on the same day share its moves, so the day, not the market, is the independent unit, and the forecast of a day is the sum of its markets' jumps times their odds changes. And the realized jump is measured with the day's own noise, which is correlated across factors too: a fair comparison gives both candidates the same full noise covariance and changes only the jumps' part.

### 2.6 Drawing uncertainty

A 3D Gaussian seen through a perspective camera has an elliptical footprint on screen, found by pushing its covariance through the projection's local Jacobian, `J S J'` (EWA splatting, Zwicker et al. 2002, the same step 3D Gaussian splatting uses). Readers, including researchers, misjudge error bars and the overlap of intervals (Belia et al. 2005). They read values inside a bar as likelier than values outside it (Correll and Gleicher 2014), and a hard-edged uncertainty shape as a container, with its size taken for the size of the thing itself (Padilla, Ruginski and Creem-Regehr 2017). In studies of hurricane cones, only numbers in the wording made people less likely to read the cone as holding every possible track. The research's guidance for the bubble: a faint fill with a crisp edge, frequency wording, one probability level throughout, no "confidence" or "σ" in the interface, size described as uncertainty rather than impact, and the portfolio's own range computed rather than read off the shadow of an ellipsoid on the arrow.

## 3. How each idea maps onto the engine

| Idea | In the engine | Where |
|---|---|---|
| SUR covariance with the same regressors | Each window reading's covariance across factors, over shared windows when the factors' windows differ | `windows.covariances` |
| Shrinkage of correlations, variances kept | Residual correlations shrunk toward calm-day ones, with the residuals' degrees of freedom | `covariance.shrink_corr` |
| Weekend windows | The same, for the perps' factors | `weekend.py` |
| Past events | Noise correlated as around the analogs, floor as now, size error half along the jump | `analogs.py` |
| View uncertainty from the market's covariance | The AI prior's base error correlated as today's daily moves | `prior.delta_reading` |
| Information filter | Fusion, the χ² check, the quorum's projection, `borrowed` | `fusion.fuse_delta` |
| A weight on the correlations | `w·Σ + (1 − w)·diag Σ`, set by E11 | `covariance.JOINT_WEIGHT` |
| Portfolio range | `b·δ ± 1.28 √(b'Σ_δ b)`, knock-ons included | `model.js`, the event card |
| EWA splatting | The 80% bubble on the map, `k = √χ²₃(0.8)` on `J G Σ_δ G' J'` | `ui-analysis.js` |
| Proper scores, day units, power, guards | E11 | `joint.py` |

## 4. What E11 found

The method and the decision were fixed in `joint.py` and pushed before the first run (commit 18dee70). On November 13, 2026, on the 55 markets of E3 over 47 resolution days (7 with several markets), ten factors a day, the readings' errors made 56% of each day's forecast variance, and their errors' correlations averaged 0.29 in size.

| | Joint | Halfway | Independent | E4's way |
|---|---|---|---|---|
| Mean Dawid–Sebastiani score (lower is better) | 5.10 | 5.26 | 5.93 | 7.37 |
| Days inside the 80% zone | 64% | 79% | 77% | 77% |
| Mean squared distance per factor (1 if calibrated) | 1.21 | 0.99 | 0.95 | 0.97 |

*E4's way takes the day's noise factor by factor as well.*

- **The primary test.** The joint error bars beat the independent ones on 34 of 47 days, by 0.83 a day on average (90% range −0.06 to +1.77), a likelihood ratio of 1.5 a day. With t = 1.53 against the 1.68 needed, and sign flips at p = 0.067, the test favours neither: a few days where the joint forecast was confidently wrong (the worst cost 9.8) offset many small gains.
- **The design's power was 1.00.** Had the joint error bars been right, the test would have found them better in essentially every simulated run. That it didn't is itself evidence that they are not right as measured.
- **The guards.** Along the hedge the joint error bars believe in most, their spread was 0.96, inside the band up to 1.21, while the independent error bars were far too wide there (0.56). But the joint forecasts' distances were significantly larger than the independent ones' (t = 4.9): too sure of themselves on whole days.
- **The decision, by the rule.** Inconclusive, with power of 0.6 or more: the engine keeps each factor's error on its own (`JOINT_WEIGHT = 0`), its ranges say so, and E11 reruns as the sample grows.
- **After the fact, not deciding.** Halfway beat the independent error bars on 35 of 47 days (gain 0.67 a day, t = 2.77) and was the best calibrated. The rule let halfway in only if the full joint error bars won the primary test, so it waits for the next run, which should test it first, on resolution days after November 7, 2026. The sensitivities lean toward the correlations: without the days of small odds moves (35 days) the joint error bars win (t = 2.28), as they do with the noise 1.5 times as large (2.84) and with a Student-t score (1.90); not with both forecasts rescaled together (1.25). Leaving out any single day moves t between 1.21 and 2.12, so the verdict rests on a few days.
- **The noise's own correlations matter most.** Taking the day's noise factor by factor, as E4 does when it checks one factor at a time, scores worse by 1.45 a day (t = 3.57). Portfolio-level checks need the full noise covariance whatever the jumps carry.
- **Portfolios.** Along 500 random directions the 80% intervals held 82% of the days (joint) and 83% (independent). Along the six sample portfolios they were close, with the joint error bars nearer 80% for QQQ (79% against 72%) and the tech-heavy portfolio (72% against 70%).

A reading that fits these numbers: the measured correlations point the right way (the joint error bars won most days and were right about where the independent ones are too wide), but they are too strong. That is what the research's simulation found when the true correlations were half the measured ones: overstating a correlation costs more than ignoring it.

## 5. What changed, and what didn't

- Every jump reading carries its covariance across factors: jump windows and weekend windows from the regressions, past events and the AI prior from the models above. The snapshot keeps them, and the fused jump's.
- Fusion, the χ² check and the quorum work in information form at the weight E11 sets; at today's weight of zero they give exactly the factor-by-factor results of before.
- The map draws an 80% bubble for the hovered or selected event (a setting shows all or none), and each event card shows the full move's 80% range for the portfolio, with a 50% band and a mark at no move, and a line saying the factors' errors are counted on their own until tested.
- E11 runs with every `redkeep jumps`. E9 keeps its factor-by-factor fusion, as fixed before its first run.
- What didn't change: each factor's own error bar, the point estimates, the tiers, and the April 2025 replay, whose saved evenings predate the covariances.

## 6. Open questions

- **Half strength.** The next E11 run should test halved correlations against none, on resolution days after November 7, 2026, with that rule fixed before it runs.
- **A better target.** Shrinking toward past jump days' residual correlations, rather than calm days', may fix the overconfidence at its source. Jump days carry other news along a few directions (risk on and off, rates and inflation, oil), so a calm-day target makes the ellipsoid thinnest exactly where portfolios feel the most.
- **The inverse's bias.** A covariance estimated with ν residual degrees of freedom has an inverse that is too large on average, by ν/(ν − N − 1) for N factors: 2.2 times at ν = 20 and 1.2 at ν = 60 for all ten, against about 1.1 for one factor at a time. Fusion weighs each reading by that inverse, so inflating a regression's covariance by the factor (less after shrinkage) before inverting it is a cheap fix aimed at the overconfidence E11 saw.
- **Few windows, wider regions.** With few windows the 80% region should come from Hotelling's F cutoff rather than χ²: on the map's three axes, a radius of about 2.4 rather than 2.15 at 20 degrees of freedom.
- **Shared information.** The AI prior and the past events may share information; covariance intersection would fuse them without assuming independence.
- **The quorum's direction.** A reading is tested along the fused jump it helped set; the fused jump without it would be a cleaner direction.
- **Across events.** Knock-on totals add a child's uncertainty as independent of its parent's, though the windows fit both together; those cross-event blocks could be kept too.
- **Tails.** Normal errors understate joint downside; a Student-t score is already one of E11's sensitivities.

## References

- Belia, S., Fidler, F., Williams, J., and Cumming, G. (2005). Researchers Misunderstand Confidence Intervals and Standard Error Bars. *Psychological Methods*, 10(4), 389–396. [doi:10.1037/1082-989X.10.4.389](https://doi.org/10.1037/1082-989X.10.4.389)
- Correll, M., and Gleicher, M. (2014). Error Bars Considered Harmful: Exploring Alternate Encodings for Mean and Error. *IEEE Transactions on Visualization and Computer Graphics*, 20(12), 2142–2151.
- Dawid, A. P., and Sebastiani, P. (1999). Coherent Dispersion Criteria for Optimal Experimental Design. *Annals of Statistics*, 27(1), 65–81. [doi:10.1214/aos/1018031101](https://doi.org/10.1214/aos/1018031101)
- Diebold, F. X., and Mariano, R. S. (1995). Comparing Predictive Accuracy. *Journal of Business and Economic Statistics*, 13(3), 253–263.
- Gneiting, T., and Raftery, A. E. (2007). Strictly Proper Scoring Rules, Prediction, and Estimation. *Journal of the American Statistical Association*, 102(477), 359–378. [doi:10.1198/016214506000001437](https://doi.org/10.1198/016214506000001437)
- Harvey, D., Leybourne, S., and Newbold, P. (1997). Testing the Equality of Prediction Mean Squared Errors. *International Journal of Forecasting*, 13(2), 281–291.
- He, G., and Litterman, R. (1999). The Intuition Behind Black-Litterman Model Portfolios. Goldman Sachs Investment Management Research.
- Julier, S. J., and Uhlmann, J. K. (1997). A Non-divergent Estimation Algorithm in the Presence of Unknown Correlations. *Proceedings of the 1997 American Control Conference*, 4, 2369–2373. [doi:10.1109/ACC.1997.609105](https://doi.org/10.1109/ACC.1997.609105)
- Ledoit, O., and Wolf, M. (2004). Honey, I Shrunk the Sample Covariance Matrix. *Journal of Portfolio Management*, 30(4), 110–119.
- Menchero, J., Wang, J., and Orr, D. J. (2012). Improving Risk Forecasts for Optimized Portfolios. *Financial Analysts Journal*, 68(3), 40–50.
- Padilla, L. M., Ruginski, I. T., and Creem-Regehr, S. H. (2017). Effects of Ensemble and Summary Displays on Interpretations of Geospatial Uncertainty Data. *Cognitive Research: Principles and Implications*, 2, 40. [doi:10.1186/s41235-017-0076-1](https://doi.org/10.1186/s41235-017-0076-1)
- Schäfer, J., and Strimmer, K. (2005). A Shrinkage Approach to Large-Scale Covariance Matrix Estimation and Implications for Functional Genomics. *Statistical Applications in Genetics and Molecular Biology*, 4(1), Article 32.
- Zellner, A. (1962). An Efficient Method of Estimating Seemingly Unrelated Regressions and Tests for Aggregation Bias. *Journal of the American Statistical Association*, 57(298), 348–368. [doi:10.2307/2281644](https://doi.org/10.2307/2281644)
- Zwicker, M., Pfister, H., van Baar, J., and Gross, M. (2002). EWA Splatting. *IEEE Transactions on Visualization and Computer Graphics*, 8(3), 223–238.
