"""pc/runner.py and pc/steps.py ’s automatic test: schedule measurement, each round of process, pause Modify the configuration file of/continue/Stop, blank period, standard calibration
 (SPEC B.5, B.6, B.12, G; AMENDMENTS M5.2, M5.6, M11) .

    python tests/test_runner.py
    python tests/test_runner.py StopTests

all on both emulators (V7 own pump control board simulator,test_tcpd of leave RPi,only connect 127.0.0.1) .”Discharge” here
are all simulated RPi Counting time; none of this computer’s ports will be opened or listed.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import core_testing as ct  # noqa: E402  (sets PLASMIC_NO_SERIAL=1 before anything else is loaded)
import devices  # noqa: E402
import pump_testing as pt  # noqa: E402
import recipe  # noqa: E402
import rpilink  # noqa: E402
import runner  # noqa: E402
import steps  # noqa: E402
import store as store_module  # noqa: E402
from devices import Blocked  # noqa: E402
from plasmic_client import PlasmicError  # noqa: E402
from pumpboard import PumpBoardError  # noqa: E402

T_TICKS = 'You must first check each item in the confirmation window'
T_RESTART = 'The program is restarted, the schedule has been interrupted and will not automatically resume. Please restart the schedule after confirming the device.'
DRAIN = ct.csv('pump_on,M8,40,mL/min,discharge', 'delay,,{seconds},s,Row', 'pump_off,M8,,,')
EXAMPLE = ct.csv('dose_weight,M9,2,g,join in CuSO₄ solution', 'dose_volume,M2,1,mL,add water', 'delay,,0.3,s,and other liquid levels are stable',
                 'scale_read,,,,Weight after adding liquid', 'plasma_run,,,,discharge＋capture spectrum', 'pump_on,M8,40,mL/min,discharge (OUT) ',
                 'delay,,0.5,s,Row', 'pump_off,M8,,,')
SCHEDULE_KEYS = {'version', 'id', 'status', 'created_at', 'finished_at', 'operator', 'recipe', 'uses_pump', 'uses_rpi',
                 'uses_plasma', 'confirmed', 'voltage_v', 'estimate_seconds', 'spec', 'planned_total', 'last_slot_at',
                 'next_index', 'next_slot_at', 'slots', 'counts', 'error', 'kind', 'calibration_id', 'blank'}
ROUND_VIEW_KEYS = {'id', 'rev', 'status', 'schedule_id', 'slot', 'slots_planned', 'operator', 'recipe', 'started_at',
                   'finished_at', 'step', 'step_count', 'round', 'rounds', 'phase', 'phases', 'current', 'uses_plasma',
                   'uses_pump', 'paused_pumps', 'plasma_job', 'error', 'stop_report', 'steps_window', 'mode',
                   'sample_kind', 'prompt', 'measurements'}
ROUND_KEYS = {'id', 'version', 'status', 'demo', 'started_at', 'finished_at', 'error', 'rev', 'schedule_id', 'slot',
              'slots_planned', 'planned_at', 'operator', 'sample_kind', 'recipe', 'step_count', 'rounds', 'phases', 'steps',
              'plasma', 'weights', 'versions', 'stop_report', 'paused', 'messages', 'mode', 'calibration_id', 'prompt',
              'measurements'}
STEP_KEYS = {'n', 'line', 'action', 'name', 'target', 'target_label', 'value', 'unit', 'note', 'round', 'rounds', 'phase',
             'text', 'motor', 'motors', 'amounts', 'planned_s', 'status', 'started_at', 'finished_at', 'result', 'error'}


def setUpModule():
    ct.set_up()


def tearDownModule():
    ct.tear_down()


class OwnedLock:
    """Stands where a lock is and knows whether the calling thread holds it."""

    def __init__(self):
        self._lock = threading.RLock()
        self._mine = threading.local()

    def acquire(self, *args, **kwargs):
        got = self._lock.acquire(*args, **kwargs)
        if got:
            self._mine.depth = getattr(self._mine, 'depth', 0) + 1
        return got

    def release(self):
        self._mine.depth -= 1
        self._lock.release()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()

    def held(self):
        return getattr(self._mine, 'depth', 0) > 0


class RunTests(ct.Case):
    def test_plasma_only_with_no_pump_board(self):
        """“Discharge measurement only” with the board off: one slot, the spectra stored once, the round record complete."""
        rig = self.rig(pump='off')
        preview = rig.runner.preview(rig.body())
        self.assertEqual(set(preview), {'spec', 'planned_total', 'last_slot_at', 'estimate_seconds', 'recipe', 'voltage_v',
                                        'flow', 'warnings', 'blockers', 'needs', 'blank'})
        self.assertEqual((preview['planned_total'], preview['voltage_v'], preview['blockers'], preview['warnings'], preview['blank']),
                         (1, 100, [], [], None))
        self.assertEqual(preview['recipe'], {'id': rig.seed, 'name': 'Only discharge measurement', 'step_count': 1, 'rounds': 1, 'uses_pump': False,
                                             'uses_rpi': True, 'uses_plasma': True, 'liquids': [], 'warnings': []})
        self.assertEqual(preview['needs'], {'high_voltage': True, 'liquid': False, 'hv_off_checked': False, 'hv_ack_text': ''})
        self.assertEqual(preview['estimate_seconds'], rig.rpi.latest()['estimate_s'])
        self.assertEqual((preview['flow']['name'], preview['flow']['background_frames']), ('unified', 1))
        sent = rig.sim_requests()
        # (Ticks the dialog did not have to ask for count for nothing: this Recipe uses no pump, and the RPi is there.)
        reply = rig.start(confirm={'high_voltage': True, 'liquid': True, 'hv_off_checked': True})
        self.assertEqual((reply['accepted'], reply['schedule']['status'], reply['schedule']['planned_total']), (True, 'waiting', 1))
        done = rig.finished()
        self.assertEqual(set(done), SCHEDULE_KEYS)
        self.assertEqual((done['status'], done['error'], done['counts'], done['kind'], done['operator']),
                         ('completed', '', {'done': 1}, 'schedule', ct.OPERATOR))
        self.assertEqual(done['recipe'], {'id': rig.seed, 'name': 'Only discharge measurement', 'sha256': rig.store.recipe(rig.seed)['sha256']})
        self.assertEqual(done['confirmed'], {'high_voltage': True, 'liquid': False, 'hv_off_checked': False})
        self.assertEqual((done['uses_pump'], done['uses_rpi'], done['uses_plasma'], done['voltage_v']), (False, True, True, 100))
        self.assertEqual(len(done['slots']), 1)
        slot = done['slots'][0]
        self.assertEqual((slot['index'], slot['status'], slot['kind'], slot['note']), (0, 'done', 'sample', ''))
        self.assertEqual(sent.count('plasma_run'), 1)
        # The spectra: stored once, by the round (the link's own thread leaves a claimed job alone).
        time.sleep(0.5)
        runs = rig.store.list_runs()
        self.assertEqual(len(runs), 1)
        meta = runs[0]
        self.assertEqual((meta['job'], meta['round_id'], meta['recipe_name'], meta['operator'], meta['slot'], meta['source']),
                         (slot['job'], slot['round_id'], 'Only discharge measurement', ct.OPERATOR, 1, 'round'))
        self.assertEqual((meta['flow'], meta['background_frames'], meta['signal_frames']), ('unified', 1, 8))
        self.assertFalse(rig.said('Discharge completed, spectrum has been archived'))
        # The round record on disk.
        record = rig.round(slot['round_id'])
        self.assertLessEqual(ROUND_KEYS, set(record))
        self.assertNotIn('board', record)            # the Recipe does not use the pump board
        self.assertEqual((record['status'], record['error'], record['mode'], record['sample_kind'], record['slot'],
                          record['slots_planned'], record['schedule_id'], record['operator'], record['demo']),
                         ('completed', '', 'schedule', 'sample', 1, 1, done['id'], ct.OPERATOR, True))
        self.assertEqual(record['recipe'], dict(done['recipe'], csv=ct.PLASMA_ONLY))
        self.assertEqual(record['plasma']['parameters'], {'Ontime': 25.0, 'Offtime': 5, 'Cycle': 25, 'Loopnum': 8,
                                                          'Integration_Time': 1000, 'Spectra_Interval': 1500, 'Voltage': 100})
        self.assertEqual(record['plasma']['flow'], 'unified')
        self.assertEqual(len(record['plasma']['jobs']), 1)
        job = record['plasma']['jobs'][0]
        self.assertEqual((job['job'], job['kind'], job['state'], job['saved'], job['voltage_v'], job['background_frames'],
                          job['signal_frames'], job['integration_ms']), (slot['job'], 'plasma', 'done', True, 100, 1, 8, 1000))
        self.assertEqual(record['versions'], {'pc': '2.0.0', 'rpi': rig.rpi.hello['version'], 'v7_backend': None})
        self.assertEqual((record['stop_report'], record['paused'], record['weights']), (None, [], []))
        self.assertEqual(len(record['steps']), 1)
        step = record['steps'][0]
        self.assertEqual(set(step), STEP_KEYS)
        self.assertEqual((step['action'], step['status'], step['error'], step['text']), ('plasma_run', 'done', '', 'discharge＋capture spectrum'))
        self.assertEqual(set(step['result']), {'job', 'voltage_v', 'flow', 'background_frames', 'signal_frames', 'wait_s',
                                               'peak_nm', 'measurement'})
        self.assertEqual((step['result']['job'], step['result']['flow']), (slot['job'], 'unified'))
        self.assertTrue(record['started_at'] <= step['started_at'] <= step['finished_at'] <= record['finished_at'])
        # The run log, the state for the page, the event log.
        row = rig.store.runlog(limit=1)[0]
        self.assertEqual((row['kind'], row['status'], row['round_id'], row['spectra'], row['slot'], row['slots_planned'],
                          row['recipe_name'], row['operator'], row['sample_kind'], row['note']),
                         ('round', 'completed', record['id'], [slot['job']], 1, 1, 'Only discharge measurement', ct.OPERATOR, 'sample', ''))
        self.assertRegex(row['result_text'], r'^Uncorrected · Highest Peak \d+\.\d nm$')
        self.assertEqual(row['measurements'], record['measurements'])
        self.assertEqual((record['measurements'][0]['kind'], record['measurements'][0]['conc_reason']), ('sample', 'Correction has not been applied yet'))
        view = rig.runner.round_view()
        self.assertEqual(set(view), ROUND_VIEW_KEYS | {'calibration_id'})
        self.assertEqual((view['id'], view['status'], view['step'], view['step_count'], view['current'], view['plasma_job'],
                          view['phase'], view['phases'], view['uses_plasma'], view['uses_pump']),
                         (record['id'], 'completed', 1, 1, None, slot['job'], 0, [{'name': 'Measure', 'from': 1, 'to': 1}], True, False))
        self.assertEqual(view['steps_window']['from'], 1)
        item = view['steps_window']['items'][0]
        self.assertEqual(set(item), {'n', 'text', 'phase', 'status', 'started_at', 'finished_at', 'result_text', 'error'})
        self.assertRegex(item['result_text'], r'^highest peak \d+\.\d nm$')
        self.assertIsNone(rig.runner.calibration_view())
        self.assertFalse(rig.runner.busy)
        texts = rig.texts()
        first = done['spec']['first_slot'][11:16]
        self.assertIn(f'Scheduled measurement starts: only discharge measurement, every 2 minutes, first time {first},common 1 times,100 V (High voltage confirmed)', texts)
        self.assertIn('Process completed: only discharge measurement (No. 1/1 times)', texts)
        self.assertEqual(texts[-1], 'Scheduled measurement end (completed): Completed 1 Second-rate')
        started = next(row for row in rig.store.events(limit=50) if row['text'].startswith('Scheduled measurement starts'))
        self.assertEqual((started['actor'], started['level']), (ct.OPERATOR, 'warn'))
        self.assertEqual(rig.alarm_keys(), [])
        self.assertIsNone(rig.devices.owner)

    def test_without_an_analyst_nothing_of_the_concentration_part_runs(self):
        rig = self.rig(pump='off', analyst=False)
        rig.start()
        done = rig.finished()
        self.assertEqual(done['status'], 'completed')
        record = rig.round()
        self.assertEqual(record['measurements'], [])
        self.assertNotIn('measurement', record['steps'][0]['result'])
        self.assertRegex(rig.store.runlog(limit=1)[0]['result_text'], r'^highest peak \d+\.\d nm · signal 8 open$')
        self.assertFalse((rig.data / 'measurements.jsonl').exists())
        with self.assertRaisesRegex(RuntimeError, 'Correction is not enabled'):
            rig.runner.start_calibration({'plan': {}})

    def test_the_pump_and_plasma_example(self):
        """Weigh in, add water, measure, drain: pumps and high voltage are never on together."""
        rig = self.rig(link__simulation_speedup=4)
        recipe_id = rig.publish('Measurement after adding liquid', EXAMPLE)
        preview = rig.runner.preview(rig.body(recipe_id))
        self.assertEqual(preview['needs'], {'high_voltage': True, 'liquid': True, 'hv_off_checked': False, 'hv_ack_text': ''})
        self.assertEqual([(row['motor'], row['ml'], row['g'], row['approx']) for row in preview['recipe']['liquids']],
                         [(9, None, 2.0, False), (2, 1.0, None, False), (8, 0.333, None, True)])
        self.assertEqual(preview['blockers'], [])
        overlap, over = [], threading.Event()

        def watch():
            while not over.is_set():
                if not rig.hv_off() and not rig.pumps_off():
                    overlap.append(time.perf_counter())
                time.sleep(0.005)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        with self.assertRaisesRegex(ValueError, T_TICKS):
            rig.start(recipe_id, confirm={'high_voltage': True})    # the liquid tick is missing
        rig.start(recipe_id)
        done = rig.finished(25)
        over.set()
        watcher.join(2)
        self.assertEqual((done['status'], done['error'], done['counts']), ('completed', '', {'done': 1}))
        self.assertEqual((done['uses_pump'], done['confirmed']['liquid']), (True, True))
        self.assertEqual(overlap, [])
        record = rig.round()
        self.assertEqual([(step['action'], step['status']) for step in record['steps']],
                         [('dose_weight', 'done'), ('dose_volume', 'done'), ('delay', 'done'), ('scale_read', 'done'),
                          ('plasma_run', 'done'), ('pump_on', 'done'), ('delay', 'done'), ('pump_off', 'done')])
        results = [step['result'] for step in record['steps']]
        self.assertEqual(set(results[0]) - {'warning'}, {'target_g', 'dispensed_g', 'error_g', 'within_tolerance', 'topups',
                                                         'elapsed_s', 'baseline_g'})
        self.assertGreaterEqual(results[0]['dispensed_g'], 1.9)
        self.assertEqual(set(results[1]), {'steps', 'sps', 'eta_s', 'elapsed_s'})
        self.assertEqual((results[1]['steps'], results[1]['sps']), (4559, 2825))
        self.assertEqual(results[3]['name'], 'Weight after adding liquid')
        self.assertEqual(record['weights'], [{'n': 4, 'name': 'Weight after adding liquid', 'grams': results[3]['grams']}])
        self.assertGreater(results[3]['grams'], 1.5)
        self.assertEqual(results[5], {'sps': 2825, 'flow_ml_min': 40.0, 'direction': 1, 'planned_s': 0.5})
        self.assertAlmostEqual(results[7]['ran_s'], 0.5, delta=0.35)
        self.assertEqual(len(record['plasma']['jobs']), 1)
        self.assertEqual(record['board']['firmware_tag'], 'p2')      # the firmware of mega/ (pc/megasim.py speaks for it)
        self.assertEqual(set(record['board']), {'simulated', 'device', 'firmware_banner', 'firmware_match', 'firmware_tag', 'hardware_sha256',
                                                'pump_dose_pulses_per_ml',
                                                'pulses_per_ml', 'dose_pulses_per_ml', 'dose_flow_ml_min', 'syringe_ul',
                                                'syringe_speed_ml_s', 'scale_calibration'})
        self.assertEqual(record['versions']['v7_backend'], '5.0.0')
        self.assertEqual([phase['name'] for phase in record['phases']], ['Add liquid', 'Measure', 'Empty'])
        # The run of motor 8 was given to the board as a whole: planned time + 5 s, never an endless command.
        commands = rig.tx()
        self.assertIn('P,8,%d,2825' % round(5.5 * 2825), commands)
        self.assertEqual([c for c in commands if c.startswith('V,8,') and not c.endswith(',0')], [])
        # The page gets a window of the step table: the six-row page of the current step and the page after it.
        view = rig.runner.round_view()
        self.assertEqual((view['step'], view['step_count'], view['phase']), (8, 8, 2))
        self.assertEqual((view['steps_window']['from'], [item['n'] for item in view['steps_window']['items']]), (7, [7, 8]))
        self.assertEqual(runner.step_result_text(record['steps'][3]), '%.3f g' % results[3]['grams'])
        self.assertRegex(runner.step_result_text(record['steps'][0]), r'^\d+\.\d\d g (error [+-]\d+\.\d\d g) $')
        self.assertRegex(runner.step_result_text(record['steps'][4]), r'^highest peak \d+\.\d nm$')
        self.assertEqual(runner.step_result_text(record['steps'][2]), '')
        # (A weight that ends a little outside its tolerance is an alarm, not a failure: see FailureTests.)
        self.assertEqual([key for key in rig.alarm_keys() if not key.startswith('weight_tolerance:')], [])

    def test_what_start_refuses_and_in_which_order(self):
        rig = self.rig()
        wait_id = rig.publish('Wait three seconds', ct.csv('delay,,3,s,'))
        pump_id = rig.publish('discharge', DRAIN.format(seconds=0.3))
        nothing = {'recipe_id': 'no-such-recipe', 'schedule': None, 'confirm': None}
        # 1. an operator
        rig.operator = ''
        with self.assertRaisesRegex(ValueError, '^Please select the operator first$'):
            rig.runner.start(nothing)
        rig.operator = ct.OPERATOR
        # 3. the Recipe
        with self.assertRaisesRegex(ValueError, '^Recipe does not exist$'):
            rig.runner.start(nothing)
        with self.assertRaises(KeyError):
            rig.runner.preview(rig.body('0' * 32))
        with self.assertRaisesRegex(ValueError, 'The schedule setting format is incorrect.'):
            rig.runner.preview(None)
        # 4. every tick the Recipe needs — before the form is looked at
        for confirm in ({}, {'high_voltage': 'yes'}, {'high_voltage': 1}, {'liquid': True}):
            with self.assertRaisesRegex(ValueError, T_TICKS):
                rig.runner.start(rig.body(confirm=confirm, interval=0))
        with self.assertRaisesRegex(ValueError, T_TICKS):
            rig.start(pump_id, confirm={'high_voltage': True})
        # 5. the start checks, with every reason and its fix
        rig.supply(True)
        with self.assertRaises(Blocked) as caught:
            rig.runner.start(rig.body(interval=0))
        self.assertEqual(str(caught.exception), 'Cannot start scheduling now')
        self.assertEqual(caught.exception.reasons, [{'code': 'rpi_not_ready', 'text': 'The pre-discharge inspection failed:HSPY power supply is ON',
                                                     'fix': 'Press ”Stop” at the bottom STOP”'}])
        self.assertEqual(rig.runner.preview(rig.body())['blockers'], caught.exception.reasons)
        with self.assertRaises(Blocked) as caught:
            rig.start(pump_id)
        self.assertEqual(caught.exception.reasons, [{'code': 'hv_on', 'text': 'high voltage power supply is ON', 'fix': 'Press ”Stop” at the bottom STOP”'}])
        rig.supply(False)
        # 6. the form, and the interval against the Recipe's own waits
        for change, text in (({'interval': 0}, '1–1440'), ({'interval': 1.5}, '1–1440'), ({'count': 0}, '1–1000')):
            with self.assertRaisesRegex(ValueError, text):
                rig.start(**change)
            with self.assertRaisesRegex(ValueError, text):
                rig.runner.preview(rig.body(**change))
        with self.assertRaisesRegex(ValueError, '^interval 2 minute ratio Recipe waiting time 3 Seconds are still short, and it times out every time$'):
            rig.start(wait_id, count=2, interval=2)
        with self.assertRaisesRegex(ValueError, 'times out every time'):
            rig.runner.preview(rig.body(wait_id, count=2, interval=3))
        self.assertEqual(rig.runner.preview(rig.body(wait_id, count=1, interval=1))['warnings'],
                         ['this Recipe has no measurement steps (plasma_run) '])     # measured once: the interval is never used
        self.assertIn('One measurement is estimated to be approximately 20 seconds, longer than the interval: some periods will be skipped because the last time has not ended yet',
                      rig.runner.preview(rig.body(count=2, interval=2))['warnings'])
        self.assertIsNone(rig.schedule())
        self.assertEqual(rig.store.list_runs(), [])
        # 2. one schedule at a time — whatever else the second request holds
        rig.start(count=2, interval=3)
        with self.assertRaisesRegex(RuntimeError, '^Already has a schedule in progress$'):
            rig.runner.start(nothing)
        self.assertTrue(rig.runner.uses_recipe(rig.seed))
        self.assertFalse(rig.runner.uses_recipe(pump_id))
        rig.devices.stop_everything('test')
        self.assertEqual(rig.finished()['status'], 'stopped')
        self.assertFalse(rig.runner.uses_recipe(rig.seed))

    def test_cleaning_without_the_rpi_needs_the_tick_of_the_start_dialog(self):
        rig = self.rig(rpi=False)
        clean_id = rig.publish('automatic cleaning', ct.csv('clean,,,,automatic cleaning'))
        preview = rig.runner.preview(rig.body(clean_id))
        self.assertEqual(preview['needs'], {'high_voltage': False, 'liquid': True, 'hv_off_checked': True,
                                            'hv_ack_text': 'RPi There is no connection, and the program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off.'})
        # A Recipe without a pump step is not asked for it (and gets no sentence for it).
        self.assertEqual(rig.runner.preview(rig.body())['needs'],
                         {'high_voltage': True, 'liquid': False, 'hv_off_checked': False, 'hv_ack_text': ''})
        self.assertEqual((preview['blockers'], preview['voltage_v'], preview['flow']['ok']), ([], None, False))
        with self.assertRaisesRegex(ValueError, T_TICKS):
            rig.start(clean_id, confirm={'liquid': True})
        with self.assertRaisesRegex(ValueError, T_TICKS):
            rig.start(clean_id)                      # the usual ticks do not include it
        with self.assertRaises(Blocked) as caught:   # and a Recipe that measures cannot start at all
            rig.start(confirm={'high_voltage': True})
        self.assertEqual([row['code'] for row in caught.exception.reasons], ['rpi_link'])
        rig.start(clean_id, confirm={'liquid': True, 'hv_off_checked': True})
        done = rig.finished()
        self.assertEqual((done['status'], done['confirmed']), ('completed', {'high_voltage': False, 'liquid': True, 'hv_off_checked': True}))
        self.assertEqual(rig.tx().count('C'), 1)
        self.assertEqual(rig.store.runlog(limit=1)[0]['result_text'], 'No measurement steps')
        self.assertFalse(rig.said('V (High voltage confirmed)'))
        self.assertEqual(rig.round()['plasma']['parameters'], None)

    def test_a_pump_schedule_with_the_supply_switched_off_at_its_mains_needs_the_tick(self):
        """7h. The RPi is connected and the high-voltage supply does not answer — the usual state of the real rig
        between measurements. A Recipe that only pumps runs on the tick of the start dialog."""
        ask = 'The status of the high-voltage power supply cannot be read (the high-voltage power supply/Nano is invalid), the program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off. After the firmware of'
        tick = {'liquid': True, 'hv_off_checked': True}
        rig = self.rig()
        recipe_id = rig.publish('discharge', DRAIN.format(seconds=0.4))
        self.assertEqual(rig.runner.preview(rig.body(recipe_id))['needs'],
                         {'high_voltage': False, 'liquid': True, 'hv_off_checked': False, 'hv_ack_text': ''})
        rig.supply_answers(False)
        self.assertTrue(rig.rpi.connected)
        preview = rig.runner.preview(rig.body(recipe_id))
        self.assertEqual(preview['needs'], {'high_voltage': False, 'liquid': True, 'hv_off_checked': True, 'hv_ack_text': ask})
        self.assertEqual(preview['blockers'], [])
        # Refused without the tick ...
        for confirm in ({'liquid': True}, {'liquid': True, 'hv_off_checked': 'yes'},
                        {'high_voltage': True, 'liquid': True, 'hv_off_checked': False}):
            with self.assertRaisesRegex(ValueError, T_TICKS):
                rig.start(recipe_id, confirm=confirm)
        self.assertIsNone(rig.schedule())
        self.assertEqual([command for command in rig.tx() if command.startswith('P,8,')], [])
        # ... and a Recipe that measures cannot start at all like this: the RPi itself says why.
        with self.assertRaises(Blocked) as caught:
            rig.start(confirm={'high_voltage': True})
        self.assertEqual([row['code'] for row in caught.exception.reasons], ['rpi_not_ready'])
        # With the tick every round runs.
        rig.start(recipe_id, count=2, interval=2, confirm=tick)
        done = rig.finished()
        self.assertEqual((done['status'], done['counts'], done['confirmed']),
                         ('completed', {'done': 2}, {'high_voltage': False, 'liquid': True, 'hv_off_checked': True}))
        self.assertEqual(len([command for command in rig.tx() if command.startswith('P,8,')]), 2)
        self.assertEqual(rig.alarm_keys(), [])
        # A round that is stopped ends as stopped: no stop is sent to a supply that could not answer it.
        long_id = rig.publish('Take longer to discharge', DRAIN.format(seconds=20))
        rig.start(long_id, count=2, interval=30, confirm=tick)
        rig.wait_step('delay')
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the pump to turn')
        rig.runner.request_stop()                    # (not the STOP button)
        done = rig.finished()
        self.assertEqual((done['status'], done['error'], done['counts']), ('stopped', '', {'stopped': 1}))
        self.assertEqual({name: half['result'] for name, half in rig.round()['stop_report'].items()}, {'pump': 'confirmed', 'rpi': 'none'})
        self.assertEqual(rig.alarm_keys(), [])
        self.assertTrue(rig.pumps_off())
        # The supply answers again and reads ON: what was read decides, and the tick is ignored.
        rig.supply_answers(True)
        rig.supply(True)
        self.assertEqual(rig.runner.preview(rig.body(recipe_id))['needs'],
                         {'high_voltage': False, 'liquid': True, 'hv_off_checked': False, 'hv_ack_text': ''})
        with self.assertRaises(Blocked) as caught:
            rig.start(recipe_id, confirm=tick)
        self.assertEqual(caught.exception.reasons, [{'code': 'hv_on', 'text': 'high voltage power supply is ON', 'fix': 'Press ”Stop” at the bottom STOP”'}])
        rig.supply(False)
        rig.start(recipe_id, confirm={'liquid': True})           # it reads off: no tick is asked for
        done = rig.finished()
        self.assertEqual((done['status'], done['confirmed']['hv_off_checked']), ('completed', False))

    def test_an_rpi_with_the_old_program_and_one_set_to_the_old_flow(self):
        with self.one(pump='off') as rig:
            flow = rig.rpi.hello.pop('flow')         # the program of 1.3.0 says nothing about how it measures
            ct.wait_for(lambda: rig.alarm('rpi_old'), 5, 'the alarm about the old program')
            old = {'code': 'rpi_old', 'text': 'RPi is still an old version: please double-click first Install.cmd renew RPi', 'fix': ''}
            self.assertEqual(rig.runner.preview(rig.body())['blockers'], [old])
            with self.assertRaises(Blocked) as caught:
                rig.start()
            self.assertEqual(caught.exception.reasons, [old])
            # It turns old while a schedule runs: the next slot is not started, and nothing is discharged.
            rig.rpi.hello['flow'] = flow
            sent = rig.sim_requests()
            rig.start(count=2, interval=2)
            ct.wait_for(lambda: rig.schedule()['counts'].get('done') == 1, 10, 'the first slot')
            rig.rpi.hello.pop('flow')
            done = rig.finished()
            self.assertEqual((done['status'], done['counts']), ('completed', {'done': 1, 'not_started': 1}))
            self.assertEqual(done['slots'][1]['note'], 'RPi is still an old version: please double-click first Install.cmd renew RPi')
            self.assertEqual(sent.count('plasma_run'), 1)
            self.assertIn('Schedule No. 2 times failed to start:RPi is still an old version: please double-click first Install.cmd renew RPi', rig.texts())
            self.assertEqual(rig.alarm('slot_not_started:')['text'], 'No. 2 times failed to start:RPi is still an old version: please double-click first Install.cmd renew RPi')
            row = rig.store.runlog(limit=1)[0]
            self.assertEqual((row['kind'], row['status'], row['slot'], row['result_text'], row['round_id'], row['spectra']),
                             ('slot', 'not_started', 2, 'Not measured', None, []))
            rig.rpi.hello['flow'] = flow
        with self.one(pump='off', speed=100, sim={'flow': 'verified'}) as rig:
            ct.wait_for(lambda: rig.alarm('rpi_flow_fallback'), 5, 'the alarm about the old flow')
            self.assertEqual(rig.alarm('rpi_flow_fallback')['priority'], 3)
            self.assertEqual(rig.runner.preview(rig.body())['flow']['background_frames'], 3)
            rig.start()
            self.assertEqual(rig.finished()['status'], 'completed')      # allowed, and shown as what it is
            job = rig.round()['plasma']['jobs'][0]
            self.assertEqual((job['background_frames'], rig.round()['plasma']['flow']), (3, 'verified'))


class RecordTests(ct.Case):
    def test_when_the_round_record_is_written(self):
        """At the start, at each device step, at most once a second during waits, and at the end."""
        rig = self.rig()
        writes = []
        real = rig.store.save_round

        def save_round(record):
            writes.append((time.perf_counter(), record['status'], [step['status'] for step in record['steps']]))
            return real(record)

        rig.store.save_round = save_round
        waits = rig.publish('Thirty short waits', ct.csv(*['delay,,0.1,s,'] * 30))
        started = time.perf_counter()
        rig.start(waits)
        self.assertEqual(rig.finished()['status'], 'completed')
        took = time.perf_counter() - started
        self.assertGreaterEqual(took, 3.0)
        self.assertEqual(writes[0][1:], ('running', ['pending'] * 30))
        self.assertEqual(writes[-1][1:], ('completed', ['done'] * 30))
        middle = [row for row in writes if row[1] == 'running'][1:]
        self.assertLessEqual(len(middle), int(took) + 1)             # not sixty times
        self.assertGreaterEqual(len(middle), 2)
        gaps = [after[0] - before[0] for before, after in zip(middle, middle[1:])]
        self.assertTrue(all(gap >= 0.9 for gap in gaps), gaps)
        # Device steps: before each one starts and after each one has ended.
        del writes[:]
        device = rig.publish('four equipment steps', ct.csv('dose_volume,M8,0.2,mL,', 'scale_read,,,,', 'dose_volume,M3,0.2,mL,', 'rpi_status,,,,'))
        rig.start(device)
        self.assertEqual(rig.finished()['status'], 'completed')
        seen = [row[2] for row in writes]
        for number in range(4):
            before = ['done'] * number + ['running'] + ['pending'] * (3 - number)
            after = ['done'] * (number + 1) + ['pending'] * (3 - number)
            self.assertIn(before, seen)
            self.assertIn(after, seen)
        on_disk = rig.round()
        self.assertEqual((on_disk['status'], [step['status'] for step in on_disk['steps']]), ('completed', ['done'] * 4))
        self.assertEqual(on_disk['steps'][3]['result'], {'hv_off': True, 'voltage_v': 0.0})
        # A disk that refuses the file does not stop the round; it is said once.
        rig.store.save_round = lambda record: (_ for _ in ()).throw(OSError('disk full'))
        rig.start(device)
        self.assertEqual(rig.finished()['status'], 'completed')
        self.assertEqual(len([text for text in rig.texts() if text == 'The process record was not archived successfully: the disk is full']), 1)

    def test_no_device_and_no_disk_work_while_a_lock_of_the_core_is_held(self):
        rig = self.rig(speed=20)
        rig.runner.lock = OwnedLock()
        rig.devices.lock = OwnedLock()
        held = []

        def guarded(owner, name, label):
            real = getattr(owner, name)

            def call(*args, **kwargs):
                if rig.runner.lock.held() or rig.devices.lock.held():
                    held.append(label)
                return real(*args, **kwargs)

            setattr(owner, name, call)
            self.addCleanup(setattr, owner, name, real)

        guarded(rig.pump._conn.raw, 'send', 'pump board command')
        guarded(rig.rpi.client, 'request', 'RPi command')
        guarded(rpilink.plasmic_client, 'stop', 'RPi stop')
        for name in ('save_round', 'save_schedule', 'append_runlog', 'event', 'save_run'):
            guarded(rig.store, name, 'store.' + name)
        guarded(rig.alarms, 'raise_', 'alarm')
        recipe_id = rig.publish('Measure after discharge', ct.csv('pump_on,M8,40,mL/min,', 'delay,,1.2,s,', 'pump_off,M8,,,', 'dose_volume,M8,0.3,mL,',
                                               'scale_read,,,,', 'plasma_run,,,,', 'delay,,5,s,'))
        rig.start(recipe_id, count=2, interval=7)
        rig.wait_step('delay')
        time.sleep(0.3)
        rig.runner.pause()
        rig.wait_status('paused')
        rig.devices.view(), rig.runner.round_view(), rig.runner.view()
        rig.runner.resume()
        rig.wait_step('plasma_run', 'done', timeout=15)
        rig.devices.view(), rig.runner.round_view(), rig.runner.calibration_view()
        rig.devices.stop_everything('test')
        done = rig.finished()
        self.assertEqual(done['status'], 'stopped')
        self.assertEqual(held, [])

    def test_the_live_line_of_the_current_step(self):
        rig = self.rig(speed=4)
        recipe_id = rig.publish('Wait, add, measure', ct.csv('delay,,1.5,s,', 'dose_volume,M8,2,mL,', 'plasma_run,,,,'))
        seen = {'delay': [], 'dose_volume': [], 'plasma_run': []}
        rig.start(recipe_id)
        while rig.runner.active:
            view = rig.runner.round_view() or {}
            current = view.get('current')
            if current:
                self.assertEqual(set(current), {'n', 'action', 'text', 'detail', 'started_at', 'eta_s'})
                item = view['steps_window']['items'][current['n'] - view['steps_window']['from']]
                if item['status'] != 'running':      # between two steps: the sentence of the one that comes next
                    self.assertEqual(current['detail'], current['text'])
                elif current['detail'] not in seen[current['action']]:
                    seen[current['action']].append(current['detail'])
            time.sleep(0.03)
        self.assertEqual(rig.finished()['status'], 'completed')
        self.assertTrue(seen['delay'] and all(re.fullmatch(r'Waiting [01]/1\.5 Second', text) for text in seen['delay']), seen['delay'])
        self.assertTrue(any(re.fullmatch(r'Pump 7 · OUT Quantitative 2 mL · already \d+ Second/about \d+ Second', text) for text in seen['dose_volume']),
                        seen['dose_volume'])
        allowed = ['discharge＋capture spectrum', 'Prepare', 'High voltage is on (Not discharged yet) ,Wait for the voltage to arrive', 'captures the background spectrum (high voltage is on, but not discharged yet) After', 'Discharging', 'Turn off high voltage and confirm',
                   'Read spectrum']
        order = [allowed.index(text.split(' · ')[0]) for text in seen['plasma_run']]
        self.assertEqual(order, sorted(order), seen['plasma_run'])
        self.assertTrue(any(re.fullmatch(r'Discharging·Signal \d/8 open', text) for text in seen['plasma_run']), seen['plasma_run'])
        self.assertIn('Prepare', seen['plasma_run'])


class FailureTests(ct.Case):
    """SPEC A.2: any failure -> the step failed, the round `failed`, everything off, the schedule ends."""

    def failed(self, rig, done, fragment, uses_rpi, stops_before):
        self.assertEqual(done['status'], 'failed', done)
        self.assertTrue(done['error'].startswith('No. 1 measurements did not end normally and the schedule has been stopped: No. '), done['error'])
        self.assertIn(fragment, done['error'])
        self.assertEqual((done['counts'], done['slots'][0]['status']), ({'failed': 1}, 'failed'))
        record = rig.round()
        self.assertEqual(record['status'], 'failed')
        self.assertIn(fragment, record['error'])
        statuses = [step['status'] for step in record['steps']]
        self.assertEqual(statuses.count('failed'), 1, statuses)
        self.assertEqual(set(statuses[statuses.index('failed') + 1:]) - {'skipped'}, set())
        failed = record['steps'][statuses.index('failed')]
        self.assertIn('No. %d step (%s) failed:' % (failed['n'], failed['name']), record['error'])
        self.assertEqual(set(record['stop_report']), {'pump', 'rpi'})
        self.assertIn(record['stop_report']['pump']['result'], ('confirmed', 'none'))
        self.assertIn(record['stop_report']['rpi']['result'], ('confirmed', 'none'))
        if uses_rpi:                                 # the RPi is told to switch everything off, once more
            self.assertEqual(record['stop_report']['rpi']['result'], 'confirmed')
            self.assertGreater(rig.sim.controller.stop_requests, stops_before)
        keys = rig.alarm_keys()
        self.assertIn('round_failed:' + record['id'], keys)
        self.assertIn('sched_failed:' + done['id'], keys)
        self.assertEqual(rig.alarm('round_failed:')['priority'], 2)
        self.assertTrue(rig.alarm('round_failed:')['text'].startswith('Process failed: No. '))
        self.assertTrue(rig.alarm('sched_failed:')['text'].startswith('Schedule has stopped: No. 1 measurements did not end normally'))
        texts = rig.texts()
        self.assertTrue(any(text.startswith('process failed:') and fragment in text for text in texts))
        self.assertTrue(any(text.startswith('Scheduled measurement ends (stopped due to failure): Completed 0 times. No. 1 measurements did not end normally') for text in texts), texts[-3:])
        row = rig.store.runlog(limit=1)[0]
        self.assertEqual((row['status'], row['round_id']), ('failed', record['id']))
        self.assertIsNone(rig.devices.owner)
        self.assertTrue(rig.pumps_off())
        self.assertTrue(rig.hv_off())

    def case(self, text, fault, fragment, uses_rpi=False, **options):
        with self.one(**options) as rig:
            plan = self.plan()
            recipe_id = rig.publish('test', text)
            stops = rig.sim.controller.stop_requests
            fault(rig, plan)
            rig.start(recipe_id, count=2, interval=2)
            done = rig.finished()
            plan.restore()
            self.failed(rig, done, fragment, uses_rpi, stops)
            self.assertEqual(len(done['slots']), 1)  # the second slot never came
            return rig.round()

    def test_pump_steps(self):
        self.case(DRAIN.format(seconds=0.3), lambda rig, plan: plan.answer('P,8', ['ERR,arg']),
                  'No. 1 step (start pump (forward)) failed: the pump control board does not accept this value')
        self.case(DRAIN.format(seconds=0.3), lambda rig, plan: plan.answer('V,8,0', ['ERR,arg']),
                  'No. 3 step (stop pump) failed:Pump 7 · OUT Did not stop according to the instruction, all have stopped')
        self.case(ct.csv('dose_volume,M8,1,mL,'), lambda rig, plan: plan.answer('P,8', ['ERR,arg']),
                  'No. 1 step (quantitate (volume)) failed: the pump control board does not accept this value')
        self.case(ct.csv('delay,,0.1,s,', 'dose_reverse,M3,1,mL,'), lambda rig, plan: plan.answer('P,3', ['ERR,arg']),
                  'No. 2 step (quantitate (volume, invert)) failed: the pump control board does not accept this value')
        # (the board runs the weight dose itself: the order is G,<motor>,…)
        record = self.case(ct.csv('dose_weight,M8,5,g,'), lambda rig, plan: plan.answer('G,8', ['ERR,arg']),
                           'No. 1 step (quantitation (weight)) failed: the pump control board does not accept this value')
        self.assertEqual(record['weights'], [])

    def test_cleaning_syringe_and_scale_steps(self):
        self.case(ct.csv('clean,,,,'), lambda rig, plan: plan.answer('C', ['ERR,cleaning_active']),
                  'No. 1 step (automatic cleaning) failed: this action cannot be performed during automatic cleaning')
        self.case(ct.csv('syringe_init,,,,'),
                  lambda rig, plan: plan.answer('Y,INIT', ['PUMP,proto=ascii,ready=0,error=9']),
                  'No. 1 step (syringe pump initialization) failed: piston overload')
        self.case(ct.csv('syringe_init,,,,', 'syringe_aspirate,CH1,0.2,mL,', 'syringe_dispense,CH1,0.2,mL,'),
                  lambda rig, plan: plan.answer('Y,ASPUL', ['PUMP,proto=ascii,ready=1,error=2']),
                  'No. 2 step (syringe pump pumping) failed: value out of range')
        self.case(ct.csv('syringe_init,,,,', 'syringe_speed,,0.4,mL/s,', 'syringe_aspirate,CH2,0.2,mL,', 'syringe_dispense,CH2,0.2,mL,'),
                  lambda rig, plan: plan.answer('Y,DISPUL', ['PUMP,proto=ascii,ready=1,error=10']),
                  'No. 4 step (syringe pump injection) failed: valve overload')
        # (The initialise sets the saved speed with the same command: the Recipe's own row is the second one.)
        self.case(ct.csv('syringe_init,,,,', 'syringe_speed,,0.5,mL/s,', 'syringe_aspirate,CH1,0.2,mL,', 'syringe_dispense,CH1,0.2,mL,'),
                  lambda rig, plan: plan.answer('Y,SPEED', ['PUMP,proto=ascii,ready=1,error=3'], skip=1),
                  'No. 2 step (syringe pump speed) failed: value out of range')

        def no_reading(rig, plan):
            def scale_read(samples=5):
                raise PumpBoardError('device', 'scale has no reading value')
            rig.pump.scale_read = scale_read

        self.case(ct.csv('scale_read,,,,empty cup'), no_reading, 'No. 1 step (recording weight) failed: the scale has no reading value')

    def test_rpi_steps(self):
        def supply_on_soon(rig, plan):
            threading.Timer(0.2, lambda: setattr(rig.sim.backend, 'run_stop', 1)).start()

        self.case(ct.csv('delay,,0.6,s,', 'rpi_status,,,,'), supply_on_soon, 'No. 2 step (checking that the high voltage is off) failed: the high voltage power supply is ON',
                  uses_rpi=True, pump='off')

        def nano_fault(rig, plan):
            rig.sim.backend.state, rig.sim.backend.fault = 'FAULT', 'HEARTBEAT_LOST'

        self.case(ct.csv('nano_prepare,,,,'), nano_fault, 'No. 1 step (synchronize parameters to Nano) failed:', uses_rpi=True, pump='off')

        def tool_fails(name):
            def fault(rig, plan):
                def broken(*args, **kwargs):
                    raise RuntimeError('Simulation: Measurement tool failed')
                setattr(rig.sim.backend, name, broken)
            return fault

        record = self.case(ct.PLASMA_ONLY, tool_fails('plasma_run'), 'No. 1 step (discharge＋Acquisition of spectrum) failed: Simulation: Measurement tool failed', uses_rpi=True)
        self.assertEqual([(job['kind'], job['state'], job['saved']) for job in record['plasma']['jobs']], [('plasma', 'failed', False)])
        self.assertEqual(record['measurements'], [])
        self.case(ct.csv('spectrum_capture,,,,'), tool_fails('spectrum'), 'No. 1 step (spectrometer check (no discharge)) failed: Simulation: Measurement tool failed',
                  uses_rpi=True, pump='off')

        def stop_fails_once(rig, plan):
            real, calls = rpilink.plasmic_client.stop, []

            def stop(host, port, **more):
                calls.append(1)
                if len(calls) == 1:
                    raise OSError('Simulation: Unable to connect')
                return real(host, port, **more)

            self.patch(rpilink.plasmic_client, 'stop', stop)

        self.case(ct.csv('power_off,,,,'), stop_fails_once, 'No. 1 step (turn off high voltage) failed: the stop command cannot be sent RPi', uses_rpi=True, pump='off')

    def test_the_steps_that_succeed(self):
        """spectrum_capture, nano_prepare, power_off, syringe moves and a reverse run: what they leave in the record."""
        with self.one() as rig:
            recipe_id = rig.publish('Various steps', ct.csv(
                'rpi_status,,,,', 'nano_prepare,,,,', 'spectrum_capture,,,,', 'power_off,,,,', 'nano_stop,,,,',
                'syringe_init,,,,', 'syringe_speed,,0.5,mL/s,', 'syringe_aspirate,CH2,0.2,mL,', 'syringe_dispense,CH1,0.2,mL,',
                'pump_reverse,M3,20,mL/min,', 'delay,,0.3,s,', 'pump_off,M3,,,', 'clean,,,,'))
            sent = rig.sim_requests()
            rig.start(recipe_id)
            done = rig.finished(25)
            self.assertEqual((done['status'], done['error']), ('completed', ''))
            record = rig.round()
            results = {step['action']: step['result'] for step in record['steps']}
            self.assertEqual(results['nano_prepare'], {'prepared': True})
            self.assertEqual(set(results['spectrum_capture']), {'job', 'integration_ms', 'frames', 'peak_nm'})
            self.assertEqual((results['spectrum_capture']['integration_ms'], results['spectrum_capture']['frames']), (1000, 3))
            self.assertTrue(results['spectrum_capture']['job'].startswith('CAP-'))
            self.assertEqual((results['power_off'], results['nano_stop']), ({'stopped': True}, {'stopped': True}))
            self.assertEqual(results['syringe_init'], {'initialised': True, 'speed_ml_s': 0.4})
            self.assertEqual(results['syringe_speed'], {'speed_ml_s': 0.5})
            self.assertEqual(results['syringe_aspirate'], {'position': 2, 'volume_ul': 200, 'speed_ml_s': 0.5, 'contents_ul': 200})
            self.assertEqual(results['syringe_dispense'], {'position': 1, 'volume_ul': 200, 'speed_ml_s': 0.5, 'contents_ul': 0})
            self.assertEqual(results['pump_reverse'], {'sps': 1412, 'flow_ml_min': 20.0, 'direction': -1, 'planned_s': 0.3})
            self.assertEqual(results['clean']['started'], True)
            self.assertEqual([(job['kind'], job['saved']) for job in record['plasma']['jobs']], [('capture', True)])
            self.assertEqual((sent.count('spectrum'), sent.count('prepare'), sent.count('stop'), sent.count('plasma_run')), (1, 1, 2, 0))
            self.assertEqual(rig.store.list_runs()[0]['source'], 'round')
            self.assertEqual(rig.store.runlog(limit=1)[0]['result_text'], 'No measurement steps')
            self.assertIn('P,3,-%d,1412' % round(5.3 * 1412), rig.tx())

    def test_a_weight_outside_its_tolerance_is_recorded_not_failed(self):
        rig = self.rig(link__simulation_speedup=20)  # the simulated liquid arrives far too fast to stop in time
        recipe_id = rig.publish('weighing', ct.csv('dose_weight,M9,2,g,join in'))
        rig.start(recipe_id)
        done = rig.finished()
        self.assertEqual(done['status'], 'completed')
        record = rig.round()
        result = record['steps'][0]['result']
        self.assertEqual((result['within_tolerance'], record['steps'][0]['status']), (False, 'done'))
        alarm = rig.alarm('weight_tolerance:%s:1' % record['id'])
        self.assertEqual((alarm['priority'], alarm['area'], alarm['text']),
                         (3, 'scales', 'Weighing quantity exceeds the allowable range: target 2 g,Actual %.2f g' % result['dispensed_g']))
        self.assertEqual(rig.store.runlog(limit=1)[0]['result_text'], 'CuSO₄ %.2f g' % result['dispensed_g'])


class StopTests(ct.Case):
    """SPEC B.5 "STOP inside each step type": the round ends `stopped` and everything is off."""

    def stop_during(self, text, action, extra=None, interval=2, **options):
        with self.one(**options) as rig:
            recipe_id = rig.publish('test', text)
            rig.start(recipe_id, count=3, interval=interval)
            rig.wait_step(action)
            if extra is not None:
                extra(rig)
            self.assertEqual(rig.devices.owner['kind'], 'round')
            started = time.perf_counter()
            report = rig.devices.stop_everything(ct.OPERATOR)
            self.assertEqual((report['accepted'], report['schedule_ended'], report['round_stopping']), (True, True, True))
            self.assertIn(report['pump']['result'], ('confirmed', 'none'))
            self.assertIn(rig.schedule()['status'], ('stopping', 'stopped'))
            done = rig.finished()
            took = time.perf_counter() - started
            self.assertEqual((done['status'], done['error'], done['counts']), ('stopped', '', {'stopped': 1}))
            self.assertEqual(len(done['slots']), 1)
            record = rig.round()
            self.assertEqual(record['status'], 'stopped')
            stopped = next(step for step in record['steps'] if step['action'] == action and step['status'] != 'done')
            self.assertEqual(stopped['status'], 'stopped')
            self.assertEqual({step['status'] for step in record['steps'][stopped['n']:]} - {'skipped'}, set())
            self.assertEqual(set(record['stop_report']), {'pump', 'rpi'})
            self.assertEqual(devices.unconfirmed(record['stop_report']), [])
            self.assertTrue(any(text.startswith('Process stopped: Test (page 1/3 times)') for text in rig.texts()))
            self.assertIn('Scheduled measurement ends (stopped): Complete 0 Second-rate', rig.texts())
            self.assertIn('Stop pressed: Pump %s; high voltage readback OFF' % ('Stopped' if rig.pump.in_use else 'not used'), rig.texts())
            self.assertEqual([key for key in rig.alarm_keys() if key.startswith(('round_failed', 'sched_failed'))], [])
            self.assertIsNone(rig.devices.owner)
            self.assertEqual(rig.store.runlog(limit=1)[0]['status'], 'stopped')
            self.assertTrue(rig.pumps_off())
            self.assertTrue(rig.hv_off())
            return rig, record, took

    def test_stop_during_a_wait_and_while_a_pump_runs(self):
        _rig, _record, took = self.stop_during(ct.csv('delay,,20,s,'), 'delay', interval=30, pump='off')
        self.assertLess(took, 3.0)

        def pump_turns(rig):
            ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the pump to turn')

        rig, record, took = self.stop_during(DRAIN.format(seconds=20), 'delay', pump_turns, interval=30)
        self.assertEqual([step['status'] for step in record['steps']], ['done', 'stopped', 'skipped'])
        self.assertLess(took, 4.0)

    def test_stop_during_doses(self):
        def moving(rig):
            ct.wait_for(lambda: rig.board.mstate['B'] != 'idle', 5, 'the dose to move')

        _rig, _record, took = self.stop_during(ct.csv('dose_volume,M8,30,mL,', 'delay,,1,s,'), 'dose_volume', moving)
        self.assertLess(took, 4.0)
        _rig, _record, took = self.stop_during(ct.csv('dose_weight,M8,500,g,'), 'dose_weight', moving)
        self.assertLess(took, 8.0)

    def test_stop_during_cleaning_and_a_syringe_move(self):
        def cleaning(rig):
            ct.wait_for(lambda: rig.board.cleaning, 5, 'cleaning to begin')

        self.stop_during(ct.csv('clean,,,,'), 'clean', cleaning, cleaning__water_lead_s=6, cleaning__overlap_s=6, cleaning__out_tail_s=6)
        rig, record, took = self.stop_during(
            ct.csv('syringe_init,,,,', 'syringe_aspirate,CH1,4,mL,', 'syringe_dispense,CH1,4,mL,'), 'syringe_aspirate')
        self.assertEqual([step['status'] for step in record['steps']], ['done', 'stopped', 'skipped'])
        self.assertLess(took, 5.0)

    def test_stop_during_a_discharge(self):
        def supply_on(rig):
            ct.wait_for(lambda: rig.sim.backend.run_stop == 1, 10, 'the pretend supply to come on')

        with self.one(speed=4, pump='off') as rig:
            sent = rig.sim_requests()
            rig.start(count=3, interval=2)
            rig.wait_step('plasma_run')
            supply_on(rig)
            report = rig.devices.stop_everything(ct.OPERATOR)
            job = rig.runner.round['plasma_job']
            self.assertEqual(report['rpi'], {'result': 'cancel_sent', 'error': '', 'job': job})
            self.assertEqual(report['pump']['result'], 'none')
            done = rig.finished()
            self.assertEqual((done['status'], done['counts'], done['slots'][0]['job']), ('stopped', {'stopped': 1}, job))
            record = rig.round()
            self.assertEqual((record['status'], record['steps'][0]['status'], record['error']), ('stopped', 'stopped', 'Stop has been pressed. The work received an interruption signal. 15'))
            self.assertEqual([(row['job'], row['state'], row['saved']) for row in record['plasma']['jobs']], [(job, 'stopped', False)])
            self.assertEqual(record['stop_report']['rpi']['result'], 'confirmed')    # the round's own check at its end
            self.assertEqual(sent.count('plasma_run'), 1)
            self.assertEqual(rig.store.list_runs(), [])
            self.assertEqual(rig.store.runlog(limit=1)[0]['result_text'], 'Not measured')
            self.assertEqual(rig.devices.hv_active, False)
            self.assertIsNone(rig.alarm('hv_stop_unconfirmed'))

    def test_two_pages_one_program(self):
        """B.12 "Two browser tabs": the same state for both, one schedule, STOP from either."""
        rig = self.rig(pump='off')
        answers = []

        def press_start():
            try:
                answers.append(rig.start(count=2, interval=3)['accepted'])
            except RuntimeError as exc:
                answers.append(str(exc))

        tabs = [threading.Thread(target=press_start) for _ in range(2)]
        for tab in tabs:
            tab.start()
        for tab in tabs:
            tab.join(10)
        self.assertEqual(sorted(answers, key=str), [True, 'Already has a schedule in progress'])
        self.assertEqual(rig.runner.view(), rig.runner.view())
        ct.wait_for(lambda: rig.schedule()['counts'].get('done') == 1 and rig.schedule()['status'] == 'waiting', 10, 'the first slot')
        first, second = rig.devices.stop_everything('Wang Xiaoming'), rig.devices.stop_everything('Li Dahua')
        self.assertEqual((first['schedule_ended'], second['accepted']), (True, True))
        done = rig.finished()
        self.assertEqual((done['status'], done['counts'], len(done['slots'])), ('stopped', {'done': 1}, 1))
        self.assertIn('Scheduled measurement ends (stopped): Complete 1 Second-rate', rig.texts())
        self.assertEqual(len([text for text in rig.texts() if text.startswith('Stop pressed: ')]), 2)


class PauseTests(ct.Case):
    """SPEC B.5 `hold()`: PAUSE stops the round's pumps; RESUME starts them again for the time that is left."""

    def test_pause_in_a_wait_with_a_running_pump(self):
        rig = self.rig()
        recipe_id = rig.publish('discharge', ct.csv('pump_on,M8,40,mL/min,discharge', 'delay,,3,s,Row', 'pump_off,M8,,,', 'delay,,0.2,s,'))
        with self.assertRaisesRegex(RuntimeError, '^There is currently no schedule that can be paused$'):
            rig.runner.pause()
        with self.assertRaisesRegex(RuntimeError, '^Schedule is not in pause state$'):
            rig.runner.resume()
        began = time.perf_counter()
        rig.start(recipe_id)
        rig.wait_step('delay')
        time.sleep(1.0)
        self.assertEqual(rig.board.mstate['B'], 'move')
        with self.assertRaisesRegex(RuntimeError, '^Schedule is not in pause state$'):
            rig.runner.resume()
        self.assertEqual(rig.runner.pause(), {'accepted': True, 'status': 'pausing'})
        view = rig.wait_status('paused')
        self.assertEqual(rig.board.mstate['B'], 'idle')              # the round's pump was stopped
        self.assertIn('V,8,0', rig.tx())
        state = rig.runner.round_view()
        self.assertEqual((state['status'], state['paused_pumps'], view['status']), ('paused', ['Pump 7 · OUT'], 'paused'))
        self.assertEqual(rig.devices.owner['kind'], 'round')         # the round keeps the equipment
        self.assertEqual(rig.runner.ownership(), 'paused_round')
        self.assertTrue(rig.runner.busy)
        self.assertEqual(rig.devices.view()['interlock']['manual']['reasons'][0],
                         {'code': 'round_active', 'text': 'The process is paused: Please click ”Continue” at the bottom RESUME”or“stop STOP”', 'fix': ''})
        with self.assertRaisesRegex(RuntimeError, '^There is currently no schedule that can be paused$'):
            rig.runner.pause()
        self.assertIn('The process has been suspended; stopped Pump 7 · OUT,will restart when continuing', rig.texts())
        time.sleep(1.5)                              # paused time does not count
        self.assertEqual(rig.board.mstate['B'], 'idle')
        self.assertEqual(rig.runner.round_view()['steps_window']['items'][1]['status'], 'running')   # still in its wait
        # Not while the high voltage is on (somebody switched it on by hand).
        rig.supply(True)
        with self.assertRaises(Blocked) as caught:
            rig.runner.resume()
        self.assertEqual((str(caught.exception), caught.exception.reasons),
                         ('The schedule cannot be continued now', [{'code': 'hv_on', 'text': 'high voltage power supply is ON', 'fix': 'Press ”Stop” at the bottom STOP”'}]))
        self.assertEqual(rig.schedule()['status'], 'paused')
        rig.supply(False)
        # Nor while it cannot be read: this round was started on the RPi's "off", not on a tick. (RESUME has no
        # dialog to tick anything in, so the reason comes without that hint.)
        rig.supply_answers(False)
        with self.assertRaises(Blocked) as caught:
            rig.runner.resume()
        self.assertEqual(caught.exception.reasons, [{'code': 'hv_unknown', 'text': 'high voltage power supply/Nano is invalid and it cannot be confirmed that the high voltage is off. The object of', 'fix': ''}])
        self.assertEqual(rig.schedule()['status'], 'paused')
        rig.supply_answers(True)
        self.assertEqual(rig.runner.resume(), {'accepted': True, 'status': 'running'})
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the pump to turn again')
        self.assertEqual(rig.runner.round_view()['paused_pumps'], [])
        done = rig.finished()
        took = time.perf_counter() - began
        self.assertEqual(done['status'], 'completed')
        self.assertGreater(took, 3.0 + 1.5)
        record = rig.round()
        self.assertEqual([step['status'] for step in record['steps']], ['done'] * 4)
        self.assertEqual(len(record['paused']), 1)
        self.assertTrue(record['paused'][0]['at'] < record['paused'][0]['resumed_at'])
        self.assertAlmostEqual(record['steps'][2]['result']['ran_s'], 3.0, delta=0.6)        # both parts together
        moves = [command for command in rig.tx() if command.startswith('P,8,')]
        self.assertEqual(len(moves), 2)
        first, second = (int(command.split(',')[2]) for command in moves)
        self.assertEqual(first, 8 * 2825)            # 3 s planned + 5 s
        self.assertAlmostEqual(second / 2825.0, 3.0 - 1.0 + 5.0, delta=0.6)      # what was left of it + 5 s
        texts = rig.texts()
        self.assertLess(texts.index('Pause has been pressed: Pause after completing this step'), texts.index('The process has been suspended; stopped Pump 7 · OUT,will restart when continuing'))
        self.assertLess(texts.index('Pressed to continue'), texts.index('Process continues'))
        self.assertEqual(rig.alarm_keys(), [])

    def test_pause_during_a_dose_waits_for_the_dose(self):
        rig = self.rig()
        recipe_id = rig.publish('Add liquid', ct.csv('dose_volume,M8,3,mL,', 'delay,,0.4,s,'))
        rig.start(recipe_id)
        rig.wait_step('dose_volume')
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the dose to move')
        self.assertEqual(rig.runner.pause()['status'], 'pausing')
        time.sleep(0.8)
        self.assertEqual((rig.schedule()['status'], rig.runner.round_view()['status'], rig.board.mstate['B']),
                         ('pausing', 'pausing', 'move'))     # the dose is not cut short
        rig.wait_status('paused', timeout=10)
        record = rig.runner.round_view()
        self.assertEqual((record['status'], record['steps_window']['items'][0]['status'], record['paused_pumps']),
                         ('paused', 'done', []))
        self.assertIn('Process has been suspended', rig.texts())
        self.assertEqual(rig.runner.resume()['status'], 'running')
        self.assertEqual(rig.finished()['status'], 'completed')
        self.assertEqual(rig.round()['steps'][0]['result']['steps'], 13677)      # the whole dose, in one piece
        self.assertEqual(len([command for command in rig.tx() if command.startswith('P,8,')]), 1)

    def test_pause_while_waiting_for_a_slot(self):
        self.patch(runner, 'PAUSE_GRACE_SECONDS', 0.4)
        rig = self.rig(pump='off', speed=100)
        rig.start(count=3, interval=2)
        ct.wait_for(lambda: rig.schedule()['counts'].get('done') == 1 and rig.schedule()['status'] == 'waiting', 10, 'the first slot')
        self.assertEqual(rig.runner.pause(), {'accepted': True, 'status': 'paused'})
        self.assertEqual(rig.schedule()['status'], 'paused')
        self.assertIsNone(rig.devices.owner)         # nobody holds the equipment: manual work is possible
        self.assertEqual((rig.runner.ownership(), rig.runner.busy), ('paused', False))
        self.assertEqual(rig.devices.view()['interlock']['start']['reasons'][0]['code'], 'schedule_active')
        ct.wait_for(lambda: len(rig.schedule()['slots']) == 2, 6, 'the second slot to pass')
        view = rig.schedule()
        self.assertEqual((view['status'], view['slots'][1]['status'], view['slots'][1]['note'], view['slots'][1]['kind']),
                         ('paused', 'skipped_paused', 'The schedule is in paused state when the period arrives.', 'sample'))
        self.assertEqual(rig.runner.resume(), {'accepted': True, 'status': 'waiting'})
        done = rig.finished()
        self.assertEqual((done['status'], done['counts']), ('completed', {'done': 2, 'skipped_paused': 1}))
        self.assertEqual([slot['status'] for slot in done['slots']], ['done', 'skipped_paused', 'done'])
        rows = rig.store.runlog(limit=3)
        self.assertEqual([(row['kind'], row['slot'], row['status'], row['result_text']) for row in rows][1],
                         ('slot', 2, 'skipped_paused', 'Not measured'))
        self.assertIn('Schedule No. 2 times skipped: schedule is paused', rig.texts())
        self.assertEqual(len(rig.store.list_runs()), 2)

    def test_stop_ends_a_paused_round_and_a_paused_schedule(self):
        with self.one() as rig:
            recipe_id = rig.publish('discharge', DRAIN.format(seconds=10))
            rig.start(recipe_id)
            rig.wait_step('delay')
            rig.runner.pause()
            rig.wait_status('paused')
            report = rig.devices.stop_everything(ct.OPERATOR)
            self.assertEqual((report['schedule_ended'], report['round_stopping']), (True, True))
            done = rig.finished()
            self.assertEqual((done['status'], rig.round()['status']), ('stopped', 'stopped'))
            self.assertEqual(rig.round()['paused_pumps'], [])
            self.assertIsNone(rig.devices.owner)
        with self.one(pump='off', speed=100) as rig:
            rig.start(count=2, interval=3)
            ct.wait_for(lambda: rig.schedule()['counts'].get('done') == 1 and rig.schedule()['status'] == 'waiting', 10, 'the first slot')
            rig.runner.pause()
            self.assertEqual(rig.devices.stop_everything(ct.OPERATOR)['round_stopping'], False)
            self.assertEqual(rig.finished()['status'], 'stopped')


