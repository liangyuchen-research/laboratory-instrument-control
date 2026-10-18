"""operation screen file (pc/web) . Added by the firmware of

Read-only file: Do not open the browser, do not start the program, do not connect to anything. The picture is very strict Content-Security-Policy executed below
 (cannot be written in HTML inside script/style, The ____ does not work innerHTML, cannot connect to external URLs), check here that every screen file obeys the rules.
also checks that the modules shared by each work package still have the agreed names.

    python tests/test_web_static.py
"""
from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'pc' / 'web'
OLD_FONTS = ROOT / 'pc' / 'web' / 'fonts'
HTTPD = ROOT / 'pc' / 'httpd.py'

PAGES = ['dash', 'schedule', 'manual', 'trend', 'runs', 'calibration', 'alarms', 'maint']
TABS = ['recipe', 'link', 'plasma', 'pumps', 'spectra', 'coefficients', 'operators']
PLACEHOLDER = 'This page is still under production'
STYLES = ['/css/base.css', '/css/components.css', '/css/pages.css', '/css/maint.css', '/css/analysis.css',
          '/css/polish.css']          # the last one sets looks only (AMENDMENTS M21)
FONTS = ['barlow-400.ttf', 'barlow-500.ttf', 'barlow-700.ttf', 'barlow-condensed-400.ttf', 'barlow-condensed-600.ttf',
         'Barlow-OFL.txt', 'BarlowCondensed-OFL.txt']
# Files of the frame (work package 6). The rest of the screen belongs to the page packages and may still be missing.
FRAME_FILES = ['index.html', 'css/base.css', 'css/components.css', 'js/main.js', 'js/api.js', 'js/store.js', 'js/dom.js',
               'js/dialog.js', 'js/labels.js', 'js/frame.js', 'js/chart.js']
# The names the page packages build against (WORKPACKAGES.md H.3, AMENDMENTS M5.5). Nothing here may disappear.
FROZEN = {
    'api.js': ['get', 'post', 'ApiError'],
    'store.js': ['store'],
    'dom.js': ['h', 'setText', 'setClass', 'list', 'pager'],
    'dialog.js': ['confirm', 'choose', 'pad', 'pin', 'text', 'form', 'message'],
    'labels.js': ['round', 'slot', 'schedule', 'step', 'nano', 'fault', 'priority', 'group',
                  'fmtTime', 'fmtDuration', 'motorLabel', 'pumpText', 'stopMessage'],
    'chart.js': ['drawSpectrum'],
}
FORBIDDEN = {
    'innerHTML': re.compile(r'\binnerHTML\b'),
    'outerHTML': re.compile(r'\bouterHTML\b'),
    'insertAdjacentHTML': re.compile(r'\binsertAdjacentHTML\b'),
    'document.write': re.compile(r'\bdocument\s*\.\s*write(ln)?\s*\('),
    'eval(': re.compile(r'(?<![.\w$])eval\s*\('),
    'new Function': re.compile(r'\bnew\s+Function\b'),
    "setAttribute('style'": re.compile(r'''setAttribute\(\s*['"`]style['"`]'''),
}
# XML namespace names are identifiers, not addresses: nothing is ever fetched from them.
NAMESPACES = ('http://www.w3.org/2000/svg', 'http://www.w3.org/1999/xlink', 'http://www.w3.org/1999/xhtml')
ADDRESS = re.compile(r'https?://', re.IGNORECASE)


def files(*suffixes):
    return sorted(path for path in WEB.rglob('*') if path.is_file() and path.suffix in suffixes)


def read(path):
    return path.read_text(encoding='utf-8')


def name_of(path):
    return path.relative_to(WEB).as_posix()


def without_namespaces(text):
    for name in NAMESPACES:
        text = text.replace(name, '')
    return text


def local(path, base):
    """The file a web path means: '/js/x.js' from the web folder, './x.js' beside the file that names it."""
    clean = path.split('?')[0].split('#')[0]
    return (WEB / clean.lstrip('/')) if clean.startswith('/') else (base.parent / clean)


