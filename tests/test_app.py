"""PC program 2.0.0 (pc/app.py, application.py, httpd.py) ’s automatic test: native web page The communication protocols of API ↔ Two emulators.

1.4.1 of tests/test_app.py Moved here (SPEC G) : Each original case, as long as the behavior it describes is 2.0.0 If you still want it, use a new one.
URL, retain the original check. ”Press discharge from the screen” in 2.0.0 no longer exists (AMENDMENTS M3) ,Those cases were changed to ”scheduling measurement, total 1 times,
built-in Recipe“only discharges power measurement and built-in operators; there is really no corresponding one, so leave a line of comments to explain why it was removed. Plus 2.0.0 New case.

Everything here is a simulator:RPi Which side is test_tcpd.SimServer (local machine 127.0.0.1) ,pump control board is V7 own emulator;
will not open or list any ports of this computer (the tripwire is installed, and its record must be empty at the end), no reading or writing pc\\data.

    python tests/test_app.py
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import importlib
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'rpi' / 'tcpd'), str(ROOT / 'pc'), str(ROOT / 'tests')]

import core_testing as ct  # noqa: E402  (first: it sets PLASMIC_NO_SERIAL=1 before anything else is loaded)
import pump_testing as pt  # noqa: E402
import application  # noqa: E402
import httpd  # noqa: E402
import pinlock  # noqa: E402
import pumpboard  # noqa: E402
import rpilink  # noqa: E402
import steps  # noqa: E402
import store  # noqa: E402
import plasmic_tcpd as tcpd  # noqa: E402
from devices import Blocked, Busy  # noqa: E402
from test_tcpd import SimServer, wait_until  # noqa: E402

ENTRY = 'app'
pc = importlib.import_module(ENTRY)

PIN = '246810'
GOOD = {'Ontime': 25, 'Offtime': 5, 'Cycle': 25, 'Loopnum': 4,
        'Integration_Time': 1000, 'Spectra_Interval': 1500, 'Voltage': 300}
ALLOW = 'allow_real_' + 'ports'     # written in two pieces: no test file may hold the call itself


def setUpModule():
    ct.set_up()         # the tripwire, no network card to look around on, a schedule's "minute" lasts one second


def tearDownModule():
    ct.tear_down()      # no board left running, nothing was opened


def write_pin(folder, pin=PIN, iterations=1000):
    """pin.json in the format Set-PIN.cmd writes, with few iterations so that a test does not wait for the hash."""
    salt = hashlib.sha256(pin.encode()).digest()
    digest = hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, iterations)
    Path(folder, 'pin.json').write_text(json.dumps({'salt': salt.hex(), 'digest': digest.hex(), 'iterations': iterations}),
                                        encoding='utf-8')


def patch(case, owner, name, value):
    original = getattr(owner, name)
    setattr(owner, name, value)
    case.addCleanup(setattr, owner, name, original)


class Harness:
    """Sim TCP server + the PC application + its local web server, all on free ports.

    As in 1.4.1, plus what 2.0.0 needs before anything can be measured: a maintenance PIN (pin=None: none is
    set), the seeded operator chosen (operator=None: nobody), and with pump='sim' the pump board simulator."""

    def __init__(self, token='', speed=40, watchdog=5, discovery=False, before=None, auto_sync=True, pump=None,
                 pin=PIN, operator=ct.OPERATOR, prepare=None, rpi=True, **dotted):
        self.sim = SimServer(speed, discovery=discovery, token=token, watchdog_s=watchdog)
        self.sim.controller.audit = lambda *args, **kwargs: None    # keeps the test output readable
        self.sim_closed = False
        if before:
            before(self.sim)  # put the pretend equipment into some state before the screen connects
        self.token = token
        self.temp = tempfile.TemporaryDirectory(prefix='plasmic-app2-')
        self.folder = Path(self.temp.name)
        (self.folder / 'config.json').write_text(json.dumps(
            {'rpi_host': '127.0.0.1', 'rpi_port': self.sim.port if rpi else ct.free_port(), 'token': token}), encoding='utf-8')
        if pin:
            write_pin(self.folder, pin)
        if prepare:
            prepare(self.folder)  # what an earlier run of the program left in the data folder
        self.app = self.http = None
        self.session = ''
        self.closed = False
        try:
            values = dict(ct.PUMP)
            values.update({name.replace('__', '.'): value for name, value in dotted.items()})
            self.app = pc.Application(self.temp.name, auto_sync=auto_sync, pump_mode=pump,
                                      pump_overrides=pt.overrides_from(values) if pump == 'sim' else None)
            self.csrf = 'test-csrf-token'
            self.http = pc.ExclusiveHTTPServer(('127.0.0.1', 0), pc.make_handler(self.app, self.csrf))
            self.port = self.http.server_address[1]
            threading.Thread(target=self.http.serve_forever, daemon=True).start()
            if pump == 'sim':
                ct.wait_for(lambda: self.app.pump.connected, 8, 'the simulated pump board to connect')
                ct.wait_for(lambda: self.app.pump.view()['scale']['online'], 5, 'the first reading of the scale')
            if operator:
                self.app.set_operator({'name': operator})
        except BaseException:
            self.close()
            raise

    def settled(self):
        """The pretend Nano was just switched on: wait until the program has loaded its parameters by itself."""
        wait_until(lambda: self.app.rpi.params_view()['nano_synced'] is True)

    # -- HTTP -------------------------------------------------------------------------------------------
    def get(self, path, raw=False, unlock=None):
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}',
                                         headers={'X-Unlock': self.session if unlock is None else unlock})
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
            return (body, dict(response.headers)) if raw else json.loads(body)

    def get_code(self, path, unlock=None):
        """(status, reply) of a GET that may be refused."""
        try:
            body, _headers = self.get(path, raw=True, unlock=unlock)
            return 200, body
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def post(self, path, body, token=None, unlock=None, origin=None, data=None):
        headers = {'Content-Type': 'application/json', 'X-Token': self.csrf if token is None else token,
                   'X-Unlock': self.session if unlock is None else unlock}
        if origin:
            headers['Origin'] = origin
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}',
                                         json.dumps(body).encode() if data is None else data, headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def unlock(self, pin=PIN):
        code, reply = self.post('/api/unlock', {'pin': pin})
        assert code == 200, reply
        self.session = reply['session']
        return self.session

    # -- what the tests look at --------------------------------------------------------------------------
    def state(self):
        return self.get('/api/state')

    def connected(self):
        return wait_until(lambda: self.get('/api/state')['rpi']['status'] and self.get('/api/state'))

    @property
    def seed(self):
        """The id of “Discharge measurement only”, which every new data folder holds."""
        return next(row['id'] for row in self.get('/api/recipes')['recipes'] if row['name'] == ct.SEED_NAME)

    def body(self, count=1, interval=2, recipe_id=None, confirm=True, **more):
        body = {'recipe_id': recipe_id or self.seed,
                'schedule': {'start': {'mode': 'now'}, 'interval_minutes': interval, 'end': {'mode': 'count', 'count': count}}}
        if confirm:
            body['confirm'] = {'high_voltage': True, 'liquid': True, 'hv_off_checked': False}
        body.update(more)
        return body

    def start(self, count=1, interval=2, **options):
        """One measurement = a schedule that runs once (there is no other way to discharge)."""
        return self.post('/api/schedule/start', self.body(count, interval, **options))

    def view(self):
        return self.app.runner.view()  # not through /api/state: that would count as a screen watching

    def finished(self, seconds=40):
        return wait_until(lambda: (lambda v: v if v['status'] not in pc.schedule.ACTIVE else None)(self.view()), seconds)

    def texts(self):
        return [row['text'] for row in self.app.store.events(limit=600)][::-1]

    def events(self):
        return ' '.join(self.texts())

    def alarm(self, key):
        return next((row for row in self.app.alarms.view()['active'] if row['key'].startswith(key)), None)

    def sim_request(self, cmd, **fields):
        """Something done to the pretend RPi from outside this program (there is no TCP terminal any more)."""
        with pc.plasmic_client.Client('127.0.0.1', self.sim.port, self.token) as client:
            return client.request(cmd, **fields)

    def supply_off(self):
        return self.sim_closed or self.sim.backend.run_stop == 0

    def close_sim(self):
        if not self.sim_closed:
            self.sim_closed = True
            self.sim.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
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
                    self.close_sim()
                finally:
                    self.temp.cleanup()


class Base(unittest.TestCase):
    options = {}

    def setUp(self):
        self.h = Harness(**self.options)
        self.addCleanup(self.h.close)
        self.addCleanup(self.off, self.h)      # runs before the harness is closed

    def off(self, harness):
        """At the end of every test the pretend supply is off and no pump of the simulator turns."""
        wait_until(harness.supply_off, 10)
        board = harness.app.pump
        if board.mode == 'sim' and board._conn is not None:
            sim = board._conn.raw._sim
            wait_until(lambda: not sim.cleaning and all(state == 'idle' for state in sim.mstate.values())
                       and not any(sim.valves.values()), 10)

    def harness(self, **options):
        harness = Harness(**options)
        self.addCleanup(harness.close)
        return harness


# =====================================================================================================
# Ported from 1.4.1
# =====================================================================================================
class ScreenFlowTests(Base):
    def test_state_static_files_and_guide(self):
        state = self.h.connected()
        self.assertTrue(state['rpi']['connected'])
        self.assertEqual(state['rpi']['server']['sim'], True)
        self.assertEqual(state['rpi']['status']['snapshot']['nano']['state'], 'IDLE')
        self.assertTrue(state['pin_set'])
        # “illustrate” opens the short guide; its links to the complete one lead to the second address.
        for path, kind in (('/', 'text/html'), ('/js/main.js', 'text/javascript'), ('/css/base.css', 'text/css'),
                           ('/guide', 'text/html'), ('/' + urllib.parse.quote('docs/detailed-guide.html'), 'text/html')):
            body, headers = self.h.get(path, raw=True)
            self.assertIn(kind, headers['Content-Type'])
            self.assertGreater(len(body), 1000)
            self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        with self.assertRaises(urllib.error.HTTPError):
            self.h.get('/../app.py', raw=True)
        with self.assertRaises(urllib.error.HTTPError):
            self.h.get('/api/runs/..%2Fconfig', raw=True)

    def test_posts_need_the_page_token_and_local_origin(self):
        self.h.connected()
        self.assertEqual(self.h.post('/api/alarms/ack', {'all': True}, token='wrong')[0], 403)
        self.assertEqual(self.h.post('/api/alarms/ack', {'all': True}, origin='http://evil.example')[0], 403)
        self.assertEqual(self.h.post('/api/alarms/ack', {'all': True},
                                     origin=f'http://127.0.0.1:{self.h.port}')[0], 200)
        self.assertEqual(self.h.post('/api/launch', {})[0], 404)  # 1.4.1: an unknown command (400); now an unknown route

    def test_parameters_capture_and_plasma_are_saved_as_runs(self):
        self.h.connected()
        self.h.unlock()
        code, reply = self.h.post('/api/parameters', {'parameters': GOOD})
        self.assertEqual((code, reply['saved'], reply['synced']), (200, True, True))
        code, reply = self.h.post('/api/parameters', {'parameters': dict(GOOD, Voltage=900)})
        self.assertEqual(code, 409)
        self.assertIn('1–700', reply['error'])
        wait_until(lambda: (self.h.state()['rpi']['parameters']['values'] or {}).get('Voltage') == 300)
        code, reply = self.h.start(confirm=False)  # no confirmation
        self.assertEqual((code, reply['error']), (400, 'You must first check each item in the confirmation window'))
        code, started = self.h.start()
        self.assertEqual((code, started['schedule']['voltage_v']), (200, 300))
        job = self.h.finished()['slots'][0]['job']
        latest = wait_until(lambda: self.h.get('/api/state')['latest_run'])
        self.assertEqual((latest['job'], latest['kind'], latest['voltage_v'], latest['signal_frames']),
                         (job, 'plasma', 300, 4))
        self.assertAlmostEqual(latest['peak_nm'], 337.13, delta=.3)
        run = self.h.get('/api/runs/' + job)
        self.assertEqual((len(run['axis']), len(run['frames'])), (1880, 4))
        body, headers = self.h.get(f'/api/runs/{job}.csv', raw=True)
        lines = body.decode('utf-8-sig').splitlines()
        self.assertEqual((lines[0], len(lines)), ('wavelength_nm,background_mean,signal_mean,difference', 1881))
        self.assertIn('attachment', headers['Content-Disposition'])
        code, capture = self.h.post('/api/rpi/tool', {'cmd': 'spectrum'})
        self.assertEqual(code, 200)
        wait_until(lambda: self.h.get('/api/state')['latest_run']['job'] == capture['job'])
        self.assertEqual([row['kind'] for row in self.h.get('/api/runs')['runs']], ['capture', 'plasma'])
        texts = self.h.events()
        # 1.4.1 said “Start plasma:300 V” and “discharge completed”; a measurement is now a round of a schedule.
        for expected in ('Stored plasma parameters:ON ', 'Synced Nano parameter', 'Scheduled measurement starts: discharge measurement only', '300 V (High voltage confirmed)',
                         'process completed: only discharge measurement', 'Spectrum acquisition completed'):
            self.assertIn(expected, texts)

    def test_there_is_one_way_to_measure_and_its_timing_is_kept(self):
        """The RPi's settings say how a measurement is made (hello["flow"]); nothing on this side chooses."""
        state = self.h.connected()
        self.assertEqual((state['rpi']['server']['flow'], state['rpi']['server']['flow_error']), ('unified', ''))
        self.assertNotIn('sequences', state['rpi']['server'])
        self.assertEqual(state['rpi']['status']['flow'], 'unified')
        self.assertNotIn('estimates', state['rpi']['status'])
        self.assertEqual((state['rpi']['flow']['name'], state['rpi']['flow']['ok'], state['rpi']['flow']['background_frames']),
                         ('unified', True, 1))
        self.assertRegex(state['rpi']['flow']['text'], r'^Turn on the high voltage first and shoot the background 1 sheets, click to make approx. Stop automatically after [\d.]+ seconds discharge$')
        sent = []
        client = self.h.app.rpi.client
        original = client.request
        client.request = lambda cmd, **fields: sent.append((cmd, fields)) or original(cmd, **fields)
        code, started = self.h.start()
        self.assertEqual(code, 200)
        done = self.h.finished()
        self.assertEqual((done['status'], done['counts']), ('completed', {'done': 1}))
        self.assertIn(('plasma_run', {'confirm_high_voltage': True}), sent)   # nothing else is sent with it
        job = done['slots'][0]['job']
        latest = wait_until(lambda: self.h.get('/api/state')['latest_run'])
        self.assertEqual((latest['job'], latest['flow'], latest['background_frames']), (job, 'unified', 1))
        self.assertGreater(latest['wait_s'], 0)
        run = self.h.get('/api/runs/' + job)
        self.assertEqual((run['timing']['flow'], len(run['timing']['frames'])), ('unified', 8))
        self.assertTrue(5.0 <= run['timing']['lead_ms'][0] <= 6.5)
        # 1.4.1 wrote “starts discharging after...seconds after pressing” into the event log; the wait is now kept with the round.
        record = self.h.get('/api/rounds/' + done['slots'][0]['round_id'])
        self.assertEqual(record['plasma']['jobs'][0]['wait_s'], latest['wait_s'])
        self.assertNotIn('order', self.h.events())

    def test_an_rpi_with_the_program_of_another_generation_is_not_talked_to(self):
        """Protocol 1 (1.3.0 and older) let this side choose an order; protocol 2 has none to choose.
        Neither side guesses what the other means: the connection is refused, with the reason."""
        self.h.connected()
        self.addCleanup(setattr, tcpd, 'PROTOCOL', tcpd.PROTOCOL)
        tcpd.PROTOCOL = 1                                  # what a 1.3.0 server says about itself
        self.h.app.rpi.reconnect()
        state = wait_until(lambda: (lambda view: view if 'protocol version' in view['rpi']['error'] else None)(self.h.get('/api/state')))
        self.assertFalse(state['rpi']['connected'])
        self.assertIn('server protocol version 1 with this program 2 is different, please update both ends.', state['rpi']['error'])
        code, reply = self.h.start()
        self.assertEqual((code, reply['code'], reply['error']), (409, 'blocked', 'Cannot start scheduling now'))
        self.assertEqual([row['code'] for row in reply['reasons']], ['rpi_link'])
        self.assertIsNone(self.h.sim.controller.job)

    def test_a_measuring_program_that_is_missing_on_the_rpi_is_said_so(self):
        """The server then refuses every discharge with the reason and the way out; it is shown as a
        reason why the discharge is not ready, like every other one."""
        self.h.connected()
        controller = self.h.sim.controller
        controller.flow_error = 'RPi There is something attached to plasma_measure.py (FileNotFoundError) .Please re-execute on the computer Install.cmd Bundle RPi end installed'
        self.h.app.rpi.command('status', fresh=True)       # 1.4.1: the “reading status” button; the program now asks by itself
        ready = wait_until(lambda: (lambda r: r if not r['plasma'] else None)(self.h.get('/api/state')['rpi']['status']['ready']))
        self.assertIn(controller.flow_error, [row['reason'] for row in ready['problems']])
        code, reply = self.h.start()
        self.assertEqual((code, reply['code']), (409, 'blocked'))
        self.assertIn('plasma_measure.py', ' '.join(row['text'] for row in reply['reasons']))
        self.assertIsNone(controller.job)

    def test_connection_diagnosis_lists_every_step(self):
        self.h.connected()
        self.h.unlock()
        code, reply = self.h.post('/api/rpi/diagnose', {})
        self.assertEqual(code, 200)
        self.assertEqual([step['key'] for step in reply['steps']], ['ip', 'ping', 'port', 'hello', 'token', 'device'])
        self.assertEqual({step['ok'] for step in reply['steps']}, {True})
        self.assertEqual(self.h.post('/api/rpi/diagnose', {}, token='wrong')[0], 403)
        self.assertIsNone(self.h.sim.controller.job)  # diagnosing never starts anything

    # test_terminal_forwards_lines_but_not_what_needs_a_dialog: dropped — 2.0.0 has no TCP terminal
    # (/api/raw answers 404, see RemovedTests).


class SlowScreenFlowTests(Base):
    options = {'speed': 2}

    def test_stop_cancels_a_running_discharge(self):
        self.h.connected()
        self.assertEqual(self.h.start()[0], 200)
        wait_until(lambda: self.h.get('/api/state') and self.h.sim.backend.run_stop == 1)
        code, reply = self.h.post('/api/stop', {})
        self.assertEqual((code, reply['rpi']['result']), (200, 'cancel_sent'))          # 1.4.1: reply['cancel_sent']
        self.assertEqual((reply['accepted'], reply['schedule_ended'], reply['round_stopping'], reply['manual_cancelled']),
                         (True, True, True, False))
        self.assertEqual(reply['pump'], {'result': 'none', 'error': ''})                # no pump board in this harness
        job = wait_until(lambda: (lambda j: j if j['state'] != 'running' else None)(
            self.h.get('/api/state')['rpi']['status']['job']))
        self.assertEqual((job['state'], job['cancel_reason'], job['post_stop']['ok']), ('cancelled', 'stop', True))
        self.assertEqual(self.h.sim.backend.run_stop, 0)
        ended = self.h.finished()
        self.assertEqual((ended['status'], ended['counts']), ('stopped', {'stopped': 1}))
        # 1.4.1 wrote “Discharge not completed: Stop pressed”; the round and the button say it now.
        wait_until(lambda: any('The process has been stopped: only discharge measurement' in text for text in self.h.texts()))
        self.assertIn('Stop has been pressed: the pump is not in use; high voltage has been sent to stop the discharge work, etc. RPi read back', self.h.texts())
        self.assertIsNone(self.h.get('/api/state')['latest_run'])
        self.assertEqual(self.h.get('/api/state')['round']['status'], 'stopped')


class ScreenWatchdogTests(Base):
    options = {'speed': 2}

    # test_discharge_stops_when_the_screen_goes_away: dropped — no discharge is tied to the page any more
    # (AMENDMENTS M3: the maintenance discharge is gone; the link's watchdog keeps its test in test_rpilink.py).
    # What is still tied to the page is a pump started by hand: ManualPumpTests.test_a_manual_pump_stops_when_the_screen_goes_away.

    def test_discharge_stops_when_the_program_dies(self):
        self.h.connected()
        patch(self, steps, 'JOB_LOST_SECONDS', 1.0)        # how long the round waits for a link that is gone
        self.assertEqual(self.h.start()[0], 200)
        wait_until(lambda: self.h.get('/api/state') and self.h.sim.backend.run_stop == 1)
        self.h.app.rpi.close()  # what the RPi sees when the black window is closed
        job = wait_until(lambda: self.h.sim.controller.job.state != 'running' and self.h.sim.controller.job)
        self.assertEqual((job.state, job.cancel_reason), ('cancelled', 'owner_disconnected'))
        self.assertEqual(self.h.sim.backend.run_stop, 0)
        ended = self.h.finished()                          # and on this side the round does not end well
        self.assertEqual(ended['status'], 'failed')
        self.assertIn('and RPi ’s connection is interrupted and the result of this measurement cannot be confirmed. The connection of', ended['error'])


