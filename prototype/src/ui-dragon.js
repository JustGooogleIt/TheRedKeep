/* ==========================================================================
   UI · Part 3: the dragon scroll scene at the top of the page.
   Ported from the Crimson Flight study (scene.js). Each frame paints a
   low-resolution colour scene into an offscreen buffer, then prints it as a
   fixed grid of digits. A clock drives the wingbeats, body lift, tail wave and
   flame flicker, so they keep moving when scrolling stops; scroll drives the
   fire's growth and the scene's progression. Everything the component touches
   is reached through refs, and every frame, observer and listener it starts is
   stopped when it unmounts.
   ========================================================================== */
const DRAGON_CHAPTERS = ['What could move your portfolio', 'Net of what’s already priced in', 'Hedging against dragons'];
const DRAGON_STILL_PHASE = 0;          // the wing pose shown when motion is reduced
const DRAGON_DIGITS = '0123456789'.split('');
// The faint castle behind the dragon: the area of citadel.png that scene.js samples, which leaves out
// the artwork's own dragon. build.py inlines it as a data URL, so the canvas can always read it back.
const DRAGON_CITADEL = typeof RK_CITADEL === 'string' ? RK_CITADEL : 'citadel.webp';
const clamp01 = x => Math.max(0, Math.min(1, x));

/* Owns the two canvases. The drawing below follows scene.js line for line;
   only its globals became arguments (progress, clock, wing phase, glow, image). */