class SlotTests(ct.Case):
    """The slot rules of 1.4.1, kept: slots lie on the time grid, a missed one is skipped, never run late."""

    def test_a_slot_that_passes_while_the_last_round_still_runs_is_skipped(self):
        rig = self.rig()
        recipe_id = rig.publish('Add slowly', ct.csv('dose_volume,M8,4,mL,'))      # about 6.5 s; the slots lie 1 s apart
        rig.start(recipe_id, count=2, interval=1)
        done = rig.finished()
        self.assertEqual((done['status'], done['counts']), ('completed', {'done': 1, 'skipped_overrun': 1}))
        self.assertEqual((done['slots'][1]['status'], done['slots'][1]['note']), ('skipped_overrun', 'No. 1 times have not ended yet. There is no measurement during this period.'))
        self.assertIn('Schedule No. 2 times skipped: The last measurement timed out', rig.texts())
        alarm = rig.alarm('sched_skipped:' + done['id'])
        self.assertEqual((alarm['priority'], alarm['text']), (3, 'Sometimes a segment is skipped because the last one has not ended yet.'))
        row = rig.store.runlog(limit=1)[0]
        self.assertEqual((row['kind'], row['status'], row['slot'], row['note']), ('slot', 'skipped_overrun', 2, 'No. 1 times have not ended yet. There is no measurement during this period.'))
        self.assertEqual(len([command for command in rig.tx() if command.startswith('P,8,')]), 1)   # it was not run late

    def test_three_slots_in_a_row_that_cannot_start_end_the_schedule(self):
        rig = self.rig(pump='off', speed=100)
        sent = rig.sim_requests()
        rig.start(count=6, interval=2)
        ct.wait_for(lambda: rig.schedule()['counts'].get('done') == 1 and rig.schedule()['status'] == 'waiting', 10, 'the first slot')
        rig.rpi.command('sim_fault', fault='HEARTBEAT_LOST')
        rig.fresh()
        done = rig.finished(20)
        self.assertEqual((done['status'], done['counts']), ('failed', {'done': 1, 'not_started': 3}))
        self.assertEqual(done['error'], 'Continuous 3 times: pre-discharge inspection failed:Nano fault lock HEARTBEAT_LOST')
        self.assertIn('HEARTBEAT_LOST', done['slots'][-1]['note'])
        self.assertEqual(sent.count('plasma_run'), 1)
        texts = rig.texts()
        for number in (2, 3, 4):
            self.assertIn(f'Schedule No. {number} times did not start: the pre-discharge inspection failed:Nano fault lock HEARTBEAT_LOST', texts)
        self.assertIn('Scheduled measurement ends (stopped due to failure): Completed 1 times. continuous Completed 3 times: pre-discharge inspection failed:Nano fault lock HEARTBEAT_LOST', texts)
        self.assertFalse(rig.said('The schedule record was not archived successfully.'))
        self.assertEqual(rig.alarm('slot_not_started:' + done['id'])['text'], 'No. 4 times did not start: the pre-discharge inspection failed:Nano fault lock HEARTBEAT_LOST')
        self.assertEqual(rig.alarm('sched_failed:' + done['id'])['priority'], 2)
        self.assertEqual(json.loads((rig.data / 'schedule.json').read_text(encoding='utf-8'))['status'], 'failed')

    def test_a_manual_operation_keeps_a_slot_from_starting(self):
        rig = self.rig()
        rig.start(count=2, interval=2)
        ct.wait_for(lambda: rig.schedule()['counts'].get('done') == 1 and rig.schedule()['status'] == 'waiting', 10, 'the first slot')
        rig.devices.bind(None, None)                 # (how a manual operation could come to hold it is not the point here)
        rig.devices.acquire('manual', 'manual', 'Manual: Pump 7 · OUT')
        rig.devices.bind(rig.runner, None)
        ct.wait_for(lambda: len(rig.schedule()['slots']) == 2, 6, 'the second slot')
        rig.devices.release('manual')
        done = rig.finished()
        self.assertEqual((done['counts'], done['slots'][1]['note']), ({'done': 1, 'not_started': 1}, 'Manual operation is not over yet'))

    def test_a_recipe_that_was_deleted_or_no_longer_fits_the_settings(self):
        rig = self.rig()
        recipe_id = rig.publish('injection', ct.csv('syringe_init,,,,', 'syringe_aspirate,CH1,1.5,mL,', 'syringe_dispense,CH1,1.5,mL,'))
        rig.start(recipe_id, count=2, interval=11)
        ct.wait_for(lambda: rig.schedule()['counts'].get('done') == 1 and rig.schedule()['status'] == 'waiting', 20, 'the first slot')
        rig.pump.apply_settings(1250, 0.1)           # a smaller syringe: 1.5 mL no longer fit (rule R11)
        done = rig.finished(25)
        self.assertEqual(done['counts'], {'done': 1, 'not_started': 1})
        self.assertEqual(done['slots'][1]['note'], 'No. 3 column: The value of the syringe pump should be within 0.001–1.25 between')
        with self.assertRaisesRegex(ValueError, 'The value must be within 0.001–1.25 between'):
            rig.start(recipe_id)
        seed = rig.seed
        rig.start(count=2, interval=2)
        ct.wait_for(lambda: rig.schedule()['counts'].get('done') == 1 and rig.schedule()['status'] == 'waiting', 10, 'the first slot')
        rig.store.delete_recipe(seed)
        done = rig.finished()
        self.assertEqual((done['counts'], done['slots'][1]['note']), ({'done': 1, 'not_started': 1}, 'Recipe does not exist'))


