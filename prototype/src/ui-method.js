/* ==========================================================================
   UI · Part 4: the methodology tab. A short tour in plain words: the
   priced-in idea, the seven steps on the analysis tab's strip, how the
   engine is tested and what it doesn't do yet. The math, the rejected
   alternatives and the references are in docs/DESIGN.md; the saved eval
   results quoted here are from docs/WRITEUP.md.
   ========================================================================== */

// The worked example in DESIGN.md section 3, with its illustrative numbers: $1M of QQQ, tariffs on China at 40%.
const M_EXAMPLE = { V: 1e6, direct: -6.0, knock: -2.5, q: 0.4 };
// Results of the saved eval runs (WRITEUP.md, "What the evals found"; DESIGN.md section 10), quoted by the tests
// below. E9's odds (backend/data/evals/baseline-2026-11-08.json) and E0's screen from Nov 8, 2026; E2's markets from
// the first calibration run, Nov 8; E3 and E4 from the jump study of Nov 13 (jumps-2026-11-13.json: 55 resolved
// markets, 550 readings, 18 of them two error bars from no move where chance alone gives about 25); E10's lead and lag
// from Nov 13 (granger-2026-11-13.json: the dollar's late share at noon and carried to 4 p.m., the VAR's pooled
// out-of-sample score, the funds whose exposures fit better, the material markets read for late moves, and the
// data-only knock-on slopes' extra error on later days); and E11's joint error bars from Nov 13 (the share of
// resolution days whose whole move landed inside each forecast's 80% zone). Update them when those evals rerun. The
// replay's numbers are not here: they come with the snapshot (REPLAY.metrics), except `replayRerun`, the share E10's
// paired reruns of April 2025 explained when the judge took the other, equivalent May-cut market.
const M_EVALS = { brierMarket: 0.110, brierAI: 0.191, brierGap: [0.052, 0.111], oddsMarkets: 250, aiHigh: [16, 32], marketHigh: [40, 49],
  calMarkets: 2907, screenFinds: 0.96, screenSends: 0.38,
  covered: 0.83, jumpMarkets: 55, readings: 550, sureReadings: 18, sureByChance: 25, signs: [25, 46],
  lateNoon: 0.23, lateCarried: 0.06, varScore: -0.082, fundsBetter: [19, 25], oddsTimingMarkets: 133, dataLinksWorse: 0.23,
  jointDays: 47, jointWins: 34, insideJoint: 0.64, insideApart: 0.77, insideHalf: 0.79, replayRerun: 0.40 };

function MethodExample({ P, top, onOpen }) {
  const { V, direct, knock, q } = M_EXAMPLE, dT = direct + knock;
  const ifYes = (1 - q) * dT, ifNo = -q * dT, sw = Math.abs(dT) * Math.sqrt(q * (1 - q));
  return html`<section className="card" aria-labelledby="m-ex-h">
    <div className="card-head"><div><span className="kicker">The core idea · an illustrative example</span><h3 className="h3" id="m-ex-h" style=${{ marginTop: 6 }}>Only the surprise counts</h3>
      <p className="sub">You hold $1M of QQQ, and prediction markets give US tariffs on China a 40% chance. If tariffs happen, QQQ falls about 8.5%: 6% directly, and 2.5% more because tariffs make a recession likelier. But at 40% odds, 40% of that fall is already in today’s price.</p></div></div>
    <${SplitBar} dT=${dT} q=${q} />
    <div className="three m-three">
      <div><span className="label">If tariffs happen</span><span className="v neg">${fmtUsd(ifYes * V / 100, true)}</span><span className="s">${fmtPct(ifYes)} · the surprise</span></div>
      <div><span className="label">If they don’t</span><span className="v pos">${fmtUsd(ifNo * V / 100, true)}</span><span className="s">${fmtPct(ifNo)} · the relief</span></div>
      <div><span className="label">Swing</span><span className="v">${swingUsd(sw * V / 100)}</span><span className="s">${swingPct(sw)} · either way</span></div>
    </div>
    <div className="formula">
      <div>full move ${fmtPct(dT)} = direct ${fmtPct(direct)} + knock-on through recession ${fmtPct(knock)}</div>
      <div>if it happens: (1 − ${q.toFixed(2)}) × ${fmtPct(dT)} = ${fmtPct(ifYes)}</div>
      <div>if it doesn’t: ${MINUS}${q.toFixed(2)} × ${fmtPct(dT)} = ${fmtPct(ifNo)}</div>
      <div>swing: ${Math.abs(dT).toFixed(1)}% × √(${q.toFixed(2)} × ${(1 - q).toFixed(2)}) = ${swingPct(sw)}</div>
    </div>
    <p className="m-p">Weighted by their odds, the two outcomes net to zero: that is what priced in means. So the engine ranks events by <b>swing</b>, how far each one could still move you either way. Swing is largest for coin flips with big moves, and small for events the market already treats as settled, however alarming the headline.</p>
    ${top && html`<p className="note">On your portfolio right now, the biggest swing is <button className="inl" onClick=${() => onOpen(top.id)}>${top.t}</button>: ${swingUsd(top.swingPct * P.V / 100)} at ${fmtOdds(top.qEff)} odds.</p>`}
  </section>`;
}