class ScheduleRuleTests(unittest.TestCase):
    """The time arithmetic of Schedule measurement (pc/schedule.py); no equipment involved."""

    def setUp(self):
        patch(self, pc.schedule, 'MINUTE', 60)             # the real minute: these are the numbers of 1.4.1

    def test_saved_parameters_are_one_plain_line_in_the_event_log(self):
        text = application.parameters_text({'Ontime': 25, 'Offtime': 5.5, 'Cycle': 25, 'Loopnum': 8,
                                            'Integration_Time': 1000, 'Spectra_Interval': 1500, 'Voltage': 300})
        self.assertEqual(text, 'ON 25 ms, OFF 5.5 ms, every round 25 pulses,8 wheel, points 1000 ms, interval 1500 ms, 300 V')
        self.assertEqual(application.parameters_text(None), '—')
        self.assertTrue(application.parameters_text({'Ontime': 25}).endswith('— V'))

    def plan(self, now, **change):
        raw = {'start': {'mode': 'now'}, 'interval_minutes': 10, 'end': {'mode': 'count', 'count': 6}}
        raw.update(change)
        return pc.schedule.validate(raw, now)

    def test_slots_lie_on_a_fixed_grid(self):
        now = pc.datetime(2026, 10, 5, 21, 50, 30, 700000).astimezone()
        spec = self.plan(now)
        self.assertEqual((spec['first_slot'][11:19], spec['interval_seconds'], pc.schedule.planned_total(spec)),
                         ('21:50:30', 600, 6))
        self.assertEqual(pc.schedule.slot_time(spec, 5).isoformat()[11:19], '22:40:30')
        later = self.plan(now, start={'mode': 'at', 'time': '08:00'}, end={'mode': 'until', 'time': '09:00'})
        self.assertEqual((later['first_slot'][:16], later['end']['until'][:16]), ('2026-10-06T08:00', '2026-10-06T09:00'))
        self.assertEqual(pc.schedule.planned_total(later), 7)  # 08:00, 08:10 ... 09:00
        self.assertIsNone(pc.schedule.planned_total(self.plan(now, end={'mode': 'continuous'})))
        with self.assertRaises(ValueError) as caught:
            self.plan(now, interval_minutes=1440, end={'mode': 'count', 'count': 40})
        self.assertIn('31 sky', str(caught.exception))

    def test_a_slot_that_passed_during_a_run_is_reported_not_queued(self):
        now = pc.datetime(2026, 10, 5, 8, 0, 0).astimezone()
        spec = self.plan(now, interval_minutes=1)
        minutes = lambda value: now + pc.schedule.timedelta(seconds=value * 60)  # noqa: E731
        self.assertEqual(pc.schedule.next_due_index(spec, 0, minutes(.5)), (1, []))     # finished in time
        self.assertEqual(pc.schedule.next_due_index(spec, 0, minutes(1.05)), (1, []))   # a few seconds late: still run
        self.assertEqual(pc.schedule.next_due_index(spec, 0, minutes(3.5)), (4, [1, 2, 3]))


class ScheduleBase(Base):
    """Schedule measurement with a "minute" that lasts one second, so that a whole schedule takes seconds."""

    def setUp(self):
        super().setUp()
        self.assertEqual(pc.schedule.MINUTE, 1)            # set for the whole module (core_testing.set_up)
        self.h.connected()

    def start(self, count=2, interval=2, confirm=True, **more):
        return self.h.start(count, interval, confirm=confirm, **more)

    def view(self):
        return self.h.view()

    def finished(self, seconds=40):
        return self.h.finished(seconds)

    def events(self):
        return self.h.events()


class ScheduleTests(ScheduleBase):
    def test_every_slot_runs_by_itself_and_its_spectra_are_saved(self):
        self.assertEqual(self.start(confirm=False)[0], 400)  # high voltage is confirmed once, at the start
        self.assertIsNone(self.h.sim.controller.job)
        code, reply = self.start(count=2, interval=2)
        self.assertEqual((code, reply['accepted'], reply['schedule']['planned_total']), (200, True, 2))
        code, reply = self.start()
        self.assertEqual((code, reply['code'], reply['error']), (409, 'state', 'Already has a schedule in progress'))  # one schedule at a time
        done = self.finished()
        self.assertEqual((done['status'], done['error'], done['counts']), ('completed', '', {'done': 2}))
        self.assertEqual([(slot['index'], slot['status']) for slot in done['slots']], [(0, 'done'), (1, 'done')])
        planned = [pc.datetime.fromisoformat(slot['planned_at']) for slot in done['slots']]
        self.assertEqual((planned[1] - planned[0]).total_seconds(), 2)  # on the time grid...
        self.assertGreaterEqual(pc.datetime.fromisoformat(done['slots'][1]['started_at']), planned[1])  # ...not back to back
        runs = wait_until(lambda: (lambda rows: rows if len(rows) == 2 else None)(self.h.get('/api/runs')['runs']))
        self.assertEqual({row['job'] for row in runs}, {slot['job'] for slot in done['slots']})
        self.assertEqual({row['kind'] for row in runs}, {'plasma'})
        self.assertEqual(self.h.sim.backend.run_stop, 0)
        self.assertEqual(self.h.get('/api/state')['schedule']['status'], 'completed')
        for expected in ('Scheduled measurement starts: only discharge measurement, every 2 minutes', 'common 2 Second-rate', 'High voltage confirmed', 'Scheduled measurement end (completed): Completed 2 Second-rate'):
            self.assertIn(expected, self.events())

    def test_a_schedule_measures_the_one_way_there_is(self):
        code, reply = self.h.post('/api/schedule/preview', self.h.body(2, 600, confirm=False))
        self.assertEqual(code, 200)
        self.assertNotIn('sequence', reply)
        self.assertEqual(reply['estimate_seconds'], self.h.app.rpi.latest()['estimate_s'])
        self.assertEqual((reply['flow']['name'], reply['flow']['background_frames']), ('unified', 1))
        sent = []
        client = self.h.app.rpi.client
        original = client.request
        client.request = lambda cmd, **fields: sent.append((cmd, fields)) or original(cmd, **fields)
        code, reply = self.start(count=2, interval=2)
        self.assertEqual(code, 200)
        self.assertNotIn('sequence', reply['schedule'])
        done = self.finished()
        self.assertEqual((done['status'], done['counts']), ('completed', {'done': 2}))
        self.assertEqual([fields for cmd, fields in sent if cmd == 'plasma_run'], [{'confirm_high_voltage': True}] * 2)
        runs = wait_until(lambda: (lambda rows: rows if len(rows) == 2 else None)(self.h.get('/api/runs')['runs']))
        self.assertEqual({(row['flow'], row['background_frames']) for row in runs}, {('unified', 1)})
        self.assertNotIn('order', self.events())

    def test_the_form_is_checked_before_anything_starts(self):
        def preview(**change):
            plan = {'start': {'mode': 'now'}, 'interval_minutes': 600, 'end': {'mode': 'count', 'count': 6}}
            plan.update(change)
            return self.h.post('/api/schedule/preview', {'recipe_id': self.h.seed, 'schedule': plan})
        code, plan = preview()
        self.assertEqual((code, plan['planned_total'], plan['spec']['interval_seconds'], plan['voltage_v']),
                         (200, 6, 600, 100))
        span = pc.datetime.fromisoformat(plan['last_slot_at']) - pc.datetime.fromisoformat(plan['spec']['first_slot'])
        self.assertEqual(span.total_seconds(), 5 * 600)
        self.assertGreater(plan['estimate_seconds'], 1)
        self.assertEqual(plan['warnings'], [])
        self.assertEqual((plan['blockers'], [plan['needs'][key] for key in ('high_voltage', 'liquid', 'hv_off_checked')]),
                         ([], [True, False, False]))
        self.assertEqual((plan['recipe']['name'], plan['recipe']['uses_plasma'], plan['blank']), ('Only discharge measurement', True, None))
        self.assertIn('is longer than the interval', preview(interval_minutes=1)[1]['warnings'][0])  # one run takes longer than that
        self.assertIsNone(preview(end={'mode': 'continuous'})[1]['planned_total'])
        self.assertEqual(preview(start={'mode': 'at', 'time': '08:30'})[1]['spec']['first_slot'][11:16], '08:30')
        self.assertEqual(preview(end={'mode': 'until', 'time': '17:00'})[1]['spec']['end']['until'][11:16], '17:00')
        for change, text in (({'interval_minutes': 0}, '1–1440'), ({'interval_minutes': 1.5}, '1–1440'),
                             ({'interval_minutes': 1441}, '1–1440'), ({'end': {'mode': 'count', 'count': 0}}, '1–1000'),
                             ({'end': {'mode': 'count', 'count': 1001}}, '1–1000'), ({'end': {}}, 'end condition'),
                             ({'start': {'mode': 'at', 'time': '25:00'}}, 'HH:MM'), ({'start': {}}, 'Start method')):
            code, reply = preview(**change)
            self.assertEqual((code, reply['code']), (400, 'bad_request'), change)
            self.assertIn(text, reply['error'])
        self.assertEqual(self.h.post('/api/schedule/preview', {'recipe_id': self.h.seed, 'schedule': None})[0], 400)
        code, reply = self.h.post('/api/schedule/preview', {'recipe_id': '0' * 32, 'schedule': {}})
        self.assertEqual((code, reply), (404, {'error': 'Recipe does not exist', 'code': 'not_found'}))
        # 1.4.1: “Stop schedule” with nothing to stop was refused. STOP is never refused now; PAUSE says there is nothing.
        code, reply = self.h.post('/api/pause', {})
        self.assertEqual((code, reply), (409, {'error': 'There is currently no schedule that can be paused', 'code': 'state'}))
        code, reply = self.h.post('/api/resume', {})
        self.assertEqual((code, reply), (409, {'error': 'Schedule is not in pause state', 'code': 'state'}))
        # Equipment that is not ready: the schedule does not start at all.
        self.h.sim_request('sim_fault', fault='HEARTBEAT_LOST')
        wait_until(lambda: not self.h.get('/api/state')['rpi']['status']['ready']['plasma'])
        code, reply = self.start()
        self.assertEqual((code, reply['code'], reply['error']), (409, 'blocked', 'Cannot start scheduling now'))
        self.assertTrue(reply['reasons'][0]['text'].startswith('Failed to pass the pre-discharge inspection'))
        self.assertEqual(reply['reasons'][0]['code'], 'rpi_not_ready')
        blockers = self.h.post('/api/schedule/preview', self.h.body(confirm=False))[1]['blockers']
        self.assertEqual([row['code'] for row in blockers], ['rpi_not_ready'] * len(blockers))
        self.assertTrue(blockers)
        self.assertIsNone(self.view())
        self.assertIsNone(self.h.sim.controller.job)

    def test_manual_actions_wait_until_the_schedule_is_over(self):
        self.h.unlock()
        self.assertEqual(self.start(count=3, interval=4)[0], 200)
        wait_until(lambda: self.view()['counts'].get('done') == 1 and self.view()['status'] == 'waiting')
        for path, body in (('/api/rpi/tool', {'cmd': 'spectrum'}), ('/api/rpi/tool', {'cmd': 'prepare'}),
                           ('/api/rpi/tool', {'cmd': 'clear_fault'}), ('/api/parameters', {'parameters': GOOD}),
                           ('/api/manual', {'op': 'clean'})):
            code, reply = self.h.post(path, body)
            self.assertEqual((code, reply['code']), (409, 'busy'), body)
            self.assertIn('Scheduled measurement in progress', reply['error'])
        # 1.4.1 also tried a discharge by hand here: there is none any more, whatever the schedule does.
        code, reply = self.h.post('/api/rpi/tool', {'cmd': 'plasma_run', 'confirm_high_voltage': True})
        self.assertEqual((code, reply), (400, {'error': 'Unsupported command', 'code': 'bad_request'}))
        self.assertEqual(self.h.post('/api/raw', {'line': '{"cmd":"spectrum"}'})[0], 404)   # the TCP terminal is gone
        self.assertEqual(self.h.get('/api/state')['schedule']['status'], 'waiting')  # looking is always allowed
        code, reply = self.h.post('/api/stop', {})      # 1.4.1: “Stop schedule”
        self.assertEqual((code, reply['schedule_ended'], reply['round_stopping'], reply['rpi']['result']),
                         (200, True, False, 'confirmed'))  # nothing was running: the stop is sent all the same, and read back
        ended = self.finished()
        self.assertEqual((ended['status'], ended['counts'], len(ended['slots'])), ('stopped', {'done': 1}, 1))
        self.assertEqual(self.h.post('/api/rpi/tool', {'cmd': 'spectrum'})[0], 200)  # and now by hand again
        wait_until(lambda: self.h.get('/api/state')['owner'] is None)

    def test_three_slots_in_a_row_that_cannot_start_end_the_schedule(self):
        self.assertEqual(self.start(count=6, interval=4)[0], 200)  # long enough to act between two slots
        wait_until(lambda: self.view()['counts'].get('done') == 1 and self.view()['status'] == 'waiting')
        self.assertEqual(self.h.sim_request('sim_fault', fault='HEARTBEAT_LOST')['ok'], True)
        ended = self.finished()
        self.assertEqual((ended['status'], ended['counts']), ('failed', {'done': 1, 'not_started': 3}))
        self.assertIn('Continuous 3 times', ended['error'])
        self.assertIn('HEARTBEAT_LOST', ended['slots'][-1]['note'])
        wait_until(lambda: 'Scheduled measurement ends (stopped due to failure)' in self.events())  # written right after the status
        self.assertNotIn('The schedule record was not archived successfully.', self.events())
        self.assertIsNotNone(self.h.alarm('sched_failed:'))


class RestartTests(unittest.TestCase):
    def test_a_schedule_is_never_resumed_after_a_restart(self):
        def left_behind(folder):  # the schedule.json of a program that was closed in the middle of a run (1.4.1's shape)
            pc.write_json(folder / 'schedule.json', {
                'id': 'a' * 32, 'status': 'running', 'created_at': pc.now_iso(), 'finished_at': None, 'counts': {'done': 3},
                'spec': {'first_slot': '2026-10-05T08:00:00+08:00', 'interval_seconds': 600, 'interval_minutes': 10,
                         'start': {'mode': 'now', 'time': None}, 'end': {'mode': 'continuous'}},
                'planned_total': None, 'last_slot_at': None, 'estimate_seconds': 40, 'voltage_v': 300, 'next_index': 4,
                'next_slot_at': '2026-10-05T08:40:00+08:00', 'error': '',
                'slots': [{'index': 3, 'planned_at': '2026-10-05T08:30:00+08:00', 'status': 'running', 'job': 'HMI-' + 'b' * 32}]})
        h = Harness(prepare=left_behind)   # what the program does when it starts
        self.addCleanup(h.close)
        h.connected()
        self.assertFalse(h.app.runner.active)
        shown = h.get('/api/state')['schedule']
        self.assertEqual((shown['status'], shown['counts'], shown['slots'][0]['status'], shown['next_slot_at']),
                         ('interrupted', {'done': 3, 'failed': 1}, 'failed', None))
        self.assertIn('will not automatically recover', shown['error'])
        self.assertIn('will not automatically recover', h.events())
        self.assertEqual(h.alarm('sched_interrupted:')['key'], 'sched_interrupted:' + 'a' * 32)
        time.sleep(1.5)
        self.assertIsNone(h.sim.controller.job)  # and nothing was started
        self.assertEqual(h.start()[0], 200)      # a new schedule can be started by hand
        self.assertEqual(h.finished()['status'], 'completed')


class SlowScheduleTests(ScheduleBase):
    options = {'speed': 2}

    def high_voltage_on(self):
        wait_until(lambda: self.h.sim.backend.run_stop == 1 and self.view()['status'] == 'running')

    def test_stop_ends_the_schedule_and_the_running_discharge(self):
        self.assertEqual(self.start(count=5, interval=1)[0], 200)
        self.high_voltage_on()
        code, reply = self.h.post('/api/stop', {})  # the big button on the screen
        self.assertEqual((code, reply['rpi']['result'], reply['schedule_ended']), (200, 'cancel_sent', True))
        ended = self.finished()
        self.assertEqual((ended['status'], ended['counts'], len(ended['slots'])), ('stopped', {'stopped': 1}, 1))
        self.assertEqual(ended['slots'][0]['status'], 'stopped')
        self.assertEqual(self.h.sim.backend.run_stop, 0)
        self.assertIn('Scheduled measurement ends (stopped)', self.events())    # 1.4.1: “Scheduled measurement also ended.” in the line of the button

    # test_stop_schedule_also_stops_the_running_discharge: dropped — “Stop schedule” is not a button of its own any more;
    # the one STOP is the test above.

    def test_a_run_that_does_not_end_cleanly_ends_the_schedule(self):
        self.assertEqual(self.start(count=5, interval=1)[0], 200)
        self.high_voltage_on()
        pc.plasmic_client.stop('127.0.0.1', self.h.sim.port)  # somebody else stops it, not this program
        ended = self.finished()
        # 1.4.1 counted that slot as "stopped"; a round that was stopped from elsewhere has failed.
        self.assertEqual((ended['status'], ended['counts']), ('failed', {'failed': 1}))
        self.assertIn('No. 1 measurements did not end normally', ended['error'])
        self.assertEqual(self.h.sim.backend.run_stop, 0)

    def test_slots_that_come_due_during_a_run_are_skipped_not_queued(self):
        self.assertEqual(self.start(count=4, interval=1)[0], 200)  # one run takes several of these intervals
        ended = self.finished(120)
        self.assertEqual(ended['status'], 'completed')
        statuses = [slot['status'] for slot in ended['slots']]
        self.assertEqual(([slot['index'] for slot in ended['slots']], statuses[0]), ([0, 1, 2, 3], 'done'))
        self.assertEqual(statuses[1], 'skipped_overrun')
        self.assertLessEqual(set(statuses), {'done', 'skipped_overrun'})
        self.assertEqual(sum(ended['counts'].values()), 4)
        self.assertIn('There is no measurement during this period', ended['slots'][1]['note'])
        self.assertIn('Schedule No. 2 times skipped: The last measurement timed out', self.events())

    def test_a_scheduled_run_goes_on_when_the_screen_goes_away(self):
        patch(self, rpilink, 'UI_WATCHDOG_SECONDS', 1.0)
        patch(self, pumpboard, 'UI_WATCHDOG_SECONDS', 1.0)
        self.assertEqual(self.start(count=1, interval=1)[0], 200)
        ended = self.finished(90)  # nobody asks /api/state all this time
        self.assertEqual((ended['status'], ended['counts']), ('completed', {'done': 1}))
        self.assertNotIn('The operation screen exceeds', self.events())


class TokenAndPinTests(Base):
    options = {'token': 'rpi-token-123'}

    def test_token_from_settings_is_used_and_never_shown(self):
        state = self.h.connected()
        self.assertTrue(state['rpi']['connected'])
        boot = self.h.get('/api/bootstrap')
        self.assertEqual(boot['config'], {'rpi_host': '127.0.0.1', 'rpi_port': self.h.sim.port, 'token_set': True})
        self.assertNotIn('rpi-token-123', json.dumps(boot) + json.dumps(state))
        # ... nor in any other reply, nor in the event log.
        self.h.unlock()
        seen = [json.dumps(self.h.get(path)) for path in ('/api/events?limit=200', '/api/recipes', '/api/runlog', '/api/runs',
                                                           '/api/pump/ports', '/api/calibration', '/api/measurements')]
        seen += [json.dumps(self.h.post(path, {})[1]) for path in ('/api/rpi/diagnose', '/api/rpi/find', '/api/stop')]
        self.assertNotIn('rpi-token-123', ' '.join(seen))
        self.assertNotIn(PIN, ' '.join(seen) + json.dumps(self.h.state()))

    def test_wrong_token_is_explained(self):
        self.h.connected()
        self.h.unlock()
        code, _ = self.h.post('/api/rpi/config', {'rpi_host': '127.0.0.1', 'rpi_port': self.h.sim.port, 'token': 'wrong'})
        self.assertEqual(code, 200)
        link = wait_until(lambda: (lambda v: v if 'token mistake' in v['error'] else None)(self.h.get('/api/state')['rpi']))
        self.assertFalse(link['connected'])
        code, _ = self.h.post('/api/rpi/config', {'rpi_host': '127.0.0.1', 'rpi_port': self.h.sim.port, 'clear_token': True})
        self.assertEqual((code, self.h.app.store.config['token'], self.h.get('/api/bootstrap')['config']['token_set']), (200, '', False))
        wait_until(lambda: 'need token' in self.h.get('/api/state')['rpi']['error'])
        self.h.post('/api/rpi/config', {'rpi_host': '127.0.0.1', 'rpi_port': self.h.sim.port, 'token': 'rpi-token-123'})
        wait_until(lambda: self.h.get('/api/state')['rpi']['connected'])
        self.assertEqual(self.h.post('/api/rpi/config', {'rpi_host': 'bad host', 'rpi_port': 1})[0], 400)
        self.assertNotIn('wrong', self.h.events())
        self.assertNotIn('rpi-token-123', self.h.events())

    def test_check_now_reads_the_equipment_and_says_what_answered(self):
        """AMENDMENTS M18 (“Recheck”): no PIN; a reading taken now; nothing is switched, written or started."""
        self.h.connected()
        backend = self.h.sim.backend
        before = (backend.run_stop, dict(self.h.state()['rpi']['parameters']['values']))
        code, reply = self.h.post('/api/rpi/check', {})
        self.assertEqual((code, reply['ok'], reply['busy'], reply['advice']), (200, True, False, 'high voltage power supply and Nano all responded.'))
        self.assertEqual([(row['name'], row['ok']) for row in reply['rows']], [('high voltage power supply (HSPY) ', True), ('Nano controller', True)])
        self.assertTrue(reply['rows'][0]['text'].startswith('has response: output Is OFF,0 V'), reply['rows'][0])
        # The supply is switched on and does not answer (the rig, 2026-10-07): said at once, with where to look.
        backend._power = lambda: {'valid': False, 'error': 'Timed out waiting for HSPY status'}
        self.addCleanup(backend.__dict__.pop, '_power', None)
        code, reply = self.h.post('/api/rpi/check', {})
        self.assertEqual((code, reply['ok'], reply['rows'][0]), (200, False, {'name': 'high voltage power supply (HSPY) ', 'ok': False, 'text': 'No response'}))
        self.assertTrue(reply['rows'][1]['ok'])
        self.assertIn('The problem is in the high voltage power supply section', reply['advice'])
        events = [(row['text'], row['level']) for row in self.h.app.store.events(limit=10)]
        self.assertIn(('Recheck the equipment: high voltage power supply (HSPY)  No response;Nano controller responded: standby', 'warn'), events)
        # It answers again: the next press says so.
        backend.__dict__.pop('_power', None)
        self.assertTrue(self.h.post('/api/rpi/check', {})[1]['ok'])
        self.assertEqual((backend.run_stop, dict(self.h.state()['rpi']['parameters']['values'])), before)
        self.assertIsNone(self.h.sim.controller.job)               # nothing was started
        self.assertFalse(self.h.state()['unlocked'])

    def test_pin_guards_the_settings_and_not_the_plasma_parameters(self):
        self.h.connected()
        good = dict(GOOD, Voltage=120)
        # The plasma parameters are set on the schedule page (AMENDMENTS M17: the person's decision): no PIN for them.
        self.h.app.set_operator({'name': 'operator'})
        code, saved = self.h.post('/api/parameters', {'parameters': good})
        self.assertEqual((code, saved['saved'], saved['parameters']['Voltage']), (200, True, 120))
        events = {row['text']: row['actor'] for row in self.h.app.store.events(limit=50)}
        self.assertEqual(events['Synced Nano parameter'], 'operator')                    # the log says who it was
        # Everything else of the maintenance pages is locked.
        for path, body in (('/api/rpi/config', {'rpi_host': '127.0.0.1', 'rpi_port': 1}), ('/api/rpi/tool', {'cmd': 'prepare'}),
                           ('/api/operators', {'operators': ['First']})):
            code, reply = self.h.post(path, body)
            self.assertEqual((code, reply['code']), (403, 'locked'), path)
        self.assertEqual(self.h.post('/api/alarms/ack', {'all': True})[0], 200)  # operating stays open
        code, reply = self.h.post('/api/unlock', {'pin': '000000'})
        self.assertEqual((code, reply), (409, {'error': 'Maintenance PIN mistake', 'code': 'pin'}))
        code, reply = self.h.post('/api/unlock', {'pin': PIN})
        self.assertEqual((code, reply['expires_in']), (200, 600))
        code, saved = self.h.post('/api/parameters', {'parameters': dict(good, Voltage=130)}, unlock=reply['session'])
        self.assertEqual((code, saved['saved']), (200, True))
        self.assertEqual(self.h.post('/api/rpi/tool', {'cmd': 'prepare'}, unlock=reply['session'])[0], 200)
        self.assertEqual(self.h.post('/api/lock', {}, unlock=reply['session']), (200, {'locked': True}))
        self.assertEqual(self.h.post('/api/rpi/tool', {'cmd': 'prepare'}, unlock=reply['session'])[0], 403)
        for bad in ('12345', '123456789', 'abcdef', 123456):
            with self.assertRaises(ValueError):
                self.h.app.pin.set(bad)
        events = {row['text']: row['actor'] for row in self.h.app.store.events(limit=50)}
        self.assertEqual(events['Maintenance PIN Unlock'], 'Maintenance')
        actors = [row['actor'] for row in self.h.app.store.events(limit=50) if row['text'] == 'Synced Nano parameter']
        self.assertEqual(sorted(set(actors)), ['operator', 'Maintenance'])                 # the schedule page's save, and the maintenance page's