class BlankTests(ct.Case):
    """AMENDMENTS M5.2: blank slots, and the measurement after every plasma_run."""

    def test_the_order_of_blank_and_sample_slots(self):
        rig = self.rig(pump='off', speed=100, hook=lambda info: None)
        blank_id = rig.publish('blank measurement', ct.PLASMA_ONLY)
        wait_id = rig.publish('Just wait', ct.csv('delay,,0.1,s,'))
        for blank, text in (({'recipe_id': '0' * 32, 'every': 2}, '^Blank Recipe does not exist$'),
                            ({'recipe_id': rig.seed, 'every': 2}, '^Blank Recipe Cannot be compared with samples Recipe same$'),
                            ({'recipe_id': blank_id, 'every': 0}, '^Insert blanks for every few samples: must be 1–100 integer$'),
                            ({'recipe_id': blank_id, 'every': 101}, '^Insert blanks for every few samples: must be 1–100 integer$'),
                            ({'recipe_id': blank_id, 'every': 2.5}, '^Insert blanks for every few samples: must be 1–100 integer$'),
                            ({'recipe_id': blank_id}, '^Insert blanks for every few samples: must be 1–100 integer$'),
                            ({'recipe_id': wait_id, 'every': 2}, r'^Blank Recipe Must content measurement steps (plasma_run) $'),
                            ('x', '^Blank setting format is incorrect$'), ({'recipe_id': blank_id, 'every': 2, 'more': 1}, '^Blank setting format is incorrect$')):
            with self.assertRaisesRegex(ValueError, text):
                rig.runner.preview(rig.body(blank=blank))
            with self.assertRaisesRegex(ValueError, text):
                rig.start(blank=blank)
        setting = {'recipe_id': blank_id, 'every': 2}
        self.assertEqual(rig.runner.preview(rig.body(blank=setting, count=7, interval=1))['blank'],
                         {'recipe_id': blank_id, 'recipe_name': 'blank measurement', 'every': 2})
        self.assertIsNone(rig.runner.preview(rig.body(blank={'enabled': False, 'recipe_id': '', 'every': 4}))['blank'])
        rig.start(blank=setting, count=7, interval=1)
        done = rig.finished(25)
        self.assertEqual((done['status'], done['counts']), ('completed', {'done': 7}))
        kinds = ['blank', 'sample', 'sample', 'blank', 'sample', 'sample', 'blank']
        self.assertEqual([slot['kind'] for slot in done['slots']], kinds)
        self.assertEqual(done['blank'], {'recipe_id': blank_id, 'recipe_name': 'blank measurement', 'every': 2,
                                         'sha256': rig.store.recipe(blank_id)['sha256']})
        rounds = [rig.round(slot['round_id']) for slot in done['slots']]
        self.assertEqual([record['sample_kind'] for record in rounds], kinds)
        self.assertEqual([record['recipe']['name'] for record in rounds],
                         ['blank measurement' if kind == 'blank' else 'Only discharge measurement' for kind in kinds])
        rows = rig.store.runlog(limit=7)[::-1]
        self.assertEqual([row['sample_kind'] for row in rows], kinds)
        self.assertEqual([row['result_text'].split(' · ')[0] for row in rows],
                         ['Blank (not compared)' if kind == 'blank' else 'Uncorrected' for kind in kinds])
        # One measurement per discharge, of the right kind, with where it came from.
        measured = rig.analyst.measurements()['rows']
        self.assertEqual([row['kind'] for row in measured], kinds)
        self.assertEqual([(row['round_id'], row['slot'], row['recipe_name'], row['operator'], row['schedule_id']) for row in measured],
                         [(record['id'], record['slot'], record['recipe']['name'], ct.OPERATOR, done['id']) for record in rounds])
        self.assertEqual([record['measurements'][0]['id'] for record in rounds], [row['id'] for row in measured])
        self.assertEqual(rounds[0]['steps'][0]['result']['measurement'], rounds[0]['measurements'][0])
        self.assertIn('Blank is not compared: the correction has not been applied and the baseline cannot be determined.', rig.texts())
        self.assertIn(',Blank Blank measurement each 2 samples,100 V (High voltage confirmed)', [text for text in rig.texts() if text.startswith('Scheduled measurement starts')][0])
        # The hook of practice mode (AMENDMENTS M11): once before every discharge, with what is measured.
        self.assertEqual([(call['mode'], call['sample_kind'], call['conc']) for call in rig.hook_calls],
                         [('schedule', kind, None) for kind in kinds])
        self.assertEqual([call['round_id'] for call in rig.hook_calls], [record['id'] for record in rounds])
        self.assertEqual(set(rig.hook_calls[0]), {'round_id', 'mode', 'sample_kind', 'conc'})

    def test_a_blank_slot_that_did_not_run_does_not_count(self):
        rig = self.rig(pump='off', speed=100)
        blank_id = rig.publish('blank measurement', ct.PLASMA_ONLY)
        rig.start(blank={'recipe_id': blank_id, 'every': 1}, count=4, interval=2)
        ct.wait_for(lambda: rig.schedule()['counts'].get('done') == 1 and rig.schedule()['status'] == 'waiting', 10, 'the first slot')
        rig.devices.bind(None, None)
        rig.devices.acquire('tool', 't1', 'Clear Nano fault')        # the second slot (a sample) cannot start
        rig.devices.bind(rig.runner, None)
        ct.wait_for(lambda: len(rig.schedule()['slots']) == 2, 6, 'the second slot')
        rig.devices.release('t1')
        done = rig.finished(20)
        self.assertEqual([(slot['kind'], slot['status']) for slot in done['slots']],
                         [('blank', 'done'), ('sample', 'not_started'), ('sample', 'done'), ('blank', 'done')])
        self.assertEqual(done['slots'][1]['note'], 'Repair tool is using the device')

    def test_a_hook_that_fails_and_an_analysis_that_fails_harm_nothing(self):
        def broken(info):
            raise RuntimeError('The simulator does not respond')

        rig = self.rig(pump='off', speed=100, hook=broken)
        (rig.data / 'analysis_config.json').write_text('{This is not JSON', encoding='utf-8')
        rig.start()
        done = rig.finished()
        self.assertEqual((done['status'], done['counts']), ('completed', {'done': 1}))
        self.assertEqual(len(rig.hook_calls), 1)
        warned = next(row for row in rig.store.events(limit=50) if row['text'].startswith('Error in preparation before discharge (does not affect measurement):'))
        self.assertEqual(warned['level'], 'warn')
        self.assertIn('The simulator does not respond', warned['text'])
        record = rig.round()
        self.assertEqual((record['status'], record['measurements'][0]['conc_reason']), ('completed', 'Analysis failed'))
        self.assertRegex(rig.store.runlog(limit=1)[0]['result_text'], r'^Analysis failed·Highest peak \d+\.\d nm$')
        # An Analyst that breaks altogether: the step has still succeeded.
        rig.analyst.measure = lambda *args, **kwargs: 1 / 0
        rig.start()
        self.assertEqual(rig.finished()['status'], 'completed')
        self.assertEqual(rig.round()['measurements'], [])
        self.assertTrue(rig.said('The measurement results have not been calculated. (Does not affect the process) '))