function MethodSteps({ tw }) {
  const listed = DATASET === 'live' && RK_LIVE.catalogue ? Object.values(RK_LIVE.catalogue).reduce((a, n) => a + (+n || 0), 0) : 0;
  const markets = listed >= 1000 ? 'Polymarket and Kalshi list about ' + (Math.round(listed / 1000) * 1000).toLocaleString('en-US') + ' markets.'
    : 'Polymarket and Kalshi list tens of thousands of markets.';
  const steps = [
    ['Look through', 'What you actually own', html`<p>Each fund is replaced by the companies it holds, and a company held several ways is added up, so a stock you own directly and through a fund shows as one line. A fund with no holdings data, or one that holds bonds, gold or futures rather than companies, stays whole and is measured from its own prices.</p>`],
    ['Exposure', 'Which way it leans', html`<p>The engine measures how each holding has moved with ten market factors, each tracked by a real price:</p>
      <div className="m-chips">${FACTORS.map(f => html`<span key=${f.k} className="m-chip">${f.name}<small>${f.proxy}</small></span>`)}</div>
      <p>Weighted by the holdings, that gives the portfolio’s arrow: how much it gains or loses when each factor makes a typical move, one standard deviation of its weekly change. Other factors join only when a portfolio needs them, and the fit is the share of its daily moves the factors explain, on prices the model didn’t see.</p>`],
    ['Discover', 'Events that could move you, and the event map', html`<p>${markets} Claude labels a sample of them, and a small model trained on those labels screens the rest: on markets it hadn’t seen, it kept ${pct0(M_EVALS.screenFinds)} of the ones that matter while sending Claude only ${pct0(M_EVALS.screenSends)}. The events form a shared library, and each portfolio sees the ones that resolve inside its horizon, ${tw.horizon} months here. Today the library’s events are picked by hand; turning the screen’s finds into events is the next step.</p>`],
    ['Match', 'Each event’s card, under Matched market', html`<p>An event takes its odds from a market only if the market asks the same question: the same threshold, deadline and source. Claude judges each candidate against a fixed rubric and quotes the rule it relied on. When nothing fits, the event is marked unpriced and the rejected near-misses are shown with the reason, instead of borrowing the nearest market’s odds.</p>`],
    ['Evidence', 'Each event’s card, under How big is the jump?', html`<p>How far each factor jumps if the event happens is measured, not guessed: from how prices moved on the days the event’s odds moved sharply, from Hyperliquid’s perps on weekends while stock markets are shut, from past similar events, and from an AI prior. Each reading carries an error bar, and they are averaged by their precision. Then each event gets a tier:</p>
      <ul className="m-tiers">${['A', 'B', 'C', 'D'].map(t => html`<li key=${t}><span className=${'tier show t' + t}><b>${t}</b><span>${TIERS[t].name}</span></span><span>${TIERS[t].rule}</span></li>`)}</ul>`],
    ['Graph', 'Each event’s card, under Knock-on effects', html`<p>Some events make others likelier: tariffs raise the odds of a recession. An event’s full move adds the moves it sets off along those links, so each blow is counted once. Links are checked against how the two events’ odds have moved together, and related prices against the rules of probability.</p>`],
    ['Simulate', 'Range of outcomes', html`<p>Thousands of futures are drawn together over the graph at market odds, so linked events tend to happen together. The average comes out near zero, because those odds are already in today’s prices. What is left is the spread: the worst 1 in 20, and what the worst futures have in common.</p>`],
  ];
  return html`<section className="card" aria-labelledby="m-steps-h">
    <div className="card-head"><div><span className="kicker">From portfolio to ranked events</span><h3 className="h3" id="m-steps-h" style=${{ marginTop: 6 }}>Seven steps</h3></div></div>
    <ol className="m-steps">${steps.map(([t, where, body], i) => html`<li key=${t} className="m-step">
      <div className="m-step-h"><span className="m-n">${String(i + 1).padStart(2, '0')}</span><h4 className="m-step-t">${t}</h4></div>
      <div className="m-step-b">${body}<p className="m-where"><span className="label">On the page</span><span>${where}</span></p></div>
    </li>`)}</ol>
  </section>`;
}

