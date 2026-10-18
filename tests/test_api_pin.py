"""Maintenance PIN (API.md C.1, C.4 of“PIN”column;AMENDMENTS M5.3; SPEC B.10) .

Check according to the table: the following POSTS/GETS is from API.md and AMENDMENTS M5.3 copied. First confirm the program’s own URL list (httpd.ROUTES) 
is exactly the same, but actually sends it once to each URL:

  * want PIN : Not logged in → 403 locked; Passed this level after logging in; not set yet PIN → 403 pin_unset.
  * don’t want PIN of, and every GET: can be used without logging in. ”Stop” forever 200.
  * delete Recipe Please enter again PIN; /api/state Login will not be extended; only maintenance actions that pass inspection will be extended.
  * The scheduled measurement is in progress, and all equipment or set maintenance actions that will be moved will be returned. 409 busy (“Busy rule”column).

All on the emulator:RPi Which side is test_tcpd.SimServer (local machine 127.0.0.1) ,Pump control board is not used; cannot read or write pc\\data.

    python tests/test_api_pin.py
"""
from __future__ import annotations

import hashlib
import importlib
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'rpi' / 'tcpd'), str(ROOT / 'pc'), str(ROOT / 'tests')]

import core_testing as ct  # noqa: E402  (first: it sets PLASMIC_NO_SERIAL=1 before anything else is loaded)
import application  # noqa: E402
import httpd  # noqa: E402
import pinlock  # noqa: E402
from test_tcpd import SimServer, wait_until  # noqa: E402

pc = importlib.import_module('app')

PIN = '246810'
LOCKED = {'error': 'This operation is only for maintenance personnel (maintenance PIN) ', 'code': 'locked'}
UNSET = {'error': 'has not been set for maintenance PIN', 'code': 'pin_unset'}
GOOD = {'Ontime': 25, 'Offtime': 5, 'Cycle': 25, 'Loopnum': 4, 'Integration_Time': 1000, 'Spectra_Interval': 1500, 'Voltage': 300}
NO_ID = '0' * 32
NO_CAL = 'CAL-' + NO_ID
# AMENDMENTS M20: what each pump carries (water and OUT are the two pumps of the cleaning) and the pump of each valve.
SETUP = {'pumps': [{'motor': 2, 'use': 'water'}] + [{'motor': motor, 'use': 'none'} for motor in (3, 4, 5, 6, 7)]
         + [{'motor': 8, 'use': 'out'}, {'motor': 9, 'use': 'chemical', 'name': 'CuSO₄'}],
         'valves': [{'id': 1, 'motor': None}, {'id': 2, 'motor': None}]}

