// Checks the prototype's math against the design doc. Run: node prototype/check.js
'use strict';
const fs = require('fs'), path = require('path'), vm = require('vm');

const src = f => fs.readFileSync(path.join(__dirname, 'src', f), 'utf8');
const M = vm.runInNewContext(
  src('data.js') + '\n' + src('model.js') + '\n;({ SAMPLES, analyzePortfolio, buildState, simulate })',
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
  check(s.id + ': the priced-in moves net to zero at market odds (4.2)', pricedIn < 1e-9);
  check(s.id + ': knock-on jumps add up through the graph (4.4)', knock < 1e-9);
  check(s.id + ': the swing matches q(1-q) times the jump squared (4.3)', swing < 1e-9);

  // The simulator (4.7): zero mean at market odds, and seeded so a run repeats exactly.
  const a = M.simulate(S, P, tw, 4000), b = M.simulate(S, P, tw, 4000);
  const se = a.sd / Math.sqrt(a.n);
  check(s.id + ': the simulated mean is zero within 4 standard errors (4.7)', Math.abs(a.mean) <= 4 * se,
    a.mean.toFixed(3) + '% vs +/-' + (4 * se).toFixed(3) + '%');
  check(s.id + ': the simulation repeats exactly', a.mean === b.mean && a.p5 === b.p5 && a.es === b.es);
}

function analyze(id) { return M.analyzePortfolio(holdings(M.SAMPLES.find(s => s.id === id))); }

console.log(failures ? '\n' + failures + ' check(s) failed' : '\nall checks passed');
process.exit(failures ? 1 : 0);
