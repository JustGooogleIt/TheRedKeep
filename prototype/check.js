// Checks the prototype's math against the design doc. Run: node prototype/check.js [live.js]
// With a live.js from `redkeep app`, it also runs the checks on the snapshot's events, and checks that the app reproduces
// the backend engine's numbers for every portfolio the snapshot analyzed. A snapshot of events only keeps the
// illustrative portfolios: their exposures per unit of each factor, and the April 2025 replay, must not change. A
// snapshot that carries the engine's replay (`redkeep replay --write`) must show it, with the engine's numbers.
'use strict';
const fs = require('fs'), path = require('path'), vm = require('vm');

const src = f => fs.readFileSync(path.join(__dirname, 'src', f), 'utf8');
const livePath = process.argv[2];
const M = vm.runInNewContext(
  (livePath ? fs.readFileSync(livePath, 'utf8') : '') + '\n' + src('data.js') + '\n' + src('model.js') + '\n' + src('dataset.js') +
  '\n;({ SAMPLES, FACTORS, TICKERS, analyzePortfolio, buildState, simulate, replayCompute, applyDataset, HAS_LIVE, LIVE_PORTFOLIOS,' +
  ' REPLAY, replaySource: () => REPLAY_SOURCE, LIVE: typeof RK_LIVE === "undefined" ? null : RK_LIVE })',
  { console },
);

let failures = 0;
function check(name, ok, detail) {
  console.log((ok ? 'ok    ' : 'FAIL  ') + name + (detail ? '  (' + detail + ')' : ''));
  if (!ok) failures++;
}
const near = (a, b, tol) => Math.abs(a - b) <= tol;
const tw = { knock: true, horizon: 6, odds: 'market', noise: false };
const holdings = s => s.h.map(([t, u, a], i) => ({ id: t + i, t, u, a }));

// The worked example in DESIGN.md section 3: $1M of QQQ, tariffs at 40%.
{
  const P = analyze('qqq');
  const e = M.buildState(P, tw, {}, {}).byId.tariff;
  const f = x => (x > 0 ? '+' : '') + x.toFixed(2) + '%';
  check('section 3: direct hit is -6.0%', near(e.dD, -6.0, 0.05), f(e.dD));
  check('section 3: total jump is -8.5%', near(e.dT, -8.5, 0.05), f(e.dT));
  check('section 3: priced in is -3.4%', near(e.priced, -3.4, 0.05), f(e.priced));
  check('section 3: if tariffs happen, -5.1%', near(e.ifYes, -5.1, 0.05), f(e.ifYes));
  check('section 3: if they do not, +3.4%', near(e.ifNo, 3.4, 0.05), f(e.ifNo));
}

// The identities in section 4, on every sample portfolio and every event.
function identities(label) {
  for (const s of M.SAMPLES) {
    const P = analyze(s.id);
    const S = M.buildState(P, tw, {}, {});
    let pricedIn = 0, knock = 0, swing = 0;
    for (const e of S.evs) {
      pricedIn = Math.max(pricedIn, Math.abs(e.qEff * e.ifYes + (1 - e.qEff) * e.ifNo));
      const viaChildren = e.children.reduce((t, c) => t + c.w * S.byId[c.id].dT, 0);
      knock = Math.max(knock, Math.abs(e.dT - e.dD - viaChildren));
      swing = Math.max(swing, Math.abs(e.swingPct ** 2 - e.swing));
    }
    check(label + s.id + ': the priced-in moves net to zero at market odds (4.2)', pricedIn < 1e-9);
    check(label + s.id + ': knock-on jumps add up through the graph (4.4)', knock < 1e-9);
    check(label + s.id + ': the swing matches q(1-q) times the jump squared (4.3)', swing < 1e-9);

    // The simulator (4.7): zero mean at market odds, and seeded so a run repeats exactly.
    const a = M.simulate(S, P, tw, 4000), b = M.simulate(S, P, tw, 4000);
    const se = a.sd / Math.sqrt(a.n);
    check(label + s.id + ': the simulated mean is zero within 4 standard errors (4.7)', Math.abs(a.mean) <= 4 * se,
      a.mean.toFixed(3) + '% vs +/-' + (4 * se).toFixed(3) + '%');
    check(label + s.id + ': the simulation repeats exactly', a.mean === b.mean && a.p5 === b.p5 && a.es === b.es);
  }
}
identities('');