class FindRpiTests(Base):
    """“automatic search RPi” from the screen. The list of network cards is emptied first (core_testing.set_up), so
    nothing is broadcast on, or knocked at, the real network while testing."""
    options = {'token': 'rpi-token-123', 'discovery': True}

    def setUp(self):
        self.assertEqual(pc.netcheck.local_ipv4(), [])
        super().setUp()
        self.h.unlock()

    def test_find_lists_the_server_and_changes_nothing(self):
        state = self.h.connected()
        self.assertEqual((state['rpi']['found'], state['rpi']['addresses']), ([], []))
        code, reply = self.h.post('/api/rpi/find', {})
        self.assertEqual(code, 200)
        self.assertEqual((reply['host'], reply['port'], reply['addresses']), ('127.0.0.1', self.h.sim.port, []))
        self.assertEqual([(row['host'], row['port'], row['version'], row['allowed']) for row in reply['found']],
                         [('127.0.0.1', self.h.sim.port, tcpd.VERSION, True)])
        self.assertNotIn('rpi-token-123', json.dumps(reply))
        self.assertEqual(self.h.post('/api/rpi/find', {}, token='wrong')[0], 403)
        self.assertIsNone(self.h.sim.controller.job)
        self.assertEqual(self.h.get('/api/bootstrap')['config']['rpi_host'], '127.0.0.1')

    def test_switching_to_a_found_address_keeps_the_saved_token(self):
        self.h.connected()
        # What the "Use this address instead" button sends: the new address and an empty token field.
        code, _ = self.h.post('/api/rpi/config', {'rpi_host': 'localhost', 'rpi_port': self.h.sim.port, 'token': ''})
        self.assertEqual(code, 200)
        link = wait_until(lambda: (lambda v: v if v['connected'] and v['host'] == 'localhost' else None)(
            self.h.get('/api/state')['rpi']))
        self.assertEqual(link['error'], '')
        self.assertEqual(self.h.app.store.config['token'], 'rpi-token-123')


class MovedRpiTests(unittest.TestCase):
    """The RPi got another IP from the router: the program notices and offers the new address."""

    def test_an_rpi_at_another_address_is_noticed_while_disconnected(self):
        class Unreachable:  # stands in for the TCP client; never touches the network
            def __init__(self, *_args, **_options):
                pass

            def connect(self):
                raise ConnectionRefusedError()

            def close(self):
                pass
        asked = []
        moved = {'host': '192.168.0.95', 'port': 8767, 'version': '1.2.0', 'name': 'raspberrypi', 'sim': False,
                 'allowed': True}
        patch(self, rpilink, 'Client', Unreachable)        # 1.4.1: the Link lived in app.py
        patch(self, pc.netcheck, 'discover',
              lambda port, timeout=1.2, hosts=(): asked.append((port, tuple(hosts))) or [dict(moved)])
        patch(self, pc.netcheck, 'local_ipv4', lambda: ['192.168.0.130'])
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'config.json').write_text(json.dumps({'rpi_host': '192.168.0.82', 'rpi_port': 8767}),
                                                   encoding='utf-8')
            app = pc.Application(folder)
            try:
                view = wait_until(lambda: app.rpi.view()['found'] and app.rpi.view())
                self.assertEqual((view['connected'], view['found'], view['addresses']),
                                 (False, [moved], ['192.168.0.130']))
                self.assertEqual(asked[0], (8767, ('192.168.0.82',)))
                self.assertIn('192.168.0.95', ' '.join(event['text'] for event in app.store.recent_events()))
                state = app.state()
                self.assertEqual((state['rpi']['found'], state['setup']['rpi'], state['setup']['complete']), ([moved], False, False))
                time.sleep(.5)
                self.assertEqual(len(asked), 1)  # one question, then quiet for a while
            finally:
                app.close()

    def test_no_search_for_the_simulator_on_this_computer(self):
        asked = []
        patch(self, pc.netcheck, 'discover', lambda *_args, **_options: asked.append(1) or [])
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'config.json').write_text(json.dumps({'rpi_host': '127.0.0.1', 'rpi_port': 9}), encoding='utf-8')
            app = pc.Application(folder)
            try:
                wait_until(lambda: '127.0.0.1:9' in app.rpi.view()['error'])
                self.assertEqual(asked, [])
            finally:
                app.close()

    def test_first_start_uses_the_address_the_folder_was_set_up_for(self):
        patch(self, store, 'SITE', ROOT / 'installer' / 'site.json')       # the tests point it at nothing (core_testing)
        site = json.loads((ROOT / 'installer' / 'site.json').read_text(encoding='utf-8'))
        self.assertEqual(pc.site_config(), {'rpi_host': site['rpi_host']})
        with tempfile.TemporaryDirectory() as folder:
            store.SITE = Path(folder) / 'missing.json'                     # 1.4.1: SITE lived in app.py
            self.assertEqual(pc.site_config(), {})
            Path(folder, 'missing.json').write_text('{"rpi_host": "nonsense"}', encoding='utf-8')
            self.assertEqual(pc.site_config(), {})


class NewNanoTests(unittest.TestCase):
    """A Nano that was just switched on shows "--" and "POWER ?" until its parameters are loaded."""

    def harness(self, **options):
        harness = Harness(**options)
        self.addCleanup(harness.close)
        return harness

    def events(self, harness):
        return ' '.join(event['text'] for event in harness.app.store.recent_events())

    def test_parameters_are_loaded_once_without_a_click(self):
        h = self.harness()
        nano = wait_until(lambda: (lambda n: n if n.get('active_parameters') else None)(
            (h.get('/api/state')['rpi']['status'] or {'snapshot': {'nano': {}}})['snapshot']['nano']))
        self.assertEqual(nano['lcd_power_report']['power_snapshot'], 'OFF')
        self.assertIn('The parameters of the configuration file have been automatically synchronized.', self.events(h))
        self.assertEqual((h.sim.backend.run_stop, h.sim.backend.state), (0, 'IDLE'))  # nothing was output
        self.assertIsNone(h.sim.controller.job)
        self.assertIs(wait_until(lambda: h.get('/api/state')['rpi']['parameters']['nano_synced']), True)
        session = h.sim.backend.session
        time.sleep(2.5)
        self.assertEqual(h.sim.backend.session, session)  # and it is not repeated

    def test_never_while_the_nano_is_faulted_or_the_supply_is_on(self):
        for label, before in (('fault', lambda sim: sim.backend.sim_fault('HEARTBEAT_LOST')),
                              ('supply on', lambda sim: setattr(sim.backend, 'run_stop', 1)),
                              ('voltage present', lambda sim: setattr(sim.backend, 'voltage', 12.0))):
            h = self.harness(before=before)
            h.connected()
            time.sleep(2.0)
            self.assertEqual(h.sim.backend.session, 0, label)
            self.assertNotIn('automatic', self.events(h), label)
            self.assertIs(h.get('/api/state')['rpi']['parameters']['nano_synced'], False, label)
            if label == 'fault':
                self.assertEqual(h.alarm('nano_fault')['text'], 'Nano Fault: Heartbeat interrupted. Ask maintenance personnel to go to ”Repair › Plasma Parameters” cleared.')

    def test_a_failed_attempt_is_reported_once_and_the_button_still_works(self):
        def refuse(sim):
            def prepare():
                raise RuntimeError('need HSPY OFF/output zero')
            sim.backend.prepare, sim.backend.real_prepare = prepare, sim.backend.prepare
        h = self.harness(before=refuse)
        wait_until(lambda: 'Automatic synchronization failed' in self.events(h))
        time.sleep(2.0)
        self.assertEqual(self.events(h).count('Automatic synchronization failed'), 1)
        h.sim.backend.prepare = h.sim.backend.real_prepare
        h.unlock()
        self.assertEqual(h.post('/api/rpi/tool', {'cmd': 'prepare'})[0], 200)   # “synchronized to Nano”
        self.assertTrue(h.sim.backend.session)

    def test_it_can_be_switched_off(self):
        h = self.harness(auto_sync=False)
        h.connected()
        time.sleep(2.0)
        self.assertEqual(h.sim.backend.session, 0)


class OfflineTests(unittest.TestCase):
    def test_no_server_is_reported_in_plain_words(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'config.json').write_text(json.dumps({'rpi_host': '127.0.0.1', 'rpi_port': 9}), encoding='utf-8')
            app = pc.Application(folder)
            try:
                view = wait_until(lambda: (lambda v: v if '127.0.0.1:9' in v['error'] else None)(app.rpi.view()))
                self.assertFalse(view['connected'])
                with self.assertRaisesRegex(RuntimeError, 'not connected yet') as caught:
                    app.rpi_tool({'cmd': 'prepare'})
                self.assertEqual((caught.exception.status, caught.exception.code), (503, 'rpi_link'))
                with self.assertRaisesRegex(RuntimeError, 'not connected yet'):
                    app.rpi_tool({'cmd': 'spectrum'})
                with self.assertRaisesRegex(RuntimeError, 'not connected yet'):
                    app.set_parameters({'parameters': GOOD})
                self.assertIsNone(app.devices.owner)       # a tool that could not run gave the equipment back
                # 1.4.1: “stop” raised when it could not reach the RPi. STOP is never refused: it says what it could not do.
                reply = app.stop()
                self.assertEqual((reply['accepted'], reply['rpi']['result'], reply['pump']['result']), (True, 'unreachable', 'none'))
                self.assertIn('Entity mode', reply['rpi']['error'])
                state = app.state()
                self.assertEqual((state['rpi']['hv'], state['rpi']['flow']['ok'], state['rpi']['parameters']),
                                 ({'known': False, 'idle': False, 'reasons': ['RPi Not connected']}, False,
                                  {'values': None, 'nano_synced': None}))
                self.assertEqual(state['interlock']['manual']['need_hv_ack'], True)
            finally:
                app.close()

    def test_bad_settings_are_refused(self):
        for raw in ({'rpi_host': ''}, {'rpi_host': 'a b'}, {'rpi_port': '8767'}, {'rpi_port': 0}, {'extra': 1},
                    {'token': 'a\nb'}):
            with self.assertRaises(ValueError):
                pc.validate_config(raw)
        self.assertEqual(pc.validate_config({})['rpi_host'], '192.168.1.1')