function makeDragonPainter(canvas) {
  const ctx = canvas.getContext('2d', { alpha: false });
  const buffer = document.createElement('canvas'), b = buffer.getContext('2d', { willReadFrequently: true });
  const mix = (a, z, t) => a + (z - a) * t;
  let W = 0, H = 0, cols = 0, rows = 0, cell = 9;

  function resize(w, h) {
    W = w; H = h; cell = W < 650 ? 6 : 7;
    cols = Math.ceil(W / cell); rows = Math.ceil(H / (cell * 1.35));
    const d = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(W * d); canvas.height = Math.round(H * d); ctx.setTransform(d, 0, 0, d, 0, 0);
    buffer.width = cols; buffer.height = rows;
  }

  function shape(path, fill, stroke, width = 1) {
    const p = new Path2D(path);
    if (fill) { b.fillStyle = fill; b.fill(p); }
    if (stroke) { b.strokeStyle = stroke; b.lineWidth = width; b.stroke(p); }
  }

  // Dorsal anatomy: long neck, narrow torso, articulated wing fingers and tapered tail.
  function dragon(x, y, size, phase) {
    b.save(); b.translate(x, y); b.scale(size, size);
    // Each joint has its own phase: the shoulder drives, the wrist follows,
    // and the outer fingers flex last. The span stays open throughout the stroke.
    for (const side of [-1, 1]) {
      b.save(); b.scale(side, 1);
      const drive = Math.sin(phase), wristBeat = Math.sin(phase - .28), tipBeat = Math.sin(phase - .56);
      const elbow = { x: 35, y: -11 + drive * 12 };
      const wrist = { x: 69 + 3 * Math.cos(phase), y: -21 + wristBeat * 28 };
      const tip = { x: 143 - 7 * Math.cos(phase), y: -25 + tipBeat * 62 };
      const fingers = [tip, { x: 115, y: 22 + Math.sin(phase - .68) * 49 }, { x: 79, y: 43 + Math.sin(phase - .52) * 33 }, { x: 40, y: 36 + Math.sin(phase - .32) * 15 }];
      const [, f1, f2, f3] = fingers;
      const outline = `M 6 -4 Q 19 ${elbow.y - 6} ${elbow.x} ${elbow.y} Q 52 ${wrist.y - 8} ${wrist.x} ${wrist.y} Q 108 ${tip.y - 10} ${tip.x} ${tip.y} Q ${f1.x - 5} ${mix(tip.y, f1.y, .6)} ${f1.x} ${f1.y} Q ${f2.x + 14} ${f2.y - 20} ${f2.x} ${f2.y} Q ${f3.x + 17} ${f3.y - 16} ${f3.x} ${f3.y} Q 20 11 6 13 Z`;
      const membrane = b.createLinearGradient(12, wrist.y - 20, 90, 46);
      membrane.addColorStop(0, '#4d1735'); membrane.addColorStop(.3, '#db426a'); membrane.addColorStop(.65, '#862c50'); membrane.addColorStop(1, '#43132e');
      shape(outline, membrane, '#df799c', .7);
      b.save(); b.clip(new Path2D(outline));
      // Thin light and shadow bands move with the flexing membrane.
      for (let j = 0; j < 170; j++) { const xx = 13 + (j * 37.73) % 129, yy = -85 + (j * 13.19) % 195; b.fillStyle = j % 3 ? '#f69fb72b' : '#21071755'; b.fillRect(xx, yy, .8, 1.1); }
      for (let i = 0; i < fingers.length; i++) {
        const f = fingers[i];
        for (let j = 1; j < 5; j++) { const t = j / 5; shape(`M ${mix(8, f.x, t)} ${mix(8, f.y, t)} Q ${mix(wrist.x, f.x, t)} ${mix(wrist.y, f.y, t) + 4} ${f.x} ${f.y}`, null, '#f094b240', .55); }
      }
      b.restore();
      // The strong upper arm and four supporting fingers remain visible at every angle.
      shape(`M 6 -4 Q 19 ${elbow.y - 6} ${elbow.x} ${elbow.y} Q 52 ${wrist.y - 8} ${wrist.x} ${wrist.y} Q 108 ${tip.y - 10} ${tip.x} ${tip.y}`, null, '#efacc2', 1.7);
      for (let i = 1; i < fingers.length; i++) { const f = fingers[i]; shape(`M ${wrist.x} ${wrist.y} Q ${mix(wrist.x, f.x, .55)} ${mix(wrist.y, f.y, .55) - 5} ${f.x} ${f.y}`, null, '#ef99b4', .9); }
      shape(`M 7 5 Q 25 ${elbow.y + 7} ${elbow.x} ${elbow.y + 2} Q 53 ${wrist.y + 2} ${wrist.x} ${wrist.y}`, null, '#4c1836', 2.3);
      shape(`M ${wrist.x - 1} ${wrist.y} L ${wrist.x + 1} ${wrist.y - 9} L ${wrist.x + 5} ${wrist.y + 1}`, '#edb0c3', null);
      b.restore();
    }
    // A continuous taper follows a broad, trailing S-curve. Motion travels
    // down its length, so the tip follows the hips instead of wagging as a unit.
    function tailPoint(t) {
      const wave = phase * .58;
      // No fixed sideways bend: the entire curve crosses the centerline.
      // Increasing amplitude and delayed phase let the tip trail the hips.
      const xx = (Math.sin(wave - t * 2.6) * 30 + Math.sin(wave - t * 4.3) * 5) * t ** 1.25;
      return { x: xx, y: 28 + 57 * t - 5 * t * t + Math.cos(wave - t * 2.6) * 1.6 * t };
    }
    const spine = [], left = [], right = [];
    for (let i = 0; i <= 70; i++) {
      const t = i / 70, pt = tailPoint(t), next = tailPoint(Math.min(1, t + .002)), prev = tailPoint(Math.max(0, t - .002));
      const dx = next.x - prev.x, dy = next.y - prev.y, len = Math.hypot(dx, dy) || 1, nx = dy / len, ny = -dx / len, r = 5.4 * (1 - t) ** 1.3 + .06;
      spine.push({ ...pt, nx, ny, r, t }); left.push({ x: pt.x - nx * r, y: pt.y - ny * r }); right.push({ x: pt.x + nx * r, y: pt.y + ny * r });
    }
    b.beginPath(); left.forEach((pt, i) => (i ? b.lineTo(pt.x, pt.y) : b.moveTo(pt.x, pt.y))); right.slice().reverse().forEach(pt => b.lineTo(pt.x, pt.y)); b.closePath();
    const tailShade = b.createLinearGradient(-6, 35, 29, 78);
    tailShade.addColorStop(0, '#bd5b80'); tailShade.addColorStop(.35, '#67213f'); tailShade.addColorStop(.65, '#cc6d93'); tailShade.addColorStop(1, '#45152e');
    b.fillStyle = tailShade; b.fill(); b.strokeStyle = '#ce7d9c'; b.lineWidth = .5; b.stroke();
    // A narrow illuminated ridge and small diminishing dorsal scales.
    b.beginPath(); spine.forEach((pt, i) => (i ? b.lineTo(pt.x - pt.nx * pt.r * .2, pt.y - pt.ny * pt.r * .2) : b.moveTo(pt.x, pt.y))); b.strokeStyle = '#eea5bd'; b.lineWidth = .62; b.stroke();
    for (let i = 4; i < 59; i += 5) {
      const pt = spine[i], tp = spine[i + 2], h = (1 - pt.t) * 2.7;
      shape(`M ${pt.x} ${pt.y} L ${pt.x - pt.nx * h} ${pt.y - pt.ny * h - 1.6} L ${tp.x} ${tp.y}`, '#d78fab', null);
      shape(`M ${left[i].x} ${left[i].y} Q ${pt.x} ${pt.y + 1.5} ${right[i].x} ${right[i].y}`, null, '#f3adc642', .4);
    }
    const body = b.createLinearGradient(-9, 0, 9, 0);
    body.addColorStop(0, '#2c0b20'); body.addColorStop(.4, '#be567e'); body.addColorStop(.56, '#f4acc2'); body.addColorStop(.7, '#9a355a'); body.addColorStop(1, '#230917');
    shape('M -3 -47 C -3 -34 -8 -24 -8 -9 C -14 1 -10 18 -7 30 Q -4 39 0 40 Q 6 35 7 27 C 9 15 13 1 7 -9 C 6 -24 3 -34 3 -47 Z', body, '#bd6a8f', .6);
    // Long folded hind legs and separated hooked talons.
    for (const side of [-1, 1]) { b.save(); b.scale(side, 1); shape('M 6 15 Q 16 23 11 36 L 15 45 L 12 43 L 10 46 L 10 41 L 7 43 L 7 37 Q 8 29 4 26 Z', '#73304f', '#d98fa6', .6); b.restore(); }
    // Segmented scales and a pale dorsal ridge bring depth through the numerical shading.
    for (let j = 0; j < 24; j++) {
      const yy = -42 + j * 3.2, ww = yy < -12 ? 2.2 : yy > 27 ? 3 : 6.1;
      shape(`M ${-ww} ${yy} Q 0 ${yy + 3} ${ww} ${yy}`, null, j % 2 ? '#f29ab6' : '#662144', .44);
      shape(`M 0 ${yy} L -1.5 ${yy + 3} L 1 ${yy + 2}`, '#f2a9bd', null);
    }
    shape('M -3 -47 L -5 -53 L -3 -59 L -1 -62 L 2 -62 L 4 -58 L 5 -53 L 3 -47 Z', body, '#ed9db5', .6);
    shape('M -4 -53 L -9 -49 L -5 -59 M 4 -53 L 9 -49 L 5 -59', null, '#eaa6be', 1);
    b.fillStyle = '#ffc5ad'; b.fillRect(-3, -57, 1, .8); b.fillRect(2, -57, 1, .8);
    b.restore();
    return { x, y: y - 62 * size };
  }

  function paint({ p, clock, phase, boost, citadel }) {
    if (!cols || !rows) return;
    b.globalAlpha = 1; b.fillStyle = '#12050c'; b.fillRect(0, 0, cols, rows);
    const sky = b.createLinearGradient(0, 0, 0, rows);
    sky.addColorStop(0, '#30111f'); sky.addColorStop(.55, '#321322'); sky.addColorStop(1, '#0c0308');
    b.fillStyle = sky; b.fillRect(0, 0, cols, rows);
    // The castle drifts slightly as you scroll (citadel is already cropped to the castle area).
    if (citadel) {
      b.save(); b.globalAlpha = .19;
      const cw = cols * 1.1, ch = rows * .78;
      b.drawImage(citadel, 0, 0, citadel.naturalWidth, citadel.naturalHeight, -cols * .04 - p * cols * .035, rows * .37 - p * rows * .025, cw, ch);
      b.globalCompositeOperation = 'multiply'; b.fillStyle = '#ba4b6d'; b.fillRect(0, rows * .37, cols, rows);
      b.restore();
    }
    const veil = b.createLinearGradient(0, rows * .35, 0, rows * .52);
    veil.addColorStop(0, '#321322'); veil.addColorStop(1, '#32132200');
    b.fillStyle = veil; b.fillRect(0, rows * .35, cols, rows * .18);
    // One finely articulated dragon holds the center while scrolling brings it closer.
    const size = Math.min(cols / 340, rows / 205) * (1 + p * .055);
    const x = cols * (.50 + Math.sin(p * 2) * .012), y = rows * .57 - Math.sin(clock * 2.65 - .4) * 1.25;
    const mouth = dragon(x, y, size, phase);
    const fire = clamp01((p - .08) / .77);
    if (fire > 0) {
      const length = rows * .10 + rows * .45 * fire, width = 1 + fire * 5.5;
      const glow = b.createRadialGradient(mouth.x, mouth.y - length * .55, 0, mouth.x, mouth.y - length * .55, length * .8);
      glow.addColorStop(0, '#da244744'); glow.addColorStop(1, '#da244700');
      b.fillStyle = glow; b.fillRect(0, 0, cols, rows);
      // Nested rising tongues: narrow at the mouth, billowing into a growing plume.
      for (let layer = 0; layer < 3; layer++) {
        b.beginPath(); b.moveTo(mouth.x - .65, mouth.y);
        for (let i = 0; i <= 36; i++) {
          const t = i / 36, yy = mouth.y - t * length, envelope = Math.sin(Math.PI * t * .92) * width * (1 - layer * .28);
          const curl = Math.sin(t * 12 - clock * 4 + layer) * t * 2 + Math.sin(t * 28 - clock * 6) * t;
          b.lineTo(mouth.x - envelope + curl, yy);
        }
        for (let i = 36; i >= 0; i--) {
          const t = i / 36, envelope = Math.sin(Math.PI * t * .92) * width * (1 - layer * .28);
          const curl = Math.sin(t * 12 - clock * 4 + layer) * t * 2 + Math.sin(t * 25 - clock * 5) * t;
          b.lineTo(mouth.x + envelope + curl, mouth.y - t * length);
        }
        b.closePath(); b.fillStyle = ['#a92249', '#f24b65', '#ffb39b'][layer]; b.fill();
      }
      for (let i = 0; i < 65; i++) {
        const t = (clock * .23 + i * .071) % 1, side = Math.sin(i * 9.31);
        const xx = mouth.x + side * (3 + t * width * 2) + Math.sin(clock + i) * t * 2, yy = mouth.y - t * length * 1.2;
        b.globalAlpha = (1 - t) * fire; b.fillStyle = i % 4 ? '#ef4564' : '#ffc1a5'; b.fillRect(xx, yy, .5, .7);
      }
      b.globalAlpha = 1;
    }
    // Print the buffer as digits on a grid that never moves: brighter pixels get denser digits.
    const pix = b.getImageData(0, 0, cols, rows).data;
    ctx.fillStyle = '#11040b'; ctx.fillRect(0, 0, W, H);
    ctx.font = `${cell + 1}px "Courier New",monospace`; ctx.textBaseline = 'top';
    const bright = boost ? 1.35 : 1;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const k = (r * cols + c) * 4, R = pix[k], G = pix[k + 1], B = pix[k + 2];
        const lum = (R * .36 + G * .45 + B * .19) / 255, density = clamp01(lum * 1.6);
        const noise = ((c * 73 + r * 37) % 101) / 101;
        const digit = Math.floor(clamp01(density * .7 + noise * .3) * 9);
        const red = Math.min(255, (R * .9 + 22) * bright), green = Math.min(210, (G * .65 + 5) * bright), blue = Math.min(230, (B * .77 + 8) * bright);
        ctx.fillStyle = `rgb(${red | 0},${green | 0},${blue | 0})`;
        ctx.fillText(DRAGON_DIGITS[digit], c * cell, r * cell * 1.35);
      }
    }
  }

  return { resize, paint };
}