IMPORT = re.compile(r'''^\s*(?:import|export)\b[^'"`;]*?\bfrom\s*['"]([^'"]+)['"]|^\s*import\s*['"]([^'"]+)['"]''', re.MULTILINE)
DYNAMIC = re.compile(r'''\bimport\(\s*(['"`])([^'"`]*)\1\s*\)''')


def imports_of(text):
    found = [first or second for first, second in IMPORT.findall(text)]
    return found + [path for _quote, path in DYNAMIC.findall(text)]


def exported(text):
    names = set(re.findall(r'^export\s+(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)', text, re.MULTILINE))
    names |= set(re.findall(r'^export\s+class\s+([A-Za-z_$][\w$]*)', text, re.MULTILINE))
    for line in re.findall(r'^export\s+(?:const|let|var)\s+([^=;]+?)\s*=', text, re.MULTILINE):
        names |= {part.strip() for part in line.split(',')}
    for group in re.findall(r'^export\s*\{([^}]*)\}', text, re.MULTILINE):
        names |= {part.split(' as ')[-1].strip() for part in group.split(',') if part.strip()}
    return names


def array_of(text, name):
    match = re.search(r'\bconst\s+' + name + r'\s*=\s*\[([^\]]*)\]', text)
    return re.findall(r'''['"]([^'"]+)['"]''', match.group(1)) if match else None


# ---------------------------------------------------------------------------------------------- /api paths
API_USE = re.compile(r'''['"`](/api/[^'"`\s]*)''')


def api_paths(text):
    """(fixed part, has a changing tail) of every /api/ path written in a script."""
    found = set()
    for raw in API_USE.findall(text):
        fixed = raw.split('${')[0].split('?')[0]
        found.add((fixed, '${' in raw or fixed.endswith('/')))
    return found


def routes_of(source):
    """Every /api/ text in the source of the HTTP layer: its route table, whatever shape it has."""
    return {route.lstrip('^').rstrip('$') for route in re.findall(r'''['"](\^?/api/[^'"]*)['"]''', source)}


def route_known(path, changing, routes):
    if changing:
        return any(route.startswith(path) for route in routes)
    if path in routes:
        return True
    for route in routes:
        try:
            if re.fullmatch(route, path):
                return True
        except re.error:
            pass
    return False


