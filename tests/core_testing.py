"""2.0.0 core (pc/devices.py, steps.py, runner.py, manual.py) Test common tools.

Rig Connect the entire set of things in the temporary folder according to the order of program startup (SPEC B.11) : 

    Store (data upgrade)→ Alarms → RpiLink (connected to test_tcpd.SimServer: local machine 127.0.0.1 on leave RPi) 
    → PumpBoard (V7 own simulator, shortened time, equipped with tripwire)→ Devices (Leave the high-pressure level to the pump control board)
    → PumpBoard.start() → Analyst → Runner → ManualOps
    → Devices.bind() (Background Check devices-watch Start here;Rig.close() The first thing is to use Devices.close() end it)

Everything here is an emulator: it will not open or list any ports of this computer (pump_testing Set up as soon as you import it
PLASMIC_NO_SERIAL=1) ,only connect 127.0.0.1,Can’t read or write pc\\data.Each test module setUpModule/
tearDownModule call here set_up()/tear_down(): The tripwire record must be empty when it ends. You need to enter it to enter the
"""
from __future__ import annotations

import contextlib
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
for _folder in (ROOT / 'tests', ROOT / 'pc', ROOT / 'rpi' / 'tcpd'):
    if str(_folder) in sys.path:
        sys.path.remove(str(_folder))
    sys.path.insert(0, str(_folder))

import pump_testing as pt  # noqa: E402  (sets PLASMIC_NO_SERIAL=1 before anything else is loaded)
import alarms as alarms_module  # noqa: E402
import analyst as analyst_module  # noqa: E402
import devices  # noqa: E402
import manual  # noqa: E402
import netcheck  # noqa: E402
import pumpboard  # noqa: E402
import recipe  # noqa: E402
import rpilink  # noqa: E402
import runner  # noqa: E402
import schedule  # noqa: E402
import store as store_module  # noqa: E402
from test_tcpd import SimServer  # noqa: E402  (import only: that file is never run or edited from here)

HEADER = 'action,target,value,unit,note\n'
PLASMA_ONLY = HEADER + 'plasma_run,,,,discharge＋capture spectrum\n'
SEED_NAME = 'Only discharge measurement'
OPERATOR = 'operator'
LONGEST_TEST_SECONDS = 30.0
# Short times for the pump simulator. A Recipe's waits are real seconds, so the simulator is not sped up
# here: a pump that a Recipe starts must still be turning when its pump_off comes.
PUMP = dict(pt.FAST, **{'link.simulation_speedup': 1})
_saved = {}


def set_up():
    """setUpModule of every test module that uses a Rig."""
    pt.install_tripwire()
    _saved.update(local_ipv4=netcheck.local_ipv4, site=store_module.SITE, minute=schedule.MINUTE,
                  poll=rpilink.POLL_SECONDS)
    netcheck.local_ipv4 = lambda: []                 # no network card to look around on
    store_module.SITE = ROOT / 'tests' / 'no-such-site.json'
    schedule.MINUTE = 1                              # a "minute" of a schedule is one second, as in the 1.4.1 tests
    rpilink.POLL_SECONDS = 0.1                       # the pretend RPi is asked ten times a second


def tear_down():
    netcheck.local_ipv4, store_module.SITE = _saved['local_ipv4'], _saved['site']
    schedule.MINUTE, rpilink.POLL_SECONDS = _saved['minute'], _saved['poll']
    assert pumpboard.PumpBoard._live is None, 'a test left a pump board running'
    limit = time.perf_counter() + 5.0    # (Devices.close() gives its watch 2 s; one that was just acting may need a moment more)
    while [thread for thread in threading.enumerate() if thread.name == 'devices-watch'] and time.perf_counter() < limit:
        time.sleep(0.05)
    assert not [thread for thread in threading.enumerate() if thread.name == 'devices-watch'], 'a test left a watch running'
    assert pumpboard._real_ports_allowed is False
    assert not [item for item in pt.VENDOR.rglob('__pycache__')], 'bytecode was written into pc/vendor'
    pt.assert_nothing_opened()


def wait_for(predicate, timeout=10.0, what='the expected state'):
    limit = time.perf_counter() + timeout
    while time.perf_counter() < limit:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError('timed out after %.1f s waiting for %s' % (timeout, what() if callable(what) else what))


