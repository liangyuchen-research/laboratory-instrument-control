"""pc/analyst.py: the Analyst, on synthetic stored spectrum sets.

The original HMI's whole-run tests (tests/test_calibration.py, CalibrationRunTests) without the
HTTP server, PLC and SSH around them - the same synthetic spectra, seeds and expected numbers -
plus the cases of AMENDMENTS M5.6. ``load_run`` and ``write_json`` are stand-ins of the Store's;
data lives in a temp folder. No equipment, no network.

    python -B tests/test_analyst.py
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pc'))
sys.path.insert(0, str(ROOT / 'tests'))  # the bundled Python does not add the script's folder

import analysis as sa  # noqa: E402
import analyst as analyst_module  # noqa: E402
import calibration as cal  # noqa: E402
import measurements as meas  # noqa: E402
from analyst import Analyst  # noqa: E402
from test_analysis import AXIS, RIG_AXIS, job_id, stored_run, synthetic  # noqa: E402
from test_calibration import PRIME, ROUND, plan, recipe  # noqa: E402

RECIPE_ID, PRIME_ID = 'a' * 32, 'b' * 32
DEFAULT_METHOD = 'area-v1-07c44d563a4d'  # of pc/analysis_default.json
BUILT_IN_METHOD = 'area-v1-ec0e59e287f2'  # of analysis.DEFAULT_CONFIG, which the synthetic spectra fit
INFO = {'round_id': 'e' * 32, 'schedule_id': 'd' * 32, 'slot': 3, 'recipe_name': 'STD round', 'operator': 'Wang Xiaoming'}
_spectra = {}


def spectrum(conc, shift=0.0, gain=1.0):
    key = (tuple(sorted(conc.items())), shift, gain)
    if key not in _spectra:
        _spectra[key] = synthetic(conc, shift, gain)
    return _spectra[key]


def published(recipe_id, name, rows, estimate=None, delay=0.0, **check):
    """What the runner hands to check_plan for one Recipe."""
    steps, phases = recipe(rows)
    validated = {'valid': True, 'error': '', 'error_line': None, 'warnings': [], 'step_count': len(steps), 'rounds': 1,
                 'delay_seconds': delay, 'phases': phases, 'uses_pump': True, 'uses_rpi': True, 'uses_plasma': True,
                 'plasma_count': sum(1 for step in steps if step['action'] == 'plasma_run'), 'steps': steps}
    validated.update(check)
    return {'id': recipe_id, 'name': name, 'sha256': hashlib.sha256(name.encode('utf-8')).hexdigest(),
            'validated': validated, 'estimate_seconds': estimate}


STANDARD = published(RECIPE_ID, 'STD round', ROUND, estimate=32.5, delay=0.2)
PRIMING = published(PRIME_ID, 'prime', PRIME, estimate=8.0, delay=0.2, uses_rpi=False, uses_plasma=False)


class Disk:
    """write_json as the program's Store has it (.tmp, then replace); remembers what was written."""

    def __init__(self):
        self.written = []
        self.broken = None  # a function(path) -> True makes that write fail

    def __call__(self, path, value):
        if self.broken is not None and self.broken(path):
            raise OSError(28, 'No space left on device')
        temporary = path.with_name(path.name + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
        temporary.replace(path)
        self.written.append(path)


class Runs:
    """load_run as Store.load_run: the stored spectrum sets by job id (kept as text, like files)."""
    ID = re.compile(r'(?:HMI|CAP|LITE)-[0-9a-f]{32}')

    def __init__(self):
        self.sets = {}
        self.loads = []
        self.gate = None  # an Event: every load waits for it

    def add(self, values, **how):
        job = job_id(len(self.sets) + 1)
        self.put(job, values, **how)
        return job

    def put(self, job, values, **how):
        self.sets[job] = json.dumps(stored_run(job, values, **how))

    def __call__(self, job):
        self.loads.append(job)
        if self.gate is not None:
            self.gate.wait(20)
        if not self.ID.fullmatch(job):
            raise ValueError('Spectrum number format is incorrect')
        if job not in self.sets:
            raise FileNotFoundError(2, 'No such file or directory', job + '.json')
        return json.loads(self.sets[job])


class Bench(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / 'data'
        self.disk, self.runs = Disk(), Runs()
        self.events, self.alarms = [], []
        self.discharges = 0
        self.analyst = self.start()

    def tearDown(self):
        if self.runs.gate is not None:
            self.runs.gate.set()
        self.temp.cleanup()

    def settings(self, config):
        """The owner edits data/analysis_config.json by hand."""
        self.data.mkdir(parents=True, exist_ok=True)
        path = self.data / 'analysis_config.json'
        text = config if isinstance(config, str) else json.dumps(config, indent=1)
        previous = path.stat().st_mtime_ns if path.exists() else 0
        path.write_text(text, encoding='utf-8')
        if path.stat().st_mtime_ns <= previous:  # two writes within one tick of the clock
            os.utime(path, ns=(previous + 1_000_000, previous + 1_000_000))

    def start(self, config=sa.DEFAULT_CONFIG):
        """One program start on the data folder. The synthetic spectra have their lines where the
        original's built-in settings look for them, so most tests run with those settings."""
        if config is not None:
            self.settings(config)
        return Analyst(self.data, self.runs, self.disk, lambda text, level: self.events.append((level, text)),
                       lambda *alarm: self.alarms.append(alarm))

    def stored(self, name):
        return json.loads((self.data / name).read_text(encoding='utf-8'))

    def discharge(self, conc, shift, gain_range):
        """The spectrum of the next discharge, as the original's simulated RPi produced it."""
        self.discharges += 1
        rng = random.Random(self.discharges)
        if shift is None:
            shift = rng.uniform(-0.03, 0.03)
        return self.runs.add(spectrum(conc, shift, rng.uniform(*gain_range)))

    def run_calibration(self, raw=None, upto=None, analyst=None, prime=None):
        """Create a calibration and measure its rounds (all of them, or the first ``upto``)."""
        analyst = analyst or self.analyst
        info = analyst.check_plan(raw or plan(recipe_id=RECIPE_ID), STANDARD, prime)
        record = analyst.create(info['plan'], STANDARD, prime, 'Wang Xiaoming', 'c' * 32)
        done = 0
        for step in info['steps']:
            if step['action'] != 'plasma_run' or (upto is not None and done >= upto):
                continue
            tag = step['calibration']
            conc = info['plan']['levels'][tag['level']]['conc']
            job = self.discharge({m: v or 0 for m, v in conc.items()}, None, (0.9, 1.1))
            self.assertIs(analyst.add_run(record['id'], dict(tag, job=job, finished_at='2026-10-06T06:00:00+00:00')), True)
            done += 1
        return record['id']

    def calibrate(self, apply=True, **how):
        record_id = self.run_calibration(**how)
        record = self.analyst.finish(record_id, 'completed')
        self.assertEqual(record['status'], 'analyzed', record.get('error'))
        if apply:
            self.analyst.apply(record_id)
        return record_id

    def measure(self, kind, conc, shift, analyst=None):
        job = self.discharge(conc, shift, (0.95, 1.05))
        return (analyst or self.analyst).measure(job, kind, dict(INFO))

    def wait(self, condition, seconds=20):
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            if condition():
                return True
            time.sleep(.01)
        return False


class CalibrationRunTests(Bench):
    """Ported from the original: plan -> rounds -> analysis -> apply -> measurements."""

    def test_full_run_analyzes_and_applies(self):
        raw = plan(recipe_id=RECIPE_ID)
        info = self.analyst.check_plan(raw, STANDARD, None)
        self.assertEqual((info['runs'], info['kept_runs']), (9, 6))
        self.assertEqual(sum(1 for step in info['steps'] if step['action'] == 'operator_prompt'), 3)
        revs = dict(self.analyst.revs)
        record_id = self.run_calibration(raw)
        self.assertEqual(self.analyst.running()['id'], record_id)
        self.assertEqual(self.analyst.running()['runs'], 9)
        record = self.analyst.finish(record_id, 'completed')
        self.assertIsNone(self.analyst.running())
        self.assertEqual(record['status'], 'analyzed', record.get('error'))
        self.assertIsNone(record['error'])
        self.assertEqual(sum(1 for run in record['runs'] if run['discard']), 3)
        self.assertEqual(record['reference']['blank_count'], 2)
        self.assertEqual(len(record['reference']['axis']), len(AXIS))
        self.assertEqual(len(record['analysis']['jobs']), 6)
        self.assertEqual(record['analysis']['method_id'], BUILT_IN_METHOD)
        self.assertEqual(record['analysis']['config'], sa.validate_config(sa.DEFAULT_CONFIG))
        self.assertEqual(record['analysis']['config_path'], str(self.data / 'analysis_config.json'))
        self.assertNotIn('frames_per_sum', record)
        self.assertNotIn('frames_per_sum', record['analysis'])
        for metal in ('Cu', 'Zn', 'Ni'):
            fit = record['analysis']['fits'][metal]
            self.assertTrue(fit['valid'], fit.get('error'))
            self.assertEqual(fit['n'], 6)
            self.assertGreater(fit['r2'], 0.999)
        self.assertEqual(record, self.analyst.record(record_id))
        self.assertEqual(record, self.stored(f'calibrations/{record_id}.json'))
        self.assertGreater(self.analyst.revs['calibrations'], revs['calibrations'])
        self.assertIsNone(self.analyst.active())
        applied = self.analyst.apply(record_id)
        self.assertEqual(set(applied), {'applied', 'config', 'active'})
        self.assertEqual(applied['applied'], ['Cu', 'Zn', 'Ni'])
        self.assertEqual(applied['config']['Cu']['c'], '1')
        self.assertAlmostEqual(float(applied['config']['Cu']['a']), record['analysis']['fits']['Cu']['a'])
        self.assertEqual(applied['config'], self.analyst.coefficients())
        self.assertEqual(applied['active'], self.analyst.active())
        self.assertEqual(self.analyst.active()['calibration_id'], record_id)
        self.assertEqual(self.analyst.records()[0]['id'], record_id)
        self.assertEqual(self.analyst.records()[0]['applied_at'], applied['active']['applied_at'])
        self.assertEqual(self.events, [])  # the runner announces the result; nothing went wrong here
        self.assertEqual(self.alarms, [])

    def test_runs_after_calibration_give_concentrations_and_blank_checks(self):
        record_id = self.calibrate()
        blank = self.measure('blank', {}, 0.004)
        self.assertEqual(blank['qc']['status'], 'ok', blank['qc'])
        sample = self.measure('sample', {'Cu': 1.5, 'Zn': 0.7, 'Ni': 3}, -0.02)
        for metal, expected in (('Cu', 1.5), ('Zn', 0.7), ('Ni', 3)):
            self.assertAlmostEqual(sample['metals'][metal]['conc'], expected, delta=expected * 0.05)
        self.assertNotIn('qc', sample)
        self.assertEqual((sample['valid'], sample['conc_reason'], sample['calibration_id'], sample['method_id']),
                         (True, '', record_id, BUILT_IN_METHOD))
        # Shifts are measured against the calibration's reference: its first kept blank (discharge 2).
        base = random.Random(2).uniform(-0.03, 0.03)
        self.assertAlmostEqual(sample['shift_nm'], abs(-0.02 - base), delta=0.004)
        self.assertAlmostEqual(blank['shift_nm'], abs(0.004 - base), delta=0.004)
        drifted = self.measure('blank', {'Cu': 0.3}, 0.12)
        self.assertEqual(drifted['qc']['status'], 'fail')
        self.assertTrue(any('Displacement' in p for p in drifted['qc']['problems']))
        self.assertTrue(any(p.startswith('Cu apparent concentration') for p in drifted['qc']['problems']))
        latest = self.analyst.latest()
        self.assertEqual(latest['latest_blank']['id'], drifted['id'])
        self.assertEqual(latest['latest_sample']['id'], sample['id'])
        self.assertEqual(latest['latest_blank']['qc'], drifted['qc'])
        reply = self.analyst.measurements(24)
        self.assertEqual(set(reply), {'rows', 'qc', 'calibration', 'coefficients', 'blank_zero', 'unit'})
        self.assertEqual([row['kind'] for row in reply['rows']], ['blank', 'sample', 'blank'])
        self.assertEqual(reply['rows'], self.analyst.measurements()['rows'])
        self.assertEqual(reply['calibration'], {key: self.analyst.active()[key]
                                                for key in ('calibration_id', 'applied_at', 'metals')})
        self.assertEqual((reply['unit'], reply['qc'], reply['coefficients']),
                         ('mg/L', meas.validate_qc({}), self.analyst.coefficients()))
        self.assertEqual(meas.summary(drifted)['qc'], 'fail')
        self.assertAlmostEqual(meas.summary(sample)['conc']['Cu'], 1.5, delta=0.08)
        # What the runner gave is on the record; the record is one line of measurements.jsonl.
        for key, value in INFO.items():
            self.assertEqual(sample[key], value)
        lines = (self.data / 'measurements.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual([json.loads(line) for line in lines], [blank, sample, drifted])
        self.assertNotIn('frames_per_sum', sample)
        self.assertEqual(self.alarms, [])  # the runner raises the blank alarm from the record it gets back

    def test_stopped_run_keeps_its_rounds(self):
        record_id = self.run_calibration(upto=4)  # the blank's three rounds and the first of STD-1
        record = self.analyst.finish(record_id, 'stopped', 'Stop pressed')
        self.assertEqual((record['status'], record['error'], len(record['runs'])), ('stopped', 'Stop pressed', 4))
        self.assertNotIn('analysis', record)
        self.assertIsNotNone(record['finished_at'])
        self.assertIsNone(self.analyst.running())
        self.assertEqual(self.analyst.record(record_id), record)
        self.assertEqual(self.analyst.finish(record_id, 'failed', 'again'), record)  # ended once
        self.assertIs(self.analyst.add_run(record_id, {'level': 1, 'job': job_id(99)}), False)  # no more rounds
        with self.assertRaisesRegex(RuntimeError, 'Calibration analysis has not been completed yet'):
            self.analyst.apply(record_id)
        # The rounds already measured can still be analysed: here only the blank has kept rounds.
        analysed = self.analyst.analyze(record_id)
        self.assertEqual(analysed['status'], 'analyzed')
        self.assertEqual(analysed['reference']['blank_count'], 2)
        self.assertEqual({fit['error'] for fit in analysed['analysis']['fits'].values()}, {'Insufficient effective points Is 2 different concentrations'})
        with self.assertRaisesRegex(ValueError, 'No metal has made a valid return'):
            self.analyst.apply(record_id)
        failed = self.analyst.finish(self.run_calibration(upto=1), 'failed', 'No. 7 step (discharge＋Failed to capture spectrum):RPi Connection interrupted')
        self.assertEqual((failed['status'], failed['error']), ('failed', 'No. 7 step (discharge＋Failed to capture spectrum):RPi Connection interrupted'))
        with self.assertRaisesRegex(ValueError, 'The calibration status is incorrect'):
            self.analyst.finish(record_id, 'done')

    def test_prime_recipe_runs_once_after_each_swap(self):
        raw = plan(recipe_id=RECIPE_ID, prime_recipe_id=PRIME_ID)
        info = self.analyst.check_plan(raw, STANDARD, PRIMING)
        self.assertEqual(info['prime_recipe_name'], 'prime')
        steps = info['steps']
        for index, step in enumerate(steps):
            if step['action'] == 'operator_prompt':  # right after the swap: the prime Recipe, before any round
                self.assertEqual([s['action'] for s in steps[index + 1:index + 1 + len(PRIME)]], [row[0] for row in PRIME])
                self.assertEqual(steps[index + 1 + len(PRIME)]['calibration'].get('prime'), None)
        self.assertEqual(sum(1 for step in steps if step['action'] == 'plasma_run'), 9)
        record_id = self.run_calibration(raw, prime=PRIMING)
        record = self.analyst.record(record_id)
        self.assertEqual(record['prime_recipe'], {key: PRIMING[key] for key in ('id', 'name', 'sha256')})
        self.assertEqual(len(record['runs']), 9)
        with self.assertRaisesRegex(ValueError, 'Fluid replacement and draining Recipe cannot be used with standard products Recipe same'):
            self.analyst.check_plan(plan(recipe_id=RECIPE_ID, prime_recipe_id=RECIPE_ID), STANDARD, STANDARD)


class PlanTests(Bench):
    def test_check_plan_reply(self):
        info = self.analyst.check_plan(plan(recipe_id=RECIPE_ID, prime_recipe_id=PRIME_ID), STANDARD, PRIMING)
        self.assertEqual(set(info), {'plan', 'warnings', 'recipe_name', 'prime_recipe_name', 'prime_delay_seconds',
                                     'runs', 'kept_runs', 'round_delay_seconds', 'estimated_seconds', 'steps', 'phases'})
        self.assertEqual(info['plan'], cal.validate_plan(plan(recipe_id=RECIPE_ID, prime_recipe_id=PRIME_ID)))
        self.assertEqual((info['recipe_name'], info['prime_recipe_name'], info['warnings']), ('STD round', 'prime', []))
        self.assertEqual((info['runs'], info['kept_runs'], info['round_delay_seconds'], info['prime_delay_seconds']),
                         (9, 6, 0.2, 0.2))
        self.assertEqual(info['estimated_seconds'], round(32.5 * 9 + 8.0 * 3))  # the Recipes' own estimates
        self.assertEqual(len(info['steps']), 3 * (1 + len(PRIME)) + 9 * len(ROUND))
        self.assertEqual(info['phases'][0], {'name': 'Fluid change', 'from': 1, 'to': 1})
        self.assertEqual(info['phases'][1], {'name': 'Fluid replacement and draining', 'from': 2, 'to': 7})
        self.assertEqual(info['phases'][-1]['to'], len(info['steps']))
        json.dumps(info, allow_nan=False)
        # Without an estimate: the delays, as the original counted.
        plain = dict(STANDARD, estimate_seconds=None)
        info = self.analyst.check_plan(plan(recipe_id=RECIPE_ID), plain, None)
        self.assertEqual((info['estimated_seconds'], info['prime_recipe_name'], info['prime_delay_seconds']),
                         (round(0.2 * 9), None, 0.0))
        self.assertEqual(self.analyst.check_plan(plan(recipe_id=RECIPE_ID), STANDARD, PRIMING)['prime_recipe_name'], None)
        raw = plan(recipe_id=RECIPE_ID)
        raw['levels'][0]['conc']['Zn'] = 0.2
        self.assertEqual(len(self.analyst.check_plan(raw, STANDARD, None)['warnings']), 1)
        self.assertEqual(self.disk.written, [])  # checking stores nothing

    def test_check_plan_refusals(self):
        double = published(RECIPE_ID, 'double', ROUND + [('plasma_run',)])
        with_rpi = published(PRIME_ID, 'rpi prime', PRIME + [('rpi_status',)])
        invalid = published(RECIPE_ID, 'old', ROUND, valid=False, error='No. 5 column: The syringe cannot be loaded (existing 1 mL,capacity 1.25 mL) ')
        cases = [
            (plan(recipe_id=''), None, None, 'Please select the standard product Recipe'),
            (plan(recipe_id=RECIPE_ID), None, None, 'Recipe does not exist'),
            (plan(recipe_id=RECIPE_ID), PRIMING, None, 'Recipe does not exist'),  # another Recipe than the plan names
            (plan(recipe_id=RECIPE_ID, prime_recipe_id=PRIME_ID), STANDARD, None, 'Recipe does not exist'),
            (plan(recipe_id=RECIPE_ID), {'id': RECIPE_ID, 'name': 'x'}, None, 'Recipe does not exist'),
            (plan(recipe_id=RECIPE_ID), invalid, None, 'No. 5 column: The syringe cannot be loaded (existing 1 mL,capacity 1.25 mL) '),
            (plan(recipe_id=RECIPE_ID), double, None, 'standard product Recipe must contain exactly 1 indivual plasma_run (at present 2 ); the number of rounds can be changed on the calibration page.'),
            (plan(recipe_id=RECIPE_ID, prime_recipe_id=PRIME_ID), STANDARD, with_rpi,
             'Fluid replacement and draining Recipe can only have pump, syringe pump and waiting steps (cannot measure content or RPi steps)'),
            (plan(recipe_id=RECIPE_ID, discard_rounds=9), STANDARD, None, 'The number of pre-running rounds for each concentration must be 0–3 integer'),
            ('plan', STANDARD, None, 'Calibration plan fields are incorrect'),
        ]
        for raw, standard, prime, text in cases:
            with self.assertRaises(ValueError, msg=text) as caught:
                self.analyst.check_plan(raw, standard, prime)
            self.assertEqual(str(caught.exception), text)

    def test_with_the_real_recipe_module(self):
        """The steps recipe.validate() really gives (another package's module; skipped while it is absent)."""
        csv_file = ROOT / 'pc' / 'recipes' / 'plasma-only.csv'
        try:
            import recipe as real
            text = csv_file.read_text(encoding='utf-8')
        except Exception as exc:
            self.skipTest(f'pc/recipe.py or its example Recipe is not usable here: {exc}')
        check = real.validate(text, None)  # “Discharge measurement only” needs no pump board
        self.assertTrue(check['valid'], check['error'])
        only = {'id': RECIPE_ID, 'name': 'Only discharge measurement', 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                'validated': check, 'estimate_seconds': real.estimate(check, None, 21, 0.4)}
        info = self.analyst.check_plan(plan(recipe_id=RECIPE_ID), only, None)
        self.assertEqual((info['recipe_name'], info['runs'], info['kept_runs'], info['estimated_seconds']),
                         ('Only discharge measurement', 9, 6, 9 * 21))
        self.assertEqual([step['action'] for step in info['steps']], (['operator_prompt'] + ['plasma_run'] * 3) * 3)
        self.assertEqual([step['n'] for step in info['steps']], list(range(1, 13)))
        for step in info['steps']:
            self.assertEqual(set(step), set(check['steps'][0]) | {'calibration'})
            phase = info['phases'][step['phase']]
            self.assertTrue(phase['from'] <= step['n'] <= phase['to'])
        self.assertEqual([phase['name'] for phase in info['phases']], ['Fluid change', check['phases'][0]['name']] * 3)
        self.assertNotIn('operator_prompt', real.ACTIONS)  # generated here, never written in a CSV

    def test_plan_draft_is_kept_as_entered(self):
        self.assertEqual(self.analyst.plan(), cal.DEFAULT_PLAN)
        self.analyst.plan()['levels'].clear()
        self.assertEqual(len(self.analyst.plan()['levels']), 3)  # a copy was handed out
        draft = {'recipe_id': RECIPE_ID, 'discard_rounds': 1, 'levels': [
            {'name': 'Blank', 'conc': {'Cu': 0, 'Zn': 0, 'Ni': 0}, 'rounds': 3},
            {'name': 'STD-1', 'conc': {'Cu': None, 'Zn': None, 'Ni': None}, 'rounds': 3}]}  # not filled in yet
        reply = self.analyst.save_plan(draft)
        self.assertEqual(reply, {'saved': True, 'valid': False, 'error': 'can only have one blank standard', 'warnings': []})
        self.assertEqual(self.analyst.plan(), draft)
        self.assertEqual(self.stored('calibration_plan.json'), draft)
        self.assertEqual(self.disk.written[-1], self.data / 'calibration_plan.json')
        good = plan(recipe_id=RECIPE_ID)
        good['levels'][0]['conc']['Zn'] = 0.2
        reply = self.analyst.save_plan(good)
        self.assertEqual(set(reply), {'saved', 'valid', 'plan', 'warnings'})
        self.assertEqual((reply['saved'], reply['valid'], reply['plan'], len(reply['warnings'])),
                         (True, True, cal.validate_plan(good), 1))
        self.assertEqual(self.analyst.plan(), good)  # as entered: not sorted, not cleaned
        self.assertEqual(self.start(None).plan(), good)  # survives a restart
        for bad in (None, [good], 'plan', dict(good, note='x' * 20_000)):
            with self.assertRaisesRegex(ValueError, 'Calibration plan format is incorrect'):
                self.analyst.save_plan(bad)
        self.assertEqual(self.analyst.plan(), good)


class RecordTests(Bench):
    def test_new_record(self):
        info = self.analyst.check_plan(plan(recipe_id=RECIPE_ID), STANDARD, None)
        revs = dict(self.analyst.revs)
        record = self.analyst.create(info['plan'], STANDARD, None, 'Wang Xiaoming', 'c' * 32)
        self.assertEqual(set(record), {'id', 'created_at', 'status', 'operator', 'plan', 'recipe', 'prime_recipe',
                                       'warnings', 'runs', 'schedule_id'})
        self.assertRegex(record['id'], r'^CAL-[0-9a-f]{32}$')
        self.assertEqual((record['status'], record['operator'], record['plan'], record['prime_recipe'], record['warnings'],
                          record['runs'], record['schedule_id']),
                         ('running', 'Wang Xiaoming', info['plan'], None, [], [], 'c' * 32))
        self.assertEqual(record['recipe'], {key: STANDARD[key] for key in ('id', 'name', 'sha256')})
        self.assertEqual(self.stored(f'calibrations/{record["id"]}.json'), record)
        self.assertEqual(self.disk.written[-1], self.data / 'calibrations' / f'{record["id"]}.json')
        self.assertEqual(self.analyst.revs['calibrations'], revs['calibrations'] + 1)
        self.assertEqual(self.analyst.running(), cal.summary(record))
        self.assertEqual(self.analyst.records(), [cal.summary(record)])
        self.assertEqual(self.analyst.record(record['id']), record)
        record['runs'].append('x')  # copies are handed out
        self.assertEqual(self.analyst.record(record['id'])['runs'], [])
        with self.assertRaisesRegex(ValueError, 'Recipe does not exist'):
            self.analyst.create(info['plan'], None, None, 'Wang Xiaoming', 'c' * 32)
        with self.assertRaisesRegex(ValueError, 'Calibration plan fields are incorrect'):
            self.analyst.create({'levels': 5, 'x': 1}, STANDARD, None, 'Wang Xiaoming', 'c' * 32)

    def test_rounds_are_added_in_order(self):
        record_id = self.run_calibration(upto=0)
        first = {'level': 0, 'level_name': 'Blank', 'run': 1, 'discard': True, 'round': None, 'job': job_id(1),
                 'finished_at': '2026-10-06T06:00:00+00:00'}
        second = {'level': 0, 'level_name': 'Blank', 'run': 2, 'discard': False, 'round': 1, 'job': job_id(2)}
        revs = dict(self.analyst.revs)
        self.assertIs(self.analyst.add_run(record_id, dict(first, extra='not kept')), True)
        self.assertIs(self.analyst.add_run(record_id, second), True)
        runs = self.analyst.record(record_id)['runs']
        self.assertEqual(runs[0], first)
        self.assertEqual({key: runs[1][key] for key in second}, second)
        self.assertRegex(runs[1]['finished_at'], r'^\d{4}-\d\d-\d\dT')  # the time of the call when none is given
        self.assertEqual(self.stored(f'calibrations/{record_id}.json')['runs'], runs)
        self.assertEqual(self.analyst.revs['calibrations'], revs['calibrations'] + 2)
        self.assertEqual(self.analyst.running()['runs'], 2)
        # Never an exception towards the runner: the reason goes to the event log.
        for cal_id, info in (('CAL-' + '0' * 32, first), (record_id, dict(first, level=3)),
                             (record_id, dict(first, level=True)), (record_id, dict(first, job=None)),
                             (record_id, {'job': job_id(3)}), (record_id, None), (None, first)):
            self.events.clear()
            self.assertIs(self.analyst.add_run(cal_id, info), False)
            self.assertEqual(len(self.events), 1)
            self.assertEqual(self.events[0][0], 'error')
            self.assertTrue(self.events[0][1].startswith('Calibration record failed to update:'), self.events[0][1])
        self.assertEqual(len(self.analyst.record(record_id)['runs']), 2)

    def test_a_failed_write_does_not_lose_a_round(self):
        record_id = self.run_calibration(upto=0)
        run = {'level': 0, 'level_name': 'Blank', 'run': 1, 'discard': True, 'round': None, 'job': job_id(1)}
        self.disk.broken = lambda path: path.parent.name == 'calibrations'
        self.assertIs(self.analyst.add_run(record_id, run), False)
        self.assertEqual(self.events, [('error', 'Calibration record failed to update:[Errno 28] No space left on device')])
        self.assertEqual(self.stored(f'calibrations/{record_id}.json')['runs'], [])
        self.assertEqual(len(self.analyst.record(record_id)['runs']), 1)  # the live record has it
        self.disk.broken = None
        self.assertIs(self.analyst.add_run(record_id, dict(run, run=2, discard=False, round=1, job=job_id(2))), True)
        self.assertEqual([item['run'] for item in self.stored(f'calibrations/{record_id}.json')['runs']], [1, 2])
        # A record that cannot be written at all: the calibration does not start.
        self.analyst.finish(record_id, 'stopped')
        self.disk.broken = lambda path: True
        count = len(self.analyst.records())
        with self.assertRaisesRegex(RuntimeError, 'Calibration record failed to update:'):
            self.analyst.create(cal.validate_plan(plan(recipe_id=RECIPE_ID)), STANDARD, None, 'Wang Xiaoming', 'c' * 32)
        self.assertIsNone(self.analyst.running())
        self.assertEqual(len(self.analyst.records()), count)

    def test_the_end_of_a_run_on_a_disk_that_cannot_be_written(self):
        record_id = self.run_calibration()
        self.disk.broken = lambda path: path.parent.name == 'calibrations'
        done = self.analyst.finish(record_id, 'completed')  # no exception towards the runner
        self.assertEqual(done['status'], 'analyzed')  # and it still gets the result
        self.assertEqual({event for event in self.events},
                         {('error', 'Calibration record failed to update:[Errno 28] No space left on device')})
        self.assertIsNone(self.analyst.running())
        self.assertEqual(self.stored(f'calibrations/{record_id}.json')['status'], 'running')  # what the disk has
        self.disk.broken = None
        restarted = self.start(None)  # the rounds are there; the analysis can be repeated
        record = restarted.record(record_id)
        self.assertEqual((record['status'], len(record['runs'])), ('interrupted', 9))
        self.assertEqual(restarted.analyze(record_id)['status'], 'analyzed')

    def test_settings_that_cannot_be_written_stay_as_they_were(self):
        before = (self.analyst.coefficients(), self.analyst.qc(), self.analyst.blank_zero(), self.analyst.plan())
        revs = dict(self.analyst.revs)
        self.disk.broken = lambda path: True
        changes = (lambda: self.analyst.set_coefficients({m: {'a': '2', 'b': '0', 'c': '1'} for m in ('Cu', 'Zn', 'Ni')}),
                   lambda: self.analyst.set_qc({'max_shift_nm': 0.5}),
                   lambda: self.analyst.set_blank_zero({'window': 20}),
                   lambda: self.analyst.save_plan(plan(recipe_id=RECIPE_ID)))
        for change in changes:
            with self.assertRaises(OSError):
                change()
        self.assertEqual((self.analyst.coefficients(), self.analyst.qc(), self.analyst.blank_zero(), self.analyst.plan()),
                         before)
        self.assertEqual(self.analyst.revs, revs)

    def test_a_run_whose_end_never_came_is_closed_by_the_next_one(self):
        first = self.run_calibration(upto=2)
        second = self.run_calibration(upto=0)
        self.assertEqual(self.analyst.running()['id'], second)
        stale = self.analyst.record(first)
        self.assertEqual((stale['status'], stale['error'], len(stale['runs'])), ('interrupted', 'program closed during calibration execution or analysis', 2))

    def test_listing_and_lookup(self):
        ids = []
        for number in range(3):
            with mock.patch.object(analyst_module, 'now', return_value=f'2026-10-0{number + 1}T00:00:00+00:00'):
                ids.append(self.run_calibration(upto=0))
                self.analyst.finish(ids[-1], 'stopped')
        (self.data / 'calibrations' / ('CAL-' + '9' * 32 + '.json')).write_text('{broken', encoding='utf-8')
        (self.data / 'calibrations' / 'notes.txt').write_text('x', encoding='utf-8')
        rows = self.analyst.records()
        self.assertEqual([row['id'] for row in rows], ids[::-1])  # newest first
        self.assertEqual([row['id'] for row in self.analyst.records(limit=2)], ids[:0:-1])
        self.assertEqual(set(rows[0]), {'id', 'created_at', 'status', 'operator', 'error', 'applied_at', 'finished_at',
                                        'recipe_name', 'levels', 'runs', 'method_id', 'fits'})
        self.assertEqual([row['id'] for row in self.start(None).records()], ids[::-1])  # and after a restart
        with self.assertRaises(KeyError) as caught:
            self.analyst.record('CAL-' + '0' * 32)
        self.assertEqual(caught.exception.args[0], 'Calibration record does not exist')
        with self.assertRaises(KeyError):
            self.analyst.record('CAL-' + '9' * 32)  # a file that is not a record
        for bad in (None, 5, '', 'CAL-1', '../' + ids[0], ids[0] + '.json', ids[0].upper()):
            with self.assertRaisesRegex(ValueError, 'calibration record ID format is incorrect'):
                self.analyst.record(bad)
        for call in (self.analyst.analyze, self.analyst.request_analysis, self.analyst.apply,
                     lambda cal_id: self.analyst.set_exclusions(cal_id, {}),
                     lambda cal_id: self.analyst.finish(cal_id, 'stopped')):
            with self.assertRaises(KeyError):
                call('CAL-' + '0' * 32)
            with self.assertRaises(ValueError):
                call('nope')


class AnalysisTests(Bench):
    def test_reanalysis_and_exclusions_do_not_change_what_is_applied(self):
        record_id = self.calibrate()
        record = self.analyst.record(record_id)
        active = self.analyst.active()
        snapshot = self.stored('calibration_active.json')
        coefficients = self.analyst.coefficients()
        sample = self.discharge({'Cu': 1.5, 'Zn': 0.7, 'Ni': 3}, -0.02, (0.95, 1.05))
        before = self.analyst.measure(sample, 'sample', dict(INFO))

        def same_measurement():
            again = self.analyst.measure(sample, 'sample', dict(INFO))
            self.assertEqual({key: again[key] for key in again if key not in ('id', 'time')},
                             {key: before[key] for key in before if key not in ('id', 'time')})
            self.assertEqual(self.analyst.active(), active)
            self.assertEqual(self.stored('calibration_active.json'), snapshot)
            self.assertEqual(self.analyst.coefficients(), coefficients)
            self.assertEqual(self.stored('calibration_config.json'), coefficients)
        same_measurement()
        # 1. Re-analysis after the blank's spectra were replaced: another reference, other fits.
        first_blank = next(run['job'] for run in record['runs'] if run['level'] == 0 and not run['discard'])
        self.runs.put(first_blank, spectrum({}, 0.05, 1.0))
        analysed = self.analyst.analyze(record_id)
        self.assertEqual(analysed['status'], 'analyzed')
        self.assertNotEqual(analysed['reference']['values'], record['reference']['values'])
        self.assertNotEqual(analysed['analysis']['fits']['Cu']['a'], record['analysis']['fits']['Cu']['a'])
        self.assertEqual(analysed['applied_at'], active['applied_at'])
        same_measurement()
        # 2. A standard left out of one metal's fit.
        excluded = self.analyst.set_exclusions(record_id, {'Cu': [2]})
        self.assertEqual(excluded['exclude_levels'], {'Cu': [2]})
        self.assertEqual(excluded['analysis']['fits']['Cu']['n'], 4)
        self.assertNotEqual(excluded['analysis']['fits']['Cu']['a'], analysed['analysis']['fits']['Cu']['a'])
        same_measurement()
        # 3. A restart reads the snapshot, not the record (the original read the record again).
        restarted = self.start(None)
        self.assertEqual(restarted.active(), active)
        again = restarted.measure(sample, 'sample', dict(INFO))
        self.assertEqual(again['metals'], before['metals'])
        self.assertEqual(again['shift_nm'], before['shift_nm'])
        # 4. “Reapply” is what changes measurements.
        applied = self.analyst.apply(record_id)
        self.assertNotEqual(applied['active']['applied_at'], active['applied_at'])
        self.assertEqual(applied['active']['previous'], coefficients)
        self.assertAlmostEqual(float(applied['config']['Cu']['a']), excluded['analysis']['fits']['Cu']['a'])
        after = self.analyst.measure(sample, 'sample', dict(INFO))
        self.assertAlmostEqual(after['shift_nm'], abs(-0.02 - 0.05), delta=0.004)  # against the new reference
        self.assertNotAlmostEqual(after['shift_nm'], before['shift_nm'], delta=0.01)
        self.assertNotEqual(after['metals']['Cu']['conc'], before['metals']['Cu']['conc'])

    def test_changed_settings_need_a_new_calibration(self):
        record_id = self.calibrate()
        sample = self.discharge({'Cu': 1.5, 'Zn': 0.7, 'Ni': 3}, -0.02, (0.95, 1.05))
        self.assertIsNotNone(self.analyst.measure(sample, 'sample')['metals']['Cu']['conc'])
        changed = json.loads(json.dumps(sa.DEFAULT_CONFIG))
        changed['metals']['Zn']['bg_left'] = None
        self.settings(changed)
        method = self.analyst.settings_view()['method_id']
        self.assertNotEqual(method, BUILT_IN_METHOD)
        record = self.analyst.measure(sample, 'sample')
        self.assertEqual((record['valid'], record['method_id'], record['conc_reason']),
                         (True, method, 'The analysis settings are different from the applied calibration, please recalibrate.'))
        self.assertEqual([item['conc'] for item in record['metals'].values()], [None, None, None])
        self.assertIsNotNone(record['metals']['Cu']['intensity'])
        self.assertFalse(any(item['applied'] for item in record['blank_zero'].values()))  # nothing to correct
        blank = self.analyst.measure(sample, 'blank')
        self.assertEqual(blank['qc'], {'status': 'unchecked', 'problems': [],
                                       'reason': 'The analysis settings are different from the applied calibration. Please recalibrate. The baseline cannot be determined.'})
        # The same raw spectra with the new settings, then apply: concentrations are back.
        analysed = self.analyst.analyze(record_id)
        self.assertEqual((analysed['status'], analysed['analysis']['method_id']), ('analyzed', method))
        self.assertEqual(self.analyst.active()['method_id'], BUILT_IN_METHOD)  # still the applied one
        self.assertIsNone(self.analyst.measure(sample, 'sample')['metals']['Cu']['conc'])
        self.analyst.apply(record_id)
        self.assertEqual(self.analyst.active()['method_id'], method)
        self.assertAlmostEqual(self.analyst.measure(sample, 'sample')['metals']['Zn']['conc'], 0.7, delta=0.035)

    def test_what_stops_an_analysis(self):
        running = self.run_calibration(upto=3)
        with self.assertRaisesRegex(RuntimeError, 'This correction is still in progress'):
            self.analyst.analyze(running)
        self.analyst.finish(running, 'stopped')
        only_discard = self.run_calibration(upto=1)
        self.analyst.finish(only_discard, 'stopped')
        before = self.analyst.record(only_discard)
        for call in (self.analyst.analyze, self.analyst.request_analysis):
            with self.assertRaisesRegex(ValueError, 'There is no measuring wheel of blank standard, and the reference spectrum cannot be established.'):
                call(only_discard)
        self.assertEqual(self.analyst.record(only_discard), before)  # not marked as being analysed
        # A calibration that completed without a kept blank round: said in the record, not raised.
        empty = self.analyst.finish(self.run_calibration(upto=1), 'completed')
        self.assertEqual((empty['status'], empty['error']), ('analysis_failed', 'There is no measuring wheel of blank standard, and the reference spectrum cannot be established.'))
        self.assertEqual(self.events, [])

    def test_spectra_that_cannot_be_read(self):
        record_id = self.run_calibration()
        record = self.analyst.record(record_id)
        kept = [run for run in record['runs'] if not run['discard']]
        gone, odd = kept[0]['job'], kept[3]['job']
        del self.runs.sets[gone]  # the first kept blank is gone
        self.runs.sets[odd] = json.dumps({'job': odd, 'kind': 'capture', 'axis': [1.0, 2.0]})
        unreadable = f'cannot read the spectrum set {gone}: No such file or directory'
        done = self.analyst.finish(record_id, 'completed')
        self.assertEqual(done['status'], 'analyzed', done.get('error'))
        self.assertEqual(done['reference']['blank_count'], 1)
        self.assertEqual(done['reference']['jobs'][0], {'job': gone, 'used': False, 'error': unreadable})
        self.assertEqual(done['reference']['jobs'][1]['used'], True)
        rows = {row['job']: row for row in done['analysis']['jobs']}
        self.assertEqual(rows[gone], {'job': gone, 'valid': False, 'error': unreadable})
        self.assertEqual(rows[odd], {'job': odd, 'valid': False, 'error': 'no signal frames in ' + odd})
        cu = done['analysis']['fits']['Cu']
        self.assertEqual((cu['valid'], cu['n']), (True, 4))
        self.assertEqual([item['reason'] for item in cu['excluded']], [unreadable, 'no signal frames in ' + odd])
        self.assertEqual(len(set(self.runs.loads)), len(self.runs.loads))  # every set is read once
        # No blank left at all.
        del self.runs.sets[kept[1]['job']]
        failed = self.analyst.analyze(record_id)
        self.assertEqual((failed['status'], failed['error']),
                         ('analysis_failed', 'no readable blank job for the reference spectrum'))
        self.assertEqual(failed['analysis'], done['analysis'])  # the last good result stays in the record
        with self.assertRaisesRegex(RuntimeError, 'Calibration analysis has not been completed yet'):
            self.analyst.apply(record_id)

    def test_broken_settings_fail_the_analysis_not_the_program(self):
        record_id = self.run_calibration()
        self.settings('{"metals": ')
        done = self.analyst.finish(record_id, 'completed')
        self.assertEqual(done['status'], 'analysis_failed')
        self.assertTrue(done['error'].startswith('analysis_config.json: not valid JSON'), done['error'])
        self.settings(sa.DEFAULT_CONFIG)
        self.assertEqual(self.analyst.analyze(record_id)['status'], 'analyzed')
        self.settings({'metals': {'Cu': {'peak': [327.5, 327.1]}}})
        again = self.analyst.analyze(record_id)
        self.assertEqual((again['status'], again['error']),
                         ('analysis_failed', 'analysis_config.json: Cu.peak must be [low_nm, high_nm] with low < high'))
        with mock.patch.object(sa, 'build_reference', side_effect=RuntimeError('x' * 900)):
            self.settings(sa.DEFAULT_CONFIG)
            self.assertEqual(self.analyst.analyze(record_id)['error'], 'x' * 400)
        # A result no JSON file can hold fails the analysis; the record is not left as "analyzing".
        good = self.analyst.analyze(record_id)
        self.assertEqual(good['status'], 'analyzed')
        bad_fits = {'Cu': {'valid': True, 'a': float('inf'), 'b': 0.0, 'r2': float('nan'), 'n': 6}}
        with mock.patch.object(cal, 'regress', return_value=bad_fits):
            odd = self.analyst.analyze(record_id)
        self.assertEqual(odd['status'], 'analysis_failed')
        self.assertTrue(odd['error'].startswith('Out of range float values are not JSON compliant'), odd['error'])
        self.assertEqual(odd['analysis'], good['analysis'])
        self.assertEqual(self.stored(f'calibrations/{record_id}.json'), odd)
        self.assertEqual(self.events, [])

    def test_one_analysis_at_a_time(self):
        first = self.calibrate(apply=False)
        second = self.run_calibration()
        self.runs.gate = threading.Event()
        reply = self.analyst.request_analysis(first)
        self.assertEqual(reply, {'accepted': True, 'id': first})
        self.assertIn('cal-analysis', [thread.name for thread in threading.enumerate()])
        self.assertEqual(self.analyst.record(first)['status'], 'analyzing')
        self.assertEqual(self.analyst.records()[1]['status'], 'analyzing')
        for call in (lambda: self.analyst.analyze(first), lambda: self.analyst.request_analysis(first),
                     lambda: self.analyst.set_exclusions(first, {'Cu': [2]})):
            with self.assertRaisesRegex(RuntimeError, 'Calibration analysis is in progress, please wait.'):
                call()
        with self.assertRaisesRegex(RuntimeError, 'Calibration analysis has not been completed yet'):
            self.analyst.apply(first)
        # The run that ends meanwhile waits for it, but not for ever.
        with mock.patch.object(analyst_module, 'ANALYSIS_WAIT_S', 0.05):
            done = self.analyst.finish(second, 'completed')
        self.assertEqual((done['status'], done['error']), ('analysis_failed', 'Calibration analysis is in progress, please wait.'))
        self.assertEqual(len(done['runs']), 9)
        self.runs.gate.set()
        self.assertTrue(self.wait(lambda: self.analyst.record(first)['status'] != 'analyzing'))
        self.assertEqual(self.analyst.record(first)['status'], 'analyzed')
        self.assertTrue(self.wait(lambda: not self.analyst._analysis.locked()))
        self.assertEqual(self.analyst.analyze(second)['status'], 'analyzed')  # Reanalysis afterwards
        self.assertEqual(self.analyst.set_exclusions(first, {'Cu': [2]})['exclude_levels'], {'Cu': [2]})

    def test_exclusions(self):
        record_id = self.calibrate(apply=False)
        record = self.analyst.record(record_id)
        revs = dict(self.analyst.revs)
        changed = self.analyst.set_exclusions(record_id, {'Cu': [2], 'Zn': []})
        self.assertEqual(changed['exclude_levels'], {'Cu': [2]})
        cu = changed['analysis']['fits']['Cu']
        self.assertEqual((cu['n'], len(cu['excluded']), cu['excluded'][0]['manual']), (4, 2, True))
        self.assertEqual(changed['analysis']['fits']['Zn'], record['analysis']['fits']['Zn'])
        self.assertEqual(changed['analysis']['jobs'], record['analysis']['jobs'])  # no new analysis
        self.assertEqual(changed, self.stored(f'calibrations/{record_id}.json'))
        self.assertEqual(self.analyst.revs['calibrations'], revs['calibrations'] + 1)
        self.assertEqual(self.analyst.records()[0]['fits']['Cu']['n'], 4)
        self.assertEqual(self.analyst.analyze(record_id)['analysis']['fits']['Cu']['n'], 4)  # kept by a re-analysis
        back = self.analyst.set_exclusions(record_id, None)
        self.assertEqual((back['exclude_levels'], back['analysis']['fits']['Cu']['n']), ({}, 6))
        for bad, text in (({'Fe': [0]}, 'The exclusion setting needs to be {Metal: [concentration serial number]}'), ({'Cu': [3]}, 'Cu is incorrect.')):
            with self.assertRaises(ValueError) as caught:
                self.analyst.set_exclusions(record_id, bad)
            self.assertEqual(str(caught.exception), text)
        # On a record that was never analysed the choice is stored for the analysis to come.
        stopped = self.run_calibration()
        self.analyst.finish(stopped, 'stopped')
        self.assertEqual(self.analyst.set_exclusions(stopped, {'Ni': [1]})['exclude_levels'], {'Ni': [1]})
        self.assertEqual(self.analyst.analyze(stopped)['analysis']['fits']['Ni']['n'], 4)

    def test_apply_rules(self):
        self.analyst.set_coefficients({'Cu': {'a': '3', 'b': '0.5', 'c': '1.1'}, 'Zn': {'a': '4', 'b': '0', 'c': '0.9'},
                                       'Ni': {'a': '7', 'b': '-1', 'c': '1.25'}})
        manual = self.analyst.coefficients()
        record_id = self.calibrate(apply=False)
        self.analyst.set_exclusions(record_id, {'Ni': [1, 2]})  # no line for Ni
        revs = dict(self.analyst.revs)
        applied = self.analyst.apply(record_id)
        fits = self.analyst.record(record_id)['analysis']['fits']
        self.assertEqual(applied['applied'], ['Cu', 'Zn'])
        self.assertEqual(applied['config']['Ni'], manual['Ni'])  # keeps its values
        self.assertEqual(applied['config']['Cu'], {'a': format(fits['Cu']['a'], '.10g'),
                                                   'b': format(fits['Cu']['b'], '.10g'), 'c': '1'})
        self.assertEqual(self.stored('calibration_config.json'), applied['config'])
        active = self.stored('calibration_active.json')
        self.assertEqual(set(active), {'calibration_id', 'applied_at', 'metals', 'method_id', 'unit', 'previous',
                                       'reference', 'blank_norm'})
        self.assertEqual((active['calibration_id'], active['metals'], active['method_id'], active['unit']),
                         (record_id, ['Cu', 'Zn'], BUILT_IN_METHOD, 'mg/L'))
        self.assertEqual(active['previous'], manual)
        record = self.analyst.record(record_id)
        self.assertEqual(active['reference'], {'axis': record['reference']['axis'], 'values': record['reference']['values']})
        self.assertEqual(set(active['blank_norm']), {'Cu', 'Zn', 'Ni'})  # these settings have normalize windows
        blank_rows = [row for row in record['analysis']['jobs'] if row['job'] in
                      {run['job'] for run in record['runs'] if run['level'] == 0 and not run['discard']}]
        self.assertAlmostEqual(active['blank_norm']['Cu'],
                               statistics.mean(row['metals']['Cu']['norm_area'] for row in blank_rows))
        self.assertEqual(applied['active'], {key: value for key, value in active.items() if key != 'reference'})
        self.assertEqual(self.analyst.active(), applied['active'])
        self.assertEqual((record['applied_at'], record['applied_metals'], record['previous_config']),
                         (active['applied_at'], ['Cu', 'Zn'], manual))
        self.assertGreater(self.analyst.revs['calibrations'], revs['calibrations'])
        self.assertGreater(self.analyst.revs['measurements'], revs['measurements'])
        sample = self.measure('sample', {'Cu': 1.5, 'Zn': 0.7, 'Ni': 3}, 0.0)
        self.assertAlmostEqual(sample['metals']['Cu']['conc'], 1.5, delta=0.075)
        self.assertIsNone(sample['metals']['Ni']['conc'])  # not applied: no concentration from old coefficients
        self.assertIsNotNone(sample['metals']['Ni']['intensity'])
        # Coefficients typed in by hand afterwards do not touch the snapshot.
        typed = dict(applied['config'], Cu={'a': '1', 'b': '0', 'c': '2'})
        self.assertEqual(self.analyst.set_coefficients(typed), typed)
        self.assertEqual(self.stored('calibration_active.json'), active)

    def test_apply_keeps_coefficients_and_snapshot_a_pair(self):
        record_id = self.calibrate(apply=False)
        before = self.analyst.coefficients()
        self.disk.broken = lambda path: path.name == 'calibration_active.json'
        with self.assertRaisesRegex(RuntimeError, 'Calibration record failed to update:'):
            self.analyst.apply(record_id)
        self.assertEqual(self.analyst.coefficients(), before)
        self.assertEqual(self.stored('calibration_config.json'), before)  # written back
        self.assertIsNone(self.analyst.active())
        self.assertNotIn('applied_at', self.analyst.record(record_id))
        self.assertEqual(self.measure('sample', {'Cu': 1}, 0.0)['conc_reason'], 'Correction has not been applied yet')
        self.disk.broken = None
        self.assertEqual(self.analyst.apply(record_id)['applied'], ['Cu', 'Zn', 'Ni'])


class MeasureTests(Bench):
    def test_without_a_calibration(self):
        revs = dict(self.analyst.revs)
        sample = self.measure('sample', {'Cu': 1}, 0.01)
        self.assertEqual(set(sample), {'id', 'time', 'kind', 'job', 'method_id', 'valid', 'error', 'shift_nm', 'metals',
                                       'calibration_id', 'conc_reason'} | set(INFO))
        self.assertEqual((sample['kind'], sample['job'], sample['method_id'], sample['valid'], sample['error'],
                          sample['shift_nm'], sample['calibration_id'], sample['conc_reason']),
                         ('sample', job_id(1), BUILT_IN_METHOD, True, '', None, None, 'Correction has not been applied yet'))
        self.assertGreater(sample['metals']['Cu']['intensity'], 0)
        self.assertEqual([item['conc'] for item in sample['metals'].values()], [None, None, None])
        self.assertEqual(self.analyst.revs['measurements'], revs['measurements'] + 1)
        blank = self.measure('blank', {}, 0.0)
        self.assertEqual(blank['qc'], {'status': 'unchecked', 'problems': [], 'reason': 'The correction has not been applied and the baseline cannot be determined.'})
        self.assertEqual(self.analyst.latest(), {'latest_sample': sample, 'latest_blank': blank})
        self.assertEqual(self.analyst.measurements()['calibration'], None)
        self.assertEqual(self.analyst.blank_zero(), {'settings': meas.validate_blank_zero({}), 'status': None})
        sample['metals']['Cu']['intensity'] = 'changed'  # a copy was handed out
        self.assertNotEqual(self.analyst.latest()['latest_sample']['metals']['Cu']['intensity'], 'changed')
        self.assertEqual(Analyst(self.data, self.runs, self.disk).latest()['latest_blank'], blank)  # read back at a start
        self.assertEqual(self.analyst.latest()['latest_blank'], blank)

    def test_never_raises(self):
        self.calibrate()
        good = self.discharge({'Cu': 1}, 0.0, (1.0, 1.0))
        missing = self.analyst.measure(job_id(999), 'sample', dict(INFO))
        self.assertEqual((missing['valid'], missing['method_id'], missing['conc_reason']),
                         (False, BUILT_IN_METHOD, 'This analysis is invalid'))
        self.assertEqual(missing['error'], f'cannot read the spectrum set {job_id(999)}: No such file or directory')
        self.assertEqual(missing['metals'], {m: {'intensity': None, 'conc': None} for m in ('Cu', 'Zn', 'Ni')})
        bad_id = self.analyst.measure('../x', 'blank', dict(INFO))
        self.assertEqual((bad_id['valid'], bad_id['error']), (False, 'Spectrum number format is incorrect'))
        self.assertEqual(bad_id['qc'], {'status': 'unchecked', 'problems': [], 'reason': 'This analysis is invalid and the baseline cannot be determined.'})
        none = self.analyst.measure(None, 'sample', None)  # load_run itself breaks
        self.assertEqual((none['valid'], none['method_id'], none['conc_reason'], none['job']), (False, None, 'Analysis failed', None))
        self.assertTrue(none['error'])
        with mock.patch.object(self.analyst, 'load_run', side_effect=RuntimeError('The disk is broken' * 200)):
            broken = self.analyst.measure(good, 'blank', dict(INFO))
        self.assertEqual((broken['conc_reason'], len(broken['error']), broken['calibration_id']),
                         ('Analysis failed', 300, self.analyst.active()['calibration_id']))
        self.assertEqual(broken['qc'], {'status': 'unchecked', 'problems': [], 'reason': 'Analysis failed, unable to determine baseline'})
        row = {'valid': True, 'shift': sa._no_shift('t'), 'error': '', 'signal_frames': 2, 'background_frames': 1,
               'metals': {'Cu': {'valid': True, 'intensity': float('nan')}}}
        with mock.patch.object(sa, 'analyze_one', return_value=row):  # a number the log cannot hold
            nan = self.analyst.measure(good, 'sample', dict(INFO))
        self.assertEqual((nan['valid'], nan['conc_reason']), (False, 'Analysis failed'))
        self.assertTrue(nan['error'].startswith('Out of range float values are not JSON compliant'), nan['error'])
        self.assertEqual(self.analyst.latest()['latest_sample'], nan)  # stored like any failed analysis
        self.assertEqual(self.analyst.measure(good, 'standard', dict(INFO))['kind'], 'sample')  # an unknown kind
        self.assertEqual(self.analyst.measure(good, None, 'info')['kind'], 'sample')
        # What the caller adds never replaces what the record says about itself.
        odd = self.analyst.measure(good, 'sample',
                                   {'id': 'mine', 'time': 'then', 'kind': 'blank', 'valid': False, 'slot': 7})
        self.assertRegex(odd['id'], r'^[0-9a-f]{32}$')
        self.assertEqual((odd['time'][:2], odd['kind'], odd['valid'], odd['slot']), ('20', 'sample', True, 7))
        self.assertEqual(self.events, [])
        stored = len(self.analyst.measurements()['rows'])
        revs = dict(self.analyst.revs)
        # A record that cannot be stored still comes back, and the reason is logged.
        odd = self.analyst.measure(good, 'sample', {'round_id': object()})
        self.assertRegex(odd['id'], r'^[0-9a-f]{32}$')
        self.assertAlmostEqual(odd['metals']['Cu']['conc'], 1.0, delta=0.05)
        self.assertEqual(len(self.events), 1)
        self.assertTrue(self.events[0][1].startswith('The measurement result failed to be archived:'))
        with mock.patch.object(self.analyst.log, 'add', side_effect=OSError(28, 'No space left on device')):
            unsaved = self.analyst.measure(good, 'sample', dict(INFO))
        self.assertEqual(self.events[-1], ('error', 'The measurement result failed to be archived:[Errno 28] No space left on device'))
        self.assertEqual(meas.summary(unsaved)['valid'], True)
        self.assertEqual(len(self.analyst.measurements()['rows']), stored)
        self.assertEqual(self.analyst.revs, revs)
        # A snapshot that was damaged on disk: the analysis fails, the program does not.
        active = self.stored('calibration_active.json')
        active['reference']['values'] = active['reference']['values'][:5]
        (self.data / 'calibration_active.json').write_text(json.dumps(active), encoding='utf-8')
        damaged = self.start(None).measure(good, 'sample', dict(INFO))
        self.assertEqual((damaged['conc_reason'], damaged['error']), ('Analysis failed', 'reference spectrum is malformed'))

    def test_broken_settings_file(self):
        """AMENDMENTS M5.6: error shown, a failed record, and the program still starts."""
        job = self.discharge({'Cu': 1}, 0.0, (1.0, 1.0))
        for text, error in (('{"metals": {"Cu": ', 'analysis_config.json: not valid JSON ('),
                            ('[]', 'analysis_config.json: analysis config keys: subtract_dark, shift, metals'),
                            ('{"metals": {}}', 'analysis_config.json: metals needs 1-10 entries'),
                            ('', 'analysis_config.json: not valid JSON (')):
            self.settings(text)
            view = self.analyst.settings_view()
            self.assertEqual((view['method_id'], view['config'], view['path']),
                             (None, None, str(self.data / 'analysis_config.json')))
            self.assertTrue(view['error'].startswith(error), view['error'])
            for kind in ('sample', 'blank'):
                record = self.analyst.measure(job, kind, dict(INFO))
                self.assertEqual((record['valid'], record['method_id'], record['conc_reason'], record['error']),
                                 (False, None, 'Analysis failed', view['error'][:300]))
            started = self.start(None)  # the program still starts
            self.assertEqual(started.settings_view(), view)
            self.assertEqual(started.measure(job, 'sample', dict(INFO))['conc_reason'], 'Analysis failed')
            self.assertEqual((self.data / 'analysis_config.json').read_text(encoding='utf-8'), text)  # left for the owner
        (self.data / 'analysis_config.json').write_bytes(b'\xff\xfe\x00{')
        os.utime(self.data / 'analysis_config.json', ns=(10 ** 18, 10 ** 18))
        self.assertEqual(self.analyst.settings_view()['error'], 'analysis_config.json: not UTF-8 text')
        self.settings(sa.DEFAULT_CONFIG)  # repaired by hand: used at the next measurement, no restart
        self.assertEqual(self.analyst.settings_view()['error'], '')
        self.assertEqual(self.analyst.measure(job, 'sample', dict(INFO))['conc_reason'], 'Correction has not been applied yet')
        self.assertEqual(self.events, [])

    def test_blank_zero_is_subtracted_from_samples(self):
        self.calibrate()
        every = {'enabled': {'Cu': True, 'Zn': True, 'Ni': False}, 'window': 5, 'min_blanks': 3, 'mad_reject': True}
        reply = self.analyst.set_blank_zero(every)
        self.assertEqual(reply['settings'], every)
        self.assertEqual(reply['status']['Cu'], {'enabled': True, 'ready': False, 'zero': None, 'n': 0, 'rejected': 0})
        early = self.measure('sample', {'Cu': 1.0}, 0.0)
        self.assertEqual(early['blank_zero']['Cu'], {'applied': False, 'zero': None, 'n': 0, 'enabled': True})
        self.assertEqual(early['metals']['Cu']['conc'], early['metals']['Cu']['conc_raw'])
        blanks = [self.measure('blank', {'Cu': 0.04}, 0.0) for _ in range(3)]
        zero = statistics.median(blank['metals']['Cu']['conc'] for blank in blanks)
        self.assertAlmostEqual(zero, 0.04, delta=0.02)
        status = self.analyst.blank_zero()['status']
        self.assertEqual(status['Cu'], {'enabled': True, 'ready': True, 'zero': zero, 'n': 3, 'rejected': 0})
        self.assertEqual(self.analyst.measurements()['blank_zero']['status'], status)
        sample = self.measure('sample', {'Cu': 1.0, 'Ni': 2.0}, 0.0)
        cu, ni = sample['metals']['Cu'], sample['metals']['Ni']
        self.assertEqual((cu['zero'], cu['conc']), (zero, cu['conc_raw'] - zero))
        self.assertAlmostEqual(cu['conc_raw'], 1.04 - 0.04 + 0.0, delta=0.06)
        self.assertEqual(sample['blank_zero']['Cu'], {'applied': True, 'zero': zero, 'n': 3, 'enabled': True})
        self.assertEqual((ni['conc'], sample['blank_zero']['Ni']['applied']), (ni['conc_raw'], False))  # not enabled
        self.assertNotIn('blank_zero', blanks[0])
        # Blanks of another calibration do not count.
        self.calibrate()
        self.assertEqual(self.analyst.blank_zero()['status']['Cu']['n'], 0)
        self.assertEqual(self.measure('sample', {'Cu': 1.0}, 0.0)['blank_zero']['Cu']['applied'], False)

    def test_several_threads(self):
        self.calibrate()
        jobs = [self.discharge({'Cu': 1.0}, 0.0, (1.0, 1.0)) for _ in range(4)]
        results, errors = [], []

        def work(job):
            try:
                for number in range(5):
                    results.append(self.analyst.measure(job, 'sample' if number % 2 else 'blank', dict(INFO)))
                    self.analyst.set_qc({'max_shift_nm': 0.05 + number / 1000})
                    self.analyst.measurements(1)
                    self.analyst.latest()
                    self.analyst.blank_zero()
                    self.analyst.settings_view()
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)
        threads = [threading.Thread(target=work, args=(job,)) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 20)
        self.assertEqual(len({record['id'] for record in results}), 20)
        self.assertTrue(all(record['valid'] and record['conc_reason'] == '' for record in results))
        lines = (self.data / 'measurements.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual({json.loads(line)['id'] for line in lines}, {record['id'] for record in results})


class RigShapeTests(Bench):
    """The shipped default settings on spectrum sets shaped like this rig's: 1862 pixels over
    180-500 nm, one background frame. The lines are synthetic and sit where the windows are, so
    this shows that the program's path works on that pixel grid - not that the windows suit the
    instrument (they were tuned for pixels less than half as wide)."""

    PITCH = (500.126 - 179.991) / 1861
    BANDS = [(278.3, 3000, .30), (279.4, 2000, .25), (331.2, 2500, .30), (332.3, 1800, .25)]  # in the shift windows
    LINES = {'Cu': [(324.80, 400)], 'Zn': [(213.42, 300)], 'Ni': [(231.75, 150), (230.90, 80), (341.32, 120)]}

    def rig(self, conc, shift):
        def line(x, center, height, sigma):
            scale = sigma * math.sqrt(2)
            low, high = (x - self.PITCH / 2 - center) / scale, (x + self.PITCH / 2 - center) / scale
            return height * sigma * math.sqrt(math.pi / 2) * (math.erf(high) - math.erf(low)) / self.PITCH
        values = []
        for x in RIG_AXIS:
            v = 800.0 + sum(line(x, center + shift, height, sigma) for center, height, sigma in self.BANDS)
            for metal, lines in self.LINES.items():
                v += sum(line(x, center + shift, height * conc.get(metal, 0), .22) for center, height in lines)
            values.append(v)
        job = job_id(len(self.runs.sets) + 1)
        self.runs.sets[job] = json.dumps(stored_run(job, values, signal_frames=8, background_frames=1, axis=RIG_AXIS))
        return job

    def test_default_settings_end_to_end(self):
        analyst = Analyst(Path(self.temp.name) / 'rig', self.runs, self.disk)
        self.assertEqual((analyst.settings_view()['method_id'], analyst.settings_view()['error']), (DEFAULT_METHOD, ''))
        info = analyst.check_plan(plan(recipe_id=RECIPE_ID), STANDARD, None)
        record = analyst.create(info['plan'], STANDARD, None, 'Wang Xiaoming', 'c' * 32)
        number = 0
        for step in info['steps']:
            if step['action'] == 'plasma_run':
                number += 1
                tag = step['calibration']
                conc = info['plan']['levels'][tag['level']]['conc']
                job = self.rig({m: v or 0 for m, v in conc.items()}, random.Random(number).uniform(-0.03, 0.03))
                analyst.add_run(record['id'], dict(tag, job=job))
        done = analyst.finish(record['id'], 'completed')
        self.assertEqual(done['status'], 'analyzed', done.get('error'))
        self.assertEqual((done['reference']['blank_count'], len(done['reference']['axis'])), (2, 1862))
        self.assertTrue(all(row['valid'] and row['shift']['applied'] for row in done['analysis']['jobs']))
        fits = done['analysis']['fits']
        self.assertTrue(all(fit['valid'] and fit['n'] == 6 for fit in fits.values()))
        self.assertGreater(fits['Ni']['r2'], 0.9999)  # three lines, area by trapezoid
        # Cu (highest pixel of 3) and Zn (mean of 3 or 4 pixels) change with the sub-pixel position
        # of the line: about 0.996 and 0.988 here, with shifts of only +-0.03 nm between the rounds.
        self.assertGreater(fits['Cu']['r2'], 0.98)
        self.assertGreater(fits['Zn']['r2'], 0.95)
        applied = analyst.apply(record['id'])
        self.assertEqual(applied['applied'], ['Cu', 'Zn', 'Ni'])
        self.assertEqual(applied['active']['blank_norm'], {})  # these settings have no normalize windows
        sample = analyst.measure(self.rig({'Cu': 1.5, 'Zn': 0.7, 'Ni': 3}, -0.02), 'sample', dict(INFO))
        self.assertEqual((sample['valid'], sample['conc_reason']), (True, ''))
        self.assertAlmostEqual(sample['metals']['Ni']['conc'], 3.0, delta=0.06)
        self.assertAlmostEqual(sample['metals']['Cu']['conc'], 1.5, delta=0.225)
        self.assertAlmostEqual(sample['metals']['Zn']['conc'], 0.7, delta=0.105)
        self.assertNotIn('norm_area', sample['metals']['Cu'])
        blank = analyst.measure(self.rig({}, 0.004), 'blank', dict(INFO))
        self.assertEqual(blank['qc'], {'status': 'ok', 'problems': [], 'reason': ''})
        drifted = analyst.measure(self.rig({'Cu': 0.3}, 0.08), 'blank', dict(INFO))
        self.assertEqual(drifted['qc']['status'], 'fail')
        self.assertTrue(any(p.startswith('spectral shift') for p in drifted['qc']['problems']))
        self.assertTrue(any(p.startswith('Cu apparent concentration') for p in drifted['qc']['problems']))
        # Beyond the settings' own limit (0.1 nm) the analysis is not trusted at all.
        far = analyst.measure(self.rig({'Cu': 1.0}, 0.2), 'sample', dict(INFO))
        self.assertEqual((far['valid'], far['conc_reason'], far['metals']['Cu']['conc']), (False, 'This analysis is invalid', None))
        self.assertIn('exceeds 0.1 nm', far['error'])


class SettingsTests(Bench):
    def setUp(self):
        super().setUp()
        self.default = json.loads(Analyst.DEFAULT_SETTINGS.read_text(encoding='utf-8'))

    def test_a_new_data_folder_gets_the_default_settings(self):
        fresh = Path(self.temp.name) / 'new' / 'data'
        analyst = Analyst(fresh, self.runs, self.disk)
        path = fresh / 'analysis_config.json'
        self.assertEqual(self.disk.written, [path])  # created at the start, through write_json
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), self.default)  # with its comment
        self.assertEqual(Analyst.DEFAULT_SETTINGS, ROOT / 'pc' / 'analysis_default.json')
        view = analyst.settings_view()
        self.assertEqual(view, {'method_id': DEFAULT_METHOD, 'config': sa.validate_config(self.default), 'error': '',
                                'path': str(path)})
        view['config']['metals'].clear()
        self.assertEqual(analyst.settings_view()['config'], sa.validate_config(self.default))
        self.assertTrue((fresh / 'calibrations').is_dir())
        self.assertEqual(self.disk.written, [path])  # read once, not written again
        path.unlink()  # removed by hand: it comes back
        self.assertEqual(analyst.settings_view()['method_id'], DEFAULT_METHOD)
        self.assertEqual(self.disk.written, [path, path])
        self.assertEqual((Analyst.UNIT, Analyst.METALS), ('mg/L', ('Cu', 'Zn', 'Ni')))

    def test_settings_are_read_again_when_the_file_changes(self):
        self.assertEqual(self.analyst.settings_view()['method_id'], BUILT_IN_METHOD)
        revs = dict(self.analyst.revs)
        edited = json.loads(json.dumps(sa.DEFAULT_CONFIG))
        edited['metals']['Cu']['peak'] = [327.1, 327.6]
        expected = sa.method_id(sa.validate_config(edited))
        with mock.patch.object(sa, 'validate_config', wraps=sa.validate_config) as validate:
            for _ in range(5):
                self.analyst.settings_view()
            self.assertEqual(validate.call_count, 0)  # the file did not change
            self.settings(dict(edited, comment='Manual adjustment Cu Windows'))
            view = self.analyst.settings_view()
            self.assertEqual(validate.call_count, 1)
            self.assertEqual(view['method_id'], expected)
            self.assertEqual(view['config']['metals']['Cu']['peak'], [327.1, 327.6])
            self.assertNotIn('comment', view['config'])
            self.analyst.settings_view()
            self.assertEqual(validate.call_count, 1)
        self.assertEqual(self.analyst.revs, {key: value + 1 for key, value in revs.items()})  # the pages reload
        # A file saved with a byte-order mark (Notepad) is read as well.
        path = self.data / 'analysis_config.json'
        path.write_bytes(b'\xef\xbb\xbf' + json.dumps(sa.DEFAULT_CONFIG).encode('utf-8'))
        os.utime(path, ns=(path.stat().st_mtime_ns + 5_000_000,) * 2)
        self.assertEqual(self.analyst.settings_view()['method_id'], BUILT_IN_METHOD)
        self.assertEqual([p for p in self.disk.written if p.name == 'analysis_config.json'], [])  # never overwritten

    def test_a_default_file_that_is_missing(self):
        fresh = Path(self.temp.name) / 'new'
        with mock.patch.object(Analyst, 'DEFAULT_SETTINGS', Path(self.temp.name) / 'nowhere' / 'analysis_default.json'):
            analyst = Analyst(fresh, self.runs, self.disk)
            view = analyst.settings_view()
            self.assertEqual((view['method_id'], view['config']), (None, None))
            self.assertTrue(view['error'].startswith('analysis_default.json: cannot be read or written ('), view['error'])
            self.assertFalse((fresh / 'analysis_config.json').exists())
            job = self.discharge({}, 0.0, (1.0, 1.0))
            self.assertEqual(analyst.measure(job, 'sample')['conc_reason'], 'Analysis failed')
        self.assertEqual(analyst.settings_view()['method_id'], DEFAULT_METHOD)  # tried again once the file is there
        broken = Path(self.temp.name) / 'analysis_default.json'
        broken.write_text('{"metals": {}}', encoding='utf-8')
        with mock.patch.object(Analyst, 'DEFAULT_SETTINGS', broken):
            error = Analyst(Path(self.temp.name) / 'other', self.runs, self.disk).settings_view()['error']
        self.assertEqual(error, 'analysis_default.json: metals needs 1-10 entries')
        self.assertFalse((Path(self.temp.name) / 'other' / 'analysis_config.json').exists())
        self.disk.broken = lambda path: True  # a data folder that cannot be written
        stuck = Analyst(Path(self.temp.name) / 'third', self.runs, self.disk)
        self.assertTrue(stuck.settings_view()['error'].startswith('analysis_config.json: cannot be read or written ('))

    def test_coefficients(self):
        start = {m: {'a': '1', 'b': '0', 'c': '1'} for m in ('Cu', 'Zn', 'Ni')}
        self.assertEqual(self.analyst.coefficients(), start)  # as the original, while nothing is stored
        self.assertFalse((self.data / 'calibration_config.json').exists())
        revs = dict(self.analyst.revs)
        empty = {'a': None, 'b': None, 'c': None}
        new = {'Cu': {'a': ' 2.5 ', 'b': -0.01, 'c': 1}, 'Zn': dict(empty), 'Ni': {'a': '3', 'b': '0', 'c': '1.02'}}
        clean = {'Cu': {'a': '2.5', 'b': '-0.01', 'c': '1'}, 'Zn': dict(empty), 'Ni': {'a': '3', 'b': '0', 'c': '1.02'}}
        self.assertEqual(self.analyst.set_coefficients(new), clean)
        self.assertEqual(self.analyst.coefficients(), clean)
        self.assertEqual(self.stored('calibration_config.json'), clean)
        self.assertEqual(self.disk.written[-1], self.data / 'calibration_config.json')
        self.assertEqual(self.analyst.revs, {key: value + 1 for key, value in revs.items()})
        self.assertEqual(self.start(None).coefficients(), clean)
        self.assertIsNone(self.analyst.active())  # typing coefficients is not applying a calibration
        for bad, text in ((None, 'calibration parameters need to include Cu, Zn, Ni'), (dict(clean, Cu={'a': '1'}), 'Cu calibration parameters need to include a, b, c'),
                          (dict(clean, Ni={'a': 'x', 'b': '0', 'c': '1'}), 'Ni of a must be a finite value')):
            with self.assertRaises(ValueError) as caught:
                self.analyst.set_coefficients(bad)
            self.assertEqual(str(caught.exception), text)
        self.assertEqual(self.analyst.coefficients(), clean)
        self.analyst.coefficients()['Cu']['a'] = '9'
        self.assertEqual(self.analyst.coefficients(), clean)
        # A stored file that cannot be used: no concentrations rather than wrong ones, and a line in the log.
        (self.data / 'calibration_config.json').write_text('{"Cu": {}}', encoding='utf-8')
        self.events.clear()
        damaged = self.start(None)
        self.assertEqual(damaged.coefficients(), {m: {'a': None, 'b': None, 'c': None} for m in ('Cu', 'Zn', 'Ni')})
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0][0], 'error')
        self.assertTrue(self.events[0][1].startswith('correction coefficient file calibration_config.json cannot be read, and the concentration is not converted temporarily:'))

    def test_blank_thresholds(self):
        self.assertEqual(self.analyst.qc(), meas.validate_qc({}))
        revs = dict(self.analyst.revs)
        new = self.analyst.set_qc({'max_shift_nm': 0.2, 'max_apparent_conc': {'Cu': 0.1}})
        self.assertEqual(new, {'max_shift_nm': 0.2, 'max_apparent_conc': {'Cu': 0.1, 'Zn': 0.05, 'Ni': 0.05},
                               'max_norm_change_percent': 20.0})
        self.assertEqual((self.analyst.qc(), self.stored('blank_qc.json'), self.start(None).qc()), (new, new, new))
        self.assertEqual(self.analyst.revs['measurements'], revs['measurements'] + 1)
        with self.assertRaisesRegex(ValueError, r'displacement threshold \(nm\) Must be between 0.001–1'):
            self.analyst.set_qc({'max_shift_nm': 5})
        self.assertEqual(self.analyst.qc(), new)
        self.assertEqual(self.analyst.measurements()['qc'], new)
        # The threshold in force decides the check of the next blank.
        self.calibrate()
        self.assertEqual(self.measure('blank', {}, 0.12)['qc']['status'], 'ok')  # 0.12 nm is within 0.2
        self.analyst.set_qc({})
        self.assertEqual(self.measure('blank', {}, 0.12)['qc']['status'], 'fail')

    def test_blank_zero_settings(self):
        self.assertEqual(self.analyst.blank_zero(), {'settings': meas.validate_blank_zero({}), 'status': None})
        new = {'enabled': {'Cu': True, 'Zn': False, 'Ni': True}, 'window': 8, 'min_blanks': 3, 'mad_reject': True}
        self.assertEqual(self.analyst.set_blank_zero(new), {'settings': new, 'status': None})
        self.assertEqual((self.stored('blank_zero.json'), self.start(None).blank_zero()['settings']), (new, new))
        self.assertEqual(self.analyst.measurements()['blank_zero']['settings']['window'], 8)
        with self.assertRaisesRegex(ValueError, 'The number of times to take blanks must be 3–30 integer'):
            self.analyst.set_blank_zero({'window': 2})
        self.assertEqual(self.analyst.blank_zero()['settings'], new)