class MarkupTests(unittest.TestCase):
    def test_the_frame_files_are_there(self):
        for name in FRAME_FILES:
            self.assertTrue((WEB / name).is_file(), name)

    def test_html_has_no_script_style_or_handler_of_its_own(self):
        pages = files('.html')
        self.assertTrue(pages)
        for path in pages:
            text = read(path)
            where = name_of(path)
            for attributes in re.findall(r'<script\b([^>]*)>', text, re.IGNORECASE):
                self.assertRegex(attributes, r'\bsrc\s*=', f'{where}: <script> without src')
            for body in re.findall(r'<script\b[^>]*>(.*?)</script>', text, re.IGNORECASE | re.DOTALL):
                self.assertEqual(body.strip(), '', f'{where}: script text inside the page')
            self.assertIsNone(re.search(r'(?i)<style\b', text), f'{where}: <style> block')
            self.assertIsNone(re.search(r'(?i)<[^>]*\sstyle\s*=', text), f'{where}: style attribute')
            self.assertIsNone(re.search(r'(?i)<[^>]*\son[a-z]+\s*=', text), f'{where}: on…= attribute')
            self.assertIsNone(re.search(r'(?i)javascript\s*:', text), f'{where}: javascript: address')
            self.assertIsNone(ADDRESS.search(without_namespaces(text)), f'{where}: outside address')

    def test_index_is_the_skeleton_the_pages_are_built_into(self):
        text = read(WEB / 'index.html')
        for part in ('class="hmi"', 'class="top"', 'class="banner', 'class="body"', 'class="nav"', 'class="foot"',
                     'id="overlay"', 'id="dialog"', 'id="btn-stop"'):
            self.assertIn(part, text, part)
        self.assertEqual(re.findall(r'<section class="page" id="page-([a-z]+)" hidden></section>', text), PAGES)
        self.assertEqual(re.findall(r'<button type="button"[^>]*data-page="([a-z]+)"', text), PAGES)
        self.assertEqual(re.findall(r'<link rel="stylesheet" href="([^"]+)">', text), STYLES)
        self.assertEqual(re.findall(r'<script\b([^>]*)>', text), [' type="module" src="/js/main.js"'])
        for words in ('Overview', 'Schedule measurement', 'manual operation', 'Trend', 'execution record', 'Correction', 'alarm', '🔧 Maintenance', 'Start scheduling', 'pause PAUSE',
                      'Stop STOP', 'No active alarm', 'confirm ACK', 'Alarm list', '⚡ contains high voltage', 'Physical emergency stop button'):
            self.assertIn(words, text, words)

    def test_every_element_the_frame_scripts_look_up_is_in_the_page(self):
        ids = set(re.findall(r'\bid="([^"]+)"', read(WEB / 'index.html')))
        for name in ('frame.js', 'dialog.js', 'main.js'):
            text = read(WEB / 'js' / name)
            wanted = set(re.findall(r'''(?:\$|getElementById)\(\s*'([^'$]+)'\s*\)''', text))
            self.assertTrue(wanted or name == 'main.js', name)
            self.assertEqual(sorted(wanted - ids), [], f'{name} looks up ids that index.html does not have')
        self.assertIn('data-through', read(WEB / 'index.html'))     # “Stop STOP” works through the scrim of a dialog

    def test_every_file_the_page_names_exists(self):
        text = read(WEB / 'index.html')
        missing = []
        for path in re.findall(r'''(?:href|src)\s*=\s*["']([^"']+)["']''', text):
            if path == '/guide' or path.startswith('data:'):    # the program serves the guide itself; data: is no file
                continue
            target = local(path, WEB / 'index.html')
            if not target.is_file():
                missing.append(target.relative_to(WEB).as_posix())
        mine = [name for name in missing if name in FRAME_FILES]
        self.assertEqual(mine, [])
        if missing:
            self.skipTest('not built yet (page packages): ' + ', '.join(missing))


