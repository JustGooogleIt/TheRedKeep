/* ==========================================================================
   UI · Part 3: the event card, historical replay, tweaks, and the app shell.
   ========================================================================== */
const REL = { same: 'Same event', sum: 'Sum of outcomes', implies: 'Stricter market · lower bound', ladder: 'Ladder', bound: 'Bounded by related markets' };

function SplitBar({ dT, q }) {
  const ref = useRef(null);
  const W = Math.max(280, useWidth(ref, 520)), a = 8, b = W - 8, x1 = a + q * (b - a);
  return html`<div className="split" ref=${ref}><svg viewBox=${'0 0 ' + W + ' 62'} role="img" aria-label=${'Of the full ' + fmtPct(dT) + ' move, ' + fmtPct(q * dT) + ' is already priced in and ' + fmtPct((1 - q) * dT) + ' would still be a surprise.'}>
    <defs><pattern id="rk-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="2" height="5" style=${{ fill: 'var(--ink-3)', opacity: 0.6 }} /></pattern></defs>
    <text x=${a} y="11" className="ax">Full move if it happens · ${fmtPct(dT)}</text>
    <rect x=${a} y="20" width=${Math.max(0, x1 - a - 1)} height="16" rx="2" style=${{ fill: 'url(#rk-hatch)' }} />
    <rect x=${x1 + 1} y="20" width=${Math.max(0, b - x1 - 1)} height="16" rx="2" style=${{ fill: dT < 0 ? 'var(--loss)' : 'var(--gain)' }} />
    <text x=${a} y="54" className="ax">Priced in at ${fmtOdds(q)} · ${fmtPct(q * dT)}</text>
    <text x=${b} y="54" textAnchor="end" className="ax">Surprise if it happens · ${fmtPct((1 - q) * dT)}</text>
  </svg></div>`;
}

