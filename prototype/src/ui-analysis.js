/* ==========================================================================
   UI · Part 2: the analysis view. Pipeline strip, tiles, 3D event map,
   ranked events, simulated outcomes, live feed.
   ========================================================================== */
function Pipeline({ P, S, sim, tw }) {
  const inH = S.evs.filter(e => e.inH);
  const matched = inH.filter(e => e.m).length;
  const nT = t => inH.filter(e => e.tier === t).length;
  const steps = [
    ['Look through', html`<b>${P.rows.length}</b> holding${P.rows.length === 1 ? '' : 's'} → <b>${P.nUnderlying}</b> companies`, 'Funds are replaced by what they hold, so overlaps add up.'],
    ['Exposure', html`<b>${NF + P.extras.length}</b> factors · fit <b>${P.fit == null ? '–' : Math.round(P.fit * 100) + '%'}</b>`, 'Ten core factors, plus extras pulled in only when this portfolio needs them.'],
    ['Discover', html`<b>${inH.length}</b> events in ${tw.horizon} months`, 'Events that resolve inside your horizon. The real library keeps about 250, tagged from thousands of markets by Laya.'],
    ['Match', html`<b>${matched}</b> matched · <b>${inH.length - matched}</b> unpriced`, 'Each event is matched to a prediction market with the same resolution rule, or marked unpriced.'],
    ['Evidence', html`<b>${nT('A')}</b> confirmed · <b>${nT('B')}</b> contested`, DATASET === 'live' ? 'Daily ETF windows, past events and the AI prior each measure the jump. Options and Hyperliquid readings come next; tier A needs two market readings that agree.' : 'Options, ETF windows and Hyperliquid’s weekend perps each measure the jump. The engine checks that they agree.'],
    ['Graph', tw.knock ? html`<b>${S.links.length}</b> knock-on links` : html`Knock-ons <b>off</b>`, 'Some events make others likelier. Their effects are added along the links.'],
    ['Simulate', html`<b>${sim.n.toLocaleString('en-US')}</b> runs · ${Math.max(1, Math.round(sim.ms || 0))} ms`, 'Joint scenarios over the event graph, at market odds.'],
  ];
  return html`<ol className="pipe" aria-label="How the engine works">${steps.map(([t, v, tip], i) => html`<li key=${t} className="pipe-step" title=${tip}><span className="label"><span className="pipe-n">${String(i + 1).padStart(2, '0')}</span>${t}</span><span className="pipe-v">${v}</span></li>`)}</ol>`;
}

