"""2.0.0 of RPi connection (pc/rpilink.py) Automated testing: RpiLink ↔ TCP ↔ analog device.

    python tests/test_rpilink.py

RPi That end is tests/test_tcpd.py of SimServer (local machine 127.0.0.1 , simulated The program on HSPY/Nano/spectrometer) ; 
The ”discharge” here is all the simulator calculating time. It does not touch any real equipment or connect 127.0.0.1 places other than.
"""
from __future__ import annotations

import inspect
import json
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'rpi' / 'tcpd'), str(ROOT / 'pc'), str(ROOT / 'tests')]

import netcheck  # noqa: E402
import plasmic_tcpd as tcpd  # noqa: E402
import rpilink  # noqa: E402
import store  # noqa: E402
from plasmic_client import PlasmicError  # noqa: E402
from test_tcpd import SimServer, wait_until  # noqa: E402

SEED_CSV = 'action,target,value,unit,note\nplasma_run,,,,discharge＋capture spectrum\n'
GOOD = {'Ontime': 25.0, 'Offtime': 5, 'Cycle': 25, 'Loopnum': 8,
        'Integration_Time': 1000, 'Spectra_Interval': 1500, 'Voltage': 100}
IDLE = {'known': True, 'idle': True, 'reasons': []}
_module = {}


def setUpModule():
    """Nothing here may reach the real network: no network cards to broadcast on, no site.json, and
    the one example Recipe the data folder needs comes from a folder of our own."""
    folder = tempfile.TemporaryDirectory()
    Path(folder.name, 'plasma-only.csv').write_bytes(SEED_CSV.encode('utf-8'))
    _module.update(folder=folder, recipes=store.RECIPES, site=store.SITE, local_ipv4=netcheck.local_ipv4)
    store.RECIPES = Path(folder.name)
    store.SITE = Path(folder.name) / 'no-site.json'
    netcheck.local_ipv4 = lambda: []


def tearDownModule():
    store.RECIPES, store.SITE, netcheck.local_ipv4 = _module['recipes'], _module['site'], _module['local_ipv4']
    _module['folder'].cleanup()


def patch(case, owner, name, value):
    original = getattr(owner, name)
    setattr(owner, name, value)
    case.addCleanup(setattr, owner, name, original)


class Rig:
    """A simulated RPi on a free local port, a data folder in a temp place, and one RpiLink to it."""

    def __init__(self, speed=40, before=None, auto_sync=True, token='', **overrides):
        self.sim = SimServer(speed, token=token, **overrides)
        if before:
            before(self.sim)  # put the pretend equipment into some state before the program connects
        self.temp = tempfile.TemporaryDirectory()
        data = Path(self.temp.name) / 'data'
        data.mkdir()
        (data / 'config.json').write_text(json.dumps({'rpi_host': '127.0.0.1', 'rpi_port': self.sim.port, 'token': token}),
                                          encoding='utf-8')
        self.store = store.Store(data)
        self.link = rpilink.RpiLink(self.store, search=False, auto_sync=auto_sync)

    def connected(self):
        return wait_until(self.link.latest)

    def fresh(self):
        """Have the pretend RPi read its pretend equipment now, and wait until this side has that reading."""
        self.link.command('status', fresh=True)
        seen = self.link.status_mono
        wait_until(lambda: self.link.status_mono != seen)

    def texts(self):
        return ' '.join(event['text'] for event in self.store.events(limit=200))

    def record(self):
        """Everything the link sends over its connection from now on: [(cmd, fields)]."""
        sent, client = [], self.link.client
        original = client.request
        client.request = lambda cmd, **fields: sent.append((cmd, fields)) or original(cmd, **fields)
        return sent

    def discharge(self, **options):
        """A discharge of the simulator, as a round would start it: nobody watching, result claimed."""
        options = dict({'attended': False, 'claim': True}, **options)
        return self.link.command('plasma_run', confirm_high_voltage=True, **options)['job']

    def close(self):
        self.link.close()
        self.sim.close()
        self.temp.cleanup()


class Base(unittest.TestCase):
    options = {}

    def setUp(self):
        self.rig = Rig(**self.options)
        self.addCleanup(self.rig.close)
        self.link, self.sim = self.rig.link, self.rig.sim


def status_dict(**change):
    """The RPi's status reply with everything off; ``change`` are dotted keys such as power__run_stop_setting."""
    status = {'busy': False, 'job': None, 'age_s': 1.2, 'ready': {'plasma': True, 'problems': []}, 'start_s': 5,
              'snapshot': {'parameter': {'valid': True, 'voltage_v': 300, 'on_time_ms': 25, 'off_time_ms': 5, 'cycles': 25,
                                         'loops': 8, 'integration_ms': 1000, 'spectra_interval_ms': 1500},
                           'power': {'valid': True, 'run_stop_setting': 0, 'voltage_v': 0, 'current_a': 0},
                           'nano': {'valid': True, 'state': 'IDLE', 'fault': 'NONE', 'output_command_high': False,
                                    'output_pin_sample': False,
                                    'active_parameters': {'on_time_us': 25000, 'off_time_us': 5000, 'cycles': 25,
                                                          'loop_count': 8, 'integration_time_us': 1000000,
                                                          'spectra_interval_us': 1500000, 'voltage_display_v': 300}}}}
    for dotted, value in change.items():
        node, names = status, dotted.split('__')
        for name in names[:-1]:
            node = node['snapshot'][name] if name in ('power', 'nano', 'parameter') else node[name]
        node[names[-1]] = value
    return status