class ScriptTests(unittest.TestCase):
    def test_no_script_uses_what_the_content_security_policy_forbids(self):
        scripts = files('.js')
        self.assertTrue(scripts)
        for path in scripts:
            text = read(path)
            for label, pattern in FORBIDDEN.items():
                self.assertIsNone(pattern.search(text), f'{name_of(path)}: {label}')
            self.assertIsNone(ADDRESS.search(without_namespaces(text)), f'{name_of(path)}: outside address')

    def test_the_screens_name_pumps_not_motors(self):
        """AMENDMENTS M16.1: a person reads “Pump n” (the name the program gives); “motor” is the board's word."""
        found = [name_of(path) for kind in ('.js', '.html', '.css') for path in files(kind) if 'motor' in read(path)]
        self.assertEqual(found, [])
        labels = read(WEB / 'js' / 'labels.js')
        for name in ('export function motorLabel(', 'export function motorShort('):
            self.assertIn(name, labels)

    def test_the_schedule_page_lists_the_steps_of_a_flow(self):
        """AMENDMENTS M23: the round written on the page is one tile that lists its steps; a tap opens the list
        (flowsteps.js). The style sheet lays the tile out as the script builds it."""
        script = read(WEB / 'js' / 'pages' / 'schedule.js')
        for words in ("tile(h, 'button', 'Steps', 'stage steps')", "h('div.sch-stages'", "el.grid.classList.toggle('staged', staged)",
                      "'/api/flow'", "import { editSteps, stepsLine } from '../flowsteps.js';", "ctx.api.post('/api/flow', {steps})"):
            self.assertIn(words, script)
        for gone in ('① Add liquid', '② Measure', '③ Empty', '④ Cleaning', 'fillText', 'data.flow'):
            self.assertNotIn(gone, script)
        style = read(WEB / 'css' / 'pages.css')
        for rule in ('#page-schedule .sch-stages {', '#page-schedule .sch-grid.staged > .stile {', '-webkit-line-clamp: 4;'):
            self.assertIn(rule, style)
        # the staged form is as high as the form of a Recipe: 48 + 128 + 72 + 72 and three gaps = four rows of 80 and three
        self.assertEqual(48 + 128 + 72 + 72 + 3 * 8, 4 * 80 + 3 * 8)
        for size in ('.sch-stages .stile { height: 128px;', '.sch-grid.staged > .stile { height: 72px;',
                     '.sch-grid.staged > .stile.both { height: 48px;'):
            self.assertIn(size, style)

    def test_the_step_list_offers_what_the_program_takes(self):
        """flowsteps.js writes rows of a Recipe; every action it can write is one recipe.FLOW_ACTIONS allows, and
        every one of those can be written (the syringe pump among them: the person's words of 2026-10-07)."""
        script = read(WEB / 'js' / 'flowsteps.js')
        named = set(re.findall(r"'((?:dose|syringe|plasma)_[a-z]+|clean|delay)'", script))
        source = re.search(r'^FLOW_ACTIONS = \(([^)]*)\)', read(WEB.parent / 'recipe.py'), re.M).group(1)
        self.assertEqual(named, set(re.findall(r"'([a-z_]+)'", source)))
        self.assertEqual(len(named), 10)
        for words in ('Steps ${index + 1}', "label: '＋ New steps'", "label: 'syringe pump'", "label: 'pump: How many times mL'", "label: 'Use two pumps together'",
                      "label: '⚡ Plasma＋Take spectrum'", "label: 'automatic cleaning'", "label: 'Waiting'", "label: 'Copy this step'", "label: 'Delete this step'"):
            self.assertIn(words, script)
        # A pump and its amount are never side by side with only a space between them (“Pump 1 5 mL” reads as 15 mL).
        for path in files('.js'):
            for bad in re.findall(r"\$\{[^}]*(?:label|short|pumpName)[^}]*\} \$\{[^}]*\} ?(?:mL|g)\b", read(path)):
                self.fail('%s: %s' % (name_of(path), bad))

    def test_the_schedule_page_sets_the_plasma_parameters(self):
        """AMENDMENTS M17: the seven parameters are one table (plasma.js), for Maintenance › plasma parameters and for Schedule measurement."""
        shared = read(WEB / 'js' / 'plasma.js')
        for key in ('Ontime', 'Offtime', 'Cycle', 'Loopnum', 'Integration_Time', 'Spectra_Interval', 'Voltage'):
            self.assertEqual(shared.count("{key: '%s'," % key), 1, key)
        for name in ('export const PARAMS', 'export function valuesOf(', 'export function topVoltage(', 'export function summary(',
                     'export function inWords(', 'export async function edit('):
            self.assertIn(name, shared)
        self.assertIn("ctx.api.post('/api/parameters'", shared)
        self.assertNotIn('unlock', shared)                          # no PIN is asked for there
        for page in ('schedule.js', 'maint-plasma.js'):
            text = read(WEB / 'js' / 'pages' / page)
            self.assertIn("from '../plasma.js'", text, page)
            self.assertNotIn("{key: 'Ontime'", text, page)         # no second table
        schedule = read(WEB / 'js' / 'pages' / 'schedule.js')
        for words in ("content: tile(h, 'button', 'measurement content', 'both')",
                      "tiles.content.box.addEventListener('click', edit(editParameters))",
                      "editParameters: () => editParameters(ctx)"):
            self.assertIn(words, schedule)
        steps = read(WEB / 'js' / 'flowsteps.js')              # M23: in a round written on the page, at its Plasma step
        for words in ("label: 'Change plasma parameters'", "{label: 'plasma parameters', value: PARAMETERS}", 'await io.editParameters()'):
            self.assertIn(words, steps)

    def test_the_footer_and_the_overview_offer_a_reading_taken_now(self):
        """AMENDMENTS M18: “Recheck” is one function (check.js), for the footer and for the two status cells of Overview."""
        shared = read(WEB / 'js' / 'check.js')
        self.assertIn("api.post('/api/rpi/check'", shared)
        self.assertIn('export async function checkNow(', shared)
        frame = read(WEB / 'js' / 'frame.js')
        for words in ("import { checkNow } from './check.js';", "h('button.btn.recheck'", "'Recheck'",
                      "const CHECKABLE = ['rpi_not_ready', 'hv_unknown', 'hv_on'];"):
            self.assertIn(words, frame)
        dash = read(WEB / 'js' / 'pages' / 'dash.js')
        self.assertIn("import { checkNow } from '../check.js';", dash)
        self.assertIn("{actions: [{label: 'Recheck', value: 'check'}]}", dash)
        self.assertIn('.prog > .recheck {', read(WEB / 'css' / 'base.css'))

    def test_the_maintenance_page_calibrates_one_pump(self):
        script = read(WEB / 'js' / 'pages' / 'maint-pumps.js')
        for words in ("'/api/pump/calibration'", "'/api/pump/calibrate'", "'/api/pump/calibrate/reset'", "op: 'dose_volume'"):
            self.assertIn(words, script)

    def test_every_import_names_a_file_that_exists(self):
        for path in files('.js'):
            for target in imports_of(read(path)):
                if '${' in target:              # the page modules: see the next test
                    continue
                self.assertRegex(target, r'^(\./|\.\./|/)', f'{name_of(path)}: import {target!r} is not a local path')
                self.assertTrue(local(target, path).is_file(), f'{name_of(path)}: import {target!r} does not exist')

    def test_every_page_has_a_module_or_the_placeholder(self):
        text = read(WEB / 'js' / 'main.js')
        self.assertEqual(array_of(text, 'PAGES'), PAGES)
        self.assertEqual(array_of(text, 'TABS'), TABS)
        self.assertIn('import(`/js/pages/${item.name}.js`)', text)
        self.assertIn(f"const PLACEHOLDER = '{PLACEHOLDER}'", text)
        self.assertTrue(re.search(r'await import\(`/js/pages/.{0,400}?\} catch \(error\) \{.{0,300}?placeholder\(item\);', text, re.DOTALL),
                        'a module that does not load must get the placeholder')
        names = PAGES + [f'maint-{tab}' for tab in TABS]
        folder = WEB / 'js' / 'pages'
        for name in names:
            path = folder / f'{name}.js'
            if not path.is_file():
                continue                        # main.js shows “This page is still under production” for it
            names_out = exported(read(path))
            self.assertLessEqual({'mount', 'render'}, names_out, f'{name_of(path)} must export mount and render')
        if folder.is_dir():
            for path in sorted(folder.glob('*.js')):
                text = read(path)
                for target in imports_of(text):
                    self.assertFalse(re.search(r'(^|/)(main|frame)\.js$', target), f'{name_of(path)}: pages reach the frame through ctx')

    def test_the_shared_modules_still_have_the_agreed_names(self):
        for name, wanted in FROZEN.items():
            have = exported(read(WEB / 'js' / name))
            self.assertEqual([item for item in wanted if item not in have], [], name)
        store = read(WEB / 'js' / 'store.js')
        for member in ('boot:', 'state:', 'stale:', 'unlock:', 'form:', 'preview:', 'subscribe(', 'setForm(', 'setUnlock('):
            self.assertIn(member, store, member)
        self.assertIn("'plasmic.schedule2'", store)
        self.assertIn("'plasmic.unlock'", store)
        self.assertTrue(re.search(r'\bsigned\b', read(WEB / 'js' / 'dialog.js')))     # the “±” key of AMENDMENTS M5.5
        self.assertIn("'±'", read(WEB / 'js' / 'dialog.js'))

    def test_the_fixture_is_off_unless_asked_for_and_the_program_is_not_there(self):
        text = read(WEB / 'js' / 'main.js')
        self.assertEqual(text.count("import('./fixtures.js')"), 1)
        self.assertTrue(re.search(r"await api\.bootstrap\(\{timeout: ABORT_MS\}\);\s*\} catch \(error\) \{.{0,400}?"
                                  r"if \(!location\.hash\.includes\(FIXTURE_FLAG\) \|\| fixtureTried\) throw error;.{0,120}?import\('\./fixtures\.js'\)",
                                  text, re.DOTALL), 'the fixture may load only after /api/bootstrap failed and only with #dev-fixture')
        for path in files('.js'):
            if path.name not in ('main.js', 'fixtures.js'):
                self.assertNotIn('fixtures.js', read(path), name_of(path))

    def test_the_route_check_itself_works(self):
        table = r'''ROUTES = [('GET', '/api/state', False), ('GET', r'^/api/runs/((?:HMI|CAP|LITE)-[0-9a-f]{32})\.csv$', False),
                              ('POST', '/api/stop', False), ('GET', '/api/recipes/examples/', False)]'''
        routes = routes_of(table)
        script = "get('/api/state'); post('/api/stop', {}); get(`/api/runs/${job}.csv`); get('/api/recipes/examples/' + file); get('/api/nothing?x=1')"
        used = api_paths(script)
        self.assertEqual(used, {('/api/state', False), ('/api/stop', False), ('/api/runs/', True),
                                ('/api/recipes/examples/', True), ('/api/nothing', False)})
        unknown = sorted(path for path, changing in used if not route_known(path, changing, routes))
        self.assertEqual(unknown, ['/api/nothing'])

    def test_every_api_path_of_the_scripts_is_a_route_of_the_program(self):
        if not HTTPD.is_file():
            self.skipTest('pc/httpd.py is not there yet: the /api paths of the scripts cannot be checked against its route table')
        routes = routes_of(HTTPD.read_text(encoding='utf-8'))
        self.assertTrue(routes, 'no /api route found in pc/httpd.py')
        unknown = []
        for path in files('.js'):
            for used, changing in sorted(api_paths(read(path))):
                if not route_known(used, changing, routes):
                    unknown.append(f'{name_of(path)}: {used}')
        self.assertEqual(unknown, [])


