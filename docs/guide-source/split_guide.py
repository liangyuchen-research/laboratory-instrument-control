"""For looking at the guide: one page per <h2> section (details opened), so a tall screenshot fits.

    python split_guide.py <guide html> <output folder>
"""
import re
import sys
from pathlib import Path

source, folder = Path(sys.argv[1]), Path(sys.argv[2])
folder.mkdir(parents=True, exist_ok=True)
html = source.read_text(encoding='utf-8')
head, rest = html.split('<main>', 1)
body, tail = rest.rsplit('</main>', 1)
head = re.sub(r'<nav>.*?</nav>', '', head, flags=re.DOTALL).replace('grid-template-columns:250px minmax(0,1fr)',
                                                                   'grid-template-columns:minmax(0,1fr)')
parts = re.split(r'(?=<h2[ >])', body)
names = []
for index, part in enumerate(parts):
    found = re.search(r'<h2[^>]*id="([^"]+)"', part)
    name = f'{index:02d}-{found.group(1) if found else "intro"}'
    (folder / (name + '.html')).write_text(head + '<main>' + part.replace('<details', '<details open') + '</main>' + tail,
                                           encoding='utf-8')
    names.append(name)
print(' '.join(names))