class HighVoltageTableTests(unittest.TestCase):
    """SPEC B.4, "High-voltage view": every row, on status replies edited by hand. No connection involved."""

    def view(self, connected=True, received=0.4, **change):
        return rpilink.hv_state(connected, status_dict(**change), received)

    def test_every_row_in_its_order(self):
        unknown = lambda text: {'known': False, 'idle': False, 'reasons': [text]}  # noqa: E731
        busy = lambda text: {'known': True, 'idle': False, 'reasons': [text]}      # noqa: E731
        running = {'id': 'HMI-' + '1' * 32, 'kind': 'plasma', 'state': 'running', 'high_voltage': True}
        self.assertEqual(self.view(), IDLE)
        self.assertEqual(self.view(connected=False), unknown('RPi Not connected'))
        self.assertEqual(self.view(received=5.1), unknown('RPi status exceeds 5 Not received in seconds No update for'))
        self.assertEqual(self.view(received=None), unknown('RPi status exceeds 5 Not received in seconds No update for'))
        self.assertEqual(rpilink.hv_state(True, None, None), unknown('RPi status exceeds 5 Not received in seconds No update for'))   # connected, nothing heard yet
        self.assertEqual(self.view(job=running), busy('is discharging'))
        self.assertEqual(self.view(age_s=10.1), unknown('RPi The device status read exceeds 10 seconds'))
        self.assertEqual(self.view(age_s=None), unknown('RPi The device status read exceeds 10 seconds'))
        self.assertEqual(self.view(power__valid=False), unknown('high voltage power supply/Nano is invalid Press the password of'))
        self.assertEqual(self.view(nano__valid=None), unknown('high voltage power supply/Nano is invalid Press the password of'))
        self.assertEqual(self.view(snapshot=None), unknown('high voltage power supply/Nano is invalid Press the password of'))
        self.assertEqual(self.view(power__run_stop_setting=1), busy('high voltage power supply is ON'))
        self.assertEqual(self.view(power__voltage_v=0.4), busy('High voltage output has not returned to zero yet'))
        self.assertEqual(self.view(power__current_a=0.01), busy('High voltage output has not returned to zero yet'))
        for state in ('ARMED', 'WAIT_TRIGGER', 'RUNNING', None):
            self.assertEqual(self.view(nano__state=state), busy('Nano is still outputting or is ready'), state)
        self.assertEqual(self.view(nano__output_command_high=True), busy('Nano is still outputting or is ready'))
        self.assertEqual(self.view(nano__output_pin_sample=None), busy('Nano is still outputting or is ready'))
        for state in ('IDLE', 'DONE', 'FAULT'):  # a faulted Nano has its output off
            self.assertEqual(self.view(nano__state=state), IDLE, state)

    def test_a_device_that_reads_on_decides_also_when_the_other_one_is_silent(self):
        """"Not known" is what an operator's tick may overrule. A supply that reads ON, or a Nano that
        is armed or pulsing, is known and never overruled, whatever the other device answers."""
        busy = lambda text: {'known': True, 'idle': False, 'reasons': [text]}      # noqa: E731
        self.assertEqual(self.view(nano__valid=False, power__run_stop_setting=1), busy('high voltage power supply is ON'))
        self.assertEqual(self.view(nano__valid=False, power__voltage_v=120), busy('High voltage output has not returned to zero yet'))
        self.assertEqual(self.view(power__valid=False, nano__state='RUNNING'), busy('Nano is still outputting or is ready'))
        self.assertEqual(self.view(power__valid=False, nano__output_pin_sample=True), busy('Nano is still outputting or is ready'))
        # both quiet as far as they answer: then, and only then, the state is not known
        self.assertEqual(self.view(power__valid=False)['known'], False)
        self.assertEqual(self.view(nano__valid=False)['known'], False)
        self.assertEqual(self.view(power__valid=False, nano__valid=False)['known'], False)

    def test_both_ages_have_their_own_limit_and_the_first_matching_row_decides(self):
        self.assertEqual(self.view(received=5.0, age_s=10), IDLE)   # 5 s since this computer heard it, 10 s since the RPi read it
        self.assertEqual(self.view(received=5.01, age_s=1)['reasons'], ['RPi status exceeds 5 Not received in seconds No update for'])
        self.assertEqual(self.view(received=1, age_s=10.01)['reasons'], ['RPi The device status read exceeds 10 seconds'])
        everything = dict(job={'state': 'running', 'high_voltage': True}, age_s=99, power__valid=False,
                          power__run_stop_setting=1, power__voltage_v=300, nano__state='RUNNING')
        self.assertEqual(self.view(connected=False, received=99, **everything)['reasons'], ['RPi Not connected'])
        self.assertEqual(self.view(received=99, **everything)['reasons'], ['RPi status exceeds 5 Not received in seconds No update for'])
        self.assertEqual(self.view(**everything)['reasons'], ['is discharging'])
        del everything['job']
        self.assertEqual(self.view(**everything)['reasons'], ['RPi The device status read exceeds 10 seconds'])
        # the supply does not answer, the Nano is pulsing: that is known, and it is not idle
        self.assertEqual(self.view(**dict(everything, age_s=2)), {'known': True, 'idle': False, 'reasons': ['Nano is still outputting or is ready']})
        self.assertEqual(self.view(**dict(everything, age_s=2, power__valid=True))['reasons'], ['high voltage power supply is ON'])
        self.assertEqual(self.view(**dict(everything, age_s=2, nano__state='IDLE'))['reasons'], ['high voltage power supply/Nano is invalid Press the password of'])
        capture = {'id': 'CAP-' + '1' * 32, 'kind': 'capture', 'state': 'running', 'high_voltage': False}
        self.assertEqual(self.view(job=capture), IDLE)                # a capture without discharge is no high voltage
        self.assertEqual(self.view(job=dict(capture, state='done', high_voltage=True)), IDLE)


class HighVoltageLiveTests(Base):
    options = {'auto_sync': False}

    def test_the_view_follows_the_pretend_equipment(self):
        self.rig.connected()
        self.assertEqual(self.link.hv_view(), IDLE)
        backend = self.sim.backend
        backend.run_stop = 1
        self.rig.fresh()
        self.assertEqual(self.link.hv_view(), {'known': True, 'idle': False, 'reasons': ['high voltage power supply is ON']})
        backend.run_stop, backend.voltage = 0, 12.0
        self.rig.fresh()
        self.assertEqual(self.link.hv_view()['reasons'], ['High voltage output has not returned to zero yet'])
        backend.voltage, backend.state = 0.0, 'ARMED'
        self.rig.fresh()
        self.assertEqual(self.link.hv_view()['reasons'], ['Nano is still outputting or is ready'])
        backend.state = 'IDLE'
        backend.sim_fault('HEARTBEAT_LOST')
        self.rig.fresh()
        self.assertEqual(self.link.hv_view(), IDLE)
        self.assertEqual(self.link.view()['hv'], IDLE)
        with self.link.request_lock:      # no new status while the clock is turned back
            time.sleep(.1)
            with self.link.lock:
                self.link.status_mono -= 6
            self.assertEqual(self.link.hv_view()['reasons'], ['RPi status exceeds 5 Not received in seconds No update for'])
        self.link.close()
        self.assertEqual(self.link.hv_view(), {'known': False, 'idle': False, 'reasons': ['RPi Not connected']})


class HighVoltageDuringDischargeTests(Base):
    options = {'speed': 4}

    def test_a_running_discharge_is_known_and_not_idle(self):
        self.rig.connected()
        job = self.rig.discharge()
        seen = wait_until(lambda: (lambda view: view if not view['idle'] and view['known'] else None)(self.link.hv_view()))
        self.assertEqual(seen['reasons'], ['is discharging'])
        self.assertEqual(self.link.wait_job(job, limit_s=60), ('done', ''))
        self.assertEqual(wait_until(lambda: self.link.hv_view()['idle'] and self.link.hv_view()), IDLE)


class FlowTests(unittest.TestCase):
    def rig(self, **options):
        rig = Rig(**options)
        self.addCleanup(rig.close)
        rig.connected()
        return rig

    def test_the_flow_the_rpi_is_set_to_is_named_with_its_frames_and_its_wait(self):
        link = self.rig().link
        self.assertEqual(link.flow_view(), {'name': 'unified', 'ok': True, 'background_frames': 1,
                                            'text': 'Turn on the high voltage first and shoot the background 1 sheets, click to make approx. Stop automatically after 5 seconds discharge'})
        self.assertEqual(link.view()['flow'], link.flow_view())
        link = self.rig(flow='verified').link
        self.assertEqual(link.flow_view(), {'name': 'verified', 'ok': True, 'background_frames': 3,
                                            'text': 'Old Process: Background 3 sheets, click to make approx. Stop automatically after 11 seconds discharge'})

    def test_an_rpi_that_does_not_say_how_it_measures_must_not_discharge(self):
        link = self.rig().link
        hello = dict(link.hello)
        del hello['flow']
        with link.lock:
            link.hello = hello        # what a server without "flow" in its hello says
        self.assertEqual(link.flow_view(), {'name': None, 'ok': False, 'background_frames': None,
                                            'text': 'RPi is still an old version'})

    def test_the_other_cases(self):
        status = {'start_s': 5}
        flow = rpilink.flow_state
        problem = 'RPi There is something attached to plasma_measure.py (FileNotFoundError) .Please re-execute on the computer Install.cmd Bundle RPi end installed'
        self.assertEqual(flow(True, {'flow': 'unified', 'flow_error': problem}, status),
                         {'name': 'unified', 'ok': False, 'background_frames': 1, 'text': problem})
        self.assertEqual(flow(True, {'flow': 'verified', 'flow_error': ''}, status)['ok'], True)
        self.assertEqual(flow(True, {'flow': 'unified'}, None)['text'], 'Turn on the high voltage first and shoot the background 1 open')          # no status yet
        self.assertEqual(flow(True, {'flow': 'verified'}, {'start_s': None})['text'], 'Old Process: Background 3 open')
        self.assertEqual(flow(False, {'flow': 'unified'}, status), {'name': None, 'ok': False, 'background_frames': None,
                                                                    'text': 'RPi Not connected'})
        self.assertEqual(flow(True, None, status)['ok'], False)
        later = flow(True, {'flow': 'turbo'}, status)                 # a flow this program has never heard of
        self.assertEqual((later['name'], later['ok'], later['background_frames']), ('turbo', False, None))
        self.assertEqual(flow(True, {'flow': ''}, status)['text'], 'RPi is still an old version')