class CalibrationTests(ct.Case):
    """AMENDMENTS M5.2 / M5.6: a calibration is one round whose steps the Analyst lays out."""

    CONFIRM = {'high_voltage': True, 'liquid': True, 'hv_off_checked': False}

    def standards(self, rig, prime=''):
        return {'recipe_id': rig.seed, 'prime_recipe_id': prime, 'discard_rounds': 1,
                'levels': [{'name': 'Blank', 'conc': {'Cu': 0, 'Zn': 0, 'Ni': 0}, 'rounds': 1},
                           {'name': 'STD-1', 'conc': {'Cu': 1, 'Zn': 1, 'Ni': None}, 'rounds': 1}]}

    def rig_with_samples(self, **options):
        box = {'sample': {'Cu': 0.6, 'Zn': 0.4, 'Ni': 0.0}}

        def hook(info):
            rig.rpi.command('sim_sample', sample=info['conc'] if info['conc'] is not None else box['sample'])

        rig = self.rig(speed=100, hook=hook, **options)
        return rig, box

    def test_two_standards_with_a_prime_recipe(self):
        rig, box = self.rig_with_samples()
        prime_id = rig.publish('Fluid replacement and draining', ct.csv('dose_volume,M8,0.3,mL,Push the old one out'))
        plan = self.standards(rig, prime_id)
        with self.assertRaisesRegex(RuntimeError, '^There are currently no fluid changes waiting for confirmation.$'):
            rig.runner.confirm_prompt()
        # What it refuses, in its order.
        rig.operator = ''
        with self.assertRaisesRegex(ValueError, '^Please select the operator first$'):
            rig.runner.start_calibration({'plan': None})
        rig.operator = ct.OPERATOR
        with self.assertRaisesRegex(ValueError, '^Please select the standard product Recipe$'):
            rig.runner.start_calibration({'plan': dict(plan, recipe_id=''), 'confirm': self.CONFIRM})
        with self.assertRaisesRegex(ValueError, '^Recipe does not exist$'):
            rig.runner.start_calibration({'plan': dict(plan, recipe_id='0' * 32), 'confirm': self.CONFIRM})
        with self.assertRaisesRegex(ValueError, 'must contain exactly 1 indivual plasma_run'):
            rig.runner.start_calibration({'plan': dict(plan, recipe_id=prime_id, prime_recipe_id=''), 'confirm': self.CONFIRM})
        for confirm in ({}, {'liquid': True}, {'high_voltage': True}):
            with self.assertRaisesRegex(ValueError, T_TICKS):
                rig.runner.start_calibration({'plan': plan, 'confirm': confirm})
        rig.supply(True)
        with self.assertRaises(Blocked) as caught:
            rig.runner.start_calibration({'plan': plan, 'confirm': self.CONFIRM})
        self.assertEqual(str(caught.exception), 'Cannot start calibration now')
        self.assertIn('hv_on', [row['code'] for row in caught.exception.reasons])
        rig.supply(False)
        self.assertEqual(rig.analyst.records(), [])
        sent = rig.sim_requests()
        reply = rig.runner.start_calibration({'plan': plan, 'confirm': self.CONFIRM})
        cal_id = reply['calibration_id']
        self.assertEqual((reply['accepted'], reply['schedule']['kind'], reply['schedule']['calibration_id'],
                          reply['schedule']['planned_total']), (True, 'calibration', cal_id, 1))
        with self.assertRaisesRegex(RuntimeError, '^Already has a schedule in progress$'):
            rig.runner.start_calibration({'plan': plan, 'confirm': self.CONFIRM})
        with self.assertRaisesRegex(RuntimeError, '^Already has a schedule in progress$'):
            rig.start()
        self.assertTrue(rig.runner.uses_recipe(prime_id))
        # The first bottle.
        ct.wait_for(lambda: (rig.runner.calibration_view() or {}).get('prompt'), 10, 'the first bottle-swap prompt')
        view = rig.runner.calibration_view()
        self.assertEqual({key: view[key] for key in ('id', 'status', 'recipe_name', 'level', 'level_name', 'run', 'runs')},
                         {'id': cal_id, 'status': 'running', 'recipe_name': 'Only discharge measurement', 'level': 0, 'level_name': 'Blank',
                          'run': 1, 'runs': 4})
        self.assertEqual(view['prompt']['text'], 'Please replace with standard product Blank (Cu 0 / Zn 0 / Ni 0 mg/L) ,Put the pipette into the new bottle and click ”Confirm Liquid Change”')
        self.assertTrue(view['prompt']['since'])
        state = rig.runner.round_view()
        self.assertEqual((state['mode'], state['calibration_id'], state['prompt'], state['step'], state['step_count'], state['rounds']),
                         ('calibration', cal_id, view['prompt'], 1, 8, 4))
        self.assertEqual([phase['name'] for phase in state['phases']][:3], ['Fluid change', 'Fluid replacement and draining', 'Measure'])
        self.assertEqual(rig.analyst.running()['id'], cal_id)
        time.sleep(0.5)
        self.assertEqual(sent.count('plasma_run'), 0)                # nothing happens until the bottle is confirmed
        self.assertEqual(rig.runner.confirm_prompt(), {'accepted': True})
        ct.wait_for(lambda: (rig.runner.calibration_view() or {}).get('level') == 1
                    and rig.runner.calibration_view()['prompt'], 15, 'the second bottle-swap prompt')
        view = rig.runner.calibration_view()
        self.assertEqual((view['level_name'], view['run']), ('STD-1', 3))
        self.assertEqual(view['prompt']['text'], 'Please replace with standard product STD-1 (Cu 1 / Zn 1 mg/L) ,Put the pipette into the new bottle and click ”Confirm Liquid Change”')
        self.assertEqual(sent.count('plasma_run'), 2)
        rig.runner.confirm_prompt()
        done = rig.finished(25)
        self.assertEqual((done['status'], done['kind'], done['counts'], done['error']), ('completed', 'calibration', {'done': 1}, ''))
        self.assertIsNone(rig.runner.calibration_view())
        self.assertEqual(sent.count('plasma_run'), 4)
        # The round: prompt, prime, discard round, kept round — twice.
        record = rig.round()
        self.assertEqual((record['mode'], record['calibration_id'], record['status'], record['prime_recipe']['id']),
                         ('calibration', cal_id, 'completed', prime_id))
        self.assertEqual([step['action'] for step in record['steps']],
                         ['operator_prompt', 'dose_volume', 'plasma_run', 'plasma_run'] * 2)
        self.assertEqual({step['status'] for step in record['steps']}, {'done'})
        self.assertEqual(record['steps'][0]['result'], {'confirmed': True})
        self.assertEqual(len(record['plasma']['jobs']), 4)
        self.assertEqual(record['measurements'], [])
        self.assertEqual(rig.store.runlog(limit=1)[0]['result_text'], 'Calibration · Completed 4/4 wheel')
        # The calibration record: four rounds, analysed.
        saved = rig.analyst.record(cal_id)
        self.assertEqual(saved['status'], 'analyzed', saved.get('error'))
        self.assertEqual([(run['level'], run['level_name'], run['run'], run['discard'], run['round']) for run in saved['runs']],
                         [(0, 'Blank', 1, True, None), (0, 'Blank', 2, False, 1), (1, 'STD-1', 3, True, None), (1, 'STD-1', 4, False, 1)])
        self.assertEqual([run['job'] for run in saved['runs']], [job['job'] for job in record['plasma']['jobs']])
        self.assertEqual((saved['operator'], saved['schedule_id']), (ct.OPERATOR, done['id']))
        # A calibration's discharges are standards: none of them is a measurement.
        self.assertEqual(rig.analyst.measurements()['rows'], [])
        self.assertFalse((rig.data / 'measurements.jsonl').exists())
        # The pretend sample was set to each standard before its discharges (a metal the bottle does not hold: 0).
        self.assertEqual([(call['mode'], call['sample_kind'], call['conc']) for call in rig.hook_calls],
                         [('calibration', 'sample', {'Cu': 0, 'Zn': 0, 'Ni': 0})] * 2
                         + [('calibration', 'sample', {'Cu': 1, 'Zn': 1, 'Ni': 0})] * 2)
        texts = rig.texts()
        for text in ('Standard product calibration starts: only discharge measurement,2 concentration,common 4 wheel (high pressure confirmed)', 'Waiting for fluid change:Blank', 'Fluid change confirmed:Blank',
                     'Correction Blank Pre-run round completed (not included in regression)', 'Correction Blank No. 1 round completed', 'Waiting for fluid change:STD-1', 'Fluid change confirmed:STD-1',
                     'Correction STD-1 Pre-run round completed (not included in regression)', 'Correction STD-1 No. 1 round completed', 'Standard calibration completed: discharge measurement only',
                     'Standard calibration completed (completed)'):
            self.assertIn(text, texts)
        order = [texts.index(text) for text in ('Waiting for fluid change:Blank', 'Fluid change confirmed:Blank', 'Correction Blank No. 1 round completed', 'Waiting for fluid change:STD-1')]
        self.assertEqual(order, sorted(order))
        analysed = next(text for text in texts if text.startswith('Calibration analysis completed:'))
        self.assertRegex(analysed, r'^Calibration analysis completed:Cu R² \d\.\d{4}, Zn R² \d\.\d{4}$')
        self.assertEqual(rig.alarm_keys(), [])
        # Applied, the next sample gets concentrations, and they are said.
        rig.analyst.apply(cal_id)
        del rig.hook_calls[:]
        rig.start()
        self.assertEqual(rig.finished()['status'], 'completed')
        self.assertEqual(rig.hook_calls[0]['conc'], None)
        row = rig.store.runlog(limit=1)[0]
        self.assertRegex(row['result_text'], r'^Cu -?[\d.e+-]+ · Zn -?[\d.e+-]+ mg/L$')
        self.assertTrue(any(re.fullmatch(r'sample concentration:Cu -?[\d.e+-]+, Zn -?[\d.e+-]+ mg/L', text) for text in rig.texts()))
        conc = row['measurements'][0]['conc']
        self.assertAlmostEqual(conc['Cu'], 0.6, delta=0.35)          # two standards on a noisy pretend spectrum
        self.assertIsNone(conc['Ni'])
        # A blank after that is compared with the calibration's blank.
        blank_id = rig.publish('blank measurement', ct.PLASMA_ONLY)
        box['sample'] = {'Cu': 0.0, 'Zn': 0.0, 'Ni': 0.0}
        rig.start(blank={'recipe_id': blank_id, 'every': 5})
        rig.finished()
        row = rig.store.runlog(limit=1)[0]
        self.assertEqual(row['sample_kind'], 'blank')
        check = rig.analyst.latest()['latest_blank']['qc']
        if check['status'] == 'fail':
            self.assertEqual(row['result_text'], 'Blank ⚠ Beyond baseline threshold')
            alarm = rig.alarm('blank_qc:' + row['round_id'])
            self.assertEqual((alarm['priority'], alarm['area']), (3, 'Measure'))
            self.assertEqual(alarm['text'], 'Blank solution exceeds baseline threshold:' + '; '.join(check['problems']))
            self.assertIn(alarm['text'], rig.texts())
        else:
            self.assertEqual((check['status'], row['result_text']), ('ok', 'Blank ✓ baseline is normal'))
            self.assertIsNone(rig.alarm('blank_qc:'))

    def test_stop_while_a_bottle_swap_waits(self):
        rig, _box = self.rig_with_samples(pump='off')
        sent = rig.sim_requests()
        reply = rig.runner.start_calibration({'plan': self.standards(rig), 'confirm': self.CONFIRM})
        ct.wait_for(lambda: (rig.runner.calibration_view() or {}).get('prompt'), 10, 'the bottle-swap prompt')
        # PAUSE is honoured while it waits, as in any wait.
        self.assertEqual(rig.runner.pause()['status'], 'pausing')
        rig.wait_status('paused')
        self.assertEqual(rig.runner.calibration_view()['status'], 'paused')
        self.assertEqual(rig.runner.resume()['status'], 'running')
        self.assertTrue(rig.runner.calibration_view()['prompt'])
        report = rig.devices.stop_everything(ct.OPERATOR)
        self.assertEqual((report['schedule_ended'], report['round_stopping']), (True, True))
        done = rig.finished()
        self.assertEqual((done['status'], done['kind']), ('stopped', 'calibration'))
        record = rig.round()
        self.assertEqual((record['status'], record['prompt'], record['steps'][0]['status']), ('stopped', None, 'stopped'))
        self.assertEqual({step['status'] for step in record['steps'][1:]}, {'skipped'})
        self.assertEqual(rig.analyst.record(reply['calibration_id'])['status'], 'stopped')
        self.assertIsNone(rig.analyst.running())
        self.assertIsNone(rig.runner.calibration_view())
        self.assertEqual(sent.count('plasma_run'), 0)
        with self.assertRaisesRegex(RuntimeError, '^There are currently no fluid changes waiting for confirmation.$'):
            rig.runner.confirm_prompt()
        self.assertIn('Standard calibration has stopped: only discharge measurement', rig.texts())
        self.assertIn('Standard calibration completed (stopped)', rig.texts())
        self.assertIsNone(rig.devices.owner)

    def test_a_calibration_that_cannot_begin_or_fails_is_closed_in_the_record(self):
        rig, _box = self.rig_with_samples(pump='off')
        reply = rig.runner.start_calibration({'plan': self.standards(rig), 'confirm': self.CONFIRM})
        ct.wait_for(lambda: (rig.runner.calibration_view() or {}).get('prompt'), 10, 'the bottle-swap prompt')

        def broken(*args, **kwargs):
            raise RuntimeError('Simulation: Measurement tool failed')

        rig.sim.backend.plasma_run = broken
        rig.runner.confirm_prompt()
        done = rig.finished()
        self.assertEqual((done['status'], done['error']),
                         ('failed', 'Calibration did not end normally: Chapter 2 step (discharge＋Acquisition of spectrum) failed: Simulation: Measurement tool failed'))
        saved = rig.analyst.record(reply['calibration_id'])
        self.assertEqual((saved['status'], saved['runs']), ('failed', []))
        self.assertIn('Simulation: Measurement tool failed', saved['error'])
        self.assertTrue(any(text.startswith('Standard calibration completed (stopped due to failure). Calibration did not end normally') for text in rig.texts()))