# path -> needs the maintenance PIN. API.md C.4, then AMENDMENTS M5.3.
POSTS = {
    '/api/unlock': False, '/api/lock': False, '/api/operator': False, '/api/operators': True,
    '/api/stop': False, '/api/pause': False, '/api/resume': False,
    '/api/schedule/preview': False, '/api/schedule/start': False, '/api/alarms/ack': False, '/api/manual': False,
    '/api/flow': False,                                               # AMENDMENTS M16: the flow of the schedule page
    '/api/parameters': False,                                         # M17: the schedule page sets the plasma parameters too
    '/api/rpi/check': False,                                          # M18: “Recheck”, a reading taken now
    '/api/recipes/validate': True, '/api/recipes/publish': True, '/api/recipes/delete': True,
    '/api/rpi/config': True, '/api/rpi/find': True, '/api/rpi/diagnose': True, '/api/rpi/tool': True,
    '/api/pump/config': True, '/api/pump/reconnect': True, '/api/pump/settings': True,
    '/api/scale/tare': True, '/api/scale/calibrate': True,
    '/api/pump/calibrate': True, '/api/pump/calibrate/reset': True,   # M16: the dose factor of one pump
    '/api/pump/setup': True,                                          # M20: what each pump carries, the valve links
    '/api/calibration/plan': False, '/api/calibration/check': False, '/api/calibration/start': False,
    '/api/calibration/confirm': False, '/api/calibration/analyze': False, '/api/calibration/exclude': False,
    '/api/calibration/apply': True, '/api/calibration-config': True, '/api/blank-qc': True, '/api/blank-zero': True,
}
# The column "Busy rule": refused with 409 `busy` while a schedule is busy. A body the route would accept otherwise.
BUSY = {
    '/api/rpi/config': {'rpi_host': 'localhost', 'rpi_port': 8767},
    '/api/rpi/tool': {'cmd': 'prepare'},
    '/api/parameters': {'parameters': GOOD},
    '/api/pump/config': {'enabled': False},
    '/api/pump/reconnect': {},
    '/api/pump/settings': {'syringe_ul': 5000, 'syringe_speed_ml_s': 0.4},
    '/api/scale/tare': {},
    '/api/scale/calibrate': {'grams': 100},
    '/api/pump/calibrate': {'motor': 3, 'measured_ml': 9.8},
    '/api/pump/calibrate/reset': {'motor': 3},
    '/api/pump/setup': SETUP,
    '/api/calibration/apply': {'id': NO_CAL},
    '/api/calibration-config': {'config': {metal: {'a': '1', 'b': '0', 'c': '1'} for metal in ('Cu', 'Zn', 'Ni')}},
}
BIG_BODY = {'/api/recipes/validate', '/api/recipes/publish'}        # up to 300 000 bytes; everything else 100 000
# Every GET (all open), with a path to ask: C.3, then M5.3.
GETS = {
    '/api/bootstrap': '/api/bootstrap', '/api/state': '/api/state', '/api/recipes': '/api/recipes',
    '/api/recipes/<id>': '/api/recipes/' + NO_ID, '/api/recipes/<id>.csv': f'/api/recipes/{NO_ID}.csv',
    '/api/recipes/examples/<file>.csv': '/api/recipes/examples/plasma-only.csv',
    '/api/runlog': '/api/runlog?limit=5', '/api/rounds/<id>': '/api/rounds/' + NO_ID, '/api/runs': '/api/runs?limit=5',
    '/api/runs/<job>': '/api/runs/HMI-' + NO_ID, '/api/runs/<job>.csv': f'/api/runs/HMI-{NO_ID}.csv',
    '/api/events': '/api/events?limit=5', '/api/pump/ports': '/api/pump/ports',
    '/api/pump/calibration': '/api/pump/calibration', '/api/pump/setup': '/api/pump/setup', '/api/flow': '/api/flow',
    '/api/calibration': '/api/calibration', '/api/calibrations/<cal>': '/api/calibrations/' + NO_CAL,
    '/api/measurements': '/api/measurements?hours=1',
}
GATED = sorted(path for path, pin in POSTS.items() if pin)
OPEN = sorted(path for path, pin in POSTS.items() if not pin)


def setUpModule():
    ct.set_up()


def tearDownModule():
    ct.tear_down()


def write_pin(folder, pin=PIN, iterations=1000):
    """pin.json in the format Set-PIN.cmd writes, with few iterations so that a test does not wait for the hash."""
    salt = hashlib.sha256(pin.encode()).digest()
    digest = hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, iterations)
    Path(folder, 'pin.json').write_text(json.dumps({'salt': salt.hex(), 'digest': digest.hex(), 'iterations': iterations}),
                                        encoding='utf-8')