class ParameterTests(Base):
    options = {'auto_sync': False}

    def test_the_seven_values_and_whether_the_nano_holds_the_same(self):
        self.rig.connected()
        view = self.link.params_view()
        self.assertEqual(view, {'values': GOOD, 'nano_synced': False})      # a Nano that was just switched on holds none
        self.assertEqual(list(view['values']), ['Ontime', 'Offtime', 'Cycle', 'Loopnum', 'Integration_Time',
                                                'Spectra_Interval', 'Voltage'])
        self.link.command('prepare')
        wait_until(lambda: self.link.params_view()['nano_synced'])
        self.assertEqual(self.link.view()['parameters'], {'values': GOOD, 'nano_synced': True})
        self.link.command('set_parameters', parameters=dict(GOOD, Voltage=300, Ontime=12.5))
        changed = wait_until(lambda: (lambda v: v if v['values']['Voltage'] == 300 else None)(self.link.params_view()))
        self.assertEqual(changed, {'values': dict(GOOD, Voltage=300, Ontime=12.5), 'nano_synced': False})
        self.link.command('prepare')
        wait_until(lambda: self.link.params_view()['nano_synced'])

    def test_what_is_not_known_is_not_guessed(self):
        params = rpilink.params_state
        self.assertEqual(params(None), {'values': None, 'nano_synced': None})
        self.assertEqual(params(status_dict()), {'values': {'Ontime': 25, 'Offtime': 5, 'Cycle': 25, 'Loopnum': 8,
                                                            'Integration_Time': 1000, 'Spectra_Interval': 1500, 'Voltage': 300},
                                                 'nano_synced': True})
        self.assertEqual(params(status_dict(parameter__valid=False)), {'values': None, 'nano_synced': None})
        self.assertEqual(params(status_dict(parameter__loops=None)), {'values': None, 'nano_synced': None})
        self.assertIsNone(params(status_dict(nano__valid=False))['nano_synced'])
        self.assertIs(params(status_dict(nano__active_parameters=None))['nano_synced'], False)
        active = status_dict()['snapshot']['nano']['active_parameters']
        for key, value in (('on_time_us', 25100), ('loop_count', 9), ('voltage_display_v', 299), ('cycles', None)):
            self.assertIs(params(status_dict(nano__active_parameters=dict(active, **{key: value})))['nano_synced'], False, key)
        self.assertIs(params(status_dict(parameter__on_time_ms=25.0, parameter__voltage_v=300.0))['nano_synced'], True)


class ClaimTests(Base):
    def test_a_claimed_job_is_left_to_whoever_claimed_it(self):
        self.rig.connected()
        job = self.rig.discharge()
        self.assertIsNone(self.link.own_job)                                   # nobody is expected to watch this one
        self.assertEqual(self.link.wait_job(job, limit_s=30), ('done', ''))
        time.sleep(2.5)                                                         # two more status rounds
        self.assertEqual((self.rig.store.list_runs(), self.rig.store.run_ids()), ([], set()))
        self.assertNotIn('discharge completed', self.rig.texts())
        result = self.link.fetch_result(job)
        self.assertEqual((result['job'], result['kind'], result['flow'], result['background_frames'], len(result['frames'])),
                         (job, 'plasma', 'unified', 1, 8))
        meta = self.rig.store.save_run(result, round_id='7' * 32, recipe_name='Only discharge measurement', operator='Wang Xiaoming', slot=1,
                                       source='round')
        self.assertEqual((meta['source'], meta['flow'], meta['signal_frames']), ('round', 'unified', 8))
        self.assertAlmostEqual(meta['peak_nm'], 337.13, delta=.3)
        capture = self.link.command('spectrum', claim=True, integration_ms=100)['job']
        self.assertEqual(self.link.wait_job(capture, limit_s=30), ('done', ''))
        time.sleep(2.5)
        self.assertEqual([row['job'] for row in self.rig.store.list_runs()], [job])
        self.assertEqual(self.sim.backend.run_stop, 0)

    def test_a_claim_counts_even_before_the_first_status_has_arrived(self):
        self.rig.connected()
        with self.link.lock:
            self.link.handled = None     # as right after the program started
        capture = self.link.command('spectrum', claim=True, integration_ms=100)['job']
        self.assertEqual(self.link.wait_job(capture, limit_s=30), ('done', ''))
        time.sleep(2.5)
        self.assertIn(capture, self.link.handled)
        self.assertEqual(self.rig.store.list_runs(), [])

    def test_a_job_nobody_claimed_is_saved_by_the_connection_itself_and_says_what_it_was(self):
        self.rig.connected()
        capture = self.link.command('spectrum', integration_ms=100)['job']                  # Maintenance › spectrum
        wait_until(lambda: capture in self.rig.store.run_ids())
        plasma = self.link.command('plasma_run', attended=False, confirm_high_voltage=True)['job']
        wait_until(lambda: plasma in self.rig.store.run_ids())
        rows = {row['job']: row for row in self.rig.store.list_runs()}
        self.assertEqual({job: (row['kind'], row['source'], row['round_id']) for job, row in rows.items()},
                         {capture: ('capture', 'check', None), plasma: ('plasma', 'manual', None)})
        texts = self.rig.texts()
        for expected in ('Spectrum acquisition completed, spectrum has been archived', 'Discharge completed, spectrum has been archived', 'starts discharging in seconds'):
            self.assertIn(expected, texts)
        # The connection went away between "the job is done" and the read: said in the log, and nothing breaks.
        self.link.close()
        self.link._finished({'id': 'HMI-' + '3' * 32, 'kind': 'plasma', 'state': 'done'})
        self.assertIn('The discharge is completed, but the spectrum reading fails: the connection is interrupted (the data is still RPi,Can be reread later) ', self.rig.texts())


class WaitTests(Base):
    options = {'speed': 3}

    def test_stop_ends_the_wait_with_stopped_and_somebody_elses_end_with_failed(self):
        self.rig.connected()
        stop = threading.Event()
        job = self.rig.discharge()
        wait_until(lambda: self.sim.backend.run_stop == 1)
        stop.set()
        self.assertEqual(self.link.stop()['cancel_sent'], True)
        status, note = self.link.wait_job(job, stop)
        self.assertEqual((status, note.startswith('Stop pressed')), ('stopped', True))
        self.assertEqual(self.sim.backend.run_stop, 0)
        # The same job cut off because the connection that owns it went away: the RPi stops it by itself.
        wait_until(lambda: self.link.latest() and self.link.latest()['ready']['plasma'])
        job = self.rig.discharge()
        wait_until(lambda: self.sim.backend.run_stop == 1)
        self.link._drop('test: the line is unplugged')
        status, note = self.link.wait_job(job, limit_s=60, lost_s=20)       # the gap is shorter than what is tolerated
        self.assertEqual((status, note.startswith('The control connection is interrupted and the server automatically stops')), ('failed', True))
        self.assertEqual(self.sim.backend.run_stop, 0)
        self.assertNotIn('Discharge not completed', self.rig.texts())                     # claimed: the round reports it, nobody else

    def test_a_job_that_fails_says_why_and_whether_the_off_readback_was_confirmed(self):
        self.rig.connected()
        backend = self.sim.backend

        def broken(outdir, runner, tick, flow='unified'):
            raise RuntimeError('HSPY Set voltage/current limit and Except parameter.json Does not match')
        backend.plasma_run = broken
        self.assertEqual(self.link.wait_job(self.rig.discharge(), limit_s=30), ('failed', 'HSPY Set voltage/current limit and Except parameter.json Does not match'))
        wait_until(lambda: self.link.latest() and not self.link.latest()['busy'])

        def no_readback():
            raise RuntimeError('RS485 No response')
        backend.stop = no_readback
        status, note = self.link.wait_job(self.rig.discharge(), limit_s=30)
        self.assertEqual(status, 'failed')
        self.assertIn('; after stopping OFF readback unconfirmed (RS485 No response) - Please confirm physically that the high voltage has been turned off', note)


