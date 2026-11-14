// Draws docs/diagrams/architecture.svg and .png in a hand-drawn style with rough.js and the Virgil font
// (Excalidraw's original hand-drawn face, OFL). Usage, from docs/diagrams: npm i roughjs@4.6.6 && node architecture.js architecture
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
const D = __dirname;
const out = process.argv[2];
(async () => {
  const b = await chromium.launch();
  const pg = await b.newPage({ deviceScaleFactor: 2 });
  await pg.setContent('<html><body style="margin:0;background:#fff"></body></html>');
  await pg.addScriptTag({ path: require.resolve('roughjs/bundled/rough.js') });
  const font = fs.readFileSync(D + '/fonts/Virgil-Regular.woff2').toString('base64');
  const svg = await pg.evaluate(async (font) => {
    const W = 1720, H = 1300, NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('xmlns', NS); svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('width', W); svg.setAttribute('height', H);
    svg.innerHTML = `<defs><style>@font-face{font-family:"Virgil";src:url(data:font/woff2;base64,${font}) format("woff2")}text{font-family:"Virgil","Segoe Print","Comic Sans MS",cursive}</style></defs>`;
    document.body.appendChild(svg);
    await document.fonts.load('20px Virgil');
    const ff = new FontFace('Virgil', `url(data:font/woff2;base64,${font})`); await ff.load(); document.fonts.add(ff);
    const rc = rough.svg(svg); let seed = 7;
    const opt = o => ({ roughness: 1.1, bowing: 1, strokeWidth: 2, stroke: '#1e1e1e', seed: seed++, ...o });
    const bg = rc.rectangle(0, 0, W, H, { fill: '#ffffff', fillStyle: 'solid', stroke: 'none', roughness: 0 }); svg.appendChild(bg);
    const add = n => (svg.appendChild(n), n);
    const rr = (x, y, w, h, r) => `M${x + r} ${y} H${x + w - r} Q${x + w} ${y} ${x + w} ${y + r} V${y + h - r} Q${x + w} ${y + h} ${x + w - r} ${y + h} H${x + r} Q${x} ${y + h} ${x} ${y + h - r} V${y + r} Q${x} ${y} ${x + r} ${y} Z`;
    const box = (x, y, w, h, o = {}) => add(rc.path(rr(x, y, w, h, o.r ?? 18), opt({ fill: o.fill, fillStyle: 'solid', stroke: o.stroke || '#1e1e1e', strokeLineDash: o.dash, strokeWidth: o.sw ?? 2, roughness: o.rough ?? 1.1 })));
    const text = (x, y, lines, o = {}) => {
      const t = document.createElementNS(NS, 'text'); t.setAttribute('x', x); t.setAttribute('y', y);
      t.setAttribute('text-anchor', o.anchor || 'middle'); t.setAttribute('font-size', o.size || 19); t.setAttribute('fill', o.color || '#1e1e1e');
      const lh = (o.size || 19) * 1.32, n = lines.length, y0 = o.top ? 0 : -((n - 1) * lh) / 2;
      lines.forEach((s, i) => { const sp = document.createElementNS(NS, 'tspan'); sp.setAttribute('x', x); sp.setAttribute('dy', i === 0 ? y0 + (o.size || 19) * 0.35 : lh); sp.textContent = s; t.appendChild(sp); });
      return add(t);
    };
    const node = (x, y, w, h, lines, o = {}) => { box(x, y, w, h, o); text(x + w / 2, y + h / 2, lines, { size: o.size, color: o.color }); };
    const arrow = (pts, o = {}) => {
      add(rc.curve(pts, opt({ strokeWidth: 2, roughness: 0.8, strokeLineDash: o.dash, stroke: o.color || '#1e1e1e' })));
      const [p, q] = [pts[pts.length - 2], pts[pts.length - 1]], a = Math.atan2(q[1] - p[1], q[0] - p[0]), L = 16;
      for (const s of [-1, 1]) add(rc.line(q[0], q[1], q[0] - L * Math.cos(a + s * 0.45), q[1] - L * Math.sin(a + s * 0.45), opt({ roughness: 0.6, stroke: o.color || '#1e1e1e' })));
      if (o.label) text(o.lx, o.ly, [o.label], { size: 17, color: o.color || '#1e1e1e' });
    };
    const panel = (x, y, w, h, title, fill, stroke, color) => { box(x, y, w, h, { fill, stroke, sw: 1.5, r: 26, rough: 0.7 }); text(x + 30, y + 40, [title], { anchor: 'start', size: 27, color }); };
    const RED = '#c92a2a', GREEN = '#2b8a3e', BLUE = '#1864ab', PURPLE = '#6741d9', ORANGE = '#d9480f', GREY = '#495057';

    text(W / 2, 48, ['The Red Keep · how it fits together'], { size: 34, color: RED });

    // Inputs
    panel(30, 80, W - 60, 190, 'INPUTS · where the numbers come from', '#f8f9fa', '#ced4da', GREY);
    const iw = 300, ig = (W - 60 - 60 - 5 * iw) / 4;
    [['Prediction markets', 'Polymarket, Kalshi', 'odds'], ['Massive', 'prices, ETF holdings,', 'option chains (next)'], ['FRED', 'rates, inflation, dollar,', 'oil, credit, fear'], ['Hyperliquid', 'perps that trade on', 'weekends and holidays'], ['Claude', 'labels, match judging,', 'a first estimate']]
      .forEach((l, i) => node(60 + i * (iw + ig), 140, iw, 104, l, { fill: i === 4 ? '#fff3bf' : '#e7f5ff' }));

    // Shared layer
    const sy = 330;
    panel(30, sy, W - 60, 300, 'SHARED · runs on a schedule, the same for everyone', '#fff5f5', '#ffc9c9', RED);
    const sw = 278, sg = (W - 60 - 60 - 5 * sw) / 4, sx = i => 60 + i * (sw + sg), syy = sy + 70;
    const shared = [['1 · Screen', 'a small model taught by', 'Claude’s labels finds', 'the markets that matter'], ['2 · Match', 'same threshold, deadline', 'and source, or', '“nothing fits”'], ['3 · Evidence', 'jump windows, weekend', 'perps, past events,', 'the AI prior'], ['4 · Fuse + quorum', 'average by precision;', 'tier A needs two', 'markets that agree'], ['5 · Event graph', 'knock-on links,', 'counted once;', 'coherence checks']];
    shared.forEach((l, i) => { node(sx(i), syy, sw, 130, l, { fill: '#ffe3e3' }); if (i < 4) arrow([[sx(i) + sw + 4, syy + 65], [sx(i + 1) - 6, syy + 65]]); });
    [[1, ['Nothing fits?', 'shown with its near-misses,', 'never borrowed']], [3, ['One sure-looking reading', 'can be luck; two that', 'agree rarely are']]]
      .forEach(([i, l]) => { box(sx(i) - 10, syy + 150, sw + 20, 68, { dash: [8, 8], sw: 1.5, rough: 0.8 }); text(sx(i) + sw / 2, syy + 184, l, { size: 16 }); });
    arrow([[W / 2, 270], [W / 2, sy - 4]]);

    // Library
    const ly = 680, lw = 560, lx = W / 2 - lw / 2;
    node(lx, ly, lw, 96, ['Event library: odds q, jump δ, tier', 'every price saved with when it was seen'], { fill: '#f1f3f5', sw: 2.5 });
    arrow([[W / 2, sy + 300], [W / 2, ly - 4]]);

    // Per portfolio
    const py = 830;
    panel(30, py, 1110, 230, 'PER PORTFOLIO · runs live, in about a second', '#ebfbee', '#b2f2bb', GREEN);
    const pw = 230, pg2 = (1110 - 60 - 4 * pw) / 3, px = i => 60 + i * (pw + pg2), pyy = py + 72;
    [['6 · Exposures', 'look through ETFs;', 'the portfolio’s arrow b'], ['7 · Impact', 'jump on you = b · δ,', 'net of what’s priced in'], ['8 · Rank by swing', 'how far each event', 'could still move you'], ['9 · Simulate', 'thousands of futures', 'drawn over the graph']]
      .forEach((l, i) => { node(px(i), pyy, pw, 120, l, { fill: '#d3f9d8' }); if (i < 3) arrow([[px(i) + pw + 4, pyy + 60], [px(i + 1) - 6, pyy + 60]]); });
    arrow([[W / 2 - 80, ly + 96], [W / 2 - 200, ly + 120], [585, py - 4]]);

    // App
    node(1270, py + 20, 420, 190, ['The app', 'portfolio builder, risk map,', 'event cards, April 2025 replay,', 'Methodology', 'red-keep.vercel.app'], { fill: '#e5dbff', color: '#1e1e1e', size: 19 });
    arrow([[1270 - 4, py + 80], [1140 + 6, py + 80]], { label: 'portfolio in', lx: 1205, ly: py + 60 });
    arrow([[1140 + 6, py + 160], [1270 - 4, py + 160]], { label: 'results out', lx: 1205, ly: py + 186 });

    // Evals
    const ey = 1110;
    node(30, ey, 1110, 150, ['Evals, each with its measure fixed before it runs', 'E0 screen · E2 calibration · E3–E5 jumps and error bars · E8 April 2025 replay', 'E9 “just ask an AI” · E10 lead and lag · E11 joint error bars'], { fill: '#fff4e6', size: 19 });
    box(1270, ey, 420, 150, { dash: [8, 8], sw: 1.5, rough: 0.8 });
    text(1480, ey + 75, ['Resolved markets keep score:', 'markets beat the AI on odds, so', 'the AI never sets odds where', 'a market fits'], { size: 17 });
    arrow([[lx + lw + 4, ly + 48], [1600, ly + 60], [1698, py - 20], [1698, ey - 40], [1200, ey - 22], [1100, ey - 4]], { dash: [10, 8], color: ORANGE });
    text(1540, ly + 40, ['resolved events'], { size: 17, color: ORANGE });
    arrow([[50, ey + 20], [10, 760], [40, sy + 250]], { dash: [10, 8], color: ORANGE });
    text(70, 1080, ['source weights, calibration'], { size: 17, color: ORANGE, anchor: 'start' });
    return svg.outerHTML;
  }, font);
  fs.writeFileSync(out + '.svg', svg);
  await pg.setViewportSize({ width: 1720, height: 1300 });
  await pg.setContent('<html><body style="margin:0">' + svg + '</body></html>');
  await pg.evaluate(() => document.fonts.ready);
  await pg.waitForTimeout(300);
  await (await pg.$('svg')).screenshot({ path: out + '.png' });
  await b.close();
})();