# =====================================================================================================
# New in 2.0.0
# =====================================================================================================
def raw_request(port, method, path, headers=None, body=None, host=None):
    """One request as written, without urllib tidying anything: (status, headers, body)."""
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        connection.putheader('Host', f'127.0.0.1:{port}' if host is None else host)
        for name, value in (headers or {}).items():
            connection.putheader(name, value)
        if body is not None:
            connection.putheader('Content-Length', str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


class StaticTableTests(unittest.TestCase):
    """API.md C.2: a file is served only on an exact hit of a table made from a listing of the web folder."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='plasmic-web-')
        cls.web = Path(cls.temp.name) / 'web'
        cls.files = {'index.html': b'<!doctype html><title>t</title>', 'css/a.css': b'body { margin: 0 }',
                     'js/a.js': b'export const a = 1;\n', 'js/pages/b.js': b'export const b = 2;\n',
                     'fonts/f.ttf': bytes(range(256)), 'fonts/OFL.txt': 'SIL OPEN FONT LICENSE Authorization'.encode('utf-8')}
        for name, data in cls.files.items():
            (cls.web / name).parent.mkdir(parents=True, exist_ok=True)
            (cls.web / name).write_bytes(data)
        for name in ('secret.json', 'js/tool.py', 'notes.md', 'css/a.css.bak', 'js/A.JS'):
            if not (cls.web / name).exists():
                (cls.web / name).write_bytes(b'not for the browser')
        (Path(cls.temp.name) / 'outside.js').write_bytes(b'outside the web folder')

        class Stub:  # the static part of the server asks its Application only whether this is practice mode
            demo = False
        cls.server = pc.ExclusiveHTTPServer(('127.0.0.1', 0), pc.make_handler(Stub(), 'csrf', web=cls.web))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def check_headers(self, headers, guide=False):
        style = "'self' 'unsafe-inline'" if guide else "'self'"
        self.assertEqual(headers['Content-Security-Policy'],
                         f"default-src 'self'; style-src {style}; script-src 'self'; img-src 'self' data:; "
                         "frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        self.assertEqual((headers['Cache-Control'], headers['X-Content-Type-Options']), ('no-store', 'nosniff'))

    def test_every_file_type_is_served_with_its_type(self):
        for path, name, kind in (('/', 'index.html', 'text/html; charset=utf-8'), ('/index.html', 'index.html', 'text/html; charset=utf-8'),
                                 ('/css/a.css', 'css/a.css', 'text/css; charset=utf-8'),
                                 ('/js/a.js', 'js/a.js', 'text/javascript; charset=utf-8'),
                                 ('/js/pages/b.js', 'js/pages/b.js', 'text/javascript; charset=utf-8'),
                                 ('/fonts/f.ttf', 'fonts/f.ttf', 'font/ttf'), ('/fonts/OFL.txt', 'fonts/OFL.txt', 'text/plain; charset=utf-8'),
                                 ('/js/a.js?v=2', 'js/a.js', 'text/javascript; charset=utf-8')):
            status, headers, body = raw_request(self.port, 'GET', path)
            self.assertEqual((status, headers['Content-Type'], body), (200, kind, self.files[name]), path)
            self.assertEqual(int(headers['Content-Length']), len(body))
            self.check_headers(headers)

    def test_nothing_else_can_be_reached(self):
        outside = '/' + str(Path(self.temp.name) / 'outside.js').replace('\\', '/')
        for path in ('/secret.json', '/js/tool.py', '/notes.md', '/css/a.css.bak',              # not a type of the table
                     '/JS/a.js', '/js/a.JS', '/Index.html',                                     # not exactly a key
                     '/../outside.js', '/js/../../outside.js', '/..%2foutside.js', '/%2e%2e/outside.js',
                     '/js/%2e%2e/%2e%2e/outside.js', '/js/..%5c..%5coutside.js', '/js\\..\\..\\outside.js',
                     '/..\\outside.js', '//outside.js', outside, '/js/a.js/', '/js/a.js%00', '/js/a.js%20',
                     '/index.html/../../outside.js', '/js', '/js/', '/css/', '/fonts', '/app.py', '/../app2.py',
                     '/C:/Windows/win.ini', '/file:///C:/Windows/win.ini', '/js/pages/../a.js', '/./index.html'):
            status, headers, body = raw_request(self.port, 'GET', path)
            self.assertEqual((status, json.loads(body)), (404, {'error': 'Not found', 'code': 'not_found'}), path)
            self.check_headers(headers)
            self.assertNotIn(b'outside the web folder', body)

    def test_the_table_is_made_once_outside_practice_mode(self):
        table = httpd.StaticTable(self.web)
        self.assertEqual(sorted(table.table), ['/', '/css/a.css', '/fonts/OFL.txt', '/fonts/f.ttf', '/index.html', '/js/a.js',
                                               '/js/pages/b.js'])
        (self.web / 'js' / 'late.js').write_bytes(b'export const late = 3;\n')
        self.addCleanup((self.web / 'js' / 'late.js').unlink)
        time.sleep(httpd.RESCAN_SECONDS + 0.2)
        self.assertIsNone(table.find('/js/late.js'))
        self.assertEqual(raw_request(self.port, 'GET', '/js/late.js')[0], 404)
        # Practice mode (pages are being written while it runs): the folder is listed again, the lookup stays exact.
        practice = httpd.StaticTable(self.web, rescan=True)
        (self.web / 'js' / 'later.js').write_bytes(b'export const later = 4;\n')
        self.addCleanup((self.web / 'js' / 'later.js').unlink)
        self.assertIsNone(practice.find('/js/later.js'))                 # listed a moment ago
        time.sleep(httpd.RESCAN_SECONDS + 0.2)
        self.assertEqual(practice.find('/js/later.js')[1], 'text/javascript; charset=utf-8')
        self.assertIsNone(practice.find('/js/../../outside.js'))

    def test_the_web_folder(self):
        """pc\\web, and nothing is left of the folder the screen was built in (AMENDMENTS M7)."""
        folder = httpd.web_folder()
        self.assertEqual(folder, ROOT / 'pc' / 'web')
        self.assertFalse((ROOT / 'pc' / 'web2').exists())
        table = httpd.StaticTable(folder)
        self.assertIn('/', table.table)
        self.assertEqual({Path(key).suffix for key in table.table if key != '/'} - set(httpd.TYPES), set())
        for key, (path, kind) in table.table.items():
            self.assertTrue(path.is_file(), key)
            self.assertEqual(kind, httpd.TYPES[path.suffix])

    def test_what_the_http_library_refuses_gets_the_same_headers(self):
        for method in ('PUT', 'DELETE', 'OPTIONS', 'HEAD', 'PATCH'):
            status, headers, body = raw_request(self.port, method, '/')
            self.assertEqual(status, 501, method)
            self.check_headers(headers)


class RequestRuleTests(Base):
    """API.md C.1: Host, Origin, token, body, headers, error shape."""

    def test_host_origin_and_token(self):
        port = self.h.port
        ok = {'X-Token': self.h.csrf, 'Content-Type': 'application/json'}
        for host in (f'127.0.0.1:{port}', f'localhost:{port}'):
            self.assertEqual(raw_request(port, 'GET', '/api/state', host=host)[0], 200)
            self.assertEqual(raw_request(port, 'POST', '/api/lock', ok, b'{}', host=host)[0], 200)
        for host in ('evil.example', f'evil.example:{port}', '127.0.0.1', f'127.0.0.1:{port + 1}', f'[::1]:{port}', ''):
            for method, path, headers, body in (('GET', '/api/state', None, None), ('GET', '/', None, None),
                                                ('GET', '/guide', None, None), ('POST', '/api/stop', ok, b'{}')):
                status, headers, reply = raw_request(port, method, path, headers, body, host=host)
                self.assertEqual((status, json.loads(reply)), (403, {'error': 'Invalid host'}), (host, path))
                self.assertIn("default-src 'self'", headers['Content-Security-Policy'])
        for headers in ({}, {'X-Token': ''}, {'X-Token': 'wrong'}, {'X-Token': self.h.csrf + 'x'},
                        dict(ok, Origin='http://evil.example'), dict(ok, Origin='null'),
                        dict(ok, Origin=f'https://127.0.0.1:{port}'), dict(ok, Origin=f'http://127.0.0.1:{port + 1}')):
            for path in ('/api/stop', '/api/lock', '/api/schedule/start', '/api/nothing'):
                status, _headers, reply = raw_request(port, 'POST', path, headers, b'{}')
                self.assertEqual((status, json.loads(reply)), (403, {'error': 'Invalid local request'}), (headers, path))
        for origin in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'):
            self.assertEqual(raw_request(port, 'POST', '/api/lock', dict(ok, Origin=origin), b'{}')[0], 200)
        self.assertFalse(self.h.app.runner.active)

    def test_the_body_must_be_one_json_object(self):
        need = {'error': 'need JSON object', 'code': 'bad_request'}
        for data in (b'', b'[]', b'"text"', b'12', b'null', b'{', b'\xff\xfe', b'{"a": 1} trailing', b'[' * 5000):
            self.assertEqual(self.h.post('/api/operator', None, data=data), (400, need), data[:20])
        self.assertEqual(self.h.post('/api/operator', None, data=b'{"name": "' + b'x' * 100_000 + b'"}'), (400, need))
        self.h.unlock()
        big = json.dumps({'csv': 'action,target,value,unit,note\n' + '#' * 150_000}).encode()
        self.assertEqual(self.h.post('/api/recipes/validate', None, data=big)[0], 200)      # up to 300 000 bytes for a Recipe
        self.assertEqual(self.h.post('/api/recipes/publish', None, data=big)[0], 400)
        huge = json.dumps({'csv': '#' * 300_000}).encode()
        self.assertEqual(self.h.post('/api/recipes/validate', None, data=huge), (400, need))
        self.assertEqual(self.h.post('/api/operators', None, data=big), (400, need))        # not for anything else
        # A wrong body after the PIN gate, never before it: nobody learns anything without a session.
        self.assertEqual(self.h.post('/api/operators', None, data=b'[]', unlock='')[1]['code'], 'locked')

    def test_every_reply_is_json_with_the_headers(self):
        for method, path, body in (('GET', '/api/state', None), ('GET', '/api/bootstrap', None), ('GET', '/api/nothing', None),
                                   ('GET', '/api/runs/HMI-' + '0' * 32, None), ('GET', '/api/runlog?limit=0', None),
                                   ('POST', '/api/stop', b'{}'), ('POST', '/api/pause', b'{}'), ('POST', '/api/operators', b'{}'),
                                   ('POST', '/api/nothing', b'{}')):
            status, headers, reply = raw_request(self.h.port, method, path, {'X-Token': self.h.csrf}, body)
            self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8', path)
            self.assertEqual((headers['Cache-Control'], headers['X-Content-Type-Options']), ('no-store', 'nosniff'))
            self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
            self.assertNotIn('unsafe-inline', headers['Content-Security-Policy'])
            reply = json.loads(reply)
            if status != 200:
                self.assertEqual(sorted(reply), ['code', 'error'], path)
        status, headers, _body = raw_request(self.h.port, 'GET', '/guide')
        self.assertIn("style-src 'self' 'unsafe-inline'", headers['Content-Security-Policy'])   # the guide only

    def test_an_error_nobody_foresaw_is_500_and_goes_to_the_event_log_once(self):
        def broken():
            raise ZeroDivisionError('division by zero')
        self.h.app.recipes = broken
        for _ in range(3):
            self.assertEqual(self.h.get_code('/api/recipes'),
                             (500, {'error': 'Program internal error, details are in the event record', 'code': 'internal'}))
        found = [text for text in self.h.texts() if text.startswith('Program internal error (GET /api/recipes) : ')]
        self.assertEqual(len(found), 1)
        self.assertIn('ZeroDivisionError', found[0])
        self.assertEqual(self.h.get('/api/state')['version'], '2.0.0')       # and the program goes on

    def test_the_error_table(self):
        """API.md C.1 and AMENDMENTS M13: what each kind of refusal becomes."""
        board = pumpboard.PumpBoardError
        for exc, status, code in ((ValueError('x'), 400, 'bad_request'), (KeyError('x'), 404, 'not_found'),
                                  (RuntimeError('x'), 409, 'state'), (Busy('x', 'schedule_active'), 409, 'busy'),
                                  (Blocked('x', [{'code': 'hv_on', 'text': 't', 'fix': ''}]), 409, 'blocked'),
                                  (board('busy', 'x'), 409, 'busy'), (board('refused', 'x'), 409, 'refused'),
                                  (board('device', 'x'), 409, 'refused'), (board('blocked', 'x'), 409, 'blocked'),
                                  (board('stopped', 'x'), 409, 'state'), (board('link', 'x'), 503, 'pump_link'),
                                  (board('timeout', 'x'), 503, 'pump_link'),
                                  (pc.plasmic_client.PlasmicError('not_ready', 'x'), 409, 'not_ready'),
                                  (application.ApiError(503, 'rpi_link', 'x'), 503, 'rpi_link'),
                                  (application.ApiError(403, 'pin_unset', 'x'), 403, 'pin_unset')):
            got = httpd.failure(exc)
            self.assertEqual((got[0], got[1]['code']), (status, code), exc)
            self.assertEqual(got[1]['error'], 'Not found' if isinstance(exc, KeyError) else 'x')
        self.assertEqual(httpd.failure(Blocked('x', [{'code': 'hv_on', 'text': 't', 'fix': ''}]))[1]['reasons'],
                         [{'code': 'hv_on', 'text': 't', 'fix': ''}])
        for exc in (OSError('disk'), PermissionError('denied'), ZeroDivisionError(), TypeError('x')):
            self.assertIsNone(httpd.failure(exc), exc)          # 500: a file that cannot be written is not "locked"

    def test_numbers_json_cannot_hold_do_not_break_a_reply(self):
        self.assertEqual(json.loads(httpd.encode({'a': float('nan'), 'b': [1.5, float('inf')], 'c': {'d': -float('inf')}})),
                         {'a': None, 'b': [1.5, None], 'c': {'d': None}})


class RemovedTests(Base):
    def test_removed_routes_answer_404(self):
        self.h.connected()
        self.h.unlock()
        for path, body in (('/api/command', {'cmd': 'refresh'}), ('/api/command', {'cmd': 'plasma_run', 'confirm_high_voltage': True}),
                           ('/api/raw', {'line': 'hello'}), ('/api/config', {'rpi_host': '127.0.0.1', 'rpi_port': 1}),
                           ('/api/find', {}), ('/api/diagnose', {})):
            self.assertEqual(self.h.post(path, body), (404, {'error': 'Not found', 'code': 'not_found'}), path)
        for path in ('/api/command', '/api/raw', '/api/nothing', '/api/recipes/zz', '/api/recipes/' + '0' * 32,
                     '/api/rounds/' + '0' * 32, '/api/runs/HMI-' + '0' * 32, '/api/runs/XYZ-' + '0' * 32,
                     '/api/recipes/examples/other.csv', '/api/recipes/examples/..%2fplasma-only.csv',
                     '/api/unlock', '/api/stop'):                # a POST route is not a GET route
            self.assertEqual(self.h.get_code(path), (404, {'error': 'Not found', 'code': 'not_found'}), path)
        self.assertEqual(self.h.get_code('/api/calibrations/CAL-' + '0' * 32), (404, {'error': 'Calibration record does not exist', 'code': 'not_found'}))
        # No maintenance discharge (AMENDMENTS M3): the tool route knows three commands.
        code, reply = self.h.post('/api/rpi/tool', {'cmd': 'plasma_run', 'confirm_high_voltage': True})
        self.assertEqual((code, reply), (400, {'error': 'Unsupported command', 'code': 'bad_request'}))
        self.assertIsNone(self.h.sim.controller.job)
        self.assertEqual(sorted(self.h.state()['interlock']), ['manual', 'start'])

    def test_the_route_table_names_every_route_once(self):
        paths = [(route['method'], route['path']) for route in httpd.ROUTES]
        self.assertEqual(len(paths), len(set(paths)))
        for route in httpd.ROUTES:
            self.assertEqual(sorted(key for key in route if key not in ('run', 'call')), ['busy', 'limit', 'method', 'path', 'pin'])
            self.assertIn(route['method'], ('GET', 'POST'))
            self.assertTrue(route['path'].startswith('/api/'))
        text = (ROOT / 'pc' / 'httpd.py').read_text(encoding='utf-8')
        for _method, path in paths:                          # tests/test_web_static.py reads them out of the source
            self.assertIn("'%s'" % path, text)


class StateShapeTests(Base):
    options = {'pump': 'sim'}

    TOP = ['alarms', 'boot_id', 'calibration_active', 'calibration_run', 'demo', 'interlock', 'latest_run', 'measurement',
           'message', 'operator', 'operators', 'owner', 'pin_set', 'pump', 'revs', 'round', 'rpi', 'schedule', 'setup', 'time',
           'unlock_left_s', 'unlocked', 'version']
    RPI = ['addresses', 'connected', 'error', 'flow', 'found', 'host', 'hv', 'own_job', 'parameters', 'pending', 'port', 'server',
           'status', 'status_age_s']
    PUMP = ['banner_seen', 'clean_phase', 'cleaning', 'description', 'device', 'error', 'firmware_match', 'firmware_ok',
            'generation', 'groups', 'last_result', 'link', 'mode', 'motion_uncertain', 'motors', 'op', 'scale', 'settings',
            'since', 'syringe', 'text', 'valves', 'weight_dose']
    SCHEDULE = ['blank', 'calibration_id', 'confirmed', 'counts', 'created_at', 'error', 'estimate_seconds', 'finished_at', 'id',
                'kind', 'last_slot_at', 'next_index', 'next_slot_at', 'operator', 'planned_total', 'recipe', 'slots', 'spec',
                'status', 'uses_plasma', 'uses_pump', 'uses_rpi', 'version', 'voltage_v']
    ROUND = ['calibration_id', 'current', 'error', 'finished_at', 'id', 'measurements', 'mode', 'operator', 'paused_pumps',
             'phase', 'phases', 'plasma_job', 'prompt', 'recipe', 'rev', 'round', 'rounds', 'sample_kind', 'schedule_id', 'slot',
             'slots_planned', 'started_at', 'status', 'step', 'step_count', 'steps_window', 'stop_report', 'uses_plasma', 'uses_pump']

    def has(self, actual, wanted):
        """Every key of the contract is there. (The parts the other modules make may carry more: what the
        contract promises is what the page relies on.)"""
        self.assertEqual(sorted(set(wanted) - set(actual)), [])

    def test_state_has_every_key_of_the_contract(self):
        """API.md C.6 + AMENDMENTS M3 (no interlock.plasma), M5.3, M13 (pump.generation)."""
        self.h.connected()
        self.h.settled()
        state = self.h.state()
        self.assertEqual(sorted(state), self.TOP)
        self.assertIs(state['rpi']['parameters']['nano_synced'], True)
        self.assertEqual((state['version'], state['demo'], state['pin_set'], state['unlocked'], state['unlock_left_s']),
                         ('2.0.0', False, True, False, 0))
        self.assertRegex(state['boot_id'], r'^[0-9a-f]{16}$')
        self.assertRegex(state['time'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$')
        self.assertEqual((state['operator'], state['operators']), ('operator', ['operator']))
        self.assertEqual(state['setup'], {'pin': True, 'rpi': True, 'board': 'ok', 'operators': 1, 'recipes': 1, 'complete': True})
        self.assertIsNone(state['owner'])
        self.assertEqual(sorted(state['message']), ['level', 'text', 'time'])
        self.assertEqual(sorted(state['revs']), ['calibrations', 'events', 'measurements', 'recipes', 'runlog', 'runs'])
        self.has(state['rpi'], self.RPI)
        self.assertEqual(state['rpi']['hv'], {'known': True, 'idle': True, 'reasons': []})
        self.has(state['rpi']['flow'], ['background_frames', 'name', 'ok', 'text'])
        self.has(state['rpi']['parameters'], ['nano_synced', 'values'])
        self.assertEqual(sorted(state['rpi']['parameters']['values']),
                         ['Cycle', 'Integration_Time', 'Loopnum', 'Offtime', 'Ontime', 'Spectra_Interval', 'Voltage'])
        self.assertEqual(state['rpi']['server']['protocol'], 2)
        self.has(state['pump'], self.PUMP)
        self.assertEqual((state['pump']['mode'], state['pump']['link'], state['pump']['text']), ('sim', 'connected', 'simulation'))
        self.assertEqual([group['id'] for group in state['pump']['groups']], ['A', 'B'])
        self.assertEqual([motor['id'] for motor in state['pump']['motors']], [2, 3, 4, 5, 6, 7, 8, 9])
        self.has(state['pump']['motors'][0],
                 ['blocked', 'by', 'direction', 'flow_ml_min', 'fluid', 'group', 'id', 'label', 'left_s', 'state', 'valve'])
        self.has(state['pump']['syringe'],
                 ['busy', 'error', 'initialised', 'max_speed_ml_s', 'online', 'position', 'size_ul', 'speed_ml_s', 'volume_ul'])
        self.has(state['pump']['scale'], ['age_s', 'calibrated', 'calibrated_at', 'grams', 'online', 'reference_g', 'stable'])
        self.assertEqual(state['pump']['settings'], {'syringe_ul': 5000, 'syringe_speed_ml_s': 0.4})
        self.assertEqual(sorted(state['interlock']), ['manual', 'start'])           # no `plasma` (AMENDMENTS M3)
        manual = state['interlock']['manual']
        self.assertEqual((manual['ok'], manual['reasons'], manual['need_hv_ack']), (True, [], False))
        self.assertEqual(state['interlock']['start'], {'ok': True, 'reasons': []})
        self.assertEqual((state['schedule'], state['round'], state['latest_run']), (None, None, None))
        self.assertEqual(state['alarms'], {'worst': None, 'unacked': 0, 'counts': {'p1': 0, 'p2': 0, 'p3': 0}, 'active': []})
        self.assertEqual(state['measurement'], {'latest_sample': None, 'latest_blank': None})
        self.assertEqual((state['calibration_active'], state['calibration_run']), (None, None))
        # After a measurement: the schedule, the round, the newest spectrum set and the newest sample.
        self.assertEqual(self.h.start()[0], 200)
        self.h.finished()
        state = self.h.state()
        self.has(state['schedule'], self.SCHEDULE)
        self.assertEqual((state['schedule']['kind'], state['schedule']['slots'][0]['kind']), ('schedule', 'sample'))
        self.has(state['round'], self.ROUND)
        self.has(state['round']['steps_window'], ['from', 'items'])
        self.has(state['round']['steps_window']['items'][0],
                 ['error', 'finished_at', 'n', 'phase', 'result_text', 'started_at', 'status', 'text'])
        self.assertEqual((state['round']['mode'], state['round']['sample_kind'], state['round']['prompt']), ('schedule', 'sample', None))
        for key in ('job', 'kind', 'saved_at', 'voltage_v', 'peak_nm', 'peak_value', 'signal_frames', 'background_frames', 'flow',
                    'wait_s', 'round_id'):
            self.assertIn(key, state['latest_run'])
        sample = state['measurement']['latest_sample']
        self.assertEqual(sorted(sample), ['conc_reason', 'metals', 'time', 'valid'])
        self.assertEqual((sample['conc_reason'], sample['metals']),
                         ('Correction has not been applied yet', {metal: {'conc': None, 'zero': None} for metal in ('Cu', 'Zn', 'Ni')}))
        self.assertEqual(state['revs']['measurements'], 1)

    def test_bootstrap_has_every_key_of_the_contract(self):
        boot = self.h.get('/api/bootstrap')
        self.assertEqual(sorted(boot), ['boot_id', 'config', 'csrf', 'demo', 'hardware', 'labels', 'limits', 'pin_set',
                                        'practice_pin', 'recipe', 'ui_watchdog_s', 'version'])
        self.assertEqual((boot['csrf'], boot['version'], boot['demo'], boot['pin_set'], boot['practice_pin'], boot['ui_watchdog_s']),
                         (self.h.csrf, '2.0.0', False, True, None, 10))
        self.assertEqual(boot['boot_id'], self.h.state()['boot_id'])
        self.assertEqual(boot['config'], {'rpi_host': '127.0.0.1', 'rpi_port': self.h.sim.port, 'token_set': False})
        self.assertEqual(boot['limits'], {'run_max_s': 300, 'flow_min_ml_min': 0.1, 'dose_min': 0.01, 'dose_max': 1000,
                                          'calibration_min_g': 0.1, 'calibration_max_g': 20000})
        self.assertEqual(sorted(boot['recipe']), ['actions', 'examples', 'limits'])
        self.assertEqual(boot['recipe']['limits'], {'bytes': 200000, 'rows': 500, 'steps': 2000, 'nesting': 5, 'repeat': 20,
                                                    'delay_total_s': 7200})
        self.assertEqual([(row['file'], row['name']) for row in boot['recipe']['examples']],
                         [('plasma-only.csv', 'Only discharge measurement'), ('dose-measure-drain.csv', 'Example: Add liquid＋Measure＋discharge'),
                          ('cleaning.csv', 'Example: automatic cleaning')])
        actions = boot['recipe']['actions']
        self.assertEqual(sorted(actions[0]), ['action', 'group', 'name', 'target', 'value'])
        self.assertEqual([row['action'] for row in actions][:3], ['delay', 'pump_on', 'pump_reverse'])
        self.assertNotIn('nano_stop', [row['action'] for row in actions])
        self.assertEqual(sorted(boot['labels']), ['round', 'schedule', 'slot', 'step'])
        self.assertEqual((boot['labels']['schedule']['paused'], boot['labels']['slot']['skipped_paused'],
                          boot['labels']['round']['interrupted'], boot['labels']['step']['skipped']),
                         ('Suspended', 'skip (scheduling pause)', 'Interrupted', 'not executed'))
        hardware = boot['hardware']
        self.assertEqual([group['id'] for group in hardware['groups']], ['A', 'B'])
        for key in ('valves', 'max_flow_ml_min', 'gravimetric', 'cleaning', 'syringe', 'scale', 'pulses_per_ml', 'dose_pulses_per_ml'):
            self.assertIn(key, hardware)
        for path in ('plasma-only', 'dose-measure-drain', 'cleaning'):          # the three example files
            body, headers = self.h.get(f'/api/recipes/examples/{path}.csv', raw=True)
            self.assertTrue(body.startswith(b'\xef\xbb\xbfaction,target,value,unit,note'), path)
            self.assertEqual(headers['Content-Disposition'], f'attachment; filename="{path}.csv"')
            self.assertEqual(body[3:].replace(b'\r\n', b'\n'),
                             (ROOT / 'pc' / 'recipes' / f'{path}.csv').read_bytes().replace(b'\r\n', b'\n'))

    def test_two_tabs_see_the_same_operator_and_alarms(self):
        self.h.connected()
        first = self.h.unlock()                                   # tab 1 is in Maintenance; tab 2 is not
        code, reply = self.h.post('/api/operators', {'operators': ['Wang Xiaoming', 'Li Dahua', 'Wang Xiaoming']})
        self.assertEqual((code, reply), (200, {'operators': ['Wang Xiaoming', 'Li Dahua'], 'current': ''}))   # the seeded one is gone
        code, reply = self.h.post('/api/operator', {'name': 'Li Dahua'}, unlock='')
        self.assertEqual((code, reply), (200, {'operator': 'Li Dahua'}))
        self.assertEqual(self.h.post('/api/operator', {'name': 'stranger'}, unlock=''),
                         (400, {'error': 'operator is not in the list', 'code': 'bad_request'}))
        self.h.app.alarms.raise_('test_alarm', 2, 'test', 'Alarm for testing', 'event')
        one, two = self.h.get('/api/state', unlock=first), self.h.get('/api/state', unlock='')
        self.assertEqual((one['operator'], one['operators'], one['alarms']), (two['operator'], two['operators'], two['alarms']))
        self.assertEqual((one['operator'], one['alarms']['unacked'], one['alarms']['worst']), ('Li Dahua', 1, 2))
        self.assertEqual((one['unlocked'], two['unlocked']), (True, False))          # the PIN session is per tab
        self.assertTrue(0 < one['unlock_left_s'] <= 600)
        self.assertEqual(self.h.post('/api/alarms/ack', {'ids': [one['alarms']['active'][0]['id']]}, unlock=''), (200, {'acked': 1}))
        self.assertEqual(self.h.get('/api/state', unlock=first)['alarms']['unacked'], 0)
        self.assertEqual(self.h.post('/api/alarms/ack', {}, unlock='')[0], 400)
        for bad, text in ((['a' * 21], 'The operator name must be 1–20 characters,Not to be included < > " \\'), (['<b>'], 'The operator name must be 1–20 characters,Not to be included < > " \\'),
                          ([str(n) for n in range(51)], 'most 50 people')):
            self.assertEqual(self.h.post('/api/operators', {'operators': bad}), (400, {'error': text, 'code': 'bad_request'}))
        self.assertEqual(self.h.post('/api/operators', {'operators': ['Wang Xiaoming']})[1], {'operators': ['Wang Xiaoming'], 'current': ''})
        self.assertEqual(self.h.state()['operator'], '')          # removing the current operator clears the selection
        code, reply = self.h.start()
        self.assertEqual((code, reply['error']), (400, 'Please select the operator first'))
        actors = {row['text']: row['actor'] for row in self.h.app.store.events(limit=50)}
        self.assertEqual((actors['Select operator: Li Dahua'], actors['Operator list updated: Total 1 people']), ('Li Dahua', 'Maintenance'))


class StopTests(unittest.TestCase):
    def harness(self, **options):
        harness = Harness(**options)
        self.addCleanup(harness.close)
        return harness

    def test_stop_is_200_without_a_pin_and_when_nothing_runs(self):
        for options in ({}, {'pin': None, 'operator': None}):
            h = self.harness(**options)
            h.connected()
            for data in (b'{}', b'', b'not json', b'[1, 2]', b'{"anything": true}'):
                code, reply = h.post('/api/stop', None, data=data, unlock='')
                self.assertEqual(code, 200, data)
                self.assertEqual(reply, {'accepted': True, 'schedule_ended': False, 'round_stopping': False, 'manual_cancelled': False,
                                         'pump': {'result': 'none', 'error': ''}, 'rpi': {'result': 'confirmed', 'error': ''}})
            self.assertIn('Stop pressed: pump not in use; high pressure readback OFF', h.texts())
            self.assertEqual(h.app.alarms.view()['active'], [])
            self.assertEqual(h.sim.backend.run_stop, 0)

    def test_stop_reaches_the_pump_board_when_the_rpi_does_not_answer(self):
        h = self.harness(pump='sim')
        h.connected()
        self.assertEqual(h.post('/api/manual', {'op': 'pump_on', 'motor': 8, 'flow_ml_min': 40, 'direction': 1, 'seconds': 60})[0], 200)
        wait_until(lambda: h.app.pump._conn.raw._sim.mstate['B'] != 'idle')
        h.close_sim()                                             # the RPi is switched off
        code, reply = h.post('/api/stop', {})
        self.assertEqual((code, reply['accepted'], reply['manual_cancelled']), (200, True, True))
        self.assertEqual(reply['pump'], {'result': 'confirmed', 'error': ''})
        self.assertEqual(reply['rpi']['result'], 'unreachable')
        self.assertIn('Please physically turn off the high voltage immediately', reply['rpi']['error'])
        sim = h.app.pump._conn.raw._sim
        self.assertTrue(all(state == 'idle' for state in sim.mstate.values()))
        self.assertEqual(h.alarm('hv_stop_unconfirmed')['text'], 'The stop command cannot be sent RPi.RPi will turn off the high voltage automatically after finding that the connection is interrupted, but please go to the site to confirm.')
        self.assertIsNone(h.alarm('pump_stop_unconfirmed'))

    def test_stop_reaches_the_rpi_when_the_pump_board_does_not_answer(self):
        h = self.harness(pump='sim', speed=2)
        h.connected()
        plan = pt.FaultPlan()
        self.addCleanup(plan.restore)
        self.assertEqual(h.start()[0], 200)
        wait_until(lambda: h.sim.backend.run_stop == 1)
        plan.pull_cable(h.app.pump._conn.raw._sim)                # the USB cable of the pump board is pulled
        code, reply = h.post('/api/stop', {})
        self.assertEqual((code, reply['accepted'], reply['rpi']['result']), (200, True, 'cancel_sent'))
        self.assertIn(reply['pump']['result'], ('confirmed', 'unconfirmed', 'pending'))
        wait_until(lambda: h.sim.backend.run_stop == 0)
        self.assertEqual(h.finished()['status'] in ('stopped', 'failed'), True)

    def test_a_fault_inside_the_program_does_not_stand_between_the_button_and_the_stop(self):
        h = self.harness(speed=2)
        h.connected()
        self.assertEqual(h.start()[0], 200)
        wait_until(lambda: h.sim.backend.run_stop == 1)

        def broken(actor):
            raise TypeError('a bug')
        h.app.devices.stop_everything = broken
        code, reply = h.post('/api/stop', {})
        self.assertEqual((code, reply['accepted'], reply['rpi']), (200, True, {'result': 'cancel_sent', 'error': ''}))
        self.assertEqual((reply['pump'], reply['schedule_ended'], reply['round_stopping']), ({'result': 'none', 'error': ''}, True, True))
        wait_until(lambda: h.sim.backend.run_stop == 0)
        self.assertTrue(any(text.startswith('Program error when stopping') for text in h.texts()))
        del h.app.devices.stop_everything
        self.assertEqual(h.finished()['status'], 'stopped')


class PracticeModeTests(unittest.TestCase):
    """Start-Demo.cmd: both simulators (the RPi's in a process of its own), a fixed PIN, never a real port."""

    def test_practice_mode_runs_on_the_two_simulators(self):
        port = ct.free_port()
        with tempfile.TemporaryDirectory(prefix='plasmic-demo-') as folder:
            # A config.json and a pin.json of somebody's real data must not matter in practice mode.
            Path(folder, 'config.json').write_text(json.dumps({'rpi_host': '192.0.2.1', 'rpi_port': 1, 'token': 'secret-token'}),
                                                   encoding='utf-8')
            write_pin(folder, '135790')
            app = pc.Application(folder, demo=True, sim_port=port)
            server = pc.ExclusiveHTTPServer(('127.0.0.1', 0), pc.make_handler(app, 'csrf'))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                wait_until(lambda: app.rpi.latest() and app.pump.connected, 20)
                state = app.state()
                self.assertEqual((state['demo'], state['pin_set'], state['setup']['pin'], state['setup']['board']), (True, True, True, 'ok'))
                self.assertEqual((state['rpi']['connected'], state['rpi']['host'], state['rpi']['port'], state['rpi']['server']['sim']),
                                 (True, '127.0.0.1', port, True))
                self.assertEqual((state['pump']['mode'], state['pump']['link'], state['pump']['text']), ('sim', 'connected', 'simulation'))
                self.assertEqual(app.pump.ports(), {'simulated': True, 'current': {'enabled': True, 'device': 'simulation',
                                                                                    'description': 'built-in analog control board'},
                                                    'ports': [], 'other_count': 0})
                boot = app.bootstrap()
                self.assertEqual((boot['demo'], boot['practice_pin'], boot['pin_set']), (True, '000000', True))
                self.assertEqual(boot['config'], {'rpi_host': '127.0.0.1', 'rpi_port': port, 'token_set': False})
                self.assertIn('-B', app.simulator.args)           # the simulator leaves no __pycache__ either
                self.assertIsNone(app.simulator.poll())
                # The fixed PIN; the pin.json of the folder is not looked at.
                with self.assertRaises(application.ApiError) as caught:
                    app.unlock({'pin': '135790'})
                self.assertEqual((caught.exception.status, caught.exception.code), (409, 'pin'))
                session = app.unlock({'pin': '000000'})['session']
                self.assertEqual(app.state(session)['unlocked'], True)
                for call, body, text in ((app.rpi_config, {'rpi_host': '127.0.0.1', 'rpi_port': 1}, 'simulation mode is fixedly connected to the local simulator'),
                                         (app.pump_config, {'enabled': False}, 'Simulation mode uses the built-in simulation control board')):
                    with self.assertRaisesRegex(ValueError, '^' + text + '$'):
                        call(body)
                found = app.post('rpi_find', {}, session)        # looks at its own simulator only
                self.assertEqual((found['addresses'], [(row['host'], row['port'], row['sim']) for row in found['found']]),
                                 ([], [('127.0.0.1', port, True)]))
                # A short measurement, so that this test does not wait for the simulator's real seconds.
                short = {'Ontime': 25, 'Offtime': 5, 'Cycle': 2, 'Loopnum': 1, 'Integration_Time': 100, 'Spectra_Interval': 200,
                         'Voltage': 100}
                self.assertEqual(app.post('set_parameters', {'parameters': short}, session)['synced'], True)
                wait_until(lambda: (app.rpi.params_view()['values'] or {}).get('Loopnum') == 1)
                # AMENDMENTS M11: before a discharge the simulator is told what the pretend sample holds.
                self.assertEqual(app.runner.before_discharge, app._sim_sample)
                sent = []
                original = app.rpi.command
                app.rpi.command = lambda cmd, *args, **fields: (sent.append((cmd, fields.get('sample'))), original(cmd, *args, **fields))[1]
                app.set_operator({'name': ct.OPERATOR})
                seed = next(row['id'] for row in app.recipes() if row['name'] == ct.SEED_NAME)
                app.post('schedule_start', {'recipe_id': seed, 'schedule': {'start': {'mode': 'now'}, 'interval_minutes': 1,
                                                                             'end': {'mode': 'count', 'count': 1}},
                                            'confirm': {'high_voltage': True}})
                done = wait_until(lambda: (lambda v: v if v['status'] not in pc.schedule.ACTIVE else None)(app.runner.view()), 60)
                self.assertEqual((done['status'], done['counts']), ('completed', {'done': 1}))
                commands = [cmd for cmd, _sample in sent]
                self.assertLess(commands.index('sim_sample'), commands.index('plasma_run'))
                self.assertEqual(dict(sent)['sim_sample'], {'Cu': 0.5, 'Zn': 0.3, 'Ni': 0.48})     # the first demo sample (n = 0)
                del sent[:]
                app._sim_sample({'round_id': 'x', 'mode': 'schedule', 'sample_kind': 'sample', 'conc': None})
                app._sim_sample({'round_id': 'x', 'mode': 'schedule', 'sample_kind': 'blank', 'conc': None})
                app._sim_sample({'round_id': 'x', 'mode': 'calibration', 'sample_kind': 'sample', 'conc': {'Cu': 1, 'Zn': 2, 'Ni': 0}})
                self.assertEqual(sent, [('sim_sample', {'Cu': round(0.5 + 0.1 * math.sin(1 / 3), 4), 'Zn': round(0.3 + 0.05 * math.sin(1 / 5), 4),
                                                        'Ni': round(0.4 + 0.08 * math.cos(1 / 4), 4)}),
                                        ('sim_sample', {'Cu': 0.0, 'Zn': 0.0, 'Ni': 0.0}),
                                        ('sim_sample', {'Cu': 1.0, 'Zn': 2.0, 'Ni': 0.0})])
                self.assertEqual(app.state()['latest_run']['voltage_v'], 100)
                self.assertNotIn('secret-token', json.dumps(app.state()) + json.dumps(app.bootstrap()))
                child = app.simulator
            finally:
                server.shutdown()
                server.server_close()
                app.close()
            self.assertIsNotNone(child.poll())                    # the simulator ended with the program
            self.assertEqual(json.loads(Path(folder, 'config.json').read_text(encoding='utf-8'))['rpi_host'], '192.0.2.1')
            self.assertFalse(list(Path(folder).rglob('__pycache__')))
        pt.assert_nothing_opened()                                # serial.Serial was never called
        self.assertIsNone(pumpboard.PumpBoard._live)

    def test_practice_mode_stays_practice_whatever_it_is_told_and_says_when_its_simulator_is_missing(self):
        patch(self, application, 'TCPD', ROOT / 'tests' / 'no-such-simulator.py')
        patch(self, application, 'SIM_START_TRIES', 30)
        with tempfile.TemporaryDirectory(prefix='plasmic-demo-') as folder:
            app = pc.Application(folder, demo=True, pump_mode='real', sim_port=ct.free_port())
            try:
                self.assertEqual(app.pump.mode, 'sim')            # never a real port in practice mode
                wait_until(lambda: app.pump.connected, 20)
                texts = [row['text'] for row in app.store.events(limit=50)]
                self.assertTrue(any(text.startswith('RPi emulator does not start (port ') for text in texts), texts)
                state = app.state()
                self.assertEqual((state['rpi']['connected'], state['rpi']['host'], state['pump']['link']), (False, '127.0.0.1', 'connected'))
                self.assertEqual((state['setup']['pin'], state['setup']['rpi'], state['setup']['complete']), (True, False, False))
                self.assertIsNotNone(app.simulator.poll())
            finally:
                app.close()
        pt.assert_nothing_opened()

    def test_a_real_program_never_tells_the_simulator_what_to_measure(self):
        h = Harness()
        self.addCleanup(h.close)
        h.connected()
        self.assertIsNone(h.app.runner.before_discharge)
        sent = []
        original = h.app.rpi.command
        h.app.rpi.command = lambda cmd, *args, **fields: (sent.append(cmd), original(cmd, *args, **fields))[1]
        h.app._sim_sample({'round_id': 'x', 'mode': 'schedule', 'sample_kind': 'sample', 'conc': None})
        self.assertEqual(sent, [])


class ProgramTests(unittest.TestCase):
    """pc/app.py itself: what it offers to the installer and the tests, its options, the way it ends."""

    def test_the_import_surface_is_kept(self):
        for name in ('Application', 'ExclusiveHTTPServer', 'make_handler', 'PinLock', 'Store', 'write_json', 'now_iso',
                     'validate_config', 'DEFAULT_CONFIG', 'site_config', 'RUN_ID', 'schedule', 'netcheck', 'plasmic_client',
                     'datetime', 'VERSION', 'main'):
            self.assertTrue(hasattr(pc, name), name)
        self.assertEqual((pc.VERSION, application.VERSION, pc.DEFAULT_CONFIG), ('2.0.0', '2.0.0', store.DEFAULT_CONFIG))
        self.assertIs(pc.PinLock, pinlock.PinLock)
        self.assertIs(pc.Store, store.Store)
        text = Path(pc.__file__).read_text(encoding='utf-8')
        lines = [line for line in text.splitlines() if line and not line.startswith('#')]
        path_line = next(index for index, line in enumerate(lines) if line.startswith('sys.path.insert'))
        self.assertTrue(lines[path_line + 1].startswith('sys.dont_write_bytecode = True'))
        for name in (ENTRY + '.py', 'application.py', 'httpd.py'):
            data = (ROOT / 'pc' / name).read_bytes()
            self.assertNotIn(b'\r', data, name)
            self.assertFalse(data.startswith(b'\xef\xbb\xbf'), name)
            data.decode('utf-8')

    def test_importing_the_program_opens_nothing(self):
        code = ('import os, sys, threading\n'
                'sys.path.insert(0, sys.argv[1])\n'
                'import %s, pumpboard\n'
                'print(threading.active_count(), pumpboard._real_ports_allowed, pumpboard.PumpBoard._live, sys.dont_write_bytecode,'
                ' pumpboard._v7 is None)\n') % ENTRY
        done = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'pc')], capture_output=True, text=True, timeout=60,
                              env=dict(os.environ, PLASMIC_NO_SERIAL='1', PYTHONIOENCODING='utf-8'))
        self.assertEqual((done.returncode, done.stdout.split()), (0, ['1', 'False', 'None', 'True', 'True']), done.stderr)

    def run_main(self, *arguments):
        """main() with a pretend Application: (what it was built with, how often real ports were allowed, what was printed)."""
        import contextlib
        import io
        built, allowed = [], []

        class Pretend:
            def __init__(self, *args, **kwargs):
                built.append((args, kwargs))
                self.demo, self.closed = bool(args[1]), []

            def close(self, fast=False):
                self.closed.append(fast)
        patch(self, pc, 'Application', Pretend)
        patch(self, pumpboard, ALLOW, lambda: allowed.append(1))
        patch(self, pc.ExclusiveHTTPServer, 'serve_forever', lambda server: (_ for _ in ()).throw(KeyboardInterrupt()))
        patch(self, pc, 'install_console_handler', lambda app, closing=None: None)
        patch(self, pc.webbrowser, 'open', lambda url: self.fail('the browser was opened'))
        patch(self, sys, 'argv', ['app2.py', *arguments])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pc.main()
        return built, allowed, out.getvalue()

    def test_practice_mode_never_allows_real_ports_and_the_program_allows_them_once(self):
        with tempfile.TemporaryDirectory() as folder:
            built, allowed, said = self.run_main('--demo', '--no-browser', '--port', '0', '--sim-port', '8791', '--data', folder)
            self.assertEqual(allowed, [])
            self.assertEqual(built, [((Path(folder), True), {'pump_mode': 'sim', 'sim_port': 8791, 'keep_awake': True})])
            self.assertRegex(said, r'^Plasmic Electronic control 2\.0\.0: http://127\.0\.0\.1:0/   (simulation mode)\nClosing this window will end the program.\n$')
            # Not practice mode (nothing real is built here: the Application is a pretend one).
            built, allowed, said = self.run_main('--no-browser', '--port', '0', '--data', folder)
            self.assertEqual(allowed, [1])
            self.assertEqual(built, [((Path(folder), False), {'pump_mode': 'real', 'sim_port': 8767, 'keep_awake': True})])
            self.assertNotIn('simulation mode', said)
        self.assertFalse(pumpboard._real_ports_allowed)

    def test_a_second_copy_stops_before_it_touches_anything(self):
        holder = socket.socket()
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            holder.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        holder.bind(('127.0.0.1', 0))
        holder.listen(1)
        self.addCleanup(holder.close)
        patch(self, pc, 'Application', lambda *args, **kwargs: self.fail('the Application was built'))
        patch(self, pumpboard, ALLOW, lambda: self.fail('real ports were allowed'))
        patch(self, pc.webbrowser, 'open', lambda url: self.fail('the browser was opened'))
        patch(self, pc, 'PORT_GRACE_S', 0.6)                      # it does not answer as this program: a moment, not ten seconds
        patch(self, pc, 'PORT_PROBE_S', 0.3)
        patch(self, sys, 'argv', ['app2.py', '--port', str(holder.getsockname()[1])])
        began = time.monotonic()
        with self.assertRaises(SystemExit) as caught:
            pc.main()
        self.assertEqual(str(caught.exception),
                         f'port {holder.getsockname()[1]} is already used by another program (maybe a Plasmic electronic control). Please close it first.')
        self.assertLess(time.monotonic() - began, 3)

    # ── a second start while a copy of the program holds the port (AMENDMENTS M17) ──
    def holder(self, handler):
        """A web server on a free port that answers as a copy of the program would: -> (server, port)."""
        import socketserver

        class Held(pc.ExclusiveHTTPServer):
            serve_forever = socketserver.BaseServer.serve_forever      # run_main() replaces the one of the class
        server = Held(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()

        def end():
            if thread.is_alive():
                server.shutdown()
                thread.join(5)
            server.server_close()
        self.addCleanup(end)
        return server, server.server_address[1]

    def running_copy(self, demo):
        class Running(httpd._Base):
            def do_GET(handler):
                if handler.path == '/api/bootstrap' and handler.trusted():
                    return handler.respond(200, {'version': '2.0.0', 'demo': demo, 'csrf': 'x'})
                handler.respond(404, {'error': 'Not found', 'code': 'not_found'})
        return self.holder(Running)

    def second_start(self, *arguments):
        """main() started while something holds its port: (how it ended, what it printed, the pages it opened)."""
        import contextlib
        import io
        opened = []
        patch(self, pc, 'Application', lambda *args, **kwargs: self.fail('the Application was built'))
        patch(self, pumpboard, ALLOW, lambda: self.fail('real ports were allowed'))
        patch(self, pc.webbrowser, 'open', opened.append)
        patch(self, sys, 'argv', ['app2.py', *arguments])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            pc.main()
        return caught.exception.code, out.getvalue(), opened

    def test_a_second_start_opens_the_screen_of_the_copy_that_runs(self):
        _server, port = self.running_copy(demo=True)
        url = f'http://127.0.0.1:{port}/'
        code, said, opened = self.second_start('--demo', '--port', str(port))
        self.assertEqual((code, opened), (0, [url]))
        self.assertEqual(said, f'Plasmic The electronic control is already on (practice mode), and there is no need to turn on another one. Operation screen:{url}\n'
                               'This window can be closed; the original black window is the program itself. If the error occurs after\n')
        # Start-Kiosk.cmd opens the screen itself: nothing is opened for it.
        code, said, opened = self.second_start('--demo', '--no-browser', '--port', str(port))
        self.assertEqual((code, opened), (0, []))
        self.assertIn('is already on (practice mode)', said)

    def test_a_second_start_in_the_other_mode_says_which_copy_to_close(self):
        _server, port = self.running_copy(demo=True)                       # practice mode runs; the real program is started
        code, said, opened = self.second_start('--port', str(port))
        self.assertEqual((code, said, opened),
                         (f'Practice mode is still on, it uses the same port {port}.Please close its black window and open it again.', '', []))
        _server, port = self.running_copy(demo=False)
        code, said, opened = self.second_start('--demo', '--port', str(port))
        self.assertEqual((code, said, opened),
                         (f'official mode is still on, it uses the same port {port}.Please close its black window and open it again.', '', []))

    def test_a_second_start_leaves_a_copy_that_is_still_starting_alone(self):
        _server, port = self.holder(httpd.NotReady)
        code, said, opened = self.second_start('--port', str(port))
        self.assertEqual((code, said, opened),
                         (0, 'another Plasmic The electronic control is starting: the operation screen will open automatically after a while. This window can be closed.\n', []))

    def test_a_second_start_waits_for_the_copy_whose_window_was_just_closed(self):
        """What the person did on the rig: closed the black window and double-clicked again at once."""
        server, port = self.holder(httpd.Closing)

        def gone():                                  # the first copy ends (Windows gives it five seconds)
            server.shutdown()
            server.server_close()
        timer = threading.Timer(0.8, gone)
        timer.start()
        self.addCleanup(timer.cancel)
        with tempfile.TemporaryDirectory() as folder:
            began = time.monotonic()
            built, allowed, said = self.run_main('--demo', '--no-browser', '--port', str(port), '--sim-port', '8791', '--data', folder)
            took = time.monotonic() - began
        self.assertEqual(len(built), 1)                               # it started, once the port was free
        self.assertTrue(0.7 < took < 6, took)
        self.assertEqual(said.splitlines()[0], 'Previous Plasmic The electronic control is ending (stop the pump first and turn off the high voltage). Wait for it a few seconds...')
        self.assertEqual(said.count('Wait for a few seconds'), 1)                   # said once, however often it looked
        self.assertIn(f'Plasmic Electronic control 2.0.0: http://127.0.0.1:{port}/', said)

    def test_a_copy_that_never_ends_is_not_waited_for_forever(self):
        _server, port = self.holder(httpd.Closing)
        patch(self, pc, 'PORT_WAIT_S', 0.8)
        began = time.monotonic()
        code, said, opened = self.second_start('--port', str(port))
        self.assertEqual(code, f'port {port} is already used by another program (maybe a Plasmic electronic control). Please close it first.')
        self.assertEqual((said, opened), ('Previous Plasmic The electronic control is ending (stop the pump first and turn off the high voltage). Wait for it a few seconds...\n', []))
        self.assertTrue(0.7 < time.monotonic() - began < 4)

    def test_the_web_server_says_that_the_program_is_ending(self):
        _server, port = self.holder(httpd.Closing)
        for method, body in (('GET', None), ('POST', b'{}')):
            link = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            self.addCleanup(link.close)
            link.request(method, '/api/state', body, {'Content-Type': 'application/json'} if body else {})
            reply = link.getresponse()
            self.assertEqual((reply.status, json.loads(reply.read())), (503, {'error': 'Program is ending', 'code': 'closing'}), method)
        self.assertEqual(pc.port_holder(port), ('closing', None))
        _server, port = self.holder(httpd.NotReady)
        self.assertEqual(pc.port_holder(port), ('starting', None))
        _server, port = self.running_copy(demo=False)
        self.assertEqual(pc.port_holder(port), ('running', False))

    def test_set_pin_writes_the_file_and_says_how_to_replace_it(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as folder:
            patch(self, pc.getpass, 'getpass', lambda prompt: '13579246')
            patch(self, pc, 'Application', lambda *args, **kwargs: self.fail('the Application was built'))
            patch(self, sys, 'argv', ['app2.py', '--set-pin', '--data', folder])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                pc.main()
            self.assertEqual(out.getvalue().strip(), 'Maintenance PIN has been saved. forget PIN , double-click again Set-PIN.cmd Set new.')
            saved = json.loads(Path(folder, 'pin.json').read_text(encoding='utf-8'))
            self.assertEqual((sorted(saved), saved['iterations']), (['digest', 'iterations', 'salt'], 600_000))
            self.assertNotIn('13579246', json.dumps(saved))

    def test_closing_the_black_window_stops_everything_first(self):
        closed = []

        class Pretend:
            def close(self, fast=False):
                closed.append(fast)
        on_event = pc.console_event(Pretend())
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertIs(on_event(0), False)                     # Ctrl+C: Python's own way out
            self.assertEqual(closed, [])
            for kind in (pc.CTRL_CLOSE_EVENT, pc.CTRL_LOGOFF_EVENT, pc.CTRL_SHUTDOWN_EVENT, pc.CTRL_BREAK_EVENT):
                self.assertIs(on_event(kind), False)              # then Windows ends the program as it would have
        self.assertEqual(closed, [True, True, True, True])
        self.assertEqual(out.getvalue().splitlines(), ['Stopping pump and high pressure...'] * 4)
        # The web server is told first (M17): a copy started in these seconds reads “closing” and waits.
        order = []

        class Ordered:
            def close(self, fast=False):
                order.append('everything off')
        with contextlib.redirect_stdout(io.StringIO()):
            pc.console_event(Ordered(), lambda: order.append('web server told'))(pc.CTRL_CLOSE_EVENT)
            pc.console_event(Ordered(), lambda: 1 / 0)(pc.CTRL_CLOSE_EVENT)      # whatever happens there, everything is switched off
        self.assertEqual(order, ['web server told', 'everything off', 'everything off'])
        self.assertEqual((pc.CTRL_BREAK_EVENT, pc.CTRL_CLOSE_EVENT, pc.CTRL_LOGOFF_EVENT, pc.CTRL_SHUTDOWN_EVENT), (1, 2, 5, 6))

    def test_close_is_done_once_and_a_second_caller_waits(self):
        h = Harness()
        self.addCleanup(h.close)
        h.connected()
        order = []
        real = h.app._close

        def slow(fast):
            order.append('begin')
            time.sleep(0.3)
            real(fast)
            order.append('end')
        h.app._close = slow
        threads = [threading.Thread(target=h.app.close, kwargs={'fast': fast}) for fast in (True, False)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
            order.append('returned')
        self.assertEqual(order, ['begin', 'end', 'returned', 'returned'])
        self.assertFalse(h.app.rpi.thread.is_alive() and not h.app.rpi.closed.is_set())

    def test_a_program_that_could_not_start_leaves_nothing_behind(self):
        h = Harness(pump='sim')
        self.addCleanup(h.close)
        before = threading.active_count()
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'config.json').write_text(json.dumps({'rpi_host': '127.0.0.1', 'rpi_port': h.sim.port}), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'another PumpBoard is live'):      # one board per process
                pc.Application(folder, pump_mode='sim')
            wait_until(lambda: threading.active_count() <= before, 10)                   # its RPi link was closed again
        self.assertIs(pumpboard.PumpBoard._live, h.app.pump)


class CloseTests(unittest.TestCase):
    """SPEC B.11: the end of the program switches everything off first, and what it writes down is true."""

    def harness(self, **options):
        harness = Harness(**options)
        self.addCleanup(harness.close)
        return harness

    def requests(self, harness):
        """Every command the pretend RPi is asked from now on."""
        seen = []
        real = harness.sim.controller.handle

        def handle(connection, request):
            seen.append(str(request.get('cmd')).lower())
            return real(connection, request)
        harness.sim.controller.handle = handle
        return seen

    def test_an_idle_program_ends_without_a_word_to_the_rpi(self):
        h = self.harness(pump='sim')
        h.connected()
        h.settled()
        seen = self.requests(h)
        sim = h.app.pump._conn.raw._sim
        began = time.monotonic()
        h.app.close()
        self.assertLess(time.monotonic() - began, 5.0)
        self.assertNotIn('stop', seen)                            # the high voltage was known to be off: nothing to send
        self.assertTrue(all(state == 'idle' for state in sim.mstate.values()))
        self.assertEqual(h.app.alarms.view()['active'], [])
        self.assertIsNone(pumpboard.PumpBoard._live)
        self.assertFalse(h.app.rpi.thread.is_alive() and not h.app.rpi.closed.is_set())
        tx = [line.split(' ', 2)[2] for line in (h.folder / 'logs' / 'pumpboard.log').read_text(encoding='utf-8').splitlines()
              if line.split(' ', 2)[1:2] == ['tx']]
        self.assertGreaterEqual(tx.count('X'), 2)                 # at the connect, and at the end

    def test_closing_in_the_middle_of_a_round_stops_both_sides_and_says_what_it_saw(self):
        with_pump = ct.csv('pump_on,M8,40,mL/min,', 'delay,,20,s,', 'pump_off,M8,,,', 'plasma_run,,,,')
        for fast, text in ((False, with_pump), (True, ct.PLASMA_ONLY), (True, with_pump), (False, ct.PLASMA_ONLY)):
            h = self.harness(pump='sim', speed=2)
            h.connected()
            h.unlock()
            made = h.post('/api/recipes/publish', {'name': 'Halfway through', 'csv': text})[1]
            self.assertEqual(h.start(recipe_id=made['id'])[0], 200)
            sim = h.app.pump._conn.raw._sim
            if text is with_pump:
                wait_until(lambda: sim.mstate['B'] != 'idle')    # the round's pump is turning
            else:
                wait_until(lambda: h.sim.backend.run_stop == 1)  # the discharge is on
            seen = self.requests(h)
            began = time.monotonic()
            h.app.close(fast=fast)
            self.assertLess(time.monotonic() - began, 5.0, (fast, text))
            self.assertTrue(all(state == 'idle' for state in sim.mstate.values()), (fast, text))
            wait_until(lambda: h.sim.backend.run_stop == 0)
            wait_until(lambda: not h.app.runner.thread.is_alive(), 30)
            record = h.app.runner.round
            self.assertEqual((h.app.runner.view()['status'], record['status']), ('stopped', 'stopped'), (fast, record['error']))
            self.assertIn(record['stop_report']['pump']['result'], ('confirmed', 'none'), (fast, text))
            self.assertEqual(record['stop_report']['rpi']['result'], 'confirmed', (fast, text))
            self.assertEqual(h.app.alarms.view()['active'], [], (fast, text))     # no "stop not confirmed" about what was stopped
            self.assertIn('stop', seen)
            h.close()


class PumpPortTests(Base):
    """Maintenance › connection, the port of the pump board — in real mode, where this test process still cannot open anything:
    real ports were never allowed here, PLASMIC_NO_SERIAL=1 is set, and the port list is the made-up one of
    pump_testing (COM91 an Arduino Mega, COM92 a CH340, COM93 without a USB id)."""
    options = {'pump': 'real'}

    def test_choosing_the_port_of_the_pump_board(self):
        self.h.connected()
        self.h.unlock()
        state = self.h.state()
        self.assertEqual((state['pump']['mode'], state['pump']['link'], state['pump']['text'], state['setup']['board'], state['setup']['complete']),
                         ('real', 'unset', 'Pump control board not found: please connect USB Wire', 'unset', False))
        self.assertEqual(state['interlock']['manual']['reasons'][0]['code'], 'pump_unset')
        mega = {'device': 'COM91', 'description': 'Arduino Mega 2560 (COM91)', 'vid': '2341', 'pid': '0042',
                'serial_number': 'TEST0000000000000091'}
        self.assertEqual(self.h.get('/api/pump/ports'),
                         {'simulated': False, 'current': {'enabled': None, 'device': '', 'description': ''},
                          'ports': [dict(mega, chosen=False)], 'other_count': 2})      # only a Mega is ever listed
        self.assertEqual(self.h.post('/api/pump/reconnect', {}), (400, {'error': 'Pump control board not found', 'code': 'bad_request'}))
        for body, text in (({'enabled': True, 'device': 'COM92'}, 'COM92 Not now Arduino Mega (or removed). Didn’t turn it on.'),
                           ({'enabled': True, 'device': 'COM99'}, 'COM99 Not now Arduino Mega (or removed). Didn’t turn it on.'),
                           ({'enabled': True}, '? Not now Arduino Mega (or removed). Didn’t turn it on.'),
                           ({}, 'enabled Must be true or false'), ({'enabled': 'yes', 'device': 'COM91'}, 'enabled Must be true or false')):
            self.assertEqual(self.h.post('/api/pump/config', body), (400, {'error': text, 'code': 'bad_request'}), body)
        self.assertEqual(self.h.post('/api/pump/config', {'enabled': True, 'device': 'COM91'}, unlock=''),
                         (403, {'error': 'This operation is only for maintenance personnel (maintenance PIN) ', 'code': 'locked'}))
        self.assertEqual(self.h.post('/api/pump/config', {'enabled': True, 'device': 'COM91'}), (200, {'saved': True}))
        # The program would now open the port. This process may not: it says so, and has opened nothing.
        state = wait_until(lambda: (lambda s: s if s['pump']['link'] == 'forbidden' else None)(self.h.state()))
        self.assertEqual((state['pump']['text'], state['pump']['device'], state['setup']['board']),
                         ('This execution does not allow opening of the real port.', 'COM91', 'problem'))
        ports = self.h.get('/api/pump/ports')
        self.assertEqual((ports['current'], ports['ports']),
                         ({'enabled': True, 'device': 'COM91', 'description': 'Arduino Mega 2560 (COM91)'}, [dict(mega, chosen=True)]))
        self.assertEqual(self.h.post('/api/pump/reconnect', {}), (200, {'accepted': True}))
        self.assertEqual(self.h.post('/api/pump/config', {'enabled': False}), (200, {'saved': True}))    # “does not use the pump control board”
        state = wait_until(lambda: (lambda s: s if s['pump']['link'] == 'disabled' else None)(self.h.state()))
        self.assertEqual((state['pump']['text'], state['setup']['board']), ('Not used', 'off'))
        texts = self.h.texts()
        self.assertIn('pump control board is set to use COM91', texts)
        self.assertIn('Pump control board is set to not use', texts)
        self.assertEqual(self.h.start()[0], 200)                  # measuring needs no pump board
        self.assertEqual(self.h.finished()['status'], 'completed')
        pt.assert_nothing_opened()


class KeepAwakeTests(unittest.TestCase):
    """AMENDMENTS M9: Windows is asked to stay awake while something runs, and gets the request back."""

    def test_the_request_follows_the_work(self):
        asked = []
        patch(self, application, 'set_execution_state', asked.append)
        patch(self, application, 'KEEP_AWAKE_POLL_SECONDS', 0.05)
        sim = SimServer(2)
        sim.controller.audit = lambda *args, **kwargs: None
        self.addCleanup(sim.close)
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'config.json').write_text(json.dumps({'rpi_host': '127.0.0.1', 'rpi_port': sim.port}), encoding='utf-8')
            app = pc.Application(folder, keep_awake=True)
            try:
                wait_until(app.rpi.latest)
                time.sleep(0.3)
                self.assertEqual(asked, [])                       # nothing runs: nothing is asked
                app.set_operator({'name': ct.OPERATOR})
                seed = next(row['id'] for row in app.recipes() if row['name'] == ct.SEED_NAME)
                app.schedule_start({'recipe_id': seed, 'confirm': {'high_voltage': True},
                                    'schedule': {'start': {'mode': 'now'}, 'interval_minutes': 1, 'end': {'mode': 'count', 'count': 1}}})
                wait_until(lambda: asked)
                self.assertEqual(asked, [application.ES_CONTINUOUS | application.ES_SYSTEM_REQUIRED])
                self.assertEqual(asked[0], 0x80000001)
                wait_until(lambda: sim.backend.run_stop == 1)
                app.stop()
                wait_until(lambda: len(asked) == 2, 20)
                self.assertEqual(asked[1], application.ES_CONTINUOUS)
                # ... and at the end of the program, when it was still held.
                app.schedule_start({'recipe_id': seed, 'confirm': {'high_voltage': True},
                                    'schedule': {'start': {'mode': 'now'}, 'interval_minutes': 1, 'end': {'mode': 'count', 'count': 1}}})
                wait_until(lambda: len(asked) == 3)
            finally:
                app.close()
            self.assertEqual(asked[2:], [0x80000001, application.ES_CONTINUOUS])
            self.assertFalse(app._awake_thread.is_alive())

    def test_the_tests_never_ask(self):
        patch(self, application, 'set_execution_state', lambda flags: self.fail('Windows was asked'))
        h = Harness()
        self.addCleanup(h.close)
        h.connected()
        self.assertIsNone(h.app._awake_thread)
        self.assertEqual(h.start()[0], 200)
        self.assertEqual(h.finished()['status'], 'completed')


class ToolTests(Base):
    """Maintenance › plasma parameters/spectrum: the tools on the RPi hold the equipment while they run (SPEC B.7)."""
    options = {'speed': 2}

    def test_a_capture_keeps_the_equipment_until_it_has_ended(self):
        self.h.connected()
        self.h.unlock()
        code, reply = self.h.post('/api/rpi/tool', {'cmd': 'spectrum'})
        self.assertEqual(code, 200)
        job = reply['job']
        self.assertRegex(job, r'^CAP-[0-9a-f]{32}$')
        self.assertGreater(reply['estimate_s'], 0)
        state = self.h.state()
        self.assertEqual((state['owner']['kind'], state['owner']['label'], state['rpi']['own_job']),
                         ('tool', 'Maintenance: Capture spectrum (no discharge)', job))
        self.assertEqual(state['interlock']['start']['reasons'][0]['code'], 'tool_active')
        self.assertEqual(self.h.post('/api/rpi/tool', {'cmd': 'prepare'}), (409, {'error': 'Another operation is in progress', 'code': 'busy'}))
        code, refused = self.h.start()
        self.assertEqual((code, refused['code'], refused['reasons'][0]['code']), (409, 'blocked', 'tool_active'))
        self.assertEqual(self.h.sim.backend.run_stop, 0)          # a capture never switches the supply on
        state = wait_until(lambda: (lambda s: s if s['owner'] is None else None)(self.h.state()), 30)
        self.assertIsNone(state['rpi']['own_job'])
        latest = wait_until(lambda: (lambda run: run if run and run['job'] == job else None)(self.h.state()['latest_run']))
        self.assertEqual((latest['kind'], latest['source'], latest['integration_ms'], latest['round_id']),
                         ('capture', 'check', 1000, None))        # at the integration time of plasma parameters, kept as a check
        self.assertEqual(self.h.get('/api/runlog')['rows'], [])   # a check is not a measurement
        # STOP ends a capture too, and the equipment is free again.
        code, reply = self.h.post('/api/rpi/tool', {'cmd': 'spectrum'})
        self.assertEqual(code, 200)
        code, stopped = self.h.post('/api/stop', {})
        self.assertEqual((code, stopped['rpi']['result']), (200, 'cancel_sent'))
        wait_until(lambda: self.h.state()['owner'] is None, 30)
        wait_until(lambda: any(text.startswith('Spectrum acquisition is not completed: Stop has been pressed') for text in self.h.texts()))
        self.assertEqual(self.h.state()['latest_run']['job'], job)
        self.assertEqual(self.h.post('/api/rpi/tool', {'cmd': 'prepare'})[0], 200)
        actors = {row['text']: row['actor'] for row in self.h.app.store.events(limit=50)}
        self.assertEqual((actors['Start capturing spectrum (no discharge)'], actors['Synced Nano parameter']), ('Maintenance', 'Maintenance'))

    def test_clearing_a_nano_fault_and_a_sync_that_fails(self):
        self.h.connected()
        self.h.unlock()
        self.h.sim_request('sim_fault', fault='HEARTBEAT_LOST')
        alarm = wait_until(lambda: self.h.alarm('nano_fault'))
        self.assertEqual((alarm['priority'], alarm['area'], alarm['cleared']), (2, 'high pressure', False))
        code, reply = self.h.post('/api/rpi/tool', {'cmd': 'clear_fault'})
        self.assertEqual((code, reply['cleared'], reply['previous_fault']), (200, True, 'HEARTBEAT_LOST'))
        wait_until(lambda: self.h.alarm('nano_fault')['cleared'])
        code, reply = self.h.post('/api/rpi/tool', {'cmd': 'clear_fault'})
        self.assertEqual((code, reply['cleared']), (200, False))
        self.assertIn('Clear Nano Fault: Cleared HEARTBEAT_LOST', self.h.texts())
        self.assertIn('Clear Nano Fault: No fault', self.h.texts())
        for body in ({}, {'cmd': 'status'}, {'cmd': 'stop'}, {'cmd': 'set_parameters'}, {'cmd': ['prepare']}):
            self.assertEqual(self.h.post('/api/rpi/tool', body), (400, {'error': 'Unsupported command', 'code': 'bad_request'}), body)

        def refuse():
            raise RuntimeError('need HSPY OFF/output zero')
        self.h.sim.backend.prepare = refuse
        code, reply = self.h.post('/api/parameters', {'parameters': GOOD})
        self.assertEqual((code, reply['saved'], reply['synced']), (200, True, False))
        self.assertIn('need HSPY OFF/output zero', reply['sync_error'])
        self.assertTrue(any(text.startswith('Parameters have been saved but synchronized Nano failed:') for text in self.h.texts()))
        self.assertIsNone(self.h.state()['owner'])
        code, reply = self.h.post('/api/rpi/tool', {'cmd': 'prepare'})
        self.assertEqual(code, 409)                               # the RPi's own refusal, with its own words and code
        self.assertIn('need HSPY OFF/output zero', reply['error'])
        self.assertNotIn(reply['code'], ('state', 'busy', 'blocked'))
        self.assertIsNone(self.h.state()['owner'])                # a tool that failed gave the equipment back


class AlarmWiringTests(unittest.TestCase):
    """AMENDMENTS M10 / M13: the alarms of the two links go into the one alarm list; a lost link is high while a
    busy schedule uses that side."""

    def harness(self, **options):
        harness = Harness(**options)
        self.addCleanup(harness.close)
        return harness

    def test_a_lost_rpi_link_with_and_without_a_schedule(self):
        h = self.harness()
        h.connected()
        self.assertEqual(h.state()['alarms']['active'], [])
        h.close_sim()                                             # the RPi is switched off
        alarm = wait_until(lambda: h.alarm('rpi_link_lost'), 20)
        self.assertEqual((alarm['priority'], alarm['area'], alarm['text']), (3, 'RPi', 'RPi The connection was interrupted and the program is reconnecting.'))
        state = h.state()
        self.assertEqual((state['rpi']['connected'], state['alarms']['worst'], state['setup']['rpi']), (False, 3, True))
        self.assertEqual(state['interlock']['manual']['need_hv_ack'], True)
        # The same while a schedule waits for its next slot: high.
        h = self.harness()
        h.connected()
        self.assertEqual(h.start(count=2, interval=120)[0], 200)
        wait_until(lambda: h.view()['counts'].get('done') == 1 and h.view()['status'] == 'waiting')
        h.close_sim()
        alarm = wait_until(lambda: h.alarm('rpi_link_lost'), 20)
        self.assertEqual((alarm['priority'], alarm['text']), (2, 'RPi The connection was interrupted and the program is reconnecting.'))
        code, reply = h.post('/api/stop', {})
        self.assertEqual((code, reply['schedule_ended'], reply['rpi']['result']), (200, True, 'unreachable'))
        wait_until(lambda: not h.app.runner.active)

    def test_what_the_links_say_is_forwarded_as_it_is(self):
        h = self.harness()
        h.connected()
        forward = h.app._device_alarm
        forward('raise', 'pump_link_lost', 3, 'pump', 'The pump control board connection is interrupted and the program is reconnecting.', 'condition')
        forward('raise', 'pump_reset', 2, 'pump', 'The pump control board restarted itself.', 'event')
        self.assertEqual({row['key']: (row['priority'], row['area']) for row in h.state()['alarms']['active']},
                         {'pump_reset': (2, 'pump'), 'pump_link_lost': (3, 'pump')})
        forward('clear', 'pump_link_lost')
        self.assertEqual({row['key']: row['cleared'] for row in h.state()['alarms']['active']},
                         {'pump_reset': False, 'pump_link_lost': True})
        self.assertEqual(h.post('/api/alarms/ack', {'all': True}), (200, {'acked': 2}))
        self.assertEqual(h.state()['alarms']['active'], [])
        # Only the names of the route table can be asked for through post().
        for name in ('close', 'state', '_close', 'require_pin', 'post', '__init__', 'nothing'):
            with self.assertRaises(KeyError):
                h.app.post(name, {}, '')


class RobustnessTests(Base):
    """Whatever a request holds, a route answers with one of its refusals — never with a fault of the program."""
    options = {'pump': 'sim'}

    FIELDS = {
        '/api/unlock': ['pin'], '/api/lock': [], '/api/operator': ['name'], '/api/operators': ['operators'],
        '/api/pause': [], '/api/resume': [],
        '/api/schedule/preview': ['recipe_id', 'schedule', 'blank'], '/api/schedule/start': ['recipe_id', 'schedule', 'blank', 'confirm'],
        '/api/alarms/ack': ['ids', 'all'],
        '/api/manual': ['op', 'motor', 'flow_ml_min', 'direction', 'seconds', 'volume_ml', 'target_g', 'kind', 'position'],
        '/api/recipes/validate': ['file_b64', 'csv', 'rows'], '/api/recipes/publish': ['name', 'csv'],
        '/api/recipes/delete': ['id', 'pin'],
        '/api/rpi/config': ['rpi_host', 'rpi_port', 'token', 'clear_token'], '/api/rpi/diagnose': [], '/api/rpi/tool': ['cmd'],
        '/api/rpi/check': [],
        '/api/parameters': ['parameters'],
        '/api/pump/config': ['enabled', 'device'], '/api/pump/settings': ['syringe_ul', 'syringe_speed_ml_s'],
        '/api/scale/calibrate': ['grams'],
        '/api/flow': ['steps'], '/api/pump/calibrate': ['motor', 'measured_ml', 'nominal_ml'],
        '/api/pump/calibrate/reset': ['motor'], '/api/pump/setup': ['pumps', 'valves'],
        '/api/calibration/plan': ['plan'], '/api/calibration/check': ['plan'], '/api/calibration/start': ['plan', 'confirm'],
        '/api/calibration/confirm': [], '/api/calibration/analyze': ['id'], '/api/calibration/exclude': ['id', 'exclude'],
        '/api/calibration/apply': ['id'], '/api/calibration-config': ['config'], '/api/blank-qc': ['qc'],
        '/api/blank-zero': ['settings'],
    }
    ODD = [None, 0, -1, 1.5, True, False, '', 'x', 'x' * 300, '0' * 32, 'CAL-' + '0' * 32, [], {}, [[]], [1, 'a', None, []],
           {'a': 1}, {'mode': 'now'}, 1e308, '\ud83d', 'plasma_run', 'pump_on', 'prepare']

    def test_no_body_makes_a_route_fail(self):
        self.h.connected()
        self.h.settled()
        self.h.unlock()
        known = {route['path'] for route in httpd.ROUTES if route['method'] == 'POST'}
        self.assertEqual(set(self.FIELDS) | {'/api/stop', '/api/rpi/find', '/api/pump/reconnect', '/api/scale/tare'}, known)
        seen = set()
        for path, fields in sorted(self.FIELDS.items()):
            self.h.app.store.set_operators([ct.OPERATOR])         # (the list and the choice may have been emptied just now)
            self.h.app.operator = ct.OPERATOR
            # A session by the back door: this test types so many wrong PINs that the front door shuts, and it locks itself out.
            with self.h.app.pin.lock:
                self.h.app.pin.sessions['a-session-for-this-test'] = time.monotonic() + 600
            self.h.session = 'a-session-for-this-test'
            bodies = [{}] + [{field: value} for field in fields for value in self.ODD]
            bodies += [{field: value for field in fields} for value in self.ODD if fields]
            for body in bodies:
                code, reply = self.h.post(path, body)
                self.assertIn(code, (200, 400, 404, 409, 503), (path, body, reply))
                seen.add(code)
        self.assertLessEqual({200, 400, 409}, seen)
        self.assertFalse([text for text in self.h.texts() if text.startswith('Program internal error')])
        self.assertIsNone(self.h.sim.controller.job)              # and nothing of it ever discharged
        self.assertFalse(self.h.app.runner.active)
        state = self.h.state()
        self.assertEqual((state['version'], state['pump']['link']), ('2.0.0', 'connected'))
        self.assertEqual(self.h.app.store.config['rpi_host'], '127.0.0.1')

    def test_no_query_makes_a_get_fail(self):
        self.h.connected()
        for path in ('/api/runlog', '/api/events', '/api/runs', '/api/measurements', '/api/state', '/api/recipes', '/api/calibration'):
            for query in ('', '?', '?limit=', '?limit=5&limit=6', '?limit=-1', '?limit=1e3', '?limit=%00', '?before=', '?before=%ff',
                          '?before=2026-13-45T99:99:99', '?before=' + '9' * 500, '?hours=abc', '?hours=99999999999999999999',
                          '?x=1&y=2', '?limit=5;before=x', '?' + 'a=1&' * 500):
                code, reply = self.h.get_code(path + query)
                self.assertIn(code, (200, 400), (path, query, reply if code != 200 else ''))
        self.assertFalse([text for text in self.h.texts() if text.startswith('Program internal error')])


class NoVendorTests(Base):
    def test_without_the_copied_pump_files_only_recipes_that_need_no_board_pass(self):
        """API.md C.3.1: `hardware` is null when pc\\vendor is incomplete; measuring without the board still works."""
        self.h.connected()
        self.h.app.pump.hardware = lambda: None       # what the pump board answers when pc\vendor cannot be loaded
        boot = self.h.get('/api/bootstrap')
        self.assertIsNone(boot['hardware'])
        self.assertEqual(boot['recipe']['actions'][0]['action'], 'delay')
        self.assertEqual([(row['name'], row['valid']) for row in self.h.get('/api/recipes')['recipes']], [('Only discharge measurement', True)])
        self.h.unlock()
        code, checked = self.h.post('/api/recipes/validate', {'rows': [['dose_volume', 'M8', '1', 'mL', '']]})
        self.assertEqual((code, checked['valid']), (200, False))
        self.assertEqual(checked['error'], 'No. 2 Column: Program folder is incomplete (missing pc\\vendor) ,cannot do ”quantitative (volume)”. Please copy the entire folder again.')
        self.assertEqual(self.h.post('/api/recipes/publish', {'name': 'Add liquid', 'csv': checked['csv']})[0], 400)
        state = self.h.state()
        self.assertEqual((state['pump']['mode'], state['pump']['groups'], state['setup']['board']), ('off', [], 'off'))
        self.assertEqual(self.h.start()[0], 200)
        self.assertEqual(self.h.finished()['status'], 'completed')

    def test_leaving_maintenance_is_written_down_once(self):
        session = self.h.unlock()
        self.assertEqual(self.h.post('/api/lock', {}), (200, {'locked': True}))
        self.assertEqual(self.h.post('/api/lock', {}), (200, {'locked': True}))          # not logged in any more: nothing to say
        self.assertEqual(self.h.post('/api/lock', {}, unlock='made-up'), (200, {'locked': True}))
        self.assertEqual(self.h.state()['unlocked'], False)
        rows = [row for row in self.h.app.store.events(limit=50) if row['text'] == 'Maintenance mode has been logged out']
        self.assertEqual([row['actor'] for row in rows], ['Maintenance'])
        self.assertNotIn(session, json.dumps(self.h.get('/api/events?limit=50')))


class StartingTests(unittest.TestCase):
    def test_the_port_is_held_while_the_program_is_still_being_built(self):
        server = pc.ExclusiveHTTPServer(('127.0.0.1', 0), httpd.NotReady)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        starting = {'error': 'The program is starting, please wait.', 'code': 'starting'}
        for method, path, body in (('GET', '/', None), ('GET', '/api/state', None), ('POST', '/api/stop', b'{}')):
            status, headers, reply = raw_request(port, method, path, {'X-Token': 'x'}, body)
            self.assertEqual((status, json.loads(reply)), (503, starting), path)
            self.assertIn("default-src 'self'", headers['Content-Security-Policy'])
        with self.assertRaises(OSError):                          # a second copy of the program stops here
            pc.ExclusiveHTTPServer(('127.0.0.1', port), httpd.NotReady)

        class Stub:
            demo = False
        server.RequestHandlerClass = pc.make_handler(Stub(), 'csrf')   # ... and then the program is there
        status, headers, body = raw_request(port, 'GET', '/')
        self.assertEqual((status, headers['Content-Type']), (200, 'text/html; charset=utf-8'))


# =====================================================================================================
# End to end, on the two simulators
# =====================================================================================================
class MeasurementTests(Base):
    options = {'speed': 10}

    def test_one_measurement_from_the_page(self):
        """A count-1 schedule: the state shows the round, the spectrum is stored, the run-log row exists."""
        self.h.connected()
        before = self.h.state()['revs']
        code, plan = self.h.post('/api/schedule/preview', self.h.body(confirm=False))
        self.assertEqual((code, plan['planned_total'], plan['blockers'], plan['needs']['high_voltage']), (200, 1, [], True))
        code, started = self.h.start()
        self.assertEqual((code, started['accepted'], started['schedule']['status'], started['schedule']['operator']),
                         (200, True, 'waiting', 'operator'))
        running = wait_until(lambda: (lambda s: s if s['round'] and s['round']['status'] == 'running'
                                      and (s['round']['current'] or {}).get('detail', '').startswith('Discharging') else None)(self.h.state()))
        self.assertEqual((running['schedule']['status'], running['schedule']['slots'][0]['status']), ('running', 'running'))
        self.assertEqual((running['owner']['kind'], running['owner']['label']), ('round', 'Discharge measurement only 1/1 Second-rate'))
        current = running['round']['current']
        self.assertEqual((current['n'], current['action'], current['text']), (1, 'plasma_run', 'discharge＋capture spectrum'))
        self.assertRegex(current['detail'], r'^Discharging·Signal \d/8 open$')
        self.assertEqual((running['round']['slot'], running['round']['slots_planned'], running['round']['recipe']['name'],
                          running['round']['operator'], running['round']['steps_window']['items'][0]['status']),
                         (1, 1, 'Only discharge measurement', 'operator', 'running'))
        self.assertEqual(running['round']['plasma_job'], running['rpi']['status']['job']['id'])
        self.assertEqual((running['rpi']['hv']['idle'], running['rpi']['hv']['reasons']), (False, ['is discharging']))
        self.assertEqual(running['interlock']['start']['reasons'][0]['code'], 'schedule_active')
        done = self.h.finished()
        self.assertEqual((done['status'], done['counts']), ('completed', {'done': 1}))
        slot = done['slots'][0]
        state = wait_until(lambda: (lambda s: s if s['owner'] is None else None)(self.h.state()))
        self.assertEqual((state['round']['status'], state['round']['id'], state['round']['current']), ('completed', slot['round_id'], None))
        self.assertEqual(state['round']['steps_window']['items'][0]['status'], 'done')
        self.assertEqual(state['rpi']['hv'], {'known': True, 'idle': True, 'reasons': []})
        for key in ('events', 'runlog', 'runs', 'measurements'):
            self.assertGreater(state['revs'][key], before[key], key)
        # The spectrum set is stored and says where it came from.
        runs = self.h.get('/api/runs?limit=5')['runs']
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0]['job'], runs[0]['kind'], runs[0]['round_id'], runs[0]['recipe_name'], runs[0]['operator'],
                          runs[0]['slot'], runs[0]['source']),
                         (slot['job'], 'plasma', slot['round_id'], 'Only discharge measurement', 'operator', 1, 'round'))
        self.assertEqual(state['latest_run']['job'], slot['job'])
        self.assertIn('axis', self.h.get('/api/runs/' + slot['job']))
        # The run-log row, and the round's own record.
        log = self.h.get('/api/runlog?limit=5')
        self.assertEqual((len(log['rows']), log['more']), (1, False))
        row = log['rows'][0]
        self.assertEqual((row['kind'], row['status'], row['operator'], row['recipe_name'], row['slot'], row['slots_planned'],
                          row['round_id'], row['spectra'], row['schedule_id'], row['sample_kind']),
                         ('round', 'completed', 'operator', 'Only discharge measurement', 1, 1, slot['round_id'], [slot['job']], done['id'], 'sample'))
        self.assertRegex(row['result_text'], r'^Uncorrected · Highest Peak \d+\.\d nm$')
        record = self.h.get('/api/rounds/' + slot['round_id'])
        self.assertEqual((record['status'], record['recipe']['csv'], record['steps'][0]['status'], record['plasma']['jobs'][0]['job']),
                         ('completed', self.h.get('/api/recipes/' + self.h.seed)['csv'], 'done', slot['job']))
        self.assertEqual(record['versions']['pc'], '2.0.0')
        events = self.h.get('/api/events?limit=3')
        self.assertEqual((len(events['events']), events['more']), (3, True))
        self.assertEqual(sorted(events['events'][0]), ['actor', 'level', 'text', 'time'])
        last = events['events'][-1]['time']
        older = self.h.get('/api/events?limit=200&before=' + urllib.request.quote(last))
        self.assertTrue(older['events'])
        self.assertTrue(all(item['time'] < last for item in older['events']))
        self.assertEqual(self.h.get('/api/events?limit=200&before=' + last), older)     # a "+" that was not escaped
        for path, text in (('/api/events?limit=0', 'limit Must be 1–200 integer'), ('/api/runlog?limit=201', 'limit Must be 1–200 integer'),
                           ('/api/runs?limit=x', 'limit Must be 1–200 integer'), ('/api/events?before=yesterday', 'before is incorrect Checklist for'),
                           ('/api/measurements?hours=0', 'hours Must be 1–8784 integer')):
            self.assertEqual(self.h.get_code(path), (400, {'error': text, 'code': 'bad_request'}), path)
        rows = self.h.get('/api/measurements?hours=1')
        self.assertEqual(sorted(rows), ['blank_zero', 'calibration', 'coefficients', 'qc', 'rows', 'unit'])
        self.assertEqual((len(rows['rows']), rows['rows'][0]['job'], rows['rows'][0]['round_id'], rows['unit']), (1, slot['job'], slot['round_id'], 'mg/L'))

    def test_pause_and_resume_between_two_slots(self):
        self.h.connected()
        self.assertEqual(self.h.start(count=3, interval=4)[0], 200)
        wait_until(lambda: self.h.view()['counts'].get('done') == 1 and self.h.view()['status'] == 'waiting')
        self.assertEqual(self.h.post('/api/pause', {}), (200, {'accepted': True, 'status': 'paused'}))
        self.assertEqual(self.h.state()['schedule']['status'], 'paused')
        self.assertEqual(self.h.post('/api/pause', {})[0], 409)
        self.assertEqual(self.h.post('/api/resume', {}), (200, {'accepted': True, 'status': 'waiting'}))
        done = self.h.finished()
        self.assertEqual(done['status'], 'completed')
        self.assertIn('Pause pressed', self.h.texts())
        self.assertIn('Pressed to continue', self.h.texts())