class WaitLimitTests(Base):
    def test_the_connection_may_be_gone_for_a_while_but_not_for_long(self):
        self.rig.connected()
        job = self.rig.discharge()
        real, started = self.link.latest, time.perf_counter()
        self.link.latest = lambda max_age=10.0: None if time.perf_counter() - started < 1.0 else real(max_age)
        self.assertEqual(self.link.wait_job(job, limit_s=30, lost_s=5), ('done', ''))     # a gap of 1 s is tolerated
        self.assertGreater(time.perf_counter() - started, 1.0)
        self.link.latest = lambda max_age=10.0: None
        started = time.perf_counter()
        self.assertEqual(self.link.wait_job(job, limit_s=30, lost_s=1.0),
                         ('failed', 'and RPi ’s connection is interrupted and the results of this measurement cannot be confirmed; please go to the site to confirm the equipment'))
        self.assertTrue(1.0 < time.perf_counter() - started < 3.0)

    def test_the_wait_has_an_end_and_after_stop_a_shorter_one(self):
        self.rig.connected()
        unknown = 'HMI-' + '0' * 32        # a job the RPi never reports as finished
        started = time.perf_counter()
        self.assertEqual(self.link.wait_job(unknown, limit_s=1), ('failed', 'exceeds 1 seconds have not ended yet; please go to the site to confirm the device'))
        self.assertTrue(.9 <= time.perf_counter() - started < 2.5)
        defaults = {name: value.default for name, value in inspect.signature(self.link.wait_job).parameters.items()}
        self.assertEqual(defaults, {'job_id': inspect.Parameter.empty, 'stop_event': None, 'limit_s': 360, 'lost_s': 30})
        patch(self, rpilink, 'STOP_WAIT_SECONDS', 1.0)
        stop = threading.Event()
        threading.Timer(.5, stop.set).start()
        started = time.perf_counter()
        self.assertEqual(self.link.wait_job(unknown, stop, limit_s=30), ('failed', 'exceeds 1 seconds have not ended yet; please go to the site to confirm the device'))
        self.assertTrue(1.3 <= time.perf_counter() - started < 3.0)      # 0.5 s until STOP, then one second more
        self.assertIsNone(self.sim.controller.job)                      # waiting never sends anything


class ResultTests(Base):
    def setUp(self):
        super().setUp()
        self.rig.connected()
        patch(self, rpilink, 'RESULT_RETRY_SECONDS', .4)
        self.job = self.rig.discharge()
        self.assertEqual(self.link.wait_job(self.job, limit_s=30), ('done', ''))

    def failing(self, how_often, error):
        """The next ``how_often`` reads of a result fail; everything the link sends is recorded."""
        sent, client = [], self.link.client
        original = client.request

        def request(cmd, **fields):
            sent.append((cmd, fields, time.perf_counter()))
            if cmd == 'result' and sum(1 for row in sent if row[0] == 'result') <= how_often:
                raise error
            return original(cmd, **fields)
        client.request = request
        return sent

    def test_a_read_that_fails_is_tried_once_more_and_the_discharge_never(self):
        sent = self.failing(1, PlasmicError('busy', 'This work is still in progress'))
        result = self.link.fetch_result(self.job)
        self.assertEqual((result['job'], len(result['frames'])), (self.job, 8))
        reads = [row for row in sent if row[0] == 'result']
        self.assertEqual([row[1] for row in reads], [{'job': self.job, 'frames': True}] * 2)
        self.assertGreaterEqual(reads[1][2] - reads[0][2], .4)
        self.assertLessEqual({row[0] for row in sent}, {'result', 'status'})   # in particular: no second plasma_run

    def test_two_failed_reads_are_reported_and_nothing_is_repeated(self):
        sent = self.failing(2, PlasmicError('not_found', 'The work folder cannot be found'))
        with self.assertRaisesRegex(PlasmicError, 'The work folder cannot be found'):
            self.link.fetch_result(self.job)
        self.assertEqual([row[0] for row in sent if row[0] != 'status'], ['result', 'result'])
        self.assertEqual(self.sim.controller.job.id, self.job)       # no second job was started

    def test_a_connection_that_drops_during_the_read_is_back_for_the_second_try(self):
        patch(self, rpilink, 'RESULT_RETRY_SECONDS', 1.5)
        self.failing(1, ConnectionError('connection reset'))
        result = self.link.fetch_result(self.job)
        self.assertEqual(result['job'], self.job)
        self.assertIn('and RPi ’s connection interrupted: connection reset The connection of', self.rig.texts())
        gone = Rig()
        self.addCleanup(gone.close)
        gone.connected()
        gone.link.close()
        with self.assertRaisesRegex(RuntimeError, 'Not connected yet RPi'):
            gone.link.fetch_result(self.job)


class StopTests(Base):
    options = {'speed': 3}

    def test_stop_takes_its_own_connection_and_never_waits_for_the_busy_one(self):
        self.rig.connected()
        calls, real = [], rpilink.plasmic_client.stop
        patch(self, rpilink.plasmic_client, 'stop', lambda host, port, **more: calls.append((host, port)) or real(host, port, **more))
        sent = self.rig.record()
        with self.link.request_lock:                # some long command holds the connection
            started = time.perf_counter()
            reply = self.link.stop()
            took = time.perf_counter() - started
        self.assertEqual((reply['ok'], reply['cancel_sent'], reply['stopped']), (True, False, True))
        self.assertLess(took, 5)
        self.assertEqual(calls, [('127.0.0.1', self.sim.port)])
        self.assertNotIn('stop', [cmd for cmd, _fields in sent])
        job = self.rig.discharge()
        wait_until(lambda: self.sim.backend.run_stop == 1)
        self.assertEqual((self.link.stop()['cancel_sent'], self.link.wait_job(job)[0]), (True, 'stopped'))
        self.assertEqual(self.sim.backend.run_stop, 0)

    def test_a_stop_that_cannot_be_delivered_says_what_to_do(self):
        self.rig.connected()
        self.rig.store.save_config({'rpi_host': '127.0.0.1', 'rpi_port': 9, 'token': ''})   # nothing listens there
        with self.assertRaisesRegex(RuntimeError, '^The stop command cannot be sent RPi (.*) — Please physically turn off the high voltage immediately$'):
            self.link.stop()
        self.link.reconnect()
        view = wait_until(lambda: (lambda v: v if '127.0.0.1:9' in v['error'] else None)(self.link.view()))
        self.assertEqual((view['connected'], view['status'], view['hv']['reasons'], view['flow']['text']),
                         (False, None, ['RPi Not connected'], 'RPi Not connected'))
        with self.assertRaisesRegex(RuntimeError, 'Not connected yet RPi'):
            self.link.command('prepare')
        with self.assertRaisesRegex(RuntimeError, '^and RPi Not connected$'):
            self.link.rpi_ready()


def bare_link(status=None, pending=''):
    """An RpiLink without its thread: only what _status_fields() looks at."""
    link = object.__new__(rpilink.RpiLink)
    link.lock = threading.Lock()
    link.status, link.pending, link.fresh_asked = status, pending, float('-inf')
    return link


class FreshRuleTests(unittest.TestCase):
    def test_when_the_next_status_asks_the_rpi_to_read_again(self):
        fresh, plain = {'fresh': True}, {}
        self.assertEqual(bare_link(None)._status_fields(), plain)                       # nothing heard yet
        for age, expected in ((0.4, plain), (8, plain), (8.1, fresh), (59.0, fresh), (None, fresh)):
            self.assertEqual(bare_link({'age_s': age, 'busy': False})._status_fields(), expected, age)
        self.assertEqual(bare_link({'age_s': 30, 'busy': True})._status_fields(), plain)     # a job runs: the RPi cannot read
        self.assertEqual(bare_link({'age_s': 30, 'busy': False}, pending='prepare')._status_fields(), plain)
        link = bare_link({'age_s': 30, 'busy': False})
        self.assertEqual([link._status_fields(), link._status_fields(), link._status_fields()], [fresh, plain, plain])
        link.fresh_asked -= 5.01                                                         # at most once every 5 s
        self.assertEqual(link._status_fields(), fresh)
        self.assertEqual((rpilink.FRESH_AGE_SECONDS, rpilink.FRESH_EVERY_SECONDS), (8.0, 5.0))


