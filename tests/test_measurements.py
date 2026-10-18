"""pc/measurements.py: measurement records, blank check, blank zero, the JSON-lines log.

The cases of the original HMI's tests/test_calibration.py (BlankCheckTests) with the original's
numbers, plus the record shapes, the settings rules and the log. Temp folders only.

    python -B tests/test_measurements.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pc'))

import measurements as meas  # noqa: E402

METALS = ('Cu', 'Zn', 'Ni')


class BlankCheckTests(unittest.TestCase):
    """Ported from the original."""

    CONTEXT = {'calibration_id': 'CAL-' + 'a' * 32, 'method_id': 'area-v1-x', 'metals': ['Cu', 'Zn', 'Ni'],
               'blank_norm': {'Cu': 100.0, 'Zn': 100.0, 'Ni': 100.0}}
    COEF = {m: {'a': '2', 'b': '-0.01', 'c': '1'} for m in ('Cu', 'Zn', 'Ni')}

    def row(self, intensity=0.005, shift=0.01, norm=100.0):
        return {'job': 'HMI-' + '1' * 32, 'valid': True, 'error': '',
                'shift': {'applied': True, 'p0': shift, 'p1': 0.0},
                'metals': {m: {'valid': True, 'intensity': intensity, 'norm_area': norm,
                               'windows_used': {'peak': [327.1, 327.5]}} for m in ('Cu', 'Zn', 'Ni')}}

    def test_clean_blank_passes(self):
        record = meas.evaluate('blank', self.row(), 'area-v1-x', self.CONTEXT, self.COEF, meas.validate_qc({}))
        self.assertEqual(record['qc']['status'], 'ok')
        self.assertAlmostEqual(record['metals']['Cu']['conc'], 0.0)
        self.assertEqual(record['qc'], {'status': 'ok', 'problems': [], 'reason': ''})
        self.assertAlmostEqual(record['metals']['Cu']['norm_change_percent'], 0.0)

    def test_each_limit_raises_a_problem(self):
        qc = meas.validate_qc({})
        record = meas.evaluate('blank', self.row(intensity=0.1, shift=0.08, norm=130.0), 'area-v1-x',
                               self.CONTEXT, self.COEF, qc)
        problems = record['qc']['problems']
        self.assertEqual(record['qc']['status'], 'fail')
        self.assertTrue(any('Displacement' in p for p in problems))
        self.assertEqual(sum(1 for p in problems if 'apparent concentration' in p), 3)
        self.assertEqual(sum(1 for p in problems if 'normalized area' in p), 3)
        self.assertEqual(problems[:3], ['spectral shift 0.080 nm (threshold 0.05) ', 'Cu apparent concentration 0.19 mg/L (threshold ±0.05) ',
                                        'Cu Normalized area change +30.0% (threshold ±20%) '])

    def test_without_matching_calibration_nothing_is_compared(self):
        none = meas.evaluate('blank', self.row(), 'area-v1-x', None, self.COEF, meas.validate_qc({}))
        self.assertEqual(none['qc']['status'], 'unchecked')
        self.assertEqual(none['qc'], {'status': 'unchecked', 'problems': [], 'reason': 'The correction has not been applied and the baseline cannot be determined.'})
        changed = meas.evaluate('sample', self.row(), 'area-v1-other', self.CONTEXT, self.COEF, meas.validate_qc({}))
        self.assertIsNone(changed['metals']['Cu']['conc'])
        self.assertIn('Recalibration', changed['conc_reason'])
        self.assertEqual(changed['conc_reason'], 'The analysis settings are different from the applied calibration, please recalibrate.')

    def test_blank_zero_uses_median_of_recent_blanks(self):
        settings = meas.validate_blank_zero({'enabled': {'Cu': False, 'Zn': True, 'Ni': True}, 'window': 5,
                                             'min_blanks': 3})

        def blank(ni, zn=0.05):
            return {'kind': 'blank', 'valid': True,
                    'metals': {'Cu': {'conc': -0.03}, 'Zn': {'conc': zn}, 'Ni': {'conc': ni}}}
        self.assertFalse(meas.zero_status([blank(.06), blank(.07)], settings)['Ni']['ready'])  # fewer than 3
        # the 0.26 outlier is rejected (3 x MAD); only the last 5 blanks count (0.9 is too old)
        blanks = [blank(.9), blank(.06), blank(.26), blank(.05), blank(.07), blank(.065)]
        status = meas.zero_status(blanks, settings)
        self.assertEqual((status['Ni']['n'], status['Ni']['rejected']), (4, 1))
        self.assertAlmostEqual(status['Ni']['zero'], 0.0625)
        sample = {'metals': {'Cu': {'conc': .35}, 'Zn': {'conc': .38}, 'Ni': {'conc': .43}}}
        meas.apply_zero(sample, status)
        self.assertAlmostEqual(sample['metals']['Ni']['conc'], .43 - .0625)
        self.assertEqual(sample['metals']['Ni']['conc_raw'], .43)
        self.assertAlmostEqual(sample['metals']['Zn']['conc'], .33)
        self.assertEqual(sample['metals']['Cu']['conc'], .35)  # Cu not enabled
        self.assertFalse(sample['blank_zero']['Cu']['applied'])
        for bad in ({'window': 2}, {'min_blanks': 11}, {'enabled': {'Fe': True}}, {'mad_reject': 'yes'}):
            with self.assertRaises(ValueError):
                meas.validate_blank_zero(bad)
        self.assertEqual(meas.validate_blank_zero({})['enabled'], {'Cu': False, 'Zn': False, 'Ni': True})

    def test_qc_settings_are_validated(self):
        with self.assertRaises(ValueError):
            meas.validate_qc({'max_shift_nm': 0})
        self.assertEqual(meas.validate_qc({'max_apparent_conc': {'Cu': 0.2}})['max_apparent_conc']['Zn'], 0.05)


class RecordTests(unittest.TestCase):
    CONTEXT = BlankCheckTests.CONTEXT
    COEF = BlankCheckTests.COEF
    QC = meas.validate_qc({})

    def row(self, intensity=0.005, shift=0.01, norm=100.0, **changes):
        """shift: p0 in nm, or the whole shift object."""
        row = BlankCheckTests().row(intensity, 0.01 if isinstance(shift, dict) else shift, norm)
        if isinstance(shift, dict):
            row['shift'] = shift
        row.update(changes)
        return row

    def test_sample_record(self):
        record = meas.evaluate('sample', self.row(), 'area-v1-x', self.CONTEXT, self.COEF, self.QC)
        self.assertEqual(set(record), {'kind', 'job', 'method_id', 'valid', 'error', 'shift_nm', 'metals',
                                       'calibration_id', 'conc_reason'})  # no frames_per_sum, no qc on a sample
        self.assertEqual((record['kind'], record['job'], record['method_id'], record['valid'], record['error'],
                          record['calibration_id'], record['conc_reason']),
                         ('sample', 'HMI-' + '1' * 32, 'area-v1-x', True, '', 'CAL-' + 'a' * 32, ''))
        self.assertAlmostEqual(record['shift_nm'], 0.01)
        self.assertEqual(set(record['metals']), set(METALS))
        self.assertEqual(set(record['metals']['Cu']), {'intensity', 'conc', 'norm_area'})
        self.assertAlmostEqual(record['metals']['Cu']['conc'], 2 * 0.005 - 0.01)
        json.dumps(record, allow_nan=False)

    def test_concentration_needs_everything(self):
        def conc(**how):
            context = how.pop('context', self.CONTEXT)
            method = how.pop('method', 'area-v1-x')
            record = meas.evaluate('sample', self.row(**how), method, context, self.COEF, self.QC)
            return record['metals']['Cu']['conc'], record['conc_reason']
        self.assertEqual(conc(context=None), (None, 'Correction has not been applied yet'))
        self.assertEqual(conc(method='area-v1-y'), (None, 'The analysis settings are different from the applied calibration, please recalibrate.'))
        self.assertEqual(conc(valid=False, error='shift 0.4000 nm exceeds 0.3 nm'), (None, 'This analysis is invalid'))
        self.assertEqual(conc(context=dict(self.CONTEXT, metals=['Zn'])), (None, ''))  # Cu was not applied
        value, reason = conc()
        self.assertEqual(reason, '')
        self.assertAlmostEqual(value, 0.0)
        # A metal whose analysis failed has no intensity and no concentration; the others keep theirs.
        row = self.row()
        row['metals']['Ni'] = {'valid': False, 'error': 'window holds no pixel'}
        record = meas.evaluate('sample', row, 'area-v1-x', self.CONTEXT, self.COEF, self.QC)
        self.assertEqual(record['metals']['Ni'], {'intensity': None, 'conc': None})
        self.assertIsNotNone(record['metals']['Zn']['conc'])
        self.assertTrue(record['valid'])
        # An unreadable spectrum: an invalid row without metals.
        record = meas.evaluate('blank', {'job': 'HMI-x', 'valid': False, 'error': 'no signal frames in HMI-x'},
                               'area-v1-x', self.CONTEXT, self.COEF, self.QC)
        self.assertEqual((record['valid'], record['error'], record['shift_nm'], record['conc_reason']),
                         (False, 'no signal frames in HMI-x', None, 'This analysis is invalid'))
        self.assertEqual(record['qc'], {'status': 'unchecked', 'problems': [], 'reason': 'This analysis is invalid and the baseline cannot be determined.'})
        self.assertEqual(record['metals'], {m: {'intensity': None, 'conc': None} for m in METALS})

    def test_shift_at_the_lines(self):
        row = self.row(shift={'applied': True, 'p0': -0.5, 'p1': 0.002})
        row['metals']['Cu']['windows_used'] = {'peak': [300.0, 302.0]}   # d = +0.102
        row['metals']['Zn']['windows_used'] = {'peak': [210.0, 212.0]}   # d = -0.078
        row['metals']['Ni']['windows_used'] = {'peak': [230.0, 232.0]}   # d = -0.038
        record = meas.evaluate('sample', row, 'area-v1-x', self.CONTEXT, self.COEF, self.QC)
        self.assertAlmostEqual(record['shift_nm'], 0.102)  # the largest |d| at a peak window
        none = meas.evaluate('sample', self.row(shift={'applied': False, 'p0': 0.0, 'p1': 0.0}), 'area-v1-x',
                             self.CONTEXT, self.COEF, self.QC)
        self.assertIsNone(none['shift_nm'])
        bare = self.row(shift={'applied': True, 'p0': -0.03, 'p1': 0.0})
        for item in bare['metals'].values():
            item.pop('windows_used')
        record = meas.evaluate('sample', bare, 'area-v1-x', self.CONTEXT, self.COEF, self.QC)
        self.assertAlmostEqual(record['shift_nm'], 0.03)

    def test_blank_check_details(self):
        # No normalization window (the shipped settings): that check is simply not made.
        row = self.row()
        for item in row['metals'].values():
            item.pop('norm_area')
        record = meas.evaluate('blank', row, 'area-v1-x', self.CONTEXT, self.COEF, self.QC)
        self.assertEqual(record['qc']['status'], 'ok')
        self.assertNotIn('norm_change_percent', record['metals']['Cu'])
        self.assertNotIn('norm_area', record['metals']['Cu'])
        no_reference = meas.evaluate('blank', self.row(norm=500.0), 'area-v1-x', dict(self.CONTEXT, blank_norm={}),
                                     self.COEF, self.QC)
        self.assertEqual(no_reference['qc']['status'], 'ok')
        # Exactly on a limit is still fine; a negative apparent concentration counts like a positive one.
        edge = meas.evaluate('blank', self.row(intensity=0.03, shift=0.05, norm=120.0), 'area-v1-x', self.CONTEXT,
                             self.COEF, self.QC)
        self.assertEqual(edge['qc']['problems'], [])
        negative = meas.evaluate('blank', self.row(intensity=-0.03), 'area-v1-x', self.CONTEXT, self.COEF, self.QC)
        self.assertEqual(negative['qc']['problems'], [f'{m} apparent concentration -0.07 mg/L (threshold ±0.05) ' for m in METALS])
        lower = meas.evaluate('blank', self.row(norm=70.0), 'area-v1-x', self.CONTEXT, self.COEF, self.QC)
        self.assertEqual(lower['qc']['problems'][0], 'Cu Normalized area change -30.0% (threshold ±20%) ')

    def test_failed_record(self):
        record = meas.failed('blank', 'HMI-' + '2' * 32, 'x' * 500, self.CONTEXT)
        self.assertEqual(record, {'kind': 'blank', 'job': 'HMI-' + '2' * 32, 'method_id': None, 'valid': False,
                                  'error': 'x' * 300, 'shift_nm': None,
                                  'metals': {m: {'intensity': None, 'conc': None} for m in METALS},
                                  'calibration_id': 'CAL-' + 'a' * 32, 'conc_reason': 'Analysis failed',
                                  'qc': {'status': 'unchecked', 'problems': [], 'reason': 'Analysis failed, unable to determine baseline'}})
        sample = meas.failed('sample', 'HMI-' + '2' * 32, 'boom', None)
        self.assertEqual((sample['calibration_id'], sample['conc_reason']), (None, 'Analysis failed'))
        self.assertNotIn('qc', sample)

    def test_summary(self):
        record = meas.evaluate('blank', self.row(), 'area-v1-x', self.CONTEXT, self.COEF, self.QC)
        record['id'] = 'abc'
        row = meas.summary(record)
        self.assertEqual(set(row), {'id', 'kind', 'valid', 'conc', 'conc_reason', 'qc'})
        self.assertEqual((row['id'], row['kind'], row['valid'], row['conc_reason'], row['qc']),
                         ('abc', 'blank', True, '', 'ok'))
        self.assertEqual(set(row['conc']), set(METALS))
        sample = dict(meas.failed('sample', 'j', 'boom', None), id='def')
        self.assertEqual(meas.summary(sample), {'id': 'def', 'kind': 'sample', 'valid': False,
                                                'conc': {m: None for m in METALS}, 'conc_reason': 'Analysis failed', 'qc': None})

    def test_concentration(self):
        self.assertEqual(meas.concentration({'a': '2', 'b': '0.5', 'c': '10'}, 0.25), 10.0)
        self.assertEqual(meas.concentration({'a': 2, 'b': 0.5, 'c': 1}, 1), 2.5)
        self.assertEqual(meas.concentration({'a': '0.1', 'b': '0.2', 'c': '1'}, 1), 0.3)  # decimal arithmetic
        for bad in ({'a': None, 'b': '0', 'c': '1'}, {'a': '1', 'b': '0'}, {'a': 'x', 'b': '0', 'c': '1'}, None):
            self.assertIsNone(meas.concentration(bad, 1.0))
        self.assertIsNone(meas.concentration({'a': '1', 'b': '0', 'c': '1'}, None))
        self.assertEqual((meas.METALS, meas.KINDS), (('Cu', 'Zn', 'Ni'), ('sample', 'blank')))


class SettingsTests(unittest.TestCase):
    def test_blank_thresholds(self):
        self.assertEqual(meas.validate_qc({}), {'max_shift_nm': 0.05,
                                                'max_apparent_conc': {'Cu': 0.05, 'Zn': 0.05, 'Ni': 0.05},
                                                'max_norm_change_percent': 20.0})
        self.assertEqual(meas.validate_qc(meas.DEFAULT_QC), meas.validate_qc({}))
        clean = meas.validate_qc({'max_shift_nm': 1, 'max_apparent_conc': {'Ni': 1000}, 'max_norm_change_percent': 100})
        self.assertEqual(clean, {'max_shift_nm': 1.0, 'max_apparent_conc': {'Cu': 0.05, 'Zn': 0.05, 'Ni': 1000.0},
                                 'max_norm_change_percent': 100.0})
        cases = [(None, 'Blank threshold field:max_shift_nm, max_apparent_conc, max_norm_change_percent'),
                 ({'extra': 1}, 'Blank threshold field:max_shift_nm, max_apparent_conc, max_norm_change_percent'),
                 ({'max_apparent_conc': 0.1}, 'The blank apparent concentration threshold must be Cu/Zn/Ni'),
                 ({'max_apparent_conc': {'Fe': 0.1}}, 'The blank apparent concentration threshold must be Cu/Zn/Ni'),
                 ({'max_shift_nm': 0.0005}, 'displacement threshold (nm) Must be between 0.001–1'),
                 ({'max_shift_nm': 1.5}, 'displacement threshold (nm) Must be between 0.001–1'),
                 ({'max_shift_nm': '0.05'}, 'displacement threshold (nm) Must be between 0.001–1'),
                 ({'max_shift_nm': True}, 'displacement threshold (nm) Must be between 0.001–1'),
                 ({'max_shift_nm': float('nan')}, 'displacement threshold (nm) Must be between 0.001–1'),
                 ({'max_apparent_conc': {'Zn': -1}}, 'Zn apparent concentration threshold (mg/L) Must be between 0–1000'),
                 ({'max_apparent_conc': {'Zn': 1001}}, 'Zn apparent concentration threshold (mg/L) Must be between 0–1000'),
                 ({'max_norm_change_percent': 0.5}, 'Normalized area change threshold (%) Must be between 1–100'),
                 ({'max_norm_change_percent': 101}, 'Normalized area change threshold (%) Must be between 1–100')]
        for raw, text in cases:
            with self.assertRaises(ValueError) as caught:
                meas.validate_qc(raw)
            self.assertEqual(str(caught.exception), text)

    def test_blank_zero_settings(self):
        self.assertEqual(meas.validate_blank_zero({}), {'enabled': {'Cu': False, 'Zn': False, 'Ni': True}, 'window': 10,
                                                        'min_blanks': 3, 'mad_reject': True})
        self.assertEqual(meas.validate_blank_zero(None), meas.validate_blank_zero({}))  # anything but an object = defaults
        self.assertEqual(meas.validate_blank_zero(meas.DEFAULT_BLANK_ZERO), meas.validate_blank_zero({}))
        clean = meas.validate_blank_zero({'enabled': {'Cu': True}, 'window': 3, 'min_blanks': 3, 'mad_reject': False})
        self.assertEqual(clean, {'enabled': {'Cu': True, 'Zn': False, 'Ni': True}, 'window': 3, 'min_blanks': 3,
                                 'mad_reject': False})
        cases = [({'extra': 1}, 'Blank zero point setting field:enabled, window, min_blanks, mad_reject'),
                 ({'enabled': [True]}, 'enabled Need to be {Metal: true/false}'),
                 ({'enabled': {'Fe': True}}, 'enabled Need to be {Metal: true/false}'),
                 ({'enabled': {'Cu': 1}}, 'enabled Need to be {Metal: true/false}'),
                 ({'window': 2}, 'The number of times to take blanks must be 3–30 integer'), ({'window': 31}, 'The number of times to take blanks must be 3–30 integer'),
                 ({'window': 5.0}, 'The number of times to take blanks must be 3–30 integer'), ({'window': True}, 'The number of times to take blanks must be 3–30 integer'),
                 ({'min_blanks': 0}, 'The minimum number of blanks must be 1–30 integer'), ({'min_blanks': '3'}, 'The minimum number of blanks must be 1–30 integer'),
                 ({'min_blanks': 11}, 'The minimum number of blanks cannot be greater than the number of blanks taken'),
                 ({'mad_reject': 'yes'}, 'mad_reject Need to be true/false')]
        for raw, text in cases:
            with self.assertRaises(ValueError) as caught:
                meas.validate_blank_zero(raw)
            self.assertEqual(str(caught.exception), text)

    def test_zero_status_and_apply(self):
        settings = meas.validate_blank_zero({'enabled': {'Cu': True, 'Zn': True, 'Ni': True}, 'window': 4,
                                             'min_blanks': 2, 'mad_reject': False})

        def blank(value, valid=True, raw=None):
            item = {'conc': value}
            if raw is not None:
                item['conc_raw'] = raw
            return {'kind': 'blank', 'valid': valid, 'metals': {m: dict(item) for m in METALS}}
        status = meas.zero_status([blank(9.0), blank(0.1), blank(0.2, valid=False), blank(None), blank(0.3),
                                   blank(5.0, raw=0.5), blank(float('nan'))], settings)
        # invalid records, missing and non-finite values are skipped; conc_raw wins; the last 4 values count
        self.assertEqual(status['Cu'], {'enabled': True, 'ready': True, 'zero': 0.4, 'n': 4, 'rejected': 0})
        with_mad = meas.zero_status([blank(0.1), blank(0.1), blank(0.1), blank(7.0)], dict(settings, mad_reject=True))
        self.assertEqual((with_mad['Cu']['n'], with_mad['Cu']['rejected'], with_mad['Cu']['zero']), (4, 0, 0.1))  # MAD is 0
        empty = meas.zero_status([], settings)
        self.assertEqual(empty['Ni'], {'enabled': True, 'ready': False, 'zero': None, 'n': 0, 'rejected': 0})
        sample = {'metals': {'Cu': {'conc': 1.0}, 'Zn': {'conc': None}, 'Ni': {'conc': 2.0}}}
        meas.apply_zero(sample, dict(status, Ni=dict(status['Ni'], enabled=False)))
        self.assertEqual(sample['metals']['Cu'], {'conc': 0.6, 'conc_raw': 1.0, 'zero': 0.4})
        self.assertEqual(sample['metals']['Zn'], {'conc': None, 'conc_raw': None})  # nothing to correct
        self.assertEqual(sample['metals']['Ni'], {'conc': 2.0, 'conc_raw': 2.0})
        self.assertEqual(sample['blank_zero'], {'Cu': {'applied': True, 'zero': 0.4, 'n': 4, 'enabled': True},
                                                'Zn': {'applied': False, 'zero': 0.4, 'n': 4, 'enabled': True},
                                                'Ni': {'applied': False, 'zero': 0.4, 'n': 4, 'enabled': False}})
        waiting = {'metals': {m: {'conc': 1.0} for m in METALS}}
        meas.apply_zero(waiting, empty)  # not enough blanks yet: nothing is subtracted
        self.assertEqual(waiting['metals']['Cu'], {'conc': 1.0, 'conc_raw': 1.0})
        self.assertEqual(waiting['blank_zero']['Cu'], {'applied': False, 'zero': None, 'n': 0, 'enabled': True})


class LogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'measurements.jsonl'

    def tearDown(self):
        self.temp.cleanup()

    def test_add_latest_since(self):
        log = meas.Log(self.path)
        self.assertEqual((log.rows(), log.latest('sample'), log.since()), ([], None, []))
        self.assertRegex(meas.now(), r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?\+00:00$')  # UTC
        stamps = [f'2026-10-06T06:00:0{second}+00:00' for second in (1, 2, 3)]  # the PC's clock ticks coarsely
        with mock.patch.object(meas, 'now', side_effect=stamps):
            first = log.add({'kind': 'blank', 'job': 'HMI-1', 'recipe_name': 'Blank'})
            second = log.add({'kind': 'sample', 'job': 'HMI-2'})
            third = log.add({'kind': 'sample', 'job': 'HMI-3'})
        self.assertEqual(len({first['id'], second['id'], third['id']}), 3)
        self.assertRegex(first['id'], r'^[0-9a-f]{32}$')
        self.assertEqual([first['time'], second['time'], third['time']], stamps)
        self.assertEqual(list(first)[:2], ['id', 'time'])
        self.assertEqual(log.latest('sample')['job'], 'HMI-3')
        self.assertEqual(log.latest('blank')['job'], 'HMI-1')
        self.assertIsNone(log.latest('standard'))
        self.assertEqual([row['job'] for row in log.since()], ['HMI-1', 'HMI-2', 'HMI-3'])
        self.assertEqual([row['job'] for row in log.since(second['time'])], ['HMI-2', 'HMI-3'])
        self.assertEqual([row['job'] for row in log.since(None, 2)], ['HMI-2', 'HMI-3'])
        self.assertEqual(log.since('9999'), [])
        raw = self.path.read_bytes()
        self.assertEqual(raw.count(b'\n'), 3)
        self.assertNotIn(b'\r', raw)
        self.assertIn('Blank'.encode('utf-8'), raw)  # not escaped
        self.assertEqual([json.loads(line)['job'] for line in raw.decode('utf-8').splitlines()], ['HMI-1', 'HMI-2', 'HMI-3'])
        again = meas.Log(self.path)  # a new start reads the file
        self.assertEqual(again.rows(), [first, second, third])

    def test_nothing_is_written_that_json_cannot_hold(self):
        log = meas.Log(self.path)
        with self.assertRaises(ValueError):
            log.add({'kind': 'sample', 'metals': {'Cu': {'conc': float('nan')}}})
        with self.assertRaises(TypeError):
            log.add({'kind': 'sample', 'extra': object()})
        self.assertEqual(log.rows(), [])
        self.assertFalse(self.path.exists())

    def test_broken_lines_are_skipped(self):
        good = {'id': 'a', 'time': '2026-10-06T00:00:00+00:00', 'kind': 'sample'}
        self.path.write_bytes((json.dumps(good) + '\n').encode() + b'{"id": "b", "ki\n' + b'\xff\xfe garbage\n'
                              + b'[1, 2]\n' + b'\r\n' + (json.dumps(dict(good, id='c')) + '\r\n').encode()
                              + b'{"id": "d", "kind": "sam')  # the power went while this line was written
        log = meas.Log(self.path)
        self.assertEqual([row['id'] for row in log.rows()], ['a', 'c'])
        added = log.add({'kind': 'blank'})
        lines = self.path.read_bytes().split(b'\n')
        self.assertEqual(json.loads(lines[-2])['id'], added['id'])  # on a line of its own
        self.assertEqual([row['id'] for row in meas.Log(self.path).rows()], ['a', 'c', added['id']])
        log.add({'kind': 'blank'})
        self.assertEqual(len(meas.Log(self.path).rows()), 4)

    def test_only_the_newest_stay_in_memory(self):
        keep = meas.KEEP_IN_MEMORY
        self.assertEqual(keep, 5000)
        with self.path.open('w', encoding='utf-8', newline='') as out:
            for number in range(keep + 20):
                out.write(json.dumps({'id': str(number), 'time': '2026-10-06T00:00:00+00:00', 'kind': 'sample'}) + '\n')
        log = meas.Log(self.path)
        self.assertEqual((len(log.rows()), log.rows()[0]['id'], log.rows()[-1]['id']), (keep, '20', str(keep + 19)))
        log.add({'kind': 'blank'})
        self.assertEqual((len(log.rows()), log.rows()[0]['id']), (keep, '21'))
        self.assertEqual(len(self.path.read_bytes().splitlines()), keep + 21)  # the file keeps everything

    def test_a_path_that_cannot_be_read_starts_empty(self):
        log = meas.Log(Path(self.temp.name))  # a folder, not a file
        self.assertEqual(log.rows(), [])
        with self.assertRaises(OSError):
            log.add({'kind': 'sample'})
        self.assertEqual(log.rows(), [])

    def test_threads_append_whole_lines(self):
        log = meas.Log(self.path)

        def work(name):
            for number in range(50):
                log.add({'kind': 'sample', 'job': f'{name}-{number}', 'text': 'Measure' * 40})
        threads = [threading.Thread(target=work, args=(name,)) for name in 'abcd']
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        lines = self.path.read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 200)
        self.assertEqual({json.loads(line)['job'] for line in lines}, {f'{n}-{i}' for n in 'abcd' for i in range(50)})
        self.assertEqual(len(log.rows()), 200)
        self.assertEqual([row['id'] for row in log.rows()], [json.loads(line)['id'] for line in lines])


if __name__ == '__main__':
    unittest.main()