class Harness:
    """The pretend RPi + the application (no pump board) + its local web server, in a temp folder."""

    def __init__(self, pin=PIN):
        self.sim = SimServer(40)
        self.sim.controller.audit = lambda *args, **kwargs: None
        self.temp = tempfile.TemporaryDirectory(prefix='plasmic-pin-')
        self.folder = Path(self.temp.name)
        (self.folder / 'config.json').write_text(json.dumps({'rpi_host': '127.0.0.1', 'rpi_port': self.sim.port, 'token': ''}),
                                                 encoding='utf-8')
        if pin:
            write_pin(self.folder, pin)
        self.app = self.http = None
        try:
            self.app = pc.Application(self.temp.name)
            self.csrf = 'test-csrf-token'
            self.http = pc.ExclusiveHTTPServer(('127.0.0.1', 0), pc.make_handler(self.app, self.csrf))
            self.port = self.http.server_address[1]
            threading.Thread(target=self.http.serve_forever, daemon=True).start()
            # The pretend Nano has just been switched on: the program loads its parameters by itself, once.
            wait_until(lambda: self.app.rpi.params_view()['nano_synced'] is True)
        except BaseException:
            self.close()
            raise

    def get(self, path, session=''):
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', headers={'X-Unlock': session})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read()
                kind = response.headers['Content-Type']
                return response.status, json.loads(body) if kind.startswith('application/json') else body
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def post(self, path, body, session=''):
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', json.dumps(body).encode(),
                                         {'Content-Type': 'application/json', 'X-Token': self.csrf, 'X-Unlock': session})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def unlock(self, pin=PIN):
        code, reply = self.post('/api/unlock', {'pin': pin})
        assert code == 200, reply
        return reply['session']

    def state(self, session=''):
        return self.get('/api/state', session)[1]

    def texts(self):
        return [row['text'] for row in self.app.store.events(limit=600)]

    def close(self):
        try:
            if self.http is not None:
                self.http.shutdown()
                self.http.server_close()
        finally:
            try:
                if self.app is not None:
                    self.app.close()
            finally:
                try:
                    self.sim.close()
                finally:
                    self.temp.cleanup()


class Case(unittest.TestCase):
    pin = PIN

    def setUp(self):
        self.h = Harness(self.pin)
        self.addCleanup(self.h.close)
        self.addCleanup(lambda: self.assertEqual(self.h.sim.backend.run_stop, 0))


class TableTests(unittest.TestCase):
    """The program's own route table is the contract, row for row."""

    def test_the_route_table_is_the_table_of_the_contract(self):
        posts = {route['path']: route for route in httpd.ROUTES if route['method'] == 'POST'}
        gets = {route['path']: route for route in httpd.ROUTES if route['method'] == 'GET'}
        self.assertEqual(sorted(posts), sorted(POSTS))
        self.assertEqual({path: route['pin'] for path, route in posts.items()}, POSTS)
        self.assertEqual(sorted(path for path, route in posts.items() if route['busy']), sorted(BUSY))
        self.assertEqual({path for path, route in posts.items() if route['limit'] == 300_000}, BIG_BODY)
        self.assertEqual({route['limit'] for path, route in posts.items() if path not in BIG_BODY}, {100_000})
        self.assertEqual(sorted(gets), sorted(GETS))
        self.assertEqual({(route['pin'], route['busy']) for route in gets.values()}, {(False, False)})     # every GET is open
        self.assertEqual(len(httpd.ROUTES), len(POSTS) + len(GETS))
        # Whatever the busy rule guards is a maintenance action — but for the plasma parameters, which the person set
        # free of the PIN (M17): they are changed on the schedule page, and still never while a schedule is busy.
        self.assertEqual(set(BUSY) - set(GATED), {'/api/parameters'})

    def test_the_pin_table_of_the_application_names_the_same_actions(self):
        calls = {route['path']: route['call'] for route in httpd.ROUTES if route['method'] == 'POST'}
        self.assertEqual({calls[path] for path in GATED}, set(application.PIN_ACTIONS))
        self.assertEqual({calls[path] for path in OPEN}, set(application.OPEN_ACTIONS))
        self.assertFalse(application.PIN_ACTIONS & application.OPEN_ACTIONS)
        self.assertNotIn('stop', application.PIN_ACTIONS)      # STOP never asks
        for name in application.POST_ACTIONS:
            self.assertTrue(callable(getattr(pc.Application, name)), name)