class TroubleTests(ct.Case):
    """SPEC B.12: what happens when a device goes away in the middle."""

    def test_the_board_disappears_during_a_dose(self):
        self.pump_patched(RETRY_S=(0.2, 0.2))
        rig = self.rig()
        plan = self.plan()
        recipe_id = rig.publish('Measurement after adding liquid', ct.csv('dose_volume,M8,20,mL,', 'plasma_run,,,,'))
        sent = rig.sim_requests()
        stops = rig.sim.controller.stop_requests
        rig.start(recipe_id, count=2, interval=2)
        rig.wait_step('dose_volume')
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the dose to move')
        plan.pull_cable(rig.board)
        done = rig.finished()
        self.assertEqual((done['status'], done['counts']), ('failed', {'failed': 1}))
        self.assertEqual(done['error'], 'No. 1 measurements did not end normally and the schedule has been stopped: No. 1 step (quantitation (volume)) failed: the pump control board lost contact and the stop command could not be sent. The pump may still be spinning.')
        alarm = rig.alarm('pump_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['text']), (1, 'Pump control board lost connection. The pump may still be spinning. Immediately turn off power to the pump using the physical switch.'))
        record = rig.round()
        self.assertEqual([step['status'] for step in record['steps']], ['failed', 'skipped'])
        self.assertEqual(sent.count('plasma_run'), 0)                # no high voltage after that
        self.assertGreater(rig.sim.controller.stop_requests, stops)  # the RPi half of "everything off": the Recipe measures
        self.assertEqual(record['stop_report']['rpi']['result'], 'confirmed')
        # The program opened the board again at once, and a connect begins with X.
        ct.wait_for(lambda: rig.pump.connected and not rig.pump.motion_uncertain, 10, 'the board to come back')
        self.assertEqual(record['stop_report']['pump']['result'], 'confirmed')
        ct.wait_for(lambda: rig.alarm('pump_stop_unconfirmed')['cleared'], 5, 'the alarm to clear')
        self.assertIn('round_failed:' + record['id'], rig.alarm_keys())

    def test_the_board_disappears_during_a_weight_dose(self):
        self.pump_patched(RETRY_S=(0.2, 0.2))
        rig = self.rig()
        plan = self.plan()
        recipe_id = rig.publish('weighing', ct.csv('dose_weight,M8,500,g,'))
        rig.start(recipe_id)
        rig.wait_step('dose_weight')
        ct.wait_for(lambda: rig.board.mstate['B'] == 'run', 8, 'the weight dose to pump')
        started = time.perf_counter()
        plan.pull_cable(rig.board)
        done = rig.finished()
        self.assertLess(time.perf_counter() - started, 12)
        self.assertEqual(done['status'], 'failed')
        # Not V7's own "pump stopped": its stop could not be sent.
        self.assertEqual(done['error'], 'No. 1 measurements did not end normally and the schedule has been stopped: No. 1 step (quantitation (weight)) failed: the pump control board lost contact and the stop command could not be sent. The pump may still be spinning.')
        self.assertEqual(rig.alarm('pump_stop_unconfirmed')['priority'], 1)
        ct.wait_for(lambda: rig.pump.connected and not rig.pump.motion_uncertain, 10, 'the board to come back')

    def test_the_board_restarts_by_itself(self):
        with self.one() as rig:
            recipe_id = rig.publish('Add liquid', ct.csv('dose_volume,M8,20,mL,', 'delay,,1,s,'))
            rig.start(recipe_id)
            rig.wait_step('dose_volume')
            ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the dose to move')
            pt.FaultPlan.reset_board(rig.board)
            done = rig.finished()
            self.assertEqual(done['error'], 'No. 1 measurements did not end normally and the schedule has been stopped: No. 1 step (quantitation (volume)) failed: the pump control board restarted itself')
            alarm = ct.wait_for(lambda: rig.alarm('pump_reset'), 5, 'the alarm about the restart')
            self.assertEqual((alarm['priority'], alarm['area']), (2, 'pump'))
            ct.wait_for(lambda: rig.pump.connected, 5, 'the board to be usable again')
        # In a wait, with a pump of the round running: the pump has stopped, so the round cannot go on as planned.
        with self.one() as rig:
            recipe_id = rig.publish('discharge', DRAIN.format(seconds=8))
            rig.start(recipe_id)
            rig.wait_step('delay')
            pt.FaultPlan.reset_board(rig.board)
            done = rig.finished()
            self.assertEqual(done['status'], 'failed')
            self.assertRegex(done['error'], r'No. 2 step (wait) failed:(The pump control board was restarted or reconnected during this round of process.|Pump control board is not connected (.*) )$')
            self.assertEqual([step['status'] for step in rig.round()['steps']], ['done', 'failed', 'skipped'])

    def test_a_pump_that_was_stopped_behind_the_rounds_back(self):
        """OFF by hand on a pump the round started: the round does not go on as if nothing had happened."""
        rig = self.rig()
        recipe_id = rig.publish('discharge', DRAIN.format(seconds=1.5))
        rig.start(recipe_id)
        rig.wait_step('delay')
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the pump to turn')
        self.assertEqual(rig.manual.submit({'op': 'pump_off', 'motor': 8}), {'stopped': True})    # always accepted
        done = rig.finished()
        self.assertEqual(done['status'], 'failed')
        self.assertIn('No. 3 step (stop pump) failed:Pump 7 · OUT has been stopped first', done['error'])

    def test_pumps_and_high_voltage_are_never_on_together(self):
        """Both directions, at run time (the Recipe check R5 is the other half): no pump starts while a round
        discharges, and no discharge starts while a pump turns."""
        with self.one(speed=4) as rig:
            sent = rig.sim_requests()
            rig.start()
            rig.wait_step('plasma_run')
            ct.wait_for(lambda: rig.sim.backend.run_stop == 1, 10, 'the pretend supply to come on')
            self.assertTrue(rig.devices.hv_blocks_motion())
            with self.assertRaises(devices.Busy):                # the page: the round holds the equipment
                rig.manual.submit({'op': 'pump_on', 'motor': 8, 'flow_ml_min': 40, 'direction': 1, 'seconds': 5})
            with self.assertRaises(Blocked):
                rig.devices.permit_motion()
            for call, args in ((rig.pump.run, (8, 40, 1, 5, 'manual')), (rig.pump.dose, (8, 1, 1, 'manual', threading.Event())),
                               (rig.pump.clean, ('manual', threading.Event())), (rig.pump.syringe_init, ('manual', threading.Event()))):
                with self.assertRaises(PumpBoardError) as refused:   # whoever reaches the board itself: the wire is closed
                    call(*args)
                self.assertEqual((refused.exception.code, refused.exception.text), ('blocked', 'The high pressure has not been turned off and the pump cannot be started.'))
            self.assertTrue(rig.pumps_off())
            self.assertEqual(rig.finished(25)['status'], 'completed')
            self.assertFalse(rig.devices.hv_blocks_motion())
            self.assertEqual([c for c in rig.tx() if c == 'C' or c.startswith(('P,', 'Y,INIT'))], [])
            self.assertEqual(sent.count('plasma_run'), 1)
        with self.one() as rig:
            recipe_id = rig.publish('Wait and measure again', ct.csv('delay,,1.5,s,', 'plasma_run,,,,'))
            sent = rig.sim_requests()
            rig.start(recipe_id)
            rig.wait_step('delay')
            rig.pump.run(8, 40, 1, 60, 'manual')                 # a pump that turns although the round holds the equipment
            done = rig.finished()
            self.assertEqual(done['status'], 'failed')
            self.assertIn('No. 2 step (discharge＋Acquisition of spectrum) failed: the pump is still moving and cannot be discharged (the pump is still moving)', done['error'])
            self.assertEqual(sent.count('plasma_run'), 0)        # the high voltage never came on
            self.assertEqual(rig.sim.backend.run_stop, 0)
            self.assertEqual(rig.round()['stop_report']['pump']['result'], 'confirmed')     # and the pump was stopped
            self.assertTrue(rig.pumps_off())

    def test_the_rpi_link_drops_during_a_discharge(self):
        rig = self.rig(speed=4, pump='off')
        sent = rig.sim_requests()
        rig.start(count=2, interval=2)
        rig.wait_step('plasma_run')
        ct.wait_for(lambda: rig.sim.backend.run_stop == 1, 10, 'the pretend supply to come on')
        job = rig.runner.round['plasma_job']
        rig.cut_rpi()
        done = rig.finished(25)
        self.assertEqual((done['status'], done['counts']), ('failed', {'failed': 1}))
        # The RPi noticed by itself that its owner was gone and switched everything off; back on the line, this side reads that.
        self.assertIn('No. 1 step (discharge＋Failed to capture spectrum): The control connection was interrupted and the server automatically stopped', done['error'])
        self.assertEqual(rig.sim.backend.run_stop, 0)
        record = rig.round()
        self.assertEqual([(row['job'], row['state'], row['saved']) for row in record['plasma']['jobs']], [(job, 'failed', False)])
        self.assertEqual(record['stop_report']['rpi']['result'], 'confirmed')
        self.assertEqual(sent.count('plasma_run'), 1)                # never repeated
        self.assertFalse(rig.devices.hv_active)
        self.assertIsNone(rig.alarm('hv_stop_unconfirmed'))          # the read-back after the stop was fine
        self.assertEqual(rig.store.list_runs(), [])

    def test_a_stop_whose_read_back_fails_is_an_urgent_alarm(self):
        rig = self.rig(speed=4, pump='off')
        self.patch(devices, 'HV_SETTLE_SECONDS', 1.0)
        rig.start()
        rig.wait_step('plasma_run')
        ct.wait_for(lambda: rig.sim.backend.run_stop == 1, 10, 'the pretend supply to come on')
        real = rig.sim.backend.stop
        calls = []

        def stop():
            calls.append(1)
            if len(calls) == 1:                      # the RPi's own confirmation after the tool has ended
                raise RuntimeError('HSPY No response')
            return real()

        rig.sim.backend.stop = stop
        rig.devices.stop_everything(ct.OPERATOR)
        done = rig.finished(25)
        self.assertEqual(done['status'], 'stopped')
        alarm = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['area'], alarm['text']), (1, 'high pressure', 'The stop of high voltage has not been confirmed. Please turn off the high voltage with a physical switch immediately.'))
        self.assertIn('after stopping OFF readback unconfirmed (HSPY No response)', rig.round()['error'])
        self.assertEqual(rig.sim.backend.run_stop, 0)

    def test_the_rpi_goes_away_while_a_recipe_is_in_its_pump_steps(self):
        rig = self.rig()
        recipe_id = rig.publish('discharge and add', ct.csv('pump_on,M8,40,mL/min,', 'delay,,2.5,s,', 'pump_off,M8,,,', 'dose_volume,M8,1,mL,'))
        rig.start(recipe_id)
        rig.wait_step('delay')
        rig.rpi_gone()
        self.assertEqual(rig.board.mstate['B'], 'move')              # a pump that runs keeps its own time limit
        done = rig.finished()
        self.assertEqual(done['status'], 'failed')
        self.assertIn('No. 4 step (quantitate (volume)) failed: the pump cannot be started now (RPi There is no connection, so it is impossible to confirm that the high voltage is off)', done['error'])
        record = rig.round()
        self.assertEqual([step['status'] for step in record['steps']], ['done', 'done', 'done', 'failed'])
        self.assertEqual(len([command for command in rig.tx() if command.startswith('P,8,')]), 1)    # the dose never went out
        self.assertEqual((record['stop_report']['pump']['result'], record['stop_report']['rpi']['result']), ('confirmed', 'unreachable'))
        self.assertIn('Stop not confirmed (RPi) — Please use the physical switch to cut off the power', record['error'])
        # middle, not Emergency: the RPi's last word was "off", and this Recipe has no step that switches the high voltage on.
        alarm = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['text']), (3, 'The stop command cannot be sent RPi.RPi will turn off the high voltage automatically after finding that the connection is interrupted, but please go to the site to confirm.'))

    def test_the_supply_is_switched_on_elsewhere_while_a_round_pumps(self):
        """The watch of Devices: the pumps are stopped at once, the round and its schedule end."""
        text = 'High voltage is displayed as ON,But the pump is still running: All pumps have been stopped. Please go to the site to confirm.'
        rig = self.rig()
        recipe_id = rig.publish('discharge', DRAIN.format(seconds=20))
        rig.start(recipe_id, count=3, interval=30)
        rig.wait_step('delay')
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the pump to turn')
        time.sleep(0.7)                  # with the high voltage off the watch leaves the round alone
        self.assertEqual(rig.board.mstate['B'], 'move')
        stops, started = rig.sim.controller.stop_requests, time.perf_counter()
        rig.sim.backend.run_stop = 1     # not by this program
        rig.rpi.command('status', fresh=True)
        ct.wait_for(rig.pumps_off, 4, 'the watch to stop the pump')
        done = rig.finished()
        self.assertLess(time.perf_counter() - started, 8.0)
        self.assertEqual((done['status'], done['counts'], len(done['slots'])), ('stopped', {'stopped': 1}, 1))
        record = rig.round()
        self.assertEqual(record['status'], 'stopped')
        self.assertEqual([step['status'] for step in record['steps']], ['done', 'stopped', 'skipped'])
        # The end of the round switches everything off once more — the supply too, whoever switched it on.
        self.assertEqual({name: half['result'] for name, half in record['stop_report'].items()}, {'pump': 'confirmed', 'rpi': 'confirmed'})
        self.assertGreater(rig.sim.controller.stop_requests, stops)
        self.assertEqual(rig.sim.backend.run_stop, 0)
        alarm = rig.alarm('pump_with_hv')
        self.assertEqual((alarm['priority'], alarm['area'], alarm['text']), (1, 'pump', text))
        self.assertEqual([row['level'] for row in rig.store.events(limit=200) if row['text'] == text], ['error'])
        self.assertIsNone(rig.devices.owner)
        self.assertEqual([key for key in rig.alarm_keys() if key.startswith(('round_failed', 'sched_failed'))], [])

    def test_a_pump_round_on_a_rig_without_an_rpi_ends_without_a_high_voltage_alarm(self):
        """Started with the tick “I have gone to the site to confirm that the high-voltage power supply is turned off.”: at its end no stop is sent to an RPi that is not there."""
        tick = {'liquid': True, 'hv_off_checked': True}
        with self.one(rpi=False) as rig:
            recipe_id = rig.publish('discharge', DRAIN.format(seconds=20))
            rig.start(recipe_id, count=2, interval=30, confirm=tick)
            rig.wait_step('delay')
            ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the pump to turn')
            rig.runner.request_stop()                # (not the STOP button: that one always tries the RPi, see below)
            done = rig.finished()
            self.assertEqual((done['status'], done['error'], done['counts']), ('stopped', '', {'stopped': 1}))
            record = rig.round()
            self.assertEqual((record['status'], record['error']), ('stopped', ''))
            self.assertEqual(record['stop_report'], {'pump': {'result': 'confirmed', 'error': ''}, 'rpi': {'result': 'none', 'error': ''}})
            self.assertEqual(rig.alarm_keys(), [])
            self.assertTrue(rig.pumps_off())
            # The STOP button: its own stop cannot reach an RPi (middle), and the round still ends as stopped.
            rig.start(recipe_id, count=2, interval=30, confirm=tick)
            rig.wait_step('delay')
            report = rig.devices.stop_everything(ct.OPERATOR)
            self.assertEqual((report['pump']['result'], report['rpi']['result']), ('confirmed', 'unreachable'))
            done = rig.finished()
            self.assertEqual((done['status'], done['error'], done['counts']), ('stopped', '', {'stopped': 1}))
            self.assertEqual({name: half['result'] for name, half in rig.round()['stop_report'].items()}, {'pump': 'confirmed', 'rpi': 'none'})
            alarm = rig.alarm('hv_stop_unconfirmed')
            self.assertEqual((alarm['priority'], alarm['text']), (3, 'The stop command cannot be sent RPi.RPi will turn off the high voltage automatically after finding that the connection is interrupted, but please go to the site to confirm.'))
            self.assertEqual(rig.alarm_keys(), ['hv_stop_unconfirmed'])
            self.assertIn('Stop has been pressed: the pump has stopped; the high pressure stop command cannot be sent RPi', rig.texts())
        with self.one(rpi=False) as rig:             # ... and one whose step fails
            plan = self.plan()
            recipe_id = rig.publish('discharge', DRAIN.format(seconds=0.3))
            plan.answer('P,8', ['ERR,arg'])
            rig.start(recipe_id, confirm=tick)
            done = rig.finished()
            plan.restore()
            self.assertEqual(done['status'], 'failed')
            record = rig.round()
            self.assertEqual({name: half['result'] for name, half in record['stop_report'].items()}, {'pump': 'confirmed', 'rpi': 'none'})
            self.assertNotIn('Stop not confirmed', record['error'])
            self.assertIsNone(rig.alarm('hv_stop_unconfirmed'))

    def test_the_tick_of_a_schedule_does_not_outlive_a_stop_that_was_never_confirmed(self):
        """The tick of the start dialog is a statement about that moment (Devices.tick). After a high-voltage stop
        that nobody could read back, the schedule that was started before it does not go on. One that is started
        after it does, and its first pump start lowers the flag: somebody has been on site since."""
        self.patch(devices, 'HV_SETTLE_SECONDS', 0.3)
        self.patch(devices, 'HV_POLL_SECONDS', 0.05)
        tick = {'liquid': True, 'hv_off_checked': True}
        rig = self.rig(rpi=False)
        recipe_id = rig.publish('discharge', DRAIN.format(seconds=0.3))
        rig.start(recipe_id, count=2, interval=4, confirm=tick)
        ct.wait_for(lambda: rig.schedule()['counts'].get('done') == 1 and rig.schedule()['status'] == 'waiting', 10, 'the first round')
        with rig.devices.hv_session():   # (a discharge whose end nobody could read back)
            pass
        self.assertTrue(rig.devices.hv_active)
        done = rig.finished()
        self.assertEqual((done['status'], done['counts']), ('completed', {'done': 1, 'not_started': 1}))
        self.assertEqual(done['slots'][1]['note'], 'RPi There is no connection and it is impossible to confirm that the high voltage is off.')
        self.assertTrue(rig.devices.hv_active)
        self.assertEqual(len([command for command in rig.tx() if command.startswith('P,8,')]), 1)
        rig.start(recipe_id, confirm=tick)
        done = rig.finished()
        self.assertEqual((done['status'], done['counts']), ('completed', {'done': 1}))
        self.assertFalse(rig.devices.hv_active)
        self.assertEqual(len([command for command in rig.tx() if command.startswith('P,8,')]), 2)
        alarm = rig.alarm('hv_stop_unconfirmed')     # it stays until the supply is read back as off
        self.assertEqual((alarm['priority'], alarm['cleared']), (1, False))

    def test_stop_with_the_supply_switched_off_at_its_mains(self):
        """7i. The RPi cannot confirm the stop because the supply does not answer; the Nano is read as quiet and
        nothing of ours was under way: a lesser alarm, and a round that was stopped stays stopped."""
        silent = 'The high voltage power supply does not respond and the program cannot confirm that it is off;Nano Confirmed no output. If the power is turned off from the switch, this is normal.'
        rig = self.rig()
        rig.supply_answers(False)        # the usual state of the real rig between measurements
        # The STOP button during a round that only pumps (started with the tick of the start dialog).
        recipe_id = rig.publish('discharge', DRAIN.format(seconds=20))
        rig.start(recipe_id, count=2, interval=30, confirm={'liquid': True, 'hv_off_checked': True})
        rig.wait_step('delay')
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the pump to turn')
        report = rig.devices.stop_everything(ct.OPERATOR)
        self.assertEqual((report['pump']['result'], report['rpi']), ('confirmed', {'result': 'silent', 'error': silent}))
        done = rig.finished()
        self.assertEqual((done['status'], done['error'], done['counts']), ('stopped', '', {'stopped': 1}))
        record = rig.round()
        self.assertEqual((record['status'], record['error']), ('stopped', ''))
        alarm = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['area'], alarm['text']), (3, 'high pressure', silent))
        self.assertEqual(rig.alarm_keys(), ['hv_stop_unconfirmed'])
        self.assertIn('Stop pressed: pump stopped; high voltage power supply not responding (Nano Confirmed no output)', rig.texts())
        self.assertTrue(rig.pumps_off())
        # A round with a step for the RPi (no discharge) that is stopped: its own "everything off" gets the same
        # answer from the RPi, and that does not make it a failed round.
        check_id = rig.publish('Wait and check again', ct.csv('delay,,20,s,', 'rpi_status,,,,'))
        rig.start(check_id, confirm={})
        rig.wait_step('delay')
        rig.runner.request_stop()                    # (not the STOP button: only the round's own end sends a stop)
        done = rig.finished()
        self.assertEqual((done['status'], done['error'], done['counts']), ('stopped', '', {'stopped': 1}))
        record = rig.round()
        self.assertEqual((record['status'], record['error']), ('stopped', ''))
        self.assertEqual(record['stop_report']['rpi'], {'result': 'silent', 'error': silent})
        self.assertEqual(rig.alarm_keys(), ['hv_stop_unconfirmed'])
        self.assertEqual(rig.alarm('hv_stop_unconfirmed')['priority'], 3)

    def test_stop_during_a_discharge_with_a_supply_that_stops_answering_is_urgent_as_before(self):
        """7i does not reach this case: a discharge of ours was under way, so "the supply does not answer" excuses nothing."""
        rig = self.rig(speed=4, pump='off')
        self.patch(devices, 'HV_SETTLE_SECONDS', 1.0)
        rig.start()
        rig.wait_step('plasma_run')
        ct.wait_for(lambda: rig.sim.backend.run_stop == 1, 10, 'the pretend supply to come on')
        rig.supply_answers(False, wait=False)        # from now on it does not answer
        report = rig.devices.stop_everything(ct.OPERATOR)
        self.assertEqual(report['rpi']['result'], 'cancel_sent')
        rig.finished(25)
        record = rig.round()
        self.assertEqual((record['status'], record['stop_report']['rpi']['result']), ('failed', 'unconfirmed'))
        self.assertIn('Stop not confirmed (RPi) — Please use the physical switch to cut off the power', record['error'])
        alarm = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['text']), (1, 'The stop of high voltage has not been confirmed. Please turn off the high voltage with a physical switch immediately.'))
        self.assertTrue(rig.devices.hv_active)
        self.assertEqual(rig.sim.backend.run_stop, 0)

    def test_a_pump_that_ran_out_before_its_pump_off(self):
        self.patch(steps, 'RUN_MARGIN_S', -0.5)      # the board is given less time than the Recipe waits
        rig = self.rig()
        recipe_id = rig.publish('discharge', DRAIN.format(seconds=1.5))
        rig.start(recipe_id)
        done = rig.finished()
        self.assertEqual(done['error'], 'No. 1 measurements did not end normally and the schedule has been stopped: No. 2 step (wait) failed:Pump 7 · OUT The planned operation time has been exceeded and has automatically stopped.')
        self.assertEqual([step['status'] for step in rig.round()['steps']], ['done', 'failed', 'skipped'])
        self.assertEqual([c for c in rig.tx() if c.startswith('V,8,') and not c.endswith(',0')], [])

    def test_a_recipe_that_leaves_a_pump_running(self):
        """Preflight refuses such a Recipe (R7). Should one get through all the same: X at the end of the round."""
        rig = self.rig()
        text = ct.csv('pump_on,M8,40,mL/min,', 'delay,,0.3,s,')
        self.assertEqual(recipe.validate(text, rig.pump.hardware())['error'], 'The pump is still running at the end of the process:Pump 7 · OUT (No. 2 column startup)')
        recipe_id = rig.publish('Forgot to turn it off', text)
        with self.assertRaisesRegex(ValueError, 'The pump is still running at the end of the process'):
            rig.start(recipe_id)
        real = recipe.validate
        self.patch(runner.recipe, 'validate', lambda *args, **kwargs: dict(real(*args, **kwargs), valid=True, error=''))
        sent = rig.tx().count('X')
        rig.start(recipe_id)
        done = rig.finished()
        self.assertEqual(done['error'], 'No. 1 measurements did not end normally and the schedule was stopped: the pumps did not stop at the end of one round and all stopped.')
        self.assertGreater(rig.tx().count('X'), sent)
        self.assertEqual([step['status'] for step in rig.round()['steps']], ['done', 'done'])
        self.assertTrue(rig.pumps_off())

    def test_a_discharge_is_never_sent_twice(self):
        self.patch(rpilink, 'RESULT_RETRY_SECONDS', 0.3)
        rig = self.rig(pump='off', speed=100)
        sent, client = [], rig.rpi.client
        real = client.request

        def request(cmd, **fields):
            sent.append(cmd)
            if cmd == 'result':
                raise PlasmicError('not_found', 'The work folder cannot be found')
            return real(cmd, **fields)

        client.request = request
        rig.start(count=3, interval=2)
        done = rig.finished()
        self.assertEqual(done['error'], 'No. 1 measurements did not end normally and the schedule has been stopped: No. 1 step (discharge＋Failed to capture the spectrum): The discharge was completed, but the spectrum reading failed: the working folder cannot be found (the data is still RPi) ')
        self.assertEqual((sent.count('plasma_run'), sent.count('result')), (1, 2))       # only the read was tried again
        record = rig.round()
        self.assertEqual([(row['state'], row['saved']) for row in record['plasma']['jobs']], [('done', False)])
        self.assertEqual(len(done['slots']), 1)
        self.assertEqual(rig.store.list_runs(), [])

    def test_a_schedule_goes_on_when_nobody_looks_at_the_page(self):
        self.pump_patched(UI_WATCHDOG_SECONDS=0.3)
        self.patch(rpilink, 'UI_WATCHDOG_SECONDS', 0.3)
        rig = self.rig(speed=100)
        rig.page_age = 3600.0            # the page was closed an hour ago
        recipe_id = rig.publish('Measure after discharge', ct.csv('pump_on,M8,40,mL/min,', 'delay,,1,s,', 'pump_off,M8,,,', 'dose_volume,M8,1,mL,',
                                               'plasma_run,,,,'))
        rig.start(recipe_id, count=2, interval=4)
        done = rig.finished(25)
        self.assertEqual((done['status'], done['counts']), ('completed', {'done': 2}))
        self.assertEqual(rig.alarm_keys(), [])
        self.assertFalse(rig.said('The operation screen exceeds'))
        self.assertEqual(len(rig.store.list_runs()), 2)