function Spark({ h, now, from }) {
  const ref = useRef(null);
  const W = Math.max(280, useWidth(ref, 520)), H = 72, n = h.length; const lo = Math.min(...h, now), hi = Math.max(...h, now); const pad = Math.max(0.01, (hi - lo) * 0.15);
  const X = i => 6 + i / (n - 1) * (W - 12); const Y = v => 6 + (1 - (v - lo + pad) / (hi - lo + 2 * pad)) * (H - 28);
  const d = h.map((v, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ',' + Y(i === n - 1 ? now : v).toFixed(1)).join('');
  return html`<div className="spark" ref=${ref}><svg viewBox=${'0 0 ' + W + ' ' + H} role="img" aria-label=${'Odds from ' + (from || '120 days ago') + ', ' + fmtOdds(h[0]) + ', to today, ' + fmtOdds(now)}>
    <path d=${d} style=${{ fill: 'none', stroke: 'var(--ink-2)', strokeWidth: 1.5, strokeLinejoin: 'round' }} />
    <circle cx=${X(n - 2)} cy=${Y(h[n - 2])} r="2.5" style=${{ fill: 'var(--ink-3)' }} />
    <circle cx=${X(n - 1)} cy=${Y(now)} r="3.5" style=${{ fill: 'var(--accent)' }} />
    <text x="6" y=${H - 4} className="ax">${from || '120 days ago'} · ${fmtOdds(h[0])}</text>
    <text x=${W - 6} y=${H - 4} textAnchor="end" className="ax">Today · ${fmtOdds(now)}</text>
  </svg></div>`;
}

function ErrBar({ k, s, market }) {
  const X = v => 6 + clamp(v / 2, 0, 1) * 188;
  return html`<svg viewBox="0 0 200 22" aria-hidden="true">
    <line x1="6" x2="194" y1="11" y2="11" style=${{ stroke: 'var(--line)' }} />
    <line x1=${X(1)} x2=${X(1)} y1="1" y2="21" style=${{ stroke: 'var(--accent)', strokeDasharray: '2 2' }} />
    <line x1=${X(k - s)} x2=${X(k + s)} y1="11" y2="11" style=${{ stroke: 'var(--ink-2)', strokeWidth: 2 }} />
    <line x1=${X(k - s)} x2=${X(k - s)} y1="7" y2="15" style=${{ stroke: 'var(--ink-2)', strokeWidth: 1.5 }} />
    <line x1=${X(k + s)} x2=${X(k + s)} y1="7" y2="15" style=${{ stroke: 'var(--ink-2)', strokeWidth: 1.5 }} />
    <circle cx=${X(k)} cy="11" r="4" style=${{ fill: market ? 'var(--ink)' : 'var(--surface)', stroke: 'var(--ink)', strokeWidth: 1.5 }} />
  </svg>`;
}

function verdictText(f) {
  if (f.nMarket >= 2 && f.agree) return html`<span className="mk ok">✓</span><p><b>Market readings agree.</b> χ² ${f.chi2.toFixed(2)} is under the 5% cutoff of ${f.crit}, so the fused jump stands.</p>`;
  if (f.nMarket >= 2) return html`<span className="mk bad">!</span><p><b>Market readings disagree.</b> χ² ${f.chi2.toFixed(1)} is over the 5% cutoff of ${f.crit}. The engine shows the event as contested instead of averaging the gap away.</p>`;
  if (f.nMarket === 1) return html`<span className="mk mid">≈</span><p><b>One market reading.</b> Nothing to cross-check it against yet, so the jump is provisional.</p>`;
  return html`<span className="mk mid">≈</span><p><b>No market readings.</b> The jump comes from past events and the AI prior only.</p>`;
}

function Explain({ e, P, q, ifYes, ifNo }) {
  const [st, setSt] = useState({ s: 'idle', text: '' });
  const ctl = useRef(null);
  useEffect(() => { if (ctl.current) ctl.current.abort(); setSt({ s: 'idle', text: '' }); }, [e.id]);
  useEffect(() => () => { if (ctl.current) ctl.current.abort(); }, []);
  const go = async () => {
    const sample = await getSample(); if (!sample) { setSt({ s: 'off', text: '' }); return; }
    if (ctl.current) ctl.current.abort(); const ac = new AbortController(); ctl.current = ac;
    setSt({ s: 'think', text: '' });
    const hold = P.rows.slice().sort((a, b) => b.w - a.w).slice(0, 6).map(r => r.t + ' ' + (r.w * 100).toFixed(0) + '%').join(', ');
    const top = P.look.filter(o => o.kind === 'stock').slice(0, 5).map(o => o.name + ' ' + (o.w * 100).toFixed(1) + '%').join(', ');
    const drivers = e.contrib.slice(0, 4).map(c => c.name + ' ' + fmtPct(c.c, 2)).join('; ');
    const prompt = [
      'You explain one result from a portfolio scenario engine to the person who owns the portfolio.',
      'Write 3 or 4 short sentences of plain English: no lists, no headings, no preamble.',
      'Say why this event moves this portfolio the way the numbers say, which holdings or exposures drive it, and one thing worth watching. Use only the numbers given. Do not give advice to buy or sell.',
      '',
      'Portfolio (' + fmtUsdFull(P.V) + '): ' + hold + '. Largest look-through companies: ' + (top || 'none') + '.',
      'Event: ' + e.t + ', resolves ' + fmtDate(e.date) + '. Odds ' + fmtOdds(q) + '.',
      'Full move if it happens: ' + fmtPct(e.dT, 2) + ' of the portfolio (direct ' + fmtPct(e.dD, 2) + ', knock-on effects through other events ' + fmtPct(e.dT - e.dD, 2) + ').',
      'Net of what the odds already price in: ' + fmtPct(ifYes, 2) + ' if it happens, ' + fmtPct(ifNo, 2) + ' if it does not.',
      'Biggest drivers (exposure times the factor move): ' + drivers + '.',
      'Background: ' + e.why,
    ].join('\n');
    try {
      const r = await sample(prompt, { modelTier: 'quick', signal: ac.signal, onText: ({ text }) => setSt({ s: 'stream', text }) });
      if (!ac.signal.aborted) setSt({ s: 'done', text: r.text });
    } catch (err) {
      const code = err && err.code;
      if (code === 'cancelled' || ac.signal.aborted) return;
      if (code === 'not_granted' || code === 'sampling_disabled' || code === 'capability_disabled' || code === 'not_declared') setSt({ s: 'off', text: '' });
      else if (code === 'rate_limited' || code === 'queue_overflow') setSt({ s: 'err', text: 'Claude is busy right now. Try again in a minute.' });
      else setSt({ s: 'err', text: (err && err.text) || 'Claude could not answer this time.' });
    }
  };
  if (st.s === 'off') return null;
  const busy = st.s === 'think' || st.s === 'stream';
  return html`<div style=${{ marginTop: 14 }}>
    ${busy ? html`<button className="btn-ghost" onClick=${() => { if (ctl.current) ctl.current.abort(); setSt({ s: 'idle', text: '' }); }}>Stop</button>`
      : html`<button className="btn-ghost" onClick=${go}>${st.s === 'idle' ? 'Explain for my portfolio' : 'Explain again'}</button>`}
    ${st.s === 'think' && html`<p className="ai-out sub">Thinking…</p>`}
    ${st.text && html`<p className=${cls('ai-out', st.s === 'err' && 'sub')} aria-live="polite">${st.text}</p>`}
  </div>`;
}

function Drawer({ e, S, P, tw, sampleId, whatIf, setWhatIf, onOpen, onClose, aiOK }) {
  const V = P.V; const closeRef = useRef(null); const bodyRef = useRef(null);
  useEffect(() => { if (closeRef.current) closeRef.current.focus({ preventScroll: true }); }, []);
  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = 0; }, [e.id]);
  useEffect(() => { const k = ev => { if (ev.key === 'Escape') onClose(); }; window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k); }, [onClose]);
  const q = e.resolved ? e.q0 : e.qEff;
  const ifYes = (1 - q) * e.dT, ifNo = -q * e.dT, sw = Math.abs(e.dT) * Math.sqrt(q * (1 - q));
  // A backend snapshot carries its own verdict, a χ² over the full jump vectors.
  const fused = useMemo(() => { const f = fuseReadings(e.src); return e.fusion ? { ...f, ...e.fusion } : f; }, [e.id]);
  const hist = useMemo(() => oddsHistory(e), [e.id]);
  const dis = e.odds.length >= 2 && !/[()]/.test(e.odds[0][0] + e.odds[1][0]) ? oddsDisagree(e.odds) : null;
  const wi = whatIf[e.id];
  const setWI = v => { const o = { ...whatIf }; if (v) o[e.id] = v; else delete o[e.id]; setWhatIf(o); };
  const knockSum = e.dT - e.dD; const dy = e.hist ? e.q0 - e.hist.y : null;
  const cmx = Math.max(1e-6, ...e.contrib.map(c => Math.abs(c.c)));
  const N = Math.round(Math.abs(e.dT) * V / 100); const yesSide = e.dT < 0; const price = yesSide ? q : 1 - q;
  return html`<${Frag}>
    <div className="scrim" onClick=${onClose}></div>
    <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="dr-title">
      <div className="dr-head">
        <div style=${{ display: 'flex', gap: 10, alignItems: 'center', minWidth: 0, flexWrap: 'wrap' }}><span className="pill">${e.cat}</span><span className="label">Resolves ${fmtDate(e.date)} · in ${e.days} days</span></div>
        <button ref=${closeRef} className="x" onClick=${onClose} aria-label="Close">×</button>
      </div>
      <div className="dr-body" ref=${bodyRef}>
        <h2 className="display dr-title" id="dr-title">${e.t}</h2>
        <div className="tierline"><span className=${'tier show t' + e.tier}><b>${e.tier}</b><span>${TIERS[e.tier].name}</span></span><p>${e.tierWhy || TIERS[e.tier].rule}</p></div>
        ${!e.inH && html`<p className="note">This resolves after your ${tw.horizon}-month horizon, so the simulation reprices it instead of resolving it.</p>`}
        ${e.resolved && html`<p className="note">You’re assuming this ${e.resolved === 'yes' ? 'happens' : 'doesn’t happen'}. The numbers below use the odds from before that assumption.</p>`}

        <section className="dr-sec"><span className="label">For your portfolio</span>
          <div className="three">
            <div><span className="label">If it happens</span><span className=${cls('v', tone(ifYes))}>${fmtUsd(ifYes * V / 100, true)}</span><span className="s">${fmtPct(ifYes)}</span></div>
            <div><span className="label">If it doesn’t</span><span className=${cls('v', tone(ifNo))}>${fmtUsd(ifNo * V / 100, true)}</span><span className="s">${fmtPct(ifNo)}</span></div>
            <div><span className="label">Swing</span><span className="v">${swingUsd(sw * V / 100)}</span><span className="s">${swingPct(sw)}</span></div>
          </div>
          <${SplitBar} dT=${e.dT} q=${q} />
          <div className="formula">
            <div>full move ${fmtPct(e.dT, 2)} = direct ${fmtPct(e.dD, 2)}${Math.abs(knockSum) >= 0.005 ? ' + knock-ons ' + fmtPct(knockSum, 2) : ''}</div>
            <div>if it happens: (1 − ${q.toFixed(2)}) × ${fmtPct(e.dT, 2)} = ${fmtPct(ifYes, 2)}</div>
            <div>if it doesn’t: ${MINUS}${q.toFixed(2)} × ${fmtPct(e.dT, 2)} = ${fmtPct(ifNo, 2)}</div>
          </div>
          ${sampleId === 'qqq' && e.id === 'tariff' && html`<p className="note">This is the worked example in DESIGN.md section 3: a −8.5% full move, −5.1% if it happens and +3.4% if it doesn’t.</p>`}
          <div className="btnrow">
            <button className=${cls('btn-ghost', wi === 'yes' && 'on')} aria-pressed=${wi === 'yes'} onClick=${() => setWI(wi === 'yes' ? null : 'yes')}>What if it happens?</button>
            <button className=${cls('btn-ghost', wi === 'no' && 'on')} aria-pressed=${wi === 'no'} onClick=${() => setWI(wi === 'no' ? null : 'no')}>What if it doesn’t?</button>
          </div>
        </section>

        <section className="dr-sec"><span className="label">Odds</span>
          <div className="odds-big"><span className="v">${fmtOdds(q)}</span><span className="sub" style=${{ margin: 0 }}>${e.m ? 'market odds' : 'AI estimate, no market'}${e.q0 !== e.q && !e.resolved ? ' · live' : ''}${dy != null && Math.abs(dy) >= 0.005 ? ' · ' + fmtPts(dy) + ' since yesterday' : ''}</span></div>
          ${Math.abs(e.ph - e.q) >= 0.005 && html`<p className="sub">Calibrated: ${fmtOdds(e.ph0)}. ${calibrationNote()}</p>`}
          ${!e.resolved && Math.abs(e.shift) >= 0.005 && html`<p className="note">Your what-if moves these odds from ${fmtOdds(e.q0)} to ${fmtOdds(e.qEff)} through the event graph.</p>`}
          <ul className="venues">${e.odds.map(([n, v, se]) => html`<li key=${n}><span>${n}</span><span>${fmtOdds(v)} ± ${(se * 100).toFixed(se * 100 < 2 ? 1 : 0)}</span></li>`)}</ul>
          ${dis && dis.z > 2 && html`<p className="note">${e.odds[0][0]} and ${e.odds[1][0]} disagree by ${Math.round(dis.gap * 100)} points, ${dis.z.toFixed(1)} times their combined error. One of them is probably mispriced.</p>`}
          ${hist && html`<${Spark} h=${hist} now=${e.q0} from=${e.seriesFrom ? fmtDate(e.seriesFrom) : null} />`}
          ${e.seriesNote && html`<p className="sub">${e.seriesNote}, read at each day’s 4 p.m. New York close.</p>`}
        </section>

        <section className="dr-sec"><span className="label">Matched market</span>
          ${e.m ? html`<${Frag}>
            <div style=${{ display: 'flex', gap: '6px 12px', alignItems: 'center', flexWrap: 'wrap' }}><span className="pill pill-acc">${REL[e.m.rel] || e.m.rel}</span><span className="mono" style=${{ fontSize: 12 }}>Match ${Math.round(e.m.score * 100)}<span className="scorebar"><i style=${{ width: e.m.score * 100 + '%' }}></i></span></span><span className="sub" style=${{ margin: 0 }}>${e.m.venue} · ${e.m.liq}</span></div>
            <blockquote className="quote"><b>${e.m.title}</b><br />${e.m.rule}</blockquote>
            <table className="cmp"><thead><tr><th></th><th>The event</th><th>The market</th><th></th></tr></thead><tbody>${e.m.cmp.map(([k, a, b, s]) => html`<tr key=${k}><td className="label">${k}</td><td>${a}</td><td>${b}</td><td><span className=${'mk ' + ['bad', 'mid', 'ok'][s]} title=${['Differs', 'Close', 'Same'][s]}>${['✕', '≈', '✓'][s]}</span></td></tr>`)}</tbody></table>
            ${e.m.ladder && html`<p className="sub">${e.m.ladder}</p>`}
            ${e.m.note && html`<p className="note">${e.m.note}</p>`}
          <//>` : html`<p className="note"><b>Nothing fits.</b> No listed market resolves on this event, so the odds and the jump come from past events and an AI estimate, with wide error bars.</p>`}
          ${e.miss && html`<${Frag}><span className="label" style=${{ display: 'block', marginTop: 16 }}>Rejected near-misses</span><ul className="miss">${e.miss.map(([v, t, why]) => html`<li key=${t}><span className="label">${v}</span><b>${t}</b><span>${why}</span></li>`)}</ul><//>`}
        </section>

        <section className="dr-sec"><span className="label">How big is the jump?</span>
          <p className="sub" style=${{ margin: '0 0 10px' }}>Each source measures the event’s jump separately. Dots show each reading against the fused value (the dashed line), with its error bar. Filled dots are market readings.</p>
          <div className="ev-src">${fused.rd.map(r => html`<${Frag} key=${r.name}>
            <span className="nm">${r.name}<small>${r.detail}</small></span>
            <${ErrBar} k=${r.kn} s=${r.sn} market=${r.market} />
            <span className="vv">${r.kn.toFixed(2)}×</span><//>`)}</div>
          <div className="verdict">${verdictText(fused)}</div>
        </section>

        <section className="dr-sec"><span className="label">Where the move comes from</span>
          <p className="sub" style=${{ margin: '0 0 12px' }}>Your exposure to each factor times how far the event moves it. Knock-on effects come on top.</p>
          <div className="contrib">${e.contrib.slice(0, 8).map(c => { const w = Math.abs(c.c) / cmx * 50; return html`<${Frag} key=${c.key}>
            <span>${c.name}<span className="why2">${c.direct ? (c.exp * 100).toFixed(1) + '% of you × ' + fmtPct(c.move, 0) : fmtPct(c.exp, 2) + ' per move × ' + c.move.toFixed(2) + ' moves'}</span></span>
            <span className="cbar"><i className=${c.c < 0 ? 'neg' : ''} style=${{ left: (c.c < 0 ? 50 - w : 50) + '%', width: w + '%' }}></i></span>
            <span className=${cls('mono', tone(c.c))} style=${{ textAlign: 'right', fontSize: 12 }}>${fmtPct(c.c, 2)}</span><//>`; })}</div>
          ${e.contrib.length === 0 && html`<p className="sub">This portfolio has almost no exposure to what this event moves.</p>`}
        </section>

        ${(e.children.length > 0 || e.parents.length > 0) && html`<section className="dr-sec"><span className="label">Knock-on effects</span><ul className="knock">
          ${e.knock.map(k => { const ce = S.byId[k.id]; return html`<li key=${'c' + k.id}>If it happens, <button onClick=${() => onOpen(k.id)}>${ce.t}</button> becomes ${k.w > 0 ? 'more' : 'less'} likely by ${Math.round(Math.abs(k.w) * 100)} points. That adds <b className=${tone(k.d)}>${fmtPct(k.d, 2)}</b> to the full move.</li>`; })}
          ${e.parents.map(p => html`<li key=${'p' + p.id}><button onClick=${() => onOpen(p.id)}>${S.byId[p.id].t}</button> would make this ${p.w > 0 ? 'more' : 'less'} likely by ${Math.round(Math.abs(p.w) * 100)} points.</li>`)}
        </ul></section>`}

        <section className="dr-sec"><span className="label">Why it moves you</span><p style=${{ margin: 0 }}>${e.why}</p>
          ${aiOK && html`<${Explain} e=${e} P=${P} q=${q} ifYes=${ifYes} ifNo=${ifNo} />`}
        </section>

        <section className="dr-sec"><span className="label">If you wanted to hedge</span><div className="hedge">
          ${e.m && N > 0 ? html`<${Frag}><p>Contracts that pay $1 if it ${yesSide ? 'happens' : 'doesn’t happen'} would offset the move: about <b>${N.toLocaleString('en-US')} ${yesSide ? 'YES' : 'NO'} contracts</b> at ${Math.round(price * 100)}¢, roughly <b>${fmtUsd(N * price)}</b>.</p><p>Either way the portfolio then ends near today’s value for this event. The cost is the price of removing the swing, and the match score above is the basis risk.</p><//>`
            : html`<p>${e.m ? 'This portfolio barely moves on this event, so there is nothing worth hedging.' : 'No market fits, so there is nothing to hedge with directly. Treat it as a risk to watch.'}</p>`}
        </div></section>
      </div>
    </aside>
  <//>`;
}