class FreshLiveTests(Base):
    options = {'poll_s': 60, 'auto_sync': False}   # an RPi set to read its equipment only once a minute

    def test_a_reading_that_has_grown_old_is_asked_for_again(self):
        patch(self, rpilink, 'FRESH_AGE_SECONDS', 1.5)
        patch(self, rpilink, 'FRESH_EVERY_SECONDS', 1.0)
        self.rig.connected()
        sent = self.rig.record()
        wait_until(lambda: sum(fields == {'fresh': True} for _cmd, fields in sent) >= 2, 20)
        self.assertEqual({cmd for cmd, _fields in sent}, {'status'})
        kinds = [bool(fields) for _cmd, fields in sent]
        first = kinds.index(True)
        self.assertEqual(kinds[first + 1], False)        # right after a fresh reading a plain question is enough
        self.assertLess(self.link.latest()['age_s'], 4)  # and the reading this side holds stays young
        self.assertEqual(self.link.hv_view(), IDLE)


class FixTests(unittest.TestCase):
    def test_the_six_replacements(self):
        fix = rpilink.rewrite_fix
        self.assertEqual(fix('Wait for the voltage to drop to 0 and then click ”Read Status”'), 'Wait until the voltage drops to 0,The program will re-read itself')
        self.assertEqual(fix('Click ”Read Status”; if it still fails, please check RS485 USB and RPi'), 'Wait a few seconds, the program will re-read itself; if it still fails, please check RS485 USB and RPi')
        self.assertEqual(fix('Click Clear Nano fault”, then click ”Synchronize Nano parameter”'),
                         'Ask maintenance personnel to go to ”Repair › Plasma Parameters” click ”Clear” Nano fault”, then click ”Sync to Nano”')
        self.assertEqual(fix('Press ”HSPY OFF＋Nano STOP”'), 'Press ”Stop” at the bottom STOP”')
        self.assertEqual(fix('re-save it in ”Plasma Parameters” (will write HSPY set value) '),          # M22: the program writes it
                         'Wait a few seconds and the program will change it by itself. There is no change: Click ”Measurement Content” in ”Scheduled Measurement” and click ”Save and Sync”')
        self.assertEqual(fix('Save after setting the voltage in ”Plasma Parameters”'), 'Ask maintenance personnel to go to ”Repair › Plasma Parameters”Set the voltage and save it')
        self.assertEqual(fix('examine HSPY Is the power on?RS485 Wiring and Addressing 1'),
                         'Turn on the high voltage power supply (HSPY) ’s power switch, and then click ”Recheck”.'
                         'is on but still no response: look behind it RS485 of A, B Are the two lines loose or connected reversely? Is the communication address correct? 1, rate 9600')
        for untouched in ('', 'examine Nano powered by, RS485 address 2 and firmware 1.2.1', 'exist HSPY panel adjusts the current limit to 0.1 A the following',
                          'Double-click on the computer Install.cmd,Reinstall RPi end'):
            self.assertEqual(fix(untouched), untouched)
        self.assertIsNone(fix(None))
        lengths = [len(old) for old, _new in rpilink.FIXES]
        self.assertEqual(lengths, sorted(lengths, reverse=True))     # the longest is replaced first

    def test_the_supply_that_does_not_answer_is_said_in_plain_words(self):
        """AMENDMENTS M17: the person asked why the schedule would not start; the RPi's own words did not tell him."""
        plain = rpilink.rewrite_reason
        for said in ('HSPY Power readback is invalid:Timed out waiting for HSPY status', 'HSPY Power readback is invalid:', 'HSPY Power readback is invalid:CRC mismatch'):
            self.assertEqual(plain(said), 'high voltage power supply (HSPY) No response: The power is not on, or its RS485 line is not connected properly')
        for untouched in ('', 'HSPY power supply is ON', 'Nano Invalid readback:timeout', 'Nano fault lock HEARTBEAT_LOST', 'Status exceeds 20 Not updated in seconds'):
            self.assertEqual(plain(untouched), untouched)
        self.assertIsNone(plain(None))
        # M22: what the person saw on 2026-10-07 (“cannot start — parameter.json Voltage 30 V and HSPY set value 1000.0 V inconsistent”)
        self.assertEqual(plain('parameter.json Voltage 30 V and HSPY set value 1000.0 V inconsistent'),
                         'high-voltage power supply panel is 1000 V,program is 30 V')
        self.assertEqual(plain('parameter.json Voltage 250 V and HSPY set value 30.5 V inconsistent'),
                         'high-voltage power supply panel is 30.5 V,program is 250 V')
        self.assertEqual(plain('parameter.json Voltage 30 V and HSPY set value None V inconsistent'),
                         'high-voltage power supply panel is None V,program is 30 V')

    def test_a_reading_taken_now_is_said_in_plain_words(self):
        """AMENDMENTS M18 (“Recheck”): which of the two answered, and where to look."""
        view = rpilink.check_view
        power = {'valid': True, 'run_stop_setting': 0, 'voltage_v': 0.0, 'set_voltage_v': 30.0, 'current_a': 0.0}
        nano = {'valid': True, 'state': 'IDLE', 'fault': 'NONE', 'firmware': '1.2.1'}
        silent = {'valid': False, 'error': 'Timed out waiting for HSPY status'}

        def status(power, nano, ready=None, busy=False):
            return {'busy': busy, 'snapshot': {'power': power, 'nano': nano}, 'ready': ready or {'plasma': True, 'problems': []}}
        self.assertEqual(view(status(power, nano)), {
            'ok': True, 'busy': False, 'advice': 'high voltage power supply and Nano all responded.',
            'rows': [{'name': 'high voltage power supply (HSPY) ', 'ok': True, 'text': 'has response: output Is OFF,0 V (settings 30 V) '},
                     {'name': 'Nano controller', 'ok': True, 'text': 'responded: Standby'}]})
        on = view(status(dict(power, run_stop_setting=1, voltage_v=298.0, set_voltage_v=300.0), dict(nano, state='RUNNING')))
        self.assertEqual([row['text'] for row in on['rows']], ['has response: output Is ON,298 V (settings 300 V) ', 'has a response: Outputting'])
        # Both answer and something else stands in the way: said with it.
        both = view(status(power, dict(nano, state='FAULT', fault='HEARTBEAT_LOST'),
                           {'plasma': False, 'problems': [{'reason': 'Nano fault lock HEARTBEAT_LOST', 'fix': 'x'}]}))
        self.assertEqual((both['ok'], both['rows'][1]['text'], both['advice']),
                         (True, 'responded: fault (HEARTBEAT_LOST) ', 'high voltage power supply and Nano all responded. Can’t discharge yet:Nano fault lock HEARTBEAT_LOST'))
        # The rig on 2026-10-07: the supply is switched on and says nothing, the Nano on the same line answers.
        rig = view(status(silent, nano, {'plasma': False, 'problems': [{'reason': 'HSPY Power readback is invalid:Timed out waiting for HSPY status'}]}))
        self.assertEqual((rig['ok'], rig['rows'][0], rig['rows'][1]['ok']),
                         (False, {'name': 'high voltage power supply (HSPY) ', 'ok': False, 'text': 'No response'}, True))
        self.assertTrue(rig['advice'].startswith('Nano has a response, so RPi, RS485 The adapter and the line are connected; the problem is in the high-voltage power supply section.'))
        for words in ('Is the panel on?', 'A, B two lines', 'Is the address 1', '9600'):
            self.assertIn(words, rig['advice'])
        # Something came back that cannot be read: another advice than for silence.
        garbled = view(status({'valid': False, 'error': 'Modbus CRC mismatch'}, nano))
        self.assertEqual((garbled['rows'][0]['text'], garbled['advice']),
                         ('Something came back, but I can’t read it (Modbus CRC mismatch) ', 'There is something in the high-voltage power supply, but the content is wrong: most likely A, B Line contact is poor or the speed is not 9600.'))
        self.assertEqual(view(status(power, {'valid': False, 'error': 'timeout'}))['advice'],
                         'High voltage power supply responds,Nano None: See Nano power supply,and its RS485 module line.')
        self.assertEqual(view(status(silent, {'valid': False, 'error': 'Timed out'}))['advice'],
                         'high voltage power supply and Nano No response: See first RS485 adapter USB plugged in? RPi , look at those two lines again.')
        # While a job runs the RPi reads nothing; and a status of no shape at all says "both silent", never a crash.
        self.assertEqual(view(status(power, nano, busy=True)),
                         {'ok': False, 'busy': True, 'rows': [], 'advice': 'RPi Measurement is being performed, and the device cannot be read at this time. Wait for it to finish and press again.'})
        for odd in (None, {}, {'snapshot': None}, {'snapshot': {'power': 5, 'nano': 'x'}}, 'x'):
            self.assertEqual((view(odd)['ok'], [row['text'] for row in view(odd)['rows']]), (False, ['No response', 'No response']), odd)

    def test_every_button_the_rpi_names_has_a_replacement(self):
        """Read only: the texts are taken from the source of plasmic_tcpd.ready_problems."""
        source = inspect.getsource(tcpd.ready_problems)
        named = re.compile(r'[Press on]“([^”]+)”')
        literals = [text for text in re.findall(r"'([^'\n]*)'", source) if named.search(text)]
        self.assertGreaterEqual(len(literals), 8, literals)          # the scan still finds them
        # buttons of Maintenance › plasma parameters in 2.0.0; the page and the button of the plasma parameters (M17.3)
        still_there = {'Clear Nano fault', 'synchronized to Nano', 'Schedule measurement', 'Save and sync'}
        for text in literals:
            new = rpilink.rewrite_fix(text)
            self.assertNotEqual(new, text, text)
            self.assertLessEqual(set(named.findall(new)), still_there, f'{text} -> {new}')
            for old in ('read status', 'HSPY OFF＋Nano STOP', 'synchronous Nano parameter', 'in ”Plasma Parameters”'):
                self.assertNotIn(old, new, text)

    def test_the_fixes_of_a_live_status_come_out_with_todays_names(self):
        rig = Rig(before=lambda sim: sim.backend.sim_fault('HEARTBEAT_LOST'), auto_sync=False)
        self.addCleanup(rig.close)
        status = rig.connected()
        fixes = [rpilink.rewrite_fix(problem['fix']) for problem in status['ready']['problems']]
        self.assertEqual(fixes, ['Ask maintenance personnel to go to ”Repair › Plasma Parameters” click ”Clear” Nano fault”, then click ”Sync to Nano”'])
        with self.assertRaisesRegex(RuntimeError, '^The pre-discharge inspection failed:Nano fault lock HEARTBEAT_LOST$'):
            rig.link.rpi_ready()


