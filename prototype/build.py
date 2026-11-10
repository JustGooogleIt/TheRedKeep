"""Build The Red Keep prototype from src/ into two pages that share one body.

- index.html                 a standalone page: open it in any browser.
- dist/the-red-keep.html     the same page in Claude artifact form (no doctype, head or body tags),
                             published with dist/hero.webp and dist/hero-960.webp beside it.

citadel.webp, the faint castle behind the dragon scene, is inlined as a data URL: the scene
reads its canvas back pixel by pixel, which an image from another origin (or from a file://
page) would block.

A backend snapshot is optional: `redkeep app --out prototype/src/live.js` writes one, and the
build inlines it so the app can switch between it and the illustrative data. `--live` takes
one from elsewhere, and `--out` writes just the standalone page and its images to another
folder (for a test build that leaves the published files alone).

The standalone page is also what Vercel serves at red-keep.vercel.app (vercel.json), so its head
carries a link preview (og.jpg, a 1200x630 crop of the hero), a tab icon, and a request that
search engines leave it out: it is for whoever is sent the link.
"""
import argparse, base64, pathlib, shutil, urllib.parse

here = pathlib.Path(__file__).resolve().parent
src = here / 'src'
ap = argparse.ArgumentParser(description='Build The Red Keep prototype.')
ap.add_argument('--live', type=pathlib.Path, help='a live.js from `redkeep app` (default: src/live.js, if it exists)')
ap.add_argument('--out', type=pathlib.Path, help='write only the standalone page and its images to this folder')
args = ap.parse_args()
live = args.live or src / 'live.js'
if args.live and not live.exists():
    ap.error(f'{live} does not exist')

css = (src / 'styles.css').read_text()
citadel = 'data:image/webp;base64,' + base64.b64encode((here / 'citadel.webp').read_bytes()).decode()
parts = [live] if live.exists() else []
parts += [src / f for f in ['data.js', 'model.js', 'dataset.js', 'ui-core.js', 'ui-analysis.js', 'ui-dragon.js', 'ui-method.js', 'ui-rest.js']]
js = f'const RK_CITADEL = "{citadel}";\n' + '\n'.join(f.read_text() for f in parts)
assert '</script' not in js.lower() and '</style' not in css.lower()

meta = '''<meta name="description" content="Scenario Analysis Engine. Ashwin's take-home for Castle.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500;600&family=Hedvig+Letters+Serif:opsz@12..24&display=swap">'''
style = f'<style>\n{css}</style>'
SITE = 'https://red-keep.vercel.app/'
ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><style>path{fill:#B4141F}'
        '@media (prefers-color-scheme:dark){path{fill:#F2555A}}</style>'
        '<path d="M5 30V9h4v3h3V9h3v3h2V9h3v3h3V9h4v21h-9v-6a2.5 2.5 0 0 0-5 0v6z"/></svg>')
share = f'''<meta name="robots" content="noindex">
<meta property="og:type" content="website">
<meta property="og:site_name" content="The Red Keep">
<meta property="og:title" content="The Red Keep · Hedging against dragons">
<meta property="og:description" content="Find the events that could move your portfolio, and see how much of each one the market has already priced in. Ashwin's take-home for Castle.">
<meta property="og:url" content="{SITE}">
<meta property="og:image" content="{SITE}og.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="A red dragon breathes fire onto a walled castle on a sea cliff">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="data:image/svg+xml,{urllib.parse.quote(ICON)}">
<meta name="theme-color" content="#F5F5F5" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#120C0D" media="(prefers-color-scheme: dark)">'''
body = f'''<div id="root"><p class="boot">Raising the keep…</p></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react/18.3.1/umd/react.production.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.3.1/umd/react-dom.production.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/htm@3.1.1/dist/htm.umd.js"></script>
<script>
{js}
</script>
'''

artifact = f'''<title>The Red Keep</title>
{meta}
<meta name="claude:viewport-fit-sides" content="cover">
{style}
{body}'''
standalone = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>The Red Keep</title>
{meta}
{share}
{style}
</head>
<body>
{body}</body>
</html>
'''

with_live = ', with the backend snapshot from ' + live.name if live.exists() else ', illustrative data only'
if args.out:
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'index.html').write_text(standalone)
    for f in ['hero.webp', 'hero-960.webp', 'og.jpg']:
        shutil.copyfile(here / f, args.out / f)
    print(f'built {args.out / "index.html"}{with_live}')
else:
    (here / 'index.html').write_text(standalone)
    dist = here / 'dist'
    dist.mkdir(exist_ok=True)
    (dist / 'the-red-keep.html').write_text(artifact)
    for f in ['hero.webp', 'hero-960.webp']:
        shutil.copyfile(here / f, dist / f)
    print(f'built index.html and dist/the-red-keep.html ({len(artifact) // 1024} KB){with_live}')
