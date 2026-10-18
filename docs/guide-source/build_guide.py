"""Build both guides from source HTML, screenshots in shots/, and a test count.

Run from the repository root:
    python -B docs/guide-source/build_guide.py [test_count]

Without a count, reuse the explicit marker in the existing complete guide.
Before writing, check tag nesting, internal links, unique IDs, and the absence
of scripts. Neither output is written if either guide fails validation.
"""
import base64
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SHOTS = HERE / 'shots'
# A neutral transparent image replaces the legacy screenshots, which contained
# Traditional Chinese labels from an earlier UI build.
BLANK_PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
FULL = ROOT / 'docs' / 'detailed-guide.html'
# (source, target, the pictures it holds)
DOCUMENTS = (
    (HERE / 'guide.src.html', ROOT / 'guide.html', (('{{IMG_DASH}}', 'guide-dash.png'),)),
    (HERE / 'installation-and-usage.src.html', FULL,
     (('{{IMG_DASH}}', 'guide-dash.png'), ('{{IMG_SCHED}}', 'guide-schedule.png'),
      ('{{IMG_MANUAL}}', 'guide-manual.png'), ('{{IMG_RUNS}}', 'guide-runs.png'),
      ('{{IMG_CAL}}', 'guide-calibration-run.png'), ('{{IMG_TREND}}', 'guide-trend.png'),
      ('{{IMG_MAINT}}', 'guide-maint.png'))),
)


class Checker(HTMLParser):
    """Every tag closed in order, every #link has a target, no id used twice, no script."""
    VOID = {'meta', 'br', 'img', 'input', 'line', 'rect', 'path', 'hr', 'link'}

    def __init__(self):
        super().__init__()
        self.stack, self.ids, self.links, self.problems = [], [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'a' and attrs.get('href', '').startswith('#'):
            self.links.append(attrs['href'][1:])
        if tag == 'script':
            self.problems.append(f'line {self.getpos()[0]}: <script> is not allowed (the screen opens the guide under a strict policy)')
        if tag not in self.VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag, attrs):
        if 'id' in dict(attrs):
            self.ids.append(dict(attrs)['id'])

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1][0] != tag:
            self.problems.append(f'line {self.getpos()[0]}: </{tag}> but open is {self.stack[-1] if self.stack else None}')
            while self.stack and self.stack[-1][0] != tag:
                self.stack.pop()
        if self.stack:
            self.stack.pop()


def build(source, images, count):
    """The finished page and what the checker found: (html, checker, problems)."""
    html = source.read_text(encoding='utf-8')
    for key, name in images:
        if html.count(key) != 1:
            sys.exit(f'{source.name}: expected exactly one {key}')
        html = html.replace(key, 'data:image/png;base64,' + BLANK_PNG)
    if html.count('{{TEST_COUNT}}') != 1:
        sys.exit(f'{source.name}: expected exactly one {{{{TEST_COUNT}}}}')
    html = html.replace('{{TEST_COUNT}}', count)
    if '{{' in html:
        sys.exit(f'{source.name}: an unresolved {{{{...}}}} placeholder remains')
    html = html.replace('</head>', f'<!-- test-count: {count} -->\n</head>', 1)
    checker = Checker()
    checker.feed(re.sub(r'data:image/png;base64,[A-Za-z0-9+/=]+', 'x', html))
    problems = checker.problems + [f'unclosed <{tag}> from line {line}' for tag, line in checker.stack]
    problems += [f'link to missing #{name}' for name in checker.links if name not in checker.ids]
    problems += [f'id {name} used twice' for name in set(checker.ids) if checker.ids.count(name) > 1]
    return html, checker, problems


def main():
    if len(sys.argv) > 1:
        count = sys.argv[1]
    else:
        found = re.search(r'<!-- test-count: (\d+) -->', FULL.read_text(encoding='utf-8')) if FULL.is_file() else None
        if not found:
            sys.exit('Supply the test count, for example: build_guide.py 103')
        count = found.group(1)
    if not re.fullmatch(r'\d+', count):
        sys.exit('The test count must be a nonnegative integer')
    pages = []
    for source, target, images in DOCUMENTS:
        html, checker, problems = build(source, images, count)
        if problems:
            sys.exit(source.name + '\n' + '\n'.join(problems))
        pages.append((target, html, checker))
    # What the short guide points at in the complete one must be there.
    short, full_ids = pages[0][1], pages[1][2].ids
    for name in re.findall(r'href="docs/detailed-guide\.html#([^"]+)"', short):
        if name not in full_ids:
            sys.exit(f'The short guide links to a missing complete-guide target: #{name}')
    for target, html, checker in pages:           # nothing is written unless both are sound
        target.write_text(html, encoding='utf-8', newline='\n')
        print(f'{target.relative_to(ROOT)}: {len(html.encode("utf-8"))} bytes, {count} tests, '
              f'{len(checker.ids)} ids, {len(checker.links)} internal links')


if __name__ == '__main__':
    main()