class RobustnessTests(ct.Case):
    """`finally` always gives the equipment back and leaves the outputs off."""

    def test_the_program_ends_in_the_middle_of_a_round(self):
        rig = self.rig()
        recipe_id = rig.publish('discharge', DRAIN.format(seconds=20))
        rig.start(recipe_id, count=2, interval=30)
        rig.wait_step('delay')
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the pump to turn')
        started = time.perf_counter()
        rig.runner.close()
        self.assertLess(time.perf_counter() - started, 6.0)
        self.assertFalse(rig.runner.thread.is_alive())
        self.assertEqual((rig.schedule()['status'], rig.round()['status']), ('stopped', 'stopped'))
        self.assertTrue(rig.pumps_off())
        self.assertIn('X', rig.tx())
        self.assertIsNone(rig.devices.owner)
        self.assertEqual(rig.runner.request_stop(), {'schedule': False, 'round': False})

    def test_a_fault_of_the_program_itself_while_a_round_is_closed(self):
        with self.one() as rig:
            real = rig.devices.all_off
            calls = []

            def all_off(need_pump, need_rpi, ack=False):
                calls.append((need_pump, need_rpi))
                if len(calls) == 1:
                    raise RuntimeError('Simulation: Program error')
                return real(need_pump, need_rpi, ack)

            rig.devices.all_off = all_off
            recipe_id = rig.publish('discharge', DRAIN.format(seconds=20))
            rig.start(recipe_id)
            rig.wait_step('delay')
            rig.runner.request_stop()                # (not the STOP button: only the runner is told)
            done = rig.finished()
            self.assertEqual(done['status'], 'stopped')
            record = rig.round()
            self.assertEqual(record['status'], 'failed')             # the round could not vouch for "everything off"
            self.assertIn('An error occurred at the end of the process', record['error'])
            self.assertIsNone(rig.devices.owner)
            self.assertTrue(rig.pumps_off())                         # stopped all the same, straight on the board
            rig.devices.all_off = real
            self.assertEqual(rig.devices.stop_everything('test')['pump']['result'], 'confirmed')
        with self.one() as rig:
            def broken(record):
                raise RuntimeError('Simulation: Program error')

            rig.runner._round_row = broken               # the very last part of closing a round fails
            recipe_id = rig.publish('discharge', DRAIN.format(seconds=20))
            rig.start(recipe_id)
            rig.wait_step('delay')
            ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the pump to turn')
            rig.runner.request_stop()
            done = rig.finished()
            self.assertEqual(done['status'], 'failed')
            self.assertTrue(done['error'].startswith('Scheduling internal error:'), done['error'])
            self.assertEqual((done['slots'][0]['status'], done['counts']), ('failed', {'failed': 1}))
            self.assertIsNone(rig.devices.owner)                     # never left held
            self.assertEqual(rig.runner.ownership(), '')
            self.assertTrue(rig.pumps_off())                         # and the pump was stopped all the same
            self.assertEqual(rig.manual.submit({'op': 'pump_on', 'motor': 8, 'flow_ml_min': 40, 'direction': 1, 'seconds': 1})['accepted'], True)
            rig.manual.submit({'op': 'pump_off', 'motor': 8})


