#!/usr/bin/env python3
"""Plasmic Electronic Control - Local Web Server: Screen files and each URL (API.md C.1–C.5; AMENDMENTS M3, M4.1, M5.3, M7, M13) .

Just listen 127.0.0.1.This file does four things:

  1. URL list ROUTES: Each column is method, path, Do you need repairs? PIN (pin) , Will the schedule be rejected if it is in progress? (busy) , 
     Content cap (limit) .Do you want PIN Taken from application.PIN_ACTIONS (PIN There is only one copy of the table) ; busy this column
     is for explanation, the real inspection is Application (to The minimum unit of Devices Obtain the right to use the equipment).
  2. screen file: put the file in the screen folder when starting .html .css .js .ttf .txt Listed in a table (website → file),
     The file will only be sent if ”the URL is exactly the same as the one in the table”; the URL will never be used to form a file name, so If .., %2e, backslash,
     The absolute path cannot touch any files.
  3. Every reply comes with the same header (not cached,Content-Security-Policy…) ,The error is always
     {"error": Words for the screen, "code": code} (C.1 table) .
  4. POST : Written in the state ofHost → Origin with the picture token → Website → Maintenance PIN → Is the content JSON object.
     “Stop” (/api/stop) Don’t read the content, don’t PIN,always reply 200.

screen is in pc\\web.
"""
from __future__ import annotations

import hmac
import json
import math
import re
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import application
import recipe
from application import ApiError
from devices import Blocked, Busy
from plasmic_client import PlasmicError
from pumpboard import PumpBoardError