/* ---------- historical replay ---------- */
function verdictOf(pred, real) {
  if (Math.abs(real) < 0.3 && Math.abs(pred) < 0.3) return ['ok', '✓', 'Both small'];
  if (Math.sign(pred) !== Math.sign(real)) return ['bad', '✕', 'Wrong direction'];
  const r = Math.abs(pred) / Math.abs(real);
  if (r >= 0.7 && r <= 1.4) return ['ok', '✓', 'Close'];
  return ['mid', '≈', r < 1 ? 'Right way, too small' : 'Right way, too big'];
}

function ReplayChart({ RC, day, setDay }) {
  const ref = useRef(null);
  const R = REPLAY, D = R.days.length, W = Math.max(300, useWidth(ref, 640)), H = 250, m = { l: 40, r: 12, t: 14, b: 30 };
  const vals = RC.real.concat(RC.cum, RC.calls.map(c => c.pred));
  const ym = Math.max(2, ...vals.map(Math.abs)) * 1.12;
  const cw = (W - m.l - m.r) / D; const every = cw < 46 ? 2 : 1; const X = i => m.l + (i + 0.5) * cw; const Y = v => m.t + (ym - v) / (2 * ym) * (H - m.t - m.b);
  const ticks = niceTicks(-ym, ym, 6);
  const cumPath = RC.cum.slice(0, day + 1).map((v, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ',' + Y(v).toFixed(1)).join('');
  return html`<div className="rp-chart" ref=${ref}><svg viewBox=${'0 0 ' + W + ' ' + H} role="img" aria-label="Daily moves of your portfolio, with the engine’s calls on the days events resolved">
    ${ticks.map(v => html`<g key=${'y' + v}><line x1=${m.l} x2=${W - m.r} y1=${Y(v)} y2=${Y(v)} style=${{ stroke: Math.abs(v) < 1e-9 ? 'var(--line-2)' : 'var(--line)' }} /><text x=${m.l - 6} y=${Y(v) + 3} textAnchor="end" className="ax">${fmtPct(v, 0)}</text></g>`)}
    ${RC.real.map((v, i) => html`<rect key=${'b' + i} x=${X(i) - cw * 0.28} y=${Math.min(Y(v), Y(0))} width=${cw * 0.56} height=${Math.max(1, Math.abs(Y(v) - Y(0)))} rx="1.5" style=${{ fill: v < 0 ? 'var(--loss)' : 'var(--gain)', opacity: i <= day ? (i === day ? 1 : 0.7) : 0.14, cursor: 'pointer' }} onClick=${() => setDay(i)} />`)}
    ${RC.calls.map(c => html`<rect key=${'p' + c.id} x=${X(c.day) - cw * 0.42} y=${Math.min(Y(c.pred), Y(0))} width=${cw * 0.84} height=${Math.max(1, Math.abs(Y(c.pred) - Y(0)))} rx="2" style=${{ fill: 'none', stroke: 'var(--ink)', strokeWidth: 1.4, strokeDasharray: '4 3', opacity: c.day <= day ? 1 : 0.25 }} />`)}
    <path d=${cumPath} style=${{ fill: 'none', stroke: 'var(--accent)', strokeWidth: 2, strokeLinejoin: 'round' }} />
    ${RC.cum.slice(0, day + 1).map((v, i) => html`<circle key=${'c' + i} cx=${X(i)} cy=${Y(v)} r=${i === day ? 4 : 2.5} style=${{ fill: 'var(--accent)' }} />`)}
    ${R.days.map((d, i) => (i % every === 0 || i === day ? html`<text key=${'d' + i} x=${X(i)} y=${H - 10} textAnchor="middle" className="ax" style=${{ fontWeight: i === day ? 700 : 500 }}>${fmtDate(d.d, false)}</text>` : null))}
  </svg></div>`;
}

function ReplayView({ P, tw }) {
  const R = REPLAY, D = R.days.length, V = P.V;
  const RC = useMemo(() => replayCompute(P, tw.knock), [P, tw.knock]);
  const [day, setDay] = useState(0); const [play, setPlay] = useState(false);
  useEffect(() => {
    if (!play) return undefined;
    const id = setInterval(() => setDay(d => { if (d >= D - 1) { setPlay(false); return d; } return d + 1; }), 1100);
    return () => clearInterval(id);
  }, [play]);
  const pick = i => { setPlay(false); setDay(i); };
  const resDays = new Set(RC.calls.map(c => c.day));
  const order = { open: 0, happened: 1, unlisted: 2 };
  const rows = RC.states[day].slice().sort((a, b) => order[a.status] - order[b.status] || (b.swing || 0) - (a.swing || 0));
  const real = RC.real[day], cum = RC.cum[day];
  const right = RC.calls.filter(c => verdictOf(c.pred, c.real)[0] !== 'bad').length;
  const worst = RC.calls.slice().sort((a, b) => Math.abs(b.real - b.pred) - Math.abs(a.real - a.pred))[0];
  const togglePlay = () => { if (!play && day >= D - 1) setDay(0); setPlay(!play); };
  let summary = '';
  if (worst) {
    summary = 'For this portfolio the engine got the direction right ' + right + ' of ' + RC.calls.length + ' times. Its biggest miss was “' + worst.s + '”: it expected ' + fmtPct(worst.pred) + ' and the day brought ' + fmtPct(worst.real) + '. ';
    summary += Math.abs(worst.real) > Math.abs(worst.pred)
      ? 'On stress days everything moves together more than usual, so exposures measured on normal days understate the hit. The design’s fix is a second set of exposures fitted on stress periods.'
      : 'Part of that move had already happened in the days before, as the odds rose.';
  }
  return html`<${Frag}>
    <section className="card" aria-labelledby="rp-h">
      <div className="card-head">
        <div><span className="kicker">Historical replay · a resolved episode</span><h2 className="h2" id="rp-h" style=${{ marginTop: 6 }}>${R.title}</h2><p className="sub">${R.sub} Each day, the engine only sees the odds as of the previous close. Change the portfolio on the left and the replay updates.</p></div>
        <div className="rp-ctl"><button className="btn" onClick=${togglePlay}>${play ? 'Pause' : day >= D - 1 ? 'Replay' : 'Play'}</button></div>
      </div>
      <div className="rp-time"><div className="rp-days" role="group" aria-label="Trading days">${R.days.map((d, i) => html`<button key=${d.d} className=${cls('rp-day', i < day && 'past', i === day && 'on', resDays.has(i) && 'res')} aria-pressed=${i === day} onClick=${() => pick(i)}>${fmtDate(d.d, false)}</button>`)}</div></div>
      <p className="rp-note"><span className="label">${fmtDate(R.days[day].d)}</span><b className=${tone(real)}>${fmtUsd(real * V / 100, true)} (${fmtPct(real)})</b><span>${R.days[day].note}</span><span className="sub" style=${{ margin: 0 }}>Running total ${fmtPct(cum)}</span></p>
    </section>
    <div className="duo">
      <section className="card"><div className="card-head"><div><span className="kicker">Your portfolio, day by day</span><h3 className="h3" style=${{ marginTop: 6 }}>Moves against the engine’s calls</h3></div></div>
        <${ReplayChart} RC=${RC} day=${day} setDay=${pick} />
        <div className="legend"><span><i className="sw" style=${{ background: 'var(--loss)', borderRadius: 2 }}></i>Your portfolio that day</span><span><i className="sw" style=${{ background: 'none', border: '1.5px dashed var(--ink)', borderRadius: 2 }}></i>The engine’s call the evening before</span><span><i className="ln"></i>Running total</span></div>
      </section>
      <section className="card"><div className="card-head"><div><span className="kicker">At the close of ${fmtDate(R.days[day].d, false)}</span><h3 className="h3" style=${{ marginTop: 6 }}>What the engine saw</h3></div></div>
        <ul className="rp-list">${rows.map(s => html`<li key=${s.id} className=${s.status !== 'open' ? 'done' : ''}>
          <span>${s.t}<small>${s.status === 'open' ? 'If it happens ' + fmtPct(s.ifYes) + ' · if not ' + fmtPct(s.ifNo) : s.status === 'happened' ? 'Happened ' + fmtDate(s.on, false) : 'No market yet'}</small></span>
          <span className="mono" style=${{ textAlign: 'right' }}>${s.status === 'open' ? fmtOdds(s.q) : ''}</span>
          <span className="cell">${s.status === 'open' ? swingUsd(s.swing * V / 100) : ''}${s.status === 'open' && html`<small>${swingPct(s.swing)}</small>`}</span></li>`)}</ul>
      </section>
    </div>
    <section className="card"><div className="card-head"><div><span className="kicker">Calls against outcomes</span><h3 className="h3" style=${{ marginTop: 6 }}>Did the engine get it right?</h3></div></div>
      <div className="scroll-x"><table className="calls"><thead><tr><th>Event</th><th>Resolved</th><th>Odds before</th><th>Engine said</th><th>The day</th><th>Verdict</th></tr></thead><tbody>
        ${RC.calls.map(c => { const [k, mk, txt] = verdictOf(c.pred, c.real); return html`<tr key=${c.id}><td>${c.t}</td><td className="n">${fmtDate(c.date, false)}</td><td className="n">${fmtOdds(c.q)}</td><td className=${cls('n', tone(c.pred))}>${fmtPct(c.pred)}<br /><small>${fmtUsd(c.pred * V / 100, true)}</small></td><td className=${cls('n', tone(c.real))}>${fmtPct(c.real)}<br /><small>${fmtUsd(c.real * V / 100, true)}</small></td><td><span className=${'mk ' + k}>${mk}</span> ${txt}</td></tr>`; })}
      </tbody></table></div>
      ${summary && html`<p className="note">${summary}</p>`}
    </section>
    <p className="sub" style=${{ maxWidth: '90ch' }}>Factor moves are approximate daily closes from April 2025. The odds paths are illustrative reconstructions, not archived prices. In the real system this test runs on every resolved event, and its results set how much each evidence source is trusted.</p>
  <//>`;
}

/* ---------- tweaks ---------- */
const DEFAULT_TWEAKS = { odds: 'market', knock: true, horizon: 6, tiers: 'ABCD', topN: 12, sims: 4000, noise: false, theme: 'auto', accent: 'dragon', heading: 'serif', density: 'comfortable', hero: true, dragon: true, glow: 'normal', citadel: true, view: 'macro', rotate: true, labels: 'top', links: true };
const TWEAK_OPTS = {
  odds: ['market', 'calibrated'], knock: [true, false], horizon: [3, 6, 12], tiers: ['ABCD', 'ABC', 'AB'], topN: [8, 12, 'all'], sims: [2000, 4000, 10000], noise: [false, true],
  theme: ['auto', 'light', 'dark'], accent: ['dragon', 'ember', 'teal', 'steel'], heading: ['serif', 'sans'], density: ['comfortable', 'compact'], hero: [true, false], dragon: [true, false], glow: ['normal', 'bright'], citadel: [true, false],
  view: ['macro', 'mine', 'flat'], rotate: [true, false], labels: ['top', 'all', 'none'], links: [true, false],
};
function cleanTweaks(t) {
  const o = {}; if (!t || typeof t !== 'object') return o;
  // One earlier version saved the hero as 'dragon', 'image' or 'off'; the image is the hero again.
  if (typeof t.hero === 'string') t = { ...t, hero: t.hero !== 'off' };
  for (const k in TWEAK_OPTS) if (TWEAK_OPTS[k].includes(t[k])) o[k] = t[k];
  return o;
}

function TweaksSheet({ tw, setTw, onClose }) {
  const [copied, setCopied] = useState(null); const closeRef = useRef(null);
  useEffect(() => { if (closeRef.current) closeRef.current.focus({ preventScroll: true }); }, []);
  useEffect(() => { const k = ev => { if (ev.key === 'Escape') onClose(); }; window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k); }, [onClose]);
  const set = (k, v) => setTw({ ...tw, [k]: v });
  const Row = (label, help, k, opts) => html`<div className="tw-row"><span>${label}${help && html`<small>${help}</small>`}</span>
    <div className="seg" role="group" aria-label=${label}>${opts.map(([v, l]) => html`<button key=${String(v)} className=${tw[k] === v ? 'on' : ''} aria-pressed=${tw[k] === v} onClick=${() => set(k, v)}>${l}</button>`)}</div></div>`;
  const json = JSON.stringify(tw, null, 2);
  const copy = () => {
    try { navigator.clipboard.writeText(json).then(() => setCopied('ok'), () => setCopied('manual')); } catch (e) { setCopied('manual'); }
  };
  return html`<${Frag}>
    <div className="scrim" onClick=${onClose}></div>
    <aside className="sheet" role="dialog" aria-modal="true" aria-labelledby="tw-h">
      <div className="dr-head"><h2 className="h3" id="tw-h">Tweaks</h2><button ref=${closeRef} className="x" onClick=${onClose} aria-label="Close">×</button></div>
      <div className="tw-body">
        <div className="tw-group"><span className="label">Model</span>
          ${Row('Odds', oddsTweak(), 'odds', [['market', 'Market'], ['calibrated', 'Calibrated']])}
          ${Row('Knock-on effects', 'Events that make others likelier', 'knock', [[true, 'On'], [false, 'Off']])}
          ${Row('Horizon', 'Events resolving after it are repriced', 'horizon', [[3, '3 mo'], [6, '6 mo'], [12, '12 mo']])}
          ${Row('Events listed', null, 'topN', [[8, '8'], [12, '12'], ['all', 'All']])}
          ${Row('Tiers shown', 'Hide events with weaker evidence', 'tiers', [['ABCD', 'All'], ['ABC', 'A–C'], ['AB', 'A–B']])}
          ${Row('Scenarios', null, 'sims', [[2000, '2k'], [4000, '4k'], [10000, '10k']])}
          ${Row('Everyday noise', 'Adds normal market moves to the simulation', 'noise', [[false, 'Off'], [true, 'On']])}
        </div>
        <div className="tw-group"><span className="label">Look</span>
          ${Row('Theme', null, 'theme', [['auto', 'Auto'], ['light', 'Light'], ['dark', 'Dark']])}
          ${Row('Accent', null, 'accent', [['dragon', 'Dragon'], ['ember', 'Ember'], ['teal', 'Teal'], ['steel', 'Steel']])}
          ${Row('Headings', null, 'heading', [['serif', 'Serif'], ['sans', 'Sans']])}
          ${Row('Density', null, 'density', [['comfortable', 'Roomy'], ['compact', 'Compact']])}
          ${Row('Hero image', null, 'hero', [[true, 'On'], [false, 'Off']])}
          ${Row('Dragon story', 'The scroll story between the hero and the engine', 'dragon', [[true, 'On'], [false, 'Off']])}
          ${tw.dragon && Row('Dragon glow', null, 'glow', [['normal', 'Normal'], ['bright', 'Bright']])}
          ${tw.dragon && Row('Castle behind the dragon', 'A faint citadel in the numbers', 'citadel', [[true, 'On'], [false, 'Off']])}
        </div>
        <div className="tw-group"><span className="label">Map</span>
          ${Row('View', null, 'view', [['macro', 'Macro'], ['mine', 'Mine'], ['flat', '2D']])}
          ${Row('Auto-rotate', null, 'rotate', [[true, 'On'], [false, 'Off']])}
          ${Row('Labels', null, 'labels', [['top', 'Top 5'], ['all', 'All'], ['none', 'None']])}
          ${Row('Knock-on links', null, 'links', [[true, 'On'], [false, 'Off']])}
        </div>
        <div className="tw-group"><span className="label">Keep these settings</span>
          <p className="sub">Settings and your portfolio are saved in this browser. Copy the settings to share them or paste them into a chat.</p>
          <div className="btnrow"><button className="btn" onClick=${copy}>${copied === 'ok' ? 'Copied' : 'Copy settings'}</button><button className="btn-ghost" onClick=${() => setTw({ ...DEFAULT_TWEAKS })}>Reset to defaults</button></div>
          ${copied === 'manual' && html`<textarea className="in copybox" readOnly value=${json} onFocus=${e => e.target.select()} aria-label="Settings as JSON"></textarea>`}
        </div>
      </div>
    </aside>
  <//>`;
}