function WhatIfBanner({ S, P, whatIf, onClear, onOpen }) {
  const ids = Object.keys(whatIf); if (!ids.length) return null;
  const move = S.whatIfMove;
  const shifted = S.evs.filter(e => !e.resolved && Math.abs(e.shift) >= 0.005).sort((a, b) => Math.abs(b.shift) - Math.abs(a.shift));
  return html`<div className="whatif" role="status">
    <span className="pill pill-acc">What if</span>
    <p>Assuming ${ids.map((id, i) => html`<${Frag} key=${id}>${i ? ' and ' : ''}<button className="inl" onClick=${() => onOpen(id)}>${S.byId[id].s}</button> ${whatIf[id] === 'yes' ? 'happens' : 'doesn’t happen'}<//>`)}, the portfolio moves <b className=${tone(move)}>${fmtUsd(move * P.V / 100, true)} (${fmtPct(move)})</b> as markets reprice${shifted.length ? html`, and ${shifted.map((e, i) => html`<${Frag} key=${e.id}>${i ? (i === shifted.length - 1 ? ' and ' : ', ') : ''}<button className="inl" onClick=${() => onOpen(e.id)}>${e.s}</button> odds go ${fmtOdds(e.q0)} → ${fmtOdds(e.qEff)}<//>`)}` : ''}.</p>
    <button className="btn-ghost" onClick=${onClear}>Clear</button>
  </div>`;
}

const tierBg = t => (t === 'B' ? 'repeating-linear-gradient(135deg, var(--tB) 0 3px, color-mix(in srgb, var(--tB) 55%, var(--surface)) 3px 5px)' : 'var(--t' + t + ')');

function Tiles({ P, sim, shown, onOpen, tw }) {
  const V = P.V; const top = shown[0];
  const counts = { A: 0, B: 0, C: 0, D: 0 }; shown.forEach(e => { counts[e.tier]++; });
  return html`<div className="tiles">
    <div className="tile"><span className="label">Event swing</span><div className="tile-v">±${fmtUsd(sim.sd * V / 100)}</div><div className="tile-s">${swingPct(sim.sd)} · one standard deviation of what these events could do by ${horizonEnd(tw.horizon)}</div></div>
    <div className="tile"><span className="label">Worst 1 in 20</span><div className=${cls('tile-v', tone(sim.p5))}>${fmtUsd(sim.p5 * V / 100, true)}</div><div className="tile-s">${fmtPct(sim.p5)} · the worst 5% average ${fmtPct(sim.es)}</div></div>
    ${top ? html`<button className="tile" onClick=${() => onOpen(top.id)}><span className="label">Biggest risk</span><div className="tile-v small">${top.s}</div><div className="tile-s">${swingUsd(top.swingPct * V / 100)} swing · ${fmtOdds(top.qEff)} odds · ${fmtDate(top.date, false)}</div></button>`
      : html`<div className="tile"><span className="label">Biggest risk</span><div className="tile-v small">Nothing in range</div></div>`}
    <div className="tile"><span className="label">Market-confirmed</span><div className="tile-v">${counts.A} of ${shown.length}</div>
      <div className="mix" aria-hidden="true">${['A', 'B', 'C', 'D'].map(t => (counts[t] ? html`<i key=${t} title=${TIERS[t].name + ': ' + counts[t]} style=${{ flex: counts[t], background: tierBg(t) }}></i>` : null))}</div>
      <div className="tile-s">events where two or more markets agree on the jump</div></div>
  </div>`;
}

function TierDot({ t, color = 'var(--ink-2)' }) {
  return html`<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" style=${{ verticalAlign: '-1px', marginRight: 5, flex: 'none' }}>
    ${(t === 'A' || t === 'B') && html`<circle cx="6" cy="6" r="5" style=${{ fill: color }} />`}
    ${t === 'B' && html`<circle cx="6" cy="6" r="2.6" style=${{ fill: 'none', stroke: 'var(--surface)', strokeWidth: 1.3 }} />`}
    ${t === 'C' && html`<circle cx="6" cy="6" r="4.3" style=${{ fill: 'none', stroke: color, strokeWidth: 1.6 }} />`}
    ${t === 'D' && html`<circle cx="6" cy="6" r="4.3" style=${{ fill: 'none', stroke: color, strokeWidth: 1.4, strokeDasharray: '2 1.8' }} />`}
  </svg>`;
}

function MapLegend({ view }) {
  return html`<div className="legend">
    <span><i className="sw" style=${{ background: 'var(--loss)' }}></i>Hurts you</span>
    <span><i className="sw" style=${{ background: 'var(--gain)' }}></i>Helps you</span>
    <span>Size is swing</span>
    ${['A', 'B', 'C', 'D'].map(t => html`<span key=${t} className="lg-t"><${TierDot} t=${t} />${TIERS[t].name}</span>`)}
    ${view !== 'flat' && html`<span><i className="arrow"></i>Your portfolio</span>`}
  </div>`;
}

/* ---------- the 3D map: a small canvas renderer with orbit, hover and pick ---------- */
const VIEW0 = { yaw: -0.62, pitch: 0.36 };
const MAP_L = 1.15;
function dotShape(ctx, x, y, r, tier, col, surf) {
  ctx.setLineDash([]);
  if (tier === 'A' || tier === 'B') {
    ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x, y, r, 0, 6.2832); ctx.fill();
    ctx.strokeStyle = surf; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, r + 0.75, 0, 6.2832); ctx.stroke();
    if (tier === 'B') { ctx.lineWidth = Math.max(1.2, r * 0.18); ctx.beginPath(); ctx.arc(x, y, r * 0.52, 0, 6.2832); ctx.stroke(); }
  } else {
    ctx.fillStyle = rgba(col, 0.14); ctx.beginPath(); ctx.arc(x, y, r, 0, 6.2832); ctx.fill();
    ctx.strokeStyle = col; ctx.lineWidth = 1.8; if (tier === 'D') ctx.setLineDash([3, 2.5]);
    ctx.beginPath(); ctx.arc(x, y, Math.max(1, r - 0.9), 0, 6.2832); ctx.stroke(); ctx.setLineDash([]);
  }
}

function Map3D({ evs, geo, P, tw, hover, setHover, sel, onOpen, ripple }) {
  const wrapRef = useRef(null), cvRef = useRef(null), tipRef = useRef(null), stRef = useRef(null);
  if (!stRef.current) stRef.current = { yaw: VIEW0.yaw, pitch: VIEW0.pitch, drag: null, last: -1e9, cur: {}, arrow: null, pts: [], dirty: true, w: 0, h: 0, dpr: 1, col: null, frame: 0, hoverFromMap: false };
  const live = useRef({}); live.current = { evs, geo, tw, hover, sel, ripple, setHover, onOpen };
  useEffect(() => { stRef.current.dirty = true; }, [evs, geo, hover, sel, tw.labels, tw.links, tw.rotate, ripple]);

  useEffect(() => {
    const st = stRef.current, cv = cvRef.current, wrap = wrapRef.current, ctx = cv.getContext('2d');
    let raf = 0, visible = true;
    const readCol = () => {
      const cs = getComputedStyle(wrap); const g = n => cs.getPropertyValue(n).trim();
      st.col = { ink: g('--ink'), ink2: g('--ink-2'), ink3: g('--ink-3'), line: g('--line'), line2: g('--line-2'), surface: g('--surface'), bg2: g('--surface-2'), accent: g('--accent'), loss: g('--loss'), gain: g('--gain') };
      st.dirty = true;
    };
    const resize = () => {
      const r = wrap.getBoundingClientRect(); st.dpr = Math.min(2, window.devicePixelRatio || 1); st.w = r.width; st.h = r.height;
      cv.width = Math.max(1, Math.round(r.width * st.dpr)); cv.height = Math.max(1, Math.round(r.height * st.dpr)); st.dirty = true;
    };
    readCol(); resize();
    const ro = new ResizeObserver(resize); ro.observe(wrap);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { st.dirty = true; });

    const project = (x, y, z) => {
      const cy = Math.cos(st.yaw), sy = Math.sin(st.yaw), cp = Math.cos(st.pitch), sp = Math.sin(st.pitch);
      const X = x * cy - z * sy, Z = x * sy + z * cy;
      const Y2 = y * cp + Z * sp, Z2 = -y * sp + Z * cp;
      const f = 8 / (8 + Z2); const base = Math.min(st.w * 0.28, st.h * 0.26);
      return [st.w / 2 + X * base * f, st.h * 0.45 - Y2 * base * f, Z2, f];
    };
    const label = (text, x, y, color, halo, font) => {
      ctx.font = font; ctx.lineJoin = 'round'; ctx.lineWidth = 3; ctx.strokeStyle = halo; ctx.strokeText(text, x, y); ctx.fillStyle = color; ctx.fillText(text, x, y);
    };

    const draw = now => {
      const { evs, geo, tw, hover, sel, ripple } = live.current; const c = st.col; const W = st.w, H = st.h, L = MAP_L;
      ctx.setTransform(st.dpr, 0, 0, st.dpr, 0, 0); ctx.clearRect(0, 0, W, H);
      if ('letterSpacing' in ctx) ctx.letterSpacing = '0px';
      // floor grid
      ctx.lineWidth = 1; ctx.strokeStyle = c.line; ctx.setLineDash([]); ctx.beginPath();
      for (let i = -2; i <= 2; i++) {
        const g = i * L / 2;
        let a = project(g, -L, -L), b = project(g, -L, L); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
        a = project(-L, -L, g); b = project(L, -L, g); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
      }
      ctx.stroke();
      // axes through the origin, with end labels
      const ends = geo.ends; const axes = [[[1, 0, 0], ends.x], [[0, 1, 0], ends.y], [[0, 0, 1], ends.z]];
      ctx.strokeStyle = rgba(c.ink3, 0.5);
      axes.forEach(([d]) => { const a = project(-d[0] * L, -d[1] * L, -d[2] * L), b = project(d[0] * L, d[1] * L, d[2] * L); ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); });
      ctx.textBaseline = 'middle'; ctx.font = '500 9.5px "Geist Mono", ui-monospace, monospace';
      if ('letterSpacing' in ctx) ctx.letterSpacing = '0.5px';
      const axisBoxes = [];
      axes.forEach(([d, lab]) => [-1, 1].forEach((s, j) => {
        const p = project(d[0] * L * 1.13 * s, d[1] * L * 1.13 * s, d[2] * L * 1.13 * s);
        const t = lab[j].toUpperCase(); const w = ctx.measureText(t).width;
        const x = clamp(p[0] - w / 2, 6, W - w - 6), y = clamp(p[1], 10, H - 10);
        ctx.fillStyle = rgba(c.ink3, 0.95); ctx.fillText(t, x, y); axisBoxes.push([x - 2, y - 7, w + 4, 14]);
      }));
      if ('letterSpacing' in ctx) ctx.letterSpacing = '0px';
      // points
      const pts = evs.map(e => {
        const p = st.cur[e.id] || geo.pos[e.id] || [0, 0, 0]; const s = project(p[0], p[1], p[2]); const fl = project(p[0], -L, p[2]);
        return { e, x: s[0], y: s[1], z: s[2], f: s[3], fx: fl[0], fy: fl[1] };
      });
      const maxSw = Math.max(1e-6, ...evs.map(e => e.swingPct));
      pts.forEach(p => { p.r = (4 + 10 * Math.sqrt(p.e.swingPct / maxSw)) * p.f; });
      st.pts = pts;
      ctx.setLineDash([2, 3]); ctx.strokeStyle = rgba(c.ink3, 0.32); ctx.lineWidth = 1;
      pts.forEach(p => { ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p.fx, p.fy); ctx.stroke(); });
      ctx.setLineDash([]); ctx.fillStyle = rgba(c.ink3, 0.16);
      pts.forEach(p => { ctx.beginPath(); ctx.ellipse(p.fx, p.fy, p.r * 0.75, p.r * 0.28, 0, 0, 6.2832); ctx.fill(); });
      // knock-on links
      if (tw.links) {
        const byId = {}; pts.forEach(p => { byId[p.e.id] = p; });
        pts.forEach(p => p.e.children.forEach(ch => {
          const q = byId[ch.id]; if (!q) return;
          const hl = hover === p.e.id || hover === ch.id || sel === p.e.id || sel === ch.id;
          const mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2 - 22;
          ctx.strokeStyle = rgba(hl ? c.accent : c.ink3, hl ? 0.95 : 0.42); ctx.lineWidth = hl ? 1.6 : 1; ctx.setLineDash(ch.w < 0 ? [4, 3] : []);
          ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.quadraticCurveTo(mx, my, q.x, q.y); ctx.stroke(); ctx.setLineDash([]);
          const ang = Math.atan2(q.y - my, q.x - mx); const tx = q.x - Math.cos(ang) * (q.r + 2), ty = q.y - Math.sin(ang) * (q.r + 2);
          ctx.fillStyle = ctx.strokeStyle; ctx.beginPath(); ctx.moveTo(tx, ty);
          ctx.lineTo(tx - Math.cos(ang - 0.42) * 7, ty - Math.sin(ang - 0.42) * 7); ctx.lineTo(tx - Math.cos(ang + 0.42) * 7, ty - Math.sin(ang + 0.42) * 7); ctx.closePath(); ctx.fill();
        }));
      }
      // the portfolio arrow
      const A = st.arrow || geo.arrow; const o = project(0, 0, 0), tip = project(A[0] * L, A[1] * L, A[2] * L);
      ctx.strokeStyle = c.accent; ctx.lineWidth = 2.5; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(o[0], o[1]); ctx.lineTo(tip[0], tip[1]); ctx.stroke(); ctx.lineCap = 'butt';
      const ang = Math.atan2(tip[1] - o[1], tip[0] - o[0]);
      ctx.fillStyle = c.accent; ctx.beginPath(); ctx.moveTo(tip[0] + Math.cos(ang) * 5, tip[1] + Math.sin(ang) * 5);
      ctx.lineTo(tip[0] - Math.cos(ang - 0.45) * 11, tip[1] - Math.sin(ang - 0.45) * 11); ctx.lineTo(tip[0] - Math.cos(ang + 0.45) * 11, tip[1] - Math.sin(ang + 0.45) * 11); ctx.closePath(); ctx.fill();
      ctx.beginPath(); ctx.arc(o[0], o[1], 3, 0, 6.2832); ctx.fill();
      // dots, far to near
      const zs = pts.map(p => p.z); const zMin = Math.min(...zs, 0), zMax = Math.max(...zs, 0.0001);
      pts.slice().sort((a, b) => b.z - a.z).forEach(p => {
        const e = p.e; const isH = hover === e.id, isS = sel === e.id;
        ctx.globalAlpha = isH || isS ? 1 : 0.5 + 0.5 * (zMax - p.z) / Math.max(1e-6, zMax - zMin);
        dotShape(ctx, p.x, p.y, p.r, e.tier, e.dT < 0 ? c.loss : c.gain, c.bg2);
        ctx.globalAlpha = 1;
        if (isH || isS) { ctx.strokeStyle = c.accent; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(p.x, p.y, p.r + 4, 0, 6.2832); ctx.stroke(); }
      });
      // "Your portfolio" tag at the arrow tip
      ctx.textBaseline = 'middle';
      const tag = 'Your portfolio'; ctx.font = '600 11.5px Geist, system-ui, sans-serif'; const tw0 = ctx.measureText(tag).width;
      // Try above the tip on the side the arrow points to, then below it, then the other side, so it never covers an axis name.
      const sides = Math.cos(ang) >= 0 ? [1, -1] : [-1, 1];
      const tagTries = [[sides[0], -1], [sides[0], 1], [sides[1], -1], [sides[1], 1]].map(([sx, sy]) =>
        [clamp(sx > 0 ? tip[0] + 10 : tip[0] - 10 - tw0, 6, W - tw0 - 6), clamp(tip[1] + sy * 12, 10, H - 10)]);
      const onAxis = b => axisBoxes.some(o => o[0] < b[0] + b[2] && b[0] < o[0] + o[2] && o[1] < b[1] + b[3] && b[1] < o[1] + o[3]);
      const [lx, ly] = tagTries.find(([x, y]) => !onAxis([x - 3, y - 8, tw0 + 6, 16])) || tagTries[0];
      label(tag, lx, ly, c.accent, c.bg2, '600 11.5px Geist, system-ui, sans-serif');
      // event labels: the top five by swing, plus whatever is hovered or selected
      const ranked = evs.slice().sort((a, b) => b.swing - a.swing);
      const want = tw.labels === 'none' ? [] : tw.labels === 'all' ? ranked : ranked.slice(0, 5);
      const ids = [hover, sel].concat(want.map(e => e.id)).filter((v, i, a) => v && a.indexOf(v) === i);
      // Labels avoid the portfolio tag, the axis names and each other: try the right of the dot, then the left.
      const boxes = [[lx - 3, ly - 8, tw0 + 6, 16]].concat(axisBoxes);
      const hits = b => boxes.some(o => o[0] < b[0] + b[2] && b[0] < o[0] + o[2] && o[1] < b[1] + b[3] && b[1] < o[1] + o[3]);
      ids.forEach(id => {
        const p = pts.find(q => q.e.id === id); if (!p) return;
        const forced = id === hover || id === sel; const font = (forced ? '600 ' : '500 ') + '11.5px Geist, system-ui, sans-serif';
        ctx.font = font; const t = p.e.s; const w = ctx.measureText(t).width;
        const y = clamp(p.y, 10, H - 10); const right = p.x + p.r + 6, left = p.x - p.r - 6 - w;
        const fits = x => x >= 6 && x + w <= W - 6;
        const tries = [right, left].filter(fits); if (!tries.length) tries.push(right + w > W - 6 ? left : right);
        let x = tries.find(x0 => !hits([x0 - 3, y - 8, w + 6, 16]));
        if (x == null) { if (!forced) return; x = tries[0]; }
        boxes.push([x - 3, y - 8, w + 6, 16]); label(t, x, y, forced ? c.ink : c.ink2, c.bg2, font);
      });
      // ripple on a live odds change
      if (ripple && now - ripple.t < 1400) {
        const p = pts.find(q => q.e.id === ripple.id);
        if (p) { const k = (now - ripple.t) / 1400; ctx.strokeStyle = rgba(c.accent, 1 - k); ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(p.x, p.y, p.r + 4 + k * 26, 0, 6.2832); ctx.stroke(); }
      }
      // tooltip follows the hovered dot
      const tipEl = tipRef.current;
      if (tipEl) {
        const p = hover && pts.find(q => q.e.id === hover);
        if (p) {
          const tw1 = tipEl.offsetWidth || 220, th = tipEl.offsetHeight || 96;
          let x = p.x + p.r + 14; if (x + tw1 > W - 8) x = p.x - p.r - 14 - tw1;
          const y = clamp(p.y - th / 2, 8, H - th - 8);
          tipEl.style.transform = 'translate(' + Math.round(Math.max(8, x)) + 'px,' + Math.round(y) + 'px)'; tipEl.classList.add('show');
        } else tipEl.classList.remove('show');
      }
    };

    const loop = now => {
      raf = 0; if (!visible) return;
      const L0 = live.current; st.frame++;
      if (st.frame % 60 === 0) readCol();
      let moving = false;
      if (L0.tw.rotate && !REDUCED && !st.drag && !L0.hover && now - st.last > 2600) { st.yaw += 0.0019; moving = true; }
      for (const e of L0.evs) {
        const tg = L0.geo.pos[e.id]; if (!tg) continue;
        let cu = st.cur[e.id];
        if (!cu) { cu = st.cur[e.id] = REDUCED ? tg.slice() : [0, 0, 0]; }
        for (let i = 0; i < 3; i++) { const d = tg[i] - cu[i]; if (Math.abs(d) > 1e-4) { cu[i] += d * 0.12; moving = true; } else cu[i] = tg[i]; }
      }
      const ta = L0.geo.arrow;
      if (!st.arrow) st.arrow = ta.slice();
      else for (let i = 0; i < 3; i++) { const d = ta[i] - st.arrow[i]; if (Math.abs(d) > 1e-4) { st.arrow[i] += d * 0.12; moving = true; } else st.arrow[i] = ta[i]; }
      if (L0.ripple && now - L0.ripple.t < 1500) moving = true;
      if (moving || st.dirty) { st.dirty = false; draw(now); }
      raf = requestAnimationFrame(loop);
    };
    const io = new IntersectionObserver(es => { visible = es[0].isIntersecting; if (visible && !raf) raf = requestAnimationFrame(loop); });
    io.observe(wrap);

    const hit = (x, y) => { let best = null, bd = 1e9; st.pts.forEach(p => { const d = Math.hypot(p.x - x, p.y - y); if (d < p.r + 6 && d < bd) { bd = d; best = p; } }); return best; };
    const pos = ev => { const r = cv.getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; };
    const onDown = ev => {
      if (ev.button != null && ev.button !== 0) return;
      const [x, y] = pos(ev); st.drag = { x, y, moved: 0 };
      try { cv.setPointerCapture(ev.pointerId); } catch (e) { /* capture is best effort */ }
    };
    const onMove = ev => {
      const [x, y] = pos(ev);
      if (st.drag) {
        const dx = x - st.drag.x, dy = y - st.drag.y; st.drag.x = x; st.drag.y = y; st.drag.moved += Math.abs(dx) + Math.abs(dy);
        if (st.drag.moved > 4) { st.yaw += dx * 0.0085; st.pitch = clamp(st.pitch + dy * 0.006, -0.05, 1.1); st.dirty = true; st.last = performance.now(); cv.style.cursor = 'grabbing'; }
        return;
      }
      const p = hit(x, y); const id = p ? p.e.id : null; cv.style.cursor = p ? 'pointer' : 'grab';
      if (id !== live.current.hover && (id || st.hoverFromMap)) { st.hoverFromMap = !!id; live.current.setHover(id); }
    };
    const onUp = ev => {
      const d = st.drag; st.drag = null; cv.style.cursor = 'grab'; st.last = performance.now();
      try { cv.releasePointerCapture(ev.pointerId); } catch (e) { /* already released */ }
      if (d && d.moved <= 4) { const [x, y] = pos(ev); const p = hit(x, y); if (p) live.current.onOpen(p.e.id); }
    };
    const onCancel = () => { st.drag = null; };
    const onLeave = () => { if (st.hoverFromMap && !st.drag) { st.hoverFromMap = false; live.current.setHover(null); } };
    cv.addEventListener('pointerdown', onDown); cv.addEventListener('pointermove', onMove); cv.addEventListener('pointerup', onUp);
    cv.addEventListener('pointercancel', onCancel); cv.addEventListener('pointerleave', onLeave);
    st.reset = () => { st.yaw = VIEW0.yaw; st.pitch = VIEW0.pitch; st.last = performance.now(); st.dirty = true; };
    return () => {
      cancelAnimationFrame(raf); ro.disconnect(); io.disconnect();
      cv.removeEventListener('pointerdown', onDown); cv.removeEventListener('pointermove', onMove); cv.removeEventListener('pointerup', onUp);
      cv.removeEventListener('pointercancel', onCancel); cv.removeEventListener('pointerleave', onLeave);
    };
  }, []);

  const hv = hover ? evs.find(e => e.id === hover) : null; const V = P.V;
  return html`<div className="map-wrap" ref=${wrapRef}>
    <canvas ref=${cvRef} role="img" aria-label=${'3D map of ' + evs.length + ' events. The ranked list has the same data.'}></canvas>
    <div className="map-tip" ref=${tipRef} aria-hidden="true">${hv && html`<${Frag}><b>${hv.t}</b>
      <div className="row"><span>Odds</span><span>${fmtOdds(hv.qEff)}</span></div>
      <div className="row"><span>If it happens</span><span>${fmtUsd(hv.ifYes * V / 100, true)}</span></div>
      <div className="row"><span>If not</span><span>${fmtUsd(hv.ifNo * V / 100, true)}</span></div>
      <div className="row"><span>Swing</span><span>${swingUsd(hv.swingPct * V / 100)}</span></div><//>`}</div>
    <span className="map-hint">Drag to turn · click a dot</span>
    <div className="map-tools"><button className="btn-ghost" onClick=${() => stRef.current.reset && stRef.current.reset()}>Reset view</button></div>
  </div>`;
}

/* ---------- the flat view: impact against odds, with equal-swing curves ---------- */
function SvgDot({ x, y, r, tier, color, hl }) {
  const solid = tier === 'A' || tier === 'B';
  return html`<g>
    ${solid ? html`<circle cx=${x} cy=${y} r=${r} style=${{ fill: color, stroke: 'var(--surface)', strokeWidth: 1.5 }} />`
      : html`<circle cx=${x} cy=${y} r=${Math.max(1, r - 0.9)} style=${{ fill: color, fillOpacity: 0.14, stroke: color, strokeWidth: 1.8, strokeDasharray: tier === 'D' ? '3 2.5' : 'none' }} />`}
    ${tier === 'B' && html`<circle cx=${x} cy=${y} r=${r * 0.52} style=${{ fill: 'none', stroke: 'var(--surface)', strokeWidth: Math.max(1.2, r * 0.18) }} />`}
    ${hl && html`<circle cx=${x} cy=${y} r=${r + 4} style=${{ fill: 'none', stroke: 'var(--accent)', strokeWidth: 2 }} />`}
  </g>`;
}

function Scatter2D({ evs, hover, setHover, sel, onOpen, tw }) {
  const ref = useRef(null); const W = Math.max(300, useWidth(ref, 640)), H = Math.round(clamp(W * 0.66, 300, 440)), m = { l: 44, r: 16, t: 14, b: 44 };
  const xm = Math.max(1, ...evs.map(e => Math.abs(e.dT))) * 1.12;
  const X = v => m.l + (v + xm) / (2 * xm) * (W - m.l - m.r);
  const Y = q => H - m.b - q * (H - m.t - m.b);
  const maxSw = Math.max(1e-6, ...evs.map(e => e.swingPct));
  const R = e => 4 + 11 * Math.sqrt(e.swingPct / maxSw);
  const ranked = evs.slice().sort((a, b) => b.swing - a.swing);
  const lab = new Set((tw.labels === 'none' ? [] : ranked.slice(0, tw.labels === 'all' ? 99 : 5)).map(e => e.id));
  const levels = ranked.length ? [ranked[0].swingPct, ranked[0].swingPct / 2] : [];
  const contour = (c, s) => {
    const pts = []; for (let i = 1; i < 200; i++) { const q = i / 200; const x = s * c / Math.sqrt(q * (1 - q)); if (Math.abs(x) <= xm) pts.push(X(x).toFixed(1) + ',' + Y(q).toFixed(1)); }
    return pts.length > 1 ? 'M' + pts.join('L') : '';
  };
  const ticks = niceTicks(-xm, xm, 6);
  return html`<div className="flat" ref=${ref}><svg viewBox=${'0 0 ' + W + ' ' + H} role="img" aria-label="Events by what they would do to you and by their odds">
    ${[0, 0.25, 0.5, 0.75, 1].map(q => html`<g key=${'q' + q}><line x1=${m.l} x2=${W - m.r} y1=${Y(q)} y2=${Y(q)} style=${{ stroke: 'var(--line)' }} /><text x=${m.l - 8} y=${Y(q) + 3} textAnchor="end" className="ax">${q * 100}%</text></g>`)}
    ${ticks.map(v => html`<text key=${'x' + v} x=${X(v)} y=${H - m.b + 16} textAnchor="middle" className="ax">${fmtPct(v, 0)}</text>`)}
    <line x1=${X(0)} x2=${X(0)} y1=${m.t} y2=${H - m.b} style=${{ stroke: 'var(--line-2)' }} />
    ${levels.map((c, i) => html`<g key=${'c' + i}><path d=${contour(c, -1)} style=${{ fill: 'none', stroke: 'var(--ink-3)', strokeDasharray: '3 4', opacity: 0.55 }} /><path d=${contour(c, 1)} style=${{ fill: 'none', stroke: 'var(--ink-3)', strokeDasharray: '3 4', opacity: 0.55 }} /></g>`)}
    <text x=${m.l + 4} y=${H - 8} className="ax">← hurts you</text>
    <text x=${W - m.r} y=${H - 8} textAnchor="end" className="ax">helps you →</text>
    <text x=${(m.l + W - m.r) / 2} y=${H - 8} textAnchor="middle" className="ax">full move if it happens, % of portfolio</text>
    <text x=${m.l + 4} y=${m.t + 10} className="ax">odds ↑</text>
    ${ranked.slice().reverse().map(e => html`<g key=${e.id} className="dot" onMouseEnter=${() => setHover(e.id)} onMouseLeave=${() => setHover(null)} onClick=${() => onOpen(e.id)}>
      <${SvgDot} x=${X(e.dT)} y=${Y(e.qEff)} r=${R(e)} tier=${e.tier} color=${e.dT < 0 ? 'var(--loss)' : 'var(--gain)'} hl=${hover === e.id || sel === e.id} /></g>`)}
    ${ranked.filter(e => lab.has(e.id) || e.id === hover).map(e => { const x = X(e.dT), right = x < W - 140; return html`<text key=${'l' + e.id} x=${right ? x + R(e) + 6 : x - R(e) - 6} y=${Y(e.qEff) + 4} textAnchor=${right ? 'start' : 'end'} className=${cls('lbl', e.id === hover && 'on')}>${e.s}</text>`; })}
  </svg></div>`;
}

function MapCard({ shown, P, tw, setTw, hover, setHover, sel, onOpen, ripple }) {
  const view = tw.view;
  const geo = useMemo(() => (view === 'flat' ? null : mapGeometry(shown, P, view)), [shown, P, view]);
  const titles = { macro: 'Events in macro space', mine: 'Events on your portfolio’s own axes', flat: 'What each event does to you, by its odds' };
  const subs = {
    macro: 'Each dot is an event, placed by what it does to growth, risk appetite and inflation. The arrow is which way your portfolio leans, and colour shows what each event does to you exactly.',
    mine: 'Left to right is exactly what each event does to you. The other two axes are the directions your portfolio is blind to, found from the events themselves.',
    flat: 'Events far from the middle line and near 50% odds swing you the most. The dashed curves mark equal swing.',
  };
  return html`<section className="card map-card" aria-labelledby="map-h">
    <div className="card-head">
      <div><span className="kicker">Event map</span><h3 className="h3" id="map-h" style=${{ marginTop: 6 }}>${titles[view]}</h3><p className="sub">${subs[view]}</p></div>
      <div className="seg" role="group" aria-label="Map view">${[['macro', 'Macro axes'], ['mine', 'My view'], ['flat', '2D']].map(([k, l]) => html`<button key=${k} className=${view === k ? 'on' : ''} aria-pressed=${view === k} onClick=${() => setTw({ ...tw, view: k })}>${l}</button>`)}</div>
    </div>
    ${view === 'flat' ? html`<${Scatter2D} evs=${shown} hover=${hover} setHover=${setHover} sel=${sel} onOpen=${onOpen} tw=${tw} />`
      : html`<${Map3D} evs=${shown} geo=${geo} P=${P} tw=${tw} hover=${hover} setHover=${setHover} sel=${sel} onOpen=${onOpen} ripple=${ripple} />`}
    <${MapLegend} view=${view} />
  </section>`;
}

/* ---------- the ranked list ---------- */
function EventList({ shown, beyond, P, tw, setTw, hover, setHover, sel, onOpen }) {
  const [sort, setSort] = useState('swing');
  const sorted = useMemo(() => {
    const a = shown.slice();
    if (sort === 'date') a.sort((x, y) => parseDate(x.date) - parseDate(y.date) || y.swing - x.swing);
    else if (sort === 'hit') a.sort((x, y) => x.ifYes - y.ifYes);
    return a;
  }, [shown, sort]);
  const rank = useMemo(() => Object.fromEntries(shown.map((e, i) => [e.id, i + 1])), [shown]);
  const n = tw.topN === 'all' ? sorted.length : Math.min(tw.topN, sorted.length);
  const list = sorted.slice(0, n); const V = P.V;
  return html`<section className="card list-card" aria-labelledby="ev-h">
    <div className="card-head">
      <div><span className="kicker">Ranked by swing</span><h3 className="h3" id="ev-h" style=${{ marginTop: 6 }}>Events that could move you</h3>
        <p className="sub">What each event would do to you, net of what its odds already price in. Swing is how far it could move you either way.</p></div>
      <div className="seg" role="group" aria-label="Sort events">${[['swing', 'Swing'], ['hit', 'Worst hit'], ['date', 'Soonest']].map(([k, l]) => html`<button key=${k} className=${sort === k ? 'on' : ''} aria-pressed=${sort === k} onClick=${() => setSort(k)}>${l}</button>`)}</div>
    </div>
    <div className="ev-head label" aria-hidden="true"><span>#</span><span>Event</span><span className="r">Odds</span><span className="r">If it happens</span><span className="r ev-c-no">If not</span><span className="r">Swing</span><span>Tier</span></div>
    <ul className="ev-list">${list.map(e => html`<li key=${e.id}><button className=${cls('ev-row', hover === e.id && 'hov', sel === e.id && 'sel')} onMouseEnter=${() => setHover(e.id)} onMouseLeave=${() => setHover(null)} onFocus=${() => setHover(e.id)} onBlur=${() => setHover(null)} onClick=${() => onOpen(e.id)}>
      <span className="ev-rank mono">${rank[e.id]}</span>
      <span className="ev-main"><span className="ev-title">${e.t}</span><span className="ev-meta mono">${fmtDate(e.date, false)} · ${e.days} days · ${e.cat}</span></span>
      <span className="cell">${fmtOdds(e.qEff)}<span className="oddsbar"><i style=${{ width: (e.qEff * 100).toFixed(1) + '%' }}></i></span></span>
      <span className=${cls('cell', tone(e.ifYes))}>${fmtUsd(e.ifYes * V / 100, true)}<small>${fmtPct(e.ifYes)}</small></span>
      <span className=${cls('cell ev-c-no', tone(e.ifNo))}>${fmtUsd(e.ifNo * V / 100, true)}<small>${fmtPct(e.ifNo)}</small></span>
      <span className="cell">${swingUsd(e.swingPct * V / 100)}<small>${swingPct(e.swingPct)}</small></span>
      <span className=${'tier t' + e.tier} title=${TIERS[e.tier].name}><b>${e.tier}</b><span>${TIERS[e.tier].name}</span></span>
    </button></li>`)}</ul>
    <p className="list-foot sub">Showing ${list.length} of ${shown.length} events in the next ${tw.horizon} months.${beyond === 1 ? ' One more resolves after that, so the simulation reprices it instead of resolving it.' : beyond > 1 ? ' ' + beyond + ' more resolve after that, so the simulation reprices them instead of resolving them.' : ''}${list.length < shown.length ? html` <button className="link" onClick=${() => setTw({ ...tw, topN: 'all' })}>Show all ${shown.length}</button>` : ''}</p>
  </section>`;
}

/* ---------- simulated outcomes ---------- */
function OutcomeCard({ sim, P, S, tw, onOpen }) {
  const V = P.V; const { lo, hi, bw, bins } = sim.hist; const mx = Math.max(1, ...bins);
  const hRef = useRef(null); const W = Math.max(300, useWidth(hRef, 600)), H = 180, m = { l: 8, r: 8, t: 14, b: 26 };
  const X = v => m.l + (v - lo) / (hi - lo) * (W - m.l - m.r);
  const bh = c => c / mx * (H - m.t - m.b);
  const ticks = niceTicks(lo, hi, 6); const dp = ticks.length > 1 && Math.abs(ticks[1] - ticks[0]) < 1 ? 1 : 0;
  const drivers = sim.drivers.map(d => {
    const yes = d.tail >= d.base; const fb = yes ? d.base : 1 - d.base, ft = yes ? d.tail : 1 - d.tail;
    return { ...d, yes, fb, ft, lift: ft - fb };
  }).filter(d => d.lift > 0.03).sort((a, b) => b.lift - a.lift).slice(0, 3);
  return html`<section className="card" aria-labelledby="oc-h">
    <div className="card-head"><div><span className="kicker">Simulation · ${sim.n.toLocaleString('en-US')} joint scenarios</span><h3 className="h3" id="oc-h" style=${{ marginTop: 6 }}>Range of outcomes by ${horizonEnd(tw.horizon)}</h3>
      <p className="sub">Events are drawn together over the graph, so linked ones tend to happen together. ${tw.noise ? 'Includes everyday market noise.' : 'Event moves only, no everyday noise.'}</p></div></div>
    <div className="hist" ref=${hRef}><svg viewBox=${'0 0 ' + W + ' ' + H} role="img" aria-label=${'Histogram of outcomes. A 1 in 20 chance of ' + fmtPct(sim.p5) + ' or worse.'}>
      ${bins.map((c, i) => { const x0 = lo + i * bw; const tail = x0 + bw / 2 <= sim.p5; return c ? html`<rect key=${i} x=${X(x0) + 0.5} y=${H - m.b - bh(c)} width=${Math.max(0.6, X(x0 + bw) - X(x0) - 1)} height=${bh(c)} rx="1" style=${{ fill: tail ? 'var(--loss)' : 'var(--line-2)' }} />` : null; })}
      <line x1=${m.l} x2=${W - m.r} y1=${H - m.b} y2=${H - m.b} style=${{ stroke: 'var(--line-2)' }} />
      ${ticks.map(v => html`<text key=${'t' + v} x=${X(v)} y=${H - 8} textAnchor="middle" className="ax">${fmtPct(v, dp)}</text>`)}
      ${lo < 0 && hi > 0 && html`<line x1=${X(0)} x2=${X(0)} y1=${m.t} y2=${H - m.b} style=${{ stroke: 'var(--ink-3)', strokeDasharray: '2 3' }} />`}
      <line x1=${X(sim.p5)} x2=${X(sim.p5)} y1=${m.t} y2=${H - m.b} style=${{ stroke: 'var(--loss)', strokeWidth: 1.5 }} />
      <text x=${X(sim.p5) < 70 ? X(sim.p5) + 5 : X(sim.p5) - 5} y=${m.t + 8} textAnchor=${X(sim.p5) < 70 ? 'start' : 'end'} className="ax">1 in 20</text>
    </svg></div>
    <div className="stats">
      <div className="stat"><span className="label">Median</span><span className=${cls('v', tone(sim.p50))}>${fmtUsd(sim.p50 * V / 100, true)}</span></div>
      <div className="stat"><span className="label">Worst 1 in 20</span><span className=${cls('v', tone(sim.p5))}>${fmtUsd(sim.p5 * V / 100, true)}</span></div>
      <div className="stat"><span className="label">Best 1 in 20</span><span className=${cls('v', tone(sim.p95))}>${fmtUsd(sim.p95 * V / 100, true)}</span></div>
      <div className="stat"><span className="label">Worst 5% average</span><span className=${cls('v', tone(sim.es))}>${fmtUsd(sim.es * V / 100, true)}</span></div>
    </div>
    ${drivers.length > 0 && html`<ul className="drivers"><li className="label">What the worst 5% have in common</li>${drivers.map(d => html`<li key=${d.id}>
      <button className="inl" onClick=${() => onOpen(d.id)}>${S.byId[d.id].s}</button> ${d.yes ? 'happens' : 'doesn’t happen'} in <b>${pct0(d.ft)}</b> of the worst 5%, against ${pct0(d.fb)} overall.
      <div className="drv-bars" aria-hidden="true"><div className="drv-bar"><i style=${{ width: d.fb * 100 + '%', background: 'var(--ink-3)' }}></i></div><div className="drv-bar"><i style=${{ width: d.ft * 100 + '%', background: 'var(--loss)' }}></i></div></div></li>`)}</ul>`}
    <p className="note">${tw.odds === 'calibrated' ? 'With calibrated odds the average is ' + fmtPct(sim.mean, 2) + '. That is the engine’s edge over market prices, a hint rather than a forecast.' : 'The average is about zero (' + fmtPct(sim.mean, 2) + ') because market odds are already in today’s prices. What is left is the swing.'}</p>
  </section>`;
}

/* ---------- live feed / overnight changes ---------- */
function FeedCard({ live, setLive, feed, S, P, onOpen, hasLive, resetLive }) {
  const V = P.V;
  const overnight = useMemo(() => S.evs.filter(e => e.hist && e.inH && !e.resolved)
    .map(e => ({ e, dq: e.q0 - e.hist.y, usd: (e.q0 - e.hist.y) * e.dT * V / 100 }))
    .filter(x => Math.abs(x.dq) >= 0.005).sort((a, b) => Math.abs(b.usd) - Math.abs(a.usd)).slice(0, 6), [S, V]);
  return html`<section className="card" aria-labelledby="fd-h">
    <div className="card-head">
      <div><span className="kicker">${live ? 'Live · simulated odds' : 'Since yesterday'}</span><h3 className="h3" id="fd-h" style=${{ marginTop: 6 }}>${live ? 'Odds moving now' : 'What moved overnight'}</h3>
        <p className="sub">${live ? 'Each odds change is priced through your portfolio the moment it happens: the change in odds times what the event does to you.' : 'Overnight odds changes, priced for your portfolio: the change in odds times what the event does to you.'}</p></div>
      <button className=${cls('btn-ghost', live && 'on')} onClick=${() => setLive(!live)} aria-pressed=${live}><i className="dot-live"></i>${live ? 'Pause' : 'Go live'}</button>
    </div>
    ${live ? (feed.length ? html`<ul className="feed" aria-live="polite">${feed.slice(0, 8).map(f => html`<li key=${f.k}>
        <button className="inl f-t" onClick=${() => onOpen(f.id)}>${S.byId[f.id].s}</button><span className=${cls('f-v', tone(f.usd))}>${fmtUsd(f.usd, true)}</span>
        <span className="f-m">${f.time} · odds ${fmtOdds(f.q0)} → ${fmtOdds(f.q1)}</span></li>`)}</ul>` : html`<p className="sub">Waiting for the first odds change…</p>`)
      : html`<ul className="feed">${overnight.map(x => html`<li key=${x.e.id}>
        <button className="inl f-t" onClick=${() => onOpen(x.e.id)}>${x.e.s}</button><span className=${cls('f-v', tone(x.usd))}>${fmtUsd(x.usd, true)}</span>
        <span className="f-m">odds ${fmtOdds(x.e.hist.y)} → ${fmtOdds(x.e.q0)} (${fmtPts(x.dq)})</span></li>`)}</ul>`}
    ${!live && hasLive && html`<p className="list-foot sub" style=${{ marginLeft: 0 }}>Odds include your live session. <button className="link" onClick=${resetLive}>Back to snapshot odds</button></p>`}
  </section>`;
}

function AnalysisView(p) {
  const { P, S, sim, shown, beyond, tw, setTw, hover, setHover, sel, onOpen, whatIf, setWhatIf, live, setLive, feed, ripple, hasLive, resetLive } = p;
  return html`<${Frag}>
    <${Pipeline} P=${P} S=${S} sim=${sim} tw=${tw} />
    <${WhatIfBanner} S=${S} P=${P} whatIf=${whatIf} onClear=${() => setWhatIf({})} onOpen=${onOpen} />
    <${Tiles} P=${P} sim=${sim} shown=${shown} onOpen=${onOpen} tw=${tw} />
    <div className="duo">
      <${MapCard} shown=${shown} P=${P} tw=${tw} setTw=${setTw} hover=${hover} setHover=${setHover} sel=${sel} onOpen=${onOpen} ripple=${ripple} />
      <${EventList} shown=${shown} beyond=${beyond} P=${P} tw=${tw} setTw=${setTw} hover=${hover} setHover=${setHover} sel=${sel} onOpen=${onOpen} />
    </div>
    <div className="duo">
      <${OutcomeCard} sim=${sim} P=${P} S=${S} tw=${tw} onOpen=${onOpen} />
      <${FeedCard} live=${live} setLive=${setLive} feed=${feed} S=${S} P=${P} onOpen=${onOpen} hasLive=${hasLive} resetLive=${resetLive} />
    </div>
  <//>`;
}