function analyze(id) { return M.analyzePortfolio(holdings(M.SAMPLES.find(s => s.id === id))); }
const perUnit = P => P.b.map((v, k) => v / M.FACTORS[k].typical);  // exposure per 1% or 1 bp of each factor

// The fit the page estimates for a mix it has no measurement of (model.js fitEstimate).
function fits(label) {
  const t = Object.keys(M.TICKERS).find(k => M.TICKERS[k].r2 > 0 && !M.TICKERS[k].x);
  const one = M.analyzePortfolio([{ id: 'one', t, u: 'usd', a: 123457 }]);  // an amount no sample holds
  check(label + 'one holding alone gets its own fit back', !one.fitMeasured && near(one.fit, M.TICKERS[t].r2, 1e-12), t + ' ' + one.fit.toFixed(4));
  for (const s of M.SAMPLES) {
    const P = analyze(s.id);
    check(label + s.id + ': the fit is a share between 0 and 1', P.fit == null || (P.fit >= 0 && P.fit <= 1), P.fit == null ? 'none' : P.fit.toFixed(3));
  }
}
fits('');

if (livePath) {
  check('live.js holds a backend snapshot', M.HAS_LIVE);
  const engineReplay = !!(M.LIVE.replay && Array.isArray(M.LIVE.replay.days) && M.LIVE.replay.days.length);
  const before = Object.fromEntries(M.SAMPLES.map(s => { const P = analyze(s.id); return [s.id, { unit: perUnit(P), replay: M.replayCompute(P, true) }]; }));
  M.applyDataset('live');
  identities('snapshot · ');

  // Events only: the illustrative portfolios stay, rescaled to the snapshot's typical moves.
  if (!M.LIVE_PORTFOLIOS) for (const s of M.SAMPLES) {
    const P = analyze(s.id), was = before[s.id];
    const gap = Math.max(...perUnit(P).map((v, k) => Math.abs(v - was.unit[k])));
    check(s.id + ': illustrative exposures keep their size per unit of each factor', gap < 1e-12, gap.toExponential(1) + ' largest gap');
    if (engineReplay) continue;
    const R = M.replayCompute(P, true);
    const rGap = Math.max(...R.real.map((v, i) => Math.abs(v - was.replay.real[i])), ...R.calls.map((c, i) => Math.abs(c.pred - was.replay.calls[i].pred)));
    check(s.id + ': the April 2025 replay is unchanged', rGap < 1e-9, rGap.toExponential(1) + ' largest gap, in % points');
  }

  // The engine's replay: on an arrow of one typical move of stocks, the app's explained moves and calls are the
  // engine's on the stock factor (DESIGN.md section 10).
  if (engineReplay) {
    const R = M.REPLAY, EQ = M.FACTORS.findIndex(f => f.k === 'eq');
    check('replay: the replay screen shows the engine\'s replay', M.replaySource() === 'engine' && R.days.length === M.LIVE.replay.days.length);
    if (R.typical[EQ] == null) check('replay: the engine measured stocks', false, 'no typical move for stocks');
    else {
      const arrow = { b: M.FACTORS.map((f, k) => (k === EQ ? f.typical / R.typical[EQ] : 0)) };
      const RC = M.replayCompute(arrow, true);
      let eGap = 0, n = 0, cGap = 0, m = 0;
      R.check.explained.forEach((row, i) => { if (row[EQ] == null) return; eGap = Math.max(eGap, Math.abs((RC.expl[i] || 0) - row[EQ])); n++; });
      R.check.calls.forEach(c => {
        if (c.pred[EQ] == null) return;
        const x = RC.calls.find(z => z.id === c.id);
        cGap = Math.max(cGap, x && x.pred != null ? Math.abs(x.pred - c.pred[EQ]) : Infinity); m++;
      });
      check('replay: the explained stock moves match the engine', n > 0 && eGap < 1e-9, n + ' days, ' + eGap.toExponential(1) + ' largest gap, in typical moves');
      check('replay: the calls on stocks match the engine', cGap < 1e-9, m + ' calls, ' + cGap.toExponential(1) + ' largest gap, in typical moves');
      const real = RC.real.map((v, i) => (R.days[i].f[EQ] == null ? null : v));
      const zGap = Math.max(0, ...real.map((v, i) => (v == null ? 0 : Math.abs(v - R.days[i].f[EQ] / R.typical[EQ]))));
      check('replay: the days\' stock moves are the engine\'s, in its typical moves', zGap < 1e-9, zGap.toExponential(1) + ' largest gap');
      // Without knock-ons, an open event's error bar on that arrow is the engine's on stocks that evening, times 1 − q.
      // A snapshot made before the replay carried error bars has none to check.
      if (R.events.some(e => e.sd)) {
        const RN = M.replayCompute(arrow, false);
        let sGap = 0, k = 0;
        RN.states.forEach((row, i) => row.forEach(s => {
          const e = R.events.find(z => z.id === s.id), sd = e.sd && e.sd[i + 1];
          if (s.status !== 'open' || s.dT == null || !sd || sd[EQ] == null) return;
          sGap = Math.max(sGap, Math.abs(s.ifYesErr - (1 - s.q) * sd[EQ])); k++;
        }));
        check('replay: the error bars on stocks are the engine\'s', k > 0 && sGap < 1e-9, k + ' event-days, ' + sGap.toExponential(1) + ' largest gap, in typical moves');
      }
    }
    // E8's portfolio metric: on each sample's arrow as it stood the evening before the episode, the app's explained
    // moves are the ones the engine scored that portfolio on.
    for (const [id, p] of Object.entries(M.LIVE.replay.portfolios || {})) {
      const arrow = { b: M.FACTORS.map((f, k) => (R.typical[k] == null ? 0 : p.b[k] * f.typical / R.typical[k])) };
      const RC = M.replayCompute(arrow, true);
      const pGap = Math.max(0, ...p.days.map((d, i) => Math.abs((RC.expl[i] || 0) - d[1])));
      check('replay: ' + id + '\'s explained moves, on its arrow of ' + R.base + ', are the engine\'s', p.days.length === R.days.length && pGap < 1e-9,
        pGap.toExponential(1) + ' largest gap, in % points');
    }
  }

  // The engine's own portfolios: the app's model must reproduce the engine's numbers for each.
  for (const r of M.LIVE.results) {
    const P = analyze(r.sample);
    if (r.fit != null) check(r.sample + ': the fit is the one the engine measured', P.fitMeasured && P.fit === r.fit, (P.fit * 100).toFixed(1) + '% vs ' + (r.fit * 100).toFixed(1) + '%');
    check(r.sample + ': value matches the engine', Math.abs(P.V - r.value) <= 1e-6 * Math.max(1, r.value), P.V.toFixed(2) + ' vs ' + r.value.toFixed(2));
    const bGap = Math.max(...P.b.map((v, k) => Math.abs(v - r.b[k])));
    check(r.sample + ': the exposure arrow matches the engine', bGap < 1e-4, bGap.toExponential(1) + ' largest gap');
    const S = M.buildState(P, { ...tw, horizon: r.horizon }, {}, r.whatIf || {});
    let gap = 0;
    for (const x of r.events) {
      const e = S.byId[x.id];
      gap = Math.max(gap, Math.abs(e.dD - x.direct), Math.abs(e.dT - x.total), Math.abs(e.ifYes - x.if_yes), Math.abs(e.ifNo - x.if_no), Math.abs(e.swingPct - x.swing_pct));
    }
    check(r.sample + ': every event\'s moves match the engine', gap < 1e-4, gap.toExponential(1) + ' largest gap, in % points');
  }
}

if (livePath && M.LIVE_PORTFOLIOS && M.LIVE.results.length) {
  // A portfolio the engine didn't analyze, such as a sample with one amount changed, gets the estimate instead.
  const s = M.SAMPLES.find(x => x.id === M.LIVE.results[0].sample);
  const h = holdings(s); h[0] = { ...h[0], a: h[0].a * 1.5 };
  const P = M.analyzePortfolio(h);
  check(s.id + ' with one amount changed: the fit is estimated, not the engine\'s', !P.fitMeasured && P.fit != null, P.fit == null ? 'none' : P.fit.toFixed(3));
  fits('snapshot · ');
}

console.log(failures ? '\n' + failures + ' check(s) failed' : '\nall checks passed');
process.exit(failures ? 1 : 0);
