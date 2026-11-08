/* ==========================================================================
   DATASETS · the illustrative data in data.js, or a backend snapshot.
   `redkeep app` writes live.js (one RK_LIVE constant) from a library snapshot
   and portfolio analyses, in the shapes data.js uses; the build inlines it
   when it exists. The model and the UI read data.js's globals, so switching
   replaces their contents in place and the app remounts.
   ========================================================================== */
const HAS_LIVE = typeof RK_LIVE !== 'undefined' && !!RK_LIVE && Array.isArray(RK_LIVE.events) && RK_LIVE.events.length > 0;
const DEMO = {
  today: TODAY, factors: FACTORS.map(f => ({ typical: f.typical, proxy: f.proxy })),
  tickers: { ...TICKERS }, names: { ...NAMES }, samples: SAMPLES.slice(), events: EVENTS.slice(), links: LINKS.slice(),
};
let DATASET = 'demo';

function refill(target, src) {
  if (Array.isArray(target)) { target.length = 0; src.forEach(x => target.push(x)); return; }
  Object.keys(target).forEach(k => { delete target[k]; });
  Object.assign(target, src);
}

function applyDataset(id) {
  const L = id === 'live' && HAS_LIVE ? RK_LIVE : null;
  TODAY = L ? L.today : DEMO.today;
  FACTORS.forEach((f, i) => {
    const lf = L && L.factors.find(x => x.k === f.k);
    f.typical = lf && lf.typical > 0 ? lf.typical : DEMO.factors[i].typical;
    f.proxy = lf ? lf.proxy : DEMO.factors[i].proxy;
  });
  refill(TICKERS, L ? L.tickers : DEMO.tickers);
  refill(NAMES, L ? { ...DEMO.names, ...L.names } : DEMO.names);
  refill(SAMPLES, L ? L.samples : DEMO.samples);
  refill(EVENTS, L ? L.events : DEMO.events);
  refill(LINKS, L ? L.links : DEMO.links);
  DATASET = L ? 'live' : 'demo';
  return DATASET;
}

/* "2026-11-08T14:05:00+00:00" -> "Nov 8, 2026, 14:05 UTC" */
function fmtStamp(iso) {
  const t = new Date(iso); if (isNaN(t)) return iso;
  const p = n => String(n).padStart(2, '0');
  return MONTHS[t.getUTCMonth()] + ' ' + t.getUTCDate() + ', ' + t.getUTCFullYear() + ', ' + p(t.getUTCHours()) + ':' + p(t.getUTCMinutes()) + ' UTC';
}