class GateTests(Case):
    def test_every_gated_post_is_locked_without_a_session(self):
        before = self.h.texts()
        stale = self.h.unlock()
        self.assertEqual(self.h.post('/api/lock', {}, stale), (200, {'locked': True}))
        for session in ('', 'made-up-session', stale, PIN):
            for path in GATED:
                self.assertEqual(self.h.post(path, BUSY.get(path, {}), session), (403, LOCKED), (path, session))
        self.assertEqual(sorted(text for text in self.h.texts() if text not in before),
                         ['Maintenance PIN Unlock', 'Maintenance mode has been logged out'])    # the login and the logout of this test, and nothing else was done
        self.assertEqual(self.h.app.store.config['rpi_host'], '127.0.0.1')
        self.assertIsNone(self.h.sim.controller.job)
        state = self.h.state()
        self.assertEqual((state['unlocked'], state['unlock_left_s'], state['pin_set']), (False, 0, True))

    def test_every_gated_post_passes_the_gate_with_a_session(self):
        session = self.h.unlock()
        for path in GATED:
            code, reply = self.h.post(path, {}, session)
            self.assertNotEqual(code, 403, path)
            self.assertNotIn(reply.get('code'), ('locked', 'pin_unset'), path)
            self.assertIn(code, (200, 400, 404, 409, 503), (path, reply))
        self.assertTrue(self.h.state(session)['unlocked'])
        self.assertIsNone(self.h.sim.controller.job)

    def test_every_open_route_works_without_a_session(self):
        for path in OPEN:
            code, reply = self.h.post(path, {})
            self.assertNotEqual(code, 403, path)
            self.assertIn(code, (200, 400, 404, 409), (path, reply))
        for session in ('', 'made-up-session'):                # every GET is open, whatever the tab says about itself
            for pattern, path in GETS.items():
                code, _reply = self.h.get(path, session)
                self.assertIn(code, (200, 404), (pattern, path))
        self.assertEqual(self.h.get('/api/recipes')[1]['recipes'][0]['name'], 'Only discharge measurement')
        code, reply = self.h.post('/api/stop', {})            # never refused, never asks
        self.assertEqual((code, reply['accepted']), (200, True))

    def test_what_an_operator_does_needs_no_pin(self):
        """SPEC B.10: choose the operator, start (after the tick), pause, resume, stop, acknowledge."""
        self.assertEqual(self.h.post('/api/operator', {'name': 'operator'}), (200, {'operator': 'operator'}))
        seed = self.h.get('/api/recipes')[1]['recipes'][0]['id']
        body = {'recipe_id': seed, 'schedule': {'start': {'mode': 'now'}, 'interval_minutes': 5, 'end': {'mode': 'count', 'count': 2}}}
        self.assertEqual(self.h.post('/api/schedule/preview', body)[0], 200)
        code, reply = self.h.post('/api/schedule/start', dict(body, confirm={'high_voltage': True}))
        self.assertEqual((code, reply['accepted']), (200, True))
        wait_until(lambda: self.h.app.runner.view()['counts'].get('done') == 1 and self.h.app.runner.view()['status'] == 'waiting')
        self.assertEqual(self.h.post('/api/pause', {}), (200, {'accepted': True, 'status': 'paused'}))
        self.assertEqual(self.h.post('/api/resume', {}), (200, {'accepted': True, 'status': 'waiting'}))
        self.h.app.alarms.raise_('test_alarm', 3, 'test', 'Alarm for testing', 'event')
        self.assertEqual(self.h.post('/api/alarms/ack', {'all': True}), (200, {'acked': 1}))
        code, reply = self.h.post('/api/stop', {})
        self.assertEqual((code, reply['schedule_ended']), (200, True))
        wait_until(lambda: not self.h.app.runner.active)
        self.assertEqual(self.h.state()['unlocked'], False)

    def test_busy_routes_are_refused_while_a_schedule_is_busy(self):
        session = self.h.unlock()
        self.h.app.set_operator({'name': 'operator'})
        seed = self.h.get('/api/recipes')[1]['recipes'][0]['id']
        body = {'recipe_id': seed, 'confirm': {'high_voltage': True},
                'schedule': {'start': {'mode': 'now'}, 'interval_minutes': 60, 'end': {'mode': 'count', 'count': 2}}}
        self.assertEqual(self.h.post('/api/schedule/start', body)[0], 200)
        wait_until(lambda: self.h.app.runner.view()['counts'].get('done') == 1 and self.h.app.runner.view()['status'] == 'waiting')
        before = (dict(self.h.app.store.config), self.h.app.analyst.coefficients(), self.h.state()['rpi']['parameters'])
        busy = {'error': 'Scheduled measurement is in progress: press ”Stop” first. STOP”,or wait it out', 'code': 'busy'}
        for path, good in sorted(BUSY.items()):
            self.assertEqual(self.h.post(path, good, session), (409, busy), path)
        self.assertEqual((dict(self.h.app.store.config), self.h.app.analyst.coefficients(), self.h.state()['rpi']['parameters']), before)
        # What the busy rule does not guard still works while the schedule waits.
        self.assertEqual(self.h.post('/api/rpi/diagnose', {}, session)[0], 200)
        self.assertEqual(self.h.post('/api/operators', {'operators': ['operator', 'Wang Xiaoming']}, session)[0], 200)
        qc = self.h.get('/api/measurements')[1]['qc']
        self.assertEqual(self.h.post('/api/blank-qc', {'qc': qc}, session), (200, {'qc': qc}))
        # Paused between two slots the equipment is free again (SPEC B.4) ...
        self.assertEqual(self.h.post('/api/pause', {})[1]['status'], 'paused')
        self.assertEqual(self.h.post('/api/rpi/tool', {'cmd': 'prepare'}, session)[0], 200)
        self.assertEqual(self.h.post('/api/resume', {})[1]['status'], 'waiting')
        self.assertEqual(self.h.post('/api/rpi/tool', {'cmd': 'prepare'}, session), (409, busy))
        # The plasma parameters need no PIN (M17) — and are refused all the same while the schedule is busy.
        self.assertEqual(self.h.post('/api/parameters', {'parameters': GOOD}), (409, busy))
        # ... and after STOP every one of them passes the busy rule.
        self.assertEqual(self.h.post('/api/stop', {})[0], 200)
        wait_until(lambda: not self.h.app.runner.active)
        for path, good in sorted(BUSY.items()):
            if path == '/api/rpi/config':
                continue                                       # it would change where this test is connected to
            code, reply = self.h.post(path, good, session)
            self.assertNotEqual(reply.get('code'), 'busy', path)
            self.assertNotEqual(code, 403, path)