/* The scene: a tall scroll section with a sticky, full-viewport stage. */
function DragonScene({ tw, goTab, heroRef }) {
  const own = useRef(null); const rootRef = heroRef || own;
  const stageRef = useRef(null), canvasRef = useRef(null), introRef = useRef(null), endRef = useRef(null), hudRef = useRef(null);
  const barRef = useRef(null), chapterNoRef = useRef(null), chapterNameRef = useRef(null);
  const look = useRef(null); look.current = { boost: tw.glow === 'bright', citadel: tw.citadel !== false };
  const redraw = useRef(null);
  useEffect(() => { if (redraw.current) redraw.current(); }, [tw.glow, tw.citadel]);

  useEffect(() => {
    const root = rootRef.current, stage = stageRef.current, canvas = canvasRef.current;
    const intro = introRef.current, end = endRef.current, hud = hudRef.current, bar = barRef.current;
    const chapterNo = chapterNoRef.current, chapterName = chapterNameRef.current;
    if (!root || !stage || !canvas || !canvas.getContext) return undefined;
    const painter = makeDragonPainter(canvas);
    const mq = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null;
    let reduce = !!(mq && mq.matches);
    let raf = 0, visible = true, dirty = true, progress = 0, goal = 0, overhang = 0, paintedAt = -1, chapter = 0, img = null;

    const size = () => painter.resize(stage.clientWidth || window.innerWidth, stage.clientHeight || window.innerHeight);
    // All layout reads happen here, once per frame, before anything is written.
    function measure() {
      const top = root.getBoundingClientRect().top, h = stage.clientHeight;
      goal = clamp01(-top / Math.max(1, root.offsetHeight - h));
      // Until the section reaches the top of the window, the foot of the stage hangs below the fold.
      overhang = Math.max(0, Math.min(top, top + h - window.innerHeight));
    }
    // The castle backdrop is optional: load it the first time it is switched on.
    const citadel = () => {
      if (!look.current.citadel) return null;
      if (!img) { img = new Image(); img.decoding = 'async'; img.onload = () => { dirty = true; schedule(); }; img.src = DRAGON_CITADEL; }
      return img.complete && img.naturalWidth ? img : null;
    };
    function paint(now) {
      const p = progress, clock = reduce ? 0 : now / 1000;
      painter.paint({ p, clock, phase: reduce ? DRAGON_STILL_PHASE : clock * 2.65 + p * .6, boost: look.current.boost, citadel: citadel() });
    }
    // The copy, chapter marker and progress rail follow the same eased progress as the canvas.
    function place() {
      const p = progress, a = 1 - clamp01(p / .21), z = clamp01((p - .80) / .15);
      intro.style.opacity = a; intro.style.visibility = a > .02 ? 'visible' : 'hidden';
      intro.style.transform = reduce ? '' : `translateY(${-p * 110}px)`;
      end.style.opacity = z; end.style.visibility = z > .02 ? 'visible' : 'hidden';
      end.style.transform = reduce ? '' : `translateY(${(1 - z) * 25}px)`;
      hud.style.transform = overhang ? `translateY(${-overhang}px)` : '';
      bar.style.transform = `scaleX(${p})`;
      const n = p < .30 ? 0 : p < .78 ? 1 : 2;
      if (n !== chapter) { chapter = n; chapterNo.textContent = `0${n + 1} / 03`; chapterName.textContent = DRAGON_CHAPTERS[n]; }
    }
    function frame(now) {
      raf = 0; measure();
      if (reduce) { progress = goal; dirty = false; paint(0); place(); return; }   // one still frame, no loop
      progress += (goal - progress) * .10;
      if (dirty || now - paintedAt > 30) { paintedAt = now; dirty = false; paint(now); }
      place();
      if (visible) raf = requestAnimationFrame(frame);
    }
    function schedule() { if (!raf && visible) raf = requestAnimationFrame(frame); }
    function stop() { if (raf) cancelAnimationFrame(raf); raf = 0; }
    // Repaint at once after a resize, because resizing clears the canvas.
    const onResize = () => { size(); measure(); if (reduce) progress = goal; paint(reduce ? 0 : performance.now()); place(); };
    const onScroll = () => { if (reduce) schedule(); };   // the running loop reads the scroll position itself
    const onMotionPref = () => { reduce = !!(mq && mq.matches); stop(); dirty = true; schedule(); };
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(onResize) : null;
    const io = typeof IntersectionObserver === 'function'
      ? new IntersectionObserver(entries => { visible = entries[entries.length - 1].isIntersecting; if (visible) { dirty = true; schedule(); } else stop(); })
      : null;

    size(); measure(); progress = goal; paint(reduce ? 0 : performance.now()); place();
    if (ro) ro.observe(stage); else window.addEventListener('resize', onResize);
    if (io) io.observe(root);
    window.addEventListener('scroll', onScroll, { passive: true });
    if (mq) { if (mq.addEventListener) mq.addEventListener('change', onMotionPref); else mq.addListener(onMotionPref); }
    redraw.current = () => { dirty = true; schedule(); };
    schedule();
    return () => {
      stop(); redraw.current = null;
      if (ro) ro.disconnect(); else window.removeEventListener('resize', onResize);
      if (io) io.disconnect();
      window.removeEventListener('scroll', onScroll);
      if (mq) { if (mq.removeEventListener) mq.removeEventListener('change', onMotionPref); else mq.removeListener(onMotionPref); }
      if (img) { img.onload = null; img = null; }
    };
  }, []);

  return html`<section className="dragon" ref=${rootRef}>
    <div className="dragon-stage" ref=${stageRef}>
      <canvas className="dragon-canvas" ref=${canvasRef} role="img" aria-label="A slender dragon drawn in crimson numbers beats its wings and breathes a plume of fire upward that grows as you scroll."></canvas>
      <div className="dragon-shade" aria-hidden="true"></div>
      <div className="dragon-intro" ref=${introRef}>
        <p className="dragon-eyebrow">Scenario Analysis Engine · Ashwin’s take-home for Castle</p>
        <h1 className="dragon-h">Hedging against dragons.</h1>
        <p className="dragon-sub">Find the events that could move your portfolio in the next ${tw.horizon} months, and see how much of each one the market has already priced in.</p>
        <div className="dragon-cta">
          <button className="btn btn-light btn-lg" onClick=${() => goTab('analysis')}>Explore the engine</button>
          <button className="btn btn-outline-light btn-lg" onClick=${() => goTab('replay')}>Replay April 2025</button>
        </div>
      </div>
      <div className="dragon-end" ref=${endRef}>
        <p className="dragon-eyebrow">The first Red Keep had thick walls and no plan for dragons.</p>
        <h2 className="dragon-h">This one starts with the dragons.</h2>
        <div className="dragon-cta"><button className="btn btn-light btn-lg" onClick=${() => goTab('analysis')}>Explore the engine</button></div>
      </div>
      <div className="dragon-hud" ref=${hudRef} aria-hidden="true">
        <div className="dragon-chapter"><b ref=${chapterNoRef}>01 / 03</b><span ref=${chapterNameRef}>${DRAGON_CHAPTERS[0]}</span></div>
        <div className="dragon-hint"><span>Scroll</span><i></i><span>↓</span></div>
        <div className="dragon-rail"><i ref=${barRef}></i></div>
      </div>
    </div>
  </section>`;
}
