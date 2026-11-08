/* ==========================================================================
   UI · React 18 + htm, no build step. Part 1: helpers, banner, nav, hero,
   portfolio panel.
   ========================================================================== */
const { useState, useEffect, useMemo, useRef, useCallback } = window.React || {};
const html = window.htm && window.React ? htm.bind(React.createElement) : null;
const Frag = window.React ? React.Fragment : null;

/* ---------- formatting ---------- */
const sgn = (v, plus = true) => (v < 0 ? MINUS : (plus && v > 0 ? '+' : ''));
function fmtPct(v, d = 1, plus = true) {
  if (v == null || !isFinite(v)) return '–';
  const r = +v.toFixed(d);
  return (r === 0 ? '' : sgn(r, plus)) + Math.abs(r).toFixed(d) + '%';
}
function compactNum(a) {
  if (a >= 1e9) return (a / 1e9).toFixed(2).replace(/\.?0+$/, '') + 'B';
  if (a >= 1e7) return (a / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (a >= 1e6) return (a / 1e6).toFixed(2).replace(/\.?0+$/, '') + 'M';
  if (a >= 1e5) return Math.round(a / 1e3) + 'K';
  if (a >= 1e3) return (a / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
  return String(Math.round(a));
}
function fmtUsd(v, plus = false) {
  if (v == null || !isFinite(v)) return '–';
  if (Math.abs(v) < 0.5) return '$0';
  return sgn(v, plus) + '$' + compactNum(Math.abs(v));
}
const fmtUsdFull = v => sgn(v, false) + '$' + Math.round(Math.abs(v)).toLocaleString('en-US');
const fmtOdds = q => (q < 0.01 ? '<1%' : q > 0.99 ? '>99%' : Math.round(q * 100) + '%');
const fmtPts = dq => { const p = Math.round(dq * 100); return (p === 0 ? '' : sgn(p)) + Math.abs(p) + (Math.abs(p) === 1 ? ' pt' : ' pts'); };
const swingPct = v => '±' + Math.abs(v).toFixed(1) + '%';
const swingUsd = v => '±$' + compactNum(Math.abs(v));
const pct0 = v => Math.round(v * 100) + '%';
const cls = (...a) => a.filter(Boolean).join(' ');
const tone = v => (v < -1e-9 ? 'neg' : v > 1e-9 ? 'pos' : '');
function horizonEnd(months) {
  const [y, m, d] = TODAY.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1 + months, d));
  return MONTHS[dt.getUTCMonth()] + ' ' + dt.getUTCDate() + ', ' + dt.getUTCFullYear();
}
function niceTicks(lo, hi, n = 5) {
  const span = hi - lo; if (!(span > 0)) return [lo];
  const step0 = span / n; const mag = Math.pow(10, Math.floor(Math.log10(step0))); const r = step0 / mag;
  const step = (r >= 5 ? 10 : r >= 2 ? 5 : 2) * mag;
  const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}
function parseAmt(s) {
  const m = String(s == null ? '' : s).trim().toLowerCase().replace(/[$,\s]/g, '').match(/^(\d*\.?\d+)([km]?)$/);
  if (!m) return NaN;
  return parseFloat(m[1]) * (m[2] === 'k' ? 1e3 : m[2] === 'm' ? 1e6 : 1);
}
const fmtAmt = h => (h.u === 'usd' ? Math.round(h.a).toLocaleString('en-US') : h.a.toLocaleString('en-US', { maximumFractionDigits: 3 }));
const sampleToHoldings = s => s.h.map(([t, u, a], i) => ({ id: t + i, t, u, a }));

/* ---------- storage, theme, Claude ---------- */
const LS_KEY = 'redkeep.v2';
function loadSaved() { try { const s = localStorage.getItem(LS_KEY); return s ? JSON.parse(s) : null; } catch (e) { return null; } }
function saveState(o) { try { localStorage.setItem(LS_KEY, JSON.stringify(o)); } catch (e) { /* storage unavailable: settings last for this visit only */ } }
const HOST_THEME = document.documentElement.getAttribute('data-theme');
const REDUCED = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