class ScheduleSettingsTests(Case):
    def test_the_plasma_parameters_are_saved_without_a_session(self):
        """AMENDMENTS M17: the person sets them on the schedule page (his answer: “The schedule page can be modified directly without PIN”)."""
        before = self.h.texts()
        code, reply = self.h.post('/api/parameters', {'parameters': GOOD})
        self.assertEqual((code, reply['saved'], reply['synced']), (200, True, True), reply)
        self.assertEqual({key: reply['parameters'][key] for key in GOOD}, GOOD)
        wait_until(lambda: (self.h.state()['rpi']['parameters'] or {}).get('values') == GOOD)
        said = [text for text in self.h.texts() if text not in before]
        self.assertTrue(any(text.startswith('Stored plasma parameters') for text in said), said)
        self.assertFalse(self.h.state()['unlocked'])               # nobody logged in for it
        self.assertIsNone(self.h.sim.controller.job)               # and nothing was started
        # What the RPi refuses is refused as before, with its sentence.
        code, reply = self.h.post('/api/parameters', {'parameters': dict(GOOD, Spectra_Interval=500)})
        self.assertEqual(code, 409, reply)
        self.assertIn('Spectra_Interval', reply['error'])
        self.assertEqual(self.h.state()['rpi']['parameters']['values'], GOOD)
        # The other tools of Maintenance › plasma parameters keep their PIN.
        for body in ({'cmd': 'prepare'}, {'cmd': 'clear_fault'}, {'cmd': 'spectrum'}):
            self.assertEqual(self.h.post('/api/rpi/tool', body), (403, LOCKED), body)