def free_port():
    """A local port number nothing listens on (for "the RPi is not there")."""
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def csv(*rows):
    """A Recipe from rows such as 'delay,,0.3,s,' (the header is added)."""
    return HEADER + ''.join(row.rstrip('\n') + '\n' for row in rows)


class Rig:
    """Everything of the core on the two simulators, in a temp folder."""

    def __init__(self, pump='sim', rpi=True, speed=40, analyst=True, operator=OPERATOR, calibration=True,
                 hook=None, auto_sync=True, sim=None, **dotted):
        self.temp = tempfile.TemporaryDirectory(prefix='plasmic-core-')
        self.data = Path(self.temp.name) / 'data'
        self.data.mkdir()
        self.sim = None
        self.sim_closed = False
        if rpi:
            self.sim = SimServer(speed, **(sim or {}))   # sim: settings of the pretend RPi, e.g. {'flow': 'verified'}
            self.sim.controller.audit = lambda *args, **kwargs: None     # keeps the test output readable
        self.port = self.sim.port if rpi else free_port()
        (self.data / 'config.json').write_text(
            json.dumps({'rpi_host': '127.0.0.1', 'rpi_port': self.port, 'token': ''}), encoding='utf-8')
        self.operator = operator
        self.page_age = 0.0              # what the page watchdog of the pump board is told
        self.hook_calls = []
        self.closed = False
        self.pump = self.rpi = self.runner = self.devices = None
        try:
            self.store = store_module.Store(self.data)
            self.alarms = alarms_module.Alarms(self.store)
            self.rpi = rpilink.RpiLink(self.store, search=False, auto_sync=auto_sync)
            self.rpi.on_alarm = self._alarm
            if not calibration:
                (self.data / 'pumpboard' / 'scale_calibration.json').unlink()
            values = dict(PUMP)
            values.update({name.replace('__', '.'): value for name, value in dotted.items()})
            self.pump = pumpboard.PumpBoard(self.data, pump, self.store, None, self._alarm, lambda: self.page_age,
                                            overrides=pt.overrides_from(values))
            self.devices = devices.Devices(self.store, self.alarms, self.rpi, self.pump)
            self.pump.start()
            if pump == 'sim':
                wait_for(lambda: self.pump.connected, 8, 'the simulated pump board to connect')
                wait_for(lambda: self.pump.view()['scale']['online'], 5, 'the first reading of the scale')
            if rpi:
                wait_for(self.rpi.latest, 8, 'the simulated RPi to answer')
            self.analyst = None
            if analyst:
                self.analyst = analyst_module.Analyst(self.data, self.store.load_run, self.store.write_json,
                                                      on_event=lambda text, level='info': self.store.event(text, level))
            self.hook = hook
            self.runner = runner.Runner(self.store, self.alarms, self.devices, self.rpi, self.pump, lambda: self.operator,
                                        analyst=self.analyst,
                                        before_discharge=self._before_discharge if hook is not None else None)
            self.manual = manual.ManualOps(self.store, self.devices, self.pump, lambda: self.operator)
            self.devices.bind(self.runner, self.manual)
        except BaseException:
            self.close()                 # never leave a live board behind: the next test could not start one
            raise

    # -- wiring ---------------------------------------------------------------------------------------
    def _alarm(self, action, key, priority=None, area=None, text=None, kind=None):
        """What Application does with the alarms of the two links: straight into the alarm list."""
        if action == 'raise':
            self.alarms.raise_(key, priority, area, text, kind or 'event')
        else:
            self.alarms.clear(key)

    def _before_discharge(self, info):
        self.hook_calls.append(dict(info))
        return self.hook(info)

    # -- Recipes and schedules --------------------------------------------------------------------------
    @property
    def seed(self):
        """The id of “Discharge measurement only”, which every new data folder holds."""
        return next(row['id'] for row in self.store.recipes() if row['name'] == SEED_NAME)

    def publish(self, name, text):
        """Publish a Recipe as Maintenance › Recipe does; returns its id."""
        checked = recipe.validate(text, self.pump.hardware(), self.pump.view()['settings']['syringe_ul'])
        return self.store.publish_recipe(name, text, checked, 'import')['id']

    def body(self, recipe_id=None, count=1, interval=2, confirm=None, blank=None, **more):
        """A schedule body: start now, every `interval` "minutes" (seconds here), `count` times."""
        body = {'recipe_id': recipe_id or self.seed,
                'schedule': {'start': {'mode': 'now'}, 'interval_minutes': interval,
                             'end': {'mode': 'count', 'count': count}},
                'confirm': {'high_voltage': True, 'liquid': True, 'hv_off_checked': False} if confirm is None else confirm}
        if blank is not None:
            body['blank'] = blank
        body.update(more)
        return body

    def start(self, recipe_id=None, **options):
        return self.runner.start(self.body(recipe_id, **options))

    def schedule(self):
        return self.runner.view()        # not through a page: nobody is watching

    def finished(self, timeout=20.0):
        """Wait until the schedule has ended; returns it."""
        wait_for(lambda: not self.runner.active, timeout,
                 lambda: 'the schedule to end (it is %s)' % (self.runner.view() or {}).get('status'))
        thread = self.runner.thread
        if thread is not None:
            thread.join(10)
        return self.runner.view()

    def wait_status(self, *statuses, timeout=10.0):
        return wait_for(lambda: (self.runner.view() or {}).get('status') in statuses and self.runner.view(), timeout,
                        lambda: 'the schedule to be %s (it is %s)' % ('/'.join(statuses), (self.runner.view() or {}).get('status')))

    def wait_step(self, action, status='running', timeout=10.0):
        """Wait until a step of this action of the running round has this status; returns the step."""
        def found():
            record = self.runner.round
            if record is None:
                return None
            return next((dict(step) for step in record['steps']
                         if step['action'] == action and step['status'] == status), None)
        return wait_for(found, timeout, lambda: 'a %s step to be %s (%s)' % (action, status, self.step_states()))

    def step_states(self):
        record = self.runner.round
        return [(step['action'], step['status']) for step in record['steps']] if record else []

    def round(self, round_id=None):
        """A round record as it is on disk (the last one by default)."""
        if round_id is None:
            round_id = self.runner.round['id']
        return self.store.round(round_id)

    # -- looking --------------------------------------------------------------------------------------
    def texts(self, limit=400):
        """The event log, oldest first."""
        return [row['text'] for row in self.store.events(limit=limit)][::-1]

    def said(self, part):
        return any(part in text for text in self.texts())

    def alarm_keys(self):
        return [row['key'] for row in self.alarms.view()['active']]

    def alarm(self, key):
        return next((row for row in self.alarms.view()['active'] if row['key'].startswith(key)), None)

    @property
    def board(self):
        """V7's simulator behind the present connection of the pump board."""
        return self.pump._conn.raw._sim

    def tx(self):
        """Every command written to the pump board so far."""
        self.pump._flush_serial_log()
        path = self.data / 'logs' / 'pumpboard.log'
        rows = []
        if path.is_file():
            for line in path.read_text(encoding='utf-8').splitlines():
                parts = line.split(' ', 2)
                if len(parts) == 3 and parts[1] == 'tx':
                    rows.append(parts[2])
        return rows

    def pumps_off(self):
        """Does the pump simulator itself say that nothing moves and every valve is closed?"""
        if self.pump is None or self.pump.mode != 'sim' or self.pump._conn is None:
            return True
        sim = self.board
        return not sim.cleaning and all(state == 'idle' for state in sim.mstate.values()) and not any(sim.valves.values())

    def hv_off(self):
        """Does the RPi simulator itself say that the supply is off?"""
        return self.sim is None or self.sim.backend.run_stop == 0

    def sim_requests(self):
        """Every command the pretend RPi is asked from now on: [cmd]."""
        seen = []
        real = self.sim.controller.handle

        def handle(connection, request):
            seen.append(str(request.get('cmd')).lower())
            return real(connection, request)

        self.sim.controller.handle = handle
        return seen

    def fresh(self):
        """Have the pretend RPi read its pretend equipment now, and wait until this side has that reading."""
        self.rpi.command('status', fresh=True)
        seen = self.rpi.status_mono
        wait_for(lambda: self.rpi.status_mono != seen, 5, 'a new status of the RPi')

    def supply(self, on):
        """Switch the pretend high-voltage supply by hand (as if somebody else had done it)."""
        self.sim.backend.run_stop = 1 if on else 0
        self.fresh()
        wait_for(lambda: self.rpi.hv_view()['idle'] is (not on), 5, 'the supply to read %s' % ('ON' if on else 'OFF'))

    def supply_answers(self, answers, wait=True):
        """The pretend high-voltage supply stops answering on RS485 — as the real one does when it is switched
        off at its mains switch — or answers again. While it is silent the pretend RPi reports what the real
        tools report: a power read-back that is not valid (read_status), and a stop that cannot be confirmed
        (plasma_job.py stop: “HSPY OFF readback failed”). ``wait`` False: do not wait until this side has seen it
        (while a discharge runs the RPi reads nothing)."""
        backend = self.sim.backend
        if answers:
            for name in ('_power', 'stop'):
                backend.__dict__.pop(name, None)
        else:
            def silent():
                return {'valid': False, 'error': 'HSPY No response (simulation)'}

            def stop():
                raise RuntimeError('HSPY OFF (Try again 3 times):HSPY No response (simulation);HSPY OFF readback failed')

            backend._power, backend.stop = silent, stop
        if wait:
            self.fresh()
            wait_for(lambda: self.rpi.hv_view()['known'] is bool(answers), 5,
                     'the supply to read as %s' % ('answering' if answers else 'not answering'))

    def nano_answers(self, answers):
        """The pretend Nano stops answering on RS485 (its read-back is not valid), or answers again."""
        backend = self.sim.backend
        if answers:
            backend.__dict__.pop('_nano', None)
        else:
            backend._nano = lambda: {'valid': False, 'error': 'Nano No response (simulation)'}
        self.fresh()

    # -- breaking things --------------------------------------------------------------------------------
    def cut_rpi(self):
        """The cable to the RPi is pulled: this side's connection ends, the pretend RPi goes on by itself."""
        client = self.rpi.client
        if client is not None:
            client.close()

    def rpi_gone(self):
        """The RPi is switched off: nothing answers on its address any more."""
        if self.sim is not None and not self.sim_closed:
            self.sim_closed = True
            self.sim.close()
        wait_for(lambda: not self.rpi.connected, 8, 'the link to the RPi to notice')

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.devices is not None:
                self.devices.close()     # the watch ends first: nothing is judged while the rest is taken down
            if self.runner is not None:
                self.runner.close()
            if self.pump is not None:
                self.pump.close()
        finally:
            try:
                if self.rpi is not None:
                    self.rpi.close()
                if self.sim is not None and not self.sim_closed:
                    self.sim_closed = True
                    self.sim.close()
            finally:
                self.temp.cleanup()