let samplePromise = null;
function getSample() {
  if (!samplePromise) {
    const c = window.claude;
    samplePromise = c && typeof c.use === 'function' ? Promise.resolve(c.use('sample')).catch(() => null) : Promise.resolve(null);
  }
  return samplePromise;
}

const colorCache = new Map();
function toRgb(c) {
  if (colorCache.has(c)) return colorCache.get(c);
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const x = cv.getContext('2d'); x.fillStyle = '#000'; x.fillStyle = c; x.fillRect(0, 0, 1, 1);
  const d = x.getImageData(0, 0, 1, 1).data; const r = [d[0], d[1], d[2]];
  colorCache.set(c, r); return r;
}
const rgba = (c, a) => { const [r, g, b] = toRgb(c); return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')'; };

// Charts draw at their real width so axis text stays at its set size instead of scaling with the box.
function useWidth(ref, fallback) {
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current; if (!el || typeof ResizeObserver !== 'function') return undefined;
    const ro = new ResizeObserver(es => { const cw = Math.round(es[0].contentRect.width); if (cw > 0) setW(cw); });
    ro.observe(el); return () => ro.disconnect();
  }, []);
  return w;
}

/* ---------- brand mark: a pixel keep ---------- */
const KEEP_BITS = ['....#.#.#....', '....#####....', '#.#.#####.#.#', '#############', '##.###.###.##', '#############', '#####...#####', '####.....####', '####.....####'];
const KEEP_PATH = KEEP_BITS.flatMap((row, y) => [...row].map((c, x) => (c === '#' ? 'M' + x + ' ' + y + 'h1v1h-1z' : ''))).join('');
function KeepMark({ size = 22 }) {
  return html`<svg className="keep" width=${Math.round(size * 13 / 9)} height=${size} viewBox="0 0 13 9" shapeRendering="crispEdges" aria-hidden="true"><path d=${KEEP_PATH} /></svg>`;
}

/* ---------- banner, floating glass nav, hero, intro ---------- */
function Banner() {
  return html`<div className="banner"><span className="banner-dot" aria-hidden="true"></span>Illustrative data · Snapshot ${fmtDate(TODAY)}<span className="banner-x"> · An independent take-home for Castle</span></div>`;
}

// True once the element's bottom edge has scrolled up past `offset` pixels from the top.
function useScrolledPast(ref, offset, enabled) {
  const [past, setPast] = useState(false);
  useEffect(() => {
    if (!enabled) return undefined;
    let raf = 0;
    const check = () => { raf = 0; const el = ref.current; if (el) setPast(el.getBoundingClientRect().bottom <= offset); };
    const on = () => { if (!raf) raf = requestAnimationFrame(check); };
    check();
    window.addEventListener('scroll', on, { passive: true }); window.addEventListener('resize', on);
    return () => { cancelAnimationFrame(raf); window.removeEventListener('scroll', on); window.removeEventListener('resize', on); };
  }, [enabled, offset]);
  return past;
}

function Nav({ tab, goTab, live, setLive, openTweaks, solid }) {
  const T = (k, long, short) => html`<button role="tab" aria-selected=${tab === k} className=${cls('tab', tab === k && 'on')} onClick=${() => goTab(k)}><span className="t-long">${long}</span><span className="t-short">${short}</span></button>`;
  return html`<header className=${cls('nav', solid && 'scrolled')}><div className="nav-in">
    <a className="brand" href="#" aria-label="The Red Keep, back to top" onClick=${e => { e.preventDefault(); window.scrollTo({ top: 0, behavior: REDUCED ? 'auto' : 'smooth' }); }}><${KeepMark} size=${20} /><span className="brand-name">The Red Keep</span></a>
    <div className="tabs" role="tablist" aria-label="Views">${T('analysis', 'Live analysis', 'Analysis')}${T('replay', 'Historical replay', 'Replay')}</div>
    <div className="nav-right">
      <button className=${cls('nav-live', live && 'on')} onClick=${() => setLive(!live)} aria-pressed=${live} title="Simulated odds changes, priced through your portfolio as they happen"><i className="dot-live"></i>${live ? 'Live odds on' : 'Simulate live odds'}</button>
      <button className="btn btn-light btn-sm" onClick=${openTweaks}>Tweaks</button>
    </div>
  </div></header>`;
}

