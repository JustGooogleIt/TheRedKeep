/* ==========================================================================
   MODEL · pure functions, no DOM. Mirrors docs/DESIGN.md sections 4 and 6.
   ========================================================================== */
const MINUS = '−';
function mulberry32(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
function hashStr(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const logit = p => Math.log(p / (1 - p));
const sigmoid = z => 1 / (1 + Math.exp(-z));
function gauss(rng) { let u = 0, v = 0; while (u === 0) u = rng(); while (v === 0) v = rng(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }

const DAY = 86400000;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function parseDate(s) { const [y, m, d] = s.split('-').map(Number); return Date.UTC(y, m - 1, d); }
function daysBetween(a, b) { return Math.round((parseDate(b) - parseDate(a)) / DAY); }
function fmtDate(s, withYear = true) { const [y, m, d] = s.split('-').map(Number); return MONTHS[m - 1] + ' ' + d + (withYear ? ', ' + y : ''); }

const TIERS = {
  A: { name: 'Confirmed', rule: 'Odds from a well-matched, liquid market, and the jump backed by two or more precise market readings that agree.' },
  B: { name: 'Contested', rule: 'Market readings disagree beyond their error bars, or the market\'s jump points the opposite way from past events and the AI. The disagreement is the insight.' },
  C: { name: 'Provisional', rule: 'Odds are observed, but the jump has fewer than two precise market readings or the match is weak.' },
  D: { name: 'Unpriced', rule: 'No market fits. Odds and jump come from past events and the AI only.' },
};
const MARKET_KINDS = new Set(['opt', 'hl', 'etf']);
// A market reading counts toward the quorum only if it could confirm the jump alone: its error bar on the
// jump's size is at most half the jump, so it tells the jump from no jump at two standard errors.
const QUORUM_SE = 0.5;

/* ---------- 6.1 Exposure engine: look-through, arrow, extras, fit ---------- */
function holdingValue(h) { const t = TICKERS[h.t]; if (!t || !(h.a > 0)) return 0; return h.u === 'usd' ? h.a : h.a * t.px; }

function analyzePortfolio(holdings) {
  const rows = holdings.filter(h => TICKERS[h.t] && h.a > 0).map(h => ({ ...h, v: holdingValue(h) }));
  const V = rows.reduce((s, r) => s + r.v, 0);
  rows.forEach(r => { r.w = V > 0 ? r.v / V : 0; });
  const b = new Array(NF).fill(0); const xRaw = {};
  rows.forEach(r => {
    const t = TICKERS[r.t];
    for (let k = 0; k < NF; k++) b[k] += r.w * t.b[k];
    if (t.x) for (const e in t.x) xRaw[e] = (xRaw[e] || 0) + r.w * t.x[e];
  });
  // Extras: added only when the portfolio's exposure to them is material.
  const extras = EXTRAS.filter(e => Math.abs(xRaw[e.k] || 0) >= 0.15).map(e => ({ ...e, v: xRaw[e.k] }));
  const added = new Set(extras.map(e => e.k));
  // The fit: the engine's own measurement when it analyzed this exact portfolio, otherwise an estimate.
  const measured = MEASURED_FIT[holdingsKey(rows)];
  const fitMeasured = measured != null;
  const fitCore = fitMeasured ? null : fitEstimate(rows, null), fit = fitMeasured ? measured : fitEstimate(rows, added);
  // Look-through: replace each fund with what it holds.
  const lt = new Map();
  const add = (key, name, w, via, kind) => {
    let o = lt.get(key);
    if (!o) { o = { key, name, w: 0, via: [], kind }; lt.set(key, o); }
    o.w += w; o.via.push([via, w]);
  };
  rows.forEach(r => {
    const t = TICKERS[r.t];
    if (t.k === 'Stock') add(r.t, t.n, r.w, 'direct', 'stock');
    else if (t.h) {
      let covered = 0;
      t.h.forEach(([k, p]) => { add(k, NAMES[k] || (TICKERS[k] && TICKERS[k].n) || k, r.w * p / 100, r.t, 'stock'); covered += p; });
      add('rest:' + r.t, 'Other ' + r.t + ' holdings', r.w * (100 - covered) / 100, r.t, 'rest');
    } else if (t.line) add('line:' + r.t, t.line, r.w, r.t, 'line');
    else add('line:' + r.t, t.n, r.w, r.t, 'fund');  // a fund whose holdings this data doesn't have: held whole
  });
  const look = [...lt.values()].sort((a, c) => c.w - a.w);
  const stockW = {}; look.forEach(o => { if (o.kind === 'stock') stockW[o.key] = o.w; });
  return { rows, V, b, xRaw, extras, added, fit, fitCore, fitMeasured, look, stockW, nUnderlying: look.filter(o => o.kind === 'stock').length };
}

/* A portfolio's out-of-sample fit, estimated from each holding's own (r2, or r2x with the extras it uses). Factor-driven
   moves add up across holdings, while each holding's own moves partly cancel, so a mix usually fits better than its
   holdings do on average. This treats the factors, and the holdings' own moves, as uncorrelated; a holding the factors
   don't explain at all (cash) adds nothing either way. One holding alone gets its own fit back. */
function fitEstimate(rows, added) {
  const bp = new Array(NF).fill(0), xp = {};
  let own = 0, n = 0;
  rows.forEach(r => {
    const t = TICKERS[r.t];
    const ext = added && t.x ? Object.keys(t.x).filter(k => added.has(k)) : [];
    const r2 = ext.length ? (t.r2x ?? t.r2) : t.r2;
    let sys = 0;  // the holding's factor-driven variance, in typical moves
    for (let k = 0; k < NF; k++) { bp[k] += r.w * t.b[k]; sys += t.b[k] ** 2; }
    ext.forEach(k => { xp[k] = (xp[k] || 0) + r.w * t.x[k]; sys += t.x[k] ** 2; });
    if (r2 == null) return;
    n++;
    if (r2 > 0) own += r.w ** 2 * sys * (1 - Math.min(r2, 1)) / Math.max(r2, 0.01);
  });
  const sp = bp.reduce((s, v) => s + v * v, 0) + Object.values(xp).reduce((s, v) => s + v * v, 0);
  return n && sp + own > 0 ? sp / (sp + own) : null;
}

/* Holdings as the engine received them, so a portfolio it analyzed finds its measured fit (dataset.js). */
function holdingsKey(rows) {
  return rows.filter(r => TICKERS[r.t] && r.a > 0).map(r => r.t + ':' + r.u + ':' + +r.a).sort().join(',');
}

/* ---------- 4.1 Impact: the dot product, plus extras and the direct channel ---------- */
function contributions(ev, P) {
  const out = [];
  for (let k = 0; k < NF; k++) { const c = P.b[k] * ev.d[k]; if (Math.abs(c) > 0.004) out.push({ key: FACTORS[k].k, name: FACTORS[k].short, move: ev.d[k], exp: P.b[k], c, unit: FACTORS[k] }); }
  if (ev.x) for (const e in ev.x) if (P.added.has(e)) { const ex = EXTRAS.find(z => z.k === e); out.push({ key: e, name: ex.name, move: ev.x[e], exp: P.xRaw[e] || 0, c: (P.xRaw[e] || 0) * ev.x[e], extra: true, unit: ex }); }
  if (ev.dir) for (const st in ev.dir) { const w = P.stockW[st] || 0; if (w > 0) out.push({ key: 'dir:' + st, name: (NAMES[st] || st) + ' (own line)', move: ev.dir[st], exp: w, c: w * ev.dir[st], direct: true }); }
  return out.sort((a, c) => Math.abs(c.c) - Math.abs(a.c));
}
// The exact direct move. contributions() leaves out negligible terms, which only matters for display.
function eventDirect(ev, P) {
  let s = dot(P.b, ev.d);
  if (ev.x) for (const e in ev.x) if (P.added.has(e)) s += (P.xRaw[e] || 0) * ev.x[e];
  if (ev.dir) for (const st in ev.dir) s += (P.stockW[st] || 0) * ev.dir[st];
  return s;
}

/* ---------- 4.2-4.4 Odds, what-ifs, knock-ons, priced-in moves, swing ---------- */
function buildState(P, tw, live, whatIf) {
  const evs = EVENTS.map(e => ({ ...e, parents: [], children: [] }));
  const byId = Object.fromEntries(evs.map(e => [e.id, e]));
  const order = [...evs].sort((a, c) => parseDate(a.date) - parseDate(c.date) || EVENTS.findIndex(z => z.id === a.id) - EVENTS.findIndex(z => z.id === c.id));
  const links = tw.knock ? LINKS : [];
  links.forEach(([p, c, w]) => { byId[p].children.push({ id: c, w }); byId[c].parents.push({ id: p, w }); });
  evs.forEach(e => {
    const off = (live && live[e.id]) || 0;
    e.q0 = clamp(e.q + off, 0.005, 0.995); e.ph0 = clamp(e.ph + off, 0.005, 0.995);
  });
  order.forEach(e => {
    const wi = whatIf && whatIf[e.id];
    if (wi) { e.resolved = wi; e.qEff = wi === 'yes' ? 1 : 0; e.shift = e.qEff - e.q0; }
    else {
      let shift = 0;
      e.parents.forEach(p => { const pe = byId[p.id]; shift += p.w * (pe.qEff - pe.q0); });
      e.qEff = clamp(e.q0 + shift, 0.005, 0.995); e.shift = e.qEff - e.q0;
    }
    e.phEff = e.resolved ? e.qEff : clamp(e.ph0 + e.shift, 0.005, 0.995);
  });
  evs.forEach(e => { e.contrib = contributions(e, P); e.dD = eventDirect(e, P); });
  [...order].reverse().forEach(e => {
    e.knock = e.children.map(c => { const ce = byId[c.id]; return { id: c.id, w: c.w, d: ce.resolved ? 0 : c.w * ce.dT }; });
    e.dT = e.dD + e.knock.reduce((s, k) => s + k.d, 0);
  });
  const H = tw.horizon * 30.44;
  evs.forEach(e => {
    const q = e.qEff;
    e.days = daysBetween(TODAY, e.date); e.inH = e.days <= H;
    e.priced = q * e.dT; e.ifYes = (1 - q) * e.dT; e.ifNo = -q * e.dT;
    e.swing = q * (1 - q) * e.dT * e.dT; e.swingPct = Math.abs(e.dT) * Math.sqrt(q * (1 - q));
    e.edge = (e.phEff - q) * e.dT;
  });
  const whatIfMove = evs.reduce((s, e) => s + e.dD * (e.qEff - e.q0), 0);
  return { evs, byId, order, links, whatIfMove };
}

/* ---------- 4.6 Evidence fusion: precision-weighted average and chi-square check ---------- */
function fuseReadings(src) {
  const rd = src.map(([kind, name, detail, k, sig]) => ({ kind, name, detail, k, sig, market: MARKET_KINDS.has(kind) }));
  let sw = 0, sx = 0; rd.forEach(r => { const w = 1 / (r.sig * r.sig); sw += w; sx += w * r.k; });
  const kbar = sx / sw;
  rd.forEach(r => { r.kn = r.k / kbar; r.sn = r.sig / kbar; r.w = (1 / (r.sig * r.sig)) / sw; r.precise = r.market && Math.abs(r.sn) <= QUORUM_SE; });
  const mk = rd.filter(r => r.market);
  let chi2 = 0; mk.forEach(r => { chi2 += Math.pow((r.kn - 1) / r.sn, 2); });
  const dof = Math.max(mk.length - 1, 0);
  const crit = [0, 3.84, 5.99, 7.81, 9.49][dof] || 9.49;
  // Every market reading takes part in the χ² check, so a vague one can still contest the jump; only precise ones confirm it.
  return { rd, nMarket: mk.filter(r => r.precise).length, nAll: mk.length, chi2, dof, crit, agree: dof ? chi2 <= crit : null };
}
function oddsDisagree(odds) {
  if (!odds || odds.length < 2) return null;
  const [a, b] = odds; const gap = Math.abs(a[1] - b[1]); const se = Math.sqrt(a[2] * a[2] + b[2] * b[2]);
  return { gap, se, z: gap / se };
}

/* ---------- 4.7 Scenario simulation over the event graph ---------- */
function simulate(S, P, tw, n) {
  const evs = S.order.filter(e => !e.resolved);
  const idx = new Map(evs.map((e, i) => [e.id, i]));
  const m = evs.length; const calib = tw.odds === 'calibrated';
  const prm = evs.map(e => ({
    id: e.id, inH: e.inH, q: e.qEff, p: calib ? e.phEff : e.qEff, dD: e.dD,
    parents: e.parents.filter(pp => idx.has(pp.id)).map(pp => ({ j: idx.get(pp.id), w: pp.w })), alpha: 0, beta: [],
  }));
  // Logistic link per child; slopes match w to first order, intercept solved so the
  // event's overall probability matches its odds.
  prm.forEach(r => {
    if (!r.inH || !r.parents.length) return;
    const pv = Math.max(r.p * (1 - r.p), 0.02);
    r.beta = r.parents.map(pp => pp.w / pv);
    const k = r.parents.length; const confs = [];
    for (let mask = 0; mask < (1 << k); mask++) {
      let pr = 1, z = 0;
      for (let t = 0; t < k; t++) { const par = prm[r.parents[t].j]; const x = (mask >> t) & 1; pr *= x ? par.p : 1 - par.p; z += r.beta[t] * (x - par.q); }
      confs.push([pr, z]);
    }
    let lo = -30, hi = 30;
    for (let it = 0; it < 60; it++) { const mid = (lo + hi) / 2; const f = confs.reduce((s, [pr, z]) => s + pr * sigmoid(mid + z), 0); if (f > r.p) hi = mid; else lo = mid; }
    r.alpha = (lo + hi) / 2;
  });
  const weeks = tw.horizon * 4.345;
  const noiseSd = tw.noise ? Math.sqrt(P.b.reduce((s, v) => s + v * v, 0)) * Math.sqrt(weeks) : 0;
  const rng = mulberry32(7);
  const pnl = new Float64Array(n); const X = new Float32Array(n * m);
  for (let s = 0; s < n; s++) {
    let tot = 0; const base = s * m;
    for (let i = 0; i < m; i++) {
      const r = prm[i];
      if (r.inH) {
        let p = r.p;
        if (r.parents.length) { let z = r.alpha; for (let t = 0; t < r.parents.length; t++) { const pj = r.parents[t].j; z += r.beta[t] * (X[base + pj] - prm[pj].q); } p = sigmoid(z); }
        const x = rng() < p ? 1 : 0; X[base + i] = x; tot += r.dD * (x - r.q);
      } else {
        // Resolves after the horizon: it does not happen or fail, its odds just move.
        let sh = 0; for (const pp of r.parents) sh += pp.w * (X[base + pp.j] - prm[pp.j].q);
        const qq = clamp(r.q + sh, 0.001, 0.999); X[base + i] = qq; tot += r.dD * (qq - r.q);
      }
    }
    if (noiseSd) tot += noiseSd * gauss(rng);
    pnl[s] = tot;
  }
  const ix = Array.from({ length: n }, (_, i) => i).sort((a, c) => pnl[a] - pnl[c]);
  const at = p => pnl[ix[Math.min(n - 1, Math.max(0, Math.floor(p * (n - 1))))]];
  let mean = 0; for (let s = 0; s < n; s++) mean += pnl[s]; mean /= n;
  let vr = 0; for (let s = 0; s < n; s++) vr += (pnl[s] - mean) * (pnl[s] - mean);
  const sd = Math.sqrt(vr / n);
  const k5 = Math.max(1, Math.floor(0.05 * n));
  let es = 0; for (let i = 0; i < k5; i++) es += pnl[ix[i]]; es /= k5;
  const drivers = [];
  prm.forEach((r, i) => {
    if (!r.inH) return;
    let all = 0, tail = 0;
    for (let s = 0; s < n; s++) all += X[s * m + i];
    for (let t = 0; t < k5; t++) tail += X[ix[t] * m + i];
    drivers.push({ id: r.id, base: all / n, tail: tail / k5, dD: r.dD });
  });
  // Histogram over the central 99.6% of outcomes.
  let lo = at(0.002), hi = at(0.998); if (hi - lo < 1e-6) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.04; lo -= pad; hi += pad;
  const nb = 38; const bins = new Array(nb).fill(0); const bw = (hi - lo) / nb;
  for (let s = 0; s < n; s++) { const bi = Math.floor((pnl[s] - lo) / bw); if (bi >= 0 && bi < nb) bins[bi]++; }
  return { n, mean, sd, es, p5: at(0.05), p50: at(0.5), p95: at(0.95), drivers, hist: { lo, hi, bw, bins } };
}

/* ---------- The 3D map: macro axes and the portfolio's own axes ---------- */
const AXES3 = [
  { name: 'growth', w: [0.5, 0.15, 0, 0, 0.2, -0.35, 0, 0.3, 0, 0], ends: ['Weaker growth', 'Stronger growth'] },
  { name: 'risk', w: [0.25, 0, 0, 0, 0, -0.45, -0.55, 0, 0, -0.3], ends: ['Risk-off', 'Risk-on'] },
  { name: 'inflation', w: [0, 0.4, 0.6, 0, 0.45, 0, 0, 0, 0, 0.15], ends: ['Lower inflation', 'Higher inflation'] },
].map(a => { const nrm = Math.hypot(...a.w); return { ...a, w: a.w.map(v => v / nrm) }; });
const dot = (a, b) => a.reduce((s, v, i) => s + v * b[i], 0);

function powerIter(M, ortho) {
  let v = M.map((_, i) => 1 + 0.1 * i);
  for (let it = 0; it < 80; it++) {
    let w = M.map(row => dot(row, v));
    ortho.forEach(u => { const d = dot(w, u); w = w.map((x, i) => x - d * u[i]); });
    const nm = Math.hypot(...w) || 1; v = w.map(x => x / nm);
  }
  return v;
}
function axisLabel(v) {
  const top = v.map((x, k) => [Math.abs(x), k, x]).sort((a, b) => b[0] - a[0]).slice(0, 2);
  const word = (k, s) => FACTORS[k].short + (s > 0 ? ' up' : ' down');
  return [top.map(([, k, x]) => word(k, -x)).join(', '), top.map(([, k, x]) => word(k, x)).join(', ')];
}
function mapGeometry(evs, P, mode) {
  if (mode === 'mine') {
    const nb = Math.hypot(...P.b) || 1; const u = P.b.map(v => v / nb);
    const M = Array.from({ length: NF }, () => new Array(NF).fill(0));
    const res = {};
    evs.forEach(e => {
      const wgt = e.qEff * (1 - e.qEff) + 0.02; const d0 = dot(e.d, u); const r = e.d.map((v, k) => v - d0 * u[k]); res[e.id] = r;
      for (let i = 0; i < NF; i++) for (let j = 0; j < NF; j++) M[i][j] += wgt * r[i] * r[j];
    });
    let v1 = powerIter(M, [u]); let v2 = powerIter(M, [u, v1]);
    const flip = v => { const k = v.reduce((bi, x, i) => Math.abs(x) > Math.abs(v[bi]) ? i : bi, 0); return v[k] < 0 ? v.map(x => -x) : v; };
    v1 = flip(v1); v2 = flip(v2);
    const raw = {}; evs.forEach(e => { raw[e.id] = [e.dT, dot(res[e.id], v1), dot(res[e.id], v2)]; });
    const mx = [0, 1, 2].map(i => Math.max(0.01, ...evs.map(e => Math.abs(raw[e.id][i]))));
    const pos = {}; evs.forEach(e => { const r = raw[e.id]; pos[e.id] = [r[0] / mx[0], r[1] / mx[1], r[2] / mx[2]]; });
    return { pos, arrow: [1, 0, 0], ends: { x: ['You lose', 'You gain'], y: axisLabel(v1), z: axisLabel(v2) } };
  }
  const raw = {}; evs.forEach(e => { raw[e.id] = AXES3.map(a => dot(a.w, e.d)); });
  const norms = evs.map(e => Math.hypot(...raw[e.id])).sort((a, b) => a - b);
  const ref = Math.max(0.6, norms[Math.floor(norms.length * 0.85)] || 1);
  const pos = {};
  evs.forEach(e => { let p = raw[e.id].map(v => v / ref); const nm = Math.hypot(...p); if (nm > 1.2) p = p.map(v => v * 1.2 / nm); pos[e.id] = p; });
  const a = AXES3.map(ax => dot(ax.w, P.b)); const na = Math.hypot(...a) || 1;
  return { pos, arrow: a.map(v => v / na), ends: { x: AXES3[0].ends, y: AXES3[1].ends, z: AXES3[2].ends } };
}

/* ---------- Odds history: the snapshot's daily closes, or an illustrative bridge in log-odds space ---------- */
function oddsHistory(e, n = 120) {
  if (e.series && e.series.length >= 2) return e.series.concat([e.q]);
  if (!e.hist) return null;
  const rng = mulberry32(hashStr(e.id)); const { h0, y, j = [] } = e.hist;
  const inc = new Array(n - 2).fill(0).map(() => (rng() - 0.5) * 0.18);
  j.forEach(([ago, size]) => { const k = n - 2 - ago; if (k >= 0 && k < inc.length) inc[k] += size; });
  const tot = inc.reduce((a, b) => a + b, 0); const corr = (logit(y) - logit(h0) - tot) / inc.length;
  const out = [h0]; let z = logit(h0);
  for (let i = 0; i < inc.length; i++) { z += inc[i] + corr; out.push(sigmoid(z)); }
  out.push(e.q);
  return out;
}

/* ---------- Historical replay (E8): engine's calls vs what happened ----------
   REPLAY comes in two shapes. The illustrative one has one jump per event (d) and fixed links. The engine's
   (`redkeep replay`) re-estimates each jump every evening (dd: index 0 is the evening before the first day, index
   i + 1 the close of day i), adds the odds at the close before the first day (q0), gives each link the weight the
   data set that evening (w per evening, null when the data didn't confirm it), and has no moves (null) on factors it
   had no prices for. An event's odds q[i] are at the close of day i, null before a market listed it. */
function replayCompute(P, knock) {
  const R = REPLAY; const D = R.days.length;
  const real = R.days.map(day => day.f.reduce((s, f, k) => s + (f == null ? 0 : P.b[k] * f / FACTORS[k].typical), 0));
  // The replay's jumps are in its own typical moves, and a backend snapshot brings others: put the arrow in the replay's.
  const bR = P.b.map((v, k) => (R.typical[k] == null ? 0 : v * R.typical[k] / FACTORS[k].typical));
  const done = (e, i) => e.res != null && i >= e.res;  // resolved by the close of day i
  const qEve = (e, k) => (k === 0 ? (e.q0 == null ? null : e.q0) : done(e, k - 1) || e.q[k - 1] == null ? null : e.q[k - 1]);
  const qAfter = (e, i) => (done(e, i) ? e.out : e.q[i] == null ? null : e.q[i]);  // the close of day i, resolutions included
  const jumpAt = (e, k) => { const d = e.dd ? e.dd[k] : e.d; return d ? dot(bR, d.map(x => (x == null ? 0 : x))) : null; };
  const linksAt = k => (knock ? (R.links || []).map(l => (Array.isArray(l) ? l : [l.p, l.c, l.w[k]])).filter(l => l[2] != null) : []);
  // At evening k: each event's jump on this portfolio, plus the knock-ons through its links to events still open (4.4),
  // along every path that doesn't revisit an event (the backend's total_jump does the same).
  const totals = k => {
    const L = linksAt(k);
    const tot = (id, path) => {
      const e = R.events.find(z => z.id === id); let s = jumpAt(e, k);
      if (s == null) return null;
      const on = new Set(path).add(id);
      L.forEach(([p, c, w]) => {
        if (p !== id || on.has(c)) return;
        const ce = R.events.find(z => z.id === c), qc = ce ? qEve(ce, k) : null;
        if (qc == null || qc >= 1) return;
        const t = tot(c, on); if (t != null) s += w * t;
      });
      return s;
    };
    return id => tot(id, []);
  };
  const states = R.days.map((day, i) => { const tot = totals(i + 1); return R.events.map(e => {
    if (done(e, i)) return { id: e.id, t: e.t, s: e.s, status: 'happened', on: R.days[e.res].d };
    const q = qEve(e, i + 1);
    if (q == null) return { id: e.id, t: e.t, s: e.s, status: 'unlisted' };
    const dT = tot(e.id);
    if (dT == null) return { id: e.id, t: e.t, s: e.s, status: 'open', q, dT: null };
    return { id: e.id, t: e.t, s: e.s, status: 'open', q, dT, ifYes: (1 - q) * dT, ifNo: -q * dT, swing: Math.abs(dT) * Math.sqrt(q * (1 - q)) };
  }); });
  const calls = R.events.filter(e => e.res != null).map(e => {
    const q = qEve(e, e.res), dT = totals(e.res)(e.id);
    const pred = q == null || dT == null ? null : e.out ? (1 - q) * dT : -q * dT;
    const why = q == null ? 'No market priced it the evening before' : dT == null ? 'No reading of its jump yet' : '';
    return { id: e.id, t: e.t, s: e.s, day: e.res, date: R.days[e.res].d, q, dT, pred, real: real[e.res], why };
  });
  // Every day: each event's own jump that evening times its odds change to the next close (Σ δ · Δq, section 10 of the
  // design). Knock-ons stay out, since the odds of the events they move are in the sum themselves. Null on a day when
  // no event had a jump and odds at both closes.
  const expl = R.days.map((day, i) => R.events.reduce((s, e) => {
    const a = qEve(e, i), b = qAfter(e, i), d = jumpAt(e, i);
    return a == null || b == null || d == null ? s : (s || 0) + d * (b - a);
  }, null));
  // How much of this portfolio's daily moves the events explain: 1 − Σ(real − explained)² / Σ real², where a day with
  // nothing to explain it counts as explaining nothing.
  const x = expl.map(v => v || 0), ss = real.reduce((s, r) => s + r * r, 0);
  const fit = { share: ss > 0 ? 1 - real.reduce((s, r, i) => s + (r - x[i]) ** 2, 0) / ss : null, corr: pearson(x, real) };
  const cum = []; let c = 1; real.forEach(r => { c *= 1 + r / 100; cum.push((c - 1) * 100); });
  return { real, cum, states, calls, expl, fit };
}

function pearson(x, y) {
  const n = x.length; if (n < 3) return null;
  const mx = x.reduce((s, v) => s + v, 0) / n, my = y.reduce((s, v) => s + v, 0) / n;
  let sxy = 0, sxx = 0, syy = 0;
  x.forEach((v, i) => { sxy += (v - mx) * (y[i] - my); sxx += (v - mx) ** 2; syy += (y[i] - my) ** 2; });
  return sxx > 0 && syy > 0 ? sxy / Math.sqrt(sxx * syy) : null;
}