// Each test checks one claim the engine rests on, in the same four parts: what was done, what the number means, how
// it backs the claim, and where it falls short. The pill says how strongly the result backs the claim.
function MethodTests({ goTab }) {
  const E = M_EVALS, R = REPLAY, engine = REPLAY_SOURCE === 'engine' && R.metrics;
  const ex = engine ? R.metrics.explained : null;
  const live = ex && ex.explained_share != null;
  const eqShare = live && ex.by_factor && ex.by_factor.eq ? ex.by_factor.eq.explained_share : null;
  const fits = live ? Object.values(R.portfolios || {}).map(v => v.metrics && v.metrics.factors.explained_share).filter(v => v != null) : [];
  const fitPct = v => (v > 0.99 ? 'over 99%' : pct0(v));
  const matched = live ? (R.events || []).filter(e => e.listed).length : 0;
  const k = FACTORS.findIndex(f => f.k === 'eq'), typ = live && R.typical ? R.typical[k] : null;
  const call = live && typ ? ((R.metrics.calls || {}).stocks || []).find(x => x.pred != null && x.real != null) : null;
  const sgn = v => (v < 0 ? MINUS : '+') + Math.abs(v).toFixed(1) + '%';
  const P = (head, body) => html`<p><b>${head}</b> ${body}</p>`;
  const tests = [
    { key: 'odds', claim: 'Markets set the odds, not the AI', verdict: 'Strong evidence', strong: true,
      label: 'Odds · markets vs an AI', value: html`${E.brierMarket.toFixed(3)} <small>vs</small> ${E.brierAI.toFixed(3)}`,
      caption: `Brier score of the markets, then of Claude, on ${E.oddsMarkets} markets that resolved after its training data. Lower is better.`,
      body: [
        P('The test.', `We took ${E.oddsMarkets} Polymarket and Kalshi markets that resolved after Claude’s training data ends, so it couldn’t know how they turned out. Thirty days before each deadline, Claude, with no tools and no news, gave its probability, and we set it beside the market’s price that day.`),
        P('What the number means.', `The Brier score is the average squared gap between a forecast and what happened, counting 1 if the event happened and 0 if not: 0 is perfect, and always saying 50% scores 0.25. The markets scored ${E.brierMarket.toFixed(3)} and Claude ${E.brierAI.toFixed(3)}. When Claude said 60% or more, the event happened half the time (${E.aiHigh[0]} of ${E.aiHigh[1]}); when the market did, ${pct0(E.marketHigh[0] / E.marketHigh[1])} of the time (${E.marketHigh[0]} of ${E.marketHigh[1]}).`),
        P('How it backs the engine.', `This is why every probability the engine shows comes from a matching market, never from the AI. The gap is too wide to be luck: its 95% range runs from ${E.brierGap[0].toFixed(3)} to ${E.brierGap[1].toFixed(3)}. A second test, on ${E.calMarkets.toLocaleString('en-US')} resolved markets, found that correcting market prices for their known biases didn’t help, so the engine uses them as they are. The AI keeps the jobs it does well: reading market rules, judging matches, and a first estimate that market evidence overrides.`),
        P('The catch.', 'Claude had no news or search here: the plain “just ask an AI” baseline. An AI given today’s news may close part of the gap, and that is the next thing to test. Where no market fits an event, the AI’s estimate is all there is, so the engine marks that event unpriced.'),
      ] },
    { key: 'replay', claim: 'An event moves prices by its jump times its odds change', verdict: 'Partial evidence',
      label: 'Replay · April 2025', onClick: () => goTab('replay'),
      value: live ? fmtShare(ex.explained_share) : null,
      caption: live ? `of the factors’ daily moves explained over the ten days of the tariff crash, with the right direction on ${ex.big_days_right} of ${ex.big_days} big moves.`
        : 'The engine replays the April 2025 tariff shock one evening at a time, seeing only the odds known at each close.',
      body: [
        P('The test.', 'We reran the engine on the April 2025 tariff crash one evening at a time, letting it see only the markets and prices that existed at each 4 p.m. close. Each evening it measured every event’s jump, how far each of the ten factors would move if the event happened, from the months before. The next day’s prediction is each jump times the change in its event’s odds, set against what the factors actually did.'),
        ...(live ? [
          P('What the number means.', `The prediction accounts for ${fmtShare(ex.explained_share)} of the factors’ daily moves${eqShare != null ? ', and ' + fmtShare(eqShare) + ' of stocks’' : ''}. It is scored like a forecast: predicting no move every day scores 0%, a perfect forecast 100%, and below zero is worse than no move. On the ${ex.big_days} big moves, a typical move or more in a day, it had the direction right ${ex.big_days_right} times.`),
          P('How it backs the engine.', `This is the engine’s core model on a crash it had never seen, and it carried real signal${matched ? ', with only ' + matched + ' of the ' + R.events.length + ' events matched to a market' : ''}. ${fits.length ? 'The other half of the math held as well: each sample portfolio’s exposures, fitted only on the two years before, explain ' + fitPct(Math.min(...fits)) + ' to ' + fitPct(Math.max(...fits)) + ' of its daily moves through the crash and the rebound. So the portfolio’s arrow is sound, and what’s missing is in the events.' : ''}`),
          P('The catch.', `It is one episode of ten days. The two biggest misses, the tariff announcement and China’s answer (April 3 and 4), had no exactly matching market the evening before: the cost of exact matching. ${call ? 'The one call, the tariff pause, pointed the right way but was far too small, ' + sgn(call.pred * typ) + ' for stocks against SPY’s ' + sgn(call.real * typ) + ', because its jump came from milder episodes in 2018 and 2019. ' : ''}And the share moves with which of two equivalent May rate-cut markets Claude picks as the match: reruns that took the other explained about ${pct0(E.replayRerun)}.`),
        ] : [P('The score.', 'The engine’s own replay, with its score, comes with the backend snapshot. Switch to it from the banner to see it here.')]),
      ] },
    { key: 'bars', claim: 'One market’s reading isn’t enough: a jump needs two that agree', verdict: 'Indirect evidence',
      label: 'Error bars · resolved markets', value: pct0(E.covered),
      caption: `of the real jumps on ${E.jumpMarkets} resolved markets landed inside the engine’s 80% ranges.`,
      body: [
        P('The test.', `We took ${E.jumpMarkets} markets that resolved between January 2025 and November 2026 on a clear day, when their odds made a final jump. For each, the engine measured the jump the evening before, from that market’s own odds history, with an 80% range, as a live refresh would. On the day it resolved, the real jump is how far each factor moved, divided by the change in the odds.`),
        P('What the number means.', `${pct0(E.covered)} of the real jumps landed inside the 80% ranges: close to the 80% promised, and a little on the safe side. So when the engine says how unsure it is, that can be believed.`),
        P('How it backs the engine.', `The same test showed how unsure a single reading is. Used as a forecast, one market’s reading did worse than predicting no move, and had the direction right ${E.signs[0]} times in ${E.signs[1]}: a coin flip. Readings that looked sure, at least two error bars from no move, turned up no more often than chance alone would make them (${E.sureReadings} of ${E.readings}, against about ${E.sureByChance} by chance). One sure-looking reading can be luck; two independent readings that agree rarely are. So a jump is confirmed (tier A) only when two agree, and until then it stays provisional, with its wide range shown.`),
        P('The catch.', 'Each past market has only one market reading, so the rule’s own payoff, confirmed jumps beating provisional ones, can’t be measured yet: options would give every factor a second reading. And most of the markets were events no factor notices, such as cabinet confirmations and a rocket launch, so this test says more about the error bars than about the events that matter.'),
      ] },
    { key: 'timing', claim: 'A jump is its first day’s move, with every price on one clock', verdict: 'Strong evidence', strong: true,
      label: 'Timing · lead and lag', value: html`${pct0(E.lateNoon)} <small>→</small> ${pct0(E.lateCarried)}`,
      caption: 'of each day’s dollar move showed up a day late: on the Fed’s noon fixing, then carried to the 4 p.m. close.',
      body: [
        P('The test.', `Granger causality asks whether one series’ past helps forecast another’s next move. We asked it three things. Since 2008, does yesterday’s move in one factor forecast today’s in another? On ${E.oddsTimingMarkets} markets that matter, do the factors keep moving the day after an event’s odds change? And is every factor read at the same time of day?`),
        P('What the number means.', `The Fed’s dollar index is fixed at noon, four hours before the 4 p.m. close at which every other price is read, so ${pct0(E.lateNoon)} of each day’s dollar move only showed up the next day. Carried to 4 p.m. with the afternoon move of UUP, a dollar fund that trades then, the late share falls to ${pct0(E.lateCarried)}.`),
        P('How it backs the engine.', `The first two answers back the one-day jump. Yesterday’s factor moves forecast today’s worse than predicting no move, on every stretch since 2008, and no factor kept moving the day after an odds change. So an event’s effect lands on its first day, which is what the engine’s one-day windows measure. The clock matters because those windows compare moves on the same day: on the noon clock, five factors seemed to lead the dollar, and international funds looked less tied to it than they are. After the fix, ${E.fundsBetter[0]} of ${E.fundsBetter[1]} funds’ exposures fit unseen days better.`),
        P('What Granger causality could and couldn’t do.', `We first hoped it could tell which way an event pushes each factor. It can’t: a jump is a same-day relation, and when two things move together on the same day, timing can’t say which drove the other. So the direction still comes from the jump windows, which tie each day’s moves to named events’ odds changes. What it did was check the engine’s timing, and that found the dollar’s clock. Where it was meant to find links on its own, it failed: links between markets picked by the data alone predicted later days with ${pct0(E.dataLinksWorse)} more error than no link at all, which is why a link also needs a mechanism.`),
        P('The catch.', `${pct0(E.lateCarried)} isn’t zero, and days before 2007 keep the noon reading. Odds on the S&P 500’s and the Nasdaq’s own levels trail stocks by a day, most likely stale prices, and bonds and oil may move a day before related odds. Thirty-minute windows would settle both.`),
      ] },
    { key: 'joint', claim: 'A range should count how the factors’ errors move together', verdict: 'Not yet',
      label: 'Error bubbles · errors together', value: html`${pct0(E.insideJoint)} <small>vs</small> ${pct0(E.insideApart)}`,
      caption: `of ${E.jointDays} resolution days landed inside the 80% zone, with the errors moving together as measured, against one by one.`,
      body: [
        P('The test.', `A portfolio feels every factor’s error at once, so how the errors move together sets how wide its range is: a hedge’s errors can cancel, a concentrated bet’s pile up. On the ${E.jointDays} days those markets resolved, we checked whether the day’s whole move, all ten factors at once, landed inside each forecast’s 80% zone, with the errors moving together as measured or taken one by one.`),
        P('What the number means.', `The joint zones held the day’s move on ${pct0(E.insideJoint)} of days, against ${pct0(E.insideApart)} one by one: they were too sure of themselves. They scored better on ${E.jointWins} of the ${E.jointDays} days, but a few confidently wrong days kept the gain short of significance.`),
        P('How it backs the engine.', 'The rule was written down before the run: no clear win, no change. So the ranges count each factor’s error on its own, and the app says so beside each one. The engine follows its tests even when they say not yet, which is the reason to fix the rules first.'),
        P('The catch.', `At half strength the zones held ${pct0(E.insideHalf)} of days, the best calibrated of the three, but that was found after the fact. The next run, on markets that resolve after November 7, 2026, tests half strength against none, with that rule fixed before it runs.`),
      ] },
  ];
  const tile = t => {
    const inner = html`<span className="label">${t.label}</span><div className=${'tile-v' + (t.value == null ? ' small' : '')}>${t.value == null ? 'Day by day' : t.value}</div><div className="tile-s">${t.caption}</div>`;
    return t.onClick ? html`<button className="tile m-tile" onClick=${t.onClick}>${inner}</button>` : html`<div className="tile m-tile">${inner}</div>`;
  };
  return html`<section className="card" aria-labelledby="m-test-h">
    <div className="card-head"><div><span className="kicker">How it’s tested</span><h3 className="h3" id="m-test-h" style=${{ marginTop: 6 }}>Checked against what happened</h3>
      <p className="sub">The engine rests on four claims, and each test below checks one against what really happened, with its measure written down before its first run. A fifth checks the newest piece, the error bubbles. Each says what was done, what the number means, how it backs the engine, and where the evidence is still thin.</p></div></div>
    <ol className="m-tests">${tests.map((t, i) => html`<li key=${t.key} className="m-test">
      <div className="m-test-h"><span className="m-n">${String(i + 1).padStart(2, '0')}</span><h4 className="m-step-t">${t.claim}</h4><span className=${'pill m-verdict' + (t.strong ? ' pill-acc' : '')}>${t.verdict}</span></div>
      <div className="m-test-g">${tile(t)}<div className="m-test-b">${t.body}</div></div>
    </li>`)}</ol>
    <span className="label m-sub-l">What it doesn’t do yet</span>
    <ul className="m-limits">
      <li><b>Find events on its own.</b> The screen finds the markets that matter, but the library’s events are still picked by hand.</li>
      <li><b>Read options.</b> They would give every factor a second market reading, so more jumps could be confirmed.</li>
      <li><b>Give the AI prior today’s news.</b> It reasons from its training data, so it can lag the markets. It never sets odds where a market fits, but it does weigh on jumps the markets can’t pin down.</li>
      <li><b>Use stress-day exposures.</b> Markets move together more in a sell-off, so a big shock can hit harder than exposures measured on ordinary days suggest.</li>
      <li><b>Count how the factors’ errors move together.</b> The engine measures it, so a hedge’s errors could cancel in its ranges, but the first test on resolved markets found the measured links too strong. Until a larger test backs them, each factor’s error counts on its own.</li>
    </ul>
  </section>`;
}

function MethodSources() {
  return html`<section className="card" aria-labelledby="m-src-h">
    <div className="card-head"><div><span className="kicker">Where the numbers come from</span><h3 className="h3" id="m-src-h" style=${{ marginTop: 6 }}>Markets first, the AI second</h3></div></div>
    <p className="m-p m-first">Odds come from Polymarket and Kalshi, prices and factor data from Massive and FRED, and weekend prices from Hyperliquid’s perps. Claude labels markets for the screen, judges the matches, picks past analogs and writes a starting estimate, but wherever a market can measure a number, the market’s evidence wins.</p>
    <p className="m-p">The design doc in the repository (docs/DESIGN.md) has the math, every alternative weighed, and the references. Nothing here is investment advice.</p>
  </section>`;
}

function MethodView({ P, shown, tw, onOpen, goTab }) {
  const top = P.V > 0 && shown.length ? shown[0] : null;
  return html`<div className="method">
    <${MethodExample} P=${P} top=${top} onOpen=${onOpen} />
    <${MethodSteps} tw=${tw} />
    <${MethodTests} goTab=${goTab} />
    <${MethodSources} />
  </div>`;
}