function Hero({ tw, goTab, heroRef }) {
  return html`<section className="hero" ref=${heroRef} aria-labelledby="hero-h">
    <img className="hero-img" src="hero.webp" srcSet="hero-960.webp 960w, hero.webp 1672w" sizes="100vw" alt="A red dragon breathes fire onto a walled castle on a sea cliff" />
    <div className="hero-shade" aria-hidden="true"></div>
    <div className="hero-copy">
      <span className="hero-eyebrow">Scenario Analysis Engine · Ashwin’s take-home for Castle</span>
      <h1 className="hero-h" id="hero-h">Hedging against dragons.</h1>
      <p className="hero-sub">Find the events that could move your portfolio in the next ${tw.horizon} months, and see how much of each one the market has already priced in.</p>
      <div className="hero-cta">
        <button className="btn btn-light btn-lg" onClick=${() => goTab('analysis')}>Explore the engine</button>
        <button className="btn btn-outline-light btn-lg" onClick=${() => goTab('replay')}>Replay April 2025</button>
      </div>
    </div>
  </section>`;
}

const INTRO = {
  analysis: ['What could move ', 'your portfolio', 'Start from a famous portfolio or build your own. Everything recomputes as you edit: what you actually own, which way it leans, the events ahead and thousands of simulated futures.'],
  replay: ['Check the engine ', 'against history', 'Replay the April 2025 tariff shock one trading day at a time. Each evening the engine sees only the odds known at that close, and you can change the portfolio while it plays.'],
};
function Intro({ tab }) {
  const [a, b, sub] = INTRO[tab] || INTRO.analysis;
  return html`<header className="intro"><h2 className="heading-xl">${a}<span className="accent">${b}</span></h2><p className="intro-sub">${sub}</p></header>`;
}

/* ---------- portfolio panel ---------- */
function SampleChips({ sampleId, onPick }) {
  return html`<div className="samples" role="group" aria-label="Sample portfolios">
    ${SAMPLES.map(s => html`<button key=${s.id} className=${cls('sample', sampleId === s.id && 'on')} aria-pressed=${sampleId === s.id} onClick=${() => onPick(s.id)}><b>${s.name}</b><span>${s.who}</span></button>`)}
  </div>`;
}

function HoldingRow({ h, row, onChange, onRemove }) {
  const t = TICKERS[h.t];
  const [txt, setTxt] = useState(null);
  const toggle = () => {
    const v = holdingValue(h);
    onChange(h.u === 'usd' ? { ...h, u: 'sh', a: +(v / t.px).toFixed(3) } : { ...h, u: 'usd', a: Math.round(v) });
  };
  return html`<div className="hold">
    <div className="hold-tk"><span className="tk">${h.t}</span><span className="tk-name" title=${t.n}>${t.n}</span></div>
    <div className="amt-box">
      <input inputMode="decimal" aria-label=${h.t + (h.u === 'usd' ? ' dollar amount' : ' shares')} value=${txt != null ? txt : fmtAmt(h)}
        onFocus=${e => { const el = e.target; setTxt(String(h.a)); requestAnimationFrame(() => el.select()); }}
        onChange=${e => { setTxt(e.target.value); const n = parseAmt(e.target.value); if (n >= 0) onChange({ ...h, a: n }); }}
        onBlur=${() => setTxt(null)}
        onKeyDown=${e => { if (e.key === 'Enter') e.target.blur(); }} />
      <button className="unit" onClick=${toggle} title=${h.u === 'usd' ? 'Switch to shares' : 'Switch to dollars'} aria-label=${h.u === 'usd' ? 'In dollars. Switch to shares' : 'In shares. Switch to dollars'}>${h.u === 'usd' ? '$' : 'sh'}</button>
    </div>
    <div className="hold-v"><b>${fmtUsd(row ? row.v : 0)}</b><span>${row ? (row.w * 100).toFixed(1) + '%' : '–'}</span></div>
    <button className="x" onClick=${onRemove} aria-label=${'Remove ' + h.t}>×</button>
  </div>`;
}