class RequestTests(Base):
    def test_nothing_this_link_sends_chooses_an_order_and_the_terminal_is_gone(self):
        self.rig.connected()
        sent = self.rig.record()
        self.assertEqual(self.link.rpi_ready()['ready']['plasma'], True)
        job = self.rig.discharge()
        self.assertEqual(self.link.wait_job(job, limit_s=30), ('done', ''))
        self.link.fetch_result(job)
        self.link.command('prepare')
        capture = self.link.command('spectrum', integration_ms=100)['job']
        wait_until(lambda: capture in self.rig.store.run_ids())
        self.assertGreaterEqual({cmd for cmd, _fields in sent}, {'status', 'plasma_run', 'result', 'prepare', 'spectrum'})
        self.assertEqual([fields for cmd, fields in sent if cmd == 'plasma_run'], [{'confirm_high_voltage': True}])
        self.assertEqual([name for _cmd, fields in sent for name in fields if 'sequence' in name], [])
        source = (ROOT / 'pc' / 'rpilink.py').read_text(encoding='utf-8')
        self.assertNotIn('sequence', source.lower())
        self.assertFalse(hasattr(rpilink.RpiLink, 'raw'))
        self.assertIs(rpilink.Link, rpilink.RpiLink)
        self.assertEqual((rpilink.UI_WATCHDOG_SECONDS, rpilink.CANCEL_REASONS['stop']), (10.0, 'Stop pressed'))

    def test_the_view_has_what_the_page_needs_and_never_the_token(self):
        rig = Rig(token='rpi-token-123')
        self.addCleanup(rig.close)
        rig.connected()
        view = rig.link.view()
        self.assertEqual(set(view), {'connected', 'host', 'port', 'error', 'server', 'pending', 'status', 'status_age_s',
                                     'hv', 'flow', 'parameters', 'own_job', 'found', 'addresses'})
        self.assertEqual((view['connected'], view['host'], view['port'], view['error'], view['own_job'], view['found'],
                          view['addresses'], view['server']['sim'], view['server']['flow'], view['server']['flow_error']),
                         (True, '127.0.0.1', rig.sim.port, '', None, [], [], True, 'unified', ''))
        self.assertNotIn('rpi-token-123', json.dumps(view, ensure_ascii=False) + rig.texts())
        self.assertTrue(rig.link.was_connected)
        self.assertIn('is connected to RPi 127.0.0.1:%d (server %s,simulation mode)' % (rig.sim.port, tcpd.VERSION), rig.texts())


class ReadyTests(Base):
    options = {'speed': 3, 'auto_sync': False}

    def test_why_a_discharge_may_not_start_right_now(self):
        self.rig.connected()
        self.assertEqual(self.link.rpi_ready()['ready']['plasma'], True)
        job = self.rig.discharge()
        wait_until(lambda: self.link.latest()['busy'])
        with self.assertRaisesRegex(RuntimeError, '^RPi is performing other work$'):
            self.link.rpi_ready()
        self.link.stop()
        self.link.wait_job(job)
        self.sim.backend.run_stop = 1
        self.rig.fresh()
        with self.assertRaisesRegex(RuntimeError, '^The pre-discharge inspection failed:HSPY power supply is ON$'):
            self.link.rpi_ready()


class ScreenWatchdogTests(Base):
    """The attended discharge of 1.3.0 is no longer offered by any page, but its guard moved along unchanged."""
    options = {'speed': 2}

    def test_an_attended_discharge_stops_when_the_screen_goes_away(self):
        self.rig.connected()
        patch(self, rpilink, 'UI_WATCHDOG_SECONDS', 1.5)
        job = self.link.command('plasma_run', confirm_high_voltage=True)['job']
        self.assertEqual(self.link.own_job, job)
        end = time.perf_counter() + 2.5
        while time.perf_counter() < end:    # the screen keeps asking: nothing happens
            self.link.ui_alive()
            self.assertEqual(self.sim.controller.job.state, 'running')
            time.sleep(.2)
        # ...then the browser is closed: nobody calls ui_alive() any more.
        ended = wait_until(lambda: self.sim.controller.job.state != 'running' and self.sim.controller.job)
        self.assertEqual((ended.state, ended.cancel_reason), ('cancelled', 'stop'))
        self.assertEqual(self.sim.backend.run_stop, 0)
        wait_until(lambda: 'Discharge not completed: Stop pressed' in self.rig.texts())
        self.assertIn('The operation screen exceeds 2 has not responded for seconds and has automatically stopped discharging.', self.rig.texts())

    def test_an_unattended_discharge_goes_on_and_the_rpi_stops_it_when_the_program_dies(self):
        self.rig.connected()
        patch(self, rpilink, 'UI_WATCHDOG_SECONDS', 1.0)
        job = self.rig.discharge()
        wait_until(lambda: self.sim.backend.run_stop == 1)
        time.sleep(2.5)                                                 # nobody looks, and that is fine
        self.assertEqual(self.sim.controller.job.state, 'running')
        self.assertNotIn('The operation screen exceeds', self.rig.texts())
        self.link.close()                                               # what the RPi sees when the black window is closed
        ended = wait_until(lambda: self.sim.controller.job.state != 'running' and self.sim.controller.job)
        self.assertEqual((ended.id, ended.state, ended.cancel_reason), (job, 'cancelled', 'owner_disconnected'))
        self.assertEqual(self.sim.backend.run_stop, 0)