class RestartTests(Bench):
    def test_records_found_running_or_analyzing_become_interrupted(self):
        running = self.run_calibration(upto=4)
        finished = self.calibrate(apply=False)  # (closes the first one's live copy; its file still says running)
        analyzing = self.analyst.record(finished)
        analyzing['status'] = 'analyzing'
        path = self.data / 'calibrations' / f'{finished}.json'
        path.write_text(json.dumps(analyzing), encoding='utf-8')
        first = self.data / 'calibrations' / f'{running}.json'
        record = json.loads(first.read_text(encoding='utf-8'))
        record.update(status='running', error=None)
        first.write_text(json.dumps(record), encoding='utf-8')
        untouched = self.analyst.finish(self.run_calibration(upto=2), 'stopped')
        self.disk.written.clear()
        restarted = self.start(None)
        self.assertEqual(sorted(self.disk.written), sorted([first, path]))  # rewritten through write_json
        for record_id, runs in ((running, 4), (finished, 9)):
            record = restarted.record(record_id)
            self.assertEqual((record['status'], record['error'], len(record['runs'])),
                             ('interrupted', 'program closed during calibration execution or analysis', runs))
            self.assertEqual(self.stored(f'calibrations/{record_id}.json'), record)
        self.assertEqual(restarted.record(untouched['id']), untouched)
        self.assertEqual({row['id']: row['status'] for row in restarted.records()},
                         {running: 'interrupted', finished: 'interrupted', untouched['id']: 'stopped'})
        self.assertIsNone(restarted.running())
        self.assertIs(restarted.add_run(running, {'level': 0, 'job': job_id(1)}), False)  # never resumed
        self.assertEqual(restarted.analyze(finished)['status'], 'analyzed')  # the rounds can still be used

    def test_the_program_starts_whatever_is_in_the_folder(self):
        self.calibrate()
        self.measure('blank', {}, 0.0)
        for name in ('analysis_config.json', 'calibration_config.json', 'calibration_active.json',
                     'calibration_plan.json', 'blank_qc.json', 'blank_zero.json'):
            (self.data / name).write_bytes(b'\x00\xff not json')
        with (self.data / 'measurements.jsonl').open('ab') as out:
            out.write(b'\xff\xfe\n{"kind": "sam')
        for record in (self.data / 'calibrations').glob('CAL-*.json'):
            record.write_text('{"id": 5}', encoding='utf-8')
        self.events.clear()
        analyst = self.start(None)
        self.assertTrue(analyst.settings_view()['error'])
        self.assertEqual(analyst.coefficients()['Cu'], {'a': None, 'b': None, 'c': None})
        self.assertEqual((analyst.active(), analyst.running(), analyst.records()), (None, None, []))
        self.assertEqual((analyst.plan(), analyst.qc()), (cal.DEFAULT_PLAN, meas.validate_qc({})))
        self.assertEqual(analyst.blank_zero(), {'settings': meas.validate_blank_zero({}), 'status': None})
        self.assertEqual(len(analyst.measurements()['rows']), 1)
        record = analyst.measure(job_id(1), 'sample', dict(INFO))
        self.assertEqual(record['conc_reason'], 'Analysis failed')
        self.assertEqual(len(analyst.measurements()['rows']), 2)
        self.assertEqual(len(Analyst(self.data, self.runs, self.disk).measurements()['rows']), 2)
        # A snapshot file of another shape is not taken for an applied calibration.
        for odd in ('[]', '{"calibration_id": 5, "applied_at": "x"}', '{"applied_at": "x"}', '{"calibration_id": "CAL-1"}'):
            (self.data / 'calibration_active.json').write_text(odd, encoding='utf-8')
            self.assertIsNone(self.start(None).active())

    def test_every_file_goes_through_write_json(self):
        self.analyst.save_plan(plan(recipe_id=RECIPE_ID))
        self.analyst.set_qc({'max_shift_nm': 0.06})
        self.analyst.set_blank_zero({'window': 6})
        record_id = self.calibrate()
        self.analyst.set_exclusions(record_id, {'Cu': [2]})
        self.analyst.analyze(record_id)
        self.measure('blank', {}, 0.0)
        self.measure('sample', {'Cu': 1}, 0.0)
        files = sorted(path.relative_to(self.data).as_posix() for path in self.data.rglob('*') if path.is_file())
        self.assertEqual(files, ['analysis_config.json', 'blank_qc.json', 'blank_zero.json', 'calibration_active.json',
                                 'calibration_config.json', 'calibration_plan.json', f'calibrations/{record_id}.json',
                                 'measurements.jsonl'])  # no .tmp left, nothing else created
        written = {path.relative_to(self.data).as_posix() for path in self.disk.written}
        # analysis_config.json was put there by this test (the owner's hand); see SettingsTests for its creation.
        self.assertEqual(written, set(files) - {'analysis_config.json', 'measurements.jsonl'})
        self.assertTrue(all(isinstance(path, Path) for path in self.disk.written))
        for name in files:
            if name.endswith('.json'):
                json.loads((self.data / name).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