function AddHolding({ onAdd }) {
  const [q, setQ] = useState(''); const [amt, setAmt] = useState(''); const [unit, setUnit] = useState('usd');
  const [open, setOpen] = useState(false); const [hi, setHi] = useState(0); const [msg, setMsg] = useState('');
  const amtRef = useRef(null);
  const list = useMemo(() => {
    const s = q.trim().toUpperCase(); if (!s) return [];
    const all = Object.keys(TICKERS);
    const a = all.filter(k => k.startsWith(s));
    const b = all.filter(k => !k.startsWith(s) && TICKERS[k].n.toUpperCase().includes(s));
    return a.concat(b).slice(0, 6);
  }, [q]);
  const pick = k => { setQ(k); setOpen(false); setMsg(''); setTimeout(() => amtRef.current && amtRef.current.focus(), 0); };
  const submit = e => {
    if (e) e.preventDefault();
    const k = q.trim().toUpperCase();
    if (!TICKERS[k]) { setMsg(k ? k + ' isn’t in this prototype’s universe yet. The list below shows what is.' : 'Type a ticker first.'); return; }
    const n = parseAmt(amt);
    if (!(n > 0)) { setMsg('Enter how many ' + (unit === 'usd' ? 'dollars' : 'shares') + ' of ' + k + '.'); if (amtRef.current) amtRef.current.focus(); return; }
    onAdd({ t: k, u: unit, a: n }); setQ(''); setAmt(''); setMsg(''); setOpen(false);
  };
  const onKey = e => {
    const showing = open && list.length > 0;
    if (e.key === 'ArrowDown' && showing) { e.preventDefault(); setHi(i => (i + 1) % list.length); }
    else if (e.key === 'ArrowUp' && showing) { e.preventDefault(); setHi(i => (i - 1 + list.length) % list.length); }
    else if (e.key === 'Enter') { e.preventDefault(); if (showing) pick(list[Math.min(hi, list.length - 1)]); else if (TICKERS[q.trim().toUpperCase()]) pick(q.trim().toUpperCase()); else submit(); }
    else if (e.key === 'Escape') setOpen(false);
  };
  return html`<form className="add" onSubmit=${submit} autoComplete="off">
    <div className="ac">
      <label className="sr" htmlFor="add-tk">Ticker</label>
      <input id="add-tk" className="in" placeholder="Add a ticker, e.g. SPY or NVDA" value=${q} role="combobox" aria-expanded=${open && list.length > 0} aria-controls="add-list" aria-autocomplete="list"
        onChange=${e => { setQ(e.target.value); setOpen(true); setHi(0); setMsg(''); }} onFocus=${() => setOpen(true)} onBlur=${() => setTimeout(() => setOpen(false), 120)} onKeyDown=${onKey} />
      ${open && list.length > 0 && html`<ul className="ac-list" id="add-list" role="listbox">
        ${list.map((k, i) => html`<li key=${k} role="option" aria-selected=${i === hi} className=${i === hi ? 'on' : ''} onMouseDown=${e => { e.preventDefault(); pick(k); }} onMouseEnter=${() => setHi(i)}><span className="tk">${k}</span><span className="ac-name">${TICKERS[k].n}</span><span className="ac-px mono">$${TICKERS[k].px.toFixed(2)}</span></li>`)}
      </ul>`}
    </div>
    <div className="add-row2">
      <input ref=${amtRef} className="in mono" inputMode="decimal" placeholder=${unit === 'usd' ? 'Amount, e.g. 25k' : 'Shares'} value=${amt} aria-label="Amount" onChange=${e => { setAmt(e.target.value); setMsg(''); }} />
      <div className="seg" role="group" aria-label="Units">
        <button type="button" className=${unit === 'usd' ? 'on' : ''} aria-pressed=${unit === 'usd'} onClick=${() => setUnit('usd')}>$</button>
        <button type="button" className=${unit === 'sh' ? 'on' : ''} aria-pressed=${unit === 'sh'} onClick=${() => setUnit('sh')}>Shares</button>
      </div>
      <button className="btn" type="submit">Add</button>
    </div>
    ${msg && html`<p className="hint" role="status">${msg}</p>`}
  </form>`;
}