class Case(unittest.TestCase):
    """Every test: at its end the pump simulator shows nothing moving and every valve closed, the RPi
    simulator shows the supply off, and it has not taken longer than 30 s."""

    def setUp(self):
        self._began = time.perf_counter()
        self._rigs = []
        self.addCleanup(self._too_long)

    def _too_long(self):
        took = time.perf_counter() - self._began
        self.assertLess(took, LONGEST_TEST_SECONDS, 'this test took %.1f s' % took)

    def rig(self, check_off=True, **options):
        rig = Rig(**options)
        self.addCleanup(rig.close)
        if check_off:
            self.addCleanup(self._all_off, rig)      # runs before the rig is closed
        return rig

    @contextlib.contextmanager
    def one(self, **options):
        """A rig for one part of a test (only one pump board can live at a time): checked and closed at the end."""
        rig = Rig(**options)
        try:
            yield rig
            self._all_off(rig)
        finally:
            rig.close()

    def _all_off(self, rig):
        wait_for(rig.pumps_off, 5, 'the pump simulator to show everything stopped at the end of the test')
        wait_for(rig.hv_off, 5, 'the RPi simulator to show the supply off at the end of the test')

    def patch(self, owner, name, value):
        original = getattr(owner, name)
        setattr(owner, name, value)
        self.addCleanup(setattr, owner, name, original)

    def plan(self):
        plan = pt.FaultPlan()
        self.addCleanup(plan.restore)
        return plan

    def pump_patched(self, **values):
        context = pt.patched(**values)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def background(self, call, *args, **kwargs):
        """`call` on a thread of its own. The box gets 'result' or 'error' (the exception)."""
        box = {}

        def work():
            try:
                box['result'] = call(*args, **kwargs)
            except BaseException as exc:
                box['error'] = exc

        box['thread'] = threading.Thread(target=work, daemon=True)
        box['thread'].start()
        return box

    def joined(self, box, timeout=15.0):
        box['thread'].join(timeout)
        self.assertFalse(box['thread'].is_alive(), 'the call did not come back')
        return box