/* ---------- shell ---------- */
function Footer({ ds }) {
  return html`<footer className="foot"><div className="foot-in">
    <div className="foot-brand"><${KeepMark} size=${18} /><span className="brand-name">The Red Keep</span><span className="foot-tag">Scenario Analysis Engine</span></div>
    <div className="foot-txt">
      <p>An independent take-home by Ashwin, built for Castle. Not affiliated with or endorsed by Castle.</p>
      ${ds !== 'live'
        ? html`<p>Everything here is illustrative: prices, odds, exposures, market matches and evidence readings are made up for the prototype, shaped like the real system’s inputs. Nothing here is investment advice.</p>`
        : EXPOSURES === 'live'
          ? html`<p>Backend snapshot of ${fmtStamp(RK_LIVE.asOf)}: odds from Polymarket and Kalshi, prices and factor data from Massive and FRED, market matches judged by Claude, and jumps and exposures estimated by the engine. The April 2025 replay stays illustrative. Nothing here is investment advice.</p>`
          : html`<p>Backend snapshot of ${fmtStamp(RK_LIVE.asOf)}: odds from Polymarket and Kalshi, factor data from ${UNMEASURED.length ? 'FRED' : 'Massive and FRED'}, market matches judged by Claude, and jumps estimated by the engine. The portfolios’ prices, holdings and exposures are illustrative, rescaled to the snapshot’s typical moves, and so is the April 2025 replay. Nothing here is investment advice.</p>`}
    </div>
  </div></footer>`;
}

