"""Build The Red Keep prototype from src/ into two pages that share one body.

- index.html                 a standalone page: open it in any browser.
- dist/the-red-keep.html     the same page in Claude artifact form (no doctype, head or body tags),
                             published with dist/hero.webp and dist/hero-960.webp beside it.
"""
import pathlib, shutil

here = pathlib.Path(__file__).resolve().parent
src = here / 'src'
css = (src / 'styles.css').read_text()
js = '\n'.join((src / f).read_text() for f in ['data.js', 'model.js', 'ui-core.js', 'ui-analysis.js', 'ui-rest.js'])
assert '</script' not in js.lower() and '</style' not in css.lower()

meta = '''<meta name="description" content="Scenario Analysis Engine. Ashwin's take-home for Castle.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500;600&family=Hedvig+Letters+Serif:opsz@12..24&display=swap">'''
style = f'<style>\n{css}</style>'
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
{style}
</head>
<body>
{body}</body>
</html>
'''

(here / 'index.html').write_text(standalone)
dist = here / 'dist'
dist.mkdir(exist_ok=True)
(dist / 'the-red-keep.html').write_text(artifact)
for f in ['hero.webp', 'hero-960.webp']:
    shutil.copyfile(here / f, dist / f)
print(f'built index.html and dist/the-red-keep.html ({len(artifact) // 1024} KB)')