function PortfolioCard({ holdings, P, sampleId, onPick, onEdit }) {
  const sample = SAMPLES.find(s => s.id === sampleId);
  const rowById = useMemo(() => Object.fromEntries(P.rows.map(r => [r.id, r])), [P]);
  const change = (i, h) => { const next = holdings.slice(); next[i] = h; onEdit(next); };
  const remove = i => onEdit(holdings.filter((_, j) => j !== i));
  const add = h => {
    const i = holdings.findIndex(x => x.t === h.t);
    if (i < 0) { onEdit(holdings.concat([{ ...h, id: h.t + Date.now() }])); return; }
    const cur = holdings[i]; const px = TICKERS[h.t].px;
    const addA = cur.u === h.u ? h.a : cur.u === 'usd' ? h.a * px : h.a / px;
    change(i, { ...cur, a: +(cur.a + addA).toFixed(3) });
  };
  return html`<section className="card" aria-labelledby="pf-h">
    <div className="side-head">
      <div><span className="kicker">Your portfolio</span><h2 className="h2" id="pf-h" style=${{ marginTop: 6 }}>Holdings</h2></div>
      <div className="side-total"><span className="label">Total</span><span className="num-lg">${fmtUsdFull(P.V)}</span></div>
    </div>
    <${SampleChips} sampleId=${sampleId} onPick=${onPick} />
    ${sample && html`<p className="sample-note">${sample.note}</p>`}
    ${holdings.length > 0 && html`<div className="holds">${holdings.map((h, i) => html`<${HoldingRow} key=${h.id} h=${h} row=${rowById[h.id]} onChange=${x => change(i, x)} onRemove=${() => remove(i)} />`)}</div>`}
    <${AddHolding} onAdd=${add} />
    <details className="universe"><summary>${Object.keys(TICKERS).length} tickers in this prototype</summary>
      <p>${Object.keys(TICKERS).join(' ')}</p>
      <p style=${{ fontFamily: 'var(--f-body)', wordSpacing: 0, lineHeight: 1.5 }}>The real system covers anything with holdings data from Massive.</p>
    </details>
  </section>`;
}

const ltTicker = o => (o.kind === 'stock' ? o.key : o.kind === 'line' ? o.key.slice(5) : '');
const ltName = o => (o.kind === 'rest' ? 'Rest of ' + o.key.slice(5) + (TICKERS[o.key.slice(5)].rest ? ' · ' + TICKERS[o.key.slice(5)].rest : '') : o.name);
function ltVia(o) {
  if (o.kind === 'line') return 'Held as one line: nothing to look through';
  if (o.kind === 'rest') return 'Smaller holdings, grouped';
  if (o.via.length === 1 && o.via[0][0] === 'direct') return 'Held directly';
  return o.via.map(([s, w]) => (s === 'direct' ? 'direct ' : 'via ' + s + ' ') + (w * 100).toFixed(1) + '%').join(' · ');
}