function EmptyState({ onPick }) {
  return html`<section className="card empty"><span className="kicker">Nothing to analyse yet</span>
    <h2 className="h2" style=${{ marginTop: 8 }}>Add a holding, or start from a famous portfolio.</h2>
    <p className="sub">Type a ticker in the panel, or pick one of these.</p>
    <${SampleChips} sampleId=${null} onPick=${onPick} /></section>`;
}

class Boundary extends React.Component {
  constructor(p) { super(p); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  render() {
    if (!this.state.err) return this.props.children;
    return html`<div className="wrap" style=${{ paddingBlock: 40 }}><div className="card empty"><span className="kicker">Something broke</span>
      <h2 className="h2" style=${{ marginTop: 8 }}>The keep took a hit.</h2><p className="sub">${String((this.state.err && this.state.err.message) || this.state.err)}</p>
      <div className="btnrow"><button className="btn" onClick=${() => { try { localStorage.removeItem(LS_KEY); } catch (e) { /* ignore */ } location.reload(); }}>Reset and reload</button></div></div></div>`;
  }
}

function validHoldings(h) {
  if (!Array.isArray(h)) return null;
  const out = h.filter(x => x && TICKERS[x.t] && (x.u === 'usd' || x.u === 'sh') && typeof x.a === 'number' && isFinite(x.a) && x.a >= 0)
    .map((x, i) => ({ id: String(x.id || x.t + i), t: x.t, u: x.u, a: x.a }));
  return out;
}

function App({ ds, setDs }) {
  // Holdings saved under other portfolios may name tickers these lack: keep only the settings then. An events-only
  // snapshot keeps the illustrative portfolios, so holdings carry over between it and the illustrative data.
  const saved = useMemo(() => { const s = loadSaved(); return s && (s.universe || s.dataset || 'demo') !== EXPOSURES ? { tweaks: s.tweaks, tab: s.tab } : s; }, []);
  const [holdings, setHoldings] = useState(() => { const h = saved && validHoldings(saved.holdings); return h && h.length ? h : sampleToHoldings(SAMPLES[0]); });
  const [sampleId, setSampleId] = useState(() => (saved && 'sampleId' in saved ? saved.sampleId : SAMPLES[0].id));
  const [tw, setTw] = useState(() => ({ ...DEFAULT_TWEAKS, ...cleanTweaks(saved && saved.tweaks) }));
  const [tab, setTab] = useState(() => (saved && saved.tab === 'replay' ? 'replay' : 'analysis'));
  const [sel, setSel] = useState(null);
  const [hover, setHover] = useState(null);
  const [whatIf, setWhatIf] = useState({});
  const [live, setLive] = useState(false);
  const [liveOdds, setLiveOdds] = useState({});
  const [feed, setFeed] = useState([]);
  const [ripple, setRipple] = useState(null);
  const [tweaksOpen, setTweaksOpen] = useState(false);
  const [aiOK, setAiOK] = useState(false);
  const heroRef = useRef(null);
  // "Bare" means nothing sits above the app but the nav: no hero image and no dragon story.
  const bare = !tw.hero && !tw.dragon, bareRef = useRef(bare); bareRef.current = bare;
  const pastHero = useScrolledPast(heroRef, 36, tw.hero);
  // Over the dragon story the nav goes back to dark glass, as it is over the hero image.
  const dragonRef = useRef(null);
  const overDragon = useUnderNav(dragonRef, 40, tw.dragon);

  useEffect(() => { saveState({ dataset: ds, universe: EXPOSURES, holdings, sampleId, tweaks: tw, tab }); }, [ds, holdings, sampleId, tw, tab]);
  useEffect(() => { let on = true; getSample().then(s => { if (on) setAiOK(!!s); }); return () => { on = false; }; }, []);
  useEffect(() => {
    const r = document.documentElement;
    if (tw.theme === 'light' || tw.theme === 'dark') r.setAttribute('data-theme', tw.theme);
    else if (HOST_THEME) r.setAttribute('data-theme', HOST_THEME); else r.removeAttribute('data-theme');
  }, [tw.theme]);

  const P = useMemo(() => analyzePortfolio(holdings), [holdings]);
  const S = useMemo(() => buildState(P, tw, liveOdds, whatIf), [P, tw.knock, tw.horizon, liveOdds, whatIf]);
  const sim = useMemo(() => { const t0 = performance.now(); const r = simulate(S, P, tw, tw.sims); r.ms = performance.now() - t0; return r; }, [S, P, tw.odds, tw.horizon, tw.sims, tw.noise]);
  const shown = useMemo(() => S.evs.filter(e => e.inH && !e.resolved && tw.tiers.includes(e.tier)).sort((a, b) => b.swing - a.swing), [S, tw.tiers]);
  const beyond = S.evs.filter(e => !e.inH).length;

  // Simulated live odds: a random walk in log-odds with a pull back to the snapshot.
  const ctx = useRef({}); ctx.current = { S, P, liveOdds };
  useEffect(() => {
    if (!live) return undefined;
    const tick = () => {
      const { S: S0, P: P0, liveOdds: prev } = ctx.current;
      const cands = S0.evs.filter(e => e.hist && !e.resolved); if (!cands.length) return;
      const e = cands[Math.floor(Math.random() * cands.length)];
      const qPrev = clamp(e.q + (prev[e.id] || 0), 0.005, 0.995);
      const z = logit(qPrev) + (Math.random() - 0.5) * 0.24 - 0.12 * (logit(qPrev) - logit(e.q));
      const q1 = clamp(sigmoid(z), 0.01, 0.99);
      const next = { ...prev, [e.id]: q1 - e.q }; ctx.current.liveOdds = next;
      const t = new Date();
      setLiveOdds(next);
      setFeed(f => [{ k: t.getTime() + e.id, id: e.id, q0: qPrev, q1, usd: (q1 - qPrev) * e.dT * P0.V / 100, time: t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }].concat(f).slice(0, 30));
      setRipple({ id: e.id, t: performance.now() });
    };
    tick(); const id = setInterval(tick, 2200);
    return () => clearInterval(id);
  }, [live]);

  const loadSample = useCallback(id => { const s = SAMPLES.find(x => x.id === id); if (!s) return; setHoldings(sampleToHoldings(s)); setSampleId(id); setWhatIf({}); }, []);
  const editHoldings = useCallback(h => { setHoldings(h); setSampleId(null); }, []);
  const openEv = useCallback(id => setSel(id), []);
  const closeEv = useCallback(() => setSel(null), []);
  const closeTweaks = useCallback(() => setTweaksOpen(false), []);
  const resetLive = useCallback(() => { setLiveOdds({}); setFeed([]); }, []);
  // Switching views from the nav or the hero lands on the top of the app, just under the nav.
  const goTab = useCallback(k => {
    setTab(k);
    requestAnimationFrame(() => {
      const el = document.getElementById('app'); if (!el) return;
      // The app's intro leaves room for the floating nav, except on a bare page, where the nav sits in the flow above it.
      const navOffset = bareRef.current ? 64 : 0;
      window.scrollTo({ top: Math.max(0, el.getBoundingClientRect().top + window.scrollY - navOffset), behavior: REDUCED ? 'auto' : 'smooth' });
    });
  }, []);
  const selEv = sel ? S.byId[sel] : null;
  const hasLive = Object.keys(liveOdds).length > 0;

  return html`<div className=${cls('app', bare && 'no-hero')} data-accent=${tw.accent} data-heading=${tw.heading} data-density=${tw.density}>
    <${Banner} ds=${ds} setDs=${setDs} />
    <${Nav} tab=${tab} goTab=${goTab} live=${live} setLive=${setLive} openTweaks=${() => setTweaksOpen(true)} solid=${(!tw.hero || pastHero) && !overDragon} />
    ${tw.hero && html`<${Hero} tw=${tw} goTab=${goTab} heroRef=${heroRef} />`}
    ${tw.dragon && html`<${DragonScene} tw=${tw} rootRef=${dragonRef} />`}
    <div className="wrap" id="app">
      <${Intro} tab=${tab} />
      <div className="layout">
        <aside className="side" aria-label="Portfolio">
          <${PortfolioCard} holdings=${holdings} P=${P} sampleId=${sampleId} onPick=${loadSample} onEdit=${editHoldings} />
          ${P.V > 0 && html`<${LookThrough} P=${P} />`}
          ${P.V > 0 && html`<${Exposure} P=${P} />`}
        </aside>
        <main className="main" id="main">
          ${P.V <= 0 ? html`<${EmptyState} onPick=${loadSample} />`
            : tab === 'analysis' ? html`<${AnalysisView} P=${P} S=${S} sim=${sim} shown=${shown} beyond=${beyond} tw=${tw} setTw=${setTw} hover=${hover} setHover=${setHover} sel=${sel} onOpen=${openEv} whatIf=${whatIf} setWhatIf=${setWhatIf} live=${live} setLive=${setLive} feed=${feed} ripple=${ripple} hasLive=${hasLive} resetLive=${resetLive} />`
            : html`<${ReplayView} P=${P} tw=${tw} />`}
        </main>
      </div>
    </div>
    <${Footer} ds=${ds} />
    ${selEv && html`<${Drawer} e=${selEv} S=${S} P=${P} tw=${tw} sampleId=${sampleId} whatIf=${whatIf} setWhatIf=${setWhatIf} onOpen=${openEv} onClose=${closeEv} aiOK=${aiOK} />`}
    ${tweaksOpen && html`<${TweaksSheet} tw=${tw} setTw=${setTw} onClose=${closeTweaks} />`}
  </div>`;
}

// Picks the data (a backend snapshot when the build included one, unless the visitor chose the illustrative data)
// and remounts the app when it changes.
function Root() {
  const [ds, setDsState] = useState(() => { const s = loadSaved(); return applyDataset(HAS_LIVE && !(s && s.dataset === 'demo') ? 'live' : 'demo'); });
  const setDs = useCallback(id => { setDsState(applyDataset(id)); }, []);
  return html`<${App} key=${ds} ds=${ds} setDs=${setDs} />`;
}

(function boot() {
  const el = document.getElementById('root');
  if (!html || !window.ReactDOM) { el.innerHTML = '<p class="boot">The app’s libraries did not load. Check the connection and reload.</p>'; return; }
  ReactDOM.createRoot(el).render(html`<${Boundary}><${Root} /><//>`);
})();