class NewNanoTests(unittest.TestCase):
    def rig(self, **options):
        rig = Rig(**options)
        self.addCleanup(rig.close)
        return rig

    def test_parameters_are_loaded_once_without_a_click(self):
        rig = self.rig()
        wait_until(lambda: rig.link.params_view()['nano_synced'])
        self.assertIn('Nano Just turned on, no parameters yet: The parameters of the configuration file have been automatically synchronized (no discharge)', rig.texts())
        self.assertEqual((rig.sim.backend.run_stop, rig.sim.backend.state, rig.sim.controller.job), (0, 'IDLE', None))
        session = rig.sim.backend.session
        time.sleep(2.5)
        self.assertEqual(rig.sim.backend.session, session)   # and it is not repeated

    def test_a_failed_attempt_is_said_once_and_points_to_the_maintenance_tab(self):
        def refuse(sim):
            def prepare():
                raise RuntimeError('need HSPY OFF/output zero')
            sim.backend.prepare = prepare
        rig = self.rig(before=refuse)
        wait_until(lambda: 'Automatic synchronization failed' in rig.texts())
        time.sleep(2.5)
        lines = [event for event in rig.store.events(limit=200) if 'Automatic synchronization failed' in event['text']]
        self.assertEqual(len(lines), 1)
        self.assertEqual((lines[0]['level'], lines[0]['actor']), ('warn', 'system'))
        self.assertRegex(lines[0]['text'], r'^Nano There are no parameters yet, and the automatic synchronization was not successful (.*need HSPY OFF/output zero.*) ; '
                                           r'Ask maintenance personnel to go to ”Repair › Plasma Parameters” press ”Synchronize to Nano”$')
        self.assertNotIn('Steps 2', rig.texts())

    def test_it_can_be_switched_off_and_never_runs_against_a_supply_that_is_on(self):
        for options in ({'auto_sync': False}, {'before': lambda sim: setattr(sim.backend, 'run_stop', 1)}):
            rig = self.rig(**options)
            rig.connected()
            time.sleep(2.0)
            self.assertEqual(rig.sim.backend.session, 0, options)
            self.assertNotIn('automatic', rig.texts())


class SupplyVoltageTests(unittest.TestCase):
    """AMENDMENTS M22. The person, 2026-10-07, with 1000 V on the supply's panel and 30 V in the program:
    “There should be no error message, just useRS485Change it and enter itvoltage”."""

    def rig(self, **options):
        rig = Rig(**options)
        self.addCleanup(rig.close)
        return rig

    @staticmethod
    def panel(volts, **more):
        def before(sim):
            sim.backend.set_voltage = volts
            for name, value in more.items():
                setattr(sim.backend, name, value)
        return before

    def test_another_voltage_on_the_panel_is_written_without_a_click(self):
        rig = self.rig(before=self.panel(1000.0))
        wait_until(lambda: rig.sim.backend.set_voltage == 100)
        wait_until(lambda: 'Used RS485 changed to' in rig.texts())
        time.sleep(2.5)
        lines = [event for event in rig.store.events(limit=200) if 'Set voltage' in event['text']]
        self.assertEqual([(line['text'], line['level']) for line in lines],
                         [('high-voltage power supply panel was originally 1000 V,Used RS485 changed to 100 V (The power is off and there is no output)', 'info')])
        self.assertEqual((rig.sim.backend.run_stop, rig.sim.backend.voltage, rig.sim.controller.job), (0, 0, None))
        self.assertEqual(rig.link.rpi_ready()['snapshot']['power']['set_voltage_v'], 100)
        # Somebody turns the knob again: written again, and said again.
        rig.sim.backend.set_voltage = 55
        rig.fresh()
        wait_until(lambda: rig.sim.backend.set_voltage == 100)
        wait_until(lambda: 'was originally 55 V' in rig.texts())

    def test_a_supply_that_does_not_take_it_is_said_once(self):
        def deaf(sim):
            def refuse(_voltage):
                raise RuntimeError('The high voltage power supply does not accept the written set voltage. The set voltage on the 100 V (Readback: Setting 1000.0 V, Run/Stop 0, output 0 V) ')
            sim.backend.set_voltage = 1000.0
            sim.backend.write_set_voltage = refuse
        rig = self.rig(before=deaf)
        wait_until(lambda: 'is not automatically changed to' in rig.texts())
        time.sleep(2.5)
        lines = [event for event in rig.store.events(limit=200) if 'Set voltage' in event['text']]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['level'], 'warn')
        self.assertEqual(lines[0]['text'], 'high-voltage power supply panel is 1000 V,is not automatically changed to 100 V (The high voltage power supply does not accept the written set voltage. The set voltage on the 100 V'
                                           ' (Readback: Setting 1000.0 V, Run/Stop 0, output 0 V) ) .The program will be written again before discharge; if it cannot be written in, the high voltage will not be turned on.')
        self.assertEqual(rig.sim.backend.set_voltage, 1000.0)

    def test_an_rpi_that_does_not_know_how_is_told_to_be_updated(self):
        def old(sim):
            sim.backend.set_voltage = 1000.0
            original = sim.controller.handle

            def handle(connection, request):
                if request.get('cmd') == 'sync_voltage':
                    raise tcpd.Failure('unknown_command', 'Unrecognized command:sync_voltage')
                return original(connection, request)
            sim.controller.handle = handle
        rig = self.rig(before=old)
        wait_until(lambda: 'renew RPi' in rig.texts())
        time.sleep(2.5)
        lines = [event['text'] for event in rig.store.events(limit=200) if 'Set voltage' in event['text']]
        self.assertEqual(lines, ['RPi will not change the setting voltage of the high-voltage power supply by itself: please double-click first. The program on Install.cmd renew RPi'])
        self.assertEqual(rig.sim.backend.set_voltage, 1000.0)

    def test_never_against_a_supply_that_is_on_and_it_can_be_switched_off(self):
        for options in ({'auto_sync': False, 'before': self.panel(1000.0)},
                        {'before': self.panel(1000.0, run_stop=1)}, {'before': self.panel(1000.0, voltage=12.0)}):
            rig = self.rig(**options)
            rig.connected()
            time.sleep(2.0)
            self.assertEqual(rig.sim.backend.set_voltage, 1000.0, options)
            self.assertNotIn('Set voltage', rig.texts())

    def test_what_is_looked_at_before_the_write(self):
        """The rule alone, on a link whose own thread has ended: which statuses make it ask the RPi."""
        rig = self.rig(auto_sync=True)
        rig.connected()
        rig.link.close()
        rig.link.thread.join(10)
        self.assertFalse(rig.link.thread.is_alive())
        link, asked = rig.link, []
        link.command = lambda cmd, **fields: asked.append(cmd) or {'changed': False}

        def ask(**change):
            link.voltage_tried = None
            del asked[:]
            link._sync_voltage(status_dict(**dict({'power__set_voltage_v': 1000.0}, **change)))
            return list(asked)
        self.assertEqual(ask(), ['sync_voltage'])
        self.assertEqual(ask(power__set_voltage_v=300), [])                  # the same number: nothing to do
        self.assertEqual(ask(power__set_voltage_v=300.0), [])
        self.assertEqual(ask(power__set_voltage_v=None), [])                 # not read
        self.assertEqual(ask(power__valid=False), [])
        self.assertEqual(ask(parameter__valid=False), [])
        self.assertEqual(ask(parameter__voltage_v=701), [])                  # nothing the program may ask for
        self.assertEqual(ask(parameter__voltage_v=0), [])
        self.assertEqual(ask(parameter__voltage_v=30.0), [])
        self.assertEqual(ask(power__run_stop_setting=1), [])                 # only while it is OFF with nothing out
        self.assertEqual(ask(power__voltage_v=3), [])
        self.assertEqual(ask(power__current_a=.01), [])
        self.assertEqual(ask(busy=True), [])
        # Once for a pair of numbers; another pair is another try.
        link.voltage_tried = None
        del asked[:]
        for _again in range(3):
            link._sync_voltage(status_dict(power__set_voltage_v=1000.0))
        link._sync_voltage(status_dict(power__set_voltage_v=999.0))
        self.assertEqual(asked, ['sync_voltage', 'sync_voltage'])


