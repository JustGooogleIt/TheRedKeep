/* ==========================================================================
   DATASETS · the illustrative data in data.js, or a backend snapshot.
   `redkeep app` writes live.js (one RK_LIVE constant) from a library snapshot,
   and any portfolio analyses, in the shapes data.js uses; the build inlines it
   when it exists. The model and the UI read data.js's globals, so switching
   replaces their contents in place and the app remounts.

   A snapshot exported without analyses brings its events only. The app then
   keeps its illustrative portfolios, rescaled to the snapshot's typical moves,
   and labels their exposures as illustrative.
   ========================================================================== */
const HAS_LIVE = typeof RK_LIVE !== 'undefined' && !!RK_LIVE && Array.isArray(RK_LIVE.events) && RK_LIVE.events.length > 0;
// Whether the snapshot brings the engine's own portfolios, prices and exposures, or events only.
const LIVE_PORTFOLIOS = HAS_LIVE && RK_LIVE.exposures !== 'illustrative' && Array.isArray(RK_LIVE.samples) && RK_LIVE.samples.length > 0;
const DEMO = {
  today: TODAY, factors: FACTORS.map(f => ({ typical: f.typical, proxy: f.proxy })),
  tickers: { ...TICKERS }, names: { ...NAMES }, samples: SAMPLES.slice(), events: EVENTS.slice(), links: LINKS.slice(),
  replay: { ...REPLAY },
};
// The engine's replay of a past episode (`redkeep replay --write`, then `redkeep app`), when the snapshot carries one.
const LIVE_REPLAY = HAS_LIVE && RK_LIVE.replay && Array.isArray(RK_LIVE.replay.days) && RK_LIVE.replay.days.length > 0
  ? RK_LIVE.replay : null;
let REPLAY_SOURCE = 'demo'; // 'engine' when the replay screen shows the engine's replay on archived prices
let DATASET = 'demo';    // where the events come from: 'demo' or 'live'
let EXPOSURES = 'demo';  // where the portfolios, prices and exposures come from
let UNMEASURED = [];     // factors the snapshot has no price data on, so no typical move of their own
let WEEKENDS = null;     // the snapshot's Hyperliquid weekend windows, when it ran them
let PERP_FACTORS = [];   // keys of the factors those windows measured
let MEASURED_FIT = {};   // the engine's out-of-sample fit for each portfolio it analyzed, by holdingsKey (model.js)

function refill(target, src) {
  if (Array.isArray(target)) { target.length = 0; src.forEach(x => target.push(x)); return; }
  Object.keys(target).forEach(k => { delete target[k]; });
  Object.assign(target, src);
}

function applyDataset(id) {
  const L = id === 'live' && HAS_LIVE ? RK_LIVE : null;
  const LP = L && LIVE_PORTFOLIOS ? L : null;
  TODAY = L ? L.today : DEMO.today;
  UNMEASURED = [];
  WEEKENDS = (L && L.windows && L.windows.weekends) || null;
  PERP_FACTORS = WEEKENDS && WEEKENDS.readings > 0 ? Object.keys(WEEKENDS.used || {}) : [];
  FACTORS.forEach((f, i) => {
    const lf = L && L.factors.find(x => x.k === f.k);
    const measured = !!lf && lf.typical > 0;
    if (L && !measured) UNMEASURED.push(f);
    f.typical = measured ? lf.typical : DEMO.factors[i].typical;
    f.proxy = measured ? lf.proxy : DEMO.factors[i].proxy;
  });
  refill(TICKERS, LP ? LP.tickers : L ? rescaled(DEMO.tickers) : DEMO.tickers);
  refill(NAMES, LP ? { ...DEMO.names, ...LP.names } : DEMO.names);
  refill(SAMPLES, LP ? LP.samples : DEMO.samples);
  MEASURED_FIT = {};
  if (LP) (LP.results || []).forEach(r => {
    const s = LP.samples.find(x => x.id === r.sample);
    if (s && r.fit != null) MEASURED_FIT[holdingsKey(s.h.map(([t, u, a]) => ({ t, u, a })))] = r.fit;
  });
  refill(EVENTS, L ? L.events : DEMO.events);
  refill(LINKS, L ? L.links : DEMO.links);
  refill(REPLAY, L && LIVE_REPLAY ? LIVE_REPLAY : DEMO.replay);
  REPLAY_SOURCE = L && LIVE_REPLAY ? 'engine' : 'demo';
  DATASET = L ? 'live' : 'demo';
  EXPOSURES = LP ? 'live' : 'demo';
  CALIBRATION = L && L.calibration && L.calibration.markets > 0 ? L.calibration : null;
  return DATASET;
}

/* The illustrative exposures are per typical move of the prototype's factors, and a snapshot's jumps are in the typical
   moves it measured. Rescaling keeps each exposure's size per unit of its factor (per 1% on the S&P 500, per basis
   point on the 10-year yield): the step the engine takes when a snapshot's typical moves differ from today's. */
function rescaled(tickers) {
  const r = FACTORS.map((f, k) => f.typical / DEMO.factors[k].typical);
  return Object.fromEntries(Object.entries(tickers).map(([t, v]) => [t, { ...v, b: v.b.map((x, k) => x * r[k]) }]));
}

/* "2026-11-08T14:05:00+00:00" -> "Nov 8, 2026, 14:05 UTC" */
function fmtStamp(iso) {
  const t = new Date(iso); if (isNaN(t)) return iso;
  const p = n => String(n).padStart(2, '0');
  return MONTHS[t.getUTCMonth()] + ' ' + t.getUTCDate() + ', ' + t.getUTCFullYear() + ', ' + p(t.getUTCHours()) + ':' + p(t.getUTCMinutes()) + ' UTC';
}

/* Calibrated odds. A snapshot carries the backend's study of resolved markets (`redkeep calibrate`) once one has run;
   before that its calibrated odds equal market odds. The illustrative data shades long shots by hand. */
let CALIBRATION = null;
/* What Tweaks says calibrated odds do. The illustrative data shades long shots by hand; a snapshot's odds move only
   where its study found a slope that beat raw prices reliably on later markets. */
function oddsTweak() {
  if (DATASET !== 'live') return 'Calibrated shades long shots down';
  if (!CALIBRATION) return 'No study of resolved markets yet, so both match';
  return Object.values(CALIBRATION.slopes || {}).some(b => Math.abs(b - 1) > 1e-6) ? 'Calibrated shades long shots down'
    : 'The latest study found no reliable long-shot bias, so both match';
}
function calibrationNote() {
  if (!CALIBRATION) return 'Long shots tend to be overpriced on prediction markets, so calibrated odds (in Tweaks) shade them down.';
  return 'That is about how often prices like this one, this far from the deadline, came true across ' + CALIBRATION.markets.toLocaleString('en-US') +
    ' resolved Polymarket and Kalshi markets (study of ' + fmtDate(CALIBRATION.fitted) + '). Tweaks can switch to calibrated odds.';
}