class FileTests(unittest.TestCase):
    def test_fonts_and_their_licences_are_there(self):
        for name in FONTS:
            path = WEB / 'fonts' / name
            self.assertTrue(path.is_file(), name)
            if OLD_FONTS != WEB / 'fonts' and (OLD_FONTS / name).is_file():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                                 hashlib.sha256((OLD_FONTS / name).read_bytes()).hexdigest(), name)
        for name in ('Barlow-OFL.txt', 'BarlowCondensed-OFL.txt'):
            self.assertIn('SIL OPEN FONT LICENSE', (WEB / 'fonts' / name).read_text(encoding='utf-8', errors='replace').upper())

    def test_web_files_are_utf8_without_bom_and_lf(self):
        checked = 0
        for path in files('.html', '.css', '.js'):
            data = path.read_bytes()
            where = name_of(path)
            self.assertFalse(data.startswith(b'\xef\xbb\xbf'), f'{where}: byte order mark')
            self.assertNotIn(b'\r', data, f'{where}: not LF')
            data.decode('utf-8')
            self.assertTrue(data.endswith(b'\n'), f'{where}: no newline at the end')
            checked += 1
        self.assertGreaterEqual(checked, len(FRAME_FILES))

    def test_style_sheets_name_only_files_of_this_folder(self):
        sheets = files('.css')
        self.assertTrue(sheets)
        for path in sheets:
            text = read(path)
            where = name_of(path)
            self.assertIsNone(re.search(r'(?i)@import', text), f'{where}: @import')
            self.assertIsNone(ADDRESS.search(without_namespaces(text)), f'{where}: outside address')
            for target in re.findall(r'''url\(\s*['"]?([^'")\s]+)['"]?\s*\)''', text):
                if target.startswith('data:'):
                    continue
                self.assertTrue(local(target, path).is_file(), f'{where}: url({target}) does not exist')

    def test_the_frame_keeps_its_sizes(self):
        """The numbers of SPEC E.1 / E.2 and AMENDMENTS M5.4 that every page relies on.

        The frame is 768 px high, always; its width follows the shape of the window (1024 px at 4:3, 1365 px at
        16:9, 1680 px at most): main.js sets --w beside the scale --k, and --wx, the surplus over 1024 as a number.
        """
        base = read(WEB / 'css' / 'base.css')
        for part in ('width: var(--w, 1024px); height: 768px', 'transform: scale(var(--k, 1))', '.top { flex: 0 0 56px',
                     '.banner { flex: 0 0 44px', '.nav { flex: 0 0 116px', '.foot { flex: 0 0 70px', '--nav-h: 50px; --nav-gap: 4px',
                     '.start { flex: 0 0 140px; height: 54px', '.stop { flex: 0 0 120px; height: 54px',
                     '.pause-btn { flex: 0 0 120px; height: 54px', '.estop { flex: 0 0 188px', 'padding: 8px 10px',
                     '--extra: calc(var(--wx, 0) * 1px)', 'container: hmi / inline-size'):
            self.assertIn(part, base, part)
        main = read(WEB / 'js' / 'main.js')
        for part in ('const FRAME_W = 1024;', 'const FRAME_H = 768;', 'const FRAME_MAX_W = 1680;',
                     "style.setProperty('--k', String(k))", "style.setProperty('--w', `${w}px`)",
                     "style.setProperty('--wx', String(w - FRAME_W))"):
            self.assertIn(part, main, part)
        # The frame is a touch screen: nothing pans or zooms, no rule needs a mouse, and full screen (the Fullscreen
        # API, asked for with one tap at the start) is never asked for with ?fs=0 or in the development fixture.
        self.assertIn('html { touch-action: none; }', base)
        for path in files('.css'):
            self.assertIsNone(re.search(r':hover\b', read(path)), f'{name_of(path)}: a rule that needs a mouse (:hover)')
        page = read(WEB / 'index.html')
        for words in ('Click the screen to start (full screen)', 'No need to go full screen', 'id="fsbtn"', 'id="fs-gate"'):
            self.assertIn(words, page, words)
        frame = read(WEB / 'js' / 'frame.js')
        for part in ("'Leave full screen'", "'full screen'", 'requestFullscreen', 'exitFullscreen', "'plasmic.fullscreen'", ".get('fs') !== '0'",
                     'hooks.gate !== false'):
            self.assertIn(part, frame, part)
        self.assertIn('gate: !location.hash.includes(FIXTURE_FLAG)', main)

    def test_the_last_style_sheet_sets_looks_only(self):
        """AMENDMENTS M21: polish.css rounds corners and softens lines. It comes last and may not move or resize
        anything — the layout of every page and the 44 px of every tap target are what the other files say."""
        self.assertEqual(STYLES[-1], '/css/polish.css')
        body = re.sub(r'/\*.*?\*/', '', read(WEB / 'css' / 'polish.css'), flags=re.S)
        for name in ('width', 'height', 'min-width', 'max-width', 'min-height', 'max-height', 'margin', 'padding', 'top', 'left',
                     'right', 'bottom', 'inset', 'position', 'display', 'flex', 'grid', 'gap', 'font', 'line-height',
                     'letter-spacing', 'transform', 'zoom', 'float'):
            found = re.search(r'(?<![-a-z])%s(-[a-z-]+)?\s*:' % name, body)
            self.assertIsNone(found, f'polish.css sets {name}: it is for looks only')
        for part in ('border-radius', 'box-shadow', '--line:', '.panel {'):
            self.assertIn(part, body)


if __name__ == '__main__':
    unittest.main(verbosity=2)