class RestartTests(ct.Case):
    """SPEC B.5 "Restart": what a program that ended in the middle left behind is never resumed."""

    def test_a_round_that_was_cut_off(self):
        rig = self.rig()
        recipe_id = rig.publish('Measure after waiting', ct.csv('dose_volume,M8,0.2,mL,', 'delay,,30,s,', 'plasma_run,,,,'))
        rig.start(recipe_id, count=3, interval=40)
        rig.wait_step('delay')
        time.sleep(1.4)                  # during a wait the record is written once a second at most
        round_id = rig.runner.round['id']
        round_path = rig.data / 'rounds' / f'ROUND-{round_id}.json'
        on_disk = ((rig.data / 'schedule.json').read_bytes(), round_path.read_bytes())    # what a dead program leaves
        self.assertEqual((json.loads(on_disk[0])['status'], json.loads(on_disk[1])['status']), ('running', 'running'))
        rig.devices.stop_everything('test')
        rig.finished()
        (rig.data / 'schedule.json').write_bytes(on_disk[0])
        round_path.write_bytes(on_disk[1])
        lines = len(rig.store.runlog(limit=100))
        stops = rig.sim.controller.stop_requests
        sent = rig.sim_requests()
        commands = len(rig.tx())
        again = runner.Runner(rig.store, rig.alarms, rig.devices, rig.rpi, rig.pump, lambda: ct.OPERATOR)
        view = again.view()
        self.assertEqual((view['status'], view['error'], view['next_slot_at']), ('interrupted', T_RESTART, None))
        self.assertEqual((view['slots'][-1]['status'], view['slots'][-1]['note'], view['counts']),
                         ('failed', 'The program was closed or the computer was restarted during measurement; please go to the site to confirm the equipment status.', {'failed': 1}))
        self.assertEqual((again.active, again.busy, again.thread, again.round_view(), again.calibration_view()),
                         (False, False, None, None, None))
        self.assertEqual(json.loads((rig.data / 'schedule.json').read_text(encoding='utf-8'))['status'], 'interrupted')
        record = rig.store.round(round_id)
        self.assertEqual((record['status'], record['error']), ('interrupted', 'The program is closed or the computer is restarted during execution; please go to the site to confirm the device status.'))
        self.assertEqual([step['status'] for step in record['steps']], ['done', 'failed', 'skipped'])
        rows = rig.store.runlog(limit=100)
        self.assertEqual(len(rows), lines + 1)
        self.assertEqual((rows[0]['kind'], rows[0]['status'], rows[0]['round_id'], rows[0]['result_text']),
                         ('round', 'interrupted', round_id, 'Not measured'))
        alarm = rig.alarm('sched_interrupted:' + view['id'])
        self.assertEqual((alarm['priority'], alarm['text']), (2, 'The schedule has been interrupted and will not be automatically restored. Please restart the schedule after confirming the device.'))
        self.assertIn('The last scheduled measurement was interrupted due to program restart and will not be automatically restored.', rig.texts())
        # One stop to the RPi (the round's Recipe measures); nothing is started again.
        ct.wait_for(lambda: rig.sim.controller.stop_requests == stops + 1, 5, 'the one stop')
        time.sleep(1.5)
        self.assertEqual(rig.sim.controller.stop_requests, stops + 1)
        self.assertEqual((sent.count('plasma_run'), sent.count('spectrum'), sent.count('prepare')), (0, 0, 0))
        self.assertEqual([command for command in rig.tx()[commands:] if command.startswith(('P,', 'V,', 'C', 'Y,'))], [])
        self.assertTrue(rig.said('The previous round of process was interrupted: Requested Answer a question on RPi Turn off high voltage'))
        # A program that starts once more finds nothing left to mark.
        third = runner.Runner(rig.store, rig.alarms, rig.devices, rig.rpi, rig.pump, lambda: ct.OPERATOR)
        self.assertEqual(third.view()['status'], 'interrupted')
        self.assertEqual(len(rig.store.runlog(limit=100)), lines + 1)
        time.sleep(0.5)
        self.assertEqual(rig.sim.controller.stop_requests, stops + 1)
        # And a new schedule can be started as usual.
        self.assertEqual(again.start(rig.body())['accepted'], True)
        ct.wait_for(lambda: not again.active, 15, 'the new schedule to end')
        self.assertEqual(again.view()['status'], 'completed')

    def test_a_schedule_of_the_program_before_2_0_0(self):
        rig = self.rig(pump='off', rpi=False, analyst=False)
        store_module.write_json(rig.data / 'schedule.json', {
            'id': 'a' * 32, 'status': 'running', 'created_at': store_module.now_iso(), 'finished_at': None, 'counts': {'done': 3},
            'spec': {'first_slot': '2026-10-05T08:00:00+08:00', 'interval_seconds': 600, 'interval_minutes': 10,
                     'start': {'mode': 'now', 'time': None}, 'end': {'mode': 'continuous'}},
            'planned_total': None, 'last_slot_at': None, 'estimate_seconds': 40, 'voltage_v': 300, 'next_index': 4,
            'next_slot_at': '2026-10-05T08:40:00+08:00', 'error': '',
            'slots': [{'index': 3, 'planned_at': '2026-10-05T08:30:00+08:00', 'status': 'running', 'job': 'HMI-' + 'b' * 32}]})
        again = runner.Runner(rig.store, rig.alarms, rig.devices, rig.rpi, rig.pump, lambda: '')
        view = again.view()
        self.assertEqual((view['status'], view['error'], view['counts']), ('interrupted', T_RESTART, {'done': 3, 'failed': 1}))
        self.assertEqual((view['kind'], view['calibration_id'], view['blank'], view['slots'][0]['kind'], view['slots'][0]['status']),
                         ('schedule', None, None, 'sample', 'failed'))
        self.assertEqual(rig.alarm('sched_interrupted:' + 'a' * 32)['priority'], 2)
        self.assertFalse(again.active)
        self.assertFalse(rig.said('Invited RPi Turn off high voltage'))    # no round was cut off: nothing is sent


if __name__ == '__main__':
    unittest.main(verbosity=2)
