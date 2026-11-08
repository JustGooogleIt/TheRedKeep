// Usage: node render.js <input.mmd> <outBase>
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
(async () => {
  const [, , inFile, outBase] = process.argv;
  const src = fs.readFileSync(inFile, 'utf8');
  const browser = await chromium.launch();
  const page = await browser.newPage({ deviceScaleFactor: 2 });
  await page.setContent('<html><head><style>body{margin:0;background:#fff}</style></head><body><div id="c"></div></body></html>');
  await page.addScriptTag({ path: path.join(__dirname, 'node_modules/mermaid/dist/mermaid.min.js') });
  const svg = await page.evaluate(async (src) => {
    mermaid.initialize({
      startOnLoad: false,
      themeCSS: '.edgeLabel rect{opacity:1 !important;fill:#FFFFFF !important} .cluster-label text{font-weight:600}',
      theme: 'base',
      securityLevel: 'strict',
      flowchart: { htmlLabels: false, curve: 'basis', nodeSpacing: 36, rankSpacing: 52, padding: 12 },
      themeVariables: {
        fontFamily: 'Inter, Helvetica, Arial, sans-serif',
        fontSize: '16px',
        primaryColor: '#F8FAFC',
        primaryBorderColor: '#64748B',
        primaryTextColor: '#0F172A',
        lineColor: '#475569',
        textColor: '#0F172A',
        clusterBkg: '#FCFCFD',
        clusterBorder: '#CBD5E1',
        edgeLabelBackground: '#FFFFFF'
      }
    });
    const { svg } = await mermaid.render('g', src);
    return svg;
  }, src);
  fs.writeFileSync(outBase + '.svg', svg);
  const m = svg.match(/viewBox="([-\d.]+) ([-\d.]+) ([\d.]+) ([\d.]+)"/);
  const w = Math.ceil(parseFloat(m[3])), h = Math.ceil(parseFloat(m[4]));
  await page.setViewportSize({ width: w + 48, height: h + 48 });
  await page.setContent('<html><head><style>body{margin:0;background:#fff;padding:24px} svg{width:' + w + 'px !important;height:' + h + 'px !important;max-width:none !important}</style></head><body>' + svg + '</body></html>');
  const el = await page.$('svg');
  await el.screenshot({ path: outBase + '.png' });
  await browser.close();
  console.log('rendered', outBase);
})().catch(e => { console.error(e); process.exit(1); });