function LookThrough({ P }) {
  const [all, setAll] = useState(false);
  const items = P.look.filter(o => o.w >= 0.0005);
  const shown = all ? items : items.slice(0, 8);
  const mx = items.length ? items[0].w : 1;
  const lines = items.filter(o => o.kind === 'line').length;
  return html`<section className="card" aria-labelledby="lt-h">
    <div className="card-head"><div><span className="kicker">Step 1 · Look through</span><h3 className="h3" id="lt-h" style=${{ marginTop: 6 }}>What you actually own</h3>
      <p className="sub">${P.rows.length} holding${P.rows.length === 1 ? '' : 's'} become ${P.nUnderlying} named companies${lines ? ', ' + lines + ' other line' + (lines === 1 ? '' : 's') : ''} and each fund’s long tail. A company held through several funds is added up.</p></div></div>
    <ul className="lt-list">${shown.map(o => html`<li key=${o.key}>
      <div className="lt-top"><span className="tk">${ltTicker(o)}</span><span className="lt-name">${ltName(o)}</span>${o.kind === 'stock' && o.w >= 0.05 && html`<span className="pill pill-acc" title="At 5% or more, the engine also tracks this company’s own event risk">Own line</span>`}<span className="lt-w mono">${(o.w * 100).toFixed(1)}%</span></div>
      <div className="bar"><i style=${{ width: (o.w / mx * 100).toFixed(1) + '%' }}></i></div>
      <div className="lt-via mono">${ltVia(o)}</div></li>`)}
    </ul>
    ${items.length > 8 && html`<button className="link" style=${{ marginTop: 12 }} onClick=${() => setAll(!all)}>${all ? 'Show fewer' : 'Show all ' + items.length}</button>`}
  </section>`;
}

function Exposure({ P }) {
  const rows = FACTORS.map((f, k) => ({ key: f.k, name: f.name, v: P.b[k], f })).concat(P.extras.map(e => ({ key: e.k, name: e.name, v: e.v, f: e, extra: true })));
  const mx = Math.max(1, ...rows.map(r => Math.abs(r.v)));
  const typ = f => f.typical + (f.unit === '%' ? '%' : ' ' + f.unit);
  return html`<section className="card" aria-labelledby="ex-h">
    <div className="card-head"><div><span className="kicker">Step 2 · Exposure</span><h3 className="h3" id="ex-h" style=${{ marginTop: 6 }}>Which way it leans</h3>
      <p className="sub">How much the portfolio moves when each factor has a typical day. Hover a row for its scale.</p></div></div>
    <div>${rows.map(r => { const w = Math.abs(r.v) / mx * 50; return html`<div key=${r.key} className="exp-row" title=${r.name + ': a typical move (' + typ(r.f) + ') moves the portfolio ' + fmtPct(r.v, 2)}>
      <span className="exp-name">${r.name}${r.extra && html` <span className="pill pill-acc">added</span>`}</span>
      <span className="exp-bar"><i className=${r.v < 0 ? 'neg' : ''} style=${{ left: (r.v < 0 ? 50 - w : 50) + '%', width: (Math.abs(r.v) >= 0.005 ? Math.max(w, 0.8) : 0) + '%' }}></i></span>
      <span className=${cls('exp-v mono', Math.abs(r.v) < 0.005 && 'faint')}>${fmtPct(r.v, 2)}</span></div>`; })}</div>
    ${P.fit != null && html`<div className="fit"><span className="fit-v">${Math.round(P.fit * 100)}%</span><p>of daily moves these factors explain, tested out of sample.${P.extras.length ? ' Adding ' + P.extras.map(e => e.name.toLowerCase()).join(' and ') + ' lifted it from ' + Math.round(P.fitCore * 100) + '%.' : ' No extra factors needed.'}</p></div>`}
  </section>`;
}