class ManualPumpTests(Base):
    options = {'pump': 'sim'}

    def motor(self, number):
        return next(row for row in self.h.state()['pump']['motors'] if row['id'] == number)

    def test_a_manual_pump_run_from_the_page(self):
        self.h.connected()
        manual = self.h.state()['interlock']['manual']
        self.assertEqual((manual['ok'], manual['reasons'], manual['need_hv_ack']), (True, [], False))
        code, reply = self.h.post('/api/manual', {'op': 'pump_on', 'motor': 8, 'flow_ml_min': 40, 'direction': 1, 'seconds': 30})
        self.assertEqual((code, reply), (200, {'accepted': True, 'motor': 8, 'until_s': 30}))
        row = wait_until(lambda: (lambda m: m if m['state'] == 'run' else None)(self.motor(8)))
        self.assertEqual((row['by'], row['flow_ml_min'], row['direction']), ('manual', 40, 1))
        self.assertTrue(0 < row['left_s'] <= 30)
        state = self.h.state()
        self.assertEqual((state['owner']['kind'], state['owner']['label']), ('manual', 'Manual: Pump 7 · OUT'))
        self.assertEqual(self.motor(9)['blocked'], 'B group is in use')
        # What is refused while it runs: a second motor of the group, a schedule, a maintenance tool.
        code, reply = self.h.post('/api/manual', {'op': 'pump_on', 'motor': 9, 'flow_ml_min': 40, 'direction': 1, 'seconds': 5})
        self.assertEqual((code, reply['code']), (409, 'refused'))
        self.assertIn('B group can only transfer one at a time', reply['error'])
        code, reply = self.h.start()
        self.assertEqual((code, reply['code'], [item['code'] for item in reply['reasons']]), (409, 'blocked', ['manual_active', 'pump_moving']))
        self.h.unlock()
        code, reply = self.h.post('/api/rpi/tool', {'cmd': 'prepare'})
        self.assertEqual((code, reply), (409, {'error': 'Another operation is in progress', 'code': 'busy'}))
        code, reply = self.h.post('/api/manual', {'op': 'pump_off', 'motor': 8})
        self.assertEqual((code, reply), (200, {'stopped': True}))
        wait_until(lambda: self.motor(8)['state'] == 'off' and self.h.state()['owner'] is None)
        texts = self.h.texts()
        self.assertIn('Manual start Pump 7 · OUT: Forward rotation 40 mL/min,30 Automatically stop in seconds', texts)
        self.assertIn('manual stop Pump 7 · OUT', texts)
        actors = {row['text']: row['actor'] for row in self.h.app.store.events(limit=50)}
        self.assertEqual(actors['manual stop Pump 7 · OUT'], 'operator')
        # A blocking operation answers at once; its end is state.pump.last_result.
        code, reply = self.h.post('/api/manual', {'op': 'dose_volume', 'motor': 8, 'volume_ml': 0.5, 'direction': 1})
        self.assertEqual((code, reply['accepted'], sorted(reply)), (200, True, ['accepted', 'eta_s', 'op_id']))
        self.assertRegex(reply['op_id'], r'^[0-9a-f]{32}$')
        result = wait_until(lambda: self.h.state()['pump']['last_result'] and self.h.state()['pump']['op'] is None
                            and self.h.state()['pump']['last_result'])
        self.assertEqual(result['ok'], True)
        wait_until(lambda: self.h.state()['owner'] is None)
        # Wrong fields are 400 with the sentence for the screen; an unknown operation too.
        for body, text in (({'op': 'pump_on', 'motor': 1, 'flow_ml_min': 40, 'seconds': 5}, 'The pump number is incorrect'),
                           ({'op': 'pump_on', 'motor': 8, 'flow_ml_min': 400, 'seconds': 5}, 'traffic should be in 0.1–80 mL/min between'),
                           ({'op': 'pump_on', 'motor': 8, 'flow_ml_min': 40, 'seconds': 301}, 'The number of seconds for automatic stop should be within 1–300 integer between'),
                           ({'op': 'launch'}, 'Unsupported manual operation'), ({}, 'Unsupported manual operation')):
            self.assertEqual(self.h.post('/api/manual', body), (400, {'error': text, 'code': 'bad_request'}), body)
        code, reply = self.h.post('/api/manual', {'op': 'syringe_move', 'kind': 'aspirate', 'position': 1, 'volume_ml': 1})
        self.assertEqual((code, reply['code'], reply['error']), (409, 'refused', 'The syringe pump has not been initialized yet.'))

    def test_pumps_do_not_start_while_the_high_voltage_is_on(self):
        self.h.connected()
        self.h.sim.backend.run_stop = 1                           # as if somebody had switched the supply on
        self.h.app.rpi.command('status', fresh=True)
        wait_until(lambda: self.h.state()['rpi']['hv']['idle'] is False)
        manual = self.h.state()['interlock']['manual']
        self.assertEqual((manual['ok'], manual['reasons']), (False, [{'code': 'hv_on', 'text': 'high voltage power supply is ON', 'fix': 'Press ”Stop” at the bottom STOP”'}]))
        code, reply = self.h.post('/api/manual', {'op': 'pump_on', 'motor': 8, 'flow_ml_min': 40, 'direction': 1, 'seconds': 5})
        self.assertEqual((code, reply), (409, {'error': 'Cannot start pump now', 'code': 'blocked',
                                               'reasons': [{'code': 'hv_on', 'text': 'high voltage power supply is ON', 'fix': 'Press ”Stop” at the bottom STOP”'}]}))
        self.assertEqual(self.h.app.pump._conn.raw._sim.mstate['B'], 'idle')
        self.assertIsNone(self.h.state()['owner'])
        code, reply = self.h.post('/api/stop', {})                # ... and STOP switches it off again
        self.assertEqual((code, reply['rpi']['result']), (200, 'confirmed'))
        wait_until(lambda: self.h.state()['rpi']['hv']['idle'] is True)
        self.assertEqual(self.h.post('/api/manual', {'op': 'pump_on', 'motor': 8, 'flow_ml_min': 40, 'direction': 1, 'seconds': 1})[0], 200)

    def test_a_manual_pump_stops_when_the_screen_goes_away(self):
        """The page watchdog of 2.0.0 (1.4.1 had it for a discharge started from the page): /api/state is what feeds it."""
        self.h.connected()
        patch(self, pumpboard, 'UI_WATCHDOG_SECONDS', 1.5)
        self.assertEqual(self.h.post('/api/manual', {'op': 'pump_on', 'motor': 8, 'flow_ml_min': 40, 'direction': 1, 'seconds': 60})[0], 200)
        wait_until(lambda: self.motor(8)['state'] == 'run')
        end = time.monotonic() + 2.5
        while time.monotonic() < end:  # the screen keeps polling: nothing happens
            self.assertEqual(self.motor(8)['state'], 'run')
            time.sleep(.3)
        # ...then the browser is closed: no more /api/state requests.
        sim = self.h.app.pump._conn.raw._sim
        wait_until(lambda: sim.mstate['B'] == 'idle')
        alarm = wait_until(lambda: self.h.alarm('page_lost'))
        self.assertEqual(alarm['text'], 'The operation screen exceeds 1.5 No response for seconds, manually started pump stopped')
        wait_until(lambda: self.h.app.devices.owner is None)

    def test_pump_settings_and_the_scale_from_the_maintenance_pages(self):
        self.h.connected()
        self.h.unlock()
        ports = self.h.get('/api/pump/ports')
        self.assertEqual((ports['simulated'], ports['ports'], ports['other_count']), (True, [], 0))
        code, reply = self.h.post('/api/pump/config', {'enabled': True, 'device': 'COM91'})
        self.assertEqual((code, reply), (400, {'error': 'Simulation mode uses the built-in simulation control board', 'code': 'bad_request'}))
        before = self.h.state()['revs']['recipes']
        code, reply = self.h.post('/api/pump/settings', {'syringe_ul': 1250, 'syringe_speed_ml_s': 0.1})
        self.assertEqual((code, reply), (200, {'saved': True, 'settings': {'syringe_ul': 1250, 'syringe_speed_ml_s': 0.1}}))
        state = self.h.state()
        self.assertEqual((state['pump']['settings'], state['pump']['syringe']['size_ul']), ({'syringe_ul': 1250, 'syringe_speed_ml_s': 0.1}, 1250))
        self.assertGreater(state['revs']['recipes'], before)      # a Recipe may fit the new syringe or not: the list is read again
        actions = {row['action']: row for row in self.h.get('/api/bootstrap')['recipe']['actions']}
        self.assertEqual(actions['syringe_aspirate']['value']['max'], 1.25)
        for body, text in (({'syringe_ul': 2000, 'syringe_speed_ml_s': 0.1}, 'syringe capacity only supports 5 mL or 1.25 mL'),
                           ({'syringe_ul': 5000}, None), ({'syringe_ul': 5000, 'syringe_speed_ml_s': 99}, None)):
            code, reply = self.h.post('/api/pump/settings', body)
            self.assertEqual((code, reply['code']), (400, 'bad_request'), body)
            self.assertTrue(reply['error'] == text if text else reply['error'].startswith('The syringe pump speed should be at 0.001–'), reply)
        code, reply = self.h.post('/api/scale/tare', {})
        self.assertEqual((code, sorted(reply['calibration'])), (200, ['divisor', 'offset', 'reference_grams', 'saved_at']))
        for grams in (0, 0.05, 20001, '100', None, True):
            self.assertEqual(self.h.post('/api/scale/calibrate', {'grams': grams}),
                             (400, {'error': 'The weight of the weight should be 0.1–20000 g between', 'code': 'bad_request'}), grams)
        code, reply = self.h.post('/api/scale/calibrate', {'grams': 100})
        self.assertEqual((code, reply['calibration']['reference_grams']), (200, 100.0))
        wait_until(lambda: self.h.state()['pump']['scale']['reference_g'] == 100.0)
        self.assertEqual(self.h.post('/api/pump/reconnect', {}), (200, {'accepted': True}))
        wait_until(lambda: self.h.app.pump.connected and self.h.state()['pump']['generation'] >= 2)
        texts = self.h.texts()
        for text in ('Syringe pump settings: Syringe 1.25 mL, speed 0.1 mL/s', 'The scale has returned to zero', 'The scale has been calibrated (weight 100 g) ', 'The pump control board has been asked to rewire'):
            self.assertIn(text, texts)