class NoPinTests(Case):
    pin = None

    def test_without_a_pin_file_maintenance_is_closed(self):
        state = self.h.state()
        self.assertEqual((state['pin_set'], state['setup']['pin'], state['setup']['complete'], state['unlocked']),
                         (False, False, False, False))
        self.assertEqual(self.h.get('/api/bootstrap')[1]['pin_set'], False)
        for session in ('', 'made-up-session'):
            for path in GATED:
                self.assertEqual(self.h.post(path, BUSY.get(path, {}), session), (403, UNSET), path)
        self.assertEqual(self.h.post('/api/unlock', {'pin': PIN}), (409, {'error': 'has not been set for maintenance PIN', 'code': 'pin'}))
        for path in OPEN:                                      # operating stays open
            self.assertNotEqual(self.h.post(path, {})[0], 403, path)
        for pattern, path in GETS.items():
            self.assertIn(self.h.get(path)[0], (200, 404), pattern)
        self.assertEqual(self.h.post('/api/stop', {})[0], 200)
        # Set-PIN.cmd takes effect without a restart.
        write_pin(self.h.folder)
        self.assertEqual(self.h.state()['pin_set'], True)
        self.assertEqual(self.h.post('/api/operators', {'operators': ['operator']}), (403, LOCKED))
        session = self.h.unlock()
        self.assertEqual(self.h.post('/api/operators', {'operators': ['operator']}, session)[0], 200)
        self.assertEqual(self.h.state(session)['setup']['pin'], True)


