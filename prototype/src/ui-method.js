/* ==========================================================================
   UI · Part 4: the methodology tab. A short tour in plain words: the
   priced-in idea, the seven steps on the analysis tab's strip, how the
   engine is tested and what it doesn't do yet. The math, the rejected
   alternatives and the references are in docs/DESIGN.md; the saved eval
   results quoted here are from docs/WRITEUP.md.
   ========================================================================== */

// The worked example in DESIGN.md section 3, with its illustrative numbers: $1M of QQQ, tariffs on China at 40%.
const M_EXAMPLE = { V: 1e6, direct: -6.0, knock: -2.5, q: 0.4 };
// Results of the saved eval runs (WRITEUP.md, "What the evals found"): E9's odds and E0's screen from Nov 8,
// 2026, E4's coverage over E3's 55 resolved markets from the ten-factor rerun of Nov 10, E10's dollar clock
// (the share of a 4 p.m. move the series shows a day late) from Nov 13, and E11's joint error bars from Nov 13
// (the share of resolution days whose whole move landed inside each forecast's 80% zone). Update them when those
// evals rerun. The replay's numbers are not here: they come with the snapshot (REPLAY.metrics).
const M_EVALS = { brierMarket: 0.110, brierAI: 0.191, oddsMarkets: 250, covered: 0.84, jumpMarkets: 55, screenFinds: 0.96, screenSends: 0.38,
  lateNoon: 0.23, lateCarried: 0.06, jointDays: 47, insideJoint: 0.64, insideApart: 0.77, insideHalf: 0.79 };

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
    ['Discover', 'Events that could move you, and the event map', html`<p>${markets} Claude labels a sample of them, and a small model trained on those labels screens the rest: on markets it hadn’t seen, it kept ${pct0(M_EVALS.screenFinds)} of the ones that matter while sending Claude only ${pct0(M_EVALS.screenSends)}. The events that survive form a shared library, and each portfolio sees the ones that resolve inside its horizon, ${tw.horizon} months here.</p>`],
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

function MethodTests({ goTab }) {
  const ex = REPLAY_SOURCE === 'engine' && REPLAY.metrics ? REPLAY.metrics.explained : null;
  return html`<section className="card" aria-labelledby="m-test-h">
    <div className="card-head"><div><span className="kicker">How it’s tested</span><h3 className="h3" id="m-test-h" style=${{ marginTop: 6 }}>Checked against what happened</h3>
      <p className="sub">Each test’s measure was written down before its first run, and one of them asks whether the engine beats simply asking an AI.</p></div></div>
    <div className="m-tests">
      <div className="tile m-tile"><span className="label">Odds · markets vs an AI</span><div className="tile-v">${M_EVALS.brierMarket.toFixed(3)} <small>vs</small> ${M_EVALS.brierAI.toFixed(3)}</div>
        <div className="tile-s">Brier score, lower is better, on ${M_EVALS.oddsMarkets} markets that resolved after Claude’s training data. The markets won, so a market sets the odds wherever one fits.</div></div>
      ${ex && ex.explained_share != null
        ? html`<button className="tile m-tile" onClick=${() => goTab('replay')}><span className="label">Replay · April 2025</span><div className="tile-v">${fmtShare(ex.explained_share)}</div>
            <div className="tile-s">of the daily factor moves came from the events’ jumps times their odds changes, rerunning the engine evening by evening on archived prices. Right sign on ${ex.big_days_right} of ${ex.big_days} big moves.</div></button>`
        : html`<button className="tile m-tile" onClick=${() => goTab('replay')}><span className="label">Replay · April 2025</span><div className="tile-v small">Day by day</div>
            <div className="tile-s">The engine replays the April 2025 tariff shock one evening at a time, seeing only the odds known at each close.</div></button>`}
      <div className="tile m-tile"><span className="label">Error bars · resolved markets</span><div className="tile-v">${pct0(M_EVALS.covered)}</div>
        <div className="tile-s">of the jumps on ${M_EVALS.jumpMarkets} resolved markets landed inside the engine’s 80% ranges. One market’s reading alone was too vague to act on, which is why a jump needs two that agree.</div></div>
      <div className="tile m-tile"><span className="label">Timing · lead and lag</span><div className="tile-v">${pct0(M_EVALS.lateNoon)} <small>→</small> ${pct0(M_EVALS.lateCarried)}</div>
        <div className="tile-s">of each day’s dollar move showed up a day late, read at the Fed’s noon fixing and then carried to the 4 p.m. close like every other factor. The same tests found that yesterday’s moves forecast today’s worse than no move, so an event’s jump is its first day’s move.</div></div>
      <div className="tile m-tile"><span className="label">Error bubbles · errors together</span><div className="tile-v">${pct0(M_EVALS.insideJoint)} <small>vs</small> ${pct0(M_EVALS.insideApart)}</div>
        <div className="tile-s">of ${M_EVALS.jointDays} resolution days landed inside the 80% zone with the factors’ errors moving together as measured, against one by one. The measured links were too strong, so ranges count each factor’s error on its own for now. At half strength the zone held ${pct0(M_EVALS.insideHalf)}, the next thing to test on new markets.</div></div>
    </div>
    <span className="label m-sub-l">What it doesn’t do yet</span>
    <ul className="m-limits">
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