class RecipeTests(Base):
    options = {'pump': 'sim'}

    def test_compose_publish_use_and_delete(self):
        """Maintenance › Recipe: the rows of the step editor -> check -> publish -> run it -> delete it."""
        self.h.connected()
        rows = [['delay', '', '0.2', 's', 'Wait a moment'], ['plasma_run', '', '', '', 'Measure']]
        self.assertEqual(self.h.post('/api/recipes/validate', {'rows': rows})[1]['code'], 'locked')
        self.h.unlock()
        code, checked = self.h.post('/api/recipes/validate', {'rows': rows})
        self.assertEqual((code, checked['valid'], checked['error'], checked['encoding'], checked['step_count'], checked['plasma_count']),
                         (200, True, '', 'utf-8', 2, 1))
        self.assertEqual(checked['csv'], 'action,target,value,unit,note\ndelay,,0.2,s,Wait a moment\nplasma_run,,,,Measure\n')
        self.assertEqual([(row['line'], row['action'], row['text'], row['ok']) for row in checked['rows']],
                         [(2, 'delay', 'Waiting 0.2 Second', True), (3, 'plasma_run', 'discharge＋capture spectrum', True)])
        self.assertGreater(checked['estimate_seconds'], 1)
        # The other two forms of the same request: the file's bytes, and the text.
        as_file = self.h.post('/api/recipes/validate', {'file_b64': base64.b64encode(('\ufeff' + checked['csv']).encode('utf-8')).decode()})[1]
        as_text = self.h.post('/api/recipes/validate', {'csv': checked['csv'].replace('\n', '\r\n')})[1]
        self.assertEqual((as_file, as_text), (checked, checked))
        big5 = self.h.post('/api/recipes/validate', {'file_b64': base64.b64encode(checked['csv'].encode('cp950')).decode()})[1]
        self.assertEqual((big5['valid'], big5['encoding'], len(big5['warnings'])), (True, 'cp950', 1))
        # A draft that is not valid is still 200: the page shows the row and the reason.
        code, bad = self.h.post('/api/recipes/validate', {'rows': rows + [['pump_on', 'M8', '40', 'mL/min', '']]})
        self.assertEqual((code, bad['valid'], bad['error']), (200, False, 'The pump is still running at the end of the process:Pump 7 · OUT (No. 4 column startup)'))
        code, empty = self.h.post('/api/recipes/validate', {'rows': []})
        self.assertEqual((code, empty['valid'], empty['error'], empty['rows']), (200, False, 'CSV There is no action that can be performed', []))
        unreadable = {'error': 'CSV cannot be read: please Excel Use ”Save as new file Liquid used in → CSV UTF-8 (comma separated)” and then import.', 'code': 'bad_request'}
        for body in ({'file_b64': 'not base64 !'}, {'file_b64': base64.b64encode(b'\x00\x01\x02').decode()}, {'csv': 12}):
            self.assertEqual(self.h.post('/api/recipes/validate', body), (400, unreadable), body)
        self.assertEqual(self.h.post('/api/recipes/validate', {'rows': [['delay', '', '1', 's']]}),
                         (400, {'error': 'The step data format is incorrect.', 'code': 'bad_request'}))
        self.assertEqual(self.h.post('/api/recipes/validate', {})[0], 400)
        self.assertEqual(self.h.post('/api/recipes/validate', {'csv': 'x', 'rows': []})[0], 400)
        # Publish.
        for body, text in (({'name': '', 'csv': checked['csv']}, 'Recipe The name must be 1–60 characters'),
                           ({'name': 'x' * 61, 'csv': checked['csv']}, 'Recipe The name must be 1–60 characters'),
                           ({'name': 'bad', 'csv': bad['csv']}, 'The pump is still running at the end of the process:Pump 7 · OUT (No. 4 column startup)'),
                           ({'name': 'Only discharge measurement', 'csv': checked['csv']}, 'already has the same name Recipe')):
            self.assertEqual(self.h.post('/api/recipes/publish', body), (400, {'error': text, 'code': 'bad_request'}), body['name'])
        revs = self.h.state()['revs']['recipes']
        code, made = self.h.post('/api/recipes/publish', {'name': ' Wait and measure again ', 'csv': checked['csv']})
        self.assertEqual((code, made['name'], made['sha256']), (200, 'Wait and measure again', hashlib.sha256(checked['csv'].encode()).hexdigest()))
        self.assertGreater(self.h.state()['revs']['recipes'], revs)
        self.assertEqual(self.h.state()['setup']['recipes'], 2)
        listed = self.h.get('/api/recipes')['recipes']
        self.assertEqual([(row['name'], row['source'], row['valid'], row['step_count'], row['uses_plasma'], row['uses_pump']) for row in listed],
                         [('Only discharge measurement', 'seed', True, 1, True, False), ('Wait and measure again', 'import', True, 2, True, False)])
        self.assertEqual(sorted(listed[1]), ['created_at', 'error', 'estimate_seconds', 'id', 'name', 'rounds', 'source', 'step_count',
                                             'uses_plasma', 'uses_pump', 'uses_rpi', 'valid'])
        detail = self.h.get('/api/recipes/' + made['id'])
        self.assertEqual((detail['csv'], detail['rows'], detail['plasma_count'], detail['delay_seconds'], len(detail['steps'])),
                         (checked['csv'], checked['rows'], 1, 0.2, 2))
        self.assertEqual([phase['name'] for phase in detail['phases']], [phase['name'] for phase in checked['phases']])
        body, headers = self.h.get(f'/api/recipes/{made["id"]}.csv', raw=True)
        self.assertEqual(body, ('\ufeff' + checked['csv']).encode('utf-8'))
        self.assertEqual((headers['Content-Disposition'], headers['Content-Type']),
                         (f'attachment; filename="recipe-{made["id"][:12]}.csv"', 'text/csv; charset=utf-8'))
        # Use it: one measurement with the new Recipe.
        code, started = self.h.start(count=2, interval=30, recipe_id=made['id'])
        self.assertEqual((code, started['schedule']['recipe']['name']), (200, 'Wait and measure again'))
        wait_until(lambda: self.h.view()['counts'].get('done') == 1 and self.h.view()['status'] == 'waiting')
        record = self.h.get('/api/rounds/' + self.h.view()['slots'][0]['round_id'])
        self.assertEqual(([step['action'] for step in record['steps']], {step['status'] for step in record['steps']}),
                         (['delay', 'plasma_run'], {'done'}))
        # Delete: the PIN again, not while a schedule uses it, and the file is only moved.
        self.assertEqual(self.h.post('/api/recipes/delete', {'id': made['id']}), (409, {'error': 'Maintenance PIN mistake', 'code': 'pin'}))
        self.assertEqual(self.h.post('/api/recipes/delete', {'id': made['id'], 'pin': '000000'})[0], 409)
        self.assertEqual(self.h.post('/api/recipes/delete', {'id': made['id'], 'pin': PIN}),
                         (409, {'error': 'this Recipe is being used in scheduling', 'code': 'state'}))
        self.h.post('/api/stop', {})
        self.h.finished()
        self.assertEqual(self.h.post('/api/recipes/delete', {'id': '0' * 32, 'pin': PIN}), (404, {'error': 'Recipe does not exist', 'code': 'not_found'}))
        self.assertEqual(self.h.post('/api/recipes/delete', {'id': made['id'], 'pin': PIN}), (200, {'deleted': True}))
        self.assertEqual([row['name'] for row in self.h.get('/api/recipes')['recipes']], ['Only discharge measurement'])
        self.assertEqual(self.h.get_code('/api/recipes/' + made['id'])[0], 404)
        self.assertEqual(self.h.get_code(f'/api/recipes/{made["id"]}.csv')[0], 404)
        kept = list((self.h.folder / 'recipes' / 'deleted').glob(made['id'] + '.deleted-*.json'))
        self.assertEqual(len(kept), 1)
        code, reply = self.h.start(recipe_id=made['id'])
        self.assertEqual((code, reply['error']), (400, 'Recipe does not exist'))
        actors = {row['text']: row['actor'] for row in self.h.app.store.events(limit=80)}
        self.assertEqual((actors['Published Recipe: Wait and measure again'], actors['Deleted Recipe: Wait and measure again']), ('Maintenance', 'Maintenance'))

    def test_a_recipe_whose_file_was_changed_is_shown_and_never_runs(self):
        self.h.connected()
        self.h.unlock()
        made = self.h.post('/api/recipes/publish', {'name': 'been changed', 'csv': ct.PLASMA_ONLY})[1]
        path = self.h.folder / 'recipes' / (made['id'] + '.json')
        record = json.loads(path.read_text(encoding='utf-8'))
        record['csv'] = ct.csv('delay,,5,s,', 'plasma_run,,,,')    # somebody edited the file by hand
        path.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        changed = 'Recipe file is different from when published, please re-publish.'
        row = next(item for item in self.h.get('/api/recipes')['recipes'] if item['id'] == made['id'])
        self.assertEqual((row['valid'], row['error'], row['estimate_seconds']), (False, changed, None))
        detail = self.h.get('/api/recipes/' + made['id'])
        self.assertEqual((detail['valid'], detail['error'], detail['csv'], detail['steps'], detail['rows']), (False, changed, '', [], []))
        self.assertEqual(self.h.get_code(f'/api/recipes/{made["id"]}.csv'), (409, {'error': changed, 'code': 'state'}))
        self.assertEqual(self.h.start(recipe_id=made['id']), (400, {'error': changed, 'code': 'bad_request'}))
        self.assertEqual(self.h.post('/api/recipes/delete', {'id': made['id'], 'pin': PIN}), (200, {'deleted': True}))

    def test_a_recipe_with_pump_steps_runs_on_both_simulators(self):
        self.h.connected()
        self.h.unlock()
        text = ct.csv('dose_volume,M8,0.3,mL,discharge', 'delay,,0.2,s,', 'plasma_run,,,,Measure')
        made = self.h.post('/api/recipes/publish', {'name': 'Measure after discharge', 'csv': text})[1]
        plan = self.h.post('/api/schedule/preview', self.h.body(recipe_id=made['id'], confirm=False))[1]
        self.assertEqual((plan['recipe']['uses_pump'], [plan['needs'][key] for key in ('high_voltage', 'liquid', 'hv_off_checked')],
                          plan['blockers']), (True, [True, True, False], []))
        code, reply = self.h.post('/api/schedule/start', dict(self.h.body(recipe_id=made['id'], confirm=False), confirm={'high_voltage': True}))
        self.assertEqual((code, reply['error']), (400, 'You must first check each item in the confirmation window'))        # the liquid tick is missing
        self.assertEqual(self.h.start(recipe_id=made['id'])[0], 200)
        done = self.h.finished()
        self.assertEqual((done['status'], done['counts'], done['uses_pump']), ('completed', {'done': 1}, True))
        record = self.h.get('/api/rounds/' + done['slots'][0]['round_id'])
        self.assertEqual([(step['action'], step['status']) for step in record['steps']],
                         [('dose_volume', 'done'), ('delay', 'done'), ('plasma_run', 'done')])
        self.assertEqual(record['board']['simulated'], True)