class SessionTests(Case):
    def test_the_state_does_not_renew_a_session_and_a_gated_post_does(self):
        original, pinlock.PinLock.SESSION_SECONDS = pinlock.PinLock.SESSION_SECONDS, 2
        self.addCleanup(setattr, pinlock.PinLock, 'SESSION_SECONDS', original)
        code, reply = self.h.post('/api/unlock', {'pin': PIN})
        self.assertEqual((code, reply['expires_in']), (200, 2))
        session = reply['session']
        end = time.monotonic() + 1.2
        while time.monotonic() < end:                          # the page polls, and does things that need no PIN
            state = self.h.state(session)
            self.assertEqual(state['unlocked'], True)
            self.assertTrue(0 < state['unlock_left_s'] <= 2)
            self.assertEqual(self.h.post('/api/alarms/ack', {'all': True}, session)[0], 200)
            self.assertEqual(self.h.get('/api/recipes', session)[0], 200)
            time.sleep(.1)
        self.assertEqual(self.h.post('/api/rpi/diagnose', {}, session)[0], 200)     # a maintenance action: 2 s from now again
        time.sleep(1.3)
        self.assertEqual(self.h.state(session)['unlocked'], True)                   # 2.5 s after the login
        end = time.monotonic() + 1.2
        while time.monotonic() < end:                          # only looking: the session runs out
            self.h.state(session)
            self.h.post('/api/alarms/ack', {'all': True}, session)
            time.sleep(.1)
        state = self.h.state(session)
        self.assertEqual((state['unlocked'], state['unlock_left_s']), (False, 0))
        self.assertEqual(self.h.post('/api/rpi/diagnose', {}, session), (403, LOCKED))
        # A gated POST that is refused for another reason has still passed the gate, and renews.
        session = self.h.unlock()
        time.sleep(1.2)
        self.assertEqual(self.h.post('/api/operators', {}, session)[0], 400)
        time.sleep(1.2)
        self.assertEqual(self.h.state(session)['unlocked'], True)

    def test_a_session_belongs_to_one_tab_and_lock_ends_it(self):
        one, two = self.h.unlock(), self.h.unlock()
        self.assertNotEqual(one, two)
        self.assertEqual([self.h.state(session)['unlocked'] for session in (one, two, '')], [True, True, False])
        self.assertEqual(self.h.post('/api/lock', {}, one), (200, {'locked': True}))
        self.assertEqual([self.h.state(session)['unlocked'] for session in (one, two)], [False, True])
        self.assertEqual(self.h.post('/api/rpi/diagnose', {}, one), (403, LOCKED))
        self.assertEqual(self.h.post('/api/rpi/diagnose', {}, two)[0], 200)
        self.assertEqual(self.h.post('/api/lock', {}, 'made-up-session'), (200, {'locked': True}))
        self.assertEqual(self.h.post('/api/lock', {}, ''), (200, {'locked': True}))
        # Neither the session nor the PIN ever comes back in a reply.
        seen = json.dumps([self.h.state(two), self.h.get('/api/bootstrap')[1], self.h.get('/api/events?limit=200')[1]])
        for secret in (one, two, PIN):
            self.assertNotIn(secret, seen)
        digest = json.loads((self.h.folder / 'pin.json').read_text(encoding='utf-8'))['digest']
        self.assertNotIn(digest, seen)

    def test_a_replaced_pin_file_ends_every_session(self):
        session = self.h.unlock()
        self.assertEqual(self.h.post('/api/rpi/diagnose', {}, session)[0], 200)
        time.sleep(0.05)
        write_pin(self.h.folder, '13579246')                   # Set-PIN.cmd was run again
        self.assertEqual(self.h.state(session)['unlocked'], False)
        self.assertEqual(self.h.post('/api/rpi/diagnose', {}, session), (403, LOCKED))
        self.assertEqual(self.h.post('/api/unlock', {'pin': PIN}), (409, {'error': 'Maintenance PIN mistake', 'code': 'pin'}))
        self.assertEqual(self.h.post('/api/rpi/diagnose', {}, self.h.unlock('13579246'))[0], 200)

    def test_wrong_pins_are_counted_and_then_shut_out(self):
        wrong = (409, {'error': 'Maintenance PIN mistake', 'code': 'pin'})
        for pin in ('000000', '', None, 246810, ['246810']):  # five wrong ones, of whatever kind
            self.assertEqual(self.h.post('/api/unlock', {'pin': pin}), wrong, pin)
        code, reply = self.h.post('/api/unlock', {'pin': PIN})     # now even the right one has to wait
        self.assertEqual((code, reply['code']), (409, 'pin'))
        self.assertRegex(reply['error'], r'^Too many attempts, please \d+ Try again in seconds$')
        self.assertEqual(self.h.state()['unlocked'], False)
        self.assertNotIn('Maintenance PIN Unlock', self.h.texts())

    def test_deleting_a_recipe_needs_the_pin_again(self):
        session = self.h.unlock()
        code, made = self.h.post('/api/recipes/publish', {'name': 'To be deleted', 'csv': ct.PLASMA_ONLY}, session)
        self.assertEqual(code, 200)
        path = self.h.folder / 'recipes' / (made['id'] + '.json')
        wrong = (409, {'error': 'Maintenance PIN mistake', 'code': 'pin'})
        self.assertEqual(self.h.post('/api/recipes/delete', {'id': made['id'], 'pin': PIN}), (403, LOCKED))       # no session
        self.assertEqual(self.h.post('/api/recipes/delete', {'id': made['id']}, session), wrong)                  # no PIN typed
        self.assertEqual(self.h.post('/api/recipes/delete', {'id': made['id'], 'pin': '135790'}, session), wrong)
        self.assertEqual(self.h.post('/api/recipes/delete', {'id': made['id'], 'pin': session}, session), wrong)
        self.assertTrue(path.is_file())
        self.assertEqual(len(self.h.get('/api/recipes')[1]['recipes']), 2)
        self.assertEqual(self.h.post('/api/recipes/delete', {'id': made['id'], 'pin': PIN}, session), (200, {'deleted': True}))
        self.assertFalse(path.exists())
        self.assertEqual(len(list((self.h.folder / 'recipes' / 'deleted').glob(made['id'] + '.deleted-*.json'))), 1)
        self.assertEqual(self.h.state(session)['unlocked'], True)                   # typing it again opened no second session
        self.assertNotIn(PIN, json.dumps(self.h.get('/api/events?limit=200')[1]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