ROOT = Path(__file__).resolve().parent
GUIDE = ROOT.parent / 'guide.html'
GUIDE_FULL = ROOT.parent / 'docs' / 'detailed-guide.html'
GUIDES = {'/guide': GUIDE, '/docs/detailed-guide.html': GUIDE_FULL}
BODY_LIMIT = 100_000                 # bytes of a POST body
RECIPE_BODY_LIMIT = 300_000          # ... of a Recipe that is checked or published
DRAIN_LIMIT = 2_000_000              # a body that is too big is read up to here and thrown away, so the answer arrives
REQUEST_TIMEOUT_SECONDS = 60         # a connection that sends nothing for this long is given up
RESCAN_SECONDS = 1.0                 # practice mode: how often the web folder may be listed again
INTERNAL_REPEAT_SECONDS = 60.0       # the same internal error is written to the event log once in this time
CSP = ("default-src 'self'; style-src {style}; script-src 'self'; img-src 'self' data:; "
       "frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
CSP_PAGE = CSP.format(style="'self'")
CSP_GUIDE = CSP.format(style="'self' 'unsafe-inline'")
JSON_TYPE = 'application/json; charset=utf-8'
HTML_TYPE = 'text/html; charset=utf-8'
CSV_TYPE = 'text/csv; charset=utf-8'
TYPES = {'.html': HTML_TYPE, '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
         '.ttf': 'font/ttf', '.txt': 'text/plain; charset=utf-8'}
T_INTERNAL = 'Program internal error, details are in the event record'
T_STARTING = 'The program is starting, please wait.'
T_CLOSING = 'Program is ending'
E_INTERNAL = 'Program internal error ({where}) : {detail}'
NOT_FOUND = {'error': 'Not found', 'code': 'not_found'}
BAD_HOST = {'error': 'Invalid host'}
BAD_REQUEST = {'error': 'Invalid local request'}
# PumpBoardError.code -> (HTTP status, code of the reply)
PUMP_CODES = {'busy': (409, 'busy'), 'refused': (409, 'refused'), 'device': (409, 'refused'), 'blocked': (409, 'blocked'),
              'stopped': (409, 'state'), 'link': (503, 'pump_link'), 'timeout': (503, 'pump_link')}
# Ids are matched before anything is done with them (API.md C.1 "Ids in paths").
PARTS = {'<id>': '([0-9a-f]{32})', '<job>': '((?:HMI|CAP|LITE)-[0-9a-f]{32})', '<cal>': '(CAL-[0-9a-f]{32})',
         '<file>': '(' + '|'.join(re.escape(row['file'][:-len('.csv')]) for row in recipe.EXAMPLES) + ')'}


class File:
    """A reply that is a file to keep: bytes, content type, and the name the browser saves it under."""

    def __init__(self, data, content_type, name):
        self.data, self.type, self.name = data, content_type, name


class Ask:
    """What a GET handler may look at: the page token, the tab's PIN session, the query."""

    def __init__(self, csrf, session, query):
        self.csrf, self.session = csrf, session
        self.values = {key: values[-1] for key, values in parse_qs(query, keep_blank_values=True).items()}

    def number(self, name, default, low, high):
        raw = self.values.get(name, '')
        if raw == '':
            return default
        if not re.fullmatch(r'[0-9]{1,6}', raw) or not low <= int(raw) <= high:
            raise ValueError(f'{name} Must be {low}–{high} integer')
        return int(raw)

    def time(self, name):
        """An ISO time out of the query, or None. (A "+" that was not escaped arrives as a space.)"""
        raw = self.values.get(name, '')
        return raw.replace(' ', '+') if raw else None


def _get(path, run):
    return {'method': 'GET', 'path': path, 'pin': False, 'busy': False, 'limit': 0, 'run': run}


def _post(path, call, busy=False, limit=BODY_LIMIT):
    if call not in application.POST_ACTIONS:
        raise KeyError(call)
    return {'method': 'POST', 'path': path, 'pin': call in application.PIN_ACTIONS, 'busy': busy, 'limit': limit,
            'call': call}


# Every /api route of the program. `path` is the literal path, or a pattern with <id> (32 hex digits),
# <job> (a spectrum set), <cal> (a calibration record) or <file> (one of the three example files).
ROUTES = [
    _get('/api/bootstrap', lambda app, ask: dict(app.bootstrap(), csrf=ask.csrf)),
    _get('/api/state', lambda app, ask: app.state(ask.session)),
    _get('/api/recipes', lambda app, ask: {'recipes': app.recipes()}),
    _get('/api/recipes/<id>', lambda app, ask, key: app.recipe_detail(key)),
    _get('/api/recipes/<id>.csv', lambda app, ask, key: File(app.recipe_csv(key), CSV_TYPE, 'recipe-%s.csv' % key[:12])),
    _get('/api/recipes/examples/<file>.csv',
         lambda app, ask, name: File(app.example_csv(name + '.csv'), CSV_TYPE, name + '.csv')),
    _get('/api/runlog', lambda app, ask: app.runlog(ask.time('before'), ask.number('limit', 50, 1, 200))),
    _get('/api/rounds/<id>', lambda app, ask, key: app.round_record(key)),
    _get('/api/runs', lambda app, ask: app.runs(ask.number('limit', 200, 1, 200))),
    _get('/api/runs/<job>', lambda app, ask, job: app.run(job)),
    _get('/api/runs/<job>.csv', lambda app, ask, job: File(app.run_csv(job), CSV_TYPE, job + '.csv')),
    _get('/api/events', lambda app, ask: app.events(ask.time('before'), ask.number('limit', 50, 1, 200))),
    _get('/api/pump/ports', lambda app, ask: app.pump_ports()),
    _get('/api/pump/calibration', lambda app, ask: app.pump_calibration()),
    _get('/api/pump/setup', lambda app, ask: app.pump_setup()),
    _get('/api/flow', lambda app, ask: app.flow()),
    _get('/api/calibration', lambda app, ask: app.calibration()),
    _get('/api/calibrations/<cal>', lambda app, ask, key: app.calibration_record(key)),
    _get('/api/measurements', lambda app, ask: app.measurements(ask.number('hours', None, 1, 8784))),
    _post('/api/unlock', 'unlock'),
    _post('/api/lock', 'lock'),
    _post('/api/operator', 'set_operator'),
    _post('/api/operators', 'set_operators'),
    _post('/api/stop', 'stop'),
    _post('/api/pause', 'pause'),
    _post('/api/resume', 'resume'),
    _post('/api/schedule/preview', 'schedule_preview'),
    _post('/api/schedule/start', 'schedule_start'),
    _post('/api/flow', 'flow_save'),
    _post('/api/alarms/ack', 'alarms_ack'),
    _post('/api/manual', 'manual_op'),
    _post('/api/recipes/validate', 'recipe_validate', limit=RECIPE_BODY_LIMIT),
    _post('/api/recipes/publish', 'recipe_publish', limit=RECIPE_BODY_LIMIT),
    _post('/api/recipes/delete', 'recipe_delete'),
    _post('/api/rpi/config', 'rpi_config', busy=True),
    _post('/api/rpi/find', 'rpi_find'),
    _post('/api/rpi/diagnose', 'rpi_diagnose'),
    _post('/api/rpi/check', 'rpi_check'),
    _post('/api/rpi/tool', 'rpi_tool', busy=True),
    _post('/api/parameters', 'set_parameters', busy=True),
    _post('/api/pump/config', 'pump_config', busy=True),
    _post('/api/pump/reconnect', 'pump_reconnect', busy=True),
    _post('/api/pump/settings', 'pump_settings', busy=True),
    _post('/api/scale/tare', 'scale_tare', busy=True),
    _post('/api/scale/calibrate', 'scale_calibrate', busy=True),
    _post('/api/pump/calibrate', 'pump_calibrate', busy=True),
    _post('/api/pump/calibrate/reset', 'pump_calibrate_reset', busy=True),
    _post('/api/pump/setup', 'pump_setup_save', busy=True),
    _post('/api/calibration/plan', 'calibration_plan'),
    _post('/api/calibration/check', 'calibration_check'),
    _post('/api/calibration/start', 'calibration_start'),
    _post('/api/calibration/confirm', 'calibration_confirm'),
    _post('/api/calibration/analyze', 'calibration_analyze'),
    _post('/api/calibration/exclude', 'calibration_exclude'),
    _post('/api/calibration/apply', 'calibration_apply', busy=True),
    _post('/api/calibration-config', 'set_coefficients', busy=True),
    _post('/api/blank-qc', 'set_blank_qc'),
    _post('/api/blank-zero', 'set_blank_zero'),
]


def _pattern(path):
    return re.compile(''.join(PARTS[piece] if piece in PARTS else re.escape(piece)
                              for piece in re.split(r'(<[a-z]+>)', path)))


GET_EXACT = {route['path']: route for route in ROUTES if route['method'] == 'GET' and '<' not in route['path']}
GET_PATTERNS = [(_pattern(route['path']), route) for route in ROUTES if route['method'] == 'GET' and '<' in route['path']]
POSTS = {route['path']: route for route in ROUTES if route['method'] == 'POST'}


def find_get(path):
    """(route, the ids in the path) for a GET, or None."""
    route = GET_EXACT.get(path)
    if route is not None:
        return route, ()
    for pattern, route in GET_PATTERNS:
        match = pattern.fullmatch(path)
        if match:
            return route, match.groups()
    return None


def failure(exc):
    """(HTTP status, reply) for what a route raised (API.md C.1, AMENDMENTS M13); None: nobody foresaw it (500)."""
    if isinstance(exc, ApiError):
        reply = {'error': exc.text, 'code': exc.code}
        if exc.reasons is not None:
            reply['reasons'] = list(exc.reasons)
        return exc.status, reply
    if isinstance(exc, Blocked):
        return 409, {'error': str(exc), 'code': 'blocked', 'reasons': list(exc.reasons)}
    if isinstance(exc, Busy):
        return 409, {'error': str(exc), 'code': 'busy'}
    if isinstance(exc, PumpBoardError):
        status, code = PUMP_CODES.get(exc.code, (409, 'refused'))
        return status, {'error': exc.text, 'code': code}
    if isinstance(exc, PlasmicError):                # the RPi said no: its own code
        return 409, {'error': str(exc), 'code': str(exc.code or 'refused')}
    if isinstance(exc, KeyError):
        return 404, dict(NOT_FOUND)
    if isinstance(exc, ValueError):
        return 400, {'error': str(exc), 'code': 'bad_request'}
    if isinstance(exc, RuntimeError):
        return 409, {'error': str(exc), 'code': 'state'}
    return None


def _clean(value):
    """The same value with every number JSON cannot hold (NaN, infinity) replaced by null."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def encode(value):
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except ValueError:
        text = json.dumps(_clean(value), ensure_ascii=False, allow_nan=False)
    return text.encode('utf-8', 'replace')


def web_folder():
    """The folder of the screen's files."""
    return ROOT / 'web'


class StaticTable:
    """URL path -> (file, content type), made from a listing of the web folder (API.md C.2).

    A request is answered only when its path is exactly a key of the table. No file name is ever made
    from what a request says. `rescan` (practice mode only): when a path is not in the table, the folder
    is listed again, at most once a second — so a page that is being written shows up without a restart."""

    def __init__(self, folder, rescan=False):
        self.folder, self.rescan = Path(folder), bool(rescan)
        self.lock = threading.Lock()
        self.table, self.listed = {}, 0.0
        self.build()

    def build(self):
        table = {}
        try:
            root = self.folder.resolve()
            for path in sorted(self.folder.rglob('*')):
                kind = TYPES.get(path.suffix)
                if kind is None or not path.is_file() or not path.resolve().is_relative_to(root):
                    continue
                table['/' + path.relative_to(self.folder).as_posix()] = (path, kind)
        except OSError:
            pass
        if '/index.html' in table:
            table['/'] = table['/index.html']
        self.table, self.listed = table, time.monotonic()

    def find(self, url_path):
        hit = self.table.get(url_path)
        if hit is None and self.rescan and time.monotonic() - self.listed > RESCAN_SECONDS:
            with self.lock:
                self.build()
            hit = self.table.get(url_path)
        return hit


class ExclusiveHTTPServer(ThreadingHTTPServer):
    """Refuse a port another copy already holds (Windows would otherwise share it silently)."""
    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = 32

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return                               # the browser went away in the middle of a request
        super().handle_error(request, client_address)


class _Base(BaseHTTPRequestHandler):
    """What every reply of this program has in common."""
    server_version = 'Plasmic'
    sys_version = ''
    timeout = REQUEST_TIMEOUT_SECONDS

    def log_message(self, *_args):
        pass

    def trusted(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',
                                            f'localhost:{self.server.server_port}')

    def respond(self, code, value, content_type=JSON_TYPE, inline_style=False, download=None):
        data = bytes(value) if isinstance(value, (bytes, bytearray)) else encode(value)
        try:
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', CSP_GUIDE if inline_style else CSP_PAGE)
            if download:
                self.send_header('Content-Disposition', f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass

    def send_error(self, code, message=None, explain=None):
        """What the HTTP library itself refuses (a method this program does not have, a broken request
        line) gets the same kind of answer as everything else."""
        self.close_connection = True
        try:
            status = int(code)
            self.respond(status, {'error': str(message or 'Error'), 'code': 'internal' if status >= 500 else 'bad_request'})
        except Exception:
            pass

    def read_body(self, limit):
        """The request body, or None when it is longer than `limit` (it is then read and thrown away)."""
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if length <= 0:
            return b''
        if length <= limit:
            return self.rfile.read(length)
        left = min(length, DRAIN_LIMIT)
        while left > 0:
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                break
            left -= len(chunk)
        return None


class NotReady(_Base):
    """Stands in while the program is still starting (the port is taken first, so that a second copy
    of the program stops before it has touched anything)."""

    def do_GET(self):
        self.respond(503, {'error': T_STARTING, 'code': 'starting'})

    def do_POST(self):
        try:
            self.read_body(BODY_LIMIT)
        except OSError:
            return
        self.respond(503, {'error': T_STARTING, 'code': 'starting'})


class Closing(_Base):
    """Stands in from the moment the window is closed until the program is gone (a few seconds: it stops the
    pumps and the high voltage first). A copy that is started meanwhile reads the code and waits for the port."""

    def do_GET(self):
        self.respond(503, {'error': T_CLOSING, 'code': 'closing'})

    def do_POST(self):
        try:
            self.read_body(BODY_LIMIT)
        except OSError:
            return
        self.respond(503, {'error': T_CLOSING, 'code': 'closing'})


def make_handler(app, csrf, web=None):
    """The request handler for one Application. `csrf` is the page token of this program run."""
    table = StaticTable(web if web is not None else web_folder(), rescan=app.demo)
    told = {}                                    # internal errors already written to the event log: text -> when
    told_lock = threading.Lock()
    token = csrf.encode()

    def answer(exc, where):
        """(HTTP status, reply) for an exception of a route; what nobody foresaw goes to the event log."""
        known = failure(exc)
        if known is not None:
            return known
        try:
            frames = traceback.extract_tb(exc.__traceback__)
            place = '%s:%s' % (Path(frames[-1].filename).name, frames[-1].lineno) if frames else ''
            text = E_INTERNAL.format(where=where, detail='%r %s' % (exc, place))
            now = time.monotonic()
            with told_lock:
                fresh = now - told.get(text, float('-inf')) > INTERNAL_REPEAT_SECONDS
                if fresh:
                    if len(told) > 100:
                        told.clear()
                    told[text] = now
            if fresh:
                app.store.event(text, 'error')
        except Exception:
            pass
        return 500, {'error': T_INTERNAL, 'code': 'internal'}

    class Handler(_Base):
        def do_GET(self):
            path = ''
            try:
                if not self.trusted():
                    return self.respond(403, BAD_HOST)
                parts = urlsplit(self.path)
                path = parts.path
                document = GUIDES.get(unquote(path))
                if document is not None:
                    try:
                        guide = document.read_bytes()
                    except OSError:              # the folder was copied without it
                        return self.respond(404, NOT_FOUND)
                    return self.respond(200, guide, HTML_TYPE, inline_style=True)
                if not path.startswith('/api/'):
                    hit = table.find(path)
                    if hit is None:
                        return self.respond(404, NOT_FOUND)
                    try:
                        data = hit[0].read_bytes()
                    except OSError:              # removed since the folder was listed
                        return self.respond(404, NOT_FOUND)
                    return self.respond(200, data, hit[1])
                found = find_get(path)
                if found is None:
                    return self.respond(404, NOT_FOUND)
                route, ids = found
                value = route['run'](app, Ask(csrf, self.headers.get('X-Unlock'), parts.query), *ids)
                if isinstance(value, File):
                    return self.respond(200, value.data, value.type, download=value.name)
                return self.respond(200, value)
            except (ConnectionError, TimeoutError):
                return None
            except Exception as exc:
                return self.respond(*answer(exc, 'GET ' + path[:80]))

        def do_POST(self):
            path = ''
            try:
                try:
                    path = urlsplit(self.path).path
                except ValueError:
                    path = ''
                route = POSTS.get(path)
                raw = self.read_body(route['limit'] if route else BODY_LIMIT)   # first: an unread body can lose the answer
                if not self.trusted():
                    return self.respond(403, BAD_HOST)
                origins = (None, f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')
                sent = (self.headers.get('X-Token') or '').encode('utf-8', 'replace')
                if self.headers.get('Origin') not in origins or not hmac.compare_digest(sent, token):
                    return self.respond(403, BAD_REQUEST)
                if route is None:
                    return self.respond(404, NOT_FOUND)
                try:
                    body = json.loads(raw) if raw else None
                except (ValueError, RecursionError):
                    body = None                  # the Application says "need JSON object" — after the PIN gate
                return self.respond(200, app.post(route['call'], body, self.headers.get('X-Unlock')))
            except (ConnectionError, TimeoutError):
                return None
            except Exception as exc:
                return self.respond(*answer(exc, 'POST ' + path[:80]))

    return Handler