class CalibrationTests(Base):
    """Correction → Apply → concentration (AMENDMENTS M5): the routes of M5.3 on the two simulators.

    The pretend sample of the RPi simulator is changed by the test at every bottle-swap prompt — the test is the
    person who puts the next standard under the tube. (In practice mode the program does that itself, M11.)"""
    options = {'speed': 100}

    CONFIRM = {'high_voltage': True, 'liquid': True, 'hv_off_checked': False}

    def plan(self):
        return {'recipe_id': self.h.seed, 'prime_recipe_id': '', 'discard_rounds': 1,
                'levels': [{'name': 'Blank', 'conc': {'Cu': 0, 'Zn': 0, 'Ni': 0}, 'rounds': 1},
                           {'name': 'STD-1', 'conc': {'Cu': 1, 'Zn': 1, 'Ni': 1}, 'rounds': 1}]}

    def prompt(self, level):
        return wait_until(lambda: (lambda run: run if run and run['prompt'] and run['level'] == level else None)(
            self.h.state()['calibration_run']), 30)

    def test_a_two_level_calibration_from_the_pages(self):
        self.h.connected()
        # plasma parameters first: at 400 V the lines of the pretend sample stand well above its noise.
        self.h.unlock()
        self.assertEqual(self.h.post('/api/parameters', {'parameters': dict(GOOD, Loopnum=8, Voltage=400)})[0], 200)
        wait_until(lambda: (self.h.state()['rpi']['parameters']['values'] or {}).get('Voltage') == 400)
        self.assertEqual(self.h.post('/api/lock', {}), (200, {'locked': True}))
        self.h.session = ''                                       # the rest is done by the operator, without the PIN
        plan = self.plan()
        # Calibration setting: the page saves the draft and asks what it would do.
        shown = self.h.get('/api/calibration')
        self.assertEqual(sorted(shown), ['active', 'coefficients', 'metals', 'plan', 'recipes', 'records', 'run', 'settings', 'unit'])
        self.assertEqual((shown['records'], shown['active'], shown['run'], shown['unit'], shown['metals'], shown['settings']['error']),
                         ([], None, None, 'mg/L', ['Cu', 'Zn', 'Ni'], ''))
        self.assertEqual(shown['recipes'], [{'id': self.h.seed, 'name': 'Only discharge measurement', 'plasma_count': 1, 'uses_rpi': True, 'valid': True}])
        self.assertTrue(shown['settings']['method_id'])
        code, saved = self.h.post('/api/calibration/plan', {'plan': plan})
        self.assertEqual((code, saved['saved'], saved['valid']), (200, True, True))
        self.assertEqual(self.h.get('/api/calibration')['plan'], plan)
        code, check = self.h.post('/api/calibration/check', {'plan': plan})
        self.assertEqual((code, check['recipe_name'], check['runs'], check['kept_runs'], check['prime_recipe_name']),
                         (200, 'Only discharge measurement', 4, 2, None))
        self.assertNotIn('steps', check)
        self.assertEqual(self.h.post('/api/calibration/check', {'plan': dict(plan, recipe_id='')}),
                         (400, {'error': 'Please select the standard product Recipe', 'code': 'bad_request'}))
        self.assertEqual(self.h.post('/api/calibration/check', {'plan': dict(plan, recipe_id='0' * 32)}),
                         (400, {'error': 'Recipe does not exist', 'code': 'bad_request'}))
        # Start calibration: nothing waits yet; the ticks are needed; then the first bottle.
        self.assertEqual(self.h.post('/api/calibration/confirm', {}), (409, {'error': 'There are currently no fluid changes waiting for confirmation.', 'code': 'state'}))
        code, reply = self.h.post('/api/calibration/start', {'plan': plan, 'confirm': {}})
        self.assertEqual((code, reply['error']), (400, 'You must first check each item in the confirmation window'))
        code, started = self.h.post('/api/calibration/start', {'plan': plan, 'confirm': self.CONFIRM})
        self.assertEqual((code, started['accepted'], started['schedule']['kind']), (200, True, 'calibration'))
        cal_id = started['calibration_id']
        self.assertRegex(cal_id, r'^CAL-[0-9a-f]{32}$')
        self.assertEqual(self.h.post('/api/calibration/start', {'plan': plan, 'confirm': self.CONFIRM}),
                         (409, {'error': 'Already has a schedule in progress', 'code': 'state'}))
        for level, name, sample in ((0, 'Blank', {'Cu': 0, 'Zn': 0, 'Ni': 0}), (1, 'STD-1', {'Cu': 1, 'Zn': 1, 'Ni': 1})):
            run = self.prompt(level)
            self.assertEqual((run['id'], run['status'], run['recipe_name'], run['level_name'], run['runs']),
                             (cal_id, 'running', 'Only discharge measurement', name, 4))
            self.assertIn(f'Please replace with standard product {name}', run['prompt']['text'])
            state = self.h.state()
            self.assertEqual((state['round']['mode'], state['round']['prompt'], state['schedule']['kind'], state['schedule']['calibration_id']),
                             ('calibration', run['prompt'], 'calibration', cal_id))
            self.assertEqual(self.h.get('/api/calibration')['run'], state['calibration_run'])
            self.h.sim.backend.sim_sample(sample)                 # the next bottle is under the tube
            self.assertEqual(self.h.post('/api/calibration/confirm', {}), (200, {'accepted': True}))
        done = self.h.finished(60)
        self.assertEqual((done['status'], done['kind'], done['counts']), ('completed', 'calibration', {'done': 1}))
        self.assertIsNone(self.h.state()['calibration_run'])
        self.assertIn('Standard product calibration starts: only discharge measurement,2 concentration,common 4 wheel (high pressure confirmed)', self.h.texts())
        # results and records: analysed; the full record; exclusion and re-analysis.
        record = wait_until(lambda: (lambda r: r if r['status'] == 'analyzed' else None)(self.h.get('/api/calibrations/' + cal_id)))
        self.assertEqual(([run['level'] for run in record['runs']], [run['discard'] for run in record['runs']]),
                         ([0, 0, 1, 1], [True, False, True, False]))
        fits = record['analysis']['fits']
        self.assertEqual({metal: fits[metal]['valid'] for metal in ('Cu', 'Zn', 'Ni')}, {'Cu': True, 'Zn': True, 'Ni': True})
        listed = self.h.get('/api/calibration')['records']
        self.assertEqual((len(listed), listed[0]['id'], listed[0]['status']), (1, cal_id, 'analyzed'))
        self.assertEqual(self.h.get('/api/measurements')['rows'], [])           # a standard is not a measurement
        code, reply = self.h.post('/api/calibration/exclude', {'id': cal_id, 'exclude': {}})
        self.assertEqual((code, reply['accepted'], sorted(reply)), (200, True, ['accepted', 'exclude_levels']))
        self.assertEqual(self.h.post('/api/calibration/analyze', {'id': cal_id}), (200, {'accepted': True, 'id': cal_id}))
        wait_until(lambda: self.h.get('/api/calibrations/' + cal_id)['status'] == 'analyzed')
        missing = (404, {'error': 'Calibration record does not exist', 'code': 'not_found'})
        for path in ('/api/calibration/analyze', '/api/calibration/exclude'):
            self.assertEqual(self.h.post(path, {'id': 'CAL-' + '0' * 32, 'exclude': {}}), missing, path)
            self.assertEqual(self.h.post(path, {'id': 'nonsense', 'exclude': {}})[0], 400, path)
        # application coefficient needs the maintenance session.
        self.assertEqual(self.h.post('/api/calibration/apply', {'id': cal_id})[1]['code'], 'locked')
        self.assertIsNone(self.h.state()['calibration_active'])
        self.h.unlock()
        self.assertEqual(self.h.post('/api/calibration/apply', {'id': 'CAL-' + '0' * 32}), missing)
        revs = self.h.state()['revs']
        code, applied = self.h.post('/api/calibration/apply', {'id': cal_id})
        self.assertEqual((code, applied['applied'], sorted(applied)), (200, ['Cu', 'Zn', 'Ni'], ['active', 'applied', 'config']))
        self.assertEqual(applied['config']['Cu']['c'], '1')
        self.assertNotIn('reference', applied['active'])
        state = self.h.state()
        self.assertEqual((state['calibration_active']['calibration_id'], state['calibration_active']['metals'],
                          sorted(state['calibration_active'])), (cal_id, ['Cu', 'Zn', 'Ni'], ['applied_at', 'calibration_id', 'metals']))
        self.assertGreater(state['revs']['calibrations'], revs['calibrations'])
        self.assertGreater(state['revs']['measurements'], revs['measurements'])
        # A later sample has concentrations: in the state, in the run log and in /api/measurements.
        self.h.sim.backend.sim_sample({'Cu': 0.5, 'Zn': 0.5, 'Ni': 0.5})
        self.assertEqual(self.h.start()[0], 200)
        self.assertEqual(self.h.finished()['status'], 'completed')
        rows = self.h.get('/api/measurements?hours=24')
        self.assertEqual((len(rows['rows']), rows['rows'][0]['kind'], rows['rows'][0]['valid'], rows['rows'][0]['conc_reason'],
                          rows['calibration']['calibration_id']), (1, 'sample', True, '', cal_id))
        for metal in ('Cu', 'Zn', 'Ni'):
            self.assertAlmostEqual(rows['rows'][0]['metals'][metal]['conc'], 0.5, delta=0.1, msg=metal)
        sample = self.h.state()['measurement']['latest_sample']
        self.assertEqual((sample['valid'], sample['conc_reason'], sample['metals']['Cu']['conc']),
                         (True, '', rows['rows'][0]['metals']['Cu']['conc']))
        self.assertRegex(self.h.get('/api/runlog?limit=1')['rows'][0]['result_text'], r'^Cu [\d.]+ · Zn [\d.]+ · Ni [\d.]+ mg/L$')
        self.assertTrue(any(text.startswith('sample concentration:Cu ') for text in self.h.texts()))
        # A blank after it is compared with the calibration's blank.
        blank_id = self.h.post('/api/recipes/publish', {'name': 'blank measurement', 'csv': ct.PLASMA_ONLY})[1]['id']
        self.h.sim.backend.sim_sample({'Cu': 0, 'Zn': 0, 'Ni': 0})
        plan = self.h.post('/api/schedule/preview', self.h.body(confirm=False, blank={'recipe_id': blank_id, 'every': 4}))[1]
        self.assertEqual(plan['blank'], {'recipe_id': blank_id, 'recipe_name': 'blank measurement', 'every': 4})
        self.assertEqual(self.h.start(blank={'recipe_id': blank_id, 'every': 4})[0], 200)
        done = self.h.finished()
        self.assertEqual((done['slots'][0]['kind'], done['blank']['recipe_name']), ('blank', 'blank measurement'))
        blank = self.h.state()['measurement']['latest_blank']
        self.assertEqual(sorted(blank), ['id', 'qc', 'time'])
        self.assertIn(blank['qc']['status'], ('ok', 'fail'))
        self.assertEqual(sorted(blank['qc']), ['problems', 'reason', 'status'])
        # The coefficients, the blank thresholds and the blank zero by hand: each needs the PIN and is checked.
        config = {metal: {'a': '2', 'b': '0.5', 'c': '1'} for metal in ('Cu', 'Zn', 'Ni')}
        self.assertEqual(self.h.post('/api/calibration-config', {'config': config})[1], {'config': config})
        self.assertEqual(self.h.get('/api/calibration')['coefficients'], config)
        self.assertEqual(self.h.post('/api/calibration-config', {'config': {'Cu': {'a': 'x'}}})[0], 400)
        qc = self.h.get('/api/measurements')['qc']
        code, reply = self.h.post('/api/blank-qc', {'qc': dict(qc, max_shift_nm=0.1)})
        self.assertEqual((code, reply['qc']['max_shift_nm']), (200, 0.1))
        self.assertEqual(self.h.post('/api/blank-qc', {'qc': dict(qc, max_shift_nm=5)})[0], 400)
        self.assertEqual(self.h.post('/api/blank-qc', {})[0], 400)
        zero = self.h.get('/api/measurements')['blank_zero']['settings']
        code, reply = self.h.post('/api/blank-zero', {'settings': dict(zero, window=5)})
        self.assertEqual((code, reply['settings']['window'], sorted(reply)), (200, 5, ['settings', 'status']))
        self.assertEqual(self.h.post('/api/blank-zero', {'settings': dict(zero, window=2)})[0], 400)
        self.assertEqual(self.h.post('/api/blank-zero', {}), (400, {'error': 'Blank zero point setting format is incorrect', 'code': 'bad_request'}))
        actors = {row['text']: row['actor'] for row in self.h.app.store.events(limit=200)}
        for text in ('The correction coefficient has been stored', 'Blank threshold has been stored', 'Blank zero setting has been stored', f'Correction applied {cal_id[:16]}: Cu, Zn, Ni'):
            self.assertEqual(actors[text], 'Maintenance', text)

    def test_stop_ends_a_calibration_that_waits_for_a_bottle(self):
        self.h.connected()
        code, started = self.h.post('/api/calibration/start', {'plan': self.plan(), 'confirm': self.CONFIRM})
        self.assertEqual(code, 200)
        self.prompt(0)
        self.assertEqual(self.h.state()['owner']['kind'], 'round')
        code, reply = self.h.post('/api/stop', {})
        self.assertEqual((code, reply['schedule_ended'], reply['round_stopping']), (200, True, True))
        done = self.h.finished()
        self.assertEqual((done['status'], done['kind']), ('stopped', 'calibration'))
        self.assertEqual(self.h.get('/api/calibrations/' + started['calibration_id'])['status'], 'stopped')
        self.assertEqual(self.h.post('/api/calibration/confirm', {}), (409, {'error': 'There are currently no fluid changes waiting for confirmation.', 'code': 'state'}))
        self.assertIsNone(self.h.sim.controller.job)              # nothing was discharged


if __name__ == '__main__':
    unittest.main(verbosity=2)