class SearchTests(unittest.TestCase):
    """While the RPi cannot be reached the program asks where it is. Moved from 1.3.0 as it was; here the
    TCP client and the question to the network are both stand-ins, so nothing leaves this computer."""

    def link(self, host, port):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        data = Path(temp.name) / 'data'
        data.mkdir()
        (data / 'config.json').write_text(json.dumps({'rpi_host': host, 'rpi_port': port}), encoding='utf-8')
        kept = store.Store(data)
        link = rpilink.RpiLink(kept)
        self.addCleanup(link.thread.join, 5)  # ...and its thread has ended before the stand-ins are taken away
        self.addCleanup(link.close)
        return kept, link

    def test_an_rpi_at_another_address_is_noticed_while_disconnected(self):
        class Unreachable:  # stands in for the TCP client; never touches the network
            def __init__(self, *_args, **_options):
                pass

            def connect(self):
                raise ConnectionRefusedError()

            def close(self):
                pass
        asked, heard = [], []
        moved = {'host': '192.0.2.95', 'port': 8767, 'version': '2.0.0', 'name': 'raspberrypi', 'sim': False,
                 'allowed': True}
        patch(self, rpilink, 'Client', Unreachable)
        patch(self, rpilink.netcheck, 'discover',
              lambda port, timeout=1.2, hosts=(): asked.append((port, tuple(hosts))) or [dict(moved)])
        patch(self, rpilink.netcheck, 'local_ipv4', lambda: ['192.0.2.130'])
        kept, link = self.link('192.0.2.82', 8767)       # addresses reserved for examples: nobody lives there
        link.on_alarm = lambda *call: heard.append(call)
        view = wait_until(lambda: link.view()['found'] and link.view())
        self.assertEqual((view['connected'], view['found'], view['addresses']), (False, [moved], ['192.0.2.130']))
        self.assertEqual((view['hv']['reasons'], view['flow']['ok'], view['parameters']),
                         (['RPi Not connected'], False, {'values': None, 'nano_synced': None}))
        self.assertEqual(asked[0], (8767, ('192.0.2.82',)))
        texts = ' '.join(event['text'] for event in kept.events())
        self.assertIn('Found on the Internet RPi: 192.0.2.95 (is 192.0.2.82) ', texts)
        self.assertIn('192.0.2.82:8767 Deny connection: RPi has a response, but TCP The server is not running.', texts)
        time.sleep(.5)
        self.assertEqual(len(asked), 1)                  # one question, then quiet for a while
        self.assertEqual((link.was_connected, heard), (False, []))   # never connected: nothing was "lost"

    def test_no_search_for_the_simulator_on_this_computer(self):
        asked = []
        patch(self, rpilink.netcheck, 'discover', lambda *_args, **_options: asked.append(1) or [])
        _kept, link = self.link('127.0.0.1', 9)
        wait_until(lambda: '127.0.0.1:9' in link.view()['error'])
        self.assertEqual(asked, [])


class UnexpectedErrorTests(Base):
    def test_an_error_nobody_foresaw_does_not_end_the_connection_for_good(self):
        """A status of a shape this program does not expect (KeyError, TypeError) used to end the link
        thread silently: the page then showed "connected" with a status that never changed."""
        self.rig.connected()
        patch(self, rpilink, 'UNEXPECTED_WAIT_SECONDS', .05)
        original, hits = self.link._on_status, []

        def broken(status):
            if not hits:
                hits.append(1)
                raise KeyError('snapshot')
            return original(status)
        self.link._on_status = broken
        wait_until(lambda: hits)
        wait_until(lambda: self.link.latest() is None)          # the connection was dropped ...
        self.assertIn('read RPi state (KeyError', self.rig.texts())
        wait_until(self.link.latest)                             # ... and it is back by itself
        self.assertTrue(self.link.thread.is_alive())
        self.assertEqual(self.link.hv_view(), IDLE)


class AlarmListenerTests(unittest.TestCase):
    """rpilink offers its alarms to whoever sets on_alarm; without a listener nothing is raised anywhere."""

    def rig(self, **options):
        rig = Rig(auto_sync=False, **options)
        self.addCleanup(rig.close)
        heard = []
        rig.link.on_alarm = lambda *call: heard.append(call)
        rig.connected()
        return rig, heard

    def test_an_old_flow_and_a_nano_fault_are_raised_and_cleared(self):
        rig, heard = self.rig(flow='verified')
        fallback = ('raise', 'rpi_flow_fallback', 3, 'RPi', 'RPi Set to the old measurement process: Background 3 sheets, click to make approx. Stop automatically after 11 Discharge in seconds', 'condition')
        wait_until(lambda: fallback in heard)
        rig.link.command('sim_fault', fault='TRIGGER_TIMEOUT')
        fault = ('raise', 'nano_fault', 2, 'high pressure', 'Nano Fault: Trigger timeout. Ask maintenance personnel to go to ”Repair › Plasma Parameters” cleared.', 'condition')
        wait_until(lambda: fault in heard)
        rig.link.command('clear_fault')
        wait_until(lambda: ('clear', 'nano_fault') in heard)
        time.sleep(2.2)
        self.assertEqual(heard, [fallback, fault, ('clear', 'nano_fault')])     # each change once, nothing repeated

    def test_a_lost_connection_is_raised_when_it_stays_away_and_cleared_when_it_is_back(self):
        rig, heard = self.rig()
        time.sleep(1.5)
        self.assertEqual(heard, [])                              # the usual state needs no word
        rig.sim.server.shutdown()                                # the RPi goes away: nobody answers on its port any more
        rig.sim.server.server_close()
        rig.link._drop('test:RPi Shut down')
        lost = ('raise', 'rpi_link_lost', 3, 'RPi', 'RPi The connection was interrupted and the program is reconnecting.', 'condition')
        wait_until(lambda: lost in heard, 20)
        other = SimServer(40)                                    # ...and comes back (here: on another port)
        self.addCleanup(other.close)
        rig.store.save_config({'rpi_host': '127.0.0.1', 'rpi_port': other.port, 'token': ''})
        rig.link.wake.set()
        wait_until(lambda: ('clear', 'rpi_link_lost') in heard, 20)
        self.assertEqual(heard, [lost, ('clear', 'rpi_link_lost')])

    def test_a_program_without_a_flow_is_raised_and_a_listener_that_fails_harms_nothing(self):
        rig = Rig(auto_sync=False)
        self.addCleanup(rig.close)
        rig.connected()
        with rig.link.lock:
            rig.link.hello = {key: value for key, value in rig.link.hello.items() if key != 'flow'}
        heard = []

        def listener(*call):
            heard.append(call)
            raise RuntimeError('the alarm list is broken')
        rig.link.on_alarm = listener                             # attached late: what holds is still told
        old = ('raise', 'rpi_old', 3, 'RPi', 'RPi is still an old version: please double-click first Install.cmd renew RPi', 'condition')
        wait_until(lambda: heard.count(old) >= 2)                # not heard (it raised): told again at the next status
        seen = rig.link.status_mono
        wait_until(lambda: rig.link.status_mono != seen)         # and the connection goes on
        self.assertEqual(rig.link.view()['connected'], True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
